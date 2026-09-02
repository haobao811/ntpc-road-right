[Setup]
AppId={{5BA9828E-3B6A-469A-B3DE-776967A24E02}
AppName=NTPC Road Right
AppVersion=1.0.3
DefaultDirName={autopf}\NTPC Road Right
DefaultGroupName=NTPC Road Right
OutputDir=output
OutputBaseFilename=ntpc-road-right-setup
Compression=lzma
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 假設你的 pyinstaller 打包後的資料夾名稱叫做 main
Source: "dist\main\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NTPC Road Right"; Filename: "{app}\main.exe"
Name: "{autodesktop}\NTPC Road Right"; Filename: "{app}\main.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked