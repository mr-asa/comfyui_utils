@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

rem =========================================================
rem Detect ComfyUI root
rem =========================================================
set "START=%~dp0"
set "ROOT="

rem Try config.json in script folder first
if exist "%START%config.json" (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%START%config.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; " ^
    "$r=$null; if($j.PSObject.Properties.Name -contains 'Comfyui_root'){ $r=$j.Comfyui_root } " ^
    "elseif($j.PSObject.Properties.Name -contains 'comfyui_root'){ $r=$j.comfyui_root } " ^
    "elseif($j.PSObject.Properties.Name -contains 'COMFYUI_ROOT'){ $r=$j.COMFYUI_ROOT } " ^
    "if($r){ $r=[string]$r; $r=$r.Trim(); if($r){ Write-Output $r } }"`) do (
    set "ROOT=%%R"
  )
  if defined ROOT (
    if not exist "%ROOT%\main.py" set "ROOT="
  )
)

if exist "%START%ComfyUI\main.py" set "ROOT=%START%ComfyUI\"
if not defined ROOT if exist "%START%main.py" set "ROOT=%START%"

if not defined ROOT goto find_root_up

goto root_ok

:find_root_up
set "ROOT=%START%"
:find_up_loop
if exist "%ROOT%main.py" goto root_ok

for %%I in ("%ROOT%.") do set "PARENT=%%~dpI"
for %%I in ("%PARENT%..") do set "UP=%%~fI"
if /I "%UP%\"=="%ROOT%" goto root_fail
set "ROOT=%UP%\"
goto find_up_loop

:root_ok
cd /d "%ROOT%"

echo ======================================
echo ComfyUI launcher - select venv
echo ======================================
echo Root: %ROOT%
echo.

rem =========================================================
rem VENV selection
rem =========================================================
set "INDEX=0"
for /f "delims=" %%D in ('dir /ad /b ".venv*" 2^>nul') do (
  if exist "%ROOT%%%D\Scripts\python.exe" (
    set /a INDEX+=1
    set "VENV_!INDEX!=%%D"
  )
)

if %INDEX%==0 goto no_venv

set "DEFAULT_INDEX=1"
set "DEFAULT_VENV="
if exist "%START%config.json" (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%START%config.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; " ^
    "$r=$null; if($j.PSObject.Properties.Name -contains 'venv_path'){ $r=$j.venv_path } " ^
    "if($r){ $r=[string]$r; $r=$r.Trim(); if($r){ Split-Path -Leaf $r } }"`) do (
    set "DEFAULT_VENV=%%R"
  )
)
if defined DEFAULT_VENV (
  for /l %%I in (1,1,%INDEX%) do (
    if /i "!VENV_%%I!"=="%DEFAULT_VENV%" set "DEFAULT_INDEX=%%I"
  )
)

for /l %%I in (1,1,%INDEX%) do (
  if "%%I"=="%DEFAULT_INDEX%" (
    echo  [%%I]* !VENV_%%I!
  ) else (
    echo  [%%I]  !VENV_%%I!
  )
)

echo.
if %INDEX%==1 (
  set "CHOICE=1"
) else (
  set /p "CHOICE=Select venv number [%DEFAULT_INDEX%]: "
  if not defined CHOICE set "CHOICE=%DEFAULT_INDEX%"
)
if not defined VENV_%CHOICE% goto bad_venv

set "VENV_DIR=!VENV_%CHOICE%!"
set "PYTHON_EXE=%ROOT%!VENV_DIR!\Scripts\python.exe"
call :save_selected_venv

echo.
echo Using venv: !VENV_DIR!
echo Python: !PYTHON_EXE!
echo.

rem =========================================================
rem Preset selection
rem =========================================================
set "PRESETS_FILE=%START%run_comfyui_presets_config.json"
if not exist "%PRESETS_FILE%" (
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%PRESETS_FILE%';" ^
    "$cfg=[ordered]@{current=@{}; all=@{mode='blacklist'; nodes=@()}; minimal=@{mode='whitelist'; nodes=@('ComfyUI-Manager')}};" ^
    "$cfg | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $p -Encoding UTF8"
)
if not exist "%PRESETS_FILE%" goto missing_presets

echo ======================================
echo Custom nodes preset - select preset
echo ======================================
echo Presets file: %PRESETS_FILE%
echo.

set "PDEFAULT_INDEX=1"
for /f "usebackq delims=" %%N in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%PRESETS_FILE%'; $j=Get-Content $p -Raw | ConvertFrom-Json; $names=@($j.PSObject.Properties.Name); " ^
  "$idx=1; foreach($n in $names){ if($n -eq 'current'){ Write-Output $idx; break } ; $idx++ }"`) do (
  set "PDEFAULT_INDEX=%%N"
)

for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%PRESETS_FILE%'; $j=Get-Content $p -Raw | ConvertFrom-Json; $names=@($j.PSObject.Properties.Name);" ^
  "$i=1; foreach($n in $names){ if($i -eq %PDEFAULT_INDEX%){ Write-Host ('[{0}]* {1}' -f $i,$n) } else { Write-Host ('[{0}]  {1}' -f $i,$n) } ; $i++ }"`) do (
  echo %%L
)

set "PRESET_COUNT="
for /f "usebackq delims=" %%C in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%PRESETS_FILE%'; $j=Get-Content $p -Raw | ConvertFrom-Json; $names=@($j.PSObject.Properties.Name); Write-Output $names.Count"`) do (
  set "PRESET_COUNT=%%C"
)
if not defined PRESET_COUNT set "PRESET_COUNT=0"

echo.
:preset_prompt
set "PCHOICE="
set /p "PCHOICE=Select preset number [%PDEFAULT_INDEX%] (?=current status, ??=edit links): "
if not defined PCHOICE set "PCHOICE=%PDEFAULT_INDEX%"
if /I "%PCHOICE%"=="?" (
  call :show_current_links
  goto preset_prompt
)
if "%PCHOICE%"=="??" (
  call :run_link_manager
  goto preset_prompt
)
set "PNUM="
set /a PNUM=%PCHOICE% 2>nul
if not defined PNUM (
  echo Invalid input. Enter a number.
  goto preset_prompt
)
if %PNUM% LSS 1 (
  echo Invalid preset number.
  goto preset_prompt
)
if %PNUM% GTR %PRESET_COUNT% (
  echo Invalid preset number.
  goto preset_prompt
)

set "PRESET_NAME="
for /f "usebackq delims=" %%N in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%PRESETS_FILE%'; $j=Get-Content $p -Raw | ConvertFrom-Json; $names=@($j.PSObject.Properties.Name); " ^
  "$idx=[int]%PNUM%; if($idx -lt 1 -or $idx -gt $names.Count){ exit 2 } ; $names[$idx-1]"`) do (
  set "PRESET_NAME=%%N"
)

if not defined PRESET_NAME goto bad_preset

echo.
echo Using preset: %PRESET_NAME%
echo.

rem =========================================================
rem current = NO-OP
rem =========================================================
if /I "%PRESET_NAME%"=="current" goto after_links

rem =========================================================
rem Apply custom nodes preset (junctions)
rem =========================================================
set "CUSTOM_SRC=%START%custom_nodes_repo"
if exist "%START%config.json" (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%START%config.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; " ^
    "$r=$null; if($j.PSObject.Properties.Name -contains 'custom_nodes_repo_path'){ $r=$j.custom_nodes_repo_path } " ^
    "if($r){ $r=[string]$r; $r=$r.Trim(); if($r){ Write-Output $r } }"`) do (
    set "CUSTOM_SRC=%%R"
  )
)
set "CUSTOM_DST=%ROOT%custom_nodes"

if not exist "%CUSTOM_DST%" mkdir "%CUSTOM_DST%" >nul 2>&1
if not exist "%CUSTOM_SRC%" call :resolve_custom_src
if not exist "%CUSTOM_SRC%" goto missing_repo

echo ======================================
echo Applying custom nodes preset
echo ======================================
echo Source: %CUSTOM_SRC%
echo Target: %CUSTOM_DST%
echo.

rem ---- cleanup ONLY junction folders in custom_nodes ----
for /f "usebackq delims=" %%C in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dst='%CUSTOM_DST%'; $count=0;" ^
  "if(Test-Path -LiteralPath $dst){" ^
  "  Get-ChildItem -LiteralPath $dst -Force | ForEach-Object {" ^
  "    if($_.PSIsContainer -and (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){" ^
  "      cmd /c ('rmdir /s /q \"{0}\"' -f $_.FullName) | Out-Null; $count++" ^
  "    }" ^
  "  }" ^
  "} else { New-Item -ItemType Directory -Path $dst | Out-Null };" ^
  "Write-Output $count"`) do (
  set "REMOVED_COUNT=%%C"
)
if errorlevel 1 goto cleanup_fail

rem ---- create junctions according to preset (PS 5.1 compatible) ----
for /f "usebackq delims=" %%C in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$presetName='%PRESET_NAME%';" ^
  "$presetPath='%PRESETS_FILE%';" ^
  "$src='%CUSTOM_SRC%';" ^
  "$dst='%CUSTOM_DST%';" ^
  "$j=Get-Content -LiteralPath $presetPath -Raw | ConvertFrom-Json;" ^
  "$p=$j.$presetName;" ^
  "if(-not $p){ throw ('Preset not found: ' + $presetName) }" ^
  "$mode='whitelist'; if($p.PSObject.Properties.Name -contains 'mode' -and $p.mode){ $mode=[string]$p.mode } ; $mode=$mode.ToLower();" ^
  "$list=@(); if($p.PSObject.Properties.Name -contains 'nodes' -and $p.nodes){ $list=@($p.nodes) }" ^
  "$all=Get-ChildItem -LiteralPath $src -Directory | Select-Object -ExpandProperty Name;" ^
  "if($mode -eq 'blacklist'){ $sel=$all | Where-Object { $list -notcontains $_ } } else { $sel=$list }" ^
  "$sel=$sel | Where-Object { $_ -and (Test-Path (Join-Path $src $_)) } | Sort-Object -Unique;" ^
  "foreach($name in $sel){" ^
  "  $from=Join-Path $src $name;" ^
  "  $to=Join-Path $dst $name;" ^
  "  if(Test-Path -LiteralPath $to){ try{ Remove-Item -LiteralPath $to -Force -Recurse } catch{} }" ^
  "  cmd /c ('mklink /J \"{0}\" \"{1}\"' -f $to,$from) | Out-Null;" ^
  "}" ^
  "Write-Output ($sel.Count)"`) do (
  set "ADDED_COUNT=%%C"
)
if errorlevel 1 goto preset_fail

echo.
echo Preset stats: removed %REMOVED_COUNT% junctions, added %ADDED_COUNT% links.
echo.
goto after_links

:resolve_custom_src
for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dst='%CUSTOM_DST%'; $root=$null;" ^
  "if(Test-Path -LiteralPath $dst){" ^
  "  Get-ChildItem -LiteralPath $dst -Force | ForEach-Object {" ^
  "    if(-not $root -and $_.PSIsContainer -and (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)){" ^
  "      try{ $t=$_.Target } catch { $t=$null };" ^
  "      if($t){ $root=Split-Path -Parent ([string]$t) }" ^
  "    }" ^
  "  }" ^
  "}" ^
  "if($root){ Write-Output $root }"`) do (
  set "CUSTOM_SRC=%%R"
)
if exist "%CUSTOM_SRC%" call :save_custom_src
if exist "%CUSTOM_SRC%" exit /b 0
echo.
echo Custom nodes repo not found. Enter path to custom_nodes_repo (or leave blank to skip):
set /p "CUSTOM_SRC="
if defined CUSTOM_SRC call :save_custom_src
exit /b 0

:save_custom_src
for /f "usebackq delims=" %%C in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%START%config.json';" ^
  "$cfg=$null; if(Test-Path -LiteralPath $p){ $cfg=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json };" ^
  "if(-not $cfg){ $cfg=[pscustomobject]@{} }" ^
  "$current=$null; if($cfg.PSObject.Properties.Name -contains 'custom_nodes_repo_path'){ $current=[string]$cfg.custom_nodes_repo_path }" ^
  "$desired='%CUSTOM_SRC%'; if($current -ne $desired){" ^
  "  $cfg | Add-Member -NotePropertyName custom_nodes_repo_path -NotePropertyValue $desired -Force;" ^
  "  $cfg | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $p -Encoding UTF8;" ^
  "  Write-Output 1" ^
  "} else { Write-Output 0 }"`) do (
  set "CONFIG_CHANGED=%%C"
)
if "%CONFIG_CHANGED%"=="1" call :format_config_json
exit /b 0

:save_selected_venv
for /f "usebackq delims=" %%C in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%START%config.json';" ^
  "$cfg=$null; if(Test-Path -LiteralPath $p){ $cfg=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json };" ^
  "if(-not $cfg){ $cfg=[pscustomobject]@{} }" ^
  "$current=$null; if($cfg.PSObject.Properties.Name -contains 'venv_path'){ $current=[string]$cfg.venv_path }" ^
  "$desired='%ROOT%%VENV_DIR%'; if($current -ne $desired){" ^
  "  $cfg | Add-Member -NotePropertyName venv_path -NotePropertyValue $desired -Force;" ^
  "  $cfg | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $p -Encoding UTF8;" ^
  "  Write-Output 1" ^
  "} else { Write-Output 0 }"`) do (
  set "CONFIG_CHANGED=%%C"
)
if "%CONFIG_CHANGED%"=="1" call :format_config_json
exit /b 0

:format_config_json
set "CONFIG_PATH=%START%config.json"
if not exist "%CONFIG_PATH%" exit /b 0
set "PY_FMT=python"
if defined PYTHON_EXE if exist "%PYTHON_EXE%" set "PY_FMT=%PYTHON_EXE%"
%PY_FMT% -c "import json,io; p=r'%CONFIG_PATH%'; j=json.load(io.open(p,'r',encoding='utf-8')); json.dump(j, io.open(p,'w',encoding='utf-8'), indent=4, ensure_ascii=False)"
exit /b 0

:run_link_manager
set "CUSTOM_DST=%ROOT%custom_nodes"
set "CUSTOM_SRC=%START%custom_nodes_repo"
if exist "%START%config.json" (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%START%config.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; " ^
    "$r=$null; if($j.PSObject.Properties.Name -contains 'custom_nodes_repo_path'){ $r=$j.custom_nodes_repo_path } " ^
    "if($r){ $r=[string]$r; $r=$r.Trim(); if($r){ Write-Output $r } }"`) do (
    set "CUSTOM_SRC=%%R"
  )
)
if not exist "%CUSTOM_DST%" mkdir "%CUSTOM_DST%" >nul 2>&1
if not exist "%CUSTOM_SRC%" call :resolve_custom_src
if not exist "%CUSTOM_SRC%" (
  echo.
  echo Custom nodes repo not found:
  echo %CUSTOM_SRC%
  echo.
  exit /b 0
)
if not exist "%START%custom_nodes_link_manager.py" (
  echo.
  echo custom_nodes_link_manager.py not found:
  echo %START%custom_nodes_link_manager.py
  echo.
  exit /b 0
)
echo.
echo Launching custom_nodes_link_manager...
echo.
"%PYTHON_EXE%" "%START%custom_nodes_link_manager.py" --repo "%CUSTOM_SRC%" --custom "%CUSTOM_DST%"
echo.
exit /b 0

:show_current_links
set "CUSTOM_DST=%ROOT%custom_nodes"
set "CUSTOM_SRC=%START%custom_nodes_repo"
if exist "%START%config.json" (
  for /f "usebackq delims=" %%R in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$p='%START%config.json'; $j=Get-Content -LiteralPath $p -Raw | ConvertFrom-Json; " ^
    "$r=$null; if($j.PSObject.Properties.Name -contains 'custom_nodes_repo_path'){ $r=$j.custom_nodes_repo_path } " ^
    "if($r){ $r=[string]$r; $r=$r.Trim(); if($r){ Write-Output $r } }"`) do (
    set "CUSTOM_SRC=%%R"
  )
)
if not exist "%CUSTOM_SRC%" (
  echo.
  echo Custom nodes repo not found:
  echo %CUSTOM_SRC%
  echo.
  exit /b 0
)
if not exist "%CUSTOM_DST%" (
  echo.
  echo custom_nodes not found:
  echo %CUSTOM_DST%
  echo.
  exit /b 0
)
for /f "usebackq delims=" %%L in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$repo='%CUSTOM_SRC%'; $dst='%CUSTOM_DST%';" ^
  "$repoNodes=Get-ChildItem -LiteralPath $repo -Directory | Where-Object { $_.Name -ne '.disabled' -and $_.Name -notmatch '\\.disable(d)?$' } | Select-Object -ExpandProperty Name | Sort-Object;" ^
  "$linkSet=@{}; if(Test-Path -LiteralPath $dst){" ^
  "  Get-ChildItem -LiteralPath $dst -Directory -Force | ForEach-Object {" ^
  "    $isLink = (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or ($_.PSObject.Properties.Name -contains 'Target' -and $_.Target);" ^
  "    if($isLink){ $linkSet[$_.Name]=$true }" ^
  "  }" ^
  "}" ^
  "$entries=@(); $i=1; $indexWidth=([string]$repoNodes.Count).Length; foreach($n in $repoNodes){ $mark= if($linkSet.ContainsKey($n)){'=> '} else {'   '}; $entries += ('{0}[{1}] {2}' -f $mark,$i.ToString().PadLeft($indexWidth),$n); $i++ }" ^
  "$rows=[int][Math]::Ceiling($entries.Count/2.0);" ^
  "$left=@(); $right=@();" ^
  "if($entries.Count -gt 0){ $left=$entries[0..($rows-1)]; if($entries.Count -gt $rows){ $right=$entries[$rows..($entries.Count-1)] } }" ^
  "$leftW=0; foreach($l in $left){ if($l.Length -gt $leftW){ $leftW=$l.Length } }" ^
  "Write-Host ''; Write-Host 'Current links (=> is linked)';" ^
  "for($r=0;$r -lt $rows;$r++){ $l=$left[$r]; $rg= if($r -lt $right.Count){ $right[$r] } else { '' }; if($rg){ Write-Host ($l.PadRight($leftW+4) + $rg) } else { Write-Host $l } }" ^
  "Write-Host ''"`) do (
  echo %%L
)
exit /b 0

:after_links
rem =========================================================
rem Launch ComfyUI
rem =========================================================
set "FLAGS_SCRIPT=%START%run_comfyui.ps1"
set "FLAGS_CONFIG=%START%run_comfyui_flags_config.json"
set "FLAGS_OUT=%TEMP%\comfyui_flags_out.txt"
set "COMFY_ARGS="
set "UPDATE_FRONTEND=1"
set "FLAGS_PRESETS="
powershell -NoProfile -ExecutionPolicy Bypass -File "%FLAGS_SCRIPT%" -ConfigPath "%FLAGS_CONFIG%" -OutPath "%FLAGS_OUT%"
if errorlevel 1 goto flags_fail
if not exist "%FLAGS_OUT%" goto flags_fail
for /f "usebackq tokens=1* delims==" %%A in ("%FLAGS_OUT%") do (
  if /I "%%A"=="COMFY_ARGS" set "COMFY_ARGS=%%B"
  if /I "%%A"=="UPDATE_FRONTEND" set "UPDATE_FRONTEND=%%B"
  if /I "%%A"=="FLAGS_PRESETS" set "FLAGS_PRESETS=%%B"
)
del /q "%FLAGS_OUT%" >nul 2>&1

echo.
echo Selected presets: %FLAGS_PRESETS%
echo Launch args: %COMFY_ARGS%
echo.

if "%UPDATE_FRONTEND%"=="1" (
  echo ======================================
  echo Updating ComfyUI frontend packages
  echo ======================================
  echo.

  "!PYTHON_EXE!" -m pip --version >nul 2>&1
  if errorlevel 1 goto no_pip

  "!PYTHON_EXE!" -m pip install -U pip
  if errorlevel 1 goto pip_upgrade_fail

  "!PYTHON_EXE!" -m pip install -U comfyui-frontend-package comfyui-workflow-templates comfyui-embedded-docs
  if errorlevel 1 goto pkgs_fail

  echo.
  echo Packages updated.
  echo.
) else (
  echo ======================================
  echo Skipping ComfyUI frontend package update
  echo ======================================
  echo.
)
"!PYTHON_EXE!" "%ROOT%main.py" %COMFY_ARGS%
pause
exit /b 0

rem =========================================================
rem Error handlers
rem =========================================================
:no_venv
echo.
echo ERROR: No valid venv found in %ROOT%
echo Expected: .venv*\Scripts\python.exe
echo.
dir /ad /b ".venv*" 2>nul
pause
exit /b 1

:bad_venv
echo.
echo ERROR: Invalid venv selection
pause
exit /b 1

:missing_presets
echo.
echo ERROR: Missing presets file:
echo %PRESETS_FILE%
pause
exit /b 1

:bad_preset
echo.
echo ERROR: Invalid preset selection
pause
exit /b 1

:missing_repo
echo.
echo WARNING: custom_nodes_repo not found:
echo %CUSTOM_SRC%
echo Skipping custom node linking.
goto after_links

:cleanup_fail
echo.
echo ERROR: Failed to cleanup junctions in custom_nodes
pause
exit /b 1

:preset_fail
echo.
echo ERROR: Failed to apply preset (check JSON / folder names).
pause
exit /b 1

:no_pip
echo.
echo ERROR: pip is not available in this venv.
echo Try: "!PYTHON_EXE!" -m ensurepip --upgrade
pause
exit /b 1

:pip_upgrade_fail
echo.
echo ERROR: Failed to upgrade pip.
pause
exit /b 1

:pkgs_fail
echo.
echo ERROR: Failed to update one or more packages.
pause
exit /b 1

:flags_fail
echo.
echo ERROR: Failed to resolve launch flags.
pause
exit /b 1

:root_fail
echo ======================================
echo ComfyUI launcher
echo ======================================
echo.
echo ERROR: main.py not found in .\ComfyUI\, current folder, or any parent folders.
pause
exit /b 1
