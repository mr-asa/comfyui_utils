@echo off
setlocal

REM Path to python in the ComfyUI environment
set PYTHON_EXE=F:\ComfyUI\env_2025-08\python.exe

REM Path to this batch script
set SCRIPT_DIR=%~dp0

REM Run the update script
"%PYTHON_EXE%" "%SCRIPT_DIR%update_comfy_repos.py" --root "F:\ComfyUI\ComfyUI"

endlocal
pause

