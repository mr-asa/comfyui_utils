@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%compare_venv_versions.ps1"
set "SETTINGS_PATH=%SCRIPT_DIR%compare_venv_versions_config.json"

if not exist "%PS_SCRIPT%" (
  echo File not found: "%PS_SCRIPT%"
  pause
  exit /b 1
)

:run_again
cls
pwsh -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -SettingsPath "%SETTINGS_PATH%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Script finished with error code %EXIT_CODE%.
)

echo.
set /p "_REFRESH=Press Enter to refresh..."
goto run_again
