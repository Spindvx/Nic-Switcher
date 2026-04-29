"""The main tray popup — frameless, acrylic, premium. Anchors bottom-right."""
from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, QSize, QTimer, Qt, pyqtSignal,
)
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from . import dhcp as dhcp_mod
from . import icons as icons
from . import mac as mac_mod
from . import nic as nic_mod
from . import theme
from .blur import enable_blur, try_enable_mica
from .config import AppConfig, Preset
from .dialogs import DhcpDialog, PresetDialog
from .scan_dialog import ScanDialog
from .sniffer import Sniffer
from .theme import STYLE
from .validate import is_valid_mask, mask_to_prefix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.BORDER_SUBTLE}; border: none;")
    return line


def _icon_button(icon_fn, tooltip: str, color: str = theme.TEXT_SECOND,
                 size: int = 30, icon_size: int = 16) -> QPushButton:
    btn = QPushButton()
    btn.setObjectName("icon")
    btn.setFixedSize(size, size)
    btn.setIcon(icon_fn(icon_size, color))
    btn.setIconSize(QSize(icon_size, icon_size))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def _led(color: str, size: int = 10) -> QLabel:
    """Returns a label rendering a filled dot of the given color with a soft halo."""
    lbl = QLabel()
    lbl.setPixmap(icons.dot(size, color).pixmap(size, size))
    lbl.setFixedSize(size, size)
    return lbl


# ---------------------------------------------------------------------------
# Preset card
# ---------------------------------------------------------------------------

class PresetCard(QFrame):
    apply_clicked = pyqtSignal(object)
    edit_clicked = pyqtSignal(object)
    delete_clicked = pyqtSignal(object)

    def __init__(self, preset: Preset, current_ip: str | None, parent=None):
        super().__init__(parent)
        self.preset = preset

        is_active = bool(preset.ip and current_ip and preset.ip == current_ip)
        self.setObjectName("presetCardActive" if is_active else "presetCard")

        name = QLabel(preset.name)
        name.setStyleSheet(
            f"font-weight: 600; font-size: 13px; color: {theme.TEXT_PRIMARY};"
        )

        if preset.ip:
            ip_text = f"{preset.ip} / {preset.prefix}"
        else:
            ip_text = "DHCP (automatic)"
        ip_label = QLabel(ip_text)
        ip_label.setObjectName("mono")

        sub_bits = []
        if preset.gateway:
            sub_bits.append(f"gw {preset.gateway}")
        if preset.dns1:
            sub_bits.append(preset.dns1 + (f", {preset.dns2}" if preset.dns2 else ""))
        if preset.mac:
            if preset.mac.strip().lower() == "restore":
                sub_bits.append("MAC: restore")
            else:
                norm = mac_mod.normalize_mac(preset.mac)
                sub_bits.append(
                    f"MAC {mac_mod.format_mac_pretty(norm)}" if norm
                    else f"MAC {preset.mac}"
                )
        sub: QLabel | None = None
        if sub_bits:
            sub = QLabel("  •  ".join(sub_bits))
            sub.setObjectName("subtle")

        text = QVBoxLayout()
        text.setSpacing(1)
        text.setContentsMargins(0, 0, 0, 0)
        text.addWidget(name)
        text.addWidget(ip_label)
        if sub is not None:
            text.addWidget(sub)

        # Status LED (left side) — green halo when this preset is currently
        # live, dim grey otherwise. Green is the universal "running / on /
        # healthy" signal here, kept independent of the brand accent.
        led = _led(theme.SUCCESS if is_active else theme.TEXT_DIM, 10)

        apply_btn = QPushButton("Active" if is_active else "Apply")
        apply_btn.setObjectName("accent")
        apply_btn.setEnabled(not is_active)
        # Height bumped to 34 — the new iOS-glass theme padding (8px 16px)
        # pushed the natural text bounds past 30px and the descender of 'y'
        # was getting clipped on the preset card row.
        apply_btn.setFixedHeight(34)
        apply_btn.setMinimumWidth(78)
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(lambda: self.apply_clicked.emit(preset))

        edit_btn = _icon_button(icons.edit, "Edit preset")
        edit_btn.clicked.connect(lambda: self.edit_clicked.emit(preset))

        del_btn = _icon_button(icons.trash, "Delete preset")
        del_btn.clicked.connect(lambda: self.delete_clicked.emit(preset))

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 10, 10)
        row.setSpacing(10)
        row.addWidget(led)
        row.addLayout(text, 1)
        row.addWidget(apply_btn)
        row.addWidget(edit_btn)
        row.addWidget(del_btn)


# ---------------------------------------------------------------------------
# Main popup
# ---------------------------------------------------------------------------

class Popup(QWidget):
    WIDTH = 460
    HEIGHT = 900
    SCREEN_MARGIN = 14

    apply_done = pyqtSignal(bool, str)
    dhcp_done = pyqtSignal(bool, str)
    mac_done = pyqtSignal(bool, str)

    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.sniffer = Sniffer()
        self._apply_busy = False
        self._dhcp_busy = False
        self._mac_busy = False
        self._last_known_dhcp_running: Optional[bool] = None
        self._pinned = False
        self.apply_done.connect(self._on_apply_done)
        self.dhcp_done.connect(self._on_dhcp_done)
        self.mac_done.connect(self._on_mac_done)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setStyleSheet(STYLE)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self._build_ui()
        self.refresh_all()

        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Background poll for DHCP lease activity (only ticks while visible).
        self._lease_timer = QTimer(self)
        self._lease_timer.setInterval(2000)
        self._lease_timer.timeout.connect(self._lease_tick)
        self._lease_timer.start()

    # ---- pin ----
    def _toggle_pin(self, checked: bool):
        self._pinned = checked
        # Filled pin icon + lighter pastel-red SELECT_GLOW when active so
        # the on-state pops without competing with regular accent buttons.
        color = theme.SELECT_GLOW if checked else theme.TEXT_SECOND
        self.pin_btn.setIcon(icons.pin(14, color, filled=checked))
        # Window flag tweak so OS focus changes can't drag the popup back to
        # idle hide-on-blur — Tray._maybe_hide also respects self._pinned.
        if checked:
            self._set_status("Pinned — won't close on focus loss", "ok")
        else:
            self._set_status("Unpinned", "ok")

    def is_pinned(self) -> bool:
        return self._pinned

    # ---- chrome ----
    def showEvent(self, e):
        super().showEvent(e)
        hwnd = int(self.winId())
        if not try_enable_mica(hwnd):
            enable_blur(hwnd)

    def paintEvent(self, e):
        # Tries to be see-through when Windows supports it. Two prerequisites
        # outside our control:
        #   1. Settings -> Personalization -> Colors -> "Show transparency
        #      effects" must be ON.
        #   2. The OS must accept the DwmSetWindowAttribute call (Mica
        #      requires Win 11 22H2+; older Windows falls back to acrylic
        #      blur via SetWindowCompositionAttribute).
        # If either fails, the alpha-200 fill below is dark enough to look
        # clean as a near-solid surface anyway.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1).toRectF(), 16, 16)
        p.fillPath(path, QColor(10, 12, 16, 200))
        p.end()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide_animated()
            return
        # Ctrl+R refreshes all data (NICs, presets, DHCP state).
        if (e.modifiers() & Qt.KeyboardModifier.ControlModifier
                and e.key() == Qt.Key.Key_R):
            self.refresh_all()
            self._set_status("Refreshed", "ok")
            return
        # Ctrl+1..9 applies the Nth preset (1-indexed). Skip if a text input
        # has focus so users typing IPs / MACs don't trip the shortcut.
        if (e.modifiers() & Qt.KeyboardModifier.ControlModifier
                and Qt.Key.Key_1 <= e.key() <= Qt.Key.Key_9):
            focused = self.focusWidget()
            if isinstance(focused, QLineEdit):
                super().keyPressEvent(e)
                return
            idx = e.key() - Qt.Key.Key_1   # 0-indexed
            if 0 <= idx < len(self.config.presets):
                self._apply_preset(self.config.presets[idx])
            return
        super().keyPressEvent(e)

    # ---- layout ----
    def _build_ui(self):
        root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        # ----- Header -----
        header = QHBoxLayout()
        header.setSpacing(10)

        brand = QLabel()
        brand.setPixmap(icons.brand_tray_icon(28).pixmap(28, 28))
        brand.setFixedSize(28, 28)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("NIC Switcher")
        title.setObjectName("title")
        subtitle = QLabel("Network presets, DHCP, scan")
        subtitle.setObjectName("subtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)

        self.pin_btn = _icon_button(icons.pin, "Pin — keep popup open when clicking away",
                                     theme.TEXT_SECOND, 30, 14)
        self.pin_btn.setCheckable(True)
        self.pin_btn.toggled.connect(self._toggle_pin)

        close_btn = _icon_button(icons.close, "Close (Esc)", theme.TEXT_SECOND, 30, 14)
        close_btn.clicked.connect(self.hide_animated)

        header.addWidget(brand)
        header.addLayout(title_col)
        header.addStretch(1)
        header.addWidget(self.pin_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # Status sits on its own row beneath the header — long status text
        # (e.g. "Setting MAC to AA:BB:..." or "Reapplying CNZ POC Lab") used
        # to overrun the title area when crammed into the header.
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusOk")
        self.status_label.setWordWrap(False)
        self.status_label.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.status_label)

        layout.addWidget(_divider())

        # ----- NIC selector -----
        nic_head = QHBoxLayout()
        nic_label = QLabel("INTERFACE")
        nic_label.setObjectName("section")
        nic_head.addWidget(nic_label)
        nic_head.addStretch(1)
        layout.addLayout(nic_head)

        nic_row = QHBoxLayout()
        nic_row.setSpacing(6)
        self.nic_combo = QComboBox()
        self.nic_combo.setMinimumHeight(34)
        self.nic_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.nic_combo.currentIndexChanged.connect(self._on_nic_changed)
        refresh_btn = _icon_button(icons.refresh, "Refresh adapters", theme.TEXT_BODY, 34, 16)
        refresh_btn.clicked.connect(self.refresh_all)
        nic_row.addWidget(self.nic_combo, 1)
        nic_row.addWidget(refresh_btn)
        layout.addLayout(nic_row)

        # NIC status line (LED + details)
        nic_status_row = QHBoxLayout()
        nic_status_row.setContentsMargins(2, 0, 0, 0)
        nic_status_row.setSpacing(8)
        self.nic_led = _led(theme.TEXT_DIM, 8)
        self.nic_status = QLabel("—")
        self.nic_status.setObjectName("subtle")
        nic_status_row.addWidget(self.nic_led)
        nic_status_row.addWidget(self.nic_status, 1)
        layout.addLayout(nic_status_row)

        # MAC edit row — manual entry + Apply + quick Random / Restore.
        mac_row = QHBoxLayout()
        mac_row.setContentsMargins(2, 2, 0, 0)
        mac_row.setSpacing(6)
        self.mac_input = QLineEdit()
        self.mac_input.setPlaceholderText("AA:BB:CC:DD:EE:FF")
        self.mac_input.setFixedHeight(32)
        self.mac_input.returnPressed.connect(self._apply_typed_mac)
        self.mac_apply_btn = QPushButton("Apply")
        self.mac_apply_btn.setObjectName("accent")
        self.mac_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mac_apply_btn.setFixedHeight(32)
        self.mac_apply_btn.setToolTip("Apply the typed MAC and restart the adapter (~5s)")
        self.mac_apply_btn.clicked.connect(self._apply_typed_mac)
        self.mac_random_btn = QPushButton("Random")
        self.mac_random_btn.setObjectName("ghost")
        self.mac_random_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mac_random_btn.setFixedHeight(32)
        self.mac_random_btn.setToolTip(
            "Set a random locally-administered MAC (writes immediately)"
        )
        self.mac_random_btn.clicked.connect(self._randomize_mac)
        self.mac_restore_btn = QPushButton("Restore")
        self.mac_restore_btn.setObjectName("ghost")
        self.mac_restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mac_restore_btn.setFixedHeight(32)
        self.mac_restore_btn.setToolTip(
            "Clear the MAC override and bring back the hardware MAC"
        )
        self.mac_restore_btn.clicked.connect(self._restore_mac)
        mac_row.addWidget(self.mac_input, 1)
        mac_row.addWidget(self.mac_apply_btn)
        mac_row.addWidget(self.mac_random_btn)
        mac_row.addWidget(self.mac_restore_btn)
        layout.addLayout(mac_row)

        # MAC status line (overridden indicator).
        self.mac_status = QLabel("")
        self.mac_status.setObjectName("subtle")
        self.mac_status.setContentsMargins(2, 0, 0, 0)
        layout.addWidget(self.mac_status)

        # ----- Presets -----
        pres_header = QHBoxLayout()
        pres_label = QLabel("PRESETS")
        pres_label.setObjectName("section")
        add_btn = QPushButton("  New preset")
        add_btn.setObjectName("ghost")
        add_btn.setIcon(icons.plus(14, theme.TEXT_SECOND))
        add_btn.setIconSize(QSize(14, 14))
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.clicked.connect(self._new_preset)
        pres_header.addWidget(pres_label)
        pres_header.addStretch(1)
        pres_header.addWidget(add_btn)
        layout.addLayout(pres_header)

        self.preset_scroll = QScrollArea()
        self.preset_scroll.setWidgetResizable(True)
        self.preset_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preset_scroll.setMinimumHeight(168)
        self.preset_scroll.setMaximumHeight(228)
        self.preset_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preset_host = QWidget()
        self.preset_layout = QVBoxLayout(self.preset_host)
        self.preset_layout.setContentsMargins(0, 0, 4, 0)
        self.preset_layout.setSpacing(6)
        self.preset_layout.addStretch(1)
        self.preset_scroll.setWidget(self.preset_host)
        layout.addWidget(self.preset_scroll)

        # ----- Manual -----
        layout.addWidget(_divider())
        m_label = QLabel("MANUAL")
        m_label.setObjectName("section")
        layout.addWidget(m_label)

        self.m_ip = QLineEdit()
        self.m_ip.setPlaceholderText("IP address — e.g. 10.17.75.240")
        self.m_mask = QLineEdit("255.255.255.0")
        self.m_mask.setPlaceholderText("255.255.255.0")
        self.m_gw = QLineEdit()
        self.m_gw.setPlaceholderText("Gateway (optional)")

        # Row 1 — IP on its own so it breathes
        layout.addWidget(self.m_ip)

        # Row 2 — mask + gateway split
        m_row2 = QHBoxLayout()
        m_row2.setSpacing(6)
        m_row2.addWidget(self.m_mask, 1)
        m_row2.addWidget(self.m_gw, 1)
        layout.addLayout(m_row2)

        # Row 3 — actions right-aligned
        m_row3 = QHBoxLayout()
        m_row3.setSpacing(6)
        m_row3.addStretch(1)
        m_save = QPushButton("Save as preset")
        m_save.setObjectName("ghost")
        m_save.setCursor(Qt.CursorShape.PointingHandCursor)
        m_save.clicked.connect(self._save_manual_as_preset)
        m_apply = QPushButton("Apply")
        m_apply.setObjectName("accent")
        m_apply.setFixedHeight(32)
        m_apply.setMinimumWidth(96)
        m_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        m_apply.clicked.connect(self._apply_manual)
        m_row3.addWidget(m_save)
        m_row3.addWidget(m_apply)
        layout.addLayout(m_row3)

        # ----- Scan button -----
        scan_btn = QPushButton("  Scan network")
        scan_btn.setIcon(icons.search(16, theme.TEXT_BODY))
        scan_btn.setIconSize(QSize(16, 16))
        scan_btn.setFixedHeight(34)
        scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        scan_btn.setToolTip("Discover Q-SYS, Crestron, Biamp, switches — suggest a free IP")
        scan_btn.clicked.connect(self._open_scan)
        layout.addSpacing(2)
        layout.addWidget(scan_btn)

        # ----- DHCP -----
        layout.addWidget(_divider())
        dhcp_header = QHBoxLayout()
        dhcp_label = QLabel("DHCP SERVER")
        dhcp_label.setObjectName("section")
        dhcp_header.addWidget(dhcp_label)
        dhcp_header.addStretch(1)
        self.dhcp_led = _led(theme.TEXT_DIM, 8)
        self.dhcp_chip = QLabel("IDLE")
        self.dhcp_chip.setObjectName("pill")
        dhcp_header.addWidget(self.dhcp_led)
        dhcp_header.addWidget(self.dhcp_chip)
        layout.addLayout(dhcp_header)

        self.dhcp_status = QLabel("Not configured")
        self.dhcp_status.setObjectName("subtle")
        layout.addWidget(self.dhcp_status)

        dhcp_row = QHBoxLayout()
        dhcp_row.setSpacing(6)
        self.dhcp_settings = QPushButton("  Configure")
        self.dhcp_settings.setIcon(icons.gear(14, theme.TEXT_BODY))
        self.dhcp_settings.setIconSize(QSize(14, 14))
        self.dhcp_settings.setFixedHeight(36)
        self.dhcp_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dhcp_settings.clicked.connect(self._configure_dhcp)
        self.dhcp_toggle = QPushButton("Start DHCP")
        self.dhcp_toggle.setObjectName("accent")
        self.dhcp_toggle.setFixedHeight(36)
        self.dhcp_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dhcp_toggle.clicked.connect(self._toggle_dhcp)
        dhcp_row.addWidget(self.dhcp_settings)
        dhcp_row.addWidget(self.dhcp_toggle, 1)
        layout.addLayout(dhcp_row)

        # Lease activity — sits BELOW the buttons so its visibility toggle
        # only pushes the footer/spacer, never moves the button row. Empty
        # text + always-in-layout means the buttons keep a fixed Y, which
        # eliminates the WA_TranslucentBackground stale-pixel artifact that
        # previously left ghost copies of the toggle button on screen.
        self.dhcp_leases = QLabel("")
        self.dhcp_leases.setObjectName("subtle")
        self.dhcp_leases.setWordWrap(True)
        self.dhcp_leases.setMinimumHeight(0)
        layout.addWidget(self.dhcp_leases)

        layout.addStretch(1)

        # ----- Footer -----
        footer = QHBoxLayout()
        footer.setSpacing(10)

        # Spindux branding — larger so it reads as a brand mark, not a label.
        brand = QLabel()
        brand_pix = icons.brand_logo(38)
        brand.setPixmap(brand_pix)
        brand.setFixedHeight(38)
        brand.setToolTip("Spindux Enterprise")

        hint = QLabel(f"v{__version__}  ·  Esc to close")
        hint.setObjectName("subtle")
        hint.setToolTip(f"{APP_NAME} v{__version__} — right-click tray icon for About / log folder / diagnostics")
        quit_btn = QPushButton("Quit")
        quit_btn.setObjectName("ghost")
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.clicked.connect(QApplication.instance().quit)
        footer.addWidget(brand)
        footer.addStretch(1)
        footer.addWidget(hint)
        footer.addWidget(quit_btn)
        layout.addLayout(footer)

        # Light shadow — just enough to lift the popup off the desktop
        # without the heavy "modal dialog" look the previous radius/alpha
        # gave it.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 110))
        root.setGraphicsEffect(shadow)

    # ---- data refresh ----
    def refresh_all(self):
        self._populate_nics()
        self._rebuild_presets()
        self._refresh_dhcp_ui()

    def _populate_nics(self):
        self.nic_combo.blockSignals(True)
        self.nic_combo.clear()
        nics = [n for n in nic_mod.list_nics() if not n.is_loopback]
        for n in nics:
            label = f"{n.name}  —  {n.ipv4 or 'no IP'}"
            if not n.is_up:
                label += "  (down)"
            self.nic_combo.addItem(label, n.name)
        self._nics = nics

        target = self.config.selected_nic
        if target:
            for i in range(self.nic_combo.count()):
                if self.nic_combo.itemData(i) == target:
                    self.nic_combo.setCurrentIndex(i)
                    break
        self.nic_combo.blockSignals(False)
        self._update_nic_status()

    def _update_nic_status(self):
        name = self.nic_combo.currentData()
        info = next((n for n in getattr(self, "_nics", []) if n.name == name), None)
        if not info:
            self.nic_status.setText("No interface selected")
            self._set_led(self.nic_led, theme.TEXT_DIM)
            self.mac_input.blockSignals(True)
            self.mac_input.setText("")
            self.mac_input.blockSignals(False)
            self.mac_status.setText("")
            for btn in (self.mac_apply_btn, self.mac_random_btn, self.mac_restore_btn):
                btn.setEnabled(False)
            self.mac_input.setEnabled(False)
            return
        bits = [info.ipv4 or "no IPv4", "up" if info.is_up else "down"]
        self.nic_status.setText("  •  ".join(bits))
        if info.is_up and info.ipv4:
            self._set_led(self.nic_led, theme.SUCCESS)
        elif info.is_up:
            self._set_led(self.nic_led, theme.WARNING)
        else:
            self._set_led(self.nic_led, theme.TEXT_DIM)
        # Only refresh the MAC field if the user isn't actively editing it —
        # otherwise every refresh wipes their typing.
        cur = mac_mod.normalize_mac(info.mac) if info.mac else None
        overridden = mac_mod.has_override(name)
        if not self.mac_input.hasFocus():
            self.mac_input.blockSignals(True)
            self.mac_input.setText(
                mac_mod.format_mac_pretty(cur) if cur else ""
            )
            self.mac_input.blockSignals(False)
        # Status line — show override + hardware MAC if available.
        if overridden:
            hw = mac_mod.hardware_mac(name)
            if hw and hw != cur:
                self.mac_status.setText(
                    f"overridden from hardware {mac_mod.format_mac_pretty(hw)}"
                )
                self.mac_status.setStyleSheet(
                    f"color: {theme.WARNING}; font-size: 11px;"
                )
            else:
                self.mac_status.setText("overridden")
                self.mac_status.setStyleSheet(
                    f"color: {theme.WARNING}; font-size: 11px;"
                )
        else:
            self.mac_status.setText("hardware MAC")
            self.mac_status.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            )
        self.mac_input.setEnabled(not self._mac_busy)
        for btn in (self.mac_apply_btn, self.mac_random_btn):
            btn.setEnabled(not self._mac_busy)
        # Disable Restore if there's no override to restore from.
        self.mac_restore_btn.setEnabled(
            overridden is True and not self._mac_busy
        )

    def _set_led(self, label: QLabel, color: str, size: int = 8):
        label.setPixmap(icons.dot(size, color).pixmap(size, size))

    def _on_nic_changed(self, _i: int):
        self.config.selected_nic = self.nic_combo.currentData()
        self.config.save()
        self._update_nic_status()
        self._rebuild_presets()

    def _rebuild_presets(self):
        while self.preset_layout.count() > 1:
            item = self.preset_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        nic_name = self.nic_combo.currentData()
        cur_ip = nic_mod.current_ip(nic_name) if nic_name else None

        if not self.config.presets:
            empty = QLabel("No presets yet — click 'New preset' to add one.")
            empty.setObjectName("subtle")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setContentsMargins(0, 14, 0, 14)
            self.preset_layout.insertWidget(0, empty)
            return

        for preset in self.config.presets:
            card = PresetCard(preset, cur_ip)
            card.apply_clicked.connect(self._apply_preset)
            card.edit_clicked.connect(self._edit_preset)
            card.delete_clicked.connect(self._delete_preset)
            self.preset_layout.insertWidget(self.preset_layout.count() - 1, card)

    def _set_status(self, msg: str, kind: str = "ok"):
        self.status_label.setObjectName(
            {"ok": "statusOk", "warn": "statusWarn", "err": "statusErr"}.get(kind, "statusOk")
        )
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(msg)
        QTimer.singleShot(4000, lambda: self.status_label.setText(""))

    # ---- apply pipeline (threaded) ----
    def _apply_in_background(self, nic_name: str, preset: Preset, label: str):
        if self._apply_busy:
            self._set_status("Previous change still applying…", "warn")
            return
        self._apply_busy = True
        self._set_status(f"Applying {label}…", "warn")

        def worker():
            try:
                ok, msg = nic_mod.apply_preset(nic_name, preset)
            except Exception as e:
                ok, msg = False, f"Apply failed: {e}"
            self.apply_done.emit(ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_apply_done(self, ok: bool, msg: str):
        self._apply_busy = False
        self._set_status(msg, "ok" if ok else "err")
        QTimer.singleShot(600, self.refresh_all)

    # ---- presets ----
    def _apply_preset(self, preset: Preset):
        nic_name = self.nic_combo.currentData()
        if not nic_name:
            self._set_status("Select a NIC first", "warn")
            return
        self._apply_in_background(nic_name, preset, preset.name or "preset")

    def _new_preset(self):
        dlg = PresetDialog(parent=self)
        if dlg.exec():
            self.config.presets.append(dlg.result_preset())
            self.config.save()
            self._rebuild_presets()

    def _edit_preset(self, preset: Preset):
        dlg = PresetDialog(preset=preset, parent=self)
        if dlg.exec():
            idx = self.config.presets.index(preset)
            self.config.presets[idx] = dlg.result_preset()
            self.config.save()
            self._rebuild_presets()

    def _delete_preset(self, preset: Preset):
        resp = QMessageBox.question(
            self, "Delete preset", f"Delete preset '{preset.name}'?"
        )
        if resp == QMessageBox.StandardButton.Yes:
            self.config.presets.remove(preset)
            self.config.save()
            self._rebuild_presets()

    # ---- MAC quick actions ----
    def _run_mac_action_bg(self, verb: str, fn):
        """Shared helper. verb is user-facing ('Setting MAC to AA:BB:...')."""
        nic_name = self.nic_combo.currentData()
        if not nic_name:
            self._set_status("Select a NIC first", "warn")
            return
        if self._mac_busy:
            self._set_status("MAC change already in progress…", "warn")
            return
        # Lock the entire MAC row + show a busy cursor so the user can't
        # double-click into a half-finished registry write. Adapter restart
        # takes 4-8s; without this, it looks like the app froze.
        self._mac_busy = True
        for w in (self.mac_input, self.mac_apply_btn,
                  self.mac_random_btn, self.mac_restore_btn):
            w.setEnabled(False)
        self.mac_apply_btn.setText("Applying…")
        QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
        self._set_status(f"{verb} — restarting adapter…", "warn")

        def worker():
            try:
                ok, msg = fn(nic_name)
            except Exception as e:
                ok, msg = False, f"MAC change failed: {e}"
            self.mac_done.emit(ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _randomize_mac(self):
        mac12 = mac_mod.random_locally_administered_mac()
        pretty = mac_mod.format_mac_pretty(mac12)
        self.mac_input.setText(pretty)
        self._run_mac_action_bg(
            f"Setting MAC to {pretty}",
            lambda name: mac_mod.set_mac(name, mac12),
        )

    def _restore_mac(self):
        self._run_mac_action_bg("Restoring hardware MAC", mac_mod.restore_mac)

    def _apply_typed_mac(self):
        raw = self.mac_input.text().strip()
        if not raw:
            self._set_status("Type a MAC (e.g. 02:AA:BB:CC:DD:EE) first", "warn")
            return
        ok, err, _ = mac_mod.validate_mac(raw)
        if not ok:
            self._set_status(err, "err")
            return
        self._run_mac_action_bg(
            f"Setting MAC to {raw}",
            lambda name: mac_mod.set_mac(name, raw),
        )

    def _on_mac_done(self, ok: bool, msg: str):
        self._mac_busy = False
        # Always restore the cursor + Apply button text — even on error, so
        # the UI never gets stuck in busy mode.
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self.mac_apply_btn.setText("Apply")
        for w in (self.mac_input, self.mac_apply_btn,
                  self.mac_random_btn, self.mac_restore_btn):
            w.setEnabled(True)
        self._set_status(msg, "ok" if ok else "err")
        # Adapter was just disabled/enabled — repopulate so current MAC,
        # IP and link state all refresh together.
        QTimer.singleShot(600, self.refresh_all)

    # ---- manual ----
    def _manual_prefix(self) -> int | None:
        mask = self.m_mask.text().strip() or "255.255.255.0"
        return mask_to_prefix(mask)

    def _apply_manual(self):
        nic_name = self.nic_combo.currentData()
        if not nic_name:
            self._set_status("Select a NIC first", "warn")
            return
        ip = self.m_ip.text().strip()
        if not ip:
            self._set_status("IP required", "warn")
            return
        prefix = self._manual_prefix()
        if prefix is None:
            self._set_status(f"Invalid subnet mask: {self.m_mask.text()!r}", "err")
            return
        preset = Preset(
            name="(manual)", ip=ip, prefix=prefix,
            gateway=self.m_gw.text().strip(),
        )
        self._apply_in_background(nic_name, preset, f"{ip}  {self.m_mask.text().strip()}")

    def _save_manual_as_preset(self):
        ip = self.m_ip.text().strip()
        if not ip:
            self._set_status("Enter an IP to save", "warn")
            return
        prefix = self._manual_prefix() or 24
        seed = Preset(
            name="", ip=ip, prefix=prefix,
            gateway=self.m_gw.text().strip(),
        )
        dlg = PresetDialog(preset=seed, parent=self)
        if dlg.exec():
            self.config.presets.append(dlg.result_preset())
            self.config.save()
            self._rebuild_presets()

    def prefill_manual(self, ip: str, prefix: int = 24, gateway: str = ""):
        from .validate import prefix_to_mask
        self.m_ip.setText(ip)
        self.m_mask.setText(prefix_to_mask(prefix))
        if gateway:
            self.m_gw.setText(gateway)
        self._set_status(f"Suggested {ip} — ready to apply", "ok")

    # ---- scan ----
    def _open_scan(self):
        nic_name = self.nic_combo.currentData()
        bind_ip = nic_mod.current_ip(nic_name) if nic_name else ""
        if not bind_ip:
            self._set_status("NIC has no IPv4 — assign one before scanning.", "warn")
            return
        dlg = ScanDialog(self.sniffer, bind_ip, parent=self)
        dlg.apply_subnet.connect(self.prefill_manual)
        dlg.exec()

    # ---- DHCP ----
    def _configure_dhcp(self):
        nic_name = self.nic_combo.currentData()
        suggested = nic_mod.current_ip(nic_name) if nic_name else ""
        dlg = DhcpDialog(self.config.dhcp, suggested_bind_ip=suggested or "", parent=self)
        if dlg.exec():
            self.config.dhcp = dlg.result_cfg()
            self.config.save()
            self._refresh_dhcp_ui()

    def _toggle_dhcp(self):
        if self._dhcp_busy:
            self._set_status("DHCP action already in progress…", "warn")
            return

        if dhcp_mod.is_running():
            # Stop path — keep button text as "Stop DHCP" the whole time
            # (only the disabled state + status line signal the busy work).
            # Changing button text during a click can leave a Qt repaint
            # artifact that reads as a duplicated button on some boxes.
            self._set_dhcp_busy(True)
            self._set_status("Stopping DHCP server…", "warn")

            def stop_worker():
                try:
                    ok, msg = dhcp_mod.stop()
                except Exception as e:
                    ok, msg = False, f"Stop failed: {e}"
                self.dhcp_done.emit(ok, msg)

            threading.Thread(target=stop_worker, daemon=True).start()
            return

        cfg = self.config.dhcp
        if not (cfg.bind_ip and cfg.range_start and cfg.range_end):
            self._configure_dhcp()
            return
        if not dhcp_mod.exe_exists(cfg):
            QMessageBox.warning(
                self, "dhcpsrv.exe not available",
                "The bundled DHCP server binary couldn't be found and no "
                "override is configured. This usually means the app was "
                "repackaged without the vendor/dhcpsrv folder. Set an explicit "
                "path in Configure to point at a local install.",
            )
            return

        # Start path — firewall rules + orphan cleanup + probe takes ~3-5s.
        self._set_dhcp_busy(True)
        self._set_status("Configuring firewall and launching dhcpsrv…", "warn")

        def start_worker():
            try:
                ok, msg = dhcp_mod.start(cfg)
            except Exception as e:
                ok, msg = False, f"Start failed: {e}"
            self.dhcp_done.emit(ok, msg)

        threading.Thread(target=start_worker, daemon=True).start()

    def _set_dhcp_busy(self, busy: bool):
        """Single source of truth for the DHCP button enable/disable. Text is
        owned by `_refresh_dhcp_ui`; this only toggles interactivity."""
        self._dhcp_busy = busy
        self.dhcp_toggle.setEnabled(not busy)
        # Force a repaint so the button never carries over a stale frame after
        # rapid disable/enable + text-change cycles.
        self.dhcp_toggle.repaint()

    def _on_dhcp_done(self, ok: bool, msg: str):
        self._set_dhcp_busy(False)
        self._set_status(msg, "ok" if ok else "err")
        self._refresh_dhcp_ui()

    def _lease_tick(self):
        # Tight no-op when hidden — file I/O + UI churn pointless when nobody
        # is looking. When DHCP is running we refresh the whole DHCP UI (not
        # just leases) so a process death is reflected within 2 seconds; when
        # the popup hasn't seen running yet we still poll once so a server
        # started via the tray menu surfaces here without the user having to
        # interact.
        if not self.isVisible():
            return
        running = dhcp_mod.is_running()
        if running != self._last_known_dhcp_running:
            self._last_known_dhcp_running = running
            self._refresh_dhcp_ui()
            return
        if running:
            self._refresh_dhcp_leases()

    def _refresh_dhcp_leases(self):
        """Pull recent lease events and render a terse activity line. Always
        keeps the label in layout — empty text when nothing to show — so the
        DHCP button row never shifts position."""
        try:
            snap = dhcp_mod.lease_snapshot(max_events=20)
        except Exception:
            self.dhcp_leases.setText("")
            return
        active = snap.active
        recent = snap.recent
        if not active and not recent:
            self.dhcp_leases.setText(
                "Waiting for clients… (DISCOVER / REQUEST / ACK events appear here)"
            )
            self.dhcp_leases.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            )
            return
        lines = [f"{len(active)} active lease(s)"]
        # Show up to 3 most recently touched leases, newest first. We pull
        # "newest event per MAC" from `recent` rather than the dict ordering.
        seen_macs: set[str] = set()
        latest: list[tuple[str, str, str]] = []  # (ip, mac, host)
        for ev in recent:
            if not ev.mac or ev.mac in seen_macs:
                continue
            if ev.mac not in active:
                continue
            seen_macs.add(ev.mac)
            lease = active[ev.mac]
            host = f" ({lease.hostname})" if lease.hostname else ""
            latest.append((lease.ip, ev.mac.lower(), host))
            if len(latest) >= 3:
                break
        for ip, mac, host in latest:
            lines.append(f"· {ip}  →  {mac}{host}")
        self.dhcp_leases.setText("\n".join(lines))
        self.dhcp_leases.setStyleSheet(
            f"color: {theme.TEXT_BODY}; font-size: 11px;"
        )

    def _refresh_dhcp_ui(self):
        running = dhcp_mod.is_running()
        self._last_known_dhcp_running = running
        cfg = self.config.dhcp
        # Always re-set + repaint, even if the text looks unchanged. Skipping
        # setText on equal text was an optimization that hid an actual bug
        # where the chip and button could show inconsistent states.
        self.dhcp_toggle.setText("Stop DHCP" if running else "Start DHCP")
        self.dhcp_toggle.repaint()
        if running:
            self._set_led(self.dhcp_led, theme.SUCCESS)
            self.dhcp_chip.setText("LIVE")
            self.dhcp_chip.setStyleSheet(
                f"background: rgba(109, 227, 164, 40); color: {theme.SUCCESS}; "
                f"border: 1px solid rgba(109, 227, 164, 120); border-radius: 10px; "
                f"padding: 2px 8px; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
            )
            self.dhcp_status.setText(
                f"Serving {cfg.range_start} – {cfg.range_end} on {cfg.bind_ip}"
            )
            self.dhcp_status.setStyleSheet(
                f"color: {theme.TEXT_BODY}; font-size: 11px;"
            )
            self._refresh_dhcp_leases()
        elif cfg.range_start and cfg.range_end:
            self._set_led(self.dhcp_led, theme.TEXT_MUTED)
            self.dhcp_chip.setText("READY")
            self.dhcp_chip.setStyleSheet("")
            self.dhcp_status.setText(
                f"Configured for {cfg.range_start} – {cfg.range_end}"
            )
            self.dhcp_status.setStyleSheet(
                f"color: {theme.TEXT_MUTED}; font-size: 11px;"
            )
            self.dhcp_leases.setText("")
        else:
            self._set_led(self.dhcp_led, theme.WARNING)
            self.dhcp_chip.setText("SETUP")
            self.dhcp_chip.setStyleSheet(
                f"background: rgba(255, 194, 102, 40); color: {theme.WARNING}; "
                f"border: 1px solid rgba(255, 194, 102, 120); border-radius: 10px; "
                f"padding: 2px 8px; font-size: 10px; font-weight: 700; letter-spacing: 0.8px;"
            )
            self.dhcp_status.setText("Not configured — click Configure")
            self.dhcp_status.setStyleSheet(
                f"color: {theme.WARNING}; font-size: 11px;"
            )
            self.dhcp_leases.setText("")
        # Force a full popup repaint. WA_TranslucentBackground + frameless
        # windows on Windows can leave stale pixels of moved children behind
        # when the layout reflows; update() schedules a clean paintEvent over
        # the entire surface, eliminating ghost-button artifacts.
        self.update()

    # ---- show / hide ----
    def show_anchored(self):
        from PyQt6.QtGui import QCursor
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = avail.right() - self.WIDTH - self.SCREEN_MARGIN
        y = avail.bottom() - self.HEIGHT - self.SCREEN_MARGIN
        self.refresh_all()
        self.setWindowOpacity(0.0)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def show_at(self, _anchor):
        self.show_anchored()

    def hide_animated(self):
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)

        def _done():
            try:
                self._fade.finished.disconnect(_done)
            except TypeError:
                pass
            self.hide()

        self._fade.finished.connect(_done)
        self._fade.start()
