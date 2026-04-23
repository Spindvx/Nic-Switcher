"""Dante / Audinate discovery via mDNS browsing.

Audinate's Dante protocol uses mDNS (Bonjour) for device discovery on these
service types:

    _netaudio-arc._udp.local   — Dante Arc (primary device service)
    _netaudio-chan._udp.local  — channel enumeration
    _netaudio-cmc._udp.local   — CMC control-and-monitoring
    _netaudio-dbc._udp.local   — Dante Domain Broker

Zeroconf does proper mDNS browsing (not just raw-socket parsing), so we get
friendly device names, IP addresses, ports, and TXT records. Those feed into
the Sniffer's device table so Dante nodes show up in the scan UI with rich
metadata even if the passive sniff hasn't seen their traffic yet.

Runs lazily: only starts when the scan dialog opens, stops cleanly when it
closes. No persistent background work when the user isn't looking.
"""
from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

# zeroconf is a runtime dependency now (requirements.txt). We import lazily
# inside methods so the module stays importable on environments where the
# package hasn't been installed yet — the scan UI shows a graceful message
# in that case instead of crashing the whole app.
_ZC_IMPORT_ERROR: Optional[str] = None
try:
    from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange
    from zeroconf import IPVersion
except ImportError as e:  # pragma: no cover — install path
    _ZC_IMPORT_ERROR = str(e)
    ServiceBrowser = Zeroconf = ServiceStateChange = None  # type: ignore


DANTE_SERVICES = [
    "_netaudio-arc._udp.local.",
    "_netaudio-chan._udp.local.",
    "_netaudio-cmc._udp.local.",
    "_netaudio-dbc._udp.local.",
]


@dataclass
class DanteDevice:
    """A Dante device seen via mDNS. One physical device can show up on
    multiple services (ARC + CMC + ...); we dedupe by IP."""
    ip: str
    name: str = ""
    services: set[str] = field(default_factory=set)
    ports: set[int] = field(default_factory=set)
    model: Optional[str] = None
    txt: dict[str, str] = field(default_factory=dict)


OnUpdate = Callable[[dict[str, DanteDevice]], None]


class DanteBrowser:
    """Background mDNS browser for Dante services. Thread-safe."""

    def __init__(self, on_update: Optional[OnUpdate] = None):
        self._zc: Optional["Zeroconf"] = None
        self._browsers: list["ServiceBrowser"] = []
        self._lock = threading.Lock()
        self._devices: dict[str, DanteDevice] = {}  # keyed by IP
        self._on_update = on_update
        self._running = False

    def available(self) -> tuple[bool, str]:
        """Is zeroconf installed? Returns (ok, err)."""
        if _ZC_IMPORT_ERROR:
            return False, (
                f"zeroconf not installed ({_ZC_IMPORT_ERROR}). "
                "Run: pip install zeroconf"
            )
        return True, ""

    def devices(self) -> dict[str, DanteDevice]:
        with self._lock:
            # Shallow copy — caller iterates safely without racing mutations.
            return {ip: DanteDevice(
                ip=d.ip, name=d.name, services=set(d.services),
                ports=set(d.ports), model=d.model, txt=dict(d.txt),
            ) for ip, d in self._devices.items()}

    def start(self) -> tuple[bool, str]:
        if self._running:
            return True, "Already browsing"
        ok, err = self.available()
        if not ok:
            return False, err
        try:
            self._zc = Zeroconf(ip_version=IPVersion.V4Only)
        except OSError as e:
            return False, f"Zeroconf init failed: {e}"
        try:
            for svc in DANTE_SERVICES:
                self._browsers.append(
                    ServiceBrowser(self._zc, svc, handlers=[self._on_state])
                )
        except Exception as e:
            self.stop()
            return False, f"ServiceBrowser failed: {e}"
        self._running = True
        return True, f"Browsing {len(DANTE_SERVICES)} Dante service types"

    def stop(self) -> None:
        self._running = False
        for b in self._browsers:
            try:
                b.cancel()
            except Exception:
                pass
        self._browsers.clear()
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None

    # --- zeroconf handler ---
    def _on_state(self, zeroconf, service_type: str, name: str,
                  state_change: "ServiceStateChange") -> None:
        try:
            info = zeroconf.get_service_info(service_type, name, timeout=2000)
        except Exception:
            info = None
        if info is None:
            return
        ips = info.parsed_addresses() or []
        ipv4s = [ip for ip in ips if ":" not in ip]
        if not ipv4s:
            return
        ip = ipv4s[0]

        # Strip "._service._udp.local." suffix off the name for display.
        friendly = name
        if "._" in friendly:
            friendly = friendly.split("._", 1)[0]

        txt: dict[str, str] = {}
        for k, v in (info.properties or {}).items():
            try:
                key = k.decode("utf-8", "ignore") if isinstance(k, bytes) else str(k)
                val = v.decode("utf-8", "ignore") if isinstance(v, bytes) else (
                    "" if v is None else str(v)
                )
                if key:
                    txt[key] = val
            except Exception:
                continue

        with self._lock:
            dev = self._devices.get(ip)
            if dev is None:
                dev = DanteDevice(ip=ip, name=friendly)
                self._devices[ip] = dev
            else:
                if not dev.name and friendly:
                    dev.name = friendly
            dev.services.add(service_type.rstrip(".").split("._", 1)[0].lstrip("_"))
            if info.port:
                dev.ports.add(info.port)
            if txt:
                dev.txt.update(txt)
                # Common TXT keys observed: 'model', 'mf', 'ver'.
                for key in ("model", "mf", "manufacturer"):
                    if key in txt and not dev.model:
                        dev.model = txt[key]
                        break

        cb = self._on_update
        if cb is not None:
            try:
                cb(self.devices())
            except Exception:
                pass
