@echo off
rem Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
setlocal
cd /d "%~dp0"
title NR Voice Typing

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\Start-NRVoiceTyping.ps1" %*
set "NR_RESULT=%ERRORLEVEL%"

if not "%NR_RESULT%"=="0" (
    echo.
    echo NR Voice Typing could not start. Read the message above, then try again.
    echo Installation help: README.md
    pause
)
exit /b %NR_RESULT%
