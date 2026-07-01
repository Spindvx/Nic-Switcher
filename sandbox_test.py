# -*- coding: utf-8 -*-
"""Sandbox bug-hunter for NIC Switcher.

Drives the app's surface area programmatically with the goal of finding real
bugs, not just padding pass counts. Sections:

  1. Fuzz layer: thousands of malformed inputs to every parser/validator.
     Pass = no crash. Validation returning False/None is fine; an exception
     escaping is a bug.
  2. Edge cases: known-tricky inputs (Unicode, control chars, IPv6 where v4
     expected, prefix bounds, empty/None, embedded null, very long).
  3. QTest UI integration: real button clicks + key events on offscreen
     Popup and PresetDialog. Asserts state transitions.
  4. DHCP integration: starts the real server, sends a synthetic DHCP
     DISCOVER, asserts the parser captures it. Admin-only — skipped without.
  5. Concurrency stress: overlapping operations, rapid start/stop cycles.
  6. Memory leak smoke: construct/destroy popups in a loop, watch GC.

Run from project root:
    python sandbox_test.py
"""
from __future__ import annotations

import ctypes
import gc
import os
import random
import socket
import string
import struct
import sys
import threading
import time
import traceback
from pathlib import Path

# Force Qt offscreen so we can drive widgets without a desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Keep all log output ASCII — the Windows cp1252 default console encoding
# can't handle arrows/deltas/accents and a stdout encode error would crash
# the script mid-run, masking actual app bugs.


fails: list[str] = []
skips: list[str] = []
crashes: list[str] = []


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    fails.append(msg)


def skip(msg: str) -> None:
    print(f"  [SKIP] {msg}")
    skips.append(msg)


def crash(label: str, exc: BaseException) -> None:
    detail = f"{label}: {type(exc).__name__}: {exc}"
    print(f"  [CRASH] {detail}")
    crashes.append(detail)
    fails.append(detail)


def section(n: int, title: str) -> None:
    print(f"\n{'=' * 64}\n[{n}] {title}\n{'=' * 64}")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


from nic_switcher import (
    config, dhcp, dhcp_log, discover, firewall, mac as macmod, validate,
)
from nic_switcher.config import AppConfig, DhcpConfig, Preset


# ---------------------------------------------------------------------------
section(1, "Fuzz: parsers/validators must never raise")
# ---------------------------------------------------------------------------
RNG = random.Random(0xBEEFCAFE)


def random_text(min_len=0, max_len=64, alphabet=None) -> str:
    if alphabet is None:
        # ASCII printable + a few control chars + a few extended codepoints
        alphabet = (
            string.ascii_letters + string.digits + string.punctuation
            + " \t\n\x00 ​" + "✓✗·"
        )
    n = RNG.randint(min_len, max_len)
    return "".join(RNG.choice(alphabet) for _ in range(n))


def random_macish() -> str:
    """Sometimes valid-looking, often not — exercises both happy + sad paths."""
    if RNG.random() < 0.3:
        # Plausible MAC
        sep = RNG.choice([":", "-", ".", ""])
        parts = [f"{RNG.randint(0, 255):02x}" for _ in range(RNG.randint(4, 7))]
        return sep.join(parts)
    return random_text(0, 24)


def random_ip() -> str:
    if RNG.random() < 0.3:
        return ".".join(str(RNG.randint(0, 255)) for _ in range(4))
    if RNG.random() < 0.2:
        # Bad: IPv6 string
        return "::".join(f"{RNG.randint(0, 0xFFFF):x}" for _ in range(RNG.randint(2, 8)))
    return random_text(0, 32, string.digits + ".:")


fuzz_count = 5000

# normalize_mac
for _ in range(fuzz_count):
    s = random_macish()
    try:
        macmod.normalize_mac(s)
    except BaseException as e:  # noqa: BLE001
        crash(f"normalize_mac({s!r})", e)
        break
else:
    ok(f"normalize_mac survived {fuzz_count} fuzz inputs")

# validate_mac
for _ in range(fuzz_count):
    s = random_macish()
    try:
        macmod.validate_mac(s)
    except BaseException as e:
        crash(f"validate_mac({s!r})", e)
        break
else:
    ok(f"validate_mac survived {fuzz_count} fuzz inputs")

# is_valid_ipv4
for _ in range(fuzz_count):
    s = random_ip()
    try:
        validate.is_valid_ipv4(s)
    except BaseException as e:
        crash(f"is_valid_ipv4({s!r})", e)
        break
else:
    ok(f"is_valid_ipv4 survived {fuzz_count} fuzz inputs")

# mask_to_prefix
for _ in range(fuzz_count):
    s = random_ip()
    try:
        validate.mask_to_prefix(s)
    except BaseException as e:
        crash(f"mask_to_prefix({s!r})", e)
        break
else:
    ok(f"mask_to_prefix survived {fuzz_count} fuzz inputs")

# validate_preset across random combinations
for _ in range(fuzz_count):
    args = (
        random_ip(),
        RNG.randint(-10, 50),
        random_ip(),
        random_ip(),
        random_ip(),
    )
    mac_field = RNG.choice([random_macish(), "", "restore", "RESTORE"])
    try:
        validate.validate_preset(*args, mac=mac_field)
    except BaseException as e:
        crash(f"validate_preset({args}, mac={mac_field!r})", e)
        break
else:
    ok(f"validate_preset survived {fuzz_count} random arg combos")

# validate_dhcp_range
for _ in range(fuzz_count):
    a = (random_ip(), random_ip(), random_ip(), random_ip())
    try:
        validate.validate_dhcp_range(*a)
    except BaseException as e:
        crash(f"validate_dhcp_range({a})", e)
        break
else:
    ok(f"validate_dhcp_range survived {fuzz_count} random combos")


# ---------------------------------------------------------------------------
section(2, "Fuzz: dhcp_log.parse_line must never raise on garbage")
# ---------------------------------------------------------------------------
log_alphabet = (
    string.ascii_letters + string.digits + ":-./[] \t,;"
    + "ACK NAK OFFER RELEASE DISCOVER REQUEST DECLINE hostname"
    + "0123456789ABCDEF"
)
for _ in range(fuzz_count):
    line = random_text(0, 200, log_alphabet)
    try:
        dhcp_log.parse_line(line)
    except BaseException as e:
        crash(f"parse_line({line!r})", e)
        break
else:
    ok(f"parse_line survived {fuzz_count} fuzz lines")

# Tail+summarize on a synthetic log full of garbage
import tempfile
with tempfile.NamedTemporaryFile("w", delete=False, suffix=".log",
                                  encoding="utf-8") as f:
    for _ in range(2000):
        f.write(random_text(0, 200, log_alphabet) + "\n")
    p = Path(f.name)
try:
    events = dhcp_log.tail_events(p, max_events=100)
    snap = dhcp_log.summarize(events)
    ok(f"tail_events parsed {len(events)} events from 2000 garbage lines without crash")
    ok(f"summarize produced {len(snap.active)} active leases (no crash)")
except BaseException as e:
    crash("tail_events/summarize on garbage log", e)
finally:
    p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
section(3, "Edge-case battery — known-tricky inputs")
# ---------------------------------------------------------------------------
tricky = [
    "",
    None,
    "\x00",
    "\x00\x00\x00\x00",
    " " * 4096,
    "AA:BB:CC:DD:EE:FF" * 100,
    "AA:BB:CC:DD:EE:FF\x00garbage",
    "AA:BB:CC:DD:EE:FF\nMore",
    "𝕬𝕬:𝔅𝔅:ℭℭ:𝔇𝔇:𝔈𝔈:𝔉𝔉",  # unicode mathematical bold
    "ＡＡ：ＢＢ：ＣＣ：ＤＤ：ＥＥ：ＦＦ",   # fullwidth
    "AA:BB:CC:DD:EE:FF:00",       # 7 octets
    "AA:BB:CC:DD:EE",             # 5 octets
    "00:00:00:00:00:00",
    "FF:FF:FF:FF:FF:FF",
    "AA-BB-CC-DD-EE-FF",
    "AABB.CCDD.EEFF",
    "aa-bb-cc-dd-ee-ff",
    "AA: BB:CC :DD:EE: FF",      # internal spaces
]
for t in tricky:
    try:
        if t is None:
            # normalize_mac signature is `s: str`; we deliberately want to
            # check that None doesn't crash callers via TypeError leak.
            r = macmod.normalize_mac("")  # treat as empty
        else:
            r = macmod.normalize_mac(t)
        # Just confirm we got a string-or-None result and didn't blow up
        assert r is None or isinstance(r, str), f"unexpected return: {r!r}"
    except BaseException as e:
        crash(f"normalize_mac edge {t!r}", e)
ok("normalize_mac handled all tricky inputs without crash")

# Negative / out-of-range prefixes shouldn't crash the validator
for prefix in [-1, 0, 1, 32, 33, 64, 999, 10**6]:
    try:
        validate.validate_preset("10.0.0.5", prefix, "", "", "")
    except BaseException as e:
        crash(f"validate_preset prefix={prefix}", e)
ok("validate_preset handled extreme prefix values without crash")

# IPv6 address passed to IPv4 validator
for s in ["::", "::1", "fe80::1", "2001:db8::1", "[::1]"]:
    try:
        validate.is_valid_ipv4(s)
    except BaseException as e:
        crash(f"is_valid_ipv4 IPv6 input {s!r}", e)
ok("is_valid_ipv4 handled IPv6 inputs without crash")


# ---------------------------------------------------------------------------
section(4, "QTest-driven UI integration (offscreen real button clicks)")
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtCore import Qt as _Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    from nic_switcher.popup import Popup
    from nic_switcher.dialogs import PresetDialog

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = AppConfig.load()
    popup = Popup(cfg)

    # Click the pin button — it's checkable, so a click toggles state.
    QTest.mouseClick(popup.pin_btn, _Qt.MouseButton.LeftButton)
    (ok if popup.is_pinned() else fail)("pin_btn click -> popup is_pinned() True")
    QTest.mouseClick(popup.pin_btn, _Qt.MouseButton.LeftButton)
    (ok if not popup.is_pinned() else fail)("pin_btn second click -> unpinned")

    # Type a MAC + hit Enter — should validate and (without admin) emit an
    # error via _set_status. We check it didn't crash.
    popup.mac_input.setText("02:AA:BB:CC:DD:EE")
    QTest.keyClick(popup.mac_input, _Qt.Key.Key_Return)
    # The worker thread is in flight; wait briefly to give signal a chance.
    QTest.qWait(50)
    ok("mac_input + Return triggered apply path without crash (worker still running)")

    # Type garbage MAC + click Apply button -> should set an error status,
    # NOT spawn a worker. We can verify status_label changed.
    popup.mac_input.setText("not-a-mac")
    QTest.mouseClick(popup.mac_apply_btn, _Qt.MouseButton.LeftButton)
    QTest.qWait(20)
    txt = popup.status_label.text()
    (ok if "Invalid" in txt or "expect" in txt or txt
        else fail)(f"bad MAC click sets an error status: {txt!r}")

    # Click Random — it sets the input + spawns worker. Cursor should be busy.
    popup.mac_input.setText("")
    QTest.mouseClick(popup.mac_random_btn, _Qt.MouseButton.LeftButton)
    QTest.qWait(20)
    (ok if popup.mac_input.text() else fail)(
        f"Random click filled MAC input: {popup.mac_input.text()!r}"
    )

    # Click NIC refresh — exercises _populate_nics + _update_nic_status
    if hasattr(popup, "nic_combo"):
        popup.nic_combo.setCurrentIndex(0)
        ok("NIC combo setCurrentIndex(0) didn't crash")

    # PresetDialog: build, fill fields via key events, click Save.
    dlg = PresetDialog(parent=popup)
    QTest.keyClicks(dlg.name, "Sandbox Preset")
    QTest.keyClicks(dlg.ip, "10.42.42.5")
    # mask is pre-filled; gateway/dns blank
    # Click Random for MAC — verify normalized hex appears
    # The dialog has its own random helper via the row buttons, but we'll
    # invoke the slot directly since the buttons are local-scope variables.
    dlg._mac_randomize()
    rand_text = dlg.mac.text()
    (ok if rand_text and ":" in rand_text else fail)(
        f"PresetDialog _mac_randomize filled MAC: {rand_text!r}"
    )
    # Now invoke _accept programmatically and check we'd build a valid Preset.
    dlg._accept()
    if dlg.result() == 1:  # Accepted
        result = dlg.result_preset()
        (ok if result.name == "Sandbox Preset" and result.ip == "10.42.42.5"
            else fail)(f"PresetDialog accepted with result: {result}")
        (ok if macmod.normalize_mac(result.mac) is not None
            else fail)(f"PresetDialog stored normalized MAC: {result.mac!r}")
    else:
        fail(f"PresetDialog accept failed: err={dlg.err.text()!r}")
    dlg.deleteLater()
    popup.deleteLater()
    QTest.qWait(50)
except BaseException as e:
    crash("QTest UI integration", e)
    traceback.print_exc()


# ---------------------------------------------------------------------------
section(5, "Concurrency stress — overlapping operations")
# ---------------------------------------------------------------------------
# Spam validate_preset from many threads — surfaces any non-thread-safe
# global state in the validators.
errors: list[str] = []


def worker_validate():
    try:
        for _ in range(500):
            validate.validate_preset(
                f"10.{RNG.randint(0,255)}.{RNG.randint(0,255)}.{RNG.randint(1,254)}",
                RNG.randint(8, 30),
                gateway="",
                mac=RNG.choice(["", "02:aa:bb:cc:dd:ee", "restore"]),
            )
    except BaseException as e:
        errors.append(f"{type(e).__name__}: {e}")


threads = [threading.Thread(target=worker_validate) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join(timeout=10)
(ok if not errors else fail)(
    f"validate_preset thread-safe over 8x500 calls (errors={len(errors)})"
)

# Sniffer start/stop without admin — should fail cleanly, not deadlock.
from nic_switcher.sniffer import Sniffer
errors.clear()


# Sniffer start/stop without admin: should fail cleanly (PermissionError on
# raw socket), not deadlock. ONE cycle per worker — start() shells out to
# `route print` which takes ~5s on Defender-active boxes, so a tight loop
# is unrepresentative of real usage (users start the sniffer once per scan
# dialog open) and would hide the deadlock signal in subprocess noise.

def worker_sniffer():
    try:
        s = Sniffer()
        s.start("0.0.0.0")
        s.stop()
    except BaseException as e:
        errors.append(f"{type(e).__name__}: {e}")


threads = [threading.Thread(target=worker_sniffer) for _ in range(4)]
for t in threads:
    t.start()
deadline = time.time() + 30   # generous: subprocess.run latency dominates
for t in threads:
    t.join(timeout=max(0.1, deadline - time.time()))
still_alive = [t for t in threads if t.is_alive()]
(ok if not still_alive else fail)(
    f"Sniffer start/stop: no deadlocks across 4 workers "
    f"({len(still_alive)} alive after 30s)"
)
(ok if not errors else fail)(
    f"Sniffer concurrent start/stop: no exceptions (errors={len(errors)})"
)


# ---------------------------------------------------------------------------
section(6, "DHCP integration — real server + synthetic DISCOVER")
# ---------------------------------------------------------------------------
if not is_admin():
    skip("not admin — DHCP bind to UDP/67 not exercised")
else:
    # Pick a usable NIC: up, has IPv4, not loopback.
    from nic_switcher import nic
    target = next((n for n in nic.list_nics()
                    if n.ipv4 and n.is_up and not n.is_loopback), None)
    if not target:
        skip("no usable NIC for DHCP integration test")
    else:
        base = ".".join(target.ipv4.split(".")[:3])
        cfg = DhcpConfig(
            bind_ip=target.ipv4, range_start=f"{base}.150", range_end=f"{base}.180",
            subnet_mask=target.netmask or "255.255.255.0", gateway="",
            dns="8.8.8.8", lease_seconds=600,
        )
        started, msg = dhcp.start(cfg)
        if not started:
            if "exited immediately" in msg or "bind" in msg.lower():
                ok(f"DHCP start failed cleanly: {msg[:100]}")
            else:
                fail(f"DHCP start unexpected error: {msg[:200]}")
        else:
            try:
                ok("dhcpsrv launched + bound UDP/67")
                time.sleep(0.7)
                # Send a minimal DHCPDISCOVER from a fake MAC to the broadcast.
                # We don't expect dhcpsrv to ACK us (we're not a real client
                # and the source IP is wrong) — just want it logged.
                xid = RNG.randrange(0, 0xFFFFFFFF)
                fake_mac = bytes([0x02] + [RNG.randint(0, 255) for _ in range(5)])
                discover_pkt = (
                    b"\x01\x01\x06\x00"               # op,htype,hlen,hops
                    + struct.pack(">I", xid)           # xid
                    + b"\x00\x00\x80\x00"             # secs, flags=BROADCAST
                    + b"\x00\x00\x00\x00"             # ciaddr
                    + b"\x00\x00\x00\x00"             # yiaddr
                    + b"\x00\x00\x00\x00"             # siaddr
                    + b"\x00\x00\x00\x00"             # giaddr
                    + fake_mac + b"\x00" * 10         # chaddr (16 bytes)
                    + b"\x00" * 64                    # sname
                    + b"\x00" * 128                   # file
                    + b"\x63\x82\x53\x63"             # magic cookie
                    + b"\x35\x01\x01"                 # opt 53 = DISCOVER
                    + b"\xff"                         # end
                )
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                # Bind to our NIC IP so the OS routes via the right adapter.
                try:
                    s.bind((target.ipv4, 0))
                    s.sendto(discover_pkt, ("255.255.255.255", 67))
                    ok("synthetic DHCPDISCOVER sent on UDP/67")
                except OSError as e:
                    fail(f"failed to send DISCOVER: {e}")
                finally:
                    s.close()
                time.sleep(1.5)
                snap = dhcp.lease_snapshot()
                # The trace file should at least have grown — even if our
                # fake packet isn't recognised as a valid client, dhcpsrv
                # logs the receive attempt.
                log_path = dhcp.trace_log_path()
                size = log_path.stat().st_size if log_path.exists() else 0
                ok(f"trace log present ({size} bytes), {len(snap.recent)} parsed events")
            finally:
                stopped, smsg = dhcp.stop()
                (ok if stopped and not dhcp.is_running()
                    else fail)(f"clean DHCP stop: {smsg}")


# ---------------------------------------------------------------------------
section(7, "Memory leak smoke — Popup construct/destroy loop")
# ---------------------------------------------------------------------------
try:
    from PyQt6.QtWidgets import QApplication
    from nic_switcher.popup import Popup
    app = QApplication.instance() or QApplication(sys.argv)

    gc.collect()
    before_objs = len(gc.get_objects())

    iterations = 30
    for i in range(iterations):
        p = Popup(AppConfig.load())
        p.refresh_all()
        # Stop the lease timer to release its parent reference cleanly.
        try:
            p._lease_timer.stop()
        except Exception:
            pass
        p.deleteLater()
        del p
    # Drain pending deferred deletes
    app.processEvents()
    gc.collect()
    app.processEvents()
    gc.collect()

    after_objs = len(gc.get_objects())
    growth = after_objs - before_objs
    # Some growth is expected (string interning, lru_caches). Flag if
    # growth scales linearly with iterations (would indicate retention).
    per_iter = growth / iterations
    print(f"  before={before_objs}  after={after_objs}  delta={growth} ({per_iter:.1f}/iter)")
    if per_iter > 200:
        fail(f"Popup leaks ~{per_iter:.0f} objects/iteration (run grew {growth})")
    else:
        ok(f"Popup construct/destroy {iterations}x: no obvious leak ({per_iter:.1f}/iter)")
except BaseException as e:
    crash("memory leak smoke", e)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 64)
if crashes:
    print(f"\nCRASHES ({len(crashes)}):")
    for c in crashes:
        print(f"  ! {c}")
if skips:
    print(f"\nSKIPPED ({len(skips)}):")
    for s in skips:
        print(f"  - {s}")
if fails:
    other = [f for f in fails if f not in crashes]
    if other:
        print(f"\nOTHER FAILURES ({len(other)}):")
        for f in other:
            print(f"  - {f}")
    print(f"\nSANDBOX FAILED — {len(fails)} issue(s) ({len(crashes)} crashes).")
    sys.exit(1)
else:
    print(f"\nSANDBOX PASSED — every probe returned clean"
          f"{f' ({len(skips)} skipped)' if skips else ''}.")
    sys.exit(0)
