#define MyAppName "TABsucks GPU Upgrade"
#define MyAppVersion "0.2.0"
#define BaseDir "{localappdata}\Programs\TABsucks"

[Setup]
AppId={{7A50CE6A-971D-44C9-A294-28F9F669EB41}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={#BaseDir}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\release
OutputBaseFilename=TABsucks-GPU-Upgrade-Setup
Compression=lzma2
SolidCompression=yes
DiskSpanning=yes
DiskSliceSize=1800000000
SlicesPerDisk=1
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
Uninstallable=no

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\TABsucks\TABsucks.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\TABsucks\_internal\*"; DestDir: "{app}\_internal"; Excludes: "models\*;pretrained\*;src\plugins\chord\external\chordmini\checkpoints\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not FileExists(ExpandConstant('{app}\TABsucks.exe')) then
    Result := 'Install TABsucks Base before installing the GPU upgrade.';
  if FileExists(ExpandConstant('{app}\_internal\models\BS-Roformer-SW.ckpt')) then
    Result := 'Install the GPU upgrade before installing the model package.';
end;
