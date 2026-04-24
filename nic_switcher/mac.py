"""MAC address spoofing — native implementation.

Uses the same mechanism as Technitium MAC Address Changer (TMAC):

    1. Find the adapter's registry subkey under
       HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4D36E972-E325-11CE-BFC1-08002BE10318}
       by matching NetCfgInstanceId to the adapter's GUID (looked up via the
       Network\\{class}\\{guid}\\Connection\\Name registry mapping).
    2. Write (or delete) the `NetworkAddress` REG_SZ value — 12 hex chars,
       no separators, no 0x.
    3. Disable + enable the adapter via `netsh interface set interface` so
       the driver re-reads the registry value.

No third-party tools. No bundled binaries. Needs admin (HKLM write + netsh).
"""
from __future__ import annotations

import random
import re
import subprocess
import time
from typing import Optional

try:
    import winreg  # stdlib on Windows
except ImportError:  # non-Windows — kept importable for unit tests on any host
    winreg = None  # type: ignore[assignment]


# Class GUID for "Net" — identifies network adapters in both registry trees.
NET_CLASS_GUID = "{4D36E972-E325-11CE-BFC1-08002BE10318}"
CLASS_KEY = rf"SYSTEM\CurrentControlSet\Control\Class\{NET_CLASS_GUID}"
NETWORK_KEY = rf"SYSTEM\CurrentControlSet\Control\Network\{NET_CLASS_GUID}"

CREATE_NO_WINDOW = 0x08000000

# How long to wait for the adapter to come back up after enable.
_ENABLE_TIMEOUT_SEC = 12.0
_POLL_INTERVAL_SEC = 0.4


# ---------------------------------------------------------------------------
# Pure helpers — no OS calls, safe to unit test everywhere
# ---------------------------------------------------------------------------

_HEX12 = re.compile(r"^[0-9A-F]{12}$")


def normalize_mac(s: str) -> Optional[str]:
    """Strip separators, uppercase. Returns 12 hex chars or None if invalid.

    Accepts: 'AA:BB:CC:DD:EE:FF', 'aa-bb-cc-dd-ee-ff', 'AABB.CCDD.EEFF',
    'aabbccddeeff', and any mix of the above.
    """
    if not s:
        return None
    cleaned = re.sub(r"[\s:\-.]", "", s).upper()
    return cleaned if _HEX12.match(cleaned) else None


def format_mac_pretty(mac12: str) -> str:
    """'AABBCCDDEEFF' -> 'AA:BB:CC:DD:EE:FF'. Assumes input is already normalized."""
    return ":".join(mac12[i:i + 2] for i in range(0, 12, 2))


def is_multicast(mac12: str) -> bool:
    """First octet LSB set => multicast (reserved, Windows rejects these)."""
    return bool(int(mac12[0:2], 16) & 0x01)


def is_locally_administered(mac12: str) -> bool:
    """First octet bit 1 set => locally-administered (safe for spoofing)."""
    return bool(int(mac12[0:2], 16) & 0x02)


def validate_mac(s: str) -> tuple[bool, str, Optional[str]]:
    """Returns (ok, error_message, normalized_mac).

    Rejects:
      - non-hex / wrong length
      - multicast addresses (first octet LSB set)
      - all zero (00:00:00:00:00:00)
      - broadcast (FF:FF:FF:FF:FF:FF)
    Does NOT require locally-administered — user may want to spoof a specific
    device's MAC, which is a legitimate TMAC use case.
    """
    norm = normalize_mac(s)
    if norm is None:
        return False, f"Invalid MAC address: {s!r} (expect 12 hex chars)", None
    if is_multicast(norm):
        return False, "MAC is multicast (first octet LSB must be 0)", None
    if norm == "000000000000":
        return False, "MAC cannot be all zero", None
    if norm == "FFFFFFFFFFFF":
        return False, "MAC cannot be broadcast (FF:FF:FF:FF:FF:FF)", None
    return True, "", norm


def random_locally_administered_mac(rng: Optional[random.Random] = None) -> str:
    """Generate a random valid unicast, locally-administered MAC.

    First octet: low 2 bits forced to binary 10 (unicast + locally-administered),
    other 6 bits random. Remaining 5 bytes uniformly random.
    """
    r = rng or random
    first = (r.randrange(0, 256) & 0xFC) | 0x02
    rest = [r.randrange(0, 256) for _ in range(5)]
    return "".join(f"{b:02X}" for b in [first, *rest])


# ---------------------------------------------------------------------------
# Registry lookup — maps NIC display name to the Class\NNNN subkey
# ---------------------------------------------------------------------------

def _require_winreg():
    if winreg is None:
        raise RuntimeError("MAC operations require Windows (winreg unavailable)")


def _adapter_guid_for_name(nic_name: str) -> Optional[str]:
    """Look up the adapter GUID (e.g. '{ABC-123-...}') for a display name like
    'Ethernet' by walking HKLM\\...\\Network\\{class}\\{guid}\\Connection\\Name.
    """
    _require_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_KEY) as top:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(top, i)
                except OSError:
                    return None
                i += 1
                # Skip non-adapter subkeys like "Descriptions", "Config"
                if not (guid.startswith("{") and guid.endswith("}")):
                    continue
                try:
                    with winreg.OpenKey(top, rf"{guid}\Connection") as conn:
                        name, _ = winreg.QueryValueEx(conn, "Name")
                except OSError:
                    continue
                if name == nic_name:
                    return guid
    except OSError:
        return None


def find_adapter_registry_key(nic_name: str) -> Optional[str]:
    """Returns the subkey path like r'SYSTEM\\...\\Class\\{class}\\0007' for this
    NIC, or None if not found.
    """
    _require_winreg()
    guid = _adapter_guid_for_name(nic_name)
    if not guid:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CLASS_KEY) as top:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(top, i)
                except OSError:
                    return None
                i += 1
                # Only numeric subkeys represent adapter instances (e.g. "0001").
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(top, sub) as s:
                        val, _ = winreg.QueryValueEx(s, "NetCfgInstanceId")
                except OSError:
                    continue
                if val.upper() == guid.upper():
                    return rf"{CLASS_KEY}\{sub}"
    except OSError:
        return None


def _read_reg_mac(subkey_path: str) -> Optional[str]:
    """Read the NetworkAddress override from a registry subkey, or None if unset."""
    _require_winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey_path) as k:
            val, _ = winreg.QueryValueEx(k, "NetworkAddress")
            return normalize_mac(val) if val else None
    except OSError:
        return None


def _write_reg_mac(subkey_path: str, mac12: str) -> None:
    _require_winreg()
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, subkey_path, 0, winreg.KEY_SET_VALUE
    ) as k:
        winreg.SetValueEx(k, "NetworkAddress", 0, winreg.REG_SZ, mac12)


def _delete_reg_mac(subkey_path: str) -> None:
    """Delete the NetworkAddress value. Missing value is not an error."""
    _require_winreg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, subkey_path, 0, winreg.KEY_SET_VALUE
        ) as k:
            winreg.DeleteValue(k, "NetworkAddress")
    except FileNotFoundError:
        pass
    except OSError as e:
        # winreg raises FileNotFoundError on 3.6+ but OSError on older —
        # swallow only "value does not exist" (WinError 2).
        if getattr(e, "winerror", None) != 2:
            raise


# ---------------------------------------------------------------------------
# Adapter restart — disable + enable via netsh
# ---------------------------------------------------------------------------

def _run_netsh(args: list[str], timeout: int = 20) -> tuple[int, str]:
    """Run netsh and return (rc, msg). A timeout or OSError is captured and
    returned as rc=1 so callers (e.g. restart_adapter) can keep executing the
    recovery path instead of letting the exception propagate and leave the
    adapter disabled."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        return 1, f"netsh timed out after {timeout}s"
    except OSError as e:
        return 1, f"netsh failed to launch: {e}"
    return proc.returncode, ((proc.stderr or proc.stdout) or "").strip()


def _adapter_is_up(nic_name: str) -> bool:
    """Poll psutil for adapter up-state. Defensive — import locally so tests on
    non-Windows can mock the adapter path without importing psutil."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        st = stats.get(nic_name)
        return bool(st and st.isup)
    except Exception:
        return False


def _disable(nic_name: str) -> tuple[bool, str]:
    rc, out = _run_netsh([
        "netsh", "interface", "set", "interface",
        f'name={nic_name}', "admin=disabled",
    ])
    if rc != 0:
        return False, out or "netsh disable failed"
    return True, ""


def _enable(nic_name: str) -> tuple[bool, str]:
    rc, out = _run_netsh([
        "netsh", "interface", "set", "interface",
        f'name={nic_name}', "admin=enabled",
    ])
    if rc != 0:
        return False, out or "netsh enable failed"
    return True, ""


def restart_adapter(nic_name: str) -> tuple[bool, str]:
    """Disable + enable + wait for up. Any failure is surfaced — but we ALWAYS
    attempt the enable step even if disable reported an error, so a half-torn-
    down adapter can never be left disabled because of this function.
    """
    disable_ok, disable_err = _disable(nic_name)
    # Small settle so the driver releases before we re-enable.
    time.sleep(0.4)

    enable_ok, enable_err = _enable(nic_name)
    if not enable_ok:
        # Retry once — some drivers take >1s to release before enable can grab.
        time.sleep(1.0)
        enable_ok, enable_err = _enable(nic_name)

    if not enable_ok:
        return False, (
            f"Adapter enable failed: {enable_err or '(no detail)'} "
            f"— re-enable '{nic_name}' manually from Network Connections."
        )

    # Wait for the adapter to actually come up — driver may take a few seconds.
    deadline = time.time() + _ENABLE_TIMEOUT_SEC
    while time.time() < deadline:
        if _adapter_is_up(nic_name):
            break
        time.sleep(_POLL_INTERVAL_SEC)

    if not disable_ok:
        # Enable succeeded, but disable had reported an error — MAC may not
        # have been re-read. Be honest about it.
        return False, f"Adapter re-enabled but disable had reported: {disable_err}"

    return True, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def current_mac(nic_name: str) -> Optional[str]:
    """Current in-use MAC of the NIC, 12 hex uppercase — or None."""
    try:
        import psutil
        addrs = psutil.net_if_addrs().get(nic_name, [])
        for a in addrs:
            fam = getattr(a.family, "name", str(a.family))
            if fam in ("AF_LINK", "AF_PACKET") or fam == "-1":
                return normalize_mac(a.address or "")
    except Exception:
        pass
    return None


# Hardware MAC is invariant for the life of the adapter, so we memoize it
# across calls. Each lookup spawns PowerShell (~150ms cold) — the popup's
# refresh path can fire it many times per session, so caching matters.
_HARDWARE_MAC_CACHE: dict[str, Optional[str]] = {}


def hardware_mac(nic_name: str) -> Optional[str]:
    """The NIC's permanent (burned-in) MAC, bypassing any override.

    Uses PowerShell's Get-NetAdapter.PermanentAddress. PowerShell is shipped
    with every supported Windows version, so this doesn't add a dependency.
    Result (including failures) is cached per-NIC for the process lifetime —
    a slow PowerShell spawn (Defender scanning a fresh process can push it
    past 5s on some boxes) gets paid at most once per NIC per session.
    """
    if nic_name in _HARDWARE_MAC_CACHE:
        return _HARDWARE_MAC_CACHE[nic_name]
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-NetAdapter -Name '{nic_name}' "
                "-ErrorAction SilentlyContinue).PermanentAddress",
            ],
            capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        # Cache the failure too — re-paying the timeout on every popup
        # refresh would freeze the UI for seconds at a time.
        _HARDWARE_MAC_CACHE[nic_name] = None
        return None
    if proc.returncode != 0:
        _HARDWARE_MAC_CACHE[nic_name] = None
        return None
    result = normalize_mac(proc.stdout.strip())
    _HARDWARE_MAC_CACHE[nic_name] = result
    return result


def set_mac(nic_name: str, mac: str) -> tuple[bool, str]:
    """Override the NIC's MAC. Validates, writes registry, restarts adapter.

    Returns (ok, human_readable_msg).
    """
    ok, err, norm = validate_mac(mac)
    if not ok:
        return False, err

    key = find_adapter_registry_key(nic_name)
    if not key:
        return False, f"Could not locate adapter '{nic_name}' in the registry"

    try:
        _write_reg_mac(key, norm)
    except PermissionError:
        return False, "Registry write denied — restart NIC Switcher as administrator"
    except OSError as e:
        return False, f"Registry write failed: {e}"

    ok, msg = restart_adapter(nic_name)
    if not ok:
        return False, msg
    return True, f"MAC set to {format_mac_pretty(norm)}"


def restore_mac(nic_name: str) -> tuple[bool, str]:
    """Remove the MAC override and restart the adapter so the hardware MAC
    is re-read from the NIC."""
    key = find_adapter_registry_key(nic_name)
    if not key:
        return False, f"Could not locate adapter '{nic_name}' in the registry"
    try:
        _delete_reg_mac(key)
    except PermissionError:
        return False, "Registry write denied — restart NIC Switcher as administrator"
    except OSError as e:
        return False, f"Registry write failed: {e}"
    ok, msg = restart_adapter(nic_name)
    if not ok:
        return False, msg
    return True, "Restored hardware MAC"


def has_override(nic_name: str) -> Optional[bool]:
    """True if a NetworkAddress override is currently set in the registry,
    False if no override, None if the adapter can't be located."""
    key = find_adapter_registry_key(nic_name)
    if not key:
        return None
    return _read_reg_mac(key) is not None
