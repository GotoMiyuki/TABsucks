param(
    [string]$PythonExe = "python",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "Checking TABsucks Windows build prerequisites..."
& $PythonExe --version
if ($LASTEXITCODE -ne 0) { throw "Python is not available: $PythonExe" }
& $PythonExe -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is not installed for: $PythonExe" }

$RequiredAssets = @(
    "models\BS-Roformer-SW.ckpt",
    "models\BS-Roformer-SW.yaml",
    "src\plugins\chord\external\chordmini\checkpoints\2e1d_model_best.pth",
    "src\plugins\chord\external\chordmini\checkpoints\btc_model_best.pth",
    "src\plugins\chord\external\chordmini\checkpoints\btc_model_large_voca.pt"
)

$MissingAssets = $RequiredAssets | Where-Object { -not (Test-Path $_) }
if ($MissingAssets) {
    throw "Missing release assets:`n$($MissingAssets -join "`n")"
}

Write-Host "Preparing bundled FFmpeg executables..."
& $PythonExe "scripts\prepare_ffmpeg.py"
if ($LASTEXITCODE -ne 0) { throw "Failed to prepare FFmpeg executables." }

Write-Host "Building PyInstaller onedir distribution..."
& $PythonExe -m PyInstaller --noconfirm --clean "packaging\tabsucks.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

if ($SkipInstaller) {
    Write-Host "Portable distribution created at dist\TABsucks"
    exit 0
}

$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if (-not $Iscc) {
    throw "Inno Setup 6 was not found. Install it or rerun with -SkipInstaller."
}

Write-Host "Building Windows installer..."
& $Iscc.Source "packaging\tabsucks.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed." }
Write-Host "Installer created at dist\installer\TABsucks-Setup.exe"
