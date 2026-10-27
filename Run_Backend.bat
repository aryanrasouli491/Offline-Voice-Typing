@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The app is not installed yet. Run Install_Voice_Typing.bat first.
    pause
    exit /b 1
)
if not exist "backend\local_config.json" (
    echo Private backend configuration is missing. Creating it now...
    ".venv\Scripts\python.exe" "prepare_backend_config.py"
    if errorlevel 1 (
        pause
        exit /b 2
    )
)
echo Starting the local API backend at http://127.0.0.1:8765
".venv\Scripts\python.exe" -m backend.main
pause
