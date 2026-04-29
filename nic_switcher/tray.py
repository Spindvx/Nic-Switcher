"""System tray icon + context menu."""
from __future__ import annotations

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__
from . import dhcp as dhcp_mod
from . import diagnostics
from .config import AppConfig
from .icon import make_tray_icon
from .popup import Popup
from .theme import STYLE


class Tray(QSystemTrayIcon):
    def __init__(self, config: AppConfig, app: QApplication):
        super().__init__(make_tray_icon(64), app)
        self.config = config
        self.app = app
        self.popup = Popup(config)
        self.app.focusChanged.connect(self._on_focus_changed)

        self.setToolTip(f"{APP_NAME} v{__version__}")
        menu = QMenu()
        menu.setStyleSheet(STYLE)

        open_action = QAction("Open", menu)
        open_action.triggered.connect(self._open_at_cursor)
        menu.addAction(open_action)

        menu.addSeparator()
        self.dhcp_action = QAction("Start DHCP server", menu)
        self.dhcp_action.triggered.connect(self._toggle_dhcp)
        menu.addAction(self.dhcp_action)

        menu.addSeparator()
        log_action = QAction("Open log folder", menu)
        log_action.triggered.connect(self._open_log_folder)
        menu.addAction(log_action)

        diag_action = QAction("Export diagnostics…", menu)
        diag_action.triggered.connect(self._export_diagnostics)
        menu.addAction(diag_action)

        about_action = QAction("About NIC Switcher", menu)
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)

        menu.addSeparator()
        quit_action = QAction("Quit NIC Switcher", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        menu.aboutToShow.connect(self._sync_menu)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._toggle_popup()

    def _toggle_popup(self):
        if self.popup.isVisible():
            self.popup.hide_animated()
        else:
            self._open_at_cursor()

    def _open_at_cursor(self):
        self.popup.show_anchored()

    def _on_focus_changed(self, old, new):
        if new is None and self.popup.isVisible():
            # User clicked away — but the click may have been on the tray icon
            # to toggle off, which is handled by _on_activated. Slight delay
            # avoids racing with that toggle. Pinned popups never auto-hide.
            if self.popup.is_pinned():
                return
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(150, self._maybe_hide)

    def _maybe_hide(self):
        if self.popup.is_pinned():
            return
        if self.popup.isVisible() and not self.popup.isActiveWindow():
            self.popup.hide_animated()

    def _toggle_dhcp(self):
        if dhcp_mod.is_running():
            ok, msg = dhcp_mod.stop()
        else:
            ok, msg = dhcp_mod.start(self.config.dhcp)
        self.showMessage("NIC Switcher", msg, QSystemTrayIcon.MessageIcon.Information, 3000)
        if self.popup.isVisible():
            self.popup._refresh_dhcp_ui()

    def _sync_menu(self):
        running = dhcp_mod.is_running()
        self.dhcp_action.setText("Stop DHCP server" if running else "Start DHCP server")

    def _open_log_folder(self):
        ok, msg = diagnostics.open_log_folder()
        self.showMessage(
            APP_NAME, msg,
            QSystemTrayIcon.MessageIcon.Information
            if ok else QSystemTrayIcon.MessageIcon.Warning,
            3000,
        )

    def _export_diagnostics(self):
        ok, msg, _path = diagnostics.export_bundle()
        self.showMessage(
            APP_NAME, msg,
            QSystemTrayIcon.MessageIcon.Information
            if ok else QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _show_about(self):
        # Lazy import — keeps tray.py from pulling the dialog tree at startup.
        from .dialogs import AboutDialog
        dlg = AboutDialog(parent=self.popup if self.popup.isVisible() else None)
        dlg.exec()

    def _quit(self):
        try:
            dhcp_mod.stop()
        except Exception:
            pass
        try:
            self.popup.sniffer.stop()
        except Exception:
            pass
        self.app.quit()
