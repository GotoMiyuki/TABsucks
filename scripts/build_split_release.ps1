param(
    [Parameter(Mandatory = $true)]
    [string]$CpuPython,
    [string]$IsccPath = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    [switch]$SkipCpuBuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path -LiteralPath $IsccPath)) {
    throw "Inno Setup compiler not found: $IsccPath"
}
if (-not (Test-Path -LiteralPath "dist\TABsucks\TABsucks.exe")) {
    throw "Existing GPU distribution not found at dist\TABsucks"
}

if (-not $SkipCpuBuild) {
    & $CpuPython -c "import PyInstaller, torch; assert torch.version.cuda is None, 'CPU build requires CPU-only PyTorch'; print('PyInstaller', PyInstaller.__version__, 'torch', torch.__version__)"
    if ($LASTEXITCODE -ne 0) { throw "CPU build environment validation failed." }

    $env:TABSUCKS_INCLUDE_MODELS = "0"
    $env:TABSUCKS_DIST_NAME = "TABsucks-CPU"
    try {
        & $CpuPython -m PyInstaller --noconfirm --clean "packaging\tabsucks.spec"
        if ($LASTEXITCODE -ne 0) { throw "CPU PyInstaller build failed." }
    }
    finally {
        Remove-Item Env:TABSUCKS_INCLUDE_MODELS -ErrorAction SilentlyContinue
        Remove-Item Env:TABSUCKS_DIST_NAME -ErrorAction SilentlyContinue
    }
}

$releaseDir = Join-Path $ProjectRoot "dist\release"
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
Get-ChildItem -LiteralPath $releaseDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "TABsucks-*" } |
    Remove-Item -Force

foreach ($script in @(
    "packaging\tabsucks-base.iss",
    "packaging\tabsucks-models.iss",
    "packaging\tabsucks-gpu-addon.iss"
)) {
    Write-Host "Compiling $script..."
    & $IsccPath $script
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $script" }
}

$limit = 2GB
$oversized = Get-ChildItem -LiteralPath $releaseDir -File |
    Where-Object { $_.Length -gt $limit }
if ($oversized) {
    throw "Release assets exceed 2 GiB:`n$($oversized.FullName -join "`n")"
}

Get-ChildItem -LiteralPath $releaseDir -File |
    Sort-Object Name |
    Select-Object Name, Length, LastWriteTime
