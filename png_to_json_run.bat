@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=python"

REM Ensure Pillow is available
"%PYTHON_EXE%" -c "import PIL" >nul 2>&1
if errorlevel 1 (
  echo Pillow not found. Installing...
  "%PYTHON_EXE%" -m pip install pillow
)

"%PYTHON_EXE%" "%SCRIPT_DIR%png_to_json.py"

endlocal
pause
