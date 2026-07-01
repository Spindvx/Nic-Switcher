@echo off
REM Build NIC Switcher as a onedir Windows app with bundled dhcpsrv + resources.
REM
REM No --uac-admin manifest: the app self-elevates via relaunch_as_admin() in
REM main.py, so the user gets a UAC prompt only when admin work is actually
REM needed — not on every launch of the tray app.
REM
REM Requires: python -m pip install -r requirements.txt pyinstaller

rmdir /s /q build dist 2>nul

python -m PyInstaller ^
  --noconfirm ^
  --onedir ^
  --windowed ^
  --name "NICSwitcher" ^
  --version-file "packaging\version_info.txt" ^
  --icon "resources/nic-switcher.ico" ^
  --hidden-import PyQt6.sip ^
  --add-data "vendor/dhcpsrv;dhcpsrv" ^
  --add-data "resources;resources" ^
  main.py

echo.
echo Built: dist\NICSwitcher\NICSwitcher.exe
