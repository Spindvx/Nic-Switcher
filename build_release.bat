@echo off
REM Full release build:
REM   1. PyInstaller --onedir (build.bat)
REM   2. Inno Setup installer (packaging\nic-switcher.iss)
REM
REM Requires:
REM   - Python 3.11+ with requirements.txt installed
REM   - PyInstaller (`pip install pyinstaller`)
REM   - Inno Setup 6 (`ISCC.exe` on PATH, or in "C:\Program Files (x86)\Inno Setup 6\")
REM
REM Output: dist\installer\NICSwitcher-Setup-<version>.exe

setlocal

echo === [1/2] Building PyInstaller onedir ===
call "%~dp0build.bat"
if errorlevel 1 (
  echo PyInstaller build FAILED
  exit /b 1
)

echo.
echo === [2/2] Building Inno Setup installer ===
where ISCC.exe >nul 2>nul
if errorlevel 1 (
  set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
) else (
  set "ISCC=ISCC.exe"
)

if not exist "%ISCC%" (
  echo Inno Setup not found. Install from https://jrsoftware.org/isinfo.php
  exit /b 1
)

"%ISCC%" "%~dp0packaging\nic-switcher.iss"
if errorlevel 1 (
  echo Installer build FAILED
  exit /b 1
)

echo.
echo Done. Installer is at dist\installer\NICSwitcher-Setup-*.exe
endlocal
