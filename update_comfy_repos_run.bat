@echo off
setlocal

REM Path to this batch script
set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "CONFIG_CLI=%SCRIPT_DIR%config_cli.py"
set "PYTHON_EXE="
set "CANDIDATE_PY="

REM Try to read active python path from config.json (schema-aware helper)
if exist "%CONFIG_PATH%" (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "python '%CONFIG_CLI%' --config '%CONFIG_PATH%' get --key python_for_active_env"`) do (
      set "CANDIDATE_PY=%%I"
  )
)

if defined CANDIDATE_PY if exist "%CANDIDATE_PY%" (
  set "PYTHON_EXE=%CANDIDATE_PY%"
) else (
  echo config.json missing python path or file not found. Using python from PATH...
  set "PYTHON_EXE=python"
)

REM Run the update script (it will prompt for config if needed)
"%PYTHON_EXE%" "%SCRIPT_DIR%update_comfy_repos.py"

endlocal
pause
