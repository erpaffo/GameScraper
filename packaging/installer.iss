; Installer Windows (Inno Setup 6) della cartella creata da PyInstaller (dist\GameScraper).
; Uso:  iscc /DAppVersion=0.5.1 packaging\installer.iss      -> release\GameScraper-0.5.1-windows-x64-setup.exe
;
; Installa per l'utente corrente (%LOCALAPPDATA%\Programs\GameScraper), senza permessi di amministratore:
; cosi' anche l'aggiornamento dall'app (installer lanciato con /SILENT) non chiede conferme UAC.
; I dati dell'utente stanno in %LOCALAPPDATA%\GameScraper: la disinstallazione non li tocca.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{CE2FB0F8-58F8-4638-8A8C-AE32627832CE}
AppName=GameScraper
AppVersion={#AppVersion}
AppVerName=GameScraper {#AppVersion}
AppPublisher=erpaffo
AppPublisherURL=https://github.com/erpaffo/GameScraper
DefaultDirName={autopf}\GameScraper
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=GameScraper-{#AppVersion}-windows-x64-setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\GameScraper.exe
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
Source: "..\dist\GameScraper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GameScraper"; Filename: "{app}\GameScraper.exe"
Name: "{autodesktop}\GameScraper"; Filename: "{app}\GameScraper.exe"; Tasks: desktopicon

[Run]
; senza skipifsilent: dopo l'aggiornamento silenzioso dall'app, GameScraper si riapre da solo
Filename: "{app}\GameScraper.exe"; Description: "{cm:LaunchProgram,GameScraper}"; Flags: nowait postinstall
