"""Network interface discovery and IP configuration via netsh."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

import psutil

from . import mac as mac_mod
from .config import Preset
from .validate import validate_preset


@dataclass
class NicInfo:
    name: str
    description: str
    mac: str
    ipv4: Optional[str]
    netmask: Optional[str]
    is_up: bool
    is_loopback: bool


CREATE_NO_WINDOW = 0x08000000


def _run(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    return proc.returncode, proc.stdout, proc.stderr


def list_nics() -> list[NicInfo]:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    nics: list[NicInfo] = []
    for name, addr_list in addrs.items():
        mac = ""
        ipv4 = None
        netmask = None
        for a in addr_list:
            fam = getattr(a.family, "name", str(a.family))
            if fam in ("AF_LINK", "AF_PACKET") or fam == "-1":
                mac = a.address or ""
            elif fam == "AF_INET":
                ipv4 = a.address
                netmask = a.netmask
        st = stats.get(name)
        is_up = bool(st.isup) if st else False
        is_loop = "loopback" in name.lower() or name.lower().startswith("lo")
        nics.append(
            NicInfo(
                name=name,
                description=name,
                mac=mac,
                ipv4=ipv4,
                netmask=netmask,
                is_up=is_up,
                is_loopback=is_loop,
            )
        )
    nics.sort(key=lambda n: (n.is_loopback, not n.is_up, n.name.lower()))
    return nics


def _apply_mac_if_any(nic_name: str, mac_field: str) -> tuple[bool, str]:
    """Handle the Preset.mac field. Returns (ok, err_if_any).

    "" — skip (no adapter restart), return (True, "").
    "restore" — remove override and restart adapter.
    otherwise — treat as a MAC, validate, apply, restart adapter.
    """
    if not mac_field:
        return True, ""
    if mac_field.strip().lower() == "restore":
        return mac_mod.restore_mac(nic_name)
    return mac_mod.set_mac(nic_name, mac_field)


def apply_preset(nic_name: str, preset: Preset) -> tuple[bool, str]:
    """Apply a preset to the given NIC. Empty IP => switch to DHCP."""
    from .validate import valid_nic_name
    if not valid_nic_name(nic_name):
        return False, f"Invalid NIC name: {nic_name!r}"
    # Validate up front so a bad MAC never kicks off a partial apply.
    ok, err = validate_preset(
        preset.ip, preset.prefix, preset.gateway, preset.dns1, preset.dns2,
        preset.mac,
    )
    if not ok:
        return False, err

    # MAC first — it forces an adapter restart, which drops any IP config.
    # Applying IP afterwards is correct ordering.
    mac_ok, mac_err = _apply_mac_if_any(nic_name, preset.mac)
    if not mac_ok:
        return False, mac_err

    if not preset.ip:
        dhcp_ok, dhcp_msg = set_dhcp(nic_name)
        if not dhcp_ok:
            return False, dhcp_msg
        if preset.mac:
            return True, f"{dhcp_msg} (MAC updated)"
        return True, dhcp_msg

    rc, out, err = _run(
        [
            "netsh", "interface", "ip", "set", "address",
            f"name={nic_name}", "static",
            preset.ip, preset.subnet_mask,
            preset.gateway or "none",
        ]
    )
    if rc != 0:
        return False, (err or out).strip() or "netsh failed"

    dns_errors: list[str] = []
    if preset.dns1:
        rc, _, err = _run([
            "netsh", "interface", "ip", "set", "dnsservers",
            f"name={nic_name}", "static", preset.dns1, "primary",
        ])
        if rc != 0:
            dns_errors.append(f"DNS1: {(err or '').strip()}")
        if preset.dns2:
            rc, _, err = _run([
                "netsh", "interface", "ip", "add", "dnsservers",
                f"name={nic_name}", preset.dns2, "index=2",
            ])
            if rc != 0:
                dns_errors.append(f"DNS2: {(err or '').strip()}")
    else:
        _run([
            "netsh", "interface", "ip", "set", "dnsservers",
            f"name={nic_name}", "dhcp",
        ])
    suffix = " + MAC" if preset.mac else ""
    if dns_errors:
        return True, f"Applied {preset.name} ({preset.ip}/{preset.prefix}){suffix} — DNS warnings: {'; '.join(dns_errors)}"
    return True, f"Applied {preset.name} ({preset.ip}/{preset.prefix}){suffix}"


def set_dhcp(nic_name: str) -> tuple[bool, str]:
    rc1, _, err1 = _run([
        "netsh", "interface", "ip", "set", "address",
        f"name={nic_name}", "dhcp",
    ])
    rc2, _, err2 = _run([
        "netsh", "interface", "ip", "set", "dnsservers",
        f"name={nic_name}", "dhcp",
    ])
    if rc1 != 0:
        return False, err1.strip() or "Failed to enable DHCP"
    return True, "Switched to DHCP"


def current_ip(nic_name: str) -> Optional[str]:
    for n in list_nics():
        if n.name == nic_name:
            return n.ipv4
    return None
