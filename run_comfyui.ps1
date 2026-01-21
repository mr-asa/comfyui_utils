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

    $defaultConfig | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
    Format-JsonWithPython -Path $ConfigPath
}

$cfg = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$rawPresets = @()
if ($cfg.PSObject.Properties.Name -contains "presets" -and $cfg.presets) {
    $rawPresets = @($cfg.presets)
}

if (-not $rawPresets -or $rawPresets.Count -eq 0) {
    Write-Host "No presets defined in config."
    Write-Output ""
    Write-Output "1"
    Write-Output ""
    exit 0
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

if ($presets.Count -eq 0) {
    Write-Host "No valid presets defined in config."
    Write-Output ""
    Write-Output "1"
    Write-Output ""
    exit 0
}

$presetNames = @($presets | ForEach-Object { $_.Name })
$currentNames = @()
if ($cfg.PSObject.Properties.Name -contains "current" -and $cfg.current) {
    if ($cfg.current -is [string]) {
        $currentNames = @([string]$cfg.current)
    } else {
        $currentNames = @($cfg.current)
    }
    $currentNames = $currentNames | Where-Object { $presetNames -contains $_ }
}

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
    $input = Read-Host "Press Enter to launch, or type any key to choose flags"

    if ([string]::IsNullOrWhiteSpace($input)) {
        $selectedNames = $currentNames
        break
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
    $input = Read-Host "Enter numbers separated by spaces"
    if ([string]::IsNullOrWhiteSpace($input)) {
        $selectedNames = $currentNames
        break
    }

    $parts = $input -split "\s+" | Where-Object { $_ }
    $indices = @()
    $bad = $false
    foreach ($p in $parts) {
        if ($p -match "^\d+$") {
            $idx = [int]$p
            if ($idx -ge 1 -and $idx -le $presets.Count) {
                $indices += $idx
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

    $indices = $indices | Sort-Object -Unique
    $selectedNames = @($indices | ForEach-Object { $presets[$_ - 1].Name })

    $cfg.current = $selectedNames
    $cfg | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
    Format-JsonWithPython -Path $ConfigPath
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
