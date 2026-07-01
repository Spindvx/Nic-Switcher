"""Tail and parse the dhcpsrv.log for lease activity.

The dhcpsrv trace format varies slightly across versions, so the parser is
intentionally tolerant: it looks for a timestamp plus an event keyword
(ACK / Offered / NACK / DECLINE / RELEASE) and extracts the IPv4 + MAC
that appear on the same line, regardless of exact field order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Timestamp forms seen in the wild: "[24/04/2026 09:15:32]" or "09:15:32".
_TS_RE = re.compile(r"\[(?P<ts>[^\]]+)\]|(?P<short>\d{2}:\d{2}:\d{2})")
_IP_RE = re.compile(r"\b(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\b")
_MAC_RE = re.compile(
    r"\b(?P<mac>[0-9A-Fa-f]{2}(?:[:\-][0-9A-Fa-f]{2}){5})\b"
)
_HOSTNAME_RE = re.compile(
    r"hostname[^A-Za-z0-9]*[:=]\s*['\"]?(?P<host>[^'\"\s,;]+)", re.IGNORECASE
)


_EVENT_KEYWORDS = {
    "ACK": "ack",
    "NACK": "nack",
    "NAK": "nack",
    "OFFER": "offer",
    "OFFERED": "offer",
    "DECLINE": "decline",
    "RELEASE": "release",
    "DISCOVER": "discover",
    "REQUEST": "request",
    "ASSIGNED": "ack",
    "RENEWED": "ack",
    "LEASED": "ack",
}


@dataclass
class LeaseEvent:
    timestamp: str
    event: str        # ack|offer|nack|decline|release|discover|request
    ip: Optional[str]
    mac: Optional[str]
    hostname: Optional[str]
    raw: str


def parse_line(line: str) -> Optional[LeaseEvent]:
    if not line.strip():
        return None
    upper = line.upper()
    event = None
    for kw, label in _EVENT_KEYWORDS.items():
        if kw in upper:
            event = label
            break
    if event is None:
        return None

    ts_match = _TS_RE.search(line)
    ts = (ts_match.group("ts") or ts_match.group("short")) if ts_match else ""

    ip_match = _IP_RE.search(line)
    ip = ip_match.group("ip") if ip_match else None

    mac_match = _MAC_RE.search(line)
    mac = mac_match.group("mac").upper().replace("-", ":") if mac_match else None

    host_match = _HOSTNAME_RE.search(line)
    host = host_match.group("host") if host_match else None

    return LeaseEvent(
        timestamp=ts.strip(),
        event=event,
        ip=ip,
        mac=mac,
        hostname=host,
        raw=line.rstrip(),
    )


def tail_events(log_path: Path, max_events: int = 25,
                max_bytes: int = 256 * 1024) -> list[LeaseEvent]:
    """Return the most-recent lease events from the log, newest-first.

    Reads only the last `max_bytes` of the file so a huge log doesn't stall
    the UI thread.
    """
    if not log_path.is_file():
        return []
    try:
        size = log_path.stat().st_size
        with log_path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # skip the partial first line
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="ignore")
    out: list[LeaseEvent] = []
    for line in reversed(text.splitlines()):
        ev = parse_line(line)
        if ev is not None:
            out.append(ev)
            if len(out) >= max_events:
                break
    return out


@dataclass
class LeaseSnapshot:
    active: dict[str, "ActiveLease"]   # keyed by MAC
    recent: list[LeaseEvent]            # newest-first, any event


@dataclass
class ActiveLease:
    ip: str
    mac: str
    hostname: Optional[str]
    last_event: str
    timestamp: str


def summarize(events: list[LeaseEvent]) -> LeaseSnapshot:
    """Build a by-MAC view of active (ack'd or offered) leases from recent events.

    Newest event per MAC wins. Records a lease as active if the most recent
    event for that MAC is ACK/OFFER/REQUEST; DECLINE/RELEASE clears it.
    """
    active: dict[str, ActiveLease] = {}
    # Events are newest-first — iterate oldest-first so later events overwrite.
    for ev in reversed(events):
        if not ev.mac:
            continue
        if ev.event in ("decline", "release", "nack"):
            active.pop(ev.mac, None)
            continue
        if ev.event in ("ack", "offer", "request", "discover"):
            if ev.ip is None and ev.mac in active:
                # keep the existing IP if this event didn't carry one
                existing = active[ev.mac]
                active[ev.mac] = ActiveLease(
                    ip=existing.ip, mac=ev.mac,
                    hostname=ev.hostname or existing.hostname,
                    last_event=ev.event,
                    timestamp=ev.timestamp or existing.timestamp,
                )
                continue
            if ev.ip:
                active[ev.mac] = ActiveLease(
                    ip=ev.ip, mac=ev.mac,
                    hostname=ev.hostname,
                    last_event=ev.event,
                    timestamp=ev.timestamp,
                )
    return LeaseSnapshot(active=active, recent=events)
