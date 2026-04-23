"""Preset editor and DHCP settings dialogs — simple, NIC-aware."""
from __future__ import annotations

import ipaddress
from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import firewall, icons, nic as nic_mod, theme
from . import discover
from .config import DhcpConfig, Preset
from .theme import STYLE
from .validate import (
    is_valid_ipv4, is_valid_mask, mask_to_prefix, prefix_to_mask,
    validate_dhcp_range, validate_preset,
)


def _apply_window_chrome(dlg: QDialog):
    dlg.setStyleSheet(STYLE)
    dlg.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dlg.setSizeGripEnabled(False)


def _form_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {theme.TEXT_SECOND}; font-size: 11px; "
        f"font-weight: 600; letter-spacing: 0.4px;"
    )
    return lbl


class _InlineError(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"color: {theme.DANGER}; font-size: 11px; font-weight: 600;")
        self.setVisible(False)
        self.setWordWrap(True)

    def set(self, msg: str):
        self.setText(msg)
        self.setVisible(bool(msg))


# ---------------------------------------------------------------------------
# Preset editor — mask input, no CIDR
# ---------------------------------------------------------------------------

class PresetDialog(QDialog):
    def __init__(self, preset: Preset | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit preset" if preset and preset.name else "New preset")
        self.resize(440, 440)
        _apply_window_chrome(self)

        title = QLabel("Preset")
        title.setObjectName("title")
        subtitle = QLabel(
            "Leave IP blank to make this a 'switch to DHCP client' preset."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.name = QLineEdit(preset.name if preset else "")
        self.name.setPlaceholderText("e.g. Somerset")
        self.ip = QLineEdit(preset.ip if preset else "")
        self.ip.setPlaceholderText("10.17.75.240   (blank = DHCP)")
        self.mask = QLineEdit(
            prefix_to_mask(preset.prefix) if preset and preset.prefix else "255.255.255.0"
        )
        self.mask.setPlaceholderText("255.255.255.0")
        self.gateway = QLineEdit(preset.gateway if preset else "")
        self.gateway.setPlaceholderText("10.17.75.1   (optional)")
        self.dns1 = QLineEdit(preset.dns1 if preset else "")
        self.dns1.setPlaceholderText("8.8.8.8   (optional)")
        self.dns2 = QLineEdit(preset.dns2 if preset else "")
        self.dns2.setPlaceholderText("1.1.1.1   (optional)")

        form = QFormLayout()
        form.setSpacing(8)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow(_form_label("Name"), self.name)
        form.addRow(_form_label("IP address"), self.ip)
        form.addRow(_form_label("Subnet mask"), self.mask)
        form.addRow(_form_label("Gateway"), self.gateway)
        form.addRow(_form_label("DNS 1"), self.dns1)
        form.addRow(_form_label("DNS 2"), self.dns2)

        self.err = _InlineError()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setObjectName("accent")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        for b in (btns.button(QDialogButtonBox.StandardButton.Save),
                  btns.button(QDialogButtonBox.StandardButton.Cancel)):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)

        root = QWidget(self)
        root.setObjectName("root")
        body = QVBoxLayout(root)
        body.setContentsMargins(22, 20, 22, 18)
        body.setSpacing(12)
        body.addWidget(title)
        body.addWidget(subtitle)
        body.addSpacing(4)
        body.addLayout(form)
        body.addStretch(1)
        body.addWidget(self.err)
        body.addWidget(btns)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addWidget(root)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 180))
        root.setGraphicsEffect(shadow)

    def _accept(self):
        ip = self.ip.text().strip()
        mask_text = self.mask.text().strip() or "255.255.255.0"
        if ip and not is_valid_mask(mask_text):
            self.err.set(f"Invalid subnet mask: {mask_text!r}")
            return
        prefix = mask_to_prefix(mask_text) or 24
        ok, msg = validate_preset(
            ip, prefix, self.gateway.text().strip(),
            self.dns1.text().strip(), self.dns2.text().strip(),
        )
        if not ok:
            self.err.set(msg)
            return
        if not self.name.text().strip():
            self.err.set("Name is required.")
            return
        self.err.set("")
        self._result_prefix = prefix
        self.accept()

    def result_preset(self) -> Preset:
        prefix = getattr(self, "_result_prefix", None)
        if prefix is None:
            prefix = mask_to_prefix(self.mask.text().strip()) or 24
        return Preset(
            name=self.name.text().strip() or "Preset",
            ip=self.ip.text().strip(),
            prefix=prefix,
            gateway=self.gateway.text().strip(),
            dns1=self.dns1.text().strip(),
            dns2=self.dns2.text().strip(),
        )


# ---------------------------------------------------------------------------
# DHCP — NIC-driven, auto-derives everything
# ---------------------------------------------------------------------------

def _derive_range(bind_ip: str, mask: str) -> tuple[str, str]:
    """Given NIC IP + mask, suggest a reasonable client range (x.100 → x.200)."""
    try:
        net = ipaddress.IPv4Network(f"{bind_ip}/{mask}", strict=False)
    except ValueError:
        return "", ""
    base = list(net.hosts())
    if len(base) < 50:
        return "", ""
    # Pick .100 … .200 if /24-ish; otherwise take middle chunk.
    if net.prefixlen >= 24 and net.prefixlen <= 30:
        first = net.network_address + 100
        last = net.network_address + 200
        if first in net and last in net:
            return str(first), str(last)
    mid = len(base) // 2
    start = base[max(0, mid - 50)]
    end = base[min(len(base) - 1, mid + 49)]
    return str(start), str(end)


class DhcpDialog(QDialog):
    def __init__(self, cfg: DhcpConfig, suggested_bind_ip: str = "", parent=None):
        super().__init__(parent)
        self.cfg_in = cfg
        self.setWindowTitle("DHCP server — NIC Switcher")
        self.resize(520, 560)
        _apply_window_chrome(self)

        title = QLabel("DHCP Server")
        title.setObjectName("title")
        subtitle = QLabel(
            "Pick a NIC — the IP, mask, gateway and range fill in automatically. "
            "Uses the bundled "
            "<a style='color:#5bd7ff; text-decoration:none;' "
            "href='https://www.dhcpserver.de/cms/'>dhcpsrv.exe</a>."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setOpenExternalLinks(True)
        subtitle.setWordWrap(True)

        # --- NIC dropdown ---
        self.nic_combo = QComboBox()
        self.nic_combo.setMinimumHeight(34)
        self.nic_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self._populate_nics(prefer_ip=cfg.bind_ip or suggested_bind_ip)
        self.nic_combo.currentIndexChanged.connect(self._on_nic_changed)

        # --- Advanced fields (auto-filled, user can override) ---
        self.bind_ip = QLineEdit(cfg.bind_ip or suggested_bind_ip)
        self.bind_ip.setPlaceholderText("(auto from NIC)")
        self.range_start = QLineEdit(cfg.range_start)
        self.range_start.setPlaceholderText("(auto)")
        self.range_end = QLineEdit(cfg.range_end)
        self.range_end.setPlaceholderText("(auto)")
        self.subnet = QLineEdit(cfg.subnet_mask or "255.255.255.0")
        self.subnet.setPlaceholderText("255.255.255.0")
        self.gateway = QLineEdit(cfg.gateway)
        self.gateway.setPlaceholderText("(optional)")
        self.dns = QLineEdit(cfg.dns or "8.8.8.8")
        self.dns.setPlaceholderText("8.8.8.8, 1.1.1.1")
        self.lease = QSpinBox()
        self.lease.setRange(60, 7 * 86400)
        self.lease.setValue(cfg.lease_seconds)
        self.lease.setSuffix("  sec")
        self.lease.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)

        # Range on one line
        range_row = QWidget()
        rr = QHBoxLayout(range_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.setSpacing(6)
        rr.addWidget(self.range_start, 1)
        dash = QLabel("—")
        dash.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        rr.addWidget(dash)
        rr.addWidget(self.range_end, 1)

        form = QFormLayout()
        form.setSpacing(8)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow(_form_label("Interface"), self.nic_combo)
        form.addRow(_form_label("Bind IP"), self.bind_ip)
        form.addRow(_form_label("Subnet mask"), self.subnet)
        form.addRow(_form_label("Gateway (opt)"), self.gateway)
        form.addRow(_form_label("Range"), range_row)
        form.addRow(_form_label("DNS"), self.dns)
        form.addRow(_form_label("Lease"), self.lease)

        # --- Firewall row ---
        self.fw_check = QCheckBox("Configure Windows Firewall (allow UDP 67/68)")
        self.fw_check.setChecked(True)
        self.fw_status = QLabel("Checking firewall…")
        self.fw_status.setObjectName("subtle")
        # rules_present() takes ~2s — never call it on the UI thread.
        from PyQt6.QtCore import QTimer
        import threading
        def _refresh_async():
            try:
                present = firewall.rules_present()
            except Exception:
                present = False
            # bounce back to UI thread via singleShot
            QTimer.singleShot(0, lambda: self._apply_fw_status(present))
        threading.Thread(target=_refresh_async, daemon=True).start()

        fw_row = QHBoxLayout()
        fw_row.setSpacing(8)
        fw_row.addWidget(self.fw_check, 1)
        fw_now = QPushButton("Apply now")
        fw_now.setObjectName("ghost")
        fw_now.setCursor(Qt.CursorShape.PointingHandCursor)
        fw_now.clicked.connect(self._apply_firewall_now)
        fw_remove = QPushButton("Remove rules")
        fw_remove.setObjectName("ghost")
        fw_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        fw_remove.clicked.connect(self._remove_firewall)
        fw_row.addWidget(fw_now)
        fw_row.addWidget(fw_remove)

        # --- Advanced: override exe path ---
        self.exe = QLineEdit(cfg.exe_path)
        self.exe.setPlaceholderText("(leave blank to use bundled copy)")
        browse = QPushButton()
        browse.setObjectName("icon")
        browse.setIcon(icons.search(16, theme.TEXT_BODY))
        browse.setIconSize(QSize(16, 16))
        browse.setFixedSize(34, 34)
        browse.setToolTip("Browse…")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._pick_exe)
        exe_row = QWidget()
        erl = QHBoxLayout(exe_row)
        erl.setContentsMargins(0, 0, 0, 0)
        erl.setSpacing(6)
        erl.addWidget(self.exe, 1)
        erl.addWidget(browse)

        adv_label = QLabel("ADVANCED")
        adv_label.setObjectName("section")
        adv_form = QFormLayout()
        adv_form.setSpacing(8)
        adv_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        adv_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        adv_form.addRow(_form_label("dhcpsrv.exe"), exe_row)

        self.err = _InlineError()

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setObjectName("accent")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setObjectName("ghost")
        for b in (btns.button(QDialogButtonBox.StandardButton.Save),
                  btns.button(QDialogButtonBox.StandardButton.Cancel)):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)

        root = QWidget(self)
        root.setObjectName("root")
        body = QVBoxLayout(root)
        body.setContentsMargins(22, 20, 22, 18)
        body.setSpacing(12)
        body.addWidget(title)
        body.addWidget(subtitle)
        body.addSpacing(2)
        body.addLayout(form)
        body.addSpacing(4)
        body.addLayout(fw_row)
        body.addWidget(self.fw_status)
        body.addSpacing(2)
        body.addWidget(adv_label)
        body.addLayout(adv_form)
        body.addStretch(1)
        body.addWidget(self.err)
        body.addWidget(btns)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addWidget(root)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 180))
        root.setGraphicsEffect(shadow)

        # Trigger initial autofill if fields are blank
        if not cfg.bind_ip or not cfg.range_start or not cfg.range_end:
            self._on_nic_changed(self.nic_combo.currentIndex())

    # ---- NIC handling ----
    def _populate_nics(self, prefer_ip: str = ""):
        self._nics = [n for n in nic_mod.list_nics() if not n.is_loopback]
        self.nic_combo.clear()
        pre_index = 0
        for i, n in enumerate(self._nics):
            label = f"{n.name}  —  {n.ipv4 or 'no IP'}"
            if not n.is_up:
                label += "  (down)"
            self.nic_combo.addItem(label, n.name)
            if prefer_ip and n.ipv4 == prefer_ip:
                pre_index = i
        if self._nics:
            self.nic_combo.setCurrentIndex(pre_index)

    def _current_nic(self) -> Optional[object]:
        name = self.nic_combo.currentData()
        return next((n for n in self._nics if n.name == name), None)

    def _on_nic_changed(self, _i: int):
        nic = self._current_nic()
        if not nic or not nic.ipv4:
            return
        self.bind_ip.setText(nic.ipv4)
        if nic.netmask:
            self.subnet.setText(nic.netmask)
        gw = discover.default_gateway_for(nic.ipv4)
        if gw:
            self.gateway.setText(gw)
        start, end = _derive_range(nic.ipv4, self.subnet.text().strip() or "255.255.255.0")
        if start and end:
            self.range_start.setText(start)
            self.range_end.setText(end)

    # ---- firewall ----
    def _apply_firewall_now(self):
        import threading
        from PyQt6.QtCore import QTimer
        self.fw_status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.fw_status.setText("Applying firewall rules…")
        exe = self.exe.text().strip() or None

        def worker():
            try:
                ok, msg = firewall.ensure_dhcp_rules(exe)
            except Exception as e:
                ok, msg = False, f"Firewall error: {e}"
            QTimer.singleShot(0, lambda: self._on_fw_result(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _remove_firewall(self):
        import threading
        from PyQt6.QtCore import QTimer
        self.fw_status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.fw_status.setText("Removing firewall rules…")

        def worker():
            try:
                ok, msg = firewall.remove_dhcp_rules()
            except Exception as e:
                ok, msg = False, f"Firewall error: {e}"
            QTimer.singleShot(0, lambda: self._on_fw_result(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_fw_result(self, ok: bool, msg: str):
        self.fw_status.setStyleSheet(
            f"color: {theme.SUCCESS if ok else theme.DANGER}; "
            f"font-size: 11px; font-weight: 600;"
        )
        self.fw_status.setText(msg)

    def _apply_fw_status(self, present: bool):
        self.fw_status.setStyleSheet(
            f"color: {theme.SUCCESS if present else theme.TEXT_MUTED}; "
            f"font-size: 11px; {'font-weight: 600;' if present else ''}"
        )
        self.fw_status.setText(
            "Firewall rules are in place — UDP 67/68 allowed."
            if present else
            "No NIC Switcher firewall rules yet — will be added on Start."
        )

    # ---- exe path ----
    def _pick_exe(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select dhcpsrv.exe", self.exe.text(), "Executable (*.exe)"
        )
        if path:
            self.exe.setText(path)

    # ---- accept ----
    def _accept(self):
        cfg = self.result_cfg()
        if not is_valid_mask(cfg.subnet_mask):
            self.err.set(f"Invalid subnet mask: {cfg.subnet_mask!r}")
            return
        ok, msg, _ = validate_dhcp_range(
            cfg.bind_ip, cfg.range_start, cfg.range_end, cfg.subnet_mask
        )
        if not ok:
            self.err.set(msg)
            return
        self.err.set("")
        # Don't block Save on firewall — it's applied async, and dhcp.start()
        # will re-run ensure_dhcp_rules on the worker thread anyway.
        self.accept()

    def result_cfg(self) -> DhcpConfig:
        return DhcpConfig(
            exe_path=self.exe.text().strip(),
            bind_ip=self.bind_ip.text().strip(),
            range_start=self.range_start.text().strip(),
            range_end=self.range_end.text().strip(),
            subnet_mask=self.subnet.text().strip() or "255.255.255.0",
            gateway=self.gateway.text().strip(),
            dns=self.dns.text().strip(),
            lease_seconds=self.lease.value(),
        )
