# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
param([switch]$Repair, [switch]$SetupOnly)

$ErrorActionPreference = 'Stop'
# Keep the portable project folder clean. The application does not need
# Python bytecode caches, and child Python processes inherit this setting.
$env:PYTHONDONTWRITEBYTECODE = '1'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$pythonw = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$app = Join-Path $projectRoot 'app\desktop_app.py'
$model = Join-Path $projectRoot 'runtime\models\small'
$readyMarker = Join-Path $projectRoot '.venv\nr-voice-typing-2.0.0.ready'
$requiredModelFiles = @('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt')

try {
    if (-not $app.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $app -PathType Leaf)) {
        throw 'The application files are incomplete. Download and extract the project again.'
    }

    $modelReady = $true
    foreach ($name in $requiredModelFiles) {
        $path = Join-Path $model $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -eq 0) {
            $modelReady = $false
            break
        }
    }
    $setupNeeded = $Repair -or -not (Test-Path -LiteralPath $python -PathType Leaf) -or
                   -not (Test-Path -LiteralPath $pythonw -PathType Leaf) -or
                   -not (Test-Path -LiteralPath $readyMarker -PathType Leaf) -or -not $modelReady

    if ($setupNeeded) {
        Write-Host 'Preparing NR Voice Typing. The first run needs an internet connection.' -ForegroundColor Cyan
        & (Join-Path $PSScriptRoot 'Install-Libraries.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'The required Python libraries could not be prepared.' }

        & $python (Join-Path $PSScriptRoot 'download_model.py')
        if ($LASTEXITCODE -ne 0) { throw 'The Small speech model could not be prepared.' }

        & $python (Join-Path $PSScriptRoot 'check_installation.py')
        if ($LASTEXITCODE -ne 0) { throw 'The installation check did not pass.' }

        # The marker makes normal starts immediate. -Repair repeats all checks.
        Set-Content -LiteralPath $readyMarker -Value 'NR Voice Typing 2.0.0 ready' -Encoding Ascii
        Write-Host 'Setup completed. Future starts will be immediate.' -ForegroundColor Green
    }

    if ($SetupOnly) { exit 0 }
    # Start-Process joins arguments into one command line, so quote the app path
    # explicitly for installations whose parent folders contain spaces.
    Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f $app) -WorkingDirectory $projectRoot
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host 'If Python is missing, install Python 3.12 (64-bit) with pip, Tcl/Tk and the launcher.' -ForegroundColor Yellow
    exit 1
}
