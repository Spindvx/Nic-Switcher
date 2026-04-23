"""Passive IP sniffer with device fingerprinting.

Windows raw socket in promiscuous mode (SIO_RCVALL). No Npcap needed.
Parses IPv4 + TCP/UDP headers, captures UDP/5353 payloads for mDNS
service string matching, and aggregates per-IP stats.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from .discover import (
    Device, MDNS_KIND, default_gateway_for, infer_kind, is_av, oui_lookup,
    read_arp_cache, resolve_hostname,
)


PROTO_NAMES = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 89: "OSPF"}


@dataclass
class SniffStats:
    packets: int = 0
    bytes_seen: int = 0
    started: float = 0.0
    sources: Counter = field(default_factory=Counter)
    subnets: Counter = field(default_factory=Counter)
    protos: Counter = field(default_factory=Counter)
    error: Optional[str] = None


def _is_skip(ip: str) -> bool:
    return (
        ip.startswith("0.")
        or ip.startswith("127.")
        or ip.startswith("224.")
        or ip.startswith("239.")
        or ip == "255.255.255.255"
    )


def _is_private(ip: str) -> bool:
    try:
        a, b, *_ = (int(x) for x in ip.split("."))
    except ValueError:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or a == 169


class Sniffer:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.stats = SniffStats()
        self.devices: dict[str, Device] = {}
        self.bind_ip: str = ""
        self.gateway_ip: Optional[str] = None
        self.on_update: Optional[Callable[[], None]] = None
        self._last_emit = 0.0

    # ---- lifecycle ----
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, bind_ip: str) -> tuple[bool, str]:
        if self.is_running():
            return False, "Already sniffing"
        if not bind_ip:
            return False, "Selected NIC has no IPv4 address to bind to"
        self.bind_ip = bind_ip
        self.stats = SniffStats(started=time.time())
        self.devices = {}
        self.gateway_ip = default_gateway_for(bind_ip)
        if self.gateway_ip:
            self._touch_device(self.gateway_ip).is_gateway = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(bind_ip,), daemon=True)
        self._thread.start()
        return True, f"Listening on {bind_ip}"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running() and self._sock is None:
            return True, "Not running"
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        return True, "Sniffer stopped"

    # ---- public snapshots ----
    def device_list(self) -> list[Device]:
        """Thread-safe snapshot: shallow-copy the per-device mutable sets under
        the lock so the UI side can iterate without racing _ingest()."""
        with self._lock:
            snaps: list[Device] = []
            for d in self.devices.values():
                snaps.append(Device(
                    ip=d.ip,
                    mac=d.mac,
                    vendor=d.vendor,
                    kind=d.kind,
                    hostname=d.hostname,
                    ports=set(d.ports),
                    mdns_services=set(d.mdns_services),
                    packets=d.packets,
                    last_seen=d.last_seen,
                    is_gateway=d.is_gateway,
                ))
        for d in snaps:
            if not d.kind:
                d.kind = infer_kind(d)
        # Sort order: gateway first, then AV devices, then everything else by
        # traffic/IP. Among AV devices, Q-SYS > Crestron > Biamp > Dante >
        # others reflects typical AV deployment priority.
        AV_PRIORITY = ["qsys", "crestron", "biamp", "dante", "extron", "amx",
                       "shure", "videoconf", "display", "camera", "livewire",
                       "yamaha", "clearone", "lutron", "solstice"]
        av_rank = {k: i for i, k in enumerate(AV_PRIORITY)}
        snaps.sort(key=lambda d: (
            not d.is_gateway,
            not is_av(d.kind),
            av_rank.get(d.kind or "", 99),
            -d.packets,
            d.ip,
        ))
        return snaps

    def merge_dante(self, dante_devs: dict) -> int:
        """Fold Dante devices discovered via zeroconf into the device table.
        Returns the count of devices added or enriched.
        """
        touched = 0
        with self._lock:
            for ip, d in dante_devs.items():
                if _is_skip(ip):
                    continue
                dev = self.devices.get(ip)
                if dev is None:
                    dev = Device(ip=ip)
                    self.devices[ip] = dev
                dev.kind = "dante"
                if d.name and not dev.hostname:
                    dev.hostname = d.name
                # Represent each Dante mDNS service as an entry in mdns_services.
                for svc in d.services:
                    dev.mdns_services.add(f"_{svc}")
                for port in d.ports:
                    dev.ports.add(("udp", port))
                if d.model and not dev.vendor:
                    dev.vendor = f"Audinate Dante · {d.model}"
                elif not dev.vendor:
                    dev.vendor = "Audinate Dante"
                touched += 1
        if touched:
            self._emit()
        return touched

    def merge_arp(self) -> int:
        """Pull the Windows ARP cache and fill in MAC/vendor for known IPs. Returns rows added."""
        added = 0
        try:
            rows = read_arp_cache()
        except Exception:
            return 0
        with self._lock:
            for ip, mac in rows:
                if _is_skip(ip):
                    continue
                dev = self.devices.get(ip)
                if dev is None:
                    dev = Device(ip=ip)
                    self.devices[ip] = dev
                    added += 1
                dev.mac = mac
                vendor, _kind = oui_lookup(mac)
                if vendor:
                    dev.vendor = vendor
                if self.gateway_ip and ip == self.gateway_ip:
                    dev.is_gateway = True
                dev.kind = infer_kind(dev)
        # Fire hostname resolution in the background so the UI gets names
        # without blocking the merge call. Safe to run concurrently with
        # further sniff activity — we only mutate `hostname` on existing
        # devices under the lock.
        threading.Thread(target=self._resolve_hostnames_bg, daemon=True).start()
        self._emit()
        return added

    def _resolve_hostnames_bg(self):
        """Resolve hostnames for every device that doesn't already have one.
        Uses a thread pool so a slow NetBIOS timeout doesn't block the rest."""
        import concurrent.futures
        with self._lock:
            targets = [ip for ip, d in self.devices.items() if not d.hostname]
        if not targets:
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(resolve_hostname, ip): ip for ip in targets}
            resolved = 0
            for fut in concurrent.futures.as_completed(futs):
                ip = futs[fut]
                try:
                    host = fut.result()
                except Exception:
                    host = None
                if not host:
                    continue
                with self._lock:
                    dev = self.devices.get(ip)
                    if dev is not None and not dev.hostname:
                        dev.hostname = host
                        resolved += 1
        if resolved:
            self._emit()

    def top_subnets(self, n: int = 3) -> list[tuple[str, int]]:
        return self.stats.subnets.most_common(n)

    def suggest_ip(self) -> Optional[tuple[str, int]]:
        if not self.stats.subnets:
            return None
        ranked = self.stats.subnets.most_common()
        ranked.sort(key=lambda x: (not _is_private(x[0] + ".0"), -x[1]))
        sub, _ = ranked[0]
        seen: set[int] = set()
        for ip in list(self.devices.keys()) + list(self.stats.sources.keys()):
            parts = ip.split(".")
            if ".".join(parts[:3]) == sub:
                try:
                    seen.add(int(parts[3]))
                except ValueError:
                    pass
        for cand in (250, 240, 230, 220, 210, 200, 150):
            if cand not in seen:
                return f"{sub}.{cand}", 24
        for cand in range(2, 255):
            if cand not in seen:
                return f"{sub}.{cand}", 24
        return f"{sub}.200", 24

    # ---- thread body ----
    def _run(self, bind_ip: str):
        # Assign self._sock BEFORE SIO_RCVALL so stop() can always close it,
        # even if the ioctl hangs or the thread dies mid-setup.
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
            s.bind((bind_ip, 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
            s.settimeout(0.5)
            self._sock = s
            s.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
        except PermissionError:
            self.stats.error = "Requires administrator privileges."
            try:
                if self._sock is not None:
                    self._sock.close()
            finally:
                self._sock = None
            return
        except OSError as e:
            self.stats.error = f"Cannot open raw socket: {e}"
            try:
                if self._sock is not None:
                    self._sock.close()
            finally:
                self._sock = None
            return

        while not self._stop.is_set():
            try:
                data, _ = s.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self._ingest(data)
            now = time.time()
            if now - self._last_emit > 0.25:
                self._last_emit = now
                self._emit()

    # ---- parsing ----
    def _touch_device(self, ip: str) -> Device:
        dev = self.devices.get(ip)
        if dev is None:
            dev = Device(ip=ip)
            self.devices[ip] = dev
        dev.last_seen = time.time()
        dev.packets += 1
        return dev

    def _ingest(self, data: bytes):
        if len(data) < 20:
            return
        vihl = data[0]
        if (vihl >> 4) != 4:
            return
        ihl = (vihl & 0x0F) * 4
        if ihl < 20 or len(data) < ihl:
            return
        proto = data[9]
        src = socket.inet_ntoa(data[12:16])
        dst = socket.inet_ntoa(data[16:20])
        st = self.stats
        with self._lock:
            st.packets += 1
            st.bytes_seen += len(data)
            st.protos[PROTO_NAMES.get(proto, f"proto{proto}")] += 1

            for ip in (src, dst):
                if _is_skip(ip):
                    continue
                st.sources[ip] += 1
                sub = ".".join(ip.split(".")[:3])
                st.subnets[sub] += 1
                dev = self._touch_device(ip)
                if self.gateway_ip and ip == self.gateway_ip:
                    dev.is_gateway = True

            # L4: TCP/UDP
            if proto in (6, 17) and len(data) >= ihl + 4:
                sport, dport = struct.unpack(">HH", data[ihl:ihl + 4])
                proto_name = "tcp" if proto == 6 else "udp"
                payload_off = ihl + (8 if proto == 17 else 20)
                for ip, port, role in ((src, sport, "src"), (dst, dport, "dst")):
                    if _is_skip(ip):
                        continue
                    dev = self.devices.get(ip)
                    if dev is not None and port < 49152:  # ignore ephemeral
                        dev.ports.add((proto_name, port))

                # mDNS sniff (UDP 5353)
                if proto == 17 and (sport == 5353 or dport == 5353) and len(data) > payload_off:
                    payload = data[payload_off:]
                    for needle, _kind in MDNS_KIND:
                        if needle in payload:
                            dev = self.devices.get(src) if not _is_skip(src) else None
                            if dev is not None:
                                dev.mdns_services.add(needle.decode("ascii", "ignore"))
                    # try to extract a hostname from the first PTR answer
                    host = _extract_mdns_hostname(payload)
                    if host:
                        dev = self.devices.get(src) if not _is_skip(src) else None
                        if dev is not None and not dev.hostname:
                            dev.hostname = host

    def _emit(self):
        cb = self.on_update
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass


def _extract_mdns_hostname(payload: bytes) -> Optional[str]:
    """Very tolerant scan for a ".local" label sequence — enough to grab device names."""
    try:
        i = 12  # skip DNS header
        # scan labels until we hit .local
        parts: list[str] = []
        depth = 0
        while i < len(payload) and depth < 16:
            ln = payload[i]
            if ln == 0:
                break
            if ln & 0xC0:  # pointer — give up simple mode
                break
            if ln > 63 or i + 1 + ln > len(payload):
                break
            label = payload[i + 1:i + 1 + ln].decode("ascii", "ignore")
            parts.append(label)
            i += 1 + ln
            depth += 1
        if parts and parts[-1] == "local" and len(parts) >= 2:
            name = parts[0]
            if name and not name.startswith("_"):
                return name
    except Exception:
        return None
    return None
