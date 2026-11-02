#define AppName "Video Channel Downloader"
#define AppVersion "1.6"

[Setup]
AppId={{5C257202-E5E6-4DA5-A953-1357E6411B93}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\VideoChannelDownloader
DefaultGroupName={#AppName}
OutputDir=dist
OutputBaseFilename=VideoChannelDownloader-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\VideoChannelDownloader.exe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\VideoChannelDownloader.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "chrome_extension\*"; DestDir: "{app}\chrome_extension"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\VideoChannelDownloader.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\VideoChannelDownloader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VideoChannelDownloader.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
