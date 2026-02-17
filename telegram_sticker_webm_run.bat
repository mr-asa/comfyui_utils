@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%telegram_sticker_webm.ps1"

if not exist "%PS_SCRIPT%" (
  echo File not found: "%PS_SCRIPT%"
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Script finished with error code %EXIT_CODE%.
)

echo.
pause
exit /b %EXIT_CODE%
