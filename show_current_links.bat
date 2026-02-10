@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=%~1"
set "START=%~2"
set "SHOW_MODE=%~3"

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

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$repo='%CUSTOM_SRC%'; $dst='%CUSTOM_DST%'; $mode='%SHOW_MODE%';" ^
  "$repoNodes=Get-ChildItem -LiteralPath $repo -Directory | Where-Object { $_.Name -ne '.disabled' -and $_.Name -notmatch '\\.disable(d)?$' } | Select-Object -ExpandProperty Name | Sort-Object;" ^
  "$linkSet=@{}; if(Test-Path -LiteralPath $dst){" ^
  "  Get-ChildItem -LiteralPath $dst -Directory -Force | ForEach-Object {" ^
  "    $isLink = (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) -or ($_.PSObject.Properties.Name -contains 'Target' -and $_.Target);" ^
  "    if($isLink){ $linkSet[$_.Name]=$true }" ^
  "  }" ^
  "}" ^
  "$nodes=$repoNodes; if($mode -eq 'linked'){ $nodes = $repoNodes | Where-Object { $linkSet.ContainsKey($_) } } elseif($mode -eq 'unlinked'){ $nodes = $repoNodes | Where-Object { -not $linkSet.ContainsKey($_) } }" ^
  "$entries=@(); $i=1; $indexWidth=([string]$nodes.Count).Length; foreach($n in $nodes){ $mark= if($linkSet.ContainsKey($n)){'=> '} else {'   '}; $entries += ('{0}[{1}] {2}' -f $mark,$i.ToString().PadLeft($indexWidth),$n); $i++ }" ^
  "$rows=[int][Math]::Ceiling($entries.Count/2.0);" ^
  "$left=@(); $right=@();" ^
  "if($entries.Count -gt 0){ $left=$entries[0..($rows-1)]; if($entries.Count -gt $rows){ $right=$entries[$rows..($entries.Count-1)] } }" ^
  "$leftW=0; foreach($l in $left){ if($l.Length -gt $leftW){ $leftW=$l.Length } }" ^
  "$title='Current links (=> is linked)'; if($mode -eq 'linked'){ $title+=' - linked only' } elseif($mode -eq 'unlinked'){ $title+=' - unlinked only' }" ^
  "Write-Host ''; Write-Host $title;" ^
  "for($r=0;$r -lt $rows;$r++){ $l=$left[$r]; $rg= if($r -lt $right.Count){ $right[$r] } else { '' }; if($rg){ Write-Host ($l.PadRight($leftW+4) + $rg) } else { Write-Host $l } }" ^
  "Write-Host ''"

exit /b 0
