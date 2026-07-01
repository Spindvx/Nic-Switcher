; Inno Setup script for NIC Switcher (v3 — consensus, onedir layout).
;
; Build (run from project root, with Inno Setup 6 installed):
;     ISCC.exe packaging\nic-switcher.iss
; Or, via the build_release.bat wrapper (recommended).
;
; Output: dist\installer\NICSwitcher-Setup-<version>.exe
;
; Features:
;   - Per-machine install to C:\Program Files\NIC Switcher\
;   - Start Menu + opt-in Desktop shortcut
;   - Add/Remove Programs entry
;   - Kills running NICSwitcher.exe + dhcpsrv.exe before uninstall
;   - Removes 3 netsh firewall rules on uninstall
;   - HKCU Run key cleanup on uninstall
;   - Opt-in %APPDATA%\NICSwitcher wipe on uninstall (default: KEEP)
;
; Pre-reqs: dist\NICSwitcher\ (onedir build from build.bat)

#define MyAppName "NIC Switcher"
#define MyAppDisplayName "NIC Switcher"
#define MyAppPublisher "Spindvx"
#define MyAppURL "https://github.com/Spindvx/Nic-Switcher"
#define MyAppExeName "NICSwitcher.exe"
#define MyAppVersion GetVersionNumbersString("..\dist\NICSwitcher\" + MyAppExeName)
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif

[Setup]
; Real GUID — keep stable across releases for upgrade detection.
AppId={{B8A9F3E1-4C2D-4E8F-9A1B-3C5D7E9F0A2B}
AppName={#MyAppDisplayName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppDisplayName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppCopyright=Copyright (C) 2026 {#MyAppPublisher}

; Per-machine — the app requires admin to run (NIC/MAC/DHCP ops).
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

; Auto-close running instances so file deletion doesn't fail.
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startmenu"; Description: "Create Start Menu shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; PyInstaller --onedir layout: dist\NICSwitcher\ contains the exe + _internal\
Source: "..\dist\NICSwitcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs uninsremovereadonly

[Icons]
Name: "{group}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenu
Name: "{group}\Open log folder"; Filename: "{userappdata}\NICSwitcher"; Tasks: startmenu
Name: "{group}\Uninstall {#MyAppDisplayName}"; Filename: "{uninstallexe}"; Tasks: startmenu
Name: "{commondesktop}\{#MyAppDisplayName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppDisplayName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill running instances so files can be deleted.
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM NICSwitcher.exe /T & exit 0"; Flags: runhidden; RunOnceId: KillApp
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM dhcpsrv.exe /T & exit 0"; Flags: runhidden; RunOnceId: KillDhcp
; Remove the three firewall rules the app created.
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP inbound (UDP 67)"" & exit 0"; Flags: runhidden; RunOnceId: DelFwIn
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP outbound (UDP 68)"" & exit 0"; Flags: runhidden; RunOnceId: DelFwOut
Filename: "{cmd}"; Parameters: "/C netsh advfirewall firewall delete rule name=""NIC Switcher — DHCP server program"" & exit 0"; Flags: runhidden; RunOnceId: DelFwProg

[UninstallDelete]
; Clean up PyInstaller --onefile temp extraction dirs if any are left behind.
Type: filesandordirs; Name: "{tmp}\_MEI*"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then begin
    RegDeleteValue(HKEY_CURRENT_USER,
      'Software\Microsoft\Windows\CurrentVersion\Run', 'NICSwitcher');
  end;
end;
