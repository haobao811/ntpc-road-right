[Setup]
AppId={{5BA9828E-3B6A-469A-B3DE-776967A24E02}
AppName=NTPC Road Right
AppVersion=1.0.9
DefaultDirName={autopf}\NTPC Road Right
DefaultGroupName=NTPC Road Right
OutputDir=output
OutputBaseFilename=ntpc-road-right-setup
SetupIconFile=road.ico
Compression=lzma
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 這裡改成對應英文資料夾名稱 road_app
Source: "dist\road_app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "road.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 捷徑依然可以指向中文的執行檔名稱，不影響顯示
Name: "{group}\NTPC Road Right"; Filename: "{app}\road_app.exe"; IconFilename: "{app}\road.ico"
Name: "{autodesktop}\NTPC Road Right"; Filename: "{app}\road_app.exe"; IconFilename: "{app}\road.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\road_app.exe"; Description: "{cm:LaunchProgram,NTPC Road Right}"; Flags: nowait postinstall skipifsilent