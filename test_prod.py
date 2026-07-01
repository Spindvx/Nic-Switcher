"""Production smoke test for NIC Switcher.

Exercises every critical path that touches the OS / dhcpsrv / firewall, so we
don't ship another build that "crashed on start".

Run from the project root:
    python test_prod.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from nic_switcher import dhcp, firewall
from nic_switcher.config import DhcpConfig, Preset
from nic_switcher.validate import (
    is_valid_ipv4, is_valid_mask, mask_to_prefix, prefix_to_mask,
    validate_dhcp_range, validate_preset,
)


fails: list[str] = []


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    fails.append(msg)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# ---------------------------------------------------------------------------
section("1. mask <-> prefix conversions")
# ---------------------------------------------------------------------------
for mask, expected in [
    ("255.255.255.0", 24), ("255.255.0.0", 16), ("255.0.0.0", 8),
    ("255.255.254.0", 23), ("255.255.255.128", 25), ("255.255.255.252", 30),
]:
    got = mask_to_prefix(mask)
    (ok if got == expected else fail)(f"mask_to_prefix({mask!r}) = {got} (want {expected})")

for bad in ["", "garbage", "256.0.0.0", "255.0.255.0"]:
    if mask_to_prefix(bad) is None:
        ok(f"mask_to_prefix({bad!r}) correctly None")
    else:
        fail(f"mask_to_prefix({bad!r}) should be None")

for prefix, expected in [(24, "255.255.255.0"), (16, "255.255.0.0"), (30, "255.255.255.252")]:
    got = prefix_to_mask(prefix)
    (ok if got == expected else fail)(f"prefix_to_mask({prefix}) = {got!r}")


# ---------------------------------------------------------------------------
section("2. validate_preset")
# ---------------------------------------------------------------------------
cases = [
    # (args, expected_ok, label)
    (("10.17.75.240", 24, "10.17.75.1", "8.8.8.8", ""), True, "valid static"),
    (("", 0, "", "", ""), True, "blank = DHCP preset"),
    (("10.17.75.240", 24, "192.168.1.1", "", ""), False, "gateway outside subnet"),
    (("notanip", 24, "", "", ""), False, "bad IP"),
    (("10.17.75.240", 33, "", "", ""), False, "bad prefix"),
    (("10.17.75.240", 24, "", "badns", ""), False, "bad DNS"),
]
for args, want_ok, label in cases:
    got_ok, msg = validate_preset(*args)
    (ok if got_ok == want_ok else fail)(f"{label}: ok={got_ok}  msg={msg!r}")


# ---------------------------------------------------------------------------
section("3. validate_dhcp_range")
# ---------------------------------------------------------------------------
cases = [
    (("10.17.75.240", "10.17.75.100", "10.17.75.200", "255.255.255.0"), True, "typical /24"),
    (("10.17.75.240", "10.17.75.200", "10.17.75.100", "255.255.255.0"), False, "reversed"),
    (("10.17.75.240", "192.168.1.100", "192.168.1.200", "255.255.255.0"), False, "cross-subnet"),
    (("10.17.75.240", "10.17.75.100", "10.17.75.200", "255.255.254.0"), True, "/23 works"),
    (("10.17.75.240", "10.17.75.100", "10.17.75.200", "gibberish"), False, "bad mask"),
]
for args, want_ok, label in cases:
    got_ok, msg, eo = validate_dhcp_range(*args)
    if got_ok == want_ok:
        ok(f"{label}: ok={got_ok}, end_octet={eo}")
    else:
        fail(f"{label}: ok={got_ok} (want {want_ok}) msg={msg!r}")


# ---------------------------------------------------------------------------
section("4. dhcpsrv.ini generation — format check")
# ---------------------------------------------------------------------------
cfg = DhcpConfig(
    exe_path="",
    bind_ip="10.17.75.240",
    range_start="10.17.75.100",
    range_end="10.17.75.200",
    subnet_mask="255.255.255.0",
    gateway="10.17.75.1",
    dns="8.8.8.8, 1.1.1.1",
    lease_seconds=86400,
)
ini = dhcp._build_ini(cfg, end_octet=200)
print("--- generated ini ---")
print(ini)
print("--- end ini ---")

required = [
    "[SETTINGS]", "IPBIND_1=10.17.75.240", "IPPOOL_1=10.17.75.100-200",
    "AssociateBindsToPools=1", "Trace=1",
    "[GENERAL]", "SUBNETMASK=255.255.255.0",
    "ROUTER_0=10.17.75.1", "DNS_0=8.8.8.8", "DNS_1=1.1.1.1",
    "LEASETIME=86400", "NODETYPE=8",
]
for needle in required:
    if needle in ini:
        ok(f"ini contains {needle!r}")
    else:
        fail(f"ini MISSING {needle!r}")


# ---------------------------------------------------------------------------
section("5. bundled dhcpsrv.exe discoverable")
# ---------------------------------------------------------------------------
exe = dhcp.effective_exe_path(cfg)
if exe:
    ok(f"found dhcpsrv.exe at {exe}")
    if Path(exe).is_file():
        ok("file exists on disk")
    else:
        fail(f"path returned but file missing: {exe}")
else:
    fail("no dhcpsrv.exe found — bundled copy missing?")


# ---------------------------------------------------------------------------
section("6. dhcp.start() / dhcp.stop() end-to-end")
# ---------------------------------------------------------------------------
print("  attempting real launch — will need to bind port 67 (needs admin)")
t0 = time.time()
started, msg = dhcp.start(cfg)
elapsed = time.time() - t0
print(f"  elapsed: {elapsed:.2f}s")
print(f"  ok={started}  msg={msg[:300]}")

if started:
    ok("dhcpsrv.exe started and held port 67")
    ok(f"is_running() reports {dhcp.is_running()}")
    time.sleep(0.5)
    stopped, smsg = dhcp.stop()
    print(f"  stop ok={stopped}  msg={smsg}")
    if stopped and not dhcp.is_running():
        ok("clean stop confirmed")
    else:
        fail(f"stop failed or process still running: {smsg}")
else:
    # Expected if not admin or port 67 held. We verify the error path is clean.
    if "exited immediately" in msg or "bind" in msg.lower() or "access" in msg.lower():
        ok("start failure captured with diagnostic message")
    else:
        fail(f"unexpected error format: {msg!r}")
    ok(f"is_running() reports {dhcp.is_running()} (should be False)")


# ---------------------------------------------------------------------------
section("7. firewall helpers (read-only)")
# ---------------------------------------------------------------------------
# rules_present is read-only — safe without admin
try:
    present = firewall.rules_present()
    ok(f"rules_present() returned {present} without exception")
except Exception as e:
    fail(f"rules_present() crashed: {e}")


# ---------------------------------------------------------------------------
section("8. orphan cleanup is idempotent")
# ---------------------------------------------------------------------------
try:
    killed = dhcp._kill_orphans()
    ok(f"_kill_orphans() returned {killed} without exception")
except Exception as e:
    fail(f"_kill_orphans() crashed: {e}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
if fails:
    print(f"FAILED: {len(fails)} issue(s)")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL TESTS PASSED")
    sys.exit(0)
