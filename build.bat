@echo off
REM Build NIC Switcher as a single-file Windows exe with bundled dhcpsrv + resources.
REM Requires: python -m pip install -r requirements.txt pyinstaller

python -m PyInstaller ^
  --noconfirm ^
  --onefile ^
  --windowed ^
  --name "NICSwitcher" ^
  --uac-admin ^
  --hidden-import PyQt6.sip ^
  --add-data "vendor/dhcpsrv;dhcpsrv" ^
  --add-data "resources;resources" ^
  main.py

echo.
echo Built: dist\NICSwitcher.exe
