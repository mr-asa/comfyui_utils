param(
    [string]$ConfigPath,
    [string]$OutPath
)

$ErrorActionPreference = "Stop"

function Format-JsonWithPython {
    param([string]$Path)
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        return
    }
    try {
        $cmd = "import json,io; p=r'$Path'; j=json.load(io.open(p,'r',encoding='utf-8')); json.dump(j, io.open(p,'w',encoding='utf-8'), indent=2, ensure_ascii=False)"
        & $py.Source -c $cmd 2>$null | Out-Null
    } catch {
        # Ignore formatting errors; config content is still valid JSON.
    }
}

function Save-Config {
    param(
        [Parameter(Mandatory = $true)][object]$Config,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $Config | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Path -Encoding UTF8
    Format-JsonWithPython -Path $Path
}

function Get-PresetsFromConfig {
    param([object]$Config)
    $rawPresets = @()
    if ($Config.PSObject.Properties.Name -contains "presets" -and $Config.presets) {
        $rawPresets = @($Config.presets)
    }
    $presets = @()
    foreach ($entry in $rawPresets) {
        if ($entry -is [string]) {
            $text = [string]$entry
            $parts = $text -split "\|", 2
            $name = $parts[0].Trim()
            $keys = ""
            if ($parts.Count -gt 1) {
                $keys = $parts[1].Trim()
            }
            if ($name) {
                $presets += [pscustomobject]@{
                    Name = $name
                    Keys = $keys
                    Comment = ""
                }
            }
        } else {
            $name = ""
            $keys = ""
            $comment = ""
            if ($entry.PSObject.Properties.Name -contains "name" -and $entry.name) {
                $name = [string]$entry.name
            }
            if ($entry.PSObject.Properties.Name -contains "keys" -and $entry.keys) {
                $keys = [string]$entry.keys
            }
            if ($entry.PSObject.Properties.Name -contains "comment" -and $entry.comment) {
                $comment = [string]$entry.comment
            }
            if ($name) {
                $presets += [pscustomobject]@{
                    Name = $name
                    Keys = $keys
                    Comment = $comment
                }
            }
        }
    }
    return ,$presets
}

function Set-PresetsInConfig {
    param(
        [Parameter(Mandatory = $true)][object]$Config,
        [Parameter(Mandatory = $true)][object[]]$Presets
    )
    $list = @()
    foreach ($p in $Presets) {
        $list += [ordered]@{
            name = [string]$p.Name
            keys = [string]$p.Keys
            comment = [string]$p.Comment
        }
    }
    $Config.presets = $list
}

function Reload-ConfigState {
    param([string]$Path)
    $cfgLocal = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $presetsLocal = Get-PresetsFromConfig -Config $cfgLocal
    $presetNamesLocal = @($presetsLocal | ForEach-Object { $_.Name })
    $currentNamesLocal = @()
    if ($cfgLocal.PSObject.Properties.Name -contains "current" -and $cfgLocal.current) {
        if ($cfgLocal.current -is [string]) {
            $currentNamesLocal = @([string]$cfgLocal.current)
        } else {
            $currentNamesLocal = @($cfgLocal.current)
        }
        $currentNamesLocal = @($currentNamesLocal | Where-Object { $presetNamesLocal -contains $_ })
    }
    return [pscustomobject]@{
        Config = $cfgLocal
        Presets = $presetsLocal
        PresetNames = $presetNamesLocal
        CurrentNames = $currentNamesLocal
    }
}

function Resolve-SelectionInput {
    param(
        [string]$InputText,
        [object[]]$Presets,
        [string[]]$CurrentNames
    )

    $trimmed = [string]$InputText
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        return [pscustomobject]@{
            Ok = $true
            Mode = "keep"
            Names = @($CurrentNames)
            Error = ""
        }
    }

    $parts = @($trimmed -split "\s+" | Where-Object { $_ })
    $mode = "replace"
    if ($parts.Count -gt 0 -and ($parts[0] -eq "+" -or $parts[0] -eq "-")) {
        $mode = if ($parts[0] -eq "+") { "add" } else { "remove" }
        $parts = @($parts | Select-Object -Skip 1)
    }

    if ($parts.Count -eq 0) {
        return [pscustomobject]@{
            Ok = $false
            Mode = $mode
            Names = @($CurrentNames)
            Error = "Use preset numbers. Examples: 1 2 3, + 5 7, - 2"
        }
    }

    $indices = @()
    foreach ($part in $parts) {
        if ($part -notmatch "^\d+$") {
            return [pscustomobject]@{
                Ok = $false
                Mode = $mode
                Names = @($CurrentNames)
                Error = "Invalid selection. Use numbers from the list. Examples: 1 2 3, + 5 7, - 2"
            }
        }

        $index = [int]$part
        if ($index -lt 1 -or $index -gt $Presets.Count) {
            return [pscustomobject]@{
                Ok = $false
                Mode = $mode
                Names = @($CurrentNames)
                Error = "Invalid selection. Use numbers from the list. Examples: 1 2 3, + 5 7, - 2"
            }
        }
        $indices += $index
    }

    $indices = @($indices | Sort-Object -Unique)
    $inputNames = @($indices | ForEach-Object { $Presets[$_ - 1].Name })

    switch ($mode) {
        "add" {
            $newNames = @($CurrentNames + $inputNames | Select-Object -Unique)
        }
        "remove" {
            $newNames = @($CurrentNames | Where-Object { $inputNames -notcontains $_ })
        }
        default {
            $newNames = @($inputNames)
        }
    }

    return [pscustomobject]@{
        Ok = $true
        Mode = $mode
        Names = $newNames
        Error = ""
    }
}

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot "run_comfyui_flags_config.json"
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    $defaultConfig = [ordered]@{
        current = @(
            "frontend_latest",
            "preview_latent2rgb",
            "preview_size_512"
        )
        presets = @(
            [ordered]@{
                name = "frontend_latest"
                keys = "--front-end-version Comfy-Org/ComfyUI_frontend@latest"
                comment = ""
            },
            [ordered]@{
                name = "preview_latent2rgb"
                keys = "--preview-method latent2rgb"
                comment = ""
            },
            [ordered]@{
                name = "preview_size_512"
                keys = "--preview-size 512"
                comment = ""
            },
            [ordered]@{
                name = "skip_frontend_update"
                keys = "@no_update"
                comment = "Skip frontend package update"
            }
        )
    }

    Save-Config -Config $defaultConfig -Path $ConfigPath
}

$state = Reload-ConfigState -Path $ConfigPath
$cfg = $state.Config
$presets = $state.Presets
$presetNames = $state.PresetNames
$currentNames = $state.CurrentNames

Write-Host "======================================"
Write-Host "Run flags preset - select preset"
Write-Host "======================================"
Write-Host ("Config: {0}" -f $ConfigPath)
Write-Host ""

$currentArgs = @()
foreach ($p in $presets) {
    if ($currentNames -contains $p.Name) {
        $args = $p.Keys
        $args = ($args -replace "(^|\s)@no_update(\s|$)", " ") -replace "(^|\s)@skip_update(\s|$)", " "
        $args = $args.Trim()
        if ($args) {
            $currentArgs += $args
        }
    }
}
$currentArgsText = ($currentArgs -join " ").Trim()
if (-not $currentArgsText) {
    $currentArgsText = "(none)"
}
Write-Host ("current: {0}" -f $currentArgsText)
Write-Host ""

while ($true) {
    Write-Host ""
    $input = Read-Host "Press Enter to launch, or type any key to choose/edit flags"

    if ([string]::IsNullOrWhiteSpace($input)) {
        if ($presets.Count -eq 0) {
            Write-Host "No presets defined. Use A to add a preset."
            continue
        } else {
            $selectedNames = $currentNames
            break
        }
    }

    if ($presets.Count -eq 0) {
        $input = Read-Host "Presets list is empty (A=add, Q=cancel)"
        if ($input -match "^[Qq]$") {
            $selectedNames = $currentNames
            break
        }
        if ($input -notmatch "^[Aa]$") {
            continue
        }
    }

    $indexWidth = ([string]$presets.Count).Length + 2
    $nameWidth = [Math]::Max(4, ($presets | ForEach-Object { $_.Name.Length } | Measure-Object -Maximum).Maximum)
    $keysWidth = [Math]::Max(4, ($presets | ForEach-Object { $_.Keys.Length } | Measure-Object -Maximum).Maximum)
    Write-Host ("{0} {1} {2} {3}" -f "No".PadRight($indexWidth), "Name".PadRight($nameWidth), "Keys".PadRight($keysWidth), "Comment")
    Write-Host ("{0} {1} {2} {3}" -f ("-" * ($indexWidth - 1)), ("-" * $nameWidth), ("-" * $keysWidth), "-------")

    $i = 1
    foreach ($p in $presets) {
        $isCurrent = $currentNames -contains $p.Name
        $mark = if ($isCurrent) { "*" } else { " " }
        $idxText = ("[{0}]{1}" -f $i, $mark).PadRight($indexWidth)
        Write-Host ("{0} {1} {2} {3}" -f $idxText, $p.Name.PadRight($nameWidth), $p.Keys.PadRight($keysWidth), $p.Comment)
        $i++
    }
    Write-Host ""
    $input = Read-Host "Enter numbers to replace, or + / - to add/remove (examples: 1 2 3, + 5 7, - 2) (A=add, E=edit, D=delete, Q=cancel)"
    if ([string]::IsNullOrWhiteSpace($input)) {
        $selectedNames = $currentNames
        break
    }
    if ($input -match "^[Aa]$") {
        $newName = Read-Host "New preset name"
        if ([string]::IsNullOrWhiteSpace($newName)) {
            Write-Host "Name is required."
            continue
        }
        $newKeys = Read-Host "Flags (keys)"
        $newComment = Read-Host "Comment (optional)"
        $existing = $presets | Where-Object { $_.Name -eq $newName }
        if ($existing) {
            $confirm = Read-Host "Preset exists. Overwrite? (y/N)"
            if ($confirm -notmatch "^[Yy]$") {
                continue
            }
            $presets = @($presets | Where-Object { $_.Name -ne $newName })
        }
        $presets += [pscustomobject]@{
            Name = $newName
            Keys = $newKeys
            Comment = $newComment
        }
        Set-PresetsInConfig -Config $cfg -Presets $presets
        Save-Config -Config $cfg -Path $ConfigPath
        $state = Reload-ConfigState -Path $ConfigPath
        $cfg = $state.Config
        $presets = $state.Presets
        $presetNames = $state.PresetNames
        $currentNames = $state.CurrentNames
        continue
    }
    if ($input -match "^[Ee]$") {
        if ($presets.Count -eq 0) {
            Write-Host "No presets to edit."
            continue
        }
        $editInput = Read-Host "Enter preset number to edit"
        if ($editInput -notmatch "^\d+$") {
            Write-Host "Invalid selection. Use a number from the list."
            continue
        }
        $editIdx = [int]$editInput
        if ($editIdx -lt 1 -or $editIdx -gt $presets.Count) {
            Write-Host "Invalid selection. Use a number from the list."
            continue
        }
        $target = $presets[$editIdx - 1]
        Write-Host ("Editing preset: {0}" -f $target.Name)
        $newName = Read-Host ("New name (Enter to keep: {0})" -f $target.Name)
        if ([string]::IsNullOrWhiteSpace($newName)) {
            $newName = $target.Name
        }
        $newKeys = Read-Host ("New flags (Enter to keep: {0})" -f $target.Keys)
        if ([string]::IsNullOrWhiteSpace($newKeys)) {
            $newKeys = $target.Keys
        }
        $newComment = Read-Host ("New comment (Enter to keep: {0})" -f $target.Comment)
        if ([string]::IsNullOrWhiteSpace($newComment)) {
            $newComment = $target.Comment
        }
        $oldName = $target.Name
        if ($newName -ne $target.Name) {
            $collision = $presets | Where-Object { $_.Name -eq $newName }
            if ($collision) {
                Write-Host "Preset with this name already exists."
                continue
            }
        }
        $target.Name = $newName
        $target.Keys = $newKeys
        $target.Comment = $newComment
        $presets[$editIdx - 1] = $target
        Set-PresetsInConfig -Config $cfg -Presets $presets
        if ($currentNames -contains $oldName) {
            $currentNames = @($currentNames | ForEach-Object { if ($_ -eq $oldName) { $newName } else { $_ } })
            $cfg.current = $currentNames
        }
        Save-Config -Config $cfg -Path $ConfigPath
        $state = Reload-ConfigState -Path $ConfigPath
        $cfg = $state.Config
        $presets = $state.Presets
        $presetNames = $state.PresetNames
        $currentNames = $state.CurrentNames
        continue
    }
    if ($input -match "^[Dd]$") {
        $delInput = Read-Host "Enter numbers to delete separated by spaces"
        if ([string]::IsNullOrWhiteSpace($delInput)) {
            continue
        }
        $delParts = $delInput -split "\s+" | Where-Object { $_ }
        $delIdx = @()
        $bad = $false
        foreach ($p in $delParts) {
            if ($p -match "^\d+$") {
                $idx = [int]$p
                if ($idx -ge 1 -and $idx -le $presets.Count) {
                    $delIdx += $idx
                } else {
                    $bad = $true
                }
            } else {
                $bad = $true
            }
        }
        if ($bad) {
            Write-Host "Invalid selection. Use numbers from the list."
            continue
        }
        $delIdx = $delIdx | Sort-Object -Unique
        $delNames = @($delIdx | ForEach-Object { $presets[$_ - 1].Name })
        if ($delNames.Count -gt 0) {
            $presets = @($presets | Where-Object { $delNames -notcontains $_.Name })
            $currentNames = @($currentNames | Where-Object { $delNames -notcontains $_ })
            $cfg.current = $currentNames
            Set-PresetsInConfig -Config $cfg -Presets $presets
            Save-Config -Config $cfg -Path $ConfigPath
        }
        $state = Reload-ConfigState -Path $ConfigPath
        $cfg = $state.Config
        $presets = $state.Presets
        $presetNames = $state.PresetNames
        $currentNames = $state.CurrentNames
        continue
    }
    if ($input -match "^[Qq]$") {
        $selectedNames = $currentNames
        break
    }

    $selection = Resolve-SelectionInput -InputText $input -Presets $presets -CurrentNames $currentNames
    if (-not $selection.Ok) {
        Write-Host $selection.Error
        continue
    }

    $selectedNames = @($selection.Names)

    $cfg.current = $selectedNames
    Save-Config -Config $cfg -Path $ConfigPath
    break
}

$updateFrontend = $true
$selectedArgs = @()
foreach ($p in $presets) {
    if ($selectedNames -contains $p.Name) {
        $args = $p.Keys
        if ($args -match "(^|\s)@no_update(\s|$)" -or $args -match "(^|\s)@skip_update(\s|$)") {
            $updateFrontend = $false
            $args = ($args -replace "(^|\s)@no_update(\s|$)", " ") -replace "(^|\s)@skip_update(\s|$)", " "
            $args = $args.Trim()
        }
        if ($args) {
            $selectedArgs += $args
        }
    }
}

$selectedArgsText = ($selectedArgs -join " ").Trim()
$presetText = $selectedNames -join ", "
$updateValue = if ($updateFrontend) { "1" } else { "0" }

if ($OutPath) {
    $lines = @(
        ("COMFY_ARGS={0}" -f $selectedArgsText),
        ("UPDATE_FRONTEND={0}" -f $updateValue),
        ("FLAGS_PRESETS={0}" -f $presetText)
    )
    $lines | Set-Content -LiteralPath $OutPath -Encoding UTF8
} else {
    Write-Output $selectedArgsText
    Write-Output $updateValue
    Write-Output $presetText
}
