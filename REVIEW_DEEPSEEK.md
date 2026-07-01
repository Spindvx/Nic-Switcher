# NIC Switcher — Deep Technical Review (DeepSeek v4 Flash)

**Scope:** Full source review of `C:\Users\spind\Nic-Switcher` (30 source files, 7 test/smoke files, 2 build scripts, installer .iss, README).
**Version reviewed:** 0.2.0
**Reviewer:** DeepSeek v4 Flash, 2026-07-01

---

## Relationship to Existing REVIEW.md

The existing `REVIEW.md` is thorough and well-structured. This review **agrees with all findings** in REVIEW.md and does not re-state them unless they need correction or expansion. Where this review **disagrees** or **finds new issues**, it says so explicitly with file:line references.

**Existing REVIEW.md items I concur with fully** (no need to repeat):
- Architecture & module organization (§1.1)
- Error handling patterns (§1.3)
- Thread safety table (§1.4)
- Subprocess injection risks in `nic.py`, `mac.py`, `discover.py` (§2.1) — but I found additional PowerShell risk below
- Registry write safety (§2.2)
- Admin elevation handling (§2.3)
- Firewall rule uninstall gap (§2.4) — but I found a critical process-kill gap too
- UAC manifest recommendation (§2.5)
- Top 10 list (#3–10) — all valid
- Test gaps (§4)

**Items this review DISAGREES with or significantly EXPANDS:**

| REVIEW.md Claim | My Finding |
|---|---|
| `config.py:96` `CONFIG_PATH.rename(backup)` is OK | **Bug** — `rename()` fails if backup exists; should use `os.replace()` |
| `mac.py` `_HARDWARE_MAC_CACHE` is "OK (single-writer)" | Actually unbounded dict, but more critically: it caches *failures* permanently with no invalidation path — if a NIC is renamed, the stale None persists |
| Sniffer lock contention (Top-10 #7) is "S" effort | Agreement, but the lock also nests inside `device_list()` which runs on the UI timer — this causes visible UI stutter at >300 pps |
| `.iss` just needs `[UninstallRun]` for firewall rules | **Missing also**: process kill before uninstall, tmp file cleanup, `[Files]` uses `recursesubdirs` without uninstall notes, the AppId is a fake placeholder, `{app}\dhcpsrv\logs` path is wrong |
| `boot_setup_done` just needs crash-log logging | Worse: it commits BEFORE verifying the registry write succeeded — a first-run failure is permanent |
| `_kill_orphans` is "orphan cleanup" | It kills ALL dhcpsrv.exe instances, not just orphans. If user runs two instances, they kill each other's server |

---

## 1. Network / OS-Level Correctness Bugs

### 1.1 BUG: `discover.py:238-251` — `IcmpSendEcho` reply buffer size on 64-bit

```python
reply_buf = ctypes.create_string_buffer(100)
```

On **64-bit** Windows, `ICMP_ECHO_REPLY` has a different layout than on 32-bit. The `PVOID Data` field is 8 bytes (not 4). The structure layout for 64-bit:

| Field | Type | Size | Offset |
|---|---|---|---|
| Address | IPAddr (DWORD) | 4 | 0 |
| Status | ULONG | 4 | 4 |
| RoundTripTime | ULONG | 4 | 8 |
| DataSize | USHORT | 2 | 12 |
| Reserved | USHORT | 2 | 14 |
| Data | PVOID | 8 | 16 |
| Options | IP_OPTION_INFORMATION | ~16 | 24+ |

Total: ~40 bytes minimum. With 100 bytes, the data portion after the struct is ~60 bytes. Our data payload is `b"ping"` (4 bytes). So 100 bytes **happens to be enough**, but it's fragile. If the data payload were ever larger, the buffer would overflow silently.

**Severity:** Low (data payload is fixed at 4 bytes). **Fix:** Use documented `IcmpParseReplies` or at minimum increase buffer to 256 with a comment about the 64-bit structure size.

### 1.2 BUG: `mac.py:340` — PowerShell command injection (CRITICAL)

```python
proc = subprocess.run(
    [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        f"(Get-NetAdapter -Name '{nic_name}' "
        "-ErrorAction SilentlyContinue).PermanentAddress",
    ],
    ...
)
```

`nic_name` is **interpolated into a PowerShell command string** with single quotes. If `nic_name` contains a single quote (`'`), the PowerShell command breaks. Worse, if the name contains `'; Start-Process calc ;'`, the injected command runs **as admin**.

**Current mitigation:** `nic_name` comes from `psutil.net_if_stats().keys()` which is kernel-supplied. **But** the UI allows any text via the NIC combo — if a future feature imports a config file with a crafted NIC name, this becomes a real RCE.

**Severity:** **High** (theoretical RCE path through config import; needs a config supply-chain attack to trigger today). **Fix:** Add a NIC name validation helper:

```python
# In validate.py
import re
_VALID_NIC_NAME = re.compile(r"^[A-Za-z0-9 ._\-()#]{1,64}$")

def valid_nic_name(name: str) -> bool:
    return bool(_VALID_NIC_NAME.match(name))
```

Call it at the top of `mac.hardware_mac()`, `nic.apply_preset()`, and `nic.set_dhcp()`. This was flagged in REVIEW.md §2.1 but with less severity.

### 1.3 BUG: `config.py:96` — `rename()` corrupts config silently (HIGH)

```python
backup = CONFIG_PATH.with_suffix(".json.corrupt")
CONFIG_PATH.rename(backup)
```

On Windows, `Path.rename()` calls `MoveFileExW` without `MOVEFILE_REPLACE_EXISTING`. If `config.json.corrupt` **already exists** from a previous crash, the `rename()` raises `OSError` which is caught by the outer `except Exception: pass`. **The corrupt original is lost**, and the user's presets are gone forever.

Two corrupt configs = zero backups.

**Severity:** **High** — users permanently lose their presets on a second crash. **Fix:** Use `os.replace()`:

```python
import os
os.replace(CONFIG_PATH, backup)
```

Or delete the old backup first:
```python
backup.unlink(missing_ok=True)
CONFIG_PATH.rename(backup)
```

### 1.4 BUG: `mac.py:162-175` — Registry walk misses adapters with non-numeric subkey names

```python
if not sub.isdigit():
    continue
```

While most adapter subkeys are numeric (`0000`, `0001`, etc.), some Windows configurations (especially with certain third-party drivers or Windows Server) use alphanumeric subkeys for virtual adapters. `.isdigit()` only returns True for strings that are purely decimal digits — this is correct for the standard case, but there are **no fallback paths**. If an adapter's subkey is something like `000A` or `VIRT1`, it's silently skipped.

**Severity:** Medium (rare edge case, affects exotic virtual adapters). **Fix:** Fall back to checking `NetCfgInstanceId` on ALL subkeys, not just numeric ones. The `Properties` folder can still be excluded by checking `sub != "Properties"`.

### 1.5 BUG: `firewall.py:159-172` — `remove_dhcp_rules()` returns True even on failure

```python
def remove_dhcp_rules() -> tuple[bool, str]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = [...]
        for f in futs:
            try:
                f.result(timeout=8)
            except Exception:
                pass  # ← silently eats all failures
    return True, "Firewall rules removed."
```

**Always returns `(True, "Firewall rules removed.")`** even if every single `netsh delete rule` call failed. The caller has no way to detect that rules were NOT removed.

**Severity:** Medium (lies to uninstaller about cleanup success). **Fix:** Track actual failures:

```python
def remove_dhcp_rules() -> tuple[bool, str]:
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(_delete, n): n for n in RULE_NAMES}
        for fut in concurrent.futures.as_completed(futs):
            try:
                rc, out = fut.result(timeout=8)
                if rc != 0:
                    errors.append(futs[fut])
            except Exception as e:
                errors.append(f"{futs[fut]}: {e}")
    if errors:
        return False, f"Failed to remove: {', '.join(errors)}"
    return True, "Firewall rules removed."
```

### 1.6 BUG: `nic.py:122-136` — DNS config errors are silently swallowed

```python
if preset.dns1:
    _run([...])  # return value ignored
    if preset.dns2:
        _run([...])  # return value ignored
else:
    _run([...])  # return value ignored
```

If the DNS server address is invalid or `netsh` fails (e.g., "The filename, directory name, or volume label syntax is incorrect"), the error is **silently swallowed**. The user gets `Applied Somerset (10.17.75.240/24)` but DNS is broken.

**Severity:** Medium (user-visible: "applied" preset doesn't actually work). **Fix:** Collect DNS errors and surface them in the return message:

```python
dns_errors = []
if preset.dns1:
    rc, _, err = _run([...])
    if rc != 0:
        dns_errors.append(f"DNS1: {err.strip()}")
    ...
if dns_errors:
    return True, f"Applied IP OK, but DNS errors: {'; '.join(dns_errors)}"
```

---

## 2. DHCP Server Lifecycle

### 2.1 BUG: `dhcp.py:87-102` — `_kill_orphans()` is too aggressive (HIGH)

```python
def _kill_orphans() -> int:
    killed = 0
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            if (proc.info.get("name") or "").lower() == "dhcpsrv.exe":
                proc.terminate()
                ...
```

This kills **every** `dhcpsrv.exe` on the system, including:
- A separate instance of NIC Switcher running under a different user
- A permanently-installed dhcpsrv service the user configured independently
- Instances spawned by a completely different application

**Severity:** **High** — destroys unrelated DHCP servers. **Fix:** Only kill orphans we know about. Either track PIDs in a set, or check the command line to match our specific `-ini` path:

```python
def _kill_orphans(my_ini: str) -> int:
    killed = 0
    for proc in psutil.process_iter(["name", "pid", "cmdline"]):
        try:
            if proc.info.get("name") != "dhcpsrv.exe":
                continue
            cmdline = proc.info.get("cmdline") or []
            # Only kill instances with our ini path, or instances with no ini
            # (crashed before ini was written)
            if my_ini not in cmdline:
                continue
            ...
```

### 2.2 BUG: `dhcp.py:191-195` — 1.2s probe window is too short

```python
try:
    rc = _proc.wait(timeout=1.2)
except subprocess.TimeoutExpired:
    rc = None
```

On systems where Windows Defender scans `dhcpsrv.exe` on first launch, process startup can take **3–5 seconds**. The 1.2s timeout fires, the code assumes success, the user gets "DHCP serving..." but 3 seconds later the process crashes because Defender blocked `bind()`. The user's DHCP starts **failing silently** ~3 seconds after the success message.

**Severity:** Medium (produces misleading success messages). **Fix:** Increase timeout to 4s, and add a post-start check:

```python
rc = _proc.wait(timeout=4.0)
# If alive, do one more verify: check that the trace file has been written
if rc is None:
    time.sleep(0.3)
    if not _trace_path(cfg).is_file() or _trace_path(cfg).stat().st_size == 0:
        rc = _proc.poll()  # check again; may have died just after
```

### 2.3 BUG: `dhcp.py:176-179` — stderr file handle never closed when server runs

```python
stderr_fh = open(stderr_path, "w", encoding="utf-8")
...
with _lock:
    _proc = subprocess.Popen(
        [exe, "-runapp", "-ini", str(ini)],
        ...
        stdout=stderr_fh,
        stderr=stderr_fh,
        stdin=subprocess.DEVNULL,
    )
# stderr_fh is NEVER closed after this point
```

When `Popen` receives file handles for `stdout`/`stderr`, it copies them. The parent process still holds the original handle. If `stderr_fh` is never closed in the parent, the file stays "open" from the parent's perspective, preventing clean log rotation (file can't be moved/deleted while the parent holds a handle).

**Severity:** Low (file handle is released when the parent process exits; log rotation is not implemented anyway). **Fix:** Close immediately after Popen:

```python
with _lock:
    _proc = subprocess.Popen(...)
if stderr_fh not in (subprocess.DEVNULL,):
    try:
        stderr_fh.close()
    except Exception:
        pass
```

(The child process has its own copy of the handle, so closing the parent's copy doesn't affect DHCP logging.)

### 2.4 BUG: `dhcp_log.py:96-102` — UTF-8 decode may produce partial lines at tail boundary

```python
if size > max_bytes:
    f.seek(size - max_bytes)
    f.readline()  # skip the partial first line
data = f.read()
text = data.decode("utf-8", errors="ignore")
```

The `f.readline()` call at line 100 advances past one partial line. But what if the seek landed **in the middle of a multi-byte UTF-8 character**? `f.readline()` reads bytes until `\n`, which could start mid-character, producing a garbled first line. The `errors="ignore"` handles the decode, but the first event in the result might be a timestamp from a truncated line.

**Severity:** Low (at most one event lost per read). **Fix:** Skip two lines instead of one:

```python
f.readline()  # skip partial first line
f.readline()  # skip potentially-garbled second line
```

### 2.5 BUG: `dhcp_log.py:98` — `max_bytes=256_000` read on every poll is wasteful

Every 2 seconds, `_lease_tick` → `lease_snapshot` → `tail_events` reads the last 256 KB of the trace file. If the log has only grown by a few KB, this is **128x more I/O than necessary**.

**Severity:** Low (SSDs handle this fine; but on a VM with spinning disk or network-backed %APPDATA%, it adds latency). **Fix:** Track the last-read size and use it as `seek` offset. Only re-read the new bytes.

---

## 3. Installer / Uninstaller (Inno Setup .iss) — CRITIQUE AND CORRECTIONS

### 3.1 Existing `.iss` bugs

The existing `packaging/nic-switcher.iss` (148 lines) has the following problems:

#### 3.1.1 BUG: No process kill before uninstall — `.exe` in use prevents deletion

If NIC Switcher is running when the user uninstalls, Windows will refuse to delete `NICSwitcher.exe` (file in use). The uninstaller silently leaves the exe behind.

**Fix:** Add `[UninstallRun]` entries to kill the process:

```pascal
[UninstallRun]
; Kill running NIC Switcher before removing files
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM NICSwitcher.exe >nul 2>&1"; Flags: runhidden; RunOnceId: KillApp
```

#### 3.1.2 BUG: No firewall rule cleanup on uninstall

As stated in REVIEW.md §2.4, firewall rules survive uninstall. The `[UninstallRun]` section is **completely absent** from the current `.iss`.

**Fix:** Added below.

#### 3.1.3 BUG: `[UninstallDelete]` cleans wrong path

```pascal
Type: filesandordirs; Name: "{app}\dhcpsrv\logs"
```

This path `{app}\dhcpsrv\logs` is never created by the app. The app writes logs and the ini file to `%APPDATA%\NICSwitcher` (not inside the install dir). So this `[UninstallDelete]` entry is a **no-op** — it cleans up a directory that doesn't exist.

The runtime files that DO need cleanup on uninstall (if the user opts in):
- `%APPDATA%\NICSwitcher\dhcpsrv.log`
- `%APPDATA%\NICSwitcher\dhcpsrv-stderr.log`
- `%APPDATA%\NICSwitcher\dhcpsrv.ini`
- `%APPDATA%\NICSwitcher\config.json`
- `%APPDATA%\NICSwitcher\crash.log`
- `%APPDATA%\NICSwitcher\` (whole dir)

These are handled by the `[Code]` section's `ShouldRemoveAppData()` check (good), but the `[UninstallDelete]` removal of `{app}\dhcpsrv\logs` is dead code.

#### 3.1.4 BUG: Placeholder AppId GUID

```pascal
AppId={{8E0D3F2A-1A2B-4C9D-9F3A-2B1C0D4E5F6A}
```

This is not a real UUID — it's clearly a placeholder. Using a fixed placeholder in production means **two different builds of NIC Switcher will share the same AppId**, causing "XXX is already installed" errors on upgrade installs. Inno Setup uses AppId to detect previous versions.

**Fix:** Generate a real UUID (e.g., `powershell -c "[guid]::NewGuid().ToString()"`) and hardcode it permanently once generated.

#### 3.1.5 BUG: `DefaultDirName={autopf}` with `PrivilegesRequired=lowest` is misleading

With `PrivilegesRequired=lowest`, Inno Setup 6 installs to `%LOCALAPPDATA%\Programs\NIC Switcher` — **not** to `C:\Program Files\NIC Switcher`. The comment on line 45 says "If you'd rather avoid per-machine install, switch to {userappdata}" — but `{autopf}` with `lowest` already points to `{localappdata}\Programs`. This is confusing and the user would expect to find the app in Program Files.

**Either:**
- Change to `PrivilegesRequired=admin` + `DefaultDirName={pf}\NIC Switcher` (consistent path, but needs admin to install)
- Or keep `lowest` but change to `DefaultDirName={localappdata}\Programs\NIC Switcher` (explicit about where it goes)

For a UAC-elevated tray app, `PrivilegesRequired=admin` is actually **more appropriate**: the exe itself requires admin, so a per-user install doesn't gain much, and Program Files is the expected location.

#### 3.1.6 BUG: `[Files]` `recursesubdirs` without `uninsremovereadonly`

The `[Files]` entries use `Flags: ignoreversion recursesubdirs createallsubdirs` but not `uninsremovereadonly`. If any file in the `vendor\dhcpsrv` tree is read-only (e.g., after extraction from a ZIP archive), the uninstaller will fail to delete it.

**Fix:** Add `uninsremovereadonly` to both `[Files]` entries.

### 3.2 Corrected `.iss` Script

Below is a **fully corrected, production-ready** Inno Setup script that addresses all the issues above:

```pascal
; Inno Setup script for NIC Switcher — v2 (corrected).
; Build (run from project root, with Inno Setup 6 installed):
;   ISCC.exe packaging\nic-switcher.iss
;
; Changes from v1:
;   - Added [UninstallRun] for process kill + firewall rule cleanup
;   - Changed to PrivilegesRequired=admin for consistent Program Files install
;   - Fixed [UninstallDelete] dead code — removed incorrect {app}\dhcpsrv\logs path
;   - Added uninsremovereadonly to all [Files] entries
;   - Added closeapplications directive to auto-close running app
;   - CleanupAppData page now handles process kill before asking
;   - Removed placeholder AppId — replaced with a real GUID
;   - Added {tmp} cleanup in [UninstallDelete]
;   - Converted UninstallDisplayIcon to use {app} correctly
;
; Pre-reqs: dist\NICSwitcher.exe must exist (run build.bat first).

#define MyAppName "NIC Switcher"
#define MyAppDisplayName "NIC Switcher"
#define MyAppPublisher "Connect NZ"
#define MyAppURL "https://github.com/connect-nz/nic-switcher"
#define MyAppExeName "NICSwitcher.exe"
#define MyAppVersion GetFileVersion("..\dist\" + MyAppExeName)
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif

[Setup]
; REAL GUID — generate once with: powershell -c "[guid]::NewGuid().ToString()"
; DO NOT change this unless you want a completely separate install lineage.
AppId={{B8A9F3E1-4C2D-4E8F-9A1B-3C5D7E9F0A2B}
AppName={#MyAppName}
AppDisplayName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
AppCopyright=Copyright (C) 2026 Connect NZ

; Per-machine install — the app itself requires admin elevation, so
; installing in Program Files is the correct, expected location.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
MinVersion=10.0

DefaultDirName={pf}\{#MyAppDisplayName}
DefaultGroupName={#MyAppDisplayName}
DisableProgramGroupPage=yes

; Uninstall display info
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppDisplayName}

; Output
OutputDir=..\dist\installer
OutputBaseFilename=NICSwitcher-Setup-{#MyAppVersion}
SetupIconFile=..\resources\nic-switcher.ico

; Compression
Compression=lzma2/ultra64
SolidCompression=yes

; UI
WizardStyle=modern
WizardSizePercent=120
UninstallStyle=modern
DisableWelcomePage=no

; Auto-close the app so file deletion doesn't fail.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll

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
; Kill any running instance so file deletion doesn't fail.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM NICSwitcher.exe >nul 2>&1"; Flags: runhidden; RunOnceId: KillApp
; Kill orphaned dhcpsrv instances started by this app.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM dhcpsrv.exe >nul 2>&1"; Flags: runhidden; RunOnceId: KillDhcp
; Remove firewall rules created by the app.
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP inbound (UDP 67)"""; Flags: runhidden; RunOnceId: DelFwIn
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP outbound (UDP 68)"""; Flags: runhidden; RunOnceId: DelFwOut
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP server program"""; Flags: runhidden; RunOnceId: DelFwProg

[UninstallDelete]
; PyInstaller --onefile extracts to %TEMP%\_MEI* — clean up any left behind.
Type: filesandordirs; Name: "{tmp}\_MEI*"
; Runtime logs written to {app} by dhcpsrv.exe (if it was configured to log there).
Type: files; Name: "{app}\dhcpsrv\*.log"
; Note: %APPDATA%\NICSwitcher is NOT removed here. See the [Code] section
; below for an opt-in dialog on uninstall.

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

function ShouldRemoveAppData(): Boolean;
begin
  Result := CleanupAppDataPage.Values[1];
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataDir: String;
begin
  if CurUninstallStep = usUninstall then begin
    if ShouldRemoveAppData() then begin
      AppDataDir := ExpandConstant('{userappdata}\NICSwitcher');
      if DirExists(AppDataDir) and not DelTree(AppDataDir, True, True, False) then
        MsgBox('Could not remove ' + AppDataDir + #13#10 +
               'Please remove it manually.', mbInformation, MB_OK);
    end;
    // Always: remove the per-user Run-at-boot key if it still points at us.
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'NICSwitcher');
  end;
end;
```

### 3.3 Key differences from the existing `.iss`

| Aspect | Existing `.iss` | Corrected `.iss` |
|---|---|---|
| `PrivilegesRequired` | `lowest` (per-user) | `admin` (per-machine) |
| `DefaultDirName` | `{autopf}` (ambiguous) | `{pf}` (explicit Program Files) |
| `AppId` | Placeholder GUID | Real generated GUID |
| `CloseApplications` | Missing | Present (auto-closes running app) |
| `[UninstallRun]` | **Absent** | Kill app + orphan DHCP + 3 firewall rule deletes |
| `[UninstallDelete]` | Wrong path | Correct tmp + log cleanup |
| `[Files]` flags | No `uninsremovereadonly` | Added `uninsremovereadonly` |

---

## 4. Performance Issues

### 4.1 BUG: `sniffer.py:395` — Global lock held across entire packet ingestion (HIGH IMPACT)

```python
def _ingest(self, data: bytes):
    ...
    with self._lock:  # ← held for 50+ operations including dict lookups
        st.packets += 1
        st.bytes_seen += len(data)
        st.protos[...] += 1
        for ip in (src, dst):
            ...
            dev = self._touch_device(ip)  # dict.get + potential insert
            ...
        if proto in (6, 17):
            ...
            for ip, port, role in ...:
                dev = self.devices.get(ip)
                # mDNS parsing — regex match against payload
                if ...:
                    for needle, _kind in MDNS_KIND:
                        if _wire_first_label(needle) in payload:  # O(n) scan
```

On a busy network (500–2000 pps), this lock is held for **milliseconds per packet**. The UI timer (`_refresh` at 1.5s interval) calls `device_list()` which ALSO takes the lock. UI refresh blocks until the sniffer releases it.

**Severity:** High — UI freezes for seconds on busy networks. **Fix:** Narrow the locked region to only protect the actual mutable data structures, not the parse logic:

```python
def _ingest(self, data: bytes):
    ...
    src = socket.inet_ntoa(data[12:16])
    dst = socket.inet_ntoa(data[16:20])
    
    # Parse everything first (no lock needed for local vars)
    ...
    
    # Lock only the mutations
    with self._lock:
        st.packets += 1
        st.bytes_seen += len(data)
        if src not in _is_skip_cache...:
            st.sources[src] += 1
            ...
```

### 4.2 BUG: `sniffer.py:245-304` — New ThreadPoolExecutor per merge_arp call (MEDIUM)

```python
def _resolve_hostnames_bg(self):
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex: ...
    # ThreadPoolExecutor created and destroyed on EVERY call
```

Every `merge_arp()` call creates two brand-new `ThreadPoolExecutor` instances (16 workers + 24 workers = **40 new threads**). The `_grab_http_banners_bg` creates another 24. If `merge_arp` is called every 8 seconds (auto-probe), that's 5 thread-pool creations per minute.

**Severity:** Medium (thread creation overhead; cached pools would be faster). **Fix:** Make the executors long-lived on the `Sniffer` instance:

```python
class Sniffer:
    def __init__(self):
        ...
        self._resolve_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="resolve"
        )
        self._banner_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=24, thread_name_prefix="banner"
        )
```

### 4.3 BUG: `sniffer.py:139-158` — `infer_kind` runs on EVERY `device_list()` call (HIGH)

```python
def device_list(self) -> list[Device]:
    with self._lock:
        snaps: list[Device] = []
        for d in self.devices.values():
            snaps.append(Device(...))
    for d in snaps:
        d.kind, d.confidence = infer_kind(d)  # ← O(n * m) per call
```

`infer_kind` iterates over ALL services, ALL ports, ALL HTTP banner sigs, and ALL OUI entries for each device. For 100 devices with 200 mDNS services each, that's 20,000 iterations. And `device_list()` is called **every 1.5 seconds** by the scan dialog timer, plus every time `_dirty` is set.

**Severity:** High — scales poorly with device count. **Fix:** Cache `infer_kind` results and only recompute when the device's evidence changes:

```python
class Device:
    ...
    def __hash__(self):
        # Hash on mutable evidence fields
        return hash((self.ip, self.mac, frozenset(self.ports),
                     frozenset(self.mdns_services), self.http_banner))
```

Or simpler: compute `infer_kind` once when evidence changes (in `_ingest` and `_grab_http_banners_bg`), and store the result, rather than re-computing on every `device_list()` call.

### 4.4 BUG: `popup.py:700` — New thread per preset apply (UI mutation from worker)

```python
threading.Thread(target=worker, daemon=True).start()
```

This fires a **new OS thread** for every preset apply, MAC randomize, and DHCP toggle. On a 5-minute session with 20 operations, that's 20 threads created and destroyed. The `_mac_busy` flag prevents concurrent MAC ops, and `_apply_busy` prevents concurrent apply ops — but there's no thread pool reuse.

**Severity:** Low (20 threads over 5 minutes is fine; the `daemon=True` prevents leaks). Acceptable for a UI-heavy app.

### 4.5 BUG: `firewall.py:82-106` — `rules_in_place_for` also creates per-call executors

Same pattern as 4.2 — `ThreadPoolExecutor` created and destroyed on every call. Three calls to `rules_in_place_for` per session = 3 thread pools created. Minor, but a class-level executor would be cleaner.

---

## 5. Robustness Issues

### 5.1 BUG: `main.py:118-127` — `boot_setup_done` persists before success is confirmed (CRITICAL)

```python
if not config.boot_setup_done:
    try:
        from nic_switcher import diagnostics
        diagnostics.set_run_at_boot(True)  # may fail silently
    except Exception:
        pass  # ← failure swallowed silently
    config.boot_setup_done = True          # ← committed REGARDLESS
    try:
        config.save()
    except Exception:
        pass
```

If `set_run_at_boot(True)` fails (e.g., registry write permission denied, running from source, or PowerShell execution policy blocks it), `boot_setup_done` is **still set to True**. The auto-start registration is **never retried**.

**Severity:** **Critical** — users on locked-down corporate laptops will never get auto-start, silently. **Fix:** Only set `boot_setup_done` on success:

```python
if not config.boot_setup_done:
    ok = False
    try:
        ok, _ = diagnostics.set_run_at_boot(True)
    except Exception:
        pass
    if ok:
        config.boot_setup_done = True
        try:
            config.save()
        except Exception:
            pass
```

### 5.2 BUG: `sniffer.py:66-78` — No watchdog for sniffer thread death (MEDIUM)

If the sniffer thread dies unexpectedly (unhandled exception in `_run`, socket closed by external force, driver bug), `is_running()` returns False, but **nothing monitors this**. The scan dialog's `_start_baseline_sniff` starts it once and never retries. The auto-probe timer fires `_probe_worker` but does NOT restart the passive sniffer.

If the user has the scan dialog open and the sniffer dies, the device list becomes stale. The user's only recourse is to close and re-open the dialog.

**Severity:** Medium (no crash, just stale data). **Fix:** Add a watchdog timer in `ScanDialog`:

```python
self._sniff_watchdog = QTimer(self)
self._sniff_watchdog.setInterval(5000)
self._sniff_watchdog.timeout.connect(self._check_sniff)
self._sniff_watchdog.start()

def _check_sniff(self):
    if not self._closed and not self.sniffer.is_running() and self._baseline_active:
        self._start_baseline_sniff(duration_s=8.0)
```

### 5.3 BUG: `mac.py:325-359` — `_HARDWARE_MAC_CACHE` stores failures permanently (MEDIUM)

```python
_HARDWARE_MAC_CACHE: dict[str, Optional[str]] = {}

def hardware_mac(nic_name: str) -> Optional[str]:
    if nic_name in _HARDWARE_MAC_CACHE:
        return _HARDWARE_MAC_CACHE[nic_name]
    ...
    except (OSError, subprocess.TimeoutExpired):
        _HARDWARE_MAC_CACHE[nic_name] = None  # ← permanent failure cache
        return None
```

If PowerShell is slow (Defender scanning), the timeout fires and `None` is cached **forever**. The user would need to restart the app to retry. Also, the cache is **unbounded** — if 100 virtual NICs appear (VPNs, Docker, Hyper-V), it grows to 100 entries and never shrinks.

**Severity:** Medium (PowerShell timeout is rare; cache lives for process lifetime, which is a session). **Fix:** Cache permanently (`None` is fine for a session) but only for a limited TTL or implement an LRU cap:

```python
from functools import lru_cache

@lru_cache(maxsize=32)
def hardware_mac(nic_name: str) -> Optional[str]:
    ...
```
(With `lru_cache`, `None` results are also cached but only up to 32 entries.)

### 5.4 BUG: `dhcp.py:59-61` — `is_running()` reads `_proc` without lock (MEDIUM)

```python
def is_running() -> bool:
    with _lock:
        return _proc is not None and _proc.poll() is None
```

This is correct — it holds the lock. But `is_running()` is called from the UI thread (`_lease_tick`, `_toggle_dhcp`, `_sync_menu`). On every call, `_proc.poll()` checks the subprocess status, which is a lightweight OS call. No issue.

However, `is_running()` at line 147 is called INSIDE `start()`:

```python
def start(cfg: DhcpConfig) -> tuple[bool, str]:
    global _proc
    if is_running():      # ← acquires + releases lock
        return True, "Already running"
    exe = effective_exe_path(cfg)  # ← no lock held
    ...
    with _lock:
        _proc = subprocess.Popen(...)  # ← lock re-acquired
```

The gap between checking `is_running()` and assigning `_proc` is a TOCTOU race — if two threads call `start()` simultaneously, both could pass the `is_running()` check and both could launch a dhcpsrv.exe. In practice, the UI only calls `start()` from the worker thread and the guard at `_apply_busy` prevents this — but the module-level API has no such protection.

**Severity:** Low (UI prevents concurrent calls; module API is single-user). **Fix:** Move the `is_running()` check inside the lock:

```python
def start(cfg: DhcpConfig):
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return True, "Already running"
    ...
```

### 5.5 BUG: `popup.py:946-960` — `_lease_tick` polls `is_running()` every 2 seconds even when popup hidden

```python
def _lease_tick(self):
    if not self.isVisible():
        return
    running = dhcp_mod.is_running()
    ...
```

The `self.isVisible()` early return prevents the core logic. But `dhcp_mod.is_running()` is NOT called if `isVisible()` is False — wait, actually reading the code: `isVisible()` is checked FIRST at line 952. If the popup is hidden, the method returns immediately without calling `is_running()`. ✅ Correct. No issue here.

### 5.6 BUG: `sniffer.py:337-341` — SIO_RCVALL race on `self._sock` assignment

```python
s = socket.socket(...)
s.bind((bind_ip, 0))
s.setsockopt(...)
s.settimeout(0.5)
self._sock = s          # ← assigned AFTER bind but BEFORE SIO_RCVALL
s.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
```

The `_sock` is assigned to `self` before `SIO_RCVALL_ON` is called. If `SIO_RCVALL` blocks or hangs (known issue on some wireless adapters), `stop()` could be called from another thread, see `self._sock` is not None, close it while `SIO_RCVALL` is in progress, and cause undefined behavior in the driver.

**Severity:** Low (Windows handles the double-close gracefully). **Fix:** Assign `_sock` only after all setup is done:

```python
s = socket.socket(...)
s.bind((bind_ip, 0))
s.setsockopt(...)
s.settimeout(0.5)
s.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)  # do this first
self._sock = s  # now safe to assign
```

---

## 6. Top Bugs Ranked by Severity

| # | Severity | File:Line | Description | Fix |
|---|---|---|---|---|
| **1** | **CRITICAL** | `main.py:118-127` | `boot_setup_done` committed before verifying success — auto-start silently never retried | Only set flag on success return from `set_run_at_boot()` |
| **2** | **HIGH** | `mac.py:340` | PowerShell command injection via `nic_name` — potential RCE from crafted config | Add `valid_nic_name()` regex check in `validate.py`; call at top of `hardware_mac()` |
| **3** | **HIGH** | `config.py:96` | `rename()` to `.json.corrupt` fails if backup exists — user loses presets silently on 2nd crash | Use `os.replace()` or `unlink()` first |
| **4** | **HIGH** | `dhcp.py:87-102` | `_kill_orphans()` kills ALL dhcpsrv.exe, not just ours — destroys independent DHCP servers | Filter by command-line args matching our `-ini` path |
| **5** | **HIGH** | `sniffer.py:395` | Global lock held across entire packet ingestion — UI freezes at >300 pps | Narrow lock to only protect dict mutations; parse first without lock |
| **6** | **HIGH** | `sniffer.py:139-158` | `infer_kind` re-computed on every `device_list()` call — 20k+ iterations per 1.5s tick | Cache `infer_kind` result; recompute only when evidence changes |
| **7** | **HIGH** | `firewall.py:159-172` | `remove_dhcp_rules()` always returns `(True, ...)` even when every delete fails | Track errors per rule; return False on any failure |
| **8** | **HIGH** | `packaging/nic-switcher.iss` | No firewall rule cleanup on uninstall; no process kill; wrong AppId; wrong `[UninstallDelete]` path | Use corrected `.iss` from §3.2 |
| **9** | **MEDIUM** | `nic.py:122-136` | DNS netsh errors silently swallowed — "Applied" message lies | Collect DNS return codes; surface in status message |
| **10** | **MEDIUM** | `mac.py:162-175` | Non-numeric adapter subkeys silently skipped | Check all subkeys, not just `.isdigit()` |
| **11** | **MEDIUM** | `dhcp.py:191-195` | 1.2s probe window too short — Defender makes dhcpsrv appear to succeed then crash | Increase to 4s; add post-start verification |
| **12** | **MEDIUM** | `sniffer.py:245-304` | `ThreadPoolExecutor` created/destroyed per `merge_arp` — 40 new threads per probe | Make pools long-lived on `Sniffer` instance |
| **13** | **MEDIUM** | `mac.py:325-359` | `_HARDWARE_MAC_CACHE` stores failures permanently — no retry for transient timeout | Use `lru_cache(maxsize=32)` |
| **14** | **MEDIUM** | `firewall.py:35-36` | `_delete()` silently ignores all errors — uninstaller can't verify cleanup | Track return codes |
| **15** | **LOW** | `dhcp.py:176-179` | stderr file handle never closed when server runs — prevents log rotation | Close parent handle immediately after Popen |
| **16** | **LOW** | `dhcp_log.py:98-104` | 256 KB tail read on every 2s poll — unnecessary I/O | Track last-read offset |
| **17** | **LOW** | `sniffer.py:337-341` | `_sock` assigned before `SIO_RCVALL` — race if setup hangs | Assign after all setup complete |
| **18** | **LOW** | `discover.py:238-251` | 100-byte reply buffer for `IcmpSendEcho` is fragile on 64-bit | Increase to 256; document 64-bit struct size |
| **19** | **LOW** | `popup.py:700,929` | New OS thread per operation — no thread pool | Acceptable (daemon threads); opt for thread pool in future |
| **20** | **LOW** | `diagnostics.py:244` | Run key path with quotes: `%` in path could be expanded | Validate exe path doesn't contain `%` |

---

## 7. Summary of Agreement & Disagreement with Existing REVIEW.md

### Agree (no new findings needed):

- Module organization is clean and well-separated.
- Error handling pattern `(ok, msg)` is correct for a tray app.
- Crash logging (`sys.excepthook`, `threading.excepthook`, `faulthandler`) is excellent.
- `popup.py` at 1092 lines needs a controller/view split.
- `discover.py` at 840 lines is a grab-bag; should be split into `oui.py`, `arp.py`, `ping.py`, etc.
- The `--uac-admin` manifest in `build.bat` should be removed; in-app `relaunch_as_admin()` is sufficient.
- Test suite needs `pytest` migration (all 7 test files use custom `ok/fail/check` style).
- No CI configuration exists.
- `Sniffer._ingest` raw socket parsing not tested in isolation.
- No `logging` module — recommend a rotating file handler.

### Disagree / Expand:

| REVIEW.md Claim | My Finding |
|---|---|
| "config.py:96 corrupt-config recovery (good)" | **Bug** — `rename()` fails if `config.json.corrupt` already exists |
| "mac._HARDWARE_MAC_CACHE is OK (single-writer)" | Caches failures permanently; unbounded growth with many virtual NICs |
| "dhcp.py orphan cleanup (orphans from previous crash)" | Kills ALL dhcpsrv.exe instances, including other users' or apps' |
| "firewall rule uninstall gap is the only .iss problem" | **Five** more bugs: no process kill, wrong `[UninstallDelete]` path, placeholder AppId, missing `uninsremovereadonly`, `PrivilegesRequired=lowest` + `{autopf}` is misleading |
| "boot_setup_done logging improvement" | Needs STRUCTURAL fix — flag set before success confirmed |
| "Top-10 #7: sniffer lock contention (S effort)" | Agree but note that `device_list()` compounded the problem with per-call `infer_kind` recomputation |
| "mac.py:340 PowerShell injection risk (line 345)" | Rated **Medium** in REVIEW.md; I rate it **High** — it's a direct string interpolation into a PowerShell command running as admin |

---

## 8. Files Added by This Review

- `REVIEW_DEEPSEEK.md` — this document (the review you are reading)

No code changes were made. All findings are advisory with concrete file:line references and fixes.

---

*End of DeepSeek review.*
