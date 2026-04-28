"""Active device discovery — ARP cache, ping sweep, mDNS probe, OUI lookup.

All Windows-native, no external deps.
"""
from __future__ import annotations

import concurrent.futures
import ctypes
import re
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
# OUI table — curated for Pro AV, network infrastructure, and common
# workstation/IoT vendors.
#
# Source format: vendor name -> (kind, "OUI1 OUI2 OUI3 ...") where each OUI
# is the first 3 bytes of the MAC (uppercase, no separators). One line per
# manufacturer keeps the table wide without exploding in length — to add a
# vendor, append a single row. The runtime lookup dict is built once at
# import time by `_build_oui_db` below.
# ---------------------------------------------------------------------------
_OUI_GROUPS: dict[str, tuple[str, str]] = {
    # ── Pro AV — DSPs, cores, control, audio ──
    "QSC Audio (Q-SYS)":        ("qsys",      "006074 00907F 0090D5"),
    "Crestron Electronics":     ("crestron",  "00107F 0050C2 C44A56 00900B"),
    "Biamp Systems":            ("biamp",     "00905E F04A2B B4994C"),
    "Audinate (Dante)":         ("dante",     "00A07E 4C3C16 00101C 00BD3A 04E536 000CCC"),
    "Extron Electronics":       ("extron",    "0005A6 000B8C 001DC1 0020C2"),
    "AMX (Harman)":             ("amx",       "001B92 0000AF 9CC9EB"),
    "Shure":                    ("shure",     "000EDD 3CA72B 0025D1 9C8275"),
    "Sennheiser":               ("shure",     "00029A 1859F5"),
    "ClearOne":                 ("clearone",  "0013E8 00B33D"),
    "Lutron Electronics":       ("lutron",    "001B48 08EA40"),
    "Mersive (Solstice)":       ("solstice",  "001D3D"),
    "Yamaha (Audio)":           ("yamaha",    "0002C7 00A0DE"),
    "Lightware":                ("extron",    "002272"),
    "Kramer Electronics":       ("extron",    "1C06B2"),
    "Atlona":                   ("extron",    "005B91"),

    # ── Video conferencing / IP phones ──
    "Cisco TelePresence/Webex": ("videoconf", "00036B 002F72 B0286C 003064"),
    "Polycom / Poly":           ("videoconf", "001641 0004F2 64167F"),
    "Logitech (Video)":         ("videoconf", "002201 88E625 00306D 4473D6"),
    "Lifesize":                 ("videoconf", "000DBD"),
    "Tandberg":                 ("videoconf", "001320"),
    "AVer Information":         ("videoconf", "003E64 ECD16E"),

    # ── Displays / projectors / signage ──
    "Samsung":                  ("display",   "001A8A 002566 0050BA 002378"),
    "LG":                       ("display",   "00266E 0050BD 700627"),
    "NEC Display":               ("display",  "000FBB 8038FD"),
    "Panasonic AV":             ("display",   "0050E4 0080F0 002022"),
    "Sony AV":                  ("display",   "24A42C 7C30E0"),
    "Barco":                    ("display",   "00036A 001A90"),
    "Christie":                 ("display",   "00C094"),
    "Epson":                    ("display",   "0026AB 5847CA 64EB8C"),
    "BenQ":                     ("display",   "001E58 00C09F"),
    "Sharp":                    ("display",   "0080CC 5C497D"),
    "ViewSonic":                ("display",   "002438 90A4DE"),

    # ── Cameras (PTZ / streaming / IP security) ──
    "Sony PTZ":                 ("camera",    "000B1F"),
    "Vaddio":                   ("camera",    "00197F 245CFC"),
    "Panasonic AW":             ("camera",    "00B0D0"),
    "Axis Communications":      ("camera",    "00408C ACCC8E B8A44F"),
    "Hikvision":                ("camera",    "44190B 28571C BC9B5E"),
    "Dahua":                    ("camera",    "3C1A57 4C11BF 90020A"),

    # ── Workstations / laptops / desktops ──
    "Dell":                     ("host",      "000874 00065B 00188B 1866DA F4521D 78AB60 8030E0"),
    "HP / HPE":                 ("host",      "00215A F0921C 0011B0 6CB311 9457A5 70F39C"),
    "Lenovo":                   ("host",      "FC01CD A4B197 D0F4F7 88A4C2 14ABC5 EC2E98 A48830"),
    "Apple":                    ("host",      "10F60A 3C15C2 BCD074 040CCE 0CBC9F D023DB"),
    "Asus":                     ("host",      "001E8C 1C872C 9C5C8E AC9E17 0411E5"),
    "Acer":                     ("host",      "000034 0023A0 1881D5 D850E6"),
    "Microsoft Surface/Xbox":   ("host",      "002248 1090C0 5C514F 70F1E5 7C1E52 50E54D"),
    "MSI":                      ("host",      "001731 309C23 D43D7E"),
    "Razer":                    ("host",      "C8E26C E4AAA0"),
    "Toshiba":                  ("host",      "00A0D1 002708"),

    # ── SBCs / IoT / makers ──
    "Raspberry Pi":             ("host",      "B827EB DCA632 E45F01 D83ADD 28CDC1 2CCF67"),
    "Espressif (ESP32/8266)":   ("host",      "240AC4 4C75BB 8CAAB5 A4CF12 BCDDC2"),
    "Arduino":                  ("host",      "A8610A 90A2DA"),

    # ── NIC chipsets / virtualization ──
    "Intel":                    ("host",      "001B21 001517 94B86D D8BBC1 001E67 70665A 8C8590 7C7A91"),
    "Realtek":                  ("host",      "00E04C 5C260A E03F49"),
    "Broadcom":                 ("host",      "001018 D0D2B0"),
    "Microchip":                ("host",      "F0AD4E"),
    "VMware":                   ("host",      "000C29 005056"),
    "Parallels":                ("host",      "001C42"),
    "Oracle VirtualBox":        ("host",      "080027"),

    # ── Network infrastructure (switches / routers / APs / firewalls) ──
    "Cisco":                    ("switch",    "000142 000163 00E0C6 001795 001759 3C0E23 FCFBFB"),
    "Aruba (HPE)":              ("switch",    "001A1E 3CA82A 94B40F 20A6CD 4007C7"),
    "Juniper Networks":         ("switch",    "000585 00121E 7C950F"),
    "Extreme Networks":         ("switch",    "000496 00E02B"),
    "Ubiquiti":                 ("switch",    "0418D6 245A4C 44D9E7 F09FC2 78BEB6 802AA8 D80D17"),
    "NETGEAR":                  ("switch",    "00095B 28C68E B03956 9C3DCF 04A151 000FB5"),
    "MikroTik":                 ("switch",    "B86191 4C5E0C 6C3B6B"),
    "TP-Link":                  ("switch",    "1027F5 60E327 9C5322 EC086B"),
    "D-Link":                   ("switch",    "001195 002401 00179A FCAA14"),
    "Ruckus Wireless":          ("switch",    "3C2C30 C0742B"),
    "HP ProCurve":              ("switch",    "0001E6 0002A5 0004EA 001083"),
    "Meraki (Cisco)":           ("switch",    "0018BA E0CB4E 88158D"),
    "Fortinet":                 ("switch",    "00094F 70124B 90F35F"),

    # ── Printers / MFPs ──
    "HP Printer":               ("printer",   "001B78 002655 38EAA7"),
    "Canon":                    ("printer",   "00BBCB 0090A9"),
    "Brother":                  ("printer",   "0080F4 30055C"),
    "Ricoh":                    ("printer",   "00266C 002673"),
    "Epson Printer":            ("printer",   "640381 60634B 9C9C1F"),
    "Xerox":                    ("printer",   "000048 080036 0000AA"),
    "Kyocera":                  ("printer",   "00C0EE 003020"),
    "Lexmark":                  ("printer",   "000400 002000 0021B7"),
    "Konica Minolta":           ("printer",   "00204F 008087 002BB1"),

    # ── Streaming / smart home / consumer AV ──
    "Google (Chromecast/Nest)": ("chromecast","6CADF8 F4F5D8 F88FCA 1C5A3E"),
    "Roku":                     ("chromecast","B0A737 D8311C 0826AE"),
    "Amazon (Echo/Fire)":       ("chromecast","FCA667 78E103 4063B7 FCA183"),
    "Sonos":                    ("apple",     "00079A 78283F B8E937"),
    "Bose":                     ("apple",     "0020D8 488ED2"),

    # ── Storage / NAS ──
    "Synology":                 ("host",      "001132 00113263 0011326C"),
    "QNAP":                     ("host",      "245EBE 246511"),
    "Western Digital":          ("host",      "00145C"),
    "NetApp":                   ("host",      "00A098 0090A4 002A6A"),
}


def _build_oui_db() -> dict[str, tuple[str, str]]:
    """Expand `_OUI_GROUPS` to the runtime lookup table. Earlier declarations
    win on prefix conflicts — order `_OUI_GROUPS` so the more informative
    vendor (e.g. Crestron DM) appears before generic chipset vendors that
    might share a block."""
    db: dict[str, tuple[str, str]] = {}
    for vendor, (kind, ouis) in _OUI_GROUPS.items():
        for oui in ouis.split():
            key = oui.upper()
            if key not in db:
                db[key] = (vendor, kind)
    return db


OUI_DB: dict[str, tuple[str, str]] = _build_oui_db()


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
        # Universal meta-query: asks for all registered services.
        "_services._dns-sd._udp.local",
        # Pro AV cores & control
        "_qsys._tcp.local",
        "_qsys-ctrl._tcp.local",
        "_crestron._tcp.local",
        "_biamp-tesira._tcp.local",
        "_tesira._tcp.local",
        "_axia-livewire._tcp.local",
        # Dante (all Audinate service names)
        "_netaudio-arc._udp.local",
        "_netaudio-chan._udp.local",
        "_netaudio-cmc._udp.local",
        "_netaudio-dbc._udp.local",
        # Shure
        "_shure-dcs._tcp.local",
        "_shure-audio._tcp.local",
        # Video conferencing
        "_cisco-phone._tcp.local",
        "_polycom._tcp.local",
        # Generic
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
    # --- Pro AV (priority) ---
    (b"_qsys._tcp", "qsys"),
    (b"_qsc._tcp", "qsys"),
    (b"_qsys-ctrl", "qsys"),
    (b"_crestron", "crestron"),
    (b"_biamp-tesira", "biamp"),
    (b"_tesira", "biamp"),
    (b"_avahi-tesira", "biamp"),
    (b"_axia-livewire", "livewire"),
    # Dante — all Audinate service names
    (b"_netaudio-arc", "dante"),
    (b"_netaudio-chan", "dante"),
    (b"_netaudio-cmc", "dante"),
    (b"_netaudio-dbc", "dante"),
    (b"_netaudio", "dante"),
    (b"_dante", "dante"),
    # Video conferencing
    (b"_cisco-phone", "videoconf"),
    (b"_polycom", "videoconf"),
    (b"_webex", "videoconf"),
    # Control systems
    (b"_shure-dcs", "shure"),
    (b"_shure-audio", "shure"),
    (b"_extron-cp", "extron"),
    (b"_amx-axlink", "amx"),
    # --- Consumer / generic (lower priority) ---
    (b"_airplay", "apple"),
    (b"_raop", "apple"),
    (b"_googlecast", "chromecast"),
    (b"_printer", "printer"),
    (b"_ipp", "printer"),
    (b"_ssh._tcp", "ssh-host"),
    (b"_workstation", "host"),
]

KIND_LABEL = {
    "qsys": "Q-SYS",
    "crestron": "Crestron",
    "biamp": "Biamp Tesira",
    "dante": "Dante",
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
    "videoconf": "Video Conf",
    "display": "Display/Projector",
    "camera": "Camera",
    "yamaha": "Yamaha Audio",
}

# Kinds that are Pro AV devices, for UI grouping + prioritization.
AV_KINDS = frozenset({
    "qsys", "crestron", "biamp", "dante", "extron", "amx", "shure",
    "clearone", "lutron", "solstice", "livewire", "videoconf", "display",
    "camera", "yamaha",
})


def is_av(kind: Optional[str]) -> bool:
    return kind in AV_KINDS


def kind_label(kind: Optional[str]) -> str:
    return KIND_LABEL.get(kind or "", "Unknown") if kind else "Unknown"


@dataclass
class Device:
    ip: str
    mac: Optional[str] = None
    vendor: Optional[str] = None
    kind: Optional[str] = None  # best-guess class key (qsys/crestron/switch/…)
    confidence: int = 0          # 0-100, how sure we are about `kind`
    hostname: Optional[str] = None
    ports: set[tuple[str, int]] = field(default_factory=set)
    mdns_services: set[str] = field(default_factory=set)
    http_banner: Optional[str] = None  # short fingerprint string from port 80
    packets: int = 0
    last_seen: float = 0.0
    is_gateway: bool = False

    def label(self) -> str:
        return kind_label(self.kind)


# Evidence-strength weights. A kind only gets assigned if its summed
# evidence reaches MIN_CONFIDENCE. Single weak signals (one open port)
# are no longer enough to pin a label on a device — that was the source
# of Tesira false positives on devices that just happened to listen on
# port 4455.
_EV_MDNS_RESPONSE = 70   # device announced the service via mDNS — strong
_EV_OUI           = 40   # MAC prefix matches a known vendor — strong on its own
_EV_PORT          = 18   # one matching port — weak; need 2+ to classify alone
_EV_HTTP_BANNER   = 70   # web GUI title/server header confirms vendor
_EV_GATEWAY_FLAG  = 100  # we already know this is the gateway

# OUI alone (40) is enough to classify with a tentative '?' badge in the
# UI. A lone matching port (18) is below threshold so we don't tag a
# device just because it listens on tcp/4455. Two ports (36) are also
# below threshold — needs three for port-only classification, or any
# port + OUI / mDNS / banner. OUI + mDNS / OUI + banner = clean (no '?').
MIN_CONFIDENCE    = 40


def infer_kind(dev: Device) -> tuple[Optional[str], int]:
    """Evidence-based classification. Returns (kind, confidence_0_to_100)."""
    scores: dict[str, int] = {}

    def add(kind: Optional[str], strength: int):
        if not kind:
            return
        scores[kind] = scores.get(kind, 0) + strength

    # Gateway flag — definitively a gateway.
    if dev.is_gateway:
        add("gateway", _EV_GATEWAY_FLAG)

    # mDNS service announcements (pre-filtered to RESPONSES by sniffer).
    for service in dev.mdns_services:
        sb = service.encode() if isinstance(service, str) else service
        for needle, kind in MDNS_KIND:
            if needle in sb:
                add(kind, _EV_MDNS_RESPONSE)
                break  # one hit per service is enough

    # Open ports. Each match is weak alone; needs 2+ or a corroborating
    # OUI / mDNS to actually pin the kind.
    for p in dev.ports:
        if p in PORT_KIND:
            add(PORT_KIND[p], _EV_PORT)

    # OUI — vendor association from MAC prefix.
    if dev.mac:
        _, kind = oui_lookup(dev.mac)
        add(kind, _EV_OUI)

    # HTTP banner fingerprint (populated by HTTP probe).
    if dev.http_banner:
        banner = dev.http_banner.lower()
        for sig, kind in _HTTP_BANNER_SIGS:
            if sig in banner:
                add(kind, _EV_HTTP_BANNER)
                break

    if not scores:
        return None, 0
    best = max(scores, key=lambda k: scores[k])
    confidence = min(100, scores[best])
    if confidence < MIN_CONFIDENCE:
        return None, confidence
    return best, confidence


# HTTP banner substring -> kind. Lowercased lookup. Covers headers like
# `Server:` and HTML <title>/meta tags. Add new fingerprints here.
_HTTP_BANNER_SIGS: list[tuple[str, str]] = [
    ("q-sys",      "qsys"),
    ("qsys",       "qsys"),
    ("qsc audio",  "qsys"),
    ("crestron",   "crestron"),
    ("biamp",      "biamp"),
    ("tesira",     "biamp"),
    ("audinate",   "dante"),
    ("extron",     "extron"),
    ("amx",        "amx"),
    ("shure",      "shure"),
    ("clearone",   "clearone"),
    ("polycom",    "videoconf"),
    ("poly ",      "videoconf"),
    ("cisco webex","videoconf"),
    ("vaddio",     "camera"),
    ("axis",       "camera"),
    ("hikvision",  "camera"),
    ("dahua",      "camera"),
    ("samsung",    "display"),
    ("nec ",       "display"),
    ("panasonic",  "display"),
    ("barco",      "display"),
    ("epson",      "display"),
]


# ---------------------------------------------------------------------------
# Hostname resolution — reverse DNS + NetBIOS NBSTAT (UDP 137).
# ---------------------------------------------------------------------------
#
# Reverse DNS hits the system resolver (DNS server + hosts file). For local
# networks that don't have PTR records populated, we fall back to NetBIOS:
# sending a NBSTAT "*" query and parsing the response yields the device's
# Windows computer name even without any DNS infrastructure.

def _reverse_dns(ip: str, timeout: float = 0.5) -> Optional[str]:
    """Reverse-DNS an IP. Returns the short hostname (first label) or None."""
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        host, _aliases, _addrs = socket.gethostbyaddr(ip)
    except (socket.herror, socket.gaierror, OSError):
        return None
    finally:
        socket.setdefaulttimeout(old)
    if not host:
        return None
    return host.split(".", 1)[0]


# NetBIOS-encoded "*" (wildcard) padded to 16 chars — the NBSTAT wildcard
# name. Each nibble is offset by 'A' (0x41).
_NB_WILDCARD = b"CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _nbstat(ip: str, timeout: float = 0.5) -> Optional[str]:
    """Ask `ip:137` for its NetBIOS name table and return the workstation
    name (suffix 0x00). None if the host doesn't answer or isn't running
    NetBIOS-over-TCP/IP (most non-Windows hosts don't)."""
    # Build the NBSTAT query by parts so the mix of literal + "* 3" + variable
    # interpolation doesn't trip Python's implicit-string-concat rule.
    header = (
        b"\x12\x34"              # transaction id — arbitrary
        b"\x00\x00"              # flags: standard query, not broadcast
        b"\x00\x01"              # QDCOUNT = 1
        + (b"\x00\x00" * 3)      # ANCOUNT/NSCOUNT/ARCOUNT = 0
    )
    name_field = b"\x20" + _NB_WILDCARD + b"\x00"  # encoded wildcard name
    trailer = b"\x00\x21" + b"\x00\x01"             # QTYPE=NBSTAT, QCLASS=IN
    query = header + name_field + trailer
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(query, (ip, 137))
        data, _ = s.recvfrom(1024)
        s.close()
    except (OSError, socket.timeout):
        return None

    # Header(12) + AnswerName(34) + Type(2) + Class(2) + TTL(4) + RDLEN(2) = 56
    if len(data) < 57:
        return None
    num_names = data[56]
    off = 57
    best = None
    for _ in range(num_names):
        if off + 18 > len(data):
            break
        name = data[off:off + 15].rstrip(b" \x00").decode("ascii", "ignore")
        suffix = data[off + 15]
        flags = int.from_bytes(data[off + 16:off + 18], "big")
        is_group = bool(flags & 0x8000)
        off += 18
        if not name or name == "__MSBROWSE__":
            continue
        # Suffix 0x00 is the workstation service — that's the one we want.
        if suffix == 0x00 and not is_group:
            return name
        if best is None and not is_group:
            best = name
    return best


def resolve_hostname(ip: str) -> Optional[str]:
    """Best-effort hostname: reverse DNS first, then NetBIOS."""
    host = _reverse_dns(ip)
    if host and host != ip:
        return host
    return _nbstat(ip)


# ---------------------------------------------------------------------------
# HTTP banner grab — fetch the device's web GUI on port 80 and extract a
# short fingerprint from the response (Server header + HTML <title>). This
# is the highest-signal way to confirm AV gear: every Q-SYS Core, Crestron
# DM, Biamp Tesira, Shure ANIUSB, etc. ships a web admin UI whose title or
# Server banner names the product.
# ---------------------------------------------------------------------------

_HTTP_REQ = (
    b"GET / HTTP/1.0\r\n"
    b"Host: %s\r\n"
    b"User-Agent: NICSwitcher-AVScan/1.0\r\n"
    b"Connection: close\r\n"
    b"\r\n"
)
_TITLE_RE = re.compile(rb"<title[^>]*>([^<]{1,200})</title>", re.IGNORECASE)
_SERVER_RE = re.compile(rb"^Server:\s*([^\r\n]{1,200})", re.IGNORECASE | re.MULTILINE)
_META_GEN_RE = re.compile(
    rb'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']{1,200})["\']',
    re.IGNORECASE,
)


def http_banner(ip: str, port: int = 80, timeout: float = 1.5) -> Optional[str]:
    """Fetch http://ip:port/ and return a short fingerprint string composed
    of (Server header) + (HTML title) + (meta generator). Returns None if
    the port is closed, refuses the connection, or doesn't speak HTTP. The
    short timeout keeps a full /24 sweep under ~10s when most ports refuse.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.sendall(_HTTP_REQ % ip.encode())
        chunks: list[bytes] = []
        # Cap at 32 KB — enough for headers + title, avoids slurping
        # massive admin UIs from cooperative servers.
        deadline = time.time() + timeout
        while time.time() < deadline and sum(len(c) for c in chunks) < 32_768:
            try:
                buf = s.recv(4096)
            except socket.timeout:
                break
            if not buf:
                break
            chunks.append(buf)
        s.close()
    except (OSError, socket.timeout):
        return None
    body = b"".join(chunks)
    if not body:
        return None
    parts: list[str] = []
    m = _SERVER_RE.search(body)
    if m:
        parts.append(m.group(1).decode("latin-1", "ignore").strip())
    m = _TITLE_RE.search(body)
    if m:
        title = m.group(1).decode("latin-1", "ignore").strip()
        # Collapse whitespace/HTML entities a little
        title = re.sub(r"\s+", " ", title)
        if title and title.lower() not in {"document", "untitled"}:
            parts.append(title)
    m = _META_GEN_RE.search(body)
    if m:
        parts.append("gen=" + m.group(1).decode("latin-1", "ignore").strip())
    if not parts:
        return None
    out = " · ".join(parts)
    return out[:160]


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
