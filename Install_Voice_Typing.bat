@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Voice Typing Offline - Installer
color 0B

REM Always work from this file's folder, even if the folder was moved.
set "ROOT=%~dp0"
cd /d "%ROOT%"
set "PY_CMD="
set "PY_INSTALLER=%TEMP%\voice-typing-python-3.13.16-amd64.exe"

cls
echo ============================================================
echo            VOICE TYPING OFFLINE - SETUP
echo ============================================================
echo.
echo This installer will:
echo   1. Find a supported Python version or download Python.
echo   2. Create the project's private Python environment.
echo   3. Install the desktop and backend libraries.
echo   4. Download the Faster-Whisper Small speech model.
echo   5. Create a private local backend token.
echo.
echo You need an internet connection and about 3 GB of free space.
echo The first installation can take some time.
echo.

REM Prefer an already installed Python 3.13, 3.12, or 3.11.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.13 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.13"
)
if not defined PY_CMD (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.12 --version >nul 2>nul
        if not errorlevel 1 set "PY_CMD=py -3.12"
    )
)
if not defined PY_CMD (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.11 --version >nul 2>nul
        if not errorlevel 1 set "PY_CMD=py -3.11"
    )
)

REM If the Python launcher is unavailable, check Python on PATH.
if not defined PY_CMD goto CHECK_PATH_PYTHON
goto PYTHON_READY

:CHECK_PATH_PYTHON
where python >nul 2>nul
if errorlevel 1 goto INSTALL_PYTHON
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13)) else 1)" >nul 2>nul
if errorlevel 1 goto INSTALL_PYTHON
set "PY_CMD=python"
goto PYTHON_READY

:INSTALL_PYTHON
echo No supported Python version was found.
echo Downloading the official Python 3.13.16 installer from python.org...
echo.
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe' -OutFile '%PY_INSTALLER%'"
if errorlevel 1 goto PYTHON_DOWNLOAD_FAILED
if not exist "%PY_INSTALLER%" goto PYTHON_DOWNLOAD_FAILED

REM Verify the download has a valid Authenticode signature before running it.
powershell -NoProfile -Command "$s=Get-AuthenticodeSignature -LiteralPath '%PY_INSTALLER%'; if ($s.Status -ne 'Valid') { Write-Host ('Signature check failed: ' + $s.Status); exit 1 }; Write-Host ('Verified signer: ' + $s.SignerCertificate.Subject)"
if errorlevel 1 goto PYTHON_SIGNATURE_FAILED

echo Installing Python just for the current Windows user...
start /wait "" "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_pip=1 Include_test=0
if errorlevel 1 goto PYTHON_INSTALL_FAILED
set "PY_CMD="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
if not exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" goto PYTHON_INSTALL_FAILED

echo Python installation completed.
echo.

:PYTHON_READY
echo [1/5] Python version:
call %PY_CMD% --version
if errorlevel 1 goto SETUP_FAILED

if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo.
    echo [2/5] Creating a private Python environment...
    call %PY_CMD% -m venv "%ROOT%.venv"
    if errorlevel 1 goto SETUP_FAILED
) else (
    echo.
    echo [2/5] Reusing the existing .venv environment.
)

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%VENV_PY%" goto SETUP_FAILED

echo.
echo [3/5] Installing required libraries...
echo A log is saved as install-pip.log.
"%VENV_PY%" -m pip install --upgrade pip --log "%ROOT%install-pip.log"
if errorlevel 1 goto PIP_FAILED
"%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" --log "%ROOT%install-pip.log"
if errorlevel 1 goto PIP_FAILED

if not exist "%ROOT%models" mkdir "%ROOT%models"
if not exist "%ROOT%data" mkdir "%ROOT%data"

echo.
echo [4/5] Downloading or checking Faster-Whisper Small...
"%VENV_PY%" "%ROOT%download_model.py"
if errorlevel 1 goto MODEL_FAILED

echo.
echo [5/5] Creating a private backend configuration...
"%VENV_PY%" "%ROOT%prepare_backend_config.py"
if errorlevel 1 goto SETUP_FAILED

echo.
echo ============================================================
echo SETUP COMPLETE
echo ============================================================
echo The desktop app will open now.
echo For future use, double-click Start Voice Typing.vbs.
echo To start the optional local API backend, use Run_Backend.bat.
echo.
echo Setup log: install-pip.log
echo ============================================================
timeout /t 4 /nobreak >nul
call "%ROOT%Run_Voice_Typing.bat"
exit /b 0

:PYTHON_DOWNLOAD_FAILED
echo ERROR: Python could not be downloaded.
echo Check your internet connection, then try again.
echo Official page: https://www.python.org/downloads/windows/
goto FAILED

:PYTHON_SIGNATURE_FAILED
echo ERROR: The downloaded Python installer did not pass signature verification.
echo For your safety, the installer will not run it.
echo Download Python from the official website manually.
goto FAILED

:PYTHON_INSTALL_FAILED
echo ERROR: Python could not be installed in the current Windows user account.
echo Install Python 3.11, 3.12, or 3.13 from python.org, then retry.
goto FAILED

:PIP_FAILED
echo ERROR: Python package installation failed.
echo Check install-pip.log and your internet connection, then run this installer again.
goto FAILED

:MODEL_FAILED
echo ERROR: The model download or model file check failed.
echo Check your internet connection. The installer can be run again to retry.
goto FAILED

:SETUP_FAILED
echo ERROR: Setup did not finish successfully.
echo Review the message above and try again.
goto FAILED

:FAILED
echo.
echo Nothing has been deleted. You can fix the problem and run this file again.
echo.
pause
exit /b 1
