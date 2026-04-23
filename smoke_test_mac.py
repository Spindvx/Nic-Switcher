"""Live hardware smoke test for MAC switching.

Runs a full round-trip on a real adapter:
    1. Snapshot current state (current MAC, override present or not).
    2. Apply a random locally-administered MAC.
    3. Verify the running MAC matches.
    4. Restore (delete override, re-read hardware MAC).
    5. Verify the running MAC is back to the pre-test value.

REQUIREMENTS:
    - Windows + admin (launch from an elevated shell).
    - An adapter whose disable/enable you can tolerate for ~20 seconds.
    - Do NOT run this against the adapter carrying your only network route —
      the script will warn if it looks like the default route uses it and
      require --force to proceed anyway.

Usage:
    python smoke_test_mac.py                  # interactive: lists NICs, prompts
    python smoke_test_mac.py "Ethernet"       # run against a specific NIC
    python smoke_test_mac.py "Ethernet" --force  # skip the default-route guard
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time

from nic_switcher import mac as mac_mod
from nic_switcher import nic as nic_mod

CREATE_NO_WINDOW = 0x08000000


def die(msg: str, code: int = 2) -> None:
    print(f"\n[ABORT] {msg}")
    sys.exit(code)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def default_route_nic() -> str:
    """Best-effort: find the NIC carrying the default route so we can warn if
    the user picks it. Uses `route print` because it works without PowerShell
    modules that some images strip."""
    try:
        proc = subprocess.run(
            ["route", "print", "0.0.0.0"],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    gateway = ""
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
            gateway = parts[2]
            break
    if not gateway:
        return ""
    # Which NIC owns the subnet the gateway is on?
    for n in nic_mod.list_nics():
        if n.ipv4 and n.netmask:
            try:
                import ipaddress
                net = ipaddress.IPv4Network(f"{n.ipv4}/{n.netmask}", strict=False)
                if ipaddress.IPv4Address(gateway) in net:
                    return n.name
            except Exception:
                continue
    return ""


def pick_nic_interactive() -> str:
    nics = [n for n in nic_mod.list_nics() if not n.is_loopback]
    if not nics:
        die("No non-loopback NICs found.")
    print("Available NICs:")
    for i, n in enumerate(nics):
        mark = "  (default route)" if n.name == default_route_nic() else ""
        status = "up" if n.is_up else "down"
        print(f"  {i}) {n.name}  —  {n.ipv4 or 'no IP'}  [{status}]{mark}")
    choice = input("Pick a number (or q): ").strip()
    if choice.lower() == "q":
        sys.exit(0)
    try:
        return nics[int(choice)].name
    except (ValueError, IndexError):
        die("Invalid selection.")
    return ""


def phase(n: int, title: str) -> None:
    print(f"\n{'=' * 60}\n[{n}] {title}\n{'=' * 60}")


def main() -> int:
    if sys.platform != "win32":
        die("Windows-only.")
    if not is_admin():
        die(
            "Admin required. Right-click your terminal, 'Run as administrator', "
            "and re-run this script."
        )

    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    nic_name = args[0] if args else pick_nic_interactive()

    default_nic = default_route_nic()
    if nic_name == default_nic and not force:
        die(
            f"'{nic_name}' appears to carry your default route. Disabling it "
            f"will drop your internet connection for ~10 seconds. Re-run with "
            f"--force if you accept that."
        )

    phase(1, f"Snapshot '{nic_name}'")
    start_mac = mac_mod.current_mac(nic_name)
    hw_mac = mac_mod.hardware_mac(nic_name)
    had_override = mac_mod.has_override(nic_name)
    reg_key = mac_mod.find_adapter_registry_key(nic_name)
    if not reg_key:
        die(f"Couldn't locate '{nic_name}' in the registry.")
    print(f"  registry key : {reg_key}")
    print(f"  current MAC  : {mac_mod.format_mac_pretty(start_mac) if start_mac else '?'}")
    print(f"  hardware MAC : {mac_mod.format_mac_pretty(hw_mac) if hw_mac else '(unknown — driver may not expose PermanentAddress)'}")
    print(f"  override set : {had_override}")

    if not start_mac:
        die("Could not read current MAC — psutil returned no link-layer address.")

    phase(2, "Apply random locally-administered MAC")
    rand_mac = mac_mod.random_locally_administered_mac()
    pretty = mac_mod.format_mac_pretty(rand_mac)
    print(f"  target       : {pretty}")
    t0 = time.time()
    ok, msg = mac_mod.set_mac(nic_name, rand_mac)
    t1 = time.time()
    print(f"  elapsed      : {t1 - t0:.2f}s")
    print(f"  set_mac msg  : {msg}")
    if not ok:
        die(f"set_mac failed: {msg}")

    # Give psutil a moment — some drivers refresh asynchronously.
    time.sleep(1.0)
    running = mac_mod.current_mac(nic_name)
    print(f"  running MAC  : {mac_mod.format_mac_pretty(running) if running else '(none)'}")
    if running != rand_mac:
        die(
            f"MAC mismatch: expected {pretty}, got "
            f"{mac_mod.format_mac_pretty(running) if running else None}. "
            f"Driver may have rejected the override."
        )
    print("  [OK] MAC change confirmed in-kernel")

    phase(3, "Restore hardware MAC")
    t0 = time.time()
    ok, msg = mac_mod.restore_mac(nic_name)
    t1 = time.time()
    print(f"  elapsed      : {t1 - t0:.2f}s")
    print(f"  restore msg  : {msg}")
    if not ok:
        die(f"restore_mac failed: {msg}")

    time.sleep(1.0)
    after = mac_mod.current_mac(nic_name)
    print(f"  running MAC  : {mac_mod.format_mac_pretty(after) if after else '(none)'}")

    # If there was no override at the start, we expect the MAC to match
    # start_mac. If the user had a pre-existing override, a "restore" removes
    # it so we expect hardware_mac instead (if known).
    expected = start_mac if not had_override else (hw_mac or start_mac)
    if after != expected:
        die(
            f"Restore mismatch: expected "
            f"{mac_mod.format_mac_pretty(expected) if expected else '?'}, got "
            f"{mac_mod.format_mac_pretty(after) if after else None}."
        )
    print("  [OK] MAC restored")

    phase(4, "Re-check override flag")
    still_override = mac_mod.has_override(nic_name)
    # We expect override absent post-restore regardless of prior state, since
    # restore_mac deletes the NetworkAddress value.
    if still_override:
        die("Override flag still set after restore — DeleteValue didn't land.")
    print("  [OK] no override present")

    print("\n" + "=" * 60)
    print("SMOKE TEST PASSED — safe to ship MAC switching.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
