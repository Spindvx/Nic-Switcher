"""IP / network input validation helpers."""
from __future__ import annotations

import ipaddress
from typing import Optional


def is_valid_ipv4(s: str) -> bool:
    if not s:
        return False
    try:
        addr = ipaddress.IPv4Address(s.strip())
    except ValueError:
        return False
    return not (addr.is_multicast or addr.is_unspecified)


def is_valid_prefix(n: int) -> bool:
    return 1 <= n <= 32


def mask_to_prefix(mask: str) -> Optional[int]:
    """255.255.255.0 → 24. Returns None if mask is invalid or non-contiguous."""
    if not mask:
        return None
    try:
        net = ipaddress.IPv4Network(f"0.0.0.0/{mask.strip()}", strict=False)
        return net.prefixlen
    except (ValueError, ipaddress.NetmaskValueError):
        return None


def prefix_to_mask(prefix: int) -> str:
    """24 → '255.255.255.0'."""
    try:
        return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}", strict=False).netmask)
    except ValueError:
        return "255.255.255.0"


def is_valid_mask(s: str) -> bool:
    return mask_to_prefix(s) is not None


def ips_in_same_subnet(a: str, b: str, prefix: int = 24) -> bool:
    try:
        net_a = ipaddress.IPv4Network(f"{a}/{prefix}", strict=False)
        return ipaddress.IPv4Address(b) in net_a
    except ValueError:
        return False


def last_octet(ip: str) -> Optional[int]:
    try:
        return int(ipaddress.IPv4Address(ip.strip()).packed[3])
    except (ValueError, IndexError):
        return None


def validate_preset(ip: str, prefix: int, gateway: str = "",
                    dns1: str = "", dns2: str = "") -> tuple[bool, str]:
    """Returns (ok, error_message). Empty ip is treated as 'DHCP mode' (valid)."""
    if not ip:
        return True, ""
    if not is_valid_ipv4(ip):
        return False, f"Invalid IP: {ip!r}"
    if not is_valid_prefix(prefix):
        return False, f"Prefix must be 1–32 (got {prefix})"
    if gateway and not is_valid_ipv4(gateway):
        return False, f"Invalid gateway: {gateway!r}"
    if gateway and not ips_in_same_subnet(ip, gateway, prefix):
        return False, f"Gateway {gateway} is not in {ip}/{prefix}"
    for name, dns in (("DNS 1", dns1), ("DNS 2", dns2)):
        if dns and not is_valid_ipv4(dns):
            return False, f"Invalid {name}: {dns!r}"
    return True, ""


def validate_dhcp_range(bind_ip: str, start: str, end: str,
                        mask: str) -> tuple[bool, str, Optional[int]]:
    """Validate DHCP config. Returns (ok, err, end_octet) where end_octet is the
    last octet of `end` (what dhcpsrv's IPPOOL expects)."""
    if not is_valid_ipv4(bind_ip):
        return False, f"Invalid bind IP: {bind_ip!r}", None
    if not is_valid_ipv4(start):
        return False, f"Invalid range start: {start!r}", None
    if not is_valid_ipv4(end):
        return False, f"Invalid range end: {end!r}", None
    if not is_valid_ipv4(mask):
        return False, f"Invalid subnet mask: {mask!r}", None
    try:
        prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask}", strict=False).prefixlen
    except ValueError:
        return False, f"Invalid subnet mask: {mask!r}", None
    if not ips_in_same_subnet(bind_ip, start, prefix):
        return False, f"Range start {start} not in {bind_ip}/{prefix}", None
    if not ips_in_same_subnet(start, end, prefix):
        return False, f"Range end {end} not in same /{prefix} as start {start}", None
    s_int = int(ipaddress.IPv4Address(start))
    e_int = int(ipaddress.IPv4Address(end))
    if s_int > e_int:
        return False, f"Range end {end} is before start {start}", None
    return True, "", last_octet(end)
