"""Network interface discovery and IP configuration via netsh."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

import psutil

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


def apply_preset(nic_name: str, preset: Preset) -> tuple[bool, str]:
    """Apply a preset to the given NIC. Empty IP => switch to DHCP."""
    if not preset.ip:
        return set_dhcp(nic_name)

    ok, err = validate_preset(
        preset.ip, preset.prefix, preset.gateway, preset.dns1, preset.dns2
    )
    if not ok:
        return False, err

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

    if preset.dns1:
        _run([
            "netsh", "interface", "ip", "set", "dnsservers",
            f"name={nic_name}", "static", preset.dns1, "primary",
        ])
        if preset.dns2:
            _run([
                "netsh", "interface", "ip", "add", "dnsservers",
                f"name={nic_name}", preset.dns2, "index=2",
            ])
    else:
        _run([
            "netsh", "interface", "ip", "set", "dnsservers",
            f"name={nic_name}", "dhcp",
        ])
    return True, f"Applied {preset.name} ({preset.ip}/{preset.prefix})"


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
