; Inno Setup script for the Windows build.
;
;   iscc /DVERSION=0.1.0 /DARCH=AMD64 /DOUTDIR=<abs path> packaging\windows\installer.iss
;
; PrivilegesRequired=lowest: a per-user install under %LOCALAPPDATA%, no UAC
; prompt, no admin password — which is what the installer promises the user.

#ifndef VERSION
  #define VERSION "0.0.0"
#endif
#ifndef ARCH
  #define ARCH "x64"
#endif
#ifndef OUTDIR
  #define OUTDIR "dist"
#endif
#ifndef SRCDIR
  #define SRCDIR "dist\Almathus"
#endif
#ifndef ICONFILE
  #define ICONFILE "assets\icon.ico"
#endif

[Setup]
AppId={{8E4C1B73-6F2A-4D2E-9E4B-6C1F0A9D3B57}
AppName=Almathus
AppVersion={#VERSION}
AppPublisher=Datarip
AppPublisherURL=https://github.com/datarip/almathus
AppSupportURL=https://github.com/datarip/almathus/issues
DefaultDirName={autopf}\Almathus
DefaultGroupName=Almathus
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OUTDIR}
OutputBaseFilename=Almathus-{#VERSION}-windows-{#ARCH}
SetupIconFile={#ICONFILE}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\Almathus.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SRCDIR}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Almathus"; Filename: "{app}\Almathus.exe"
Name: "{autodesktop}\Almathus"; Filename: "{app}\Almathus.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Almathus.exe"; Description: "{cm:LaunchProgram,Almathus}"; Flags: nowait postinstall skipifsilent
