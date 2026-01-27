@echo off
setlocal
set /p REPO_PATH=Enter repository path: 
if "%REPO_PATH%"=="" (
  echo No path provided.
  exit /b 1
)
python "%~dp0rename_to_english.py" "%REPO_PATH%"
endlocal
