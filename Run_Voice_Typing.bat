@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo The app is not installed yet. Run Install_Voice_Typing.bat first.
    pause
    exit /b 1
)
if not exist "desktop_app.py" (
    echo desktop_app.py was not found in this folder.
    pause
    exit /b 2
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop_app.py"
exit /b 0
