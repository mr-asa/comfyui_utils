@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "START=%~dp0"
set "PY=python"
set "CFG=%START%config.json"
set "CFG_CLI=%START%config_cli.py"

if exist "%CFG%" (
  for /f "usebackq delims=" %%R in (`python "%CFG_CLI%" --config "%CFG%" get --key selected_venv_path`) do (
    set "VENV_PATH=%%R"
  )
)

if defined VENV_PATH if exist "%VENV_PATH%\\Scripts\\python.exe" (
  set "PY=%VENV_PATH%\\Scripts\\python.exe"
)

echo.
echo [1] Update now (default)
echo [2] Edit settings
set /p CHOICE="Select [1-2]: "
if not defined CHOICE set "CHOICE=1"

if /i "%CHOICE%"=="2" (
  "%PY%" "%START%partial_repo_sync.py" --interactive
) else (
  "%PY%" "%START%partial_repo_sync.py"
)
pause
exit /b 0
