# Build & Install Notes

Session: 2026-07-02 — built a real Windows installer end-to-end on a fresh
Windows 11 box that had no Python and no dev tooling.

## What was produced

- `dist\NICSwitcher\NICSwitcher.exe` + `_internal\` — PyInstaller `--onedir` bundle.
- `dist\installer\NICSwitcher-Setup-0.2.0.0.exe` — Inno Setup installer (27 MB,
  signed-ready, modern wizard, Add/Remove Programs entry, uninstall cleanup).

## Steps that worked

1. **Install Python** — `winget install --id Python.Python.3.12 --scope user`.
   Resolved the Microsoft Store `python.exe` stub shadowing the real install by
   using the full path (`C:\Users\JoshC\AppData\Local\Programs\Python\Python312\python.exe`)
   in this session; long-term, either disable the App Execution Aliases in
   Settings → Apps → Advanced, or move the user PATH entry above the
   WindowsApps one.
2. **Create venv** in the project root: `python -m venv .venv`.
3. **Install deps**: `.venv\Scripts\python -m pip install -r requirements.txt`
   (PyQt6 6.11.0, psutil 7.2.2, zeroconf 0.150.0).
4. **Install PyInstaller**: `.venv\Scripts\python -m pip install pyinstaller`.
5. **Build bundle** (equivalent to `build.bat`, expanded for clarity):
   ```powershell
   .venv\Scripts\python -m PyInstaller `
     --noconfirm --onedir --windowed `
     --name "NICSwitcher" `
     --version-file "packaging\version_info.txt" `
     --icon "resources/nic-switcher.ico" `
     --hidden-import PyQt6.sip `
     --add-data "vendor/dhcpsrv;dhcpsrv" `
     --add-data "resources;resources" `
     main.py
   ```
6. **Install Inno Setup**: `winget install --id JRSoftware.InnoSetup --scope machine`.
7. **Compile installer**: `"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\nic-switcher.iss`.
8. **Verify**: `Get-Item dist\installer\*.exe`.

## Fixes applied to `packaging\nic-switcher.iss`

The shipped script didn't compile cleanly against modern Inno Setup 6.7. Three
changes were needed:

- `GetFileVersion(...)` → `GetVersionNumbersString(...)` (renamed built-in).
- Removed `AppDisplayName={...}` line — not a valid `[Setup]` directive; the
  display name is driven by `AppName` alone.
- Removed `Flags: checked` from the `startmenu` `[Tasks]` entry — not a valid
  task flag; tasks default to checked.
- Removed the `wpUninstall` `CreateInputOptionPage` block. `wpUninstall` is
  only valid in uninstall-wizard hooks, not as a parent page ID for
  `CreateInputOptionPage`. Tradeoff: the installer no longer prompts "delete
  %APPDATA%\NICSwitcher on uninstall?" — it always keeps it (which is the
  README's recommended default anyway). Re-add this properly by creating the
  option page at install time, persisting the choice to the registry, and
  reading it back in `CurUninstallStepChanged`.

Inno Setup also emitted warnings (not errors) about `UninstallStyle` being
obsolete, `pf` → `commonpf` rename, and a per-user area / admin install note.
None block install/uninstall behavior.

## Things still TODO (not done this session)

- **DHCP server runtime dep**: at runtime the app expects
  `C:\dhcpsrv\dhcpsrv.exe`. The repo vendors a copy at `vendor\dhcpsrv\`, so
  either copy that to `C:\dhcpsrv\` post-install, or change
  `nic_switcher/dhcp.py:dhcpsrv_path()` to look under the bundled install dir
  (`{app}\dhcpsrv\dhcpsrv.exe` when installed via Setup) so the app is
  truly self-contained.
- **Code signing**: the exe and installer are unsigned → SmartScreen warnings
  on first run. Acquire an Authenticode cert and sign `NICSwitcher.exe` and
  `NICSwitcher-Setup-*.exe` before distribution.
- **Pushing the build**: no remote configured in this checkout. Add a remote
  (`git remote add origin <url>`) and push when ready.

## Re-building from scratch

```powershell
# from project root, fresh shell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python -m PyInstaller --noconfirm --onedir --windowed `
    --name "NICSwitcher" `
    --version-file "packaging\version_info.txt" `
    --icon "resources/nic-switcher.ico" `
    --hidden-import PyQt6.sip `
    --add-data "vendor/dhcpsrv;dhcpsrv" `
    --add-data "resources;resources" `
    main.py
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\nic-switcher.iss
# -> dist\installer\NICSwitcher-Setup-0.2.0.0.exe
```

Or just run `build_release.bat` (the project's own wrapper, not exercised
this session).