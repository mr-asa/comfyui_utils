@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"

REM Needs only Python with requests + packaging and uv on PATH (pip install uv)
python "%SCRIPT_DIR%build_venv.py" %*

pause
