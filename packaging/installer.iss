; Compile from build_windows.ps1 after the onedir application has been built.
#ifndef AppVersion
  #error AppVersion must be supplied by build_windows.ps1
#endif
#ifndef AppFileVersion
  #error AppFileVersion must be supplied by build_windows.ps1
#endif

[Setup]
AppId={{11C4C643-00CD-4D81-A998-6C75B0DC580D}
AppName=島語 inline 空位監看
AppVersion={#AppVersion}
VersionInfoVersion={#AppFileVersion}
AppPublisher=sfy257000
AppPublisherURL=https://github.com/sfy257000/island-inline-watcher
AppSupportURL=https://github.com/sfy257000/island-inline-watcher/issues
AppUpdatesURL=https://github.com/sfy257000/island-inline-watcher/releases
DefaultDirName={localappdata}\Programs\IslandInlineWatcher
DefaultGroupName=島語 inline 空位監看
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\assets\island-inline-watcher.ico
UninstallDisplayIcon={app}\IslandInlineWatcher.exe
OutputDir=..\release
OutputBaseFilename=island-inline-watcher-v{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
#ifdef SignEnabled
SignTool=islandsign
SignedUninstaller=yes
SignedUninstallerDir=..\build\signed-uninstaller
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesetraditional"; MessagesFile: "ChineseTraditional.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他選項："

[Files]
Source: "..\dist\IslandInlineWatcher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{localappdata}\IslandInlineWatcher"; Flags: uninsneveruninstall

[Icons]
Name: "{autoprograms}\島語 inline 空位監看"; Filename: "{app}\IslandInlineWatcher.exe"
Name: "{autodesktop}\島語 inline 空位監看"; Filename: "{app}\IslandInlineWatcher.exe"; Tasks: desktopicon
Name: "{autoprograms}\島語 inline 空位監看資料夾"; Filename: "{localappdata}\IslandInlineWatcher"

[Run]
Filename: "{app}\IslandInlineWatcher.exe"; Description: "啟動島語 inline 空位監看"; Flags: nowait postinstall skipifsilent

; User settings and Chrome profiles intentionally survive upgrades and uninstall.
[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not FileExists(ExpandConstant('{pf64}\Google\Chrome\Application\chrome.exe')) and
     not FileExists(ExpandConstant('{pf32}\Google\Chrome\Application\chrome.exe')) and
     not FileExists(ExpandConstant('{localappdata}\Google\Chrome\Application\chrome.exe')) then
    MsgBox('此程式需要 Google Chrome。請先安裝 Chrome，再使用空位監看功能。', mbInformation, MB_OK);
end;
