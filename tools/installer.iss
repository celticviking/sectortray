; SectorTray installer (Inno Setup 6). Built by tools/build_dist.py from the portable bundle:
;   ISCC /DAppVersion=0.5.0 /DSourceDir=<out\dist\SectorTray> /DOutputDir=<out\dist> tools\installer.iss
; Per-user, no admin rights: installs to %LOCALAPPDATA%\Programs\SectorTray (where the tester prompt used to unzip it,
; so installing over a hand-unzipped copy just upgrades it). Saved drawers in Documents\SectorTray are never touched.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<the bundle folder>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef Mutex
  #define Mutex "Local\SectorTray.App"
#endif

[Setup]
AppId={{8F3C2B71-5D4E-4A8B-9C6F-2E7D1A0B3C45}
AppName=SectorTray
AppVersion={#AppVersion}
AppVerName=SectorTray {#AppVersion}
AppPublisher=celticviking
AppComments=Offline Gridfinity drawer planner
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\SectorTray
DisableProgramGroupPage=yes
; always the per-user default folder: setup clears its app/python folders on upgrade, so never let it
; point at a folder that has other things in it
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=SectorTray-{#AppVersion}-setup
SetupIconFile=..\host\SectorTray.ico
UninstallDisplayIcon={app}\SectorTray.exe
UninstallDisplayName=SectorTray
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; the desktop window's single-instance mutex: setup and uninstall ask you to close SectorTray first
AppMutex={#Mutex}

[Messages]
ConfirmUninstall=Remove %1 from this PC?%n%nYour saved drawers and exported files in Documents\SectorTray are kept.

[Tasks]
Name: desktopicon; Description: "Put a SectorTray shortcut on the desktop"

[InstallDelete]
; an upgrade starts from clean program folders, so files an older version had don't linger
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\runtimes"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\SectorTray"; Filename: "{app}\SectorTray.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\SectorTray"; Filename: "{app}\SectorTray.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\SectorTray.exe"; Description: "Start SectorTray"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Python writes bytecode caches next to the app while it runs
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"
