"""Unit tests for MAC address switching.

Covers pure logic (normalization, validation, randomization) plus the
Windows-specific paths (registry discovery, set_mac / restore_mac) with
winreg mocked out. Does NOT touch real adapters — that's smoke_test_mac.py.

Run from the project root:
    python test_mac.py
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from nic_switcher import mac as mac_mod
from nic_switcher.config import AppConfig, DhcpConfig, Preset
from nic_switcher.validate import validate_preset


fails: list[str] = []


def ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")
    fails.append(msg)


def check(cond: bool, msg: str) -> None:
    (ok if cond else fail)(msg)


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# ---------------------------------------------------------------------------
section("1. normalize_mac — every sane input format")
# ---------------------------------------------------------------------------
cases = {
    "AA:BB:CC:DD:EE:FF": "AABBCCDDEEFF",
    "aa:bb:cc:dd:ee:ff": "AABBCCDDEEFF",
    "aa-bb-cc-dd-ee-ff": "AABBCCDDEEFF",
    "AABB.CCDD.EEFF": "AABBCCDDEEFF",
    "aabbccddeeff": "AABBCCDDEEFF",
    "AA BB CC DD EE FF": "AABBCCDDEEFF",
    "  00:11:22:33:44:55  ": "001122334455",
    "02-00-00-00-00-01": "020000000001",
}
for src, want in cases.items():
    got = mac_mod.normalize_mac(src)
    check(got == want, f"normalize_mac({src!r}) = {got!r} (want {want!r})")

for bad in ["", "ZZZZZZZZZZZZ", "AA:BB:CC:DD:EE", "AA:BB:CC:DD:EE:FF:00", "garbage"]:
    check(mac_mod.normalize_mac(bad) is None, f"normalize_mac({bad!r}) correctly None")


# ---------------------------------------------------------------------------
section("2. format_mac_pretty")
# ---------------------------------------------------------------------------
check(
    mac_mod.format_mac_pretty("AABBCCDDEEFF") == "AA:BB:CC:DD:EE:FF",
    "format_mac_pretty produces colon form",
)


# ---------------------------------------------------------------------------
section("3. multicast / LAA bit detection")
# ---------------------------------------------------------------------------
check(mac_mod.is_multicast("010000000000"), "01: is multicast")
check(not mac_mod.is_multicast("020000000000"), "02: not multicast")
check(mac_mod.is_multicast("FFFFFFFFFFFF"), "FF: broadcast is multicast")
check(mac_mod.is_locally_administered("020000000000"), "02: is locally-administered")
check(not mac_mod.is_locally_administered("000000000000"), "00: not LAA")
check(mac_mod.is_locally_administered("0A1122334455"), "0A: LAA (bit 1 set)")


# ---------------------------------------------------------------------------
section("4. validate_mac — accept / reject matrix")
# ---------------------------------------------------------------------------
good = [
    "02:AA:BB:CC:DD:EE",   # typical LAA
    "00:50:56:00:00:01",   # a real VMware OUI (unicast, not LAA)
    "AA-BB-CC-DD-EE-FF",   # dash form
]
for g in good:
    ok_, err, norm = mac_mod.validate_mac(g)
    check(ok_ and norm is not None,
          f"validate_mac({g!r}) accepted (err={err!r}, norm={norm})")

bad = [
    ("", "empty"),
    ("not-a-mac", "garbage"),
    ("01:00:00:00:00:00", "multicast (LSB set)"),
    ("FF:FF:FF:FF:FF:FF", "broadcast"),
    ("00:00:00:00:00:00", "all zero"),
    ("GG:HH:II:JJ:KK:LL", "non-hex"),
]
for s, label in bad:
    ok_, err, _ = mac_mod.validate_mac(s)
    check(not ok_, f"validate_mac rejects {label}: {s!r} (err={err})")


# ---------------------------------------------------------------------------
section("5. random_locally_administered_mac — 10000 iterations, all valid LAA")
# ---------------------------------------------------------------------------
RNG = random.Random(0xC0FFEE)
generated = set()
N = 10_000
for _ in range(N):
    m = mac_mod.random_locally_administered_mac(RNG)
    if not (len(m) == 12 and all(c in "0123456789ABCDEF" for c in m)):
        fail(f"random MAC is not 12-hex uppercase: {m!r}")
        break
    if mac_mod.is_multicast(m):
        fail(f"random MAC is multicast: {m!r}")
        break
    if not mac_mod.is_locally_administered(m):
        fail(f"random MAC is not LAA: {m!r}")
        break
    ok_, err, _ = mac_mod.validate_mac(m)
    if not ok_:
        fail(f"random MAC rejected by validate_mac: {m!r} ({err})")
        break
    generated.add(m)
else:
    ok(f"{N} random MACs: all 12-hex, all unicast, all LAA, all validate_mac-clean")
    # Very low collision chance given 2^46 space — require significant uniqueness.
    check(len(generated) >= N * 0.999,
          f"random distribution looks unique: {len(generated)}/{N} distinct")


# ---------------------------------------------------------------------------
section("6. random_locally_administered_mac — deterministic from seed")
# ---------------------------------------------------------------------------
a = mac_mod.random_locally_administered_mac(random.Random(42))
b = mac_mod.random_locally_administered_mac(random.Random(42))
check(a == b, f"same seed -> same MAC ({a})")


# ---------------------------------------------------------------------------
section("7. find_adapter_registry_key — mocked registry walk")
# ---------------------------------------------------------------------------
#
# We simulate two top-level keys:
#   Network\{class}\{GUID_A}\Connection\Name = "Ethernet"
#   Network\{class}\{GUID_B}\Connection\Name = "Wi-Fi"
# and the class tree:
#   Class\{class}\0000\NetCfgInstanceId = GUID_B  (wifi)
#   Class\{class}\0001\NetCfgInstanceId = GUID_A  (ethernet)
# Expectation: finding "Ethernet" returns ...Class\{class}\0001

GUID_A = "{AAAAAAAA-1111-2222-3333-444444444444}"
GUID_B = "{BBBBBBBB-5555-6666-7777-888888888888}"


class FakeKey:
    """Just a token so `with FakeRegistry.OpenKey(...) as k:` works."""
    def __init__(self, path): self.path = path
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeRegistry:
    """Enough of the winreg surface to exercise find_adapter_registry_key."""
    HKEY_LOCAL_MACHINE = object()
    REG_SZ = 1
    KEY_SET_VALUE = 2

    def __init__(self, tree: dict):
        self.tree = tree  # dict: path -> {"subkeys": [...], "values": {name: (val, type)}}
        self.writes: dict[str, dict[str, str]] = {}
        self.deletes: list[tuple[str, str]] = []

    def _get(self, path):
        node = self.tree.get(path)
        if node is None:
            raise OSError(f"no such key {path!r}")
        return node

    def OpenKey(self, root_or_key, path, reserved=0, access=0):
        if isinstance(root_or_key, FakeKey):
            path = root_or_key.path + "\\" + path
        self._get(path)
        return FakeKey(path)

    def EnumKey(self, key, i):
        subs = self._get(key.path).get("subkeys", [])
        if i >= len(subs):
            raise OSError("no more")
        return subs[i]

    def QueryValueEx(self, key, name):
        vals = self._get(key.path).get("values", {})
        if name not in vals:
            raise OSError(f"no value {name!r}")
        return vals[name]

    def SetValueEx(self, key, name, reserved, vtype, value):
        self.writes.setdefault(key.path, {})[name] = value

    def DeleteValue(self, key, name):
        self.deletes.append((key.path, name))


fake_tree = {
    mac_mod.NETWORK_KEY: {"subkeys": [GUID_A, GUID_B, "Descriptions"]},
    rf"{mac_mod.NETWORK_KEY}\{GUID_A}\Connection": {"values": {"Name": ("Ethernet", 1)}},
    rf"{mac_mod.NETWORK_KEY}\{GUID_B}\Connection": {"values": {"Name": ("Wi-Fi", 1)}},
    # Descriptions is a non-GUID sibling that must be skipped without crashing.
    rf"{mac_mod.NETWORK_KEY}\Descriptions": {"values": {}},
    mac_mod.CLASS_KEY: {"subkeys": ["Properties", "0000", "0001"]},
    rf"{mac_mod.CLASS_KEY}\0000": {"values": {"NetCfgInstanceId": (GUID_B, 1)}},
    rf"{mac_mod.CLASS_KEY}\0001": {"values": {"NetCfgInstanceId": (GUID_A, 1)}},
    rf"{mac_mod.CLASS_KEY}\Properties": {"values": {}},
}
fake = FakeRegistry(fake_tree)

with mock.patch.object(mac_mod, "winreg", fake):
    got = mac_mod.find_adapter_registry_key("Ethernet")
    check(got == rf"{mac_mod.CLASS_KEY}\0001",
          f"Ethernet -> {got!r}")
    got = mac_mod.find_adapter_registry_key("Wi-Fi")
    check(got == rf"{mac_mod.CLASS_KEY}\0000",
          f"Wi-Fi -> {got!r}")
    got = mac_mod.find_adapter_registry_key("DoesNotExist")
    check(got is None, "missing adapter -> None")


# ---------------------------------------------------------------------------
section("8. set_mac happy path — validates, writes registry, restarts adapter")
# ---------------------------------------------------------------------------
calls: list[tuple[str, tuple]] = []

with mock.patch.object(mac_mod, "winreg", fake), \
     mock.patch.object(
         mac_mod, "find_adapter_registry_key",
         side_effect=lambda name: rf"{mac_mod.CLASS_KEY}\0001" if name == "Ethernet" else None,
     ), \
     mock.patch.object(
         mac_mod, "restart_adapter",
         side_effect=lambda name: (calls.append(("restart", (name,))) or (True, "")),
     ):
    ok_, msg = mac_mod.set_mac("Ethernet", "02:AA:BB:CC:DD:EE")
    check(ok_ and "02:AA:BB:CC:DD:EE" in msg, f"set_mac ok — msg: {msg!r}")
    written = fake.writes.get(rf"{mac_mod.CLASS_KEY}\0001", {})
    check(written.get("NetworkAddress") == "02AABBCCDDEE",
          f"NetworkAddress written = {written.get('NetworkAddress')!r}")
    check(("restart", ("Ethernet",)) in calls, "restart_adapter was called")


# ---------------------------------------------------------------------------
section("9. set_mac rejects invalid MAC without touching registry / adapter")
# ---------------------------------------------------------------------------
fake.writes.clear()
calls.clear()
with mock.patch.object(
        mac_mod, "find_adapter_registry_key",
        side_effect=AssertionError("should not be called for invalid MAC")), \
     mock.patch.object(
        mac_mod, "restart_adapter",
        side_effect=AssertionError("should not be called for invalid MAC")):
    ok_, msg = mac_mod.set_mac("Ethernet", "not-a-mac")
    check(not ok_ and "Invalid MAC" in msg,
          f"rejected garbage MAC — msg: {msg!r}")

    ok_, msg = mac_mod.set_mac("Ethernet", "01:00:00:00:00:00")  # multicast
    check(not ok_ and "multicast" in msg,
          f"rejected multicast — msg: {msg!r}")


# ---------------------------------------------------------------------------
section("10. set_mac surfaces adapter-restart failure intact")
# ---------------------------------------------------------------------------
with mock.patch.object(mac_mod, "winreg", fake), \
     mock.patch.object(
         mac_mod, "find_adapter_registry_key",
         return_value=rf"{mac_mod.CLASS_KEY}\0001",
     ), \
     mock.patch.object(
         mac_mod, "restart_adapter",
         return_value=(False, "Adapter enable failed: ACCESS DENIED"),
     ):
    ok_, msg = mac_mod.set_mac("Ethernet", "02:AA:BB:CC:DD:EE")
    check(not ok_ and "Adapter enable failed" in msg,
          f"restart failure surfaced — msg: {msg!r}")


# ---------------------------------------------------------------------------
section("11. restore_mac — deletes override + restarts adapter")
# ---------------------------------------------------------------------------
fake.deletes.clear()
calls.clear()
with mock.patch.object(mac_mod, "winreg", fake), \
     mock.patch.object(
         mac_mod, "find_adapter_registry_key",
         return_value=rf"{mac_mod.CLASS_KEY}\0001",
     ), \
     mock.patch.object(
         mac_mod, "restart_adapter",
         side_effect=lambda name: (calls.append(("restart", (name,))) or (True, "")),
     ):
    # Inject a NetworkAddress value so DeleteValue has something to delete.
    fake.tree[rf"{mac_mod.CLASS_KEY}\0001"] = {
        "values": {
            "NetCfgInstanceId": (GUID_A, 1),
            "NetworkAddress": ("02AABBCCDDEE", 1),
        }
    }
    ok_, msg = mac_mod.restore_mac("Ethernet")
    check(ok_, f"restore_mac ok — msg: {msg!r}")
    check(
        (rf"{mac_mod.CLASS_KEY}\0001", "NetworkAddress") in fake.deletes,
        "NetworkAddress was deleted",
    )
    check(("restart", ("Ethernet",)) in calls, "restart_adapter was called")


# ---------------------------------------------------------------------------
section("12. restart_adapter — always tries enable even if disable errored")
# ---------------------------------------------------------------------------
seq: list[str] = []

def fake_run_netsh_scripted(args, timeout=20):
    # args = ['netsh','interface','set','interface', f'name={nic}', 'admin=disabled|enabled']
    mode = args[-1].split("=")[-1]
    seq.append(mode)
    # Fail disable the first time, succeed on enable.
    if mode == "disabled":
        return 1, "cannot disable right now"
    return 0, ""

with mock.patch.object(mac_mod, "_run_netsh", side_effect=fake_run_netsh_scripted), \
     mock.patch.object(mac_mod, "_adapter_is_up", return_value=True), \
     mock.patch.object(mac_mod.time, "sleep", return_value=None):
    ok_, msg = mac_mod.restart_adapter("Ethernet")
    check("enabled" in seq, "enable attempted even though disable failed")
    check(not ok_ and "disable had reported" in msg,
          f"outer result surfaces the disable error: {msg!r}")


# ---------------------------------------------------------------------------
section("13. restart_adapter — retries enable once on first failure")
# ---------------------------------------------------------------------------
attempts = {"count": 0}

def enable_flaky(args, timeout=20):
    mode = args[-1].split("=")[-1]
    if mode == "disabled":
        return 0, ""
    attempts["count"] += 1
    # First enable fails, second succeeds.
    if attempts["count"] == 1:
        return 1, "transient"
    return 0, ""

with mock.patch.object(mac_mod, "_run_netsh", side_effect=enable_flaky), \
     mock.patch.object(mac_mod, "_adapter_is_up", return_value=True), \
     mock.patch.object(mac_mod.time, "sleep", return_value=None):
    ok_, msg = mac_mod.restart_adapter("Ethernet")
    check(ok_ and attempts["count"] == 2,
          f"retried enable once, succeeded on 2nd try ({attempts['count']} attempts)")


# ---------------------------------------------------------------------------
section("14. Preset JSON round-trip with mac field")
# ---------------------------------------------------------------------------
p = Preset(
    name="Somerset", ip="10.17.75.240", prefix=24, gateway="10.17.75.1",
    mac="02AABBCCDDEE",
)
blob = json.dumps(asdict(p))
p2 = Preset(**json.loads(blob))
check(p == p2, f"Preset round-trips with mac: {p2}")

# Default mac is empty string (don't-touch).
p3 = Preset(name="DHCP", ip="", prefix=0)
check(p3.mac == "", "Preset.mac defaults to empty string")


# ---------------------------------------------------------------------------
section("15. AppConfig.load tolerates unknown preset keys (forward compat)")
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    path = Path(td) / "config.json"
    path.write_text(json.dumps({
        "selected_nic": "Ethernet",
        "presets": [
            {
                "name": "Future", "ip": "10.0.0.5", "prefix": 24,
                "gateway": "", "dns1": "", "dns2": "",
                "mac": "02AABBCCDDEE",
                "future_field_we_dont_know_about": "whatever",
            },
        ],
        "dhcp": {
            "bind_ip": "10.0.0.5",
            "range_start": "10.0.0.100",
            "range_end": "10.0.0.200",
            "subnet_mask": "255.255.255.0",
            "gateway": "",
            "dns": "8.8.8.8",
            "lease_seconds": 86400,
            "unknown_future_dhcp_field": True,
        },
        "auto_start": False,
        "unknown_top_level": "ignored",
    }), encoding="utf-8")
    with mock.patch("nic_switcher.config.CONFIG_PATH", path):
        cfg = AppConfig.load()
    check(len(cfg.presets) == 1 and cfg.presets[0].name == "Future",
          "unknown preset field didn't wipe presets")
    check(cfg.presets[0].mac == "02AABBCCDDEE",
          f"mac preserved: {cfg.presets[0].mac!r}")
    check(cfg.dhcp.bind_ip == "10.0.0.5",
          "unknown DHCP field didn't break DHCP parse")


# ---------------------------------------------------------------------------
section("16. validate_preset rejects invalid MAC on preset")
# ---------------------------------------------------------------------------
ok_, msg = validate_preset("10.0.0.5", 24, "", "", "", mac="garbage")
check(not ok_ and "Invalid MAC" in msg,
      f"validate_preset rejects bad mac: {msg!r}")
ok_, msg = validate_preset("10.0.0.5", 24, "", "", "", mac="02:AA:BB:CC:DD:EE")
check(ok_, f"validate_preset accepts good mac (msg={msg!r})")
ok_, msg = validate_preset("10.0.0.5", 24, "", "", "", mac="")
check(ok_, "empty mac is fine (don't-touch)")


# ---------------------------------------------------------------------------
section("17. apply_preset ordering: MAC -> adapter restart -> IP (mocked)")
# ---------------------------------------------------------------------------
from nic_switcher import nic as nic_mod

call_log: list[str] = []


def _fake_run(args, timeout=15):
    call_log.append(" ".join(args))
    return 0, "", ""


def _fake_set_mac(name, m):
    call_log.append(f"set_mac({name}, {m})")
    return True, f"MAC set to {mac_mod.format_mac_pretty(mac_mod.normalize_mac(m) or m)}"


with mock.patch.object(nic_mod, "_run", side_effect=_fake_run), \
     mock.patch.object(mac_mod, "set_mac", side_effect=_fake_set_mac):
    preset = Preset(
        name="Somerset", ip="10.17.75.240", prefix=24, gateway="10.17.75.1",
        mac="02:AA:BB:CC:DD:EE",
    )
    ok_, msg = nic_mod.apply_preset("Ethernet", preset)
    check(ok_, f"apply_preset returned ok — msg: {msg!r}")
    # MAC must happen before any IP netsh call.
    mac_idx = next((i for i, s in enumerate(call_log) if s.startswith("set_mac")), -1)
    ip_idx = next((i for i, s in enumerate(call_log)
                   if "set address" in s), -1)
    check(mac_idx != -1 and ip_idx != -1 and mac_idx < ip_idx,
          f"MAC change precedes IP apply (mac@{mac_idx}, ip@{ip_idx}): {call_log}")


# ---------------------------------------------------------------------------
section("18. apply_preset with empty MAC doesn't trigger restart")
# ---------------------------------------------------------------------------
call_log.clear()

with mock.patch.object(nic_mod, "_run", side_effect=_fake_run), \
     mock.patch.object(
         mac_mod, "set_mac",
         side_effect=AssertionError("set_mac must not be called for empty MAC")
     ), \
     mock.patch.object(
         mac_mod, "restore_mac",
         side_effect=AssertionError("restore_mac must not be called for empty MAC")
     ):
    preset = Preset(name="NoMAC", ip="10.1.1.1", prefix=24)
    ok_, msg = nic_mod.apply_preset("Ethernet", preset)
    check(ok_, f"apply_preset ok without MAC — msg: {msg!r}")


# ---------------------------------------------------------------------------
section("19. apply_preset with 'restore' sentinel calls restore_mac")
# ---------------------------------------------------------------------------
call_log.clear()
restore_called = {"n": 0}


def _fake_restore(name):
    restore_called["n"] += 1
    call_log.append(f"restore_mac({name})")
    return True, "Restored hardware MAC"


with mock.patch.object(nic_mod, "_run", side_effect=_fake_run), \
     mock.patch.object(
         mac_mod, "set_mac",
         side_effect=AssertionError("set_mac must not be called for 'restore'")
     ), \
     mock.patch.object(mac_mod, "restore_mac", side_effect=_fake_restore):
    preset = Preset(name="ResetMAC", ip="", prefix=0, mac="restore")
    ok_, msg = nic_mod.apply_preset("Ethernet", preset)
    check(ok_ and restore_called["n"] == 1,
          f"restore_mac called once — msg: {msg!r}")


# ---------------------------------------------------------------------------
section("20. apply_preset short-circuits on bad MAC without touching anything")
# ---------------------------------------------------------------------------
with mock.patch.object(
        nic_mod, "_run",
        side_effect=AssertionError("netsh must not run when MAC invalid")), \
     mock.patch.object(
        mac_mod, "set_mac",
        side_effect=AssertionError("set_mac must not run when MAC invalid")):
    preset = Preset(name="Bad", ip="10.0.0.5", prefix=24, mac="ZZZZZZZZZZZZ")
    ok_, msg = nic_mod.apply_preset("Ethernet", preset)
    check(not ok_ and "Invalid MAC" in msg,
          f"bad-MAC preset rejected cleanly — msg: {msg!r}")


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
