@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=python"

pushd "%SCRIPT_DIR%"
"%PYTHON_EXE%" "%SCRIPT_DIR%update_workflow_repos.py" %*
popd

endlocal
pause
