#define MyAppName "TABsucks Models"
#define MyAppVersion "0.2.0"
#define BaseDir "{localappdata}\Programs\TABsucks"

[Setup]
AppId={{1BC480AB-16A7-4F1D-B45C-BEE7C37E5BE1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={#BaseDir}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\release
OutputBaseFilename=TABsucks-Models-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Uninstallable=no

[Files]
Source: "..\models\*"; DestDir: "{app}\_internal\models"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\pretrained\*"; DestDir: "{app}\_internal\pretrained"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\src\plugins\chord\external\chordmini\checkpoints\*"; DestDir: "{app}\_internal\src\plugins\chord\external\chordmini\checkpoints"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\TABsucks.exe')) then
    Result := 'Install TABsucks Base before installing the model package.';
end;
