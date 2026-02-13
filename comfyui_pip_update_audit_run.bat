@echo off
setlocal EnableExtensions

REM Directory where this batch file lives
set "SCRIPT_DIR=%~dp0"
set "CONFIG_PATH=%SCRIPT_DIR%config.json"
set "PYTHON_EXE="
set "ROOT_DIR=%SCRIPT_DIR%..\.."
set "VENV_PY=%ROOT_DIR%\.venv\Scripts\python.exe"

REM Read env_type from config.json and resolve python path for selected environment.
if exist "%CONFIG_PATH%" (
  for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$cfg=Get-Content -LiteralPath '%CONFIG_PATH%' -Raw | ConvertFrom-Json; " ^
    "$envType=''; if($cfg.PSObject.Properties.Name -contains 'env_type' -and $cfg.env_type){ $envType=([string]$cfg.env_type).ToLower().Trim() }; " ^
    "$py=''; " ^
    "if($envType -eq 'venv'){ " ^
    "  $v=''; if($cfg.PSObject.Properties.Name -contains 'venv_path'){ $v=[string]$cfg.venv_path }; " ^
    "  if($v){ $cand=Join-Path $v 'Scripts\\python.exe'; if(Test-Path -LiteralPath $cand){ $py=$cand } } " ^
    "} elseif($envType -eq 'conda'){ " ^
    "  $c=''; if($cfg.PSObject.Properties.Name -contains 'conda_env_folder'){ $c=[string]$cfg.conda_env_folder }; " ^
    "  if($c){ $cand=Join-Path $c 'python.exe'; if(Test-Path -LiteralPath $cand){ $py=$cand } } " ^
    "} " ^
    "if(-not $py -and $cfg.PSObject.Properties.Name -contains 'venv_path' -and $cfg.venv_path){ " ^
    "  $v=[string]$cfg.venv_path; $cand=Join-Path $v 'Scripts\\python.exe'; if(Test-Path -LiteralPath $cand){ $py=$cand } " ^
    "} " ^
    "if(-not $py -and $cfg.PSObject.Properties.Name -contains 'conda_env_folder' -and $cfg.conda_env_folder){ " ^
    "  $c=[string]$cfg.conda_env_folder; $cand=Join-Path $c 'python.exe'; if(Test-Path -LiteralPath $cand){ $py=$cand } " ^
    "} " ^
    "if($py){ Write-Output $py }"`) do (
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

"%PYTHON_EXE%" >nul 2>&1
if errorlevel 1 if exist "%VENV_PY%" set "PYTHON_EXE=%VENV_PY%"

@REM echo SCRIPT_DIR = %SCRIPT_DIR%
@REM echo PYTHON_EXE = %PYTHON_EXE%

"%PYTHON_EXE%" "%SCRIPT_DIR%comfyui_pip_update_audit.py" %*

pause
