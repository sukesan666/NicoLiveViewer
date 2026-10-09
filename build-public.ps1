param(
    [string]$Python = (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'),
    [string]$OutputDir = (Join-Path $PSScriptRoot 'dist')
)
$ErrorActionPreference = 'Stop'
$Python = (Resolve-Path -LiteralPath $Python).Path
Push-Location $PSScriptRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onedir --console --name NicoLiveViewer --icon 'src/nicoview/assets/studio.ico' --paths src --collect-all streamlink --collect-all streamlink_cli --collect-data certifi --add-data 'src/nicoview/assets;nicoview/assets' --distpath $OutputDir --workpath build --specpath . src/nicoview/__main__.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    $package = Join-Path $OutputDir 'NicoLiveViewer'
    Copy-Item -LiteralPath 'README.md','USAGE.md','THIRD_PARTY.md','FEATURE_STATUS.md','LICENSE' -Destination $package
    & (Join-Path $package 'NicoLiveViewer.exe') --self-test
    if ($LASTEXITCODE -ne 0) { throw 'Packaged self-test failed' }
    Write-Output 'Before redistributing, include dependency notices and corresponding sources described in THIRD_PARTY.md.'
} finally { Pop-Location }
