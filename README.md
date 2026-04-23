# NIC Switcher

A discreet Windows tray app for switching NIC IP presets and running a DHCP server on demand.

## Features

- **Tray icon** — click to open a translucent, acrylic-blurred popup anchored to the tray
- **Presets** — save named IP configs (e.g. *Somerset → 10.17.75.240/24*) and apply with one click
- **NIC picker** — choose which interface the presets act on; selection is remembered
- **DHCP toggle** — one-click start/stop of a DHCP server with a configurable range
- **Modern UI** — frameless, rounded, Windows 11 acrylic/Mica, Fluent-inspired

## Requirements

- Windows 10 / 11
- Python 3.11+ (for development) or the packaged `.exe`
- **[DHCP Server for Windows](https://www.dhcpserver.de/cms/)** by Kirby (unzip to e.g. `C:\dhcpsrv`)
- Admin rights (auto-elevated via UAC)

## Install (dev)

```powershell
cd "NIC Switcher"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Build (standalone .exe)

```powershell
pip install pyinstaller
build.bat
```

The resulting `dist\NIC Switcher.exe` self-elevates via a UAC manifest.

## First run

1. Download dhcpsrv from <https://www.dhcpserver.de/cms/>, unzip to `C:\dhcpsrv`.
2. Launch NIC Switcher → tray icon appears → click it.
3. Pick your interface, add/edit presets, click **Apply**.
4. Click **Configure…** under *DHCP Server* to set bind IP, range, mask, etc., then **Start DHCP**.

## Config location

`%APPDATA%\NICSwitcher\config.json`

## Notes

- IP changes are applied via `netsh interface ip set address`.
- DHCP is run by spawning `dhcpsrv.exe -runStdAlone` with a generated `dhcpsrv.ini`.
- Leaving an IP blank on a preset makes it a "switch back to DHCP client" preset.
