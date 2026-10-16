param(
    [ValidateSet('dir', 'nsis', 'portable')][string]$Target = 'nsis',
    [switch]$BackendOnly
)
$ErrorActionPreference = 'Stop'
# Hosted Windows runners may default to cp1252; build logs include Chinese paths.
$env:PYTHONUTF8 = '1'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating Python environment failed. Install Python 3.12 x64.' }
}
$taskPython = Join-Path (Get-Location) '.venv/Scripts/python.exe'
& $taskPython -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Build requires Python 3.12 x64. Recreate .venv with that interpreter.' }
& $taskPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Installing build dependencies failed.' }
& $taskPython scripts/prepare_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Preparing release assets failed. Check the download error above.' }
& $taskPython -m unittest discover -s tests -q
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
& $taskPython scripts/collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'Collecting third-party notices failed.' }
& $taskPython -m PyInstaller --noconfirm --clean --distpath build/backend --workpath build/pyinstaller backend/videomagicmix-backend.spec
if ($LASTEXITCODE -ne 0) { throw 'Building standalone backend failed.' }
if ($BackendOnly) { exit 0 }
if (-not (Test-Path 'node_modules/.bin/electron-builder.cmd')) {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Installing Node dependencies failed.' }
}
& node --check desktop/main.js
if ($LASTEXITCODE -ne 0) { throw 'Desktop syntax check failed.' }
& npx.cmd electron-builder --win $Target --x64 --publish never
if ($LASTEXITCODE -ne 0) { throw 'Packaging Windows application failed.' }
& $taskPython scripts/smoke_packaged.py dist/win-unpacked/resources/app-runtime
if ($LASTEXITCODE -ne 0) { throw 'Standalone runtime smoke test failed.' }
& $taskPython scripts/smoke_desktop.py dist/win-unpacked/VideoMagicMix.exe
if ($LASTEXITCODE -ne 0) { throw 'Packaged desktop smoke test failed.' }
Get-ChildItem dist -File -Filter '*.exe' | ForEach-Object {
    $digest = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath ($_.FullName + '.sha256') -Value ($digest + '  ' + $_.Name) -Encoding ASCII
}
Write-Host 'Build complete. Outputs are in dist/; upload installers to GitHub Releases.'
