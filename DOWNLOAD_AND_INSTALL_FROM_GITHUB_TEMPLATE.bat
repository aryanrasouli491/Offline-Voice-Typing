@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Voice Typing Offline - GitHub Bootstrap

REM IMPORTANT: after creating your public GitHub repository, replace the
REM placeholder below with YOUR-USERNAME/repository-name, then publish this file.
set "VT_GITHUB_REPO=YOUR-USERNAME/voice-typing-offline"
set "VT_GITHUB_BRANCH=main"
set "VT_INSTALL_ROOT=%USERPROFILE%\Voice Typing Offline"
set "VT_ZIP=%TEMP%\voice-typing-source.zip"
set "VT_EXTRACT=%TEMP%\voice-typing-source"

if /I "%VT_GITHUB_REPO%"=="YOUR-USERNAME/voice-typing-offline" goto NEED_REPO

echo ============================================================
echo VOICE TYPING OFFLINE - DOWNLOAD FROM GITHUB
echo ============================================================
echo Repository: %VT_GITHUB_REPO%
echo Install folder: %VT_INSTALL_ROOT%
echo.
echo Downloading the source code from the public repository...
echo Then the main installer will download Python dependencies and the model.
echo.

powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; $repo=$env:VT_GITHUB_REPO; $branch=$env:VT_GITHUB_BRANCH; $zip=$env:VT_ZIP; $extract=$env:VT_EXTRACT; $target=$env:VT_INSTALL_ROOT; Invoke-WebRequest -UseBasicParsing -Uri ('https://github.com/' + $repo + '/archive/refs/heads/' + $branch + '.zip') -OutFile $zip; if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }; Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force; $source=Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1; if ($null -eq $source -or !(Test-Path (Join-Path $source.FullName 'Install_Voice_Typing.bat'))) { throw 'The archive does not contain Install_Voice_Typing.bat at its project root.' }; New-Item -ItemType Directory -Path $target -Force | Out-Null; Copy-Item -Path (Join-Path $source.FullName '*') -Destination $target -Recurse -Force; if (!(Test-Path (Join-Path $target 'Install_Voice_Typing.bat'))) { throw 'Copying the project failed.' }; Write-Host 'Source code downloaded and extracted successfully.'"
if errorlevel 1 goto DOWNLOAD_FAILED

echo.
echo Source code is ready. Starting the full installer...
echo.
call "%VT_INSTALL_ROOT%\Install_Voice_Typing.bat"
exit /b %ERRORLEVEL%

:NEED_REPO
echo This is a TEMPLATE and is not ready to share yet.
echo Open this file in Notepad and replace:
echo   YOUR-USERNAME/voice-typing-offline
echo with the real public GitHub owner/repository name.
echo Also confirm the branch name is main.
echo Save the file and upload it to the repository root.
echo.
pause
exit /b 2

:DOWNLOAD_FAILED
echo.
echo ERROR: The GitHub source could not be downloaded or prepared.
echo Check the public repository name, branch name, and internet connection.
echo.
pause
exit /b 1
