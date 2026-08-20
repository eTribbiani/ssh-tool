; Inno Setup 安装脚本 — 把 dist/sshtool (onedir) 打包成单个 setup.exe
;
; 构建 (需已安装 Inno Setup 6, 且已用 PyInstaller 生成 dist/sshtool):
;   ISCC.exe sshtool_installer.iss
; 产物:
;   dist/SSHTool-Setup-1.0.0.exe
;
; 安装到 {localappdata}\Programs\SSHTool (可写目录, 无需管理员),
; 生成开始菜单 + 可选桌面快捷方式 + 卸载项。

#define MyAppName "SSHTool GUI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SSHTool"
#define MyAppExeName "sshtool.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-4A7B-8C9D-0E1F2A3B4C5D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\SSHTool
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=SSHTool-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks:"

[Files]
Source: "dist\sshtool\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
