"""Windows Firewall helpers — auto-allow DHCP (UDP 67/68) and raw socket traffic.

Uses `netsh advfirewall firewall` because it's locale-stable and doesn't need
PowerShell. All rules are idempotent (add replaces existing with same name).

Each netsh invocation takes ~1.5-2s on a typical Windows box. To keep
ensure_dhcp_rules fast we run them in a ThreadPoolExecutor so the total wall
time ≈ the slowest single call, not the sum of all calls.
"""
from __future__ import annotations

import concurrent.futures
import subprocess
from typing import Optional

CREATE_NO_WINDOW = 0x08000000

RULE_DHCP_IN = "NIC Switcher — DHCP inbound (UDP 67)"
RULE_DHCP_OUT = "NIC Switcher — DHCP outbound (UDP 68)"
RULE_DHCP_PROG = "NIC Switcher — DHCP server program"
RULE_NAMES = [RULE_DHCP_IN, RULE_DHCP_OUT, RULE_DHCP_PROG]


def _run(args: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def _delete(name: str) -> None:
    _run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"])


def _add_port_in(name: str, port: int) -> tuple[int, str]:
    return _run([
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={name}",
        "dir=in", "action=allow", "protocol=UDP", f"localport={port}",
        "profile=private,domain,public",
    ])


def _add_port_out(name: str, port: int) -> tuple[int, str]:
    return _run([
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={name}",
        "dir=out", "action=allow", "protocol=UDP", f"localport={port}",
        "profile=private,domain,public",
    ])


def _add_program(name: str, exe_path: str) -> tuple[int, str]:
    return _run([
        "netsh", "advfirewall", "firewall", "add", "rule",
        f"name={name}",
        "dir=in", "action=allow",
        f"program={exe_path}",
        "enable=yes", "profile=private,domain,public",
    ])


def _show_rule(name: str) -> tuple[int, str]:
    return _run([
        "netsh", "advfirewall", "firewall", "show", "rule", f"name={name}",
    ])


def rules_in_place_for(exe_path: Optional[str]) -> bool:
    """Tighter check than `rules_present`: confirms BOTH UDP rules exist AND
    the program rule (if exe_path given) references the same exe. Used to
    short-circuit the 8s delete+re-add cycle when nothing's actually changed.

    Total time: ~3s (three parallel show-rule calls). The cost of the check
    is justified because the alternative — re-applying every startup —
    blocks a UI status line for 5-8s on a locked-down machine.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        f_in = ex.submit(_show_rule, RULE_DHCP_IN)
        f_out = ex.submit(_show_rule, RULE_DHCP_OUT)
        f_prog = ex.submit(_show_rule, RULE_DHCP_PROG) if exe_path else None
        try:
            rc_in, out_in = f_in.result(timeout=6)
            rc_out, out_out = f_out.result(timeout=6)
        except Exception:
            return False
        if rc_in != 0 or RULE_DHCP_IN not in out_in:
            return False
        if rc_out != 0 or RULE_DHCP_OUT not in out_out:
            return False
        if f_prog is not None:
            try:
                rc_prog, out_prog = f_prog.result(timeout=6)
            except Exception:
                return False
            if rc_prog != 0 or RULE_DHCP_PROG not in out_prog:
                return False
            # Program path must match — if the exe moved, we need to
            # re-add or dhcpsrv will silently get blocked.
            if exe_path and exe_path.lower() not in out_prog.lower():
                return False
    return True


def ensure_dhcp_rules(exe_path: Optional[str] = None) -> tuple[bool, str]:
    """Install (idempotent) firewall rules so dhcpsrv can receive client DISCOVERs
    and our raw-socket sniffer isn't blocked.

    Fast path: if the rules are already in place AND (when exe_path is given)
    the program rule references the same exe, skip the whole delete+re-add
    cycle. Saves ~5-8s on every app startup once configured.

    Slow path: parallel netsh calls ≈ 2s instead of ≈ 8s sequential.

    Returns (ok, human_readable_msg).
    """
    if rules_in_place_for(exe_path):
        return True, "Firewall rules already in place — no change."

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        # delete first (fire-and-forget) so fresh add doesn't accumulate dupes
        del_futures = [
            ex.submit(_delete, RULE_DHCP_IN),
            ex.submit(_delete, RULE_DHCP_OUT),
            ex.submit(_delete, RULE_DHCP_PROG),
        ]
        for f in del_futures:
            try:
                f.result(timeout=8)
            except Exception:
                pass

        add_futures = [
            ex.submit(_add_port_in, RULE_DHCP_IN, 67),
            ex.submit(_add_port_out, RULE_DHCP_OUT, 68),
        ]
        if exe_path:
            add_futures.append(ex.submit(_add_program, RULE_DHCP_PROG, exe_path))

        results: list[tuple[int, str]] = []
        for f in add_futures:
            try:
                results.append(f.result(timeout=10))
            except Exception as e:
                results.append((1, str(e)))

    if all(rc == 0 for rc, _ in results):
        return True, "Firewall rules added (UDP 67 in / UDP 68 out)."
    bad = next((m for rc, m in results if rc != 0), "unknown")
    # Trim noisy netsh output
    bad_short = bad.splitlines()[0] if bad else "unknown"
    return False, f"Firewall add failed: {bad_short}"


def remove_dhcp_rules() -> tuple[bool, str]:
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_delete, name): name for name in RULE_NAMES}
        for f in concurrent.futures.as_completed(futs):
            name = futs[f]
            try:
                rc, _ = f.result(timeout=8)
                if rc != 0:
                    errors.append(name)
            except Exception:
                errors.append(name)
    if errors:
        return False, f"Failed to remove: {', '.join(errors)}"
    return True, "Firewall rules removed."


def rules_present() -> bool:
    """Quick check: do our named rules exist? ~1-2s; call off the UI thread."""
    rc, out = _run([
        "netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE_DHCP_IN}",
    ])
    return rc == 0 and RULE_DHCP_IN in out
