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

"%PY%" "%START%custom_nodes_link_manager.py"
pause
exit /b 0
