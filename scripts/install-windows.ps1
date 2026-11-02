param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '',
    [string]$Destination = '',
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
    throw 'VideoMagicMix requires Windows x64 (not ARM64 or 32-bit Windows).'
}
$releaseApi = 'https://api.github.com/repos/mgstationery1-code/VideoMagicMix/releases/'
if ($Version) { $releaseApi += "tags/v$Version" } else { $releaseApi += 'latest' }
$release = Invoke-RestMethod -Uri $releaseApi -Headers @{'User-Agent'='VideoMagicMix-Installer'}
if ($release.draft -or $release.prerelease -or $release.tag_name -notmatch '^v(\d+\.\d+\.\d+)$') { throw 'Expected a stable release.' }
$Version = $Matches[1]
$archiveName = "VideoMagicMix-Windows-$Version-x64.zip"
$archiveAsset = @($release.assets | Where-Object name -eq $archiveName)
$checksumAsset = @($release.assets | Where-Object name -eq 'SHA256SUMS.txt')
if ($archiveAsset.Count -ne 1 -or $checksumAsset.Count -ne 1) { throw 'Release is missing its directory package or checksums.' }
if (-not $Destination) { $Destination = Join-Path $env:LOCALAPPDATA "Programs\VideoMagicMix-$Version" }
$Destination = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $Destination) { throw "Destination already exists; choose a NEW directory. Nothing was overwritten: $Destination" }
$downloadDir = Join-Path $env:TEMP ('VideoMagicMix-download-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $downloadDir | Out-Null
$archive = Join-Path $downloadDir $archiveName
$checksums = Join-Path $downloadDir 'SHA256SUMS.txt'
Write-Host "Downloading VideoMagicMix $Version. Models and runtime are included; this is a large download."
Invoke-WebRequest -UseBasicParsing -Uri $checksumAsset[0].browser_download_url -OutFile $checksums
Invoke-WebRequest -UseBasicParsing -Uri $archiveAsset[0].browser_download_url -OutFile $archive
$pattern = '^([0-9a-fA-F]{64})\s+\*?' + [regex]::Escape($archiveName) + '$'
$matching = @(Get-Content -LiteralPath $checksums | Where-Object { $_ -match $pattern })
if ($matching.Count -ne 1) { throw 'Checksum entry is missing or ambiguous.' }
$null = $matching[0] -match $pattern
$expected = $Matches[1]
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) { throw 'ZIP checksum failed. Do not run this package.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    $prefix = $Destination.TrimEnd('\') + '\'
    foreach ($entry in $zip.Entries) {
        $resolved = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
        if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe archive path.' }
    }
} finally { $zip.Dispose() }
Write-Host "Verified ZIP. Extracting to $Destination"
Expand-Archive -LiteralPath $archive -DestinationPath $Destination
$manifest = Get-Content -LiteralPath (Join-Path $Destination 'FILE-MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    $file = [IO.Path]::GetFullPath((Join-Path $Destination $entry.path))
    if (-not $file.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe manifest path.' }
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing file: $($entry.path)" }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.sha256) { throw "File verification failed: $($entry.path)" }
}
$executable = Join-Path $Destination 'VideoMagicMix.exe'
$desktopPath = [Environment]::GetFolderPath('Desktop')
if ($desktopPath) {
    $shortcutPath = Join-Path $desktopPath "VideoMagicMix $Version.lnk"
    if (-not (Test-Path -LiteralPath $shortcutPath)) {
        $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $executable
        $shortcut.WorkingDirectory = $Destination
        $shortcut.Save()
    }
}
Write-Host "Installed and verified: $executable"
Write-Host 'Guide: https://github.com/mgstationery1-code/VideoMagicMix/blob/main/docs/USER_GUIDE.md'
Write-Host "Downloaded archive retained at: $archive"
if (-not $NoLaunch) { Start-Process -FilePath $executable -WorkingDirectory $Destination -WindowStyle Hidden }
