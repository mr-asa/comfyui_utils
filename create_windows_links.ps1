# =========================================================
# ComfyUI Utils — Windows Shortcuts Generator
# Fully editable list of shortcuts
# =========================================================

$ErrorActionPreference = "Stop"

$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunDir  = Join-Path $BaseDir "run_windows"
$IcoDir  = Join-Path $BaseDir "ico"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$WshShell = New-Object -ComObject WScript.Shell
$CmdExe   = Join-Path $env:SystemRoot "System32\cmd.exe"
$PwshExe  = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

# =========================================================
# EDIT HERE — shortcuts list
# =========================================================

$Shortcuts = @(
  @{
    name   = "ComfyUI Run"
    target = "run_comfyui.bat"
  },
  @{
    name   = "Clone Workflow Repos"
    target = "clone_workflow_repos_run.bat"
  },
  @{
    name   = "Pip Update Audit"
    target = "comfyui_pip_update_audit_run.bat"
  },
  @{
    name   = "Junk Links Manager"
    target = "custom_nodes_link_manager_run.bat"
  },
  @{
    name   = "Partial Repo Sync"
    target = "partial_repo_sync_run.bat"
  },
  @{
    name   = "PNG to JSON"
    target = "png_to_json_run.bat"
  },
  @{
    name   = "Update Comfy Repos"
    target = "update_comfy_repos_run.bat"
  },
  @{
    name   = "Update Workflow Repos"
    target = "update_workflow_repos_run.bat"
  }
)

# =========================================================
# DO NOT EDIT BELOW
# =========================================================

foreach ($s in $Shortcuts) {

  $lnkPath = Join-Path $RunDir ($s.name + ".lnk")
  $targetPath = Join-Path $BaseDir $s.target
  $iconName = [System.IO.Path]::ChangeExtension($s.target, ".ico")
  $iconPath = Join-Path $IcoDir $iconName

  if (-not (Test-Path $targetPath)) {
    Write-Host "SKIP (target not found): $($s.target)"
    continue
  }

  $sc = $WshShell.CreateShortcut($lnkPath)

  $shell = if ($s.ContainsKey("shell")) { $s.shell } else { "cmd" }

  switch ($shell) {
    "powershell" {
      $sc.TargetPath = $PwshExe
      $sc.Arguments  = "-NoProfile -ExecutionPolicy Bypass -File `"$($s.target)`""
    }
    default {
      $sc.TargetPath = $CmdExe
      $sc.Arguments  = "/c `"$($s.target)`""
    }
  }

  $sc.WorkingDirectory = $BaseDir
  $sc.WindowStyle = 1
  $sc.Description = $s.target

  if (Test-Path $iconPath) {
    $sc.IconLocation = $iconPath
  }

  $sc.Save()
  Write-Host "Created: $lnkPath"
}

Write-Host "`nDone. Shortcuts created in: $RunDir"
