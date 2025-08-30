@echo off
setlocal EnableExtensions

REM Directory where this batch file lives
set "SCRIPT_DIR=%~dp0"

REM Read conda_env_folder from config.json (via PowerShell) and build full path to python.exe
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "(Get-Content '%SCRIPT_DIR%config.json' -Raw | ConvertFrom-Json).conda_env_folder + '\\python.exe'"`) do (
    set "PYTHON_EXE=%%I"
)

@REM echo SCRIPT_DIR = %SCRIPT_DIR%
@REM echo PYTHON_EXE = %PYTHON_EXE%

"%PYTHON_EXE%" "%SCRIPT_DIR%/comfyui_pip_update_audit.py"

pause

