@echo off
setlocal EnableExtensions

REM Directory where this batch file lives
set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "PYTHON_EXE="
set "ROOT_DIR=%SCRIPT_DIR%..\.."
set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"

REM Read conda_env_folder from config.json (via PowerShell) and build full path to python.exe
if exist "%CONFIG_PATH%" (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "(Get-Content '%CONFIG_PATH%' -Raw | ConvertFrom-Json).conda_env_folder + '\\python.exe'"`) do (
      set "PYTHON_EXE=%%I"
  )
)

if not defined PYTHON_EXE (
  if exist "%VENV_PY%" (
    set "PYTHON_EXE=%VENV_PY%"
  ) else (
    echo config.json not found or missing conda_env_folder. Using python from PATH to bootstrap config...
    set "PYTHON_EXE=python"
  )
)

"%PYTHON_EXE%" >nul 2>&1
if errorlevel 1 if exist "%VENV_PY%" set "PYTHON_EXE=%VENV_PY%"

@REM echo SCRIPT_DIR = %SCRIPT_DIR%
@REM echo PYTHON_EXE = %PYTHON_EXE%

"%PYTHON_EXE%" "%SCRIPT_DIR%comfyui_pip_update_audit.py" %*

pause
