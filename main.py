"""NIC Switcher — entry point. Ensures admin, then runs the tray app."""
from __future__ import annotations

import ctypes
import faulthandler
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from nic_switcher import APP_NAME
from nic_switcher import dhcp as dhcp_mod
from nic_switcher.config import AppConfig, CONFIG_PATH
from nic_switcher.tray import Tray


CRASH_LOG = CONFIG_PATH.parent / "crash.log"


def _install_crash_loggers() -> None:
    """Capture every class of crash to %APPDATA%\\NICSwitcher\\crash.log:
    - Python unhandled exceptions (sys.excepthook)
    - Exceptions in background threads (threading.excepthook, Py 3.8+)
    - Native crashes / segfaults (faulthandler)
    """
    CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(CRASH_LOG, "a", encoding="utf-8", buffering=1)
        fh.write(f"\n===== session start {datetime.now().isoformat(timespec='seconds')} =====\n")
        faulthandler.enable(file=fh)
    except OSError:
        fh = None

    def _log_exc(kind: str, exc_type, exc_value, tb) -> None:
        if fh is None:
            return
        try:
            fh.write(
                f"\n--- {kind} {datetime.now().isoformat(timespec='seconds')} ---\n"
            )
            traceback.print_exception(exc_type, exc_value, tb, file=fh)
            fh.flush()
        except Exception:
            pass

    def sys_hook(exc_type, exc_value, tb):
        _log_exc("main-thread exception", exc_type, exc_value, tb)
        sys.__excepthook__(exc_type, exc_value, tb)

    def thread_hook(args):
        _log_exc(
            f"thread exception ({args.thread.name})",
            args.exc_type, args.exc_value, args.exc_traceback,
        )

    sys.excepthook = sys_hook
    try:
        threading.excepthook = thread_hook  # Py 3.8+
    except AttributeError:
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    try:
        # list2cmdline properly escapes args containing spaces or quotes.
        script = os.path.abspath(sys.argv[0])
        params = subprocess.list2cmdline([script] + list(sys.argv[1:]))
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        return rc > 32
    except Exception:
        return False


def main() -> int:
    if sys.platform != "win32":
        print("NIC Switcher is Windows-only.")
        return 1

    _install_crash_loggers()

    if not is_admin() and "--no-elevate" not in sys.argv:
        if relaunch_as_admin():
            return 0

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        # DPI awareness already set (e.g. by the exe manifest) — ignore.
        pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)

    config = AppConfig.load()

    if not is_admin():
        QMessageBox.warning(
            None,
            APP_NAME,
            "Running without administrator privileges.\n\n"
            "NIC changes and the DHCP server will fail. Restart the app "
            "as administrator.",
        )

    tray = Tray(config, app)
    tray.show()
    tray.showMessage(
        APP_NAME,
        "Click the tray icon to switch NIC presets.",
        tray.MessageIcon.Information,
        2500,
    )

    # Guarantee cleanup even if the Quit button bypasses Tray._quit.
    def _cleanup():
        try:
            dhcp_mod.stop()
        except Exception:
            pass
        try:
            tray.popup.sniffer.stop()
        except Exception:
            pass
    app.aboutToQuit.connect(_cleanup)

    # Fire firewall setup in the background so by the time the user clicks
    # Start DHCP, rules are already in place. Never blocks anything.
    def _ensure_firewall_bg():
        try:
            from nic_switcher import firewall
            exe = dhcp_mod.bundled_dhcpsrv_path()
            firewall.ensure_dhcp_rules(exe)
        except Exception:
            pass
    threading.Thread(target=_ensure_firewall_bg, daemon=True).start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
