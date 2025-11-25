@echo off
setlocal

REM Path to this batch script
set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "PYTHON_EXE="
set "CANDIDATE_PY="

REM Try to read python path from conda_env_folder in config.json
if exist "%CONFIG_PATH%" (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "(Get-Content '%CONFIG_PATH%' -Raw | ConvertFrom-Json).conda_env_folder + '\\python.exe'"`) do (
      set "CANDIDATE_PY=%%I"
  )
  if not defined CANDIDATE_PY (
    for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "(Get-Content '%CONFIG_PATH%' -Raw | ConvertFrom-Json).venv_path + '\\Scripts\\python.exe'"`) do (
        set "CANDIDATE_PY=%%I"
    )
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
