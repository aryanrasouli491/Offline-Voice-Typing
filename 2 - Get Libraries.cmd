@echo off
rem Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\Install-Libraries.ps1" %*
set "NR_RESULT=%ERRORLEVEL%"
echo.
if not "%NR_RESULT%"=="0" echo Setup did not finish. Read the message above, then retry.
if /i not "%~1"=="-CheckOnly" pause
exit /b %NR_RESULT%
