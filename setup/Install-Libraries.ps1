# Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        if ($CheckOnly) { throw 'Libraries are not installed. Run Start NR Voice Typing.cmd.' }
        # Query interpreters without changing PATH, system Python or other environments.
        $basePython = $null
        if (Get-Command py.exe -ErrorAction SilentlyContinue) {
            try {
                $candidate = & py.exe -3.12 -c 'import sys, struct; print(sys.executable if sys.version_info[:2] == (3, 12) and struct.calcsize(chr(80)) == 8 else str())' 2>$null
                if ($LASTEXITCODE -eq 0 -and $candidate) { $basePython = $candidate.Trim() }
            } catch { } # The launcher can exist without a Python 3.12 installation.
        }
        if (-not $basePython -and (Get-Command python.exe -ErrorAction SilentlyContinue)) {
            try {
                $candidate = & python.exe -c 'import sys, struct; print(sys.executable if sys.version_info[:2] == (3, 12) and struct.calcsize(chr(80)) == 8 else str())' 2>$null
                if ($LASTEXITCODE -eq 0 -and $candidate) { $basePython = $candidate.Trim() }
            } catch { }
        }
        if (-not $basePython) { throw 'Install Python 3.12 (64-bit) with Tcl/Tk and the Python launcher from python.org, then retry.' }
        # Compatible global libraries remain visible; new/updated wheels stay in .venv.
        & $basePython -m venv --system-site-packages (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
    }
    $installerArgs = @((Join-Path $PSScriptRoot 'install_libraries.py'))
    if ($CheckOnly) { $installerArgs += '--check' }
    & $pythonPath @installerArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host 'Required libraries are ready.' -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
