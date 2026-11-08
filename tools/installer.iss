; Inno Setup script for MDI Downloader. Built by tools/build.py:
;   iscc /DAppVersion=1.1.0 tools/installer.iss
; Per-user install: no admin prompt, goes to %LOCALAPPDATA%\Programs.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{75D49D71-B39A-4779-90B1-AA272E982BAD}
AppName=MDI Downloader
AppVersion={#AppVersion}
AppVerName=MDI Downloader {#AppVersion}
AppPublisher=MDI Downloader
AppPublisherURL=https://github.com/irfan-khan-96/mdi
DefaultDirName={autopf}\MDI Downloader
DefaultGroupName=MDI Downloader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=MDI-Setup-{#AppVersion}
SetupIconFile=..\mdi\assets\icon.ico
UninstallDisplayIcon={app}\MDI.exe
UninstallDisplayName=MDI Downloader
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Closes a running MDI before upgrading or uninstalling.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\MDI\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
; AppUserModelID matches the app's own, so a pinned taskbar icon, the
; running window and its notifications are all treated as one app.
Name: "{autoprograms}\MDI Downloader"; Filename: "{app}\MDI.exe"; AppUserModelID: "MDI.Downloader"
Name: "{autodesktop}\MDI Downloader"; Filename: "{app}\MDI.exe"; AppUserModelID: "MDI.Downloader"; Tasks: desktopicon

[Registry]
; Name shown on notifications (the app also writes this; removed on uninstall).
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\MDI.Downloader"; ValueType: string; ValueName: "DisplayName"; ValueData: "MDI Downloader"; Flags: uninsdeletekey

[Run]
Filename: "{app}\MDI.exe"; Description: "Start MDI Downloader"; Flags: nowait postinstall skipifsilent

; Your download list and settings (%APPDATA%\MDI) are kept on uninstall,
; like most apps do, so reinstalling picks up where you left off.
