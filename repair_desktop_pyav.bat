@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The project environment was not found. Run Install_Voice_Typing.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install "av>=11,<19"
if errorlevel 1 goto FAIL
".venv\Scripts\python.exe" -c "import av,faster_whisper; print('PyAV:', av.__version__); print('Faster-Whisper:', getattr(faster_whisper, '__version__', 'unknown'))"
if errorlevel 1 goto FAIL
echo PyAV repair complete.
pause
exit /b 0
:FAIL
echo Repair failed. Check the error above and your internet connection.
pause
exit /b 1
