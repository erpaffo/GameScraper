; Installer Windows (Inno Setup 6) della cartella creata da PyInstaller (dist\PozzoLauncher).
; Uso:  iscc /DAppVersion=0.5.1 packaging\installer.iss      -> release\PozzoLauncher-0.5.1-windows-x64-setup.exe
;
; Installa per l'utente corrente (%LOCALAPPDATA%\Programs\PozzoLauncher), senza permessi di amministratore:
; cosi' anche l'aggiornamento dall'app (installer lanciato con /SILENT) non chiede conferme UAC.
; I dati dell'utente stanno in %LOCALAPPDATA%\PozzoLauncher: la disinstallazione non li tocca.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{CE2FB0F8-58F8-4638-8A8C-AE32627832CE}
AppName=Pozzo Launcher
AppVersion={#AppVersion}
AppVerName=Pozzo Launcher {#AppVersion}
AppPublisher=erpaffo
AppPublisherURL=https://github.com/erpaffo/GameScraper
DefaultDirName={autopf}\PozzoLauncher
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=PozzoLauncher-{#AppVersion}-windows-x64-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PozzoLauncher.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; librerie della versione precedente: evita file vecchi mescolati ai nuovi
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\PozzoLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Pozzo Launcher"; Filename: "{app}\PozzoLauncher.exe"
Name: "{autodesktop}\Pozzo Launcher"; Filename: "{app}\PozzoLauncher.exe"; Tasks: desktopicon

[Run]
; senza skipifsilent: dopo l'aggiornamento silenzioso dall'app, Pozzo Launcher si riapre da solo
Filename: "{app}\PozzoLauncher.exe"; Description: "{cm:LaunchProgram,Pozzo Launcher}"; Flags: nowait postinstall
