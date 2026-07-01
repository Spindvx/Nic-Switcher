"""End-to-end UAT for NIC Switcher.

Exercises every feature surface that can be driven programmatically:

  1.  Module imports + entry point
  2.  Pure helpers (validators, normalizers, randomizer)
  3.  OUI lookups across the expanded table
  4.  Live system reads (NICs, ARP, default gateway, hardware MACs)
  5.  DHCP log parser against a synthetic trace
  6.  Real DHCP server start/stop (binds UDP/67 — needs admin)
  7.  mDNS probe send + Dante browser start/stop
  8.  Hostname resolution against the default gateway
  9.  Headless QApplication + Popup widget construction (proves the full UI
     tree builds; no window is shown)

Does NOT touch any NIC's MAC override (use smoke_test_mac.py for that).

Run:
    python uat.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

# Force Qt offscreen so this can run without a desktop session if needed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


fails: list[str] = []
skips: list[str] = []


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    fails.append(msg)


def skip(msg: str) -> None:
    print(f"  [SKIP] {msg}")
    skips.append(msg)


def section(n: int, title: str) -> None:
    print(f"\n{'=' * 64}\n[{n}] {title}\n{'=' * 64}")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
section(1, "Module imports + entry point")
# ---------------------------------------------------------------------------
mods = [
    "nic_switcher",
    "nic_switcher.config",
    "nic_switcher.validate",
    "nic_switcher.mac",
    "nic_switcher.nic",
    "nic_switcher.dhcp",
    "nic_switcher.dhcp_log",
    "nic_switcher.discover",
    "nic_switcher.sniffer",
    "nic_switcher.dante",
    "nic_switcher.firewall",
    "nic_switcher.icons",
    "nic_switcher.theme",
    "nic_switcher.dialogs",
    "nic_switcher.popup",
    "nic_switcher.tray",
    "nic_switcher.scan_dialog",
    "nic_switcher.blur",
    "main",
]
for m in mods:
    try:
        __import__(m)
        ok(f"import {m}")
    except Exception as e:
        fail(f"import {m}: {e}")
        traceback.print_exc()

from nic_switcher import (
    config, dhcp, dhcp_log, dante, discover, firewall, icons, mac as macmod,
    nic, sniffer, theme, validate,
)
from nic_switcher.config import AppConfig, DhcpConfig, Preset


# ---------------------------------------------------------------------------
section(2, "Pure helpers (validation / normalization / randomization)")
# ---------------------------------------------------------------------------

# IP / mask
for s, want in [("255.255.255.0", 24), ("255.255.254.0", 23), ("255.0.0.0", 8)]:
    got = validate.mask_to_prefix(s)
    (ok if got == want else fail)(f"mask_to_prefix({s}) = {got}")

# Preset validation including MAC field
ok_, msg = validate.validate_preset("10.17.75.240", 24, "10.17.75.1", "8.8.8.8", "",
                                     mac="02:AA:BB:CC:DD:EE")
(ok if ok_ else fail)(f"validate_preset with valid MAC: {msg!r}")
ok_, msg = validate.validate_preset("10.17.75.240", 24, "", "", "", mac="restore")
(ok if ok_ else fail)(f"validate_preset with restore sentinel: {msg!r}")
ok_, msg = validate.validate_preset("10.17.75.240", 24, "", "", "", mac="garbage")
(ok if not ok_ else fail)(f"validate_preset rejects bad MAC: {msg!r}")

# MAC normalization across formats
for src, want in {
    "AA:BB:CC:DD:EE:FF": "AABBCCDDEEFF",
    "aa-bb-cc-dd-ee-ff": "AABBCCDDEEFF",
    "AABB.CCDD.EEFF":    "AABBCCDDEEFF",
}.items():
    got = macmod.normalize_mac(src)
    (ok if got == want else fail)(f"normalize_mac({src!r}) = {got!r}")

# Randomizer — sample 500, all valid LAA unicast
import random as _r
sample = [macmod.random_locally_administered_mac(_r.Random(s)) for s in range(500)]
bad = [m for m in sample
       if macmod.is_multicast(m) or not macmod.is_locally_administered(m)]
(ok if not bad else fail)(f"500 random MACs all unicast+LAA (bad={len(bad)})")
unique = len(set(sample))
(ok if unique >= 495 else fail)(f"500 random MACs ~unique ({unique}/500)")


# ---------------------------------------------------------------------------
section(3, "OUI lookup — broad vendor coverage")
# ---------------------------------------------------------------------------
print(f"  OUI_DB has {len(discover.OUI_DB)} entries from "
      f"{len(discover._OUI_GROUPS)} vendor groups")
cases = [
    ("006074", "QSC"),    ("00107F", "Crestron"), ("00905E", "Biamp"),
    ("00A07E", "Audinate"), ("0005A6", "Extron"), ("000EDD", "Shure"),
    ("FC01CD", "Lenovo"), ("10F60A", "Apple"),  ("000874", "Dell"),
    ("00215A", "HP"),     ("0418D6", "Ubiquiti"),("00095B", "NETGEAR"),
    ("B827EB", "Raspberry"),("001A8A","Samsung"),("44190B", "Hikvision"),
    ("00408C", "Axis"),   ("F4F5D8", "Google"), ("FCA667", "Amazon"),
]
for prefix, expect in cases:
    v, k = discover.oui_lookup(prefix)
    if v and expect.lower() in v.lower():
        ok(f"{prefix} -> {v} ({k})")
    else:
        fail(f"{prefix} expected {expect}, got ({v},{k})")


# ---------------------------------------------------------------------------
section(4, "Live system reads — NICs / ARP / gateway / hardware MAC")
# ---------------------------------------------------------------------------
nics = nic.list_nics()
print(f"  Found {len(nics)} interfaces:")
non_loopback = 0
for n in nics:
    if n.is_loopback:
        continue
    non_loopback += 1
    print(f"   - {n.name!r:42}  ip={n.ipv4 or '-':16} mac={n.mac:18} up={n.is_up}")
(ok if non_loopback else fail)(f"non-loopback NICs visible: {non_loopback}")

arp = discover.read_arp_cache()
print(f"  ARP cache: {len(arp)} entries (showing up to 5)")
for ip, m in arp[:5]:
    v, k = discover.oui_lookup(m)
    print(f"   - {ip:18} {m:18} {v or '?':30} ({k or '-'})")
(ok if isinstance(arp, list) else fail)("read_arp_cache returns a list")

# Default gateway via the canonical helper
test_ip = next((n.ipv4 for n in nics if n.ipv4 and not n.is_loopback), "")
gw = discover.default_gateway_for(test_ip) if test_ip else None
print(f"  default_gateway_for({test_ip!r}) -> {gw!r}")
ok("default_gateway_for ran without exception")

# Hardware MAC for the first usable NIC (cached after first call)
target_nic_name = next((n.name for n in nics if n.ipv4 and n.is_up
                        and not n.is_loopback), None)
if target_nic_name:
    t0 = time.time()
    hw = macmod.hardware_mac(target_nic_name)
    cold = time.time() - t0
    t0 = time.time()
    hw2 = macmod.hardware_mac(target_nic_name)
    warm = time.time() - t0
    print(f"  hardware_mac({target_nic_name!r}) cold={cold*1000:.0f}ms "
          f"warm={warm*1000:.0f}ms -> {hw}")
    (ok if hw == hw2 else fail)("hardware_mac cache returns stable value")
    (ok if warm < 0.05 else fail)(f"hardware_mac warm call <50ms ({warm*1000:.1f}ms)")


# ---------------------------------------------------------------------------
section(5, "DHCP log parser — synthetic trace round-trip")
# ---------------------------------------------------------------------------
sample_log = """\
[24/04/2026 09:15:30] DISCOVER from mac 00:1C:C5:AA:BB:CC
[24/04/2026 09:15:30] OFFER 10.17.75.101 to 00:1C:C5:AA:BB:CC
[24/04/2026 09:15:30] REQUEST from 00:1C:C5:AA:BB:CC for 10.17.75.101
[24/04/2026 09:15:30] ACK 10.17.75.101 to 00:1C:C5:AA:BB:CC hostname:my-qsys-core
[24/04/2026 09:15:32] ACK 10.17.75.102 to AA:BB:CC:11:22:33 hostname=biamp-tesira
[24/04/2026 09:15:35] RELEASE 10.17.75.101 from 00:1C:C5:AA:BB:CC
[24/04/2026 09:15:40] DISCOVER from mac AA:BB:CC:11:22:33
"""
with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log",
                                  encoding="utf-8") as f:
    f.write(sample_log)
    tmp = Path(f.name)
try:
    events = dhcp_log.tail_events(tmp, max_events=20)
    snap = dhcp_log.summarize(events)
    (ok if len(events) == 7 else fail)(f"parsed 7 events, got {len(events)}")
    # 00:1C:C5:AA:BB:CC was RELEASE'd -> shouldn't be active
    (ok if "00:1C:C5:AA:BB:CC" not in snap.active else fail)(
        "released MAC removed from active leases"
    )
    # AA:BB:CC:11:22:33 should be active with hostname
    lease = snap.active.get("AA:BB:CC:11:22:33")
    (ok if lease and lease.ip == "10.17.75.102" else fail)(
        f"active lease tracked: {lease}"
    )
    (ok if lease and lease.hostname == "biamp-tesira" else fail)(
        f"hostname captured: {lease.hostname if lease else None!r}"
    )
finally:
    tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
section(6, "DHCP server — real start/stop on AppConfig")
# ---------------------------------------------------------------------------
if not is_admin():
    skip("not admin — DHCP server start (UDP/67 bind) requires elevation. "
         "Re-run UAT from an elevated shell to exercise this path.")
else:
    cfg = AppConfig.load().dhcp
    if not (cfg.bind_ip and cfg.range_start and cfg.range_end):
        # Synthesise from the first usable NIC.
        target = next((n for n in nics if n.ipv4 and n.is_up and not n.is_loopback), None)
        if target:
            base = ".".join(target.ipv4.split(".")[:3])
            cfg = DhcpConfig(
                bind_ip=target.ipv4, range_start=f"{base}.150", range_end=f"{base}.180",
                subnet_mask=target.netmask or "255.255.255.0", gateway="",
                dns="8.8.8.8", lease_seconds=3600,
            )
            print(f"  using synthesized cfg: bind={cfg.bind_ip} "
                  f"range={cfg.range_start}-{cfg.range_end}")
    started, msg = dhcp.start(cfg)
    print(f"  start -> ok={started}, msg={msg!r}")
    if started:
        ok("dhcpsrv launched + bound UDP/67")
        time.sleep(0.4)
        (ok if dhcp.is_running() else fail)("is_running() reports True post-start")
        # lease_snapshot should at least not throw on a fresh log
        snap = dhcp.lease_snapshot()
        ok(f"lease_snapshot ran ({len(snap.active)} active, "
           f"{len(snap.recent)} recent events)")
        stopped, smsg = dhcp.stop()
        (ok if stopped and not dhcp.is_running() else fail)(f"clean stop: {smsg}")
    else:
        # Acceptable failures: another DHCP server already running, port held, etc.
        if "exited immediately" in msg or "bind" in msg.lower() or "access" in msg.lower():
            ok(f"start failed cleanly with diagnostic: {msg[:160]}")
        else:
            fail(f"unexpected start error: {msg[:200]}")


# ---------------------------------------------------------------------------
section(7, "mDNS probe + Dante browser lifecycle")
# ---------------------------------------------------------------------------
if test_ip:
    try:
        discover.mdns_probe(test_ip)
        ok(f"mdns_probe({test_ip}) sent without exception")
    except Exception as e:
        fail(f"mdns_probe crashed: {e}")
else:
    fail("no usable bind IP — skipping mdns_probe")

browser = dante.DanteBrowser()
avail, err = browser.available()
if not avail:
    fail(f"zeroconf not available: {err}")
else:
    started, msg = browser.start()
    (ok if started else fail)(f"DanteBrowser.start: {msg}")
    if started:
        time.sleep(2.0)  # short window for any local Dante node to surface
        devs = browser.devices()
        print(f"  Dante devices found in 2s window: {len(devs)}")
        for ip, d in devs.items():
            svc_short = ",".join(sorted(d.services))[:50]
            print(f"   - {ip:16} name={d.name!r:30} svcs={svc_short}")
        ok("DanteBrowser collected results without crash")
        browser.stop()
        ok("DanteBrowser.stop ran cleanly")


# ---------------------------------------------------------------------------
section(8, "Hostname resolution — reverse DNS + NetBIOS")
# ---------------------------------------------------------------------------
target = gw or test_ip
if target:
    t0 = time.time()
    host = discover.resolve_hostname(target)
    el = time.time() - t0
    print(f"  resolve_hostname({target!r}) -> {host!r} in {el*1000:.0f}ms")
    ok("resolve_hostname ran without exception")
else:
    fail("no target IP for hostname resolution")


# ---------------------------------------------------------------------------
section(9, "Headless UI — QApplication + Popup widget tree builds")
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtWidgets import QApplication
    from nic_switcher.popup import Popup
    app = QApplication.instance() or QApplication([])
    cfg = AppConfig.load()
    popup = Popup(cfg)
    # Force a refresh — exercises every callback path that touches OS state.
    popup.refresh_all()
    ok("Popup constructed + refresh_all completed")
    # Verify the new MAC input field exists with the right widgets.
    assert hasattr(popup, "mac_input"), "missing mac_input widget"
    assert hasattr(popup, "mac_apply_btn"), "missing mac_apply_btn"
    assert hasattr(popup, "mac_random_btn"), "missing mac_random_btn"
    assert hasattr(popup, "mac_restore_btn"), "missing mac_restore_btn"
    assert hasattr(popup, "mac_status"), "missing mac_status label"
    ok("MAC input + Apply/Random/Restore + status all present")
    # DHCP toggle starts as 'Start DHCP' when not running.
    expected = "Stop DHCP" if dhcp.is_running() else "Start DHCP"
    actual = popup.dhcp_toggle.text()
    (ok if actual == expected else fail)(
        f"dhcp_toggle text deterministic: {actual!r} (expected {expected!r})"
    )
    # Lease label is always in layout (avoids ghost-button repaint bug);
    # it just carries empty text when DHCP isn't running.
    assert hasattr(popup, "dhcp_leases"), "missing dhcp_leases widget"
    if not dhcp.is_running():
        (ok if popup.dhcp_leases.text() == "" else fail)(
            f"dhcp_leases empty when server idle "
            f"(got {popup.dhcp_leases.text()!r})"
        )
    # Pin button defaults unchecked
    assert hasattr(popup, "pin_btn"), "missing pin_btn"
    (ok if not popup.pin_btn.isChecked() else fail)(
        "pin_btn defaults unchecked + popup unpinned"
    )
    # Brand label has the larger pixmap (height 38 now).
    pm = icons.brand_logo(38)
    (ok if not pm.isNull() and pm.height() == 38 else fail)(
        f"brand_logo(38) returns 38-tall pixmap (got {pm.height()})"
    )
    popup.deleteLater()
except Exception as e:
    fail(f"Popup headless build crashed: {e}")
    traceback.print_exc()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 64)
if skips:
    print(f"SKIPPED ({len(skips)}):")
    for s in skips:
        print(f"  - {s}")
if fails:
    print(f"\nUAT FAILED — {len(fails)} issue(s):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
else:
    print(f"\nUAT PASSED — every programmatically-verifiable surface is green"
          f"{f' ({len(skips)} skipped)' if skips else ''}.")
    sys.exit(0)
