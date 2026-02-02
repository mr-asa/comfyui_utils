@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "START=%~dp0"
set "PY=python"

if exist "%START%config.json" (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%START%config.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; " ^
    "$r=$null; if($j.PSObject.Properties.Name -contains 'venv_path'){ $r=$j.venv_path } " ^
    "if($r){ $r=[string]$r; $r=$r.Trim(); if($r){ Write-Output $r } }"`) do (
    set "VENV_PATH=%%R"
  )
)

if defined VENV_PATH if exist "%VENV_PATH%\\Scripts\\python.exe" (
  set "PY=%VENV_PATH%\\Scripts\\python.exe"
)

echo.
echo [1] Update now (default)
echo [2] Edit settings
set /p CHOICE="Select [1-2]: "
if not defined CHOICE set "CHOICE=1"

if /i "%CHOICE%"=="2" (
  "%PY%" "%START%partial_repo_sync.py" --interactive
) else (
  "%PY%" "%START%partial_repo_sync.py"
)
pause
exit /b 0
