"""Network scan dialog — live device discovery view with premium styling."""
from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import discover, icons, theme
from .discover import Device, kind_label
from .sniffer import Sniffer
from .theme import KIND_COLORS, STYLE


# ---------------------------------------------------------------------------
# Pill badge for device kind
# ---------------------------------------------------------------------------

def _kind_pill(kind: Optional[str]) -> QLabel:
    text = kind_label(kind).upper()
    color = KIND_COLORS.get(kind or "host", theme.TEXT_MUTED)
    lbl = QLabel(text)
    lbl.setObjectName("pill")
    # Tinted background using kind color — pale translucent
    bg_rgba = _hex_to_rgba(color, alpha=36)
    border_rgba = _hex_to_rgba(color, alpha=120)
    lbl.setStyleSheet(
        f"background: {bg_rgba}; color: {color}; "
        f"border: 1px solid {border_rgba}; border-radius: 10px; "
        f"padding: 2px 9px; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
    )
    return lbl


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> str:
    """#rrggbb → 'rgba(r, g, b, a)'."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# ---------------------------------------------------------------------------
# Device row
# ---------------------------------------------------------------------------

class DeviceRow(QFrame):
    use_subnet = pyqtSignal(str, int)

    def __init__(self, dev: Device, sniffer: Sniffer, parent=None):
        super().__init__(parent)
        self.dev = dev
        self.sniffer = sniffer
        self.setObjectName("deviceCard")

        color = KIND_COLORS.get(dev.kind or "host", theme.TEXT_MUTED)

        # LED
        led = QLabel()
        led.setPixmap(icons.dot(12, color).pixmap(12, 12))
        led.setFixedSize(12, 12)

        # Title line: hostname or IP, bold
        name = dev.hostname or dev.ip
        if dev.is_gateway and "gateway" not in name.lower():
            name += "  · gateway"
        title = QLabel(name)
        title.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {theme.TEXT_PRIMARY};"
        )

        # Mono IP + MAC line
        mono_bits = [dev.ip]
        if dev.mac:
            mono_bits.append(dev.mac.lower())
        mono = QLabel("   ·   ".join(mono_bits))
        mono.setObjectName("mono")

        # Vendor / meta line
        meta_bits = []
        if dev.vendor:
            meta_bits.append(dev.vendor)
        if dev.ports:
            top = sorted(dev.ports, key=lambda p: (p[0], p[1]))[:4]
            meta_bits.append(", ".join(f"{p}/{n}" for p, n in top))
        if dev.mdns_services:
            svcs = ", ".join(sorted(dev.mdns_services))
            if len(svcs) > 46:
                svcs = svcs[:44] + "…"
            meta_bits.append(svcs)
        if dev.packets:
            meta_bits.append(f"{dev.packets} pkt")
        meta_text = "  ·  ".join(meta_bits)
        meta = QLabel(meta_text) if meta_text else None
        if meta is not None:
            meta.setObjectName("subtle")
            meta.setWordWrap(True)

        # kind pill
        pill = _kind_pill(dev.kind)

        # Left column: LED
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 5, 0, 0)
        left_col.setSpacing(0)
        left_col.addWidget(led)
        left_col.addStretch(1)

        # Middle column: title, mono, meta
        mid_col = QVBoxLayout()
        mid_col.setContentsMargins(0, 0, 0, 0)
        mid_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(pill)
        mid_col.addLayout(title_row)
        mid_col.addWidget(mono)
        if meta is not None:
            mid_col.addWidget(meta)

        # Use button
        use_btn = QPushButton("Use")
        use_btn.setObjectName("ghost")
        use_btn.setToolTip("Prefill manual IP form with a free address in this subnet")
        use_btn.setFixedHeight(28)
        use_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        use_btn.clicked.connect(self._emit_subnet)

        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        right_col.addStretch(1)
        right_col.addWidget(use_btn, alignment=Qt.AlignmentFlag.AlignRight)
        right_col.addStretch(1)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 10, 10)
        root.setSpacing(10)
        root.addLayout(left_col)
        root.addLayout(mid_col, 1)
        root.addLayout(right_col)

    def _emit_subnet(self):
        parts = self.dev.ip.split(".")
        subnet = ".".join(parts[:3])
        seen: set[int] = set()
        for ip in self.sniffer.devices:
            p = ip.split(".")
            if len(p) == 4 and ".".join(p[:3]) == subnet:
                try:
                    seen.add(int(p[3]))
                except ValueError:
                    pass
        chosen = None
        for cand in (250, 240, 230, 220, 210, 200, 150):
            if cand not in seen:
                chosen = cand
                break
        if chosen is None:
            for cand in range(2, 255):
                if cand not in seen:
                    chosen = cand
                    break
        self.use_subnet.emit(f"{subnet}.{chosen or 250}", 24)


# ---------------------------------------------------------------------------
# Scan dialog
# ---------------------------------------------------------------------------

class ScanDialog(QDialog):
    apply_subnet = pyqtSignal(str, int)
    probe_status = pyqtSignal(str, str)

    def __init__(self, sniffer: Sniffer, bind_ip: str, parent=None):
        super().__init__(parent)
        self.sniffer = sniffer
        self.bind_ip = bind_ip
        self._closed = False
        self.probe_status.connect(self._on_probe_status)

        self.setWindowTitle("Network Scan — NIC Switcher")
        self.resize(620, 680)
        self.setStyleSheet(STYLE)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        # Header
        title = QLabel("Network Scan")
        title.setObjectName("title")
        subtitle = QLabel(f"Passive sniff + active discovery on {bind_ip or 'this adapter'}")
        subtitle.setObjectName("subtitle")

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.addWidget(title)
        title_col.addWidget(subtitle)

        self.sniff_chip = QLabel("IDLE")
        self.sniff_chip.setObjectName("pill")

        header = QHBoxLayout()
        header.addLayout(title_col)
        header.addStretch(1)
        header.addWidget(self.sniff_chip)

        # Stat strip
        self.stat_pkt = QLabel("0 packets")
        self.stat_pkt.setObjectName("subtle")
        self.stat_dev = QLabel("0 devices")
        self.stat_dev.setObjectName("subtle")
        self.stat_sub = QLabel("no subnets yet")
        self.stat_sub.setObjectName("subtle")

        stat_row = QHBoxLayout()
        stat_row.setSpacing(14)
        stat_row.addWidget(self.stat_pkt)
        stat_row.addWidget(self.stat_dev)
        stat_row.addWidget(self.stat_sub, 1)

        # Device list
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.list_host)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Controls
        self.start_btn = QPushButton("Start passive sniff")
        self.start_btn.setObjectName("accent")
        self.start_btn.setFixedHeight(34)
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self._toggle_sniff)

        self.probe_btn = QPushButton("  Probe (mDNS + ping sweep)")
        self.probe_btn.setIcon(icons.search(15, theme.TEXT_BODY))
        self.probe_btn.setIconSize(QSize(15, 15))
        self.probe_btn.setFixedHeight(34)
        self.probe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.probe_btn.setToolTip(
            "Broadcast mDNS + ping-sweep the local /24 to flush silent devices"
        )
        self.probe_btn.clicked.connect(self._probe)

        self.suggest_btn = QPushButton("Suggest IP")
        self.suggest_btn.setObjectName("ghost")
        self.suggest_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.suggest_btn.setToolTip(
            "Pick a free IP in the busiest subnet and prefill the manual form"
        )
        self.suggest_btn.clicked.connect(self._suggest)

        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)

        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(8)
        btn_row1.addWidget(self.start_btn, 1)
        btn_row1.addWidget(self.probe_btn, 1)

        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(8)
        btn_row2.addWidget(self.suggest_btn)
        btn_row2.addStretch(1)
        btn_row2.addWidget(close_btn)

        self.status = QLabel("")
        self.status.setObjectName("subtle")
        self.status.setWordWrap(True)

        # Root container (so we can add shadow + padding)
        root = QWidget(self)
        root.setObjectName("root")

        body = QVBoxLayout(root)
        body.setContentsMargins(20, 18, 20, 16)
        body.setSpacing(10)
        body.addLayout(header)
        body.addLayout(stat_row)
        body.addSpacing(2)
        body.addWidget(scroll, 1)
        body.addLayout(btn_row1)
        body.addLayout(btn_row2)
        body.addWidget(self.status)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addWidget(root)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 180))
        root.setGraphicsEffect(shadow)

        # refresh pump
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self.sniffer.on_update = self._mark_dirty
        self._dirty = True
        self._update_sniff_chip()
        self._refresh()

    # ---- lifecycle ----
    def closeEvent(self, e):
        self._closed = True
        self.sniffer.on_update = None
        self._timer.stop()
        super().closeEvent(e)

    def _mark_dirty(self):
        self._dirty = True

    # ---- chip helpers ----
    def _update_sniff_chip(self):
        running = self.sniffer.is_running()
        if running:
            self.sniff_chip.setText("LIVE")
            self.sniff_chip.setStyleSheet(
                f"background: rgba(109, 227, 164, 40); color: {theme.SUCCESS}; "
                f"border: 1px solid rgba(109, 227, 164, 120); border-radius: 10px; "
                f"padding: 2px 9px; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
            )
        else:
            self.sniff_chip.setText("IDLE")
            self.sniff_chip.setStyleSheet("")  # inherit #pill default

    def _set_status(self, msg: str, kind: str = "ok"):
        colors = {"ok": theme.SUCCESS, "err": theme.DANGER, "warn": theme.WARNING}
        self.status.setStyleSheet(
            f"color: {colors.get(kind, theme.TEXT_MUTED)}; font-size: 11px;"
        )
        self.status.setText(msg)

    # ---- actions ----
    def _toggle_sniff(self):
        if self.sniffer.is_running():
            ok, msg = self.sniffer.stop()
            self._set_status(msg, "ok" if ok else "err")
        else:
            ok, msg = self.sniffer.start(self.bind_ip)
            if ok:
                threading.Thread(
                    target=discover.mdns_probe, args=(self.bind_ip,), daemon=True
                ).start()
            self._set_status(msg, "ok" if ok else "err")
        self._update_button_states()
        self._update_sniff_chip()

    def _probe(self):
        if not self.bind_ip:
            self._set_status("Selected NIC has no IPv4 — can't probe.", "err")
            return
        self._set_status("Probing — mDNS broadcast + ping sweep running…", "warn")
        threading.Thread(target=self._probe_worker, daemon=True).start()

    def _probe_worker(self):
        try:
            discover.mdns_probe(self.bind_ip)
            parts = self.bind_ip.split(".")
            if len(parts) == 4:
                prefix = ".".join(parts[:3])
                discover.ping_sweep(prefix, timeout_ms=300, workers=96)
            added = self.sniffer.merge_arp()
            self.probe_status.emit(
                f"Probe complete — {added} new ARP entries merged.", "ok"
            )
        except Exception as e:
            self.probe_status.emit(f"Probe error: {e}", "err")

    def _on_probe_status(self, msg: str, kind: str):
        if self._closed:
            return
        self._set_status(msg, kind)

    def _suggest(self):
        sug = self.sniffer.suggest_ip()
        if not sug:
            self._set_status("No traffic yet — start sniff or probe first.", "warn")
            return
        ip, prefix = sug
        self.apply_subnet.emit(ip, prefix)
        self._set_status(f"Suggested {ip}/{prefix} — filled into manual form.", "ok")

    # ---- refresh ----
    def _update_button_states(self):
        running = self.sniffer.is_running()
        self.start_btn.setText("Stop sniff" if running else "Start passive sniff")

    def _refresh(self):
        if self._closed:
            return
        self._update_button_states()
        self._update_sniff_chip()

        st = self.sniffer.stats
        self.stat_pkt.setText(f"{st.packets:,} packets")
        self.stat_dev.setText(f"{len(self.sniffer.devices)} devices")
        top = self.sniffer.top_subnets(3)
        self.stat_sub.setText(
            "  ·  ".join(f"{s}.0/24 ({c})" for s, c in top) if top else "no subnets yet"
        )
        if st.error:
            self._set_status(st.error, "err")

        running = self.sniffer.is_running()
        if not self._dirty and running:
            return
        self._dirty = False

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        devs = self.sniffer.device_list()
        if not devs:
            empty = QLabel(
                "No devices yet. Start the sniff or hit Probe to flush the subnet."
            )
            empty.setObjectName("subtle")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 40, 0, 40)
            self.list_layout.insertWidget(0, empty)
            return

        for dev in devs:
            row = DeviceRow(dev, self.sniffer)
            row.use_subnet.connect(self.apply_subnet)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
