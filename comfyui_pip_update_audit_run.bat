@echo off
setlocal EnableExtensions

REM Directory where this batch file lives
set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "CONFIG_CLI=%SCRIPT_DIR%config_cli.py"
set "PYTHON_EXE="
set "ROOT_DIR=%SCRIPT_DIR%..\.."
set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"

REM Resolve active python path from config.json (schema-aware helper).
if exist "%CONFIG_PATH%" (
  for /f "usebackq delims=" %%I in (`python "%CONFIG_CLI%" --config "%CONFIG_PATH%" get --key python_for_active_env`) do (
      set "PYTHON_EXE=%%I"
  )
)

if not defined PYTHON_EXE (
  if exist "%VENV_PY%" (
    set "PYTHON_EXE=%VENV_PY%"
  ) else (
    echo config.json not found or selected env python missing. Using python from PATH...
    set "PYTHON_EXE=python"
  )
)

"%PYTHON_EXE%" -V >nul 2>&1
if errorlevel 1 if exist "%VENV_PY%" set "PYTHON_EXE=%VENV_PY%"

@REM echo SCRIPT_DIR = %SCRIPT_DIR%
@REM echo PYTHON_EXE = %PYTHON_EXE%

"%PYTHON_EXE%" "%SCRIPT_DIR%comfyui_pip_update_audit.py" %*

pause
