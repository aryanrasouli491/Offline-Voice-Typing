@echo off
rem Copyright (c) 2026 Nooraqa Rasouli. SPDX-License-Identifier: MIT
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo First run 2 - Get Libraries.cmd. Then run this file again.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0setup\download_model.py"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" "%~dp0setup\check_installation.py"
if errorlevel 1 goto failed
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app\desktop_app.py"
exit /b 0
:failed
echo.
echo Setup did not finish. Read the message above, then retry this file.
pause
exit /b 1
