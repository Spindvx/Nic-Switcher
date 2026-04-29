"""One-click diagnostics bundle.

When a colleague's NIC Switcher is misbehaving, asking them to find log
files in %APPDATA% is friction. This module exports a single .zip on the
user's Desktop containing everything support would ask for:

  * `config.json` (sanitized — see _sanitize_config)
  * `dhcpsrv.log` and `dhcpsrv-stderr.log` (if present)
  * `crash.log` (if present)
  * `system_info.txt`  — Windows version, NIC list, default route
  * `ipconfig_all.txt` — full ipconfig /all output
  * `route_print.txt`  — IPv4 routing table
  * `firewall_rules.txt` — our DHCP rules' state
  * `version.txt` — app version + Python version + build origin

User runs Tray menu -> Export diagnostics, gets a zip path back, and
emails/uploads it to whoever's helping. No prying for logs.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

from . import APP_NAME, __version__
from .config import CONFIG_PATH

CREATE_NO_WINDOW = 0x08000000


def _desktop_dir() -> Path:
    """%USERPROFILE%\\Desktop, fallback to %USERPROFILE%."""
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    desk = home / "Desktop"
    return desk if desk.is_dir() else home


def _sanitize_config(raw: str) -> str:
    """The config has no secrets today, but be defensive — if a future field
    holds an API key or wifi PSK, redact rather than leak. Recognises common
    sensitive key names; pass-through everything else."""
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    SENSITIVE = {"password", "psk", "secret", "api_key", "token", "wifi_password"}

    def _scrub(obj):
        if isinstance(obj, dict):
            return {
                k: ("<redacted>" if k.lower() in SENSITIVE else _scrub(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_scrub(x) for x in obj]
        return obj

    return json.dumps(_scrub(data), indent=2)


def _shell(cmd: list[str], timeout: int = 10) -> str:
    """Run a system tool and return combined stdout/stderr. Best-effort —
    failure becomes a one-line note in the bundle, not an exception."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        return (proc.stdout or "") + (
            ("\n--- stderr ---\n" + proc.stderr) if proc.stderr else ""
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"[{' '.join(cmd)} failed: {e}]"


def _system_info() -> str:
    lines = [
        f"NIC Switcher {__version__}",
        f"Python:   {sys.version.splitlines()[0]}",
        f"Platform: {platform.platform()}",
        f"Machine:  {platform.machine()}",
        f"Frozen:   {bool(getattr(sys, 'frozen', False))}  "
        f"(MEIPASS={getattr(sys, '_MEIPASS', '-')})",
        f"Time:     {time.strftime('%Y-%m-%d %H:%M:%S %z')}",
    ]
    try:
        from . import nic
        lines.append("\nNICs:")
        for n in nic.list_nics():
            lines.append(
                f"  {n.name!r}  ip={n.ipv4 or '-'}  mac={n.mac or '-'}  "
                f"up={n.is_up}  loopback={n.is_loopback}"
            )
    except Exception as e:
        lines.append(f"\n[nic.list_nics failed: {e}]")
    return "\n".join(lines) + "\n"


def export_bundle() -> tuple[bool, str, Optional[Path]]:
    """Build the diagnostics zip. Returns (ok, message, path-or-None).

    Always best-effort: a failed component becomes a placeholder file in
    the zip rather than aborting the whole export, so the user always
    gets *something* to send.
    """
    runtime_dir = CONFIG_PATH.parent
    desk = _desktop_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    zip_path = desk / f"NICSwitcher-diagnostics-{stamp}.zip"

    files_to_copy: list[tuple[str, Path]] = []
    if CONFIG_PATH.is_file():
        files_to_copy.append(("config.json", CONFIG_PATH))
    for fname in ("dhcpsrv.log", "dhcpsrv-stderr.log",
                  "dhcpsrv.ini", "crash.log"):
        p = runtime_dir / fname
        if p.is_file():
            files_to_copy.append((fname, p))

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for arcname, path in files_to_copy:
                try:
                    if arcname == "config.json":
                        z.writestr(arcname, _sanitize_config(path.read_text(
                            encoding="utf-8", errors="replace"
                        )))
                    else:
                        z.write(path, arcname)
                except Exception as e:
                    z.writestr(f"{arcname}.error.txt", f"failed to read: {e}")

            z.writestr("system_info.txt", _system_info())
            z.writestr(
                "ipconfig_all.txt",
                _shell(["ipconfig", "/all"], timeout=12),
            )
            z.writestr(
                "route_print.txt",
                _shell(["route", "print", "-4"], timeout=8),
            )
            z.writestr(
                "firewall_rules.txt",
                _shell([
                    "netsh", "advfirewall", "firewall", "show", "rule",
                    "name=all", "verbose",
                ], timeout=15),
            )
            z.writestr(
                "version.txt",
                f"{APP_NAME} {__version__}\n"
                f"Python {sys.version.splitlines()[0]}\n"
                f"Frozen: {bool(getattr(sys, 'frozen', False))}\n"
                f"Runtime dir: {runtime_dir}\n"
                f"Built: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n",
            )
    except OSError as e:
        return False, f"Could not write {zip_path.name}: {e}", None

    size_kb = zip_path.stat().st_size // 1024
    return True, f"Saved {zip_path.name} ({size_kb} KB) to your Desktop", zip_path


def open_log_folder() -> tuple[bool, str]:
    """Open %APPDATA%\\NICSwitcher in Explorer."""
    p = CONFIG_PATH.parent
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(p))   # Windows-native — opens in Explorer
        return True, f"Opened {p}"
    except Exception as e:
        return False, f"Could not open {p}: {e}"


# ---------------------------------------------------------------------------
# Run-at-boot toggle (Windows HKCU\...\Run registry entry)
# ---------------------------------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "NICSwitcher"


def _running_exe_path() -> Optional[str]:
    """Return the absolute path to the launching exe when frozen
    (PyInstaller --onefile bundles set sys.frozen=True and sys.executable
    points at the wrapper exe). Returns None when running from source —
    Run-at-boot only makes sense for the packaged build."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return None


def is_run_at_boot() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            try:
                winreg.QueryValueEx(key, _RUN_VALUE)
                return True
            except FileNotFoundError:
                return False
            except OSError:
                return False
    except OSError:
        return False


def set_run_at_boot(enable: bool) -> tuple[bool, str]:
    """Toggle the HKCU\\...\\Run\\NICSwitcher value pointing at this exe.

    HKCU (current user) — no admin needed, no UAC prompt. Survives
    reboot. Removed cleanly when the user disables it. Has no effect
    when running from source (returns a friendly message).
    """
    try:
        import winreg
    except ImportError:
        return False, "winreg unavailable (Windows-only)"

    exe = _running_exe_path()
    if not exe and enable:
        return False, (
            "Run at boot only works for the packaged build (the .exe). "
            "When running from source there's no fixed launcher path."
        )

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE,
        ) as key:
            if enable:
                # Quote the path so spaces in 'NIC Switcher' don't break
                # the auto-launch.
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, f'"{exe}"')
                return True, f"Run at boot enabled — {exe}"
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                except FileNotFoundError:
                    pass
                return True, "Run at boot disabled"
    except PermissionError:
        return False, "Registry write denied"
    except OSError as e:
        return False, f"Registry error: {e}"
