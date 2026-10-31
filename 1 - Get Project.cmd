@echo off
rem Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
setlocal
set "NR_BOOTSTRAP_SELF=%~f0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$text = [IO.File]::ReadAllText($env:NR_BOOTSTRAP_SELF); & ([scriptblock]::Create(($text -split '(?m)^# POWERSHELL_PAYLOAD\r?$', 2)[1]))"
set "NR_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %NR_RESULT%
# POWERSHELL_PAYLOAD
# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
param([string]$ArchivePath, [string]$Destination)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$releaseVersion = '2.0.0'
$repository = 'aryanrasouli491/Offline-Voice-Typing'
$archiveUrl = "https://github.com/$repository/archive/refs/tags/v$releaseVersion.zip"
$stage = $null
try {
    if (-not $Destination) {
        $Destination = Join-Path (Split-Path -Parent $env:NR_BOOTSTRAP_SELF) "NR Voice Typing $releaseVersion"
    }
    $Destination = [IO.Path]::GetFullPath($Destination)
    if (Test-Path -LiteralPath $Destination) {
        throw 'The destination already exists. Open it and run Get Libraries, or place this downloader in another folder. Nothing was overwritten.'
    }
    # A unique temporary folder makes interrupted downloads safe to retry.
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $stage = Join-Path $tempRoot ('NRVoiceSetup-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    if (-not $ArchivePath) {
        $ArchivePath = Join-Path $stage 'source.zip'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host "Downloading NR Voice Typing $releaseVersion source from GitHub..."
        try {
            Invoke-WebRequest -Uri $archiveUrl -OutFile $ArchivePath -UseBasicParsing -TimeoutSec 180
        } catch {
            throw "Could not download v$releaseVersion. Check your internet connection and that the developer has published this release at https://github.com/$repository/releases. No existing installation was changed."
        }
    }
    if ((Get-Item -LiteralPath $ArchivePath).Length -gt 50MB) { throw 'The archive is unexpectedly large for a source-only release.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    $unpacked = Join-Path $stage 'unpacked'
    New-Item -ItemType Directory -Path $unpacked | Out-Null
    try {
        $totalBytes = 0L
        $roots = @{}
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            $parts = $name.Split('/')
            if (-not $parts[0] -or $name.Contains(':') -or $parts -contains '..' -or $parts -contains '.') {
                throw 'The archive contains an unsafe path.'
            }
            $roots[$parts[0]] = $true
            if ($parts -contains '.venv' -or $parts -contains 'runtime' -or $parts -contains '.git' -or $parts -contains 'local_config.json') {
                throw 'This archive contains local files that do not belong in a source release.'
            }
            if ((($entry.ExternalAttributes -shr 16) -band 61440) -eq 40960) { throw 'Symbolic links are not accepted in this source release.' }
            $totalBytes += $entry.Length
            if ($totalBytes -gt 100MB -or $zip.Entries.Count -gt 10000) { throw 'The archive exceeds the source release limits.' }
            $target = [IO.Path]::GetFullPath((Join-Path $unpacked $name))
            if (-not $target.StartsWith($unpacked + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'The archive path leaves the extraction folder.' }
            if ($name.EndsWith('/')) {
                New-Item -ItemType Directory -Force -Path $target | Out-Null
            } else {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $false)
            }
        }
    } finally {
        $zip.Dispose()
    }
    if ($roots.Count -ne 1) { throw 'The archive must contain one project folder.' }
    $source = Join-Path $unpacked (@($roots.Keys)[0])
    foreach ($required in @('app\desktop_app.py', 'app\app_info.py', 'setup\Install-Libraries.ps1', '2 - Get Libraries.cmd', '3 - Get Model and Start.cmd', 'LICENSE', 'README.md')) {
        if (-not (Test-Path -LiteralPath (Join-Path $source $required) -PathType Leaf)) { throw "Missing release file: $required" }
    }
    $identity = [IO.File]::ReadAllText((Join-Path $source 'app\app_info.py'))
    if ($identity -notmatch ('APP_VERSION\s*=\s*"' + [regex]::Escape($releaseVersion) + '"')) { throw 'The downloaded source does not match version 2.0.0.' }
    # Both paths are verified before moving. Never merge into an existing project.
    $source = [IO.Path]::GetFullPath($source)
    if (-not $source.StartsWith($unpacked + '\', [StringComparison]::OrdinalIgnoreCase) -or (Test-Path -LiteralPath $Destination)) { throw 'The destination changed. Please retry.' }
    Move-Item -LiteralPath $source -Destination $Destination
    Write-Host "Source saved to: $Destination" -ForegroundColor Green
    Write-Host 'Open that folder, run 2 - Get Libraries.cmd, then 3 - Get Model and Start.cmd.'
    Write-Host 'Only source was downloaded. No library or model is bundled.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        $cleanup = [IO.Path]::GetFullPath($stage)
        if ($cleanup.StartsWith($tempRoot.TrimEnd('\') + '\NRVoiceSetup-', [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $cleanup -Recurse -Force
        }
    }
}
