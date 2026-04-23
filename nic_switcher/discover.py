"""Active device discovery — ARP cache, ping sweep, mDNS probe, OUI lookup.

All Windows-native, no external deps.
"""
from __future__ import annotations

import concurrent.futures
import ctypes
import socket
import struct
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Optional

CREATE_NO_WINDOW = 0x08000000

# ---------------------------------------------------------------------------
# OUI table — curated for pro AV + common network gear.
# Format: first 3 bytes of MAC (uppercase, no separators) -> (vendor, kind)
# ---------------------------------------------------------------------------
OUI_DB: dict[str, tuple[str, str]] = {
    # --- Pro AV cores / DSPs / control ---
    "006074": ("QSC Audio (Q-SYS)", "qsys"),
    "00907F": ("QSC Audio", "qsys"),
    "00107F": ("Crestron Electronics", "crestron"),
    "0050C2": ("Crestron Electronics", "crestron"),
    "C44A56": ("Crestron Electronics", "crestron"),
    "00905E": ("Biamp Systems (Tesira)", "biamp"),
    "F04A2B": ("Biamp Systems", "biamp"),
    "0005A6": ("Extron Electronics", "extron"),
    "000B8C": ("Extron", "extron"),
    "001DC1": ("Extron", "extron"),
    "001B92": ("AMX LLC", "amx"),
    "0000AF": ("AMX", "amx"),
    "000EDD": ("Shure Incorporated", "shure"),
    "3CA72B": ("Shure", "shure"),
    "000CCC": ("Lab X Tech (Dante)", "dante"),
    "00A07E": ("Audinate (Dante)", "dante"),
    "4C3C16": ("Audinate (Dante)", "dante"),
    "0013E8": ("ClearOne", "clearone"),
    "00095B": ("NETGEAR", "switch"),
    "28C68E": ("NETGEAR", "switch"),
    "B03956": ("NETGEAR", "switch"),
    "001B48": ("Lutron Electronics", "lutron"),
    "08EA40": ("Lutron", "lutron"),
    "001D3D": ("Mersive (Solstice)", "solstice"),

    # --- Switches / network infra ---
    "000142": ("Cisco", "switch"),
    "000163": ("Cisco", "switch"),
    "00E04C": ("Realtek", "host"),
    "FCFBFB": ("Cisco", "switch"),
    "001795": ("Cisco", "switch"),
    "001759": ("Cisco", "switch"),
    "3C0E23": ("Cisco", "switch"),
    "00E0C6": ("Cisco", "switch"),
    "001A1E": ("Aruba Networks (HPE)", "switch"),
    "3CA82A": ("Aruba Networks", "switch"),
    "94B40F": ("Aruba Networks", "switch"),
    "20A6CD": ("Aruba", "switch"),
    "000496": ("Extreme Networks", "switch"),
    "00E02B": ("Extreme Networks", "switch"),
    "000585": ("Juniper Networks", "switch"),
    "00121E": ("Juniper", "switch"),
    "0418D6": ("Ubiquiti", "switch"),
    "245A4C": ("Ubiquiti", "switch"),
    "44D9E7": ("Ubiquiti", "switch"),
    "F09FC2": ("Ubiquiti", "switch"),
    "78BEB6": ("Ubiquiti", "switch"),
    "F0AD4E": ("Microchip (many)", "host"),
    "0001E6": ("HP", "switch"),
    "0002A5": ("HP", "switch"),
    "0004EA": ("HP", "switch"),
    "3C2C30": ("Ruckus Wireless", "switch"),
    "C0742B": ("Ruckus", "switch"),

    # --- Common hosts ---
    "000C29": ("VMware", "host"),
    "005056": ("VMware", "host"),
    "001B21": ("Intel", "host"),
    "001517": ("Intel", "host"),
    "94B86D": ("Intel", "host"),
    "D8BBC1": ("Intel", "host"),
    "000874": ("Dell", "host"),
    "00065B": ("Dell", "host"),
    "00215A": ("HP", "host"),
    "F0921C": ("HP", "host"),
    "001E8C": ("ASUSTek", "host"),
    "10F60A": ("Apple", "host"),
    "3C15C2": ("Apple", "host"),
    "BCD074": ("Apple", "host"),
    "B827EB": ("Raspberry Pi", "host"),
    "DCA632": ("Raspberry Pi", "host"),
    "E45F01": ("Raspberry Pi", "host"),
    "4473D6": ("Logitech", "host"),
}


def oui_lookup(mac: str) -> tuple[Optional[str], Optional[str]]:
    """Return (vendor, kind) or (None, None) if unknown."""
    key = mac.replace(":", "").replace("-", "").upper()[:6]
    return OUI_DB.get(key, (None, None))


# ---------------------------------------------------------------------------
# Windows ARP cache via iphlpapi.GetIpNetTable
# ---------------------------------------------------------------------------
class _MIB_IPNETROW(ctypes.Structure):
    _fields_ = [
        ("dwIndex", wintypes.DWORD),
        ("dwPhysAddrLen", wintypes.DWORD),
        ("bPhysAddr", ctypes.c_ubyte * 8),
        ("dwAddr", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
    ]


# MIB_IPNET_TYPE_* constants
_MIB_IPNET_TYPE_OTHER = 1
_MIB_IPNET_TYPE_INVALID = 2

_iphlpapi = ctypes.windll.iphlpapi
_iphlpapi.GetIpNetTable.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL,
]
_iphlpapi.GetIpNetTable.restype = wintypes.DWORD

_ERROR_INSUFFICIENT_BUFFER = 122
_NO_ERROR = 0


def read_arp_cache() -> list[tuple[str, str]]:
    """Return [(ip, mac)] for all dynamic/static ARP entries."""
    size = wintypes.ULONG(0)
    rc = _iphlpapi.GetIpNetTable(None, ctypes.byref(size), False)
    if rc not in (_ERROR_INSUFFICIENT_BUFFER, _NO_ERROR) or size.value == 0:
        return []
    buf = (ctypes.c_ubyte * size.value)()
    rc = _iphlpapi.GetIpNetTable(buf, ctypes.byref(size), False)
    if rc != _NO_ERROR:
        return []
    n = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
    if n == 0:
        return []
    offset = ctypes.sizeof(wintypes.DWORD)
    rows = (_MIB_IPNETROW * n).from_address(ctypes.addressof(buf) + offset)
    out: list[tuple[str, str]] = []
    for r in rows:
        if r.dwPhysAddrLen == 0:
            continue
        if r.dwType in (_MIB_IPNET_TYPE_OTHER, _MIB_IPNET_TYPE_INVALID):
            continue
        ip = socket.inet_ntoa(struct.pack("<I", r.dwAddr))
        mac = ":".join(f"{b:02X}" for b in r.bPhysAddr[: r.dwPhysAddrLen])
        if ip.startswith("224.") or ip == "255.255.255.255" or ip.endswith(".255"):
            continue
        out.append((ip, mac))
    return out


# ---------------------------------------------------------------------------
# Ping sweep — uses IcmpSendEcho; populates ARP for replies.
# ---------------------------------------------------------------------------
_icmp = ctypes.windll.iphlpapi
_icmp.IcmpCreateFile.restype = wintypes.HANDLE
_icmp.IcmpSendEcho.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.WORD,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
]
_icmp.IcmpSendEcho.restype = wintypes.DWORD
_icmp.IcmpCloseHandle.argtypes = [wintypes.HANDLE]


def _ping_one(ip: str, timeout_ms: int = 400) -> bool:
    h = _icmp.IcmpCreateFile()
    if not h:
        return False
    try:
        data = b"ping"
        reply_buf = ctypes.create_string_buffer(100)
        addr = struct.unpack("<I", socket.inet_aton(ip))[0]
        n = _icmp.IcmpSendEcho(
            h, addr, data, len(data), None,
            reply_buf, ctypes.sizeof(reply_buf), timeout_ms,
        )
        return n > 0
    finally:
        _icmp.IcmpCloseHandle(h)


def ping_sweep(subnet_prefix: str, timeout_ms: int = 400, workers: int = 64,
               stop_event: Optional[threading.Event] = None) -> list[str]:
    """Parallel-ping every host in a /24 (subnet_prefix='10.17.75'). Returns live IPs.

    If stop_event fires, pending futures are cancelled so the call returns promptly.
    """
    targets = [f"{subnet_prefix}.{i}" for i in range(1, 255)]
    alive: list[str] = []
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {ex.submit(_ping_one, t, timeout_ms): t for t in targets}
        for fut in concurrent.futures.as_completed(futs):
            if stop_event is not None and stop_event.is_set():
                break
            try:
                if fut.result():
                    alive.append(futs[fut])
            except Exception:
                pass
    finally:
        # cancel_futures=True actually drops pending work (Python 3.9+).
        ex.shutdown(wait=False, cancel_futures=True)
    return alive


# ---------------------------------------------------------------------------
# mDNS query — one-shot UDP broadcast to 224.0.0.251:5353
# ---------------------------------------------------------------------------
def _dns_encode_name(name: str) -> bytes:
    out = b""
    for label in name.split("."):
        out += bytes([len(label)]) + label.encode("ascii")
    return out + b"\x00"


def mdns_probe(bind_ip: str, questions: Optional[list[str]] = None) -> None:
    """Fire a single mDNS query for common service names. Does not wait for replies —
    the Sniffer picks replies up from the raw socket.
    """
    qs = questions or [
        "_services._dns-sd._udp.local",
        "_qsys._tcp.local",
        "_crestron._tcp.local",
        "_biamp-tesira._tcp.local",
        "_axia-livewire._tcp.local",
        "_workstation._tcp.local",
        "_http._tcp.local",
    ]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
        s.settimeout(1.0)
        for q in qs:
            header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)  # 1 question
            body = _dns_encode_name(q) + struct.pack(">HH", 12, 1)  # PTR, IN
            try:
                s.sendto(header + body, ("224.0.0.251", 5353))
            except OSError:
                pass
        s.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------
# Port signatures — if an IP is seen using this (src or dst) port, tag kind.
PORT_KIND: dict[tuple[str, int], str] = {
    ("udp", 2467): "qsys",
    ("udp", 2468): "qsys",
    ("tcp", 1710): "qsys",
    ("tcp", 1711): "qsys",
    ("udp", 41794): "crestron",
    ("tcp", 41794): "crestron",
    ("tcp", 41795): "crestron",
    ("udp", 319): "dante",  # PTP event
    ("udp", 320): "dante",  # PTP general
    ("udp", 4440): "dante",
    ("udp", 8700): "dante",
    ("tcp", 4455): "biamp",
    ("tcp", 4456): "biamp",
    ("udp", 161): "switch",  # SNMP — usually managed gear
    ("udp", 162): "switch",  # SNMP trap
}

# mDNS service substrings observed in UDP 5353 payloads.
MDNS_KIND: list[tuple[bytes, str]] = [
    (b"_qsys._tcp", "qsys"),
    (b"_qsc._tcp", "qsys"),
    (b"_crestron", "crestron"),
    (b"_biamp-tesira", "biamp"),
    (b"_tesira", "biamp"),
    (b"_axia-livewire", "livewire"),
    (b"_airplay", "apple"),
    (b"_raop", "apple"),
    (b"_googlecast", "chromecast"),
    (b"_printer", "printer"),
    (b"_ipp", "printer"),
    (b"_ssh._tcp", "ssh-host"),
    (b"_workstation", "host"),
    (b"_dante", "dante"),
    (b"_netaudio", "dante"),
]

KIND_LABEL = {
    "qsys": "Q-SYS peripheral",
    "crestron": "Crestron",
    "biamp": "Biamp Tesira",
    "dante": "Dante device",
    "extron": "Extron",
    "amx": "AMX",
    "shure": "Shure",
    "clearone": "ClearOne",
    "lutron": "Lutron",
    "solstice": "Mersive Solstice",
    "switch": "Switch/Router",
    "gateway": "Gateway",
    "apple": "Apple device",
    "chromecast": "Chromecast",
    "printer": "Printer",
    "host": "Host",
    "ssh-host": "SSH host",
    "livewire": "Axia Livewire",
}


def kind_label(kind: Optional[str]) -> str:
    return KIND_LABEL.get(kind or "", "Unknown") if kind else "Unknown"


@dataclass
class Device:
    ip: str
    mac: Optional[str] = None
    vendor: Optional[str] = None
    kind: Optional[str] = None  # best-guess class key (qsys/crestron/switch/…)
    hostname: Optional[str] = None
    ports: set[tuple[str, int]] = field(default_factory=set)
    mdns_services: set[str] = field(default_factory=set)
    packets: int = 0
    last_seen: float = 0.0
    is_gateway: bool = False

    def label(self) -> str:
        return kind_label(self.kind)


def infer_kind(dev: Device) -> Optional[str]:
    # mDNS is most authoritative
    for service in dev.mdns_services:
        sb = service.encode() if isinstance(service, str) else service
        for needle, kind in MDNS_KIND:
            if needle in sb:
                return kind
    # Ports next
    for p in dev.ports:
        if p in PORT_KIND:
            return PORT_KIND[p]
    # OUI / vendor last
    if dev.mac:
        _, kind = oui_lookup(dev.mac)
        if kind:
            return kind
    if dev.is_gateway:
        return "gateway"
    return None


def default_gateway_for(bind_ip: str) -> Optional[str]:
    """Best-effort: find gateway IP via `route print`."""
    try:
        out = subprocess.run(
            ["route", "print", "-4", "0.0.0.0"],
            capture_output=True, text=True, timeout=5,
            creationflags=CREATE_NO_WINDOW,
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                gw = parts[2]
                # prefer the route whose interface IP matches our bind_ip
                if len(parts) >= 5 and parts[3] == bind_ip:
                    return gw
        # fallback: first default route found
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
                return parts[2]
    except Exception:
        return None
    return None
