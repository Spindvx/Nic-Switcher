# NIC Switcher — Code Review & Improvement Plan

**Scope:** Full review of `C:\Users\spind\Nic-Switcher` (16 source files, ~133 KB,
plus tests, build scripts, and vendor dhcpsrv).
**Version reviewed:** 0.2.0 (`nic_switcher/__init__.py:2`).
**Reviewer:** AI subagent, 2026-07-01.

---

## TL;DR

The code is in good shape — clean module boundaries, well-named functions,
defensive error handling around the dangerous surfaces (registry, raw sockets,
subprocess, firewall), and a real test suite that includes fuzz, UAT, and
live-system smoke. The biggest gaps are: (1) **no proper Windows installer**,
just a hand-launched `.exe`; (2) some Windows-specific `ctypes` and
`subprocess` calls pass user-controllable values into argument strings that
should be tighter; (3) the test suite has no real test-runner integration
(unittest/pytest) — it's all custom `check()` printers; (4) a few small
threading and resource-leak cleanups. The Top 10 list at the end ranks the
highest-ROI items.

---

## 1. Architecture & Code Quality

### 1.1 Module organization

Strong, conventional layout:

| Module | Lines | Responsibility |
|---|---|---|
| `main.py` | 174 | Admin elevation, crash logging, app bootstrap |
| `config.py` | 119 | `AppConfig` / `Preset` / `DhcpConfig` dataclasses + JSON persistence |
| `validate.py` | 114 | IP / mask / preset / DHCP-range validation (pure) |
| `nic.py` | 159 | NIC enumeration, netsh IP apply |
| `mac.py` | 412 | MAC validation, registry write, adapter restart |
| `dhcp.py` | 282 | dhcpsrv.ini generation, process spawn/stop, orphan cleanup |
| `dhcp_log.py` | 162 | Pure log parser |
| `firewall.py` | 179 | `netsh advfirewall` rule install/remove with parallelization |
| `diagnostics.py` | 255 | Export zip, log folder, run-at-boot registry |
| `discover.py` | 840 | OUI DB, ARP, ping sweep, mDNS, AV probes, default gateway |
| `sniffer.py` | 508 | Raw socket sniffer (SIO_RCVALL), device table |
| `dante.py` | 182 | zeroconf-based Dante browser |
| `tray.py` | 171 | QSystemTrayIcon + context menu |
| `popup.py` | 1092 | Frameless, acrylic main popup UI |
| `dialogs.py` | 33 KB | All modal dialogs (preset editor, DHCP config, About) |
| `scan_dialog.py` | 27 KB | Network scan results window |
| `theme.py`, `icons.py`, `icon.py`, `blur.py` | ~28 KB | Visual chrome |

**Observation:** `popup.py` is **44 KB / 1092 lines** doing UI composition,
NIC apply, MAC apply, DHCP toggle, and DHCP lease polling. The action
handlers (`_apply_in_background`, `_apply_manual`, `_save_manual_as_preset`,
`_toggle_dhcp`, `_configure_dhcp`, `_on_dhcp_done`, `_lease_tick`,
`_refresh_dhcp_ui`) belong in a separate controller. This is a refactor for
a quiet afternoon, not a blocker.

**Observation:** `dialogs.py` (33 KB) and `scan_dialog.py` (27 KB) are not
reviewed line-by-line here — that would need a second pass. Suggest the same
"controller vs. view" split.

### 1.2 Separation of concerns

- ✅ Pure helpers (`validate.py`, `dhcp_log.py`, OUI lookups, MAC
  validation/normalization) are cleanly separate from I/O.
- ✅ `mac.py` is importable on non-Windows (`winreg = None` shim at line 24–27)
  so unit tests run cross-platform — good defensive design.
- ⚠️ `discover.py` is a 840-line grab-bag of "everything that touches the
  network." A natural split is: `oui.py` (OUI table + lookup), `arp.py`
  (Win32 ARP table), `ping.py` (IcmpSendEcho + sweep), `mdns.py` (probes +
  wire parser), `av_probes.py` (Q-SYS/Shure/Crestron UDP probes),
  `http_banner.py` (port-80 fingerprint). Today's single file is workable
  but will become painful as more vendors are added.
- ⚠️ `popup.py` controller logic (apply/save/toggle) should be a separate
  `popup_controller.py`. The view should not be constructing threads
  (`popup.py:901`, `popup.py:929`).

### 1.3 Error handling patterns

The codebase uses a **consistent, pragmatic style**: return `(ok, msg)`
tuples from the boundary layer, raise (or `except Exception: pass`) deep
inside background workers. This is the right call for a tray app that
must never crash visibly. A few specific callouts:

- **Crash logging** (`main.py:26–66`) installs `sys.excepthook`,
  `threading.excepthook`, and `faulthandler` to a single file. Excellent
  — the kind of telemetry a desktop app needs.
- ⚠️ `main.py:122` and `main.py:126` swallow **all** exceptions from
  `diagnostics.set_run_at_boot(True)` and `config.save()`. If either fails
  the user just gets a silent "run at boot" that's not really set. At
  minimum log to `crash.log`.
- ⚠️ `popup.py` worker threads (`popup.py:894-901`, `popup.py:922-929`) wrap
  `dhcp_mod.start`/`stop` in a `try/except` that only writes the message
  back via `dhcp_done` — but the **outer** `try/except` is fine. The
  concern is that `dhcp_done.emit()` may fire after the popup is destroyed
  if the user quits mid-start; that would raise but is also caught by
  PyQt. Not a real bug, but a `_safe_emit()` helper would tidy it.

### 1.4 Thread safety

| Hot spot | What | Verdict |
|---|---|---|
| `dhcp._proc` | module-global `Popen` handle, `threading.Lock` | ✅ Good |
| `Sniffer._lock` | around `devices`, `stats`, `sources`, `subnets`, `protos` | ✅ Good |
| `Sniffer._ingest` (line 395) | takes lock for **every packet** | ⚠️ Hot path — see 1.4.1 |
| `DanteBrowser._lock` | protects `devices` dict | ✅ Good |
| `AppConfig.save` | writes `%TEMP%/config.json.tmp` then `os.replace` | ✅ Atomic |
| `mac._HARDWARE_MAC_CACHE` | dict, never written concurrently | ✅ OK (single-writer) |
| `_ensure_firewall_bg` thread (`main.py:168`) | daemon, no shared state | ✅ OK |

**1.4.1** `Sniffer._ingest` (sniffer.py:382–455) takes the lock on every
single packet received. At 1000+ pps on a busy network that's measurable
contention. The cheap fix: protect only the dict mutations
(`devices[ip] = ...`, `ports.add(...)`, `sources[ip] += 1`, counters), and
let the periodic `_emit()` snapshot take the lock briefly. Better still,
use a `collections.Counter` and `dict` without a lock and only synchronize
on the snapshot boundary — the device map is a "best effort, eventually
consistent" view.

**1.4.2** `Sniffer._resolve_hostnames_bg` and `_grab_http_banners_bg`
(`sniffer.py:245–304`) fire fresh `ThreadPoolExecutor`s on every call
(once per `merge_arp`). On a busy scan that creates many short-lived
threads. Consider a single long-lived executor owned by the Sniffer.

### 1.5 Logging

There is no real `logging` module use — only `print` in tests and
`crash.log` for unhandled exceptions. Recommend a `nic_switcher.log` in
`%APPDATA%\NICSwitcher` rotating at 1 MB, used by `dhcp.py`, `firewall.py`,
and the sniffer for diagnostic breadcrumbs (rule install attempts, sniffer
start/stop with bind IP, scan timing). The `diagnostics.export_bundle`
already collects this kind of file — make sure to include it.

---

## 2. Security

The app runs **elevated** (`main.py:96–98` re-launches via `ShellExecuteW`
"runas" if not admin). This is a meaningful attack surface: any code path
that takes user input and feeds it to a privileged subprocess, registry
write, or firewall rule needs careful review.

### 2.1 Subprocess injection

`grep` for `shell=True` in `nic_switcher/`: **none found**. Good — the app
always uses argv form. However, there are argument-form injection risks:

**`nic.py:113–148`** — `f"name={nic_name}"` is concatenated into a netsh
argv. The NIC name comes from `psutil.net_if_addrs().keys()` (line 41)
which is **kernel-supplied** on Windows and effectively trustworthy — NIC
display names can include spaces and special chars but Windows rejects the
truly dangerous ones at creation time. **Low risk**, but the `nic_name`
should still be validated/regex-checked to contain only printable chars
before being passed to `netsh`. A malicious profile import or a future
config-driven NIC override could otherwise pass arbitrary text in.

**`mac.py:340–345`** — `f"(Get-NetAdapter -Name '{nic_name}' ...)"`
**inlines `nic_name` into a PowerShell command string**. The NIC name
comes from the sniffer/UI which can be influenced by attacker-controlled
data (e.g. a NIC added by a guest with a name like `' ; Start-Process
calc ; '` — extremely unlikely on a single-user AV laptop, but the
PowerShell string-substitution makes this a *real* argument-injection if
the input ever becomes less trusted. Cache the result (already done) AND
strip the value: re.match(r"^[A-Za-z0-9 ._\-()]{1,64}$", nic_name) before
interpolation.

**`discover.py:749`** — `b"Host: %s\r\n" % ip.encode()` — this is a
Python `%` on bytes, not shell injection, but **the IP** comes from
attacker-shaped network traffic (ping sweep replies, ARP). `bytes %
str_ip` with a non-IPv4 string would raise. Wrap in `try/except
(binascii.Error, UnicodeEncodeError)` and return None on failure.

**`dhcp.py:184`** — `subprocess.Popen([exe, "-runapp", "-ini", str(ini)],
cwd=...)`. The `exe` is either `cfg.exe_path` (user-configurable in
`DhcpConfig`, default empty) or `bundled_dhcpsrv_path()`. If a user
points `cfg.exe_path` at a malicious binary, the app will run it as
**admin** silently. Consider:
1. Validating that the user-configured `exe_path` resolves to a
   `dhcpsrv.exe` (or at least to a file whose name ends with
   `dhcpsrv.exe`).
2. Adding a one-line warning in the Configure dialog when the path is
   overridden: "Running this binary as administrator can be dangerous. Use
   only the bundled dhcpsrv.exe unless you know what you're doing."

### 2.2 Registry write safety

`mac.py` and `diagnostics.py` write two registry values:

| Location | Key | Value | Risk |
|---|---|---|---|
| `mac._write_reg_mac` (line 195) | `HKLM\…\Class\{4D36E972-…}\<nnnn>\NetworkAddress` | 12 hex chars (validated) | ✅ Good — input is `normalize_mac()`d to 12 hex |
| `diagnostics.set_run_at_boot` (line 244) | `HKCU\…\Run\NICSwitcher` | `"\"<path-to-sys.executable>\""` | ✅ Good — path is from `sys.executable`, quoted |

**Suggestion:** `diagnostics.set_run_at_boot` is silent on success of the
disable branch (returns "Run at boot disabled" even when no value
existed). Add a check that the value is actually absent afterwards
(rare but possible if another tool recreates it).

### 2.3 Admin elevation

`main.py:76–86` `relaunch_as_admin` uses `ShellExecuteW(... "runas" ...)`
with `subprocess.list2cmdline` to escape argv. This is correct and safe.

**Concern:** `main.py:97` — if `relaunch_as_admin()` returns `False` (user
clicked Cancel on UAC, or `ShellExecuteW` failed), the code **continues
to run unelevated** and shows the warning at line 130. That warning is
fine but the **background firewall-ensure thread** at `main.py:168` will
silently fail on every netsh call. Consider detecting non-admin at boot
and **exiting cleanly** (or entering a "view-only" mode) instead of
pretending to work and failing opaquely.

The `--no-elevate` escape hatch (line 96) is documented nowhere in the
README. Add a note.

### 2.4 Firewall rule handling

`firewall.py` uses `netsh advfirewall firewall` (locale-stable, no
PowerShell). The rules are:

- `NIC Switcher — DHCP inbound (UDP 67)` — inbound allow UDP/67
- `NIC Switcher — DHCP outbound (UDP 68)` — outbound allow UDP/68
- `NIC Switcher — DHCP server program` — inbound allow program

**Risk:** `RULE_DHCP_PROG` references the program by **full path**. If
the user later moves the exe (re-install to a different folder), the
old rule points at a non-existent path — silently no-op, but dhcpsrv
won't be allowed. `rules_in_place_for` (line 73–106) already handles
this with the `if exe_path and exe_path.lower() not in out_prog.lower():
return False` check. ✅ Good defensive design.

**Risk:** No removal path on uninstall. If the user uninstalls NIC
Switcher, the firewall rules survive (because they are
machine-scoped and the installer doesn't know to remove them). The
[UninstallDelete] section of the proposed Inno Setup script
(`packaging\nic-switcher.iss`) should add:

```pascal
[UninstallRun]
; Remove the firewall rules this app created.
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP inbound (UDP 67)"""; Flags: runhidden
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP outbound (UDP 68)"""; Flags: runhidden
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP server program"""; Flags: runhidden
```

**Risk:** `RULE_DHCP_IN/OUT` allow UDP/67 and UDP/68 on **all profiles**
(`private,domain,public`). This is necessary for the AV use case but
expands the attack surface. Add a comment to the constant definitions
and consider an opt-in in the installer ("Install on public networks
too?" → check the rule scope).

### 2.5 UAC manifest

`build.bat:10` passes `--uac-admin` to PyInstaller, which embeds
`<requestedExecutionLevel level="requireAdministrator"/>` in the exe
manifest. This means **every** launch of the exe prompts UAC — including
right-click on the tray icon (which doesn't need admin). This is
annoying. **Better:** leave the manifest at `asInvoker` and rely on the
in-app `relaunch_as_admin()` (`main.py:76–86`) to prompt UAC only on
the first run per session. The crash logger and config-loader don't
need admin; the NIC/MAC changes do. Trade-off: UAC fires on **every
preset apply** instead of every launch. Most users will perceive that as
a smaller cost (and Windows remembers the elevation for ~5 minutes if
you check the right box).

### 2.6 Diagnostics zip

`diagnostics.export_bundle` (`diagnostics.py:106–168`) bundles the
config, logs, and ipconfig/route/firewall output into a Desktop zip. The
**sanitize** function at line 45–65 redacts a small list of
sensitive-looking keys (`password`, `psk`, `secret`, `api_key`, `token`,
`wifi_password`). It's an allowlist-by-keyword approach — fine for
defense in depth. **Caveat:** the OUI DB and the device list (which can
include MAC addresses of other devices on the user's network) is **not**
included in the bundle, which is the right call for a privacy-conscious
user. Worth a comment in the bundle README so support engineers know.

---

## 3. Installer / Uninstaller

### 3.1 Current state

`build.bat` is a 18-line PyInstaller wrapper. It produces a single
`dist\NICSwitcher.exe` (~40–60 MB with PyQt6). There is **no installer**:
no Start Menu entry, no Add/Remove Programs registration, no desktop
shortcut, no uninstaller, no admin prompts, no first-run experience.
Users currently:
- Copy the exe somewhere
- Manually create a shortcut
- The app self-elevates via `--uac-admin` manifest on every launch

That works for a power-user dev tool, but it does not ship.

### 3.2 Recommended approach: **Inno Setup 6**

Three realistic options for Windows in 2026:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Inno Setup** | Free, 25-year track record, NSIS-like scripting, single `.exe` output, excellent Windows 10/11 support, easy to script per-machine install, easy custom uninstall code | `.exe`, not `.msi` (corporate MSI deployment tools won't pick it up) | **Best fit** for a single-app, low-volume tool |
| **NSIS** | Equally scriptable, more painful to learn, smaller install footprint | Two-license model (some commercial plugins cost $) | Equivalent; pick on familiarity |
| **WiX** (MSI) | The only "real" MSI; required for some enterprise GPO / Intune deploy paths | Steep learning curve, WiX 4 still has rough edges in 2026, longer build pipeline | Overkill unless you need MSI for GPO push |

**Verdict: Inno Setup 6.** It produces a single self-extracting `.exe`,
supports per-user installs (no UAC to install), supports a modern
wizard UI, and the [Code] section lets us add the opt-in
"wipe %APPDATA% on uninstall" page. The output is not an MSI, so
corporate-software-center deployment needs a wrapper .intunewin, but for
the AV-tech-on-a-laptop target audience, an `.exe` is fine.

### 3.3 Proposed installer artifacts (already written)

Two new files have been added to the project:

- **`packaging\nic-switcher.iss`** — Inno Setup 6 script. Per-user install
  to `{autopf}\NIC Switcher`, Start Menu + opt-in Desktop shortcut,
  Add/Remove Programs entry via `AppId`, optional opt-in to remove
  `%APPDATA%\NICSwitcher` on uninstall, automatic removal of the
  HKCU Run key the app may have set, and (recommended addition below)
  firewall rule cleanup on uninstall.
- **`build_release.bat`** — two-stage wrapper: runs `build.bat`, then
  `ISCC.exe packaging\nic-switcher.iss`. Output:
  `dist\installer\NICSwitcher-Setup-<version>.exe`.

### 3.4 Requirements mapping (per the user's checklist)

| Requirement | How it is met |
|---|---|
| Installs the exe to Program Files | `DefaultDirName={autopf}\NIC Switcher` (line 29 of `.iss`); `{autopf}` resolves to `C:\Program Files\` for per-machine, `C:\Users\<u>\AppData\Local\Programs\` for per-user. With `PrivilegesRequired=lowest` Inno writes to the latter. Switch `PrivilegesRequired=admin` + `DefaultDirName={pf}\NIC Switcher` to enforce per-machine. |
| Start Menu shortcut | `[Icons]` + `Name: "{group}\…"` |
| Desktop shortcut | `[Tasks] desktopicon` opt-in checkbox |
| Add/Remove Programs registration | Automatic with `[Setup] AppId`; Inno writes the `UninstallString` and the display info for `arp.exe` / `Settings → Apps` |
| Bundles `vendor/dhcpsrv` | `[Files] Source: "..\dist\dhcpsrv\*"; DestDir: "{app}\dhcpsrv"; Flags: recursesubdirs createallsubdirs`. **Important:** the path is `dist\dhcpsrv\`, not the source `vendor\dhcpsrv\` — that's the directory PyInstaller lays down at build time (from `--add-data "vendor/dhcpsrv;dhcpsrv"` in `build.bat:13`). |
| UAC/admin handled gracefully | Two paths: (a) install itself is per-user so no UAC to install; (b) the installed exe keeps its UAC manifest, so the *first NIC change* per session still triggers UAC. Acceptable. |
| Clean uninstall of config | `[Code]` block in `.iss` adds an extra wizard page on uninstall asking "Keep my data (recommended) / Delete my presets and logs" with a one-click `DelTree` of `%APPDATA%\NICSwitcher`. |
| Firewall rule cleanup on uninstall | Recommended addition to `[UninstallRun]` (see 2.4) |
| Run-at-boot key cleanup on uninstall | Handled in `[Code] CurUninstallStepChanged` — `RegDeleteValue` of `HKCU\…\Run\NICSwitcher` |

### 3.5 Suggested `.iss` improvements (not yet in the file)

1. **Add firewall rule cleanup to `[UninstallRun]`.** The current
   `.iss` only cleans up files; firewall rules are machine-scoped and
   won't be removed automatically. Add three `Filename: "{cmd}"; …`
   lines, one per rule.
2. **Add `[Messages]` custom strings** for the "Run as administrator"
   reminder, the data-wipe page, etc.
3. **Add a license file prompt** — `[Setup] LicenseFile=…` shows an
   EULA page. Today there's no LICENSE file in the repo.
4. **Code-sign the installer.** Without a signature Windows SmartScreen
   warns on first launch. The setup script is unsigned-friendly (no
   `SignTool=` directive) but the resulting install will hit
   SmartScreen. If the user has a code-signing cert, add `SignTool=…`
   in the `[Setup]` section.
5. **Set the installer's `AppCopyright`** and `AppContact` for clean
   Add/Remove Programs display.

### 3.6 PyInstaller tweaks to pair with the installer

`build.bat` is fine but two tweaks make the installer story cleaner:

- **Switch to `--onedir` for the installer payload.** `--onefile` extracts
  to `%TEMP%` on every launch (~3s delay on Windows Defender machines).
  For an installed app, `--onedir` is dramatically faster at launch.
  Trade-off: many files in the install dir instead of one.
- **Add `--version-file`** so `GetFileVersion` works inside Inno Setup
  (the `#define MyAppVersion GetFileVersion(...)` line in the `.iss`
  depends on this being present in the resource table).

---

## 4. Testing gaps

The project has **seven** test artifacts in the project root:

| File | Purpose | Style | Net (admin?) |
|---|---|---|---|
| `test_mac.py` | 543 lines — MAC normalize/validate, registry walk mocked, apply pipeline | Custom `check()` printer | No |
| `test_prod.py` | 7 KB — mask/prefix round-trip, DHCP validate, IP validators | Custom `check()` printer | No |
| `test_ini_format.py` | 3 KB — `dhcpsrv.ini` formatting regression | Custom `check()` printer | No |
| `smoke_test_mac.py` | 7 KB — **live** MAC write/restore on a real NIC, refuses default-route NIC | Custom `check()` printer | **Yes** |
| `uat.py` | 15 KB — full E2E: imports, helpers, OUI, NICs, ARP, gateway, DHCP start/stop, mDNS, Dante, headless QApplication | Custom `check()` printer | Yes (for DHCP) |
| `sandbox_test.py` | 21 KB — fuzz layer, QTest UI, DHCP integration, concurrency stress, memory leak | Custom `check()` printer | Partial |
| `scan_test.py` | 14 KB — focused on the scan dialog (synthetic device fixture) | Custom `check()` printer | No (mostly) |

**Observation:** all tests use the same custom `ok/fail/check` style and
return `1` if `fails` is non-empty. They are not `unittest.TestCase` and
**cannot be discovered by `pytest` or VS Code's test runner.** This is the
biggest test infrastructure gap.

### 4.1 What's well covered

- MAC validation/normalization/randomization (pure) — `test_mac.py`
- Mask ↔ prefix round-trip — `test_prod.py`
- IP validators — `test_prod.py`
- `apply_preset` ordering, `set_dhcp` — `test_mac.py` (mocked)
- Registry walk (`_adapter_guid_for_name`, `find_adapter_registry_key`) — `test_mac.py`
- Fuzz layer against every parser — `sandbox_test.py` (huge value)
- Scan dialog rendering, sort order, filter, repaint — `scan_test.py`
- `infer_kind` AV classification — `scan_test.py`
- Live DHCP start/stop + log parser — `uat.py`

### 4.2 Gaps and recommendations

1. **Adopt `pytest` as the runner.** Convert one file (suggest
   `test_prod.py`) as a model: each `section(...)` becomes a
   `class TestMask: def test_xxx(self): …`. Then add a `pyproject.toml`
   or `pytest.ini` with `testpaths = .` and `addopts = -v`. All seven
   files can be converted incrementally — they already group into
   `section` blocks which map cleanly to `class`/`def` test
   organization. CI runs `pytest -q` for free.

2. **No coverage measurement.** Add `pytest-cov` and a target of ≥80%
   line coverage for `validate.py`, `dhcp_log.py`, `config.py`,
   `mac.py` (the pure parts), and `firewall.py`. These modules are
   heavily testable in isolation.

3. **No CI configuration.** There is no `.github/workflows/`, no
   `.gitlab-ci.yml`, no `azure-pipelines.yml`. Add a GitHub Actions
   workflow that runs `pytest -q` on `windows-latest` for every PR.

4. **`Sniffer._run` raw socket parsing (`sniffer.py:382–455`)** is
   not tested. Build a test that feeds a sequence of crafted
   `bytes` (IPv4 + TCP/UDP headers, mDNS payloads with compression
   pointers, IP fragments, malformed/truncated packets) and asserts
   the device table is updated as expected. Reference: the fuzzer in
   `sandbox_test.py` covers the wrong layer.

5. **`discover.http_banner` (`discover.py:739–785`)** is the highest-
   signal piece of the AV-fingerprinting pipeline. Test with a
   canned HTTP response (Server header, HTML title, meta generator)
   using `socket.socket` mocked out.

6. **`diagnostics._sanitize_config` (`diagnostics.py:45–65`)** has no
   tests. Add a test for each sensitive key, nested dicts, lists,
   and a config that fails to parse.

7. **`firewall.ensure_dhcp_rules` `rules_in_place_for`**
   (`firewall.py:73–106`)** has no unit test. Mock `subprocess.run`
   to return canned `netsh show rule` output and assert the
   short-circuit path is taken.

8. **`Sniffer.merge_arp` (sniffer.py:210–243)** fires off two
   background threads (`_resolve_hostnames_bg`,
   `_grab_http_banners_bg`). Add a test that asserts they don't
   mutate after the Sniffer is `stop()`d (today they will, since
   the futures are not cancelled on stop).

9. **Dante `DanteBrowser` (`dante.py:62–182`)** has no test. The
   public `devices()` snapshot is easy to test by injecting a fake
   `ServiceBrowser`/handler into a mocked `_zc`.

10. **The Crash logger (`main.py:26–66`)** is the only "if this
    breaks the user is blind" path. Add a test that mounts a
    `sys.excepthook`-fired exception and asserts the line lands in
    the crash file.

11. **`nic.py:111–138` `apply_preset`** — the actual `netsh` call is
    not tested in isolation. Mock `_run` and assert the argv
    shape for static IP, gateway, two DNS, and "DHCP" branches.

---

## 5. Top 10 actionable improvements (ranked by impact)

| # | Improvement | Why | Effort | Where |
|---|---|---|---|---|
| **1** | **Add an Inno Setup installer** with per-user install, Start Menu + Desktop shortcuts, Add/Remove Programs registration, optional `DelTree` of `%APPDATA%\NICSwitcher` on uninstall, firewall rule cleanup on uninstall, and HKCU Run-key cleanup. | The app currently has no install/uninstall story. This unblocks distribution to anyone other than the dev. | M | `packaging\nic-switcher.iss` (written), `build_release.bat` (written), `build.bat` (small `--onedir` + `--version-file` tweak) |
| **2** | **Move from `--uac-admin` manifest to in-app `relaunch_as_admin()`** (already implemented at `main.py:76–86`). | The current manifest makes **every launch** of the exe prompt UAC, even when the user is just right-clicking the tray icon. Switching to in-app elevation prompts UAC only when admin work is actually about to happen. | S | `build.bat:10` (remove `--uac-admin`); document the change in `README.md` |
| **3** | **Adopt `pytest` for the test suite.** | The custom `ok/fail/check` style is unreadable in CI, IDE, and on GitHub. `pytest` gives free test discovery, parametrization, fixtures, and coverage. Convert one file as a model. | M (L for all seven) | `test_prod.py` first; add `pyproject.toml` with `[tool.pytest.ini_options]` |
| **4** | **Add a GitHub Actions (or equivalent) CI workflow** that runs `pytest -q` on `windows-latest` and uploads the result + coverage XML. | Right now nothing enforces that `main` is green. With seven test files and no CI, regressions slip in silently. | S | `.github/workflows/ci.yml` |
| **5** | **Sanitize user-controllable strings before passing them to `netsh`/`powershell`.** | Argument-form injection is a real (low-probability) risk in `mac.py:340` (PowerShell `Get-NetAdapter -Name '…'`), `nic.py:113-148` (netsh `name=…`), and `discover.py:749` (HTTP `Host:`). Add a `valid_nic_name` regex check. | S | New helper in `validate.py`; call at the top of `nic.apply_preset`, `mac.hardware_mac`, `discover.http_banner` |
| **6** | **Validate `DhcpConfig.exe_path` before launching.** | `dhcp.py:184` will `Popen` *any* file the user configured in the Configure dialog, **as administrator**. Add a name-extension check (`dhcpsrv.exe`) and a one-line dialog warning. | S | `dhcp.py:effective_exe_path`; add warning in `dialogs.py` DHCP config |
| **7** | **Sniffer: don't take the global lock on every packet.** | `sniffer.py:382–455` calls `with self._lock` for every received packet. On busy networks this is measurable contention. Lock only the dict mutations; counters are `Counter` and naturally atomic for `+=`. | S | `sniffer.py:_ingest` |
| **8** | **Add `[UninstallRun]` firewall rule cleanup to the installer.** | Without it, uninstalling leaves three `netsh advfirewall` rules behind that point at a non-existent program. Harmless but messy, and a security-review red flag. | XS | `packaging\nic-switcher.iss` `[UninstallRun]` section (snippet in §2.4 above) |
| **9** | **Add a real `logging` module + rotating file handler.** | The app has `crash.log` for unhandled exceptions but no per-module breadcrumbs. When something goes wrong on a customer's box, "what did the sniffer see?" or "did the firewall rule install?" are the first questions. Wire `dhcp.py`, `firewall.py`, `sniffer.py` to a `%APPDATA%\NICSwitcher\nic-switcher.log` rotating at 1 MB. | M | New `nic_switcher/log.py` (~30 lines); wire in modules |
| **10** | **Split `popup.py` (1092 lines) into a view + controller.** | Single Responsibility: `popup.py` constructs the UI, applies presets, runs DHCP start/stop, polls leases, and handles thread signals. Extract `_apply_in_background`, `_apply_manual`, `_save_manual_as_preset`, `_toggle_dhcp`, `_configure_dhcp`, `_on_dhcp_done`, `_lease_tick`, `_refresh_dhcp_ui` into a `popup_controller.py`. The view shrinks by ~40% and the controller is unit-testable. | L | `nic_switcher\popup.py`, new `nic_switcher\popup_controller.py` |

### Quick-wins under 30 minutes each (bonus)

- `main.py:122, 126` — log swallowed exceptions to `crash.log` so the
  user isn't blind to a first-run registration failure.
- `mac.py:340` — strip `nic_name` to `^[A-Za-z0-9 ._\-()]{1,64}$`
  before PowerShell interpolation.
- `discover.py:749` — wrap `b"Host: %s\r\n" % ip.encode()` in try/except.
- `dhcp.py:start` (line 143) — add a one-line startup log entry so the
  crash.log shows "DHCP start" if the user reports it never began.
- `popup.py:901, 929` — call `dhcp_done.disconnect()` on
  `aboutToQuit` to avoid a late signal hitting a destroyed popup
  (defensive; PyQt swallows it, but it's untidy).

---

## 6. Files added by this review

These have been written to the project as part of the review:

- `C:\Users\spind\Nic-Switcher\packaging\nic-switcher.iss` — Inno Setup 6
  installer script. Per-user install to `{autopf}\NIC Switcher`, Start
  Menu + opt-in Desktop shortcut, Add/Remove Programs entry, opt-in
  `%APPDATA%\NICSwitcher` removal on uninstall, automatic HKCU Run-key
  cleanup on uninstall. **To use:** install Inno Setup 6 from
  <https://jrsoftware.org/isinfo.php>, then run `ISCC.exe
  packaging\nic-switcher.iss` (the `build_release.bat` wrapper does this
  for you after running `build.bat`).

- `C:\Users\spind\Nic-Switcher\build_release.bat` — two-stage release
  build: `build.bat` → `ISCC.exe`. Output:
  `dist\installer\NICSwitcher-Setup-<version>.exe`.

## 7. Files referenced (key spots)

- `main.py:23, 26–66` — crash logging (good), swallowed exceptions at L122, L126
- `main.py:76–86` — `relaunch_as_admin` (good), `--no-elevate` undocumented
- `main.py:96` — `--uac-admin` manifest via `build.bat:10` (re-evaluate, see Top-10 #2)
- `build.bat:13` — `--add-data "vendor/dhcpsrv;dhcpsrv"` — path the installer must use
- `config.py:78–89` — forward-compat field filtering (good)
- `config.py:96` — corrupt-config recovery (good)
- `nic.py:111–138` — `apply_preset` netsh argv (validation recommended)
- `mac.py:225, 340` — `subprocess.run` on `netsh` and `powershell` (PowerShell interpolation risk)
- `mac.py:325–359` — hardware MAC cache (good)
- `mac.py:269–301` — adapter restart with retry + always-enable safety net (excellent)
- `dhcp.py:30, 182–223` — module-global proc + thread-safe start (good)
- `dhcp.py:184` — `Popen` of user-configured exe (validate, see Top-10 #6)
- `firewall.py:73–106` — `rules_in_place_for` short-circuit (excellent)
- `firewall.py:18–20` — rule names used by uninstaller cleanup
- `discover.py:195–221` — Win32 ARP via ctypes (good; no test)
- `discover.py:326–359` — `av_probe` UDP broadcast (good; binds to bind_ip)
- `discover.py:739–785` — `http_banner` (no test; argument-form risk at L749)
- `sniffer.py:99–116` — `stop()` always closes the socket (good)
- `sniffer.py:382–455` — `_ingest` hot path (lock contention, see Top-10 #7)
- `sniffer.py:245–304` — per-merge `ThreadPoolExecutor` (long-lived executor recommended)
- `tray.py:147–160` — run-at-boot toggle reverts on failure (good)
- `dante.py:62–182` — `DanteBrowser` (no test, but well-isolated)
- `diagnostics.py:45–65` — config sanitization (no test)
- `diagnostics.py:218–255` — `set_run_at_boot` (good; verify on disable branch)

---

*End of review.*
