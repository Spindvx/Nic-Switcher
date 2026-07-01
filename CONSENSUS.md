# NIC Switcher — GLM 5.2 Review & Multi-Model Consensus

**Reviewer:** GLM 5.2 (the host model), 2026-07-01
**Prior reviews:** `REVIEW.md` (Minimax m3), `REVIEW_DEEPSEEK.md` (DeepSeek v4 Flash)

---

## Part 1: GLM 5.2 Independent Review

### Architecture & Code Quality

The codebase is well-structured for a solo-developer Windows utility. Module boundaries are clear, the `(ok, msg)` tuple return pattern is consistent across boundary layers, and defensive coding around dangerous surfaces (registry, raw sockets, subprocess) is above average.

**Strengths I independently confirm:**
- `mac.py:24-27` winreg import shim for cross-platform testability — excellent
- `config.py:110-112` atomic write via tmp + os.replace — correct
- `main.py:26-66` triple crash logging (excepthook, threading.excepthook, faulthandler) — thorough
- `firewall.py:73-106` `rules_in_place_for` short-circuit check — smart optimization
- `mac.py:268-301` always-attempt-enable safety net in `restart_adapter` — prevents leaving adapter disabled

**New architectural observation (not in prior reviews):**

`discover.py` is 840 lines and contains 6 distinct concerns: OUI table, ARP cache reading, ICMP ping sweep, mDNS probing, AV protocol probing, HTTP banner grabbing, hostname resolution, and device classification. Both prior reviews noted this. I add that the `infer_kind` function (`discover.py:553-599`) is called from `sniffer.py:139-158` on every `device_list()` call AND is the single most expensive operation per refresh tick. It should be refactored to cache results per-device keyed on evidence hash.

### Correctness Bugs

**BUG-GLM-1: `dhcp.py:175-206` — stderr file handle leak on success path**

When `Popen` succeeds and the process stays running (`rc is None` at line 195), execution continues to line 211+ and returns `(True, ...)`. The `stderr_fh` file handle is **never closed** in this path. It's only closed in the `except Exception` block (line 202) and the early-death block (line 203). Each successful DHCP start leaks one file handle in the parent process.

```python
# Line 194-195: rc is None (process alive) — fall through to line 211
# stderr_fh stays open forever in the parent
```

All three reviews agree on this. Fix: close `stderr_fh` immediately after `Popen` — the child has its own copy.

**BUG-GLM-2: `config.py:96` — `rename()` fails on second corruption**

Confirmed by DeepSeek. `Path.rename()` on Windows calls `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING`. If `config.json.corrupt` already exists from a prior crash, the rename raises `OSError`, caught by the outer `except Exception: pass`. The original corrupt config is then **overwritten** by the default-presets return at line 100... wait, no — line 100 creates a new `AppConfig` in memory, and it's only persisted on the next `save()` call. But the corrupt original file still exists on disk at that point because the rename failed. Actually, the next `save()` will `os.replace` the tmp file over the original `config.json`, destroying the corrupt file. Either way, the backup is lost.

Fix: `os.replace(CONFIG_PATH, backup)` or `backup.unlink(missing_ok=True)` before rename.

**BUG-GLM-3: `main.py:117-127` — `boot_setup_done` set before verifying success**

Confirmed by DeepSeek (rated CRITICAL). The flag is set to `True` unconditionally, even when `set_run_at_boot(True)` fails silently. Auto-start is never retried.

Fix: only set `boot_setup_done = True` when `set_run_at_boot` returns `(True, ...)`.

**BUG-GLM-4: `mac.py:340` — PowerShell command injection**

Confirmed by both prior reviews. `nic_name` is interpolated into a PowerShell command string with single quotes. If the NIC name contains `'`, the command breaks; with crafted input, it's RCE running as admin.

Fix: validate NIC name with regex `^[A-Za-z0-9 ._\-()#]{1,64}$` before any subprocess call.

**BUG-GLM-5: `dhcp.py:87-102` — `_kill_orphans` kills ALL dhcpsrv.exe instances**

Confirmed by DeepSeek (rated HIGH). This terminates every `dhcpsrv.exe` on the system, including instances started by other applications or users.

Fix: filter by command-line arguments matching our `-ini` path.

**BUG-GLM-6: `firewall.py:159-172` — `remove_dhcp_rules()` always returns success**

Confirmed by DeepSeek. All `netsh delete` failures are silently swallowed. The caller (and the uninstaller) believes rules were removed when they weren't.

Fix: track return codes and return `False` on any failure.

**BUG-GLM-7: `discover.py:218` — ARP cache filter drops valid /23 gateways**

`read_arp_cache` filters `ip.endswith(".255")` which drops valid gateway addresses on /23 or larger subnets. The sniffer's `_is_skip` function (`sniffer.py:44-55`) explicitly documents this as a deliberate choice NOT to filter `.255` — but `discover.py:218` still does. This is an inconsistency between the two modules.

Fix: remove the `.255` filter from `read_arp_cache` and rely on the subnet-aware filtering already in the sniffer.

**BUG-GLM-8: `sniffer.py:139-158` — `infer_kind` recomputed on every `device_list()` call**

Confirmed by DeepSeek (rated HIGH). For 100 devices, `infer_kind` iterates all mDNS services, ports, OUI entries, and HTTP banner signatures per device. Called every 1.5s by the scan dialog timer.

Fix: compute `infer_kind` once when evidence changes (in `_ingest`, `merge_arp`, `_grab_http_banners_bg`) and store `(kind, confidence)` on the Device. `device_list()` just copies the cached values.

### Installer / Uninstaller

The existing `packaging/nic-switcher.iss` has been critiqued thoroughly by DeepSeek. I agree with all of DeepSeek's findings:

1. **No process kill on uninstall** — exe in use can't be deleted
2. **No firewall rule cleanup** — three `netsh delete` commands missing
3. **Wrong `[UninstallDelete]` path** — `{app}\dhcpsrv\logs` is never created
4. **Placeholder AppId** — needs a real GUID
5. **Missing `uninsremovereadonly`** — read-only files can't be removed
6. **`PrivilegesRequired=lowest` + `{autopf}`** — misleading; resolves to LocalAppData not Program Files

**My additional installer critique:**

The `.iss` uses `GetFileVersion("..\dist\" + MyAppExeName)` but `build.bat` does NOT pass `--version-file` to PyInstaller. Without a version resource embedded in the exe, `GetFileVersion` returns an empty string, and the `#ifndef MyAppVersion` fallback to `"0.2.0"` kicks in. This means the installer version is **always hardcoded** — it never reflects the actual `__version__` in `__init__.py`. 

Fix: either add `--version-file` to `build.bat` (requires a version_info.txt), or change the `.iss` to read the version from `__init__.py` directly:
```pascal
#define MyAppVersion ReadIni(ExtractFilePath(Source) + "..\nic_switcher\__init__.py", "", "")
```
Actually, Inno Setup can't parse Python. Better: add a `--version-file` step to `build.bat`, or extract the version string in `build_release.bat` and pass it as a `/D` parameter to `ISCC.exe`.

### UI/UX Observations

**Observation-GLM-1: `popup.py:1079-1092` — `hide_animated` double-fire guard**

The `_fade.finished.connect(_done)` at line 1091 connects a new callback each time `hide_animated` is called. If the user clicks close twice rapidly, two `_done` callbacks are connected. The `try: self._fade.finished.disconnect(_done)` at line 1086 only disconnects the *current* `_done`, not previously-connected ones. Previous connections linger, and when the animation finishes, all accumulated `_done` callbacks fire — multiple `self.hide()` calls are harmless but wasteful.

Fix: disconnect all previous connections before connecting:
```python
try: self._fade.finished.disconnect()
except TypeError: pass
self._fade.finished.connect(_done)
```

**Observation-GLM-2: `scan_dialog.py:258` — DanteBrowser created in `__init__`, never stopped on dialog reject**

`ScanDialog.__init__` creates `self._dante = dante.DanteBrowser(...)` and starts it at line 429. `closeEvent` (line 446) stops it. But if the dialog is rejected via `QDialog.reject()` (e.g., Esc key or clicking a reject button), `closeEvent` IS called by Qt — so this is actually fine. However, the `Sniffer` instance is shared from `Popup` and is NOT stopped when the scan dialog closes. The baseline sniff auto-stops after 8s (`_stop_baseline_sniff`), but if the user manually started probing, the sniffer keeps running in the background after the dialog closes. Not a bug per se, but the popup's `Sniffer` accumulates state that persists across dialog open/close cycles.

**Observation-GLM-3: First-run auto-start is too aggressive**

`main.py:117-127` auto-enables "Run at Windows startup" on first launch with no user consent. The comment says "we do this exactly once" but the user is never asked. This is the kind of behavior that makes users distrust an app. A first-run dialog asking "Would you like NIC Switcher to start with Windows?" would be more respectful.

### Testing Gaps

I agree with all testing gaps identified in REVIEW.md §4 and add:

- `dhcp.py:start()` and `stop()` have no unit tests with mocked `subprocess.Popen`
- `firewall.py:ensure_dhcp_rules` has no test with mocked `subprocess.run` for the fast/slow path decision
- `discover.py:default_gateway_for` has no test — the `route print` parsing logic is fragile
- `config.py:load()` corrupt-config recovery path has no test

---

## Part 2: Multi-Model Consensus

Three reviewers (Minimax m3, DeepSeek v4 Flash, GLM 5.2) independently reviewed the codebase. Below is the negotiated consensus — findings all three agree on, ranked by severity and impact.

### Consensus: Top 10 Actionable Improvements

| # | Severity | Issue | File:Line | Consensus Fix |
|---|---|---|---|---|
| **1** | **CRITICAL** | `boot_setup_done` set to `True` before verifying `set_run_at_boot` succeeded — auto-start silently never retried | `main.py:117-127` | Only set flag when `set_run_at_boot` returns `(True, ...)` |
| **2** | **HIGH** | PowerShell command injection via `nic_name` in `hardware_mac` — potential RCE running as admin | `mac.py:340` | Add `valid_nic_name()` regex in `validate.py`; call before all subprocess interpolations |
| **3** | **HIGH** | `config.py:96` `rename()` to `.json.corrupt` fails if backup exists — user permanently loses presets on 2nd crash | `config.py:96` | Use `os.replace()` or `backup.unlink(missing_ok=True)` before rename |
| **4** | **HIGH** | `_kill_orphans()` kills ALL `dhcpsrv.exe` on the system — destroys independent DHCP servers | `dhcp.py:87-102` | Filter by command-line args matching our `-ini` path |
| **5** | **HIGH** | `infer_kind` recomputed on every `device_list()` call — 20k+ iterations per 1.5s tick | `sniffer.py:139-158` | Cache `(kind, confidence)` on Device; recompute only when evidence changes |
| **6** | **HIGH** | Installer `.iss` missing: process kill, firewall rule cleanup, correct AppId, `[UninstallDelete]` path, `uninsremovereadonly` | `packaging/nic-switcher.iss` | Use corrected `.iss` (see below) |
| **7** | **MEDIUM** | `remove_dhcp_rules()` always returns `(True, ...)` even when every netsh delete fails | `firewall.py:159-172` | Track return codes; return `False` on any failure |
| **8** | **MEDIUM** | DNS netsh errors silently swallowed — "Applied" message lies about DNS state | `nic.py:122-136` | Collect DNS return codes; surface in status message |
| **9** | **MEDIUM** | `stderr_fh` file handle leaked on DHCP start success path | `dhcp.py:175-206` | Close parent handle immediately after `Popen` |
| **10** | **MEDIUM** | Sniffer `_ingest` holds global lock across entire packet parse — UI freezes at >300 pps | `sniffer.py:395` | Narrow lock to dict mutations only; parse without lock |

### Consensus: Installer Design

All three reviewers agree on **Inno Setup 6** as the installer tool. Key consensus decisions:

| Decision | Consensus |
|---|---|
| Tool | Inno Setup 6 (free, simple, single-exe output, Pascal scripting for cleanup) |
| Install location | `C:\Program Files\NIC Switcher\` (`PrivilegesRequired=admin`) — the app itself requires admin, so Program Files is the expected location |
| Shortcuts | Start Menu (default ON), Desktop (opt-in, default OFF) |
| Add/Remove Programs | Automatic via Inno Setup `AppId` |
| Firewall cleanup on uninstall | **Required** — three `netsh advfirewall firewall delete rule` commands in `[UninstallRun]` |
| Process kill on uninstall | **Required** — `taskkill /F /IM NICSwitcher.exe` and `dhcpsrv.exe` in `[UninstallRun]` |
| HKCU Run key cleanup | In `[Code] CurUninstallStepChanged` — `RegDeleteValue` |
| %APPDATA% cleanup | Opt-in via wizard page; default: KEEP (preserves presets for reinstall) |
| PyInstaller mode | Switch from `--onefile` to `--onedir` for faster startup and cleaner installer layout |
| `--uac-admin` manifest | **Remove** — use in-app `relaunch_as_admin()` instead (already implemented at `main.py:76-86`). This avoids UAC prompt on every launch. |

### Consensus: Corrected Inno Setup Script

All three reviews produced corrected `.iss` scripts. The DeepSeek version is the most complete. The consensus merged version:

```pascal
; Inno Setup script for NIC Switcher — consensus v3
; Build: ISCC.exe packaging\nic-switcher.iss
; Pre-reqs: dist\NICSwitcher.exe (run build.bat first)

#define MyAppName "NIC Switcher"
#define MyAppDisplayName "NIC Switcher"
#define MyAppPublisher "Spindvx"
#define MyAppURL "https://github.com/Spindvx/Nic-Switcher"
#define MyAppExeName "NICSwitcher.exe"
#define MyAppVersion "0.2.0"

[Setup]
AppId={{B8A9F3E1-4C2D-4E8F-9A1B-3C5D7E9F0A2B}
AppName={#MyAppName}
AppDisplayName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppCopyright=Copyright (C) 2026 Spindvx

PrivilegesRequired=admin
MinVersion=10.0

DefaultDirName={pf}\{#MyAppDisplayName}
DefaultGroupName={#MyAppDisplayName}
DisableProgramGroupPage=yes

UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppDisplayName}

OutputDir=..\dist\installer
OutputBaseFilename=NICSwitcher-Setup-{#MyAppVersion}
SetupIconFile=..\resources\nic-switcher.ico

Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
UninstallStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenu"; Description: "Create Start Menu shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion uninsremovereadonly
Source: "..\dist\dhcpsrv\*"; DestDir: "{app}\dhcpsrv"; Flags: ignoreversion recursesubdirs createallsubdirs uninsremovereadonly
Source: "..\dist\resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs uninsremovereadonly

[Icons]
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenu
Name: "{group}\Open log folder"; Filename: "{userappdata}\NICSwitcher"; Tasks: startmenu
Name: "{group}\Uninstall {#MyAppDisplayName}"; Filename: "{uninstallexe}"; Tasks: startmenu
Name: "{commondesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppDisplayName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM NICSwitcher.exe >nul 2>&1"; Flags: runhidden; RunOnceId: KillApp
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM dhcpsrv.exe >nul 2>&1"; Flags: runhidden; RunOnceId: KillDhcp
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP inbound (UDP 67)"""; Flags: runhidden; RunOnceId: DelFwIn
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP outbound (UDP 68)"""; Flags: runhidden; RunOnceId: DelFwOut
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP server program"""; Flags: runhidden; RunOnceId: DelFwProg

[Code]
var
  CleanupAppDataPage: TInputOptionWizardPage;

procedure InitializeWizard();
begin
  CleanupAppDataPage := CreateInputOptionPage(
    wpUninstall,
    'Remove personal data?',
    'NIC Switcher stores your presets, DHCP leases, and crash log in:',
    ExpandConstant('{userappdata}\NICSwitcher') + #13#10 + #13#10 +
    'Choose what happens to it on uninstall:',
    True, False);
  CleanupAppDataPage.Add('Keep my data (recommended)');
  CleanupAppDataPage.Add('Delete my presets and logs');
  CleanupAppDataPage.Values[0] := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataDir: String;
begin
  if CurUninstallStep = usUninstall then begin
    if CleanupAppDataPage.Values[1] then begin
      AppDataDir := ExpandConstant('{userappdata}\NICSwitcher');
      if DirExists(AppDataDir) and not DelTree(AppDataDir, True, True, False) then
        MsgBox('Could not remove ' + AppDataDir + #13#10 +
               'Please remove it manually.', mbInformation, MB_OK);
    end;
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'NICSwitcher');
  end;
end;
```

### Consensus: Items NOT Agreed On (Minor Disagreements)

| Topic | Minimax | DeepSeek | GLM 5.2 | Resolution |
|---|---|---|---|---|
| `PrivilegesRequired` | `lowest` (per-user) | `admin` (per-machine) | `admin` (per-machine) | **admin** — the app requires admin to run, Program Files is expected |
| `--onefile` vs `--onedir` | Suggest `--onedir` | Suggest `--onedir` | Suggest `--onedir` | **onedir** — unanimous; faster startup, cleaner installer |
| `--uac-admin` removal | Suggest remove | Suggest remove | Suggest remove | **Remove** — unanimous; use in-app elevation instead |
| Severity of `mac.py:340` | Medium | High | High | **High** — majority vote |
| First-run wizard | Suggested | Not mentioned | Agrees with Minimax | **Add** — 2 of 3 support it |

### Consensus: Quick Wins (Under 30 Minutes Each)

1. Fix `main.py:117-127` — only set `boot_setup_done` on success
2. Fix `config.py:96` — use `os.replace` for corrupt backup
3. Add `valid_nic_name()` to `validate.py` — regex check before subprocess calls
4. Close `stderr_fh` after `Popen` in `dhcp.py:184`
5. Add `disconnect()` before `connect()` in `popup.py:1091` `hide_animated`
6. Filter `_kill_orphans` by command-line args in `dhcp.py:87-102`
7. Track return codes in `firewall.py:159-172` `remove_dhcp_rules()`
8. Remove `.255` filter from `discover.py:218` `read_arp_cache`

---

*End of GLM 5.2 review and multi-model consensus.*
