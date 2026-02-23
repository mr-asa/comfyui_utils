param(
    [string]$SettingsPath = ".\compare_venv_versions_config.json",
    [string]$ConfigPath,
    [string[]]$VenvPaths
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PythonExecutable {
    param([string]$VenvPath)

    if ([string]::IsNullOrWhiteSpace($VenvPath)) {
        return $null
    }

    if (Test-Path -LiteralPath $VenvPath -PathType Leaf) {
        if ([System.IO.Path]::GetFileName($VenvPath).ToLowerInvariant() -eq "python.exe") {
            return (Resolve-Path -LiteralPath $VenvPath).Path
        }
    }

    $candidate = Join-Path $VenvPath "Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        return (Resolve-Path -LiteralPath $candidate).Path
    }

    return $null
}

function Get-ConfigVenvPaths {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Config file not found: $Path"
    }

    $cfg = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $fromList = @()
    if ($cfg.PSObject.Properties.Name -contains "venv_paths" -and $cfg.venv_paths) {
        $fromList = @($cfg.venv_paths)
    }

    if ($fromList.Count -gt 0) {
        return $fromList
    }

    if ($cfg.PSObject.Properties.Name -contains "venv_path" -and $cfg.venv_path) {
        return @([string]$cfg.venv_path)
    }

    throw "No venv_paths or venv_path found in config: $Path"
}

function Get-Settings {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        $defaultSettings = [ordered]@{
            _documentation = "See compare_venv_versions_README_RU.md"
            source_config_path = ".\config.json"
            venv_paths = @()
            include_python = $true
            include_cuda_from_torch = $true
            modules = @(
                "torch",
                "xformers",
                "triton",
                "transformers"
            )
            custom_checks = @(
                @{
                    name = "FlashAttention"
                    kind = "module"
                    candidates = @("flash-attn", "flash_attn")
                },
                @{
                    name = "SageAttention"
                    kind = "module"
                    candidates = @("sageattention", "sage_attention", "sage-attention")
                }
            )
        }

        $defaultSettings | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
        Write-Host "Created default settings: $Path"
    }

    return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}

function Get-ChecksFromSettings {
    param([object]$Settings)

    $checksByName = @{}
    $baseOrder = New-Object System.Collections.Generic.List[string]

    if ($Settings.PSObject.Properties.Name -contains "include_python" -and [bool]$Settings.include_python) {
        $name = "Python"
        $checksByName[$name] = @{
            Name = $name
            Kind = "python"
            Candidates = @()
        }
        [void]$baseOrder.Add($name)
    }

    if ($Settings.PSObject.Properties.Name -contains "include_cuda_from_torch" -and [bool]$Settings.include_cuda_from_torch) {
        $name = "CUDA"
        $checksByName[$name] = @{
            Name = $name
            Kind = "cuda_from_torch"
            Candidates = @()
        }
        [void]$baseOrder.Add($name)
    }

    if ($Settings.PSObject.Properties.Name -contains "modules" -and $Settings.modules) {
        foreach ($moduleName in @($Settings.modules)) {
            $moduleText = [string]$moduleName
            if ([string]::IsNullOrWhiteSpace($moduleText)) {
                continue
            }
            $checksByName[$moduleText] = @{
                Name = $moduleText
                Kind = "module"
                Candidates = @($moduleText)
            }
        }
    }

    if ($Settings.PSObject.Properties.Name -contains "custom_checks" -and $Settings.custom_checks) {
        foreach ($item in @($Settings.custom_checks)) {
            $name = [string]$item.name
            $kind = [string]$item.kind
            $candidates = @()
            if ($item.PSObject.Properties.Name -contains "candidates" -and $item.candidates) {
                $candidates = @($item.candidates)
            }

            if (-not [string]::IsNullOrWhiteSpace($name) -and -not [string]::IsNullOrWhiteSpace($kind)) {
                $checksByName[$name] = @{
                    Name = $name
                    Kind = $kind
                    Candidates = $candidates
                }
            }
        }
    }

    $allNames = @($checksByName.Keys)
    $baseNames = @($baseOrder | Where-Object { $checksByName.ContainsKey($_) })
    $baseSet = @{}
    foreach ($n in $baseNames) {
        $baseSet[$n] = $true
    }

    $otherNames = @(
        $allNames |
        Where-Object { -not $baseSet.ContainsKey($_) } |
        Sort-Object
    )

    $checks = @()
    foreach ($n in $baseNames) {
        $checks += $checksByName[$n]
    }
    foreach ($n in $otherNames) {
        $checks += $checksByName[$n]
    }
    return $checks
}

function Get-CommonPathPrefix {
    param([string[]]$Paths)

    if (-not $Paths -or $Paths.Count -eq 0) {
        return ""
    }

    $prefix = [string]$Paths[0]
    foreach ($p in $Paths) {
        $cur = [string]$p
        while ($prefix.Length -gt 0 -and -not $cur.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $prefix = $prefix.Substring(0, $prefix.Length - 1)
        }
        if ($prefix.Length -eq 0) {
            break
        }
    }

    if ($prefix.Length -eq 0) {
        return ""
    }

    $lastSlash = [Math]::Max($prefix.LastIndexOf("\"), $prefix.LastIndexOf("/"))
    if ($lastSlash -ge 0) {
        return $prefix.Substring(0, $lastSlash + 1)
    }
    return ""
}

function Get-ShortVenvNames {
    param([string[]]$FullPaths)

    $prefix = Get-CommonPathPrefix -Paths $FullPaths
    $short = @()
    foreach ($p in $FullPaths) {
        $name = [string]$p
        if ($prefix -and $name.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $name = $name.Substring($prefix.Length)
        }
        if ([string]::IsNullOrWhiteSpace($name)) {
            $name = Split-Path -Path $p -Leaf
        }
        if ([string]::IsNullOrWhiteSpace($name)) {
            $name = $p
        }
        $short += $name
    }

    $result = @()
    $countByName = @{}
    foreach ($name in $short) {
        if (-not $countByName.ContainsKey($name)) {
            $countByName[$name] = 0
        }
        $countByName[$name]++
        if ($countByName[$name] -eq 1) {
            $result += $name
        } else {
            $result += ("{0}#{1}" -f $name, $countByName[$name])
        }
    }
    return $result
}

function Get-VenvReport {
    param(
        [string]$PythonExe,
        [object[]]$Checks
    )

    $checksJson = $Checks | ConvertTo-Json -Compress -Depth 6
    $probeScript = @'
import json
import sys
import importlib
import importlib.metadata

with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    checks = json.load(f)
result = {}

def get_module_version(candidates):
    for name in candidates:
        try:
            return importlib.metadata.version(name)
        except Exception:
            pass
        try:
            m = importlib.import_module(name)
            v = getattr(m, "__version__", None)
            if v:
                return str(v)
        except Exception:
            pass
    return "-"

for c in checks:
    name = c["Name"]
    kind = c["Kind"]
    if kind == "python":
        result[name] = sys.version.split()[0]
    elif kind == "cuda_from_torch":
        try:
            import torch
            result[name] = str(torch.version.cuda or "-")
        except Exception:
            result[name] = "-"
    elif kind == "module":
        result[name] = get_module_version(c.get("Candidates", []))
    else:
        result[name] = "?"

print(json.dumps(result, ensure_ascii=True))
'@

    $tmpJson = $null
    $tmpPy = $null
    try {
        $tmpJson = New-TemporaryFile
        $tmpPy = [System.IO.Path]::ChangeExtension((New-TemporaryFile).FullName, ".py")
        Set-Content -LiteralPath $tmpJson.FullName -Value $checksJson -Encoding UTF8
        Set-Content -LiteralPath $tmpPy -Value $probeScript -Encoding UTF8
        $jsonLine = & $PythonExe $tmpPy $tmpJson.FullName 2>$null
        if (-not $jsonLine) {
            return $null
        }
        return $jsonLine | ConvertFrom-Json
    } catch {
        return $null
    } finally {
        if ($tmpJson -and (Test-Path -LiteralPath $tmpJson.FullName)) {
            Remove-Item -LiteralPath $tmpJson.FullName -Force -ErrorAction SilentlyContinue
        }
        if ($tmpPy -and (Test-Path -LiteralPath $tmpPy)) {
            Remove-Item -LiteralPath $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }
}

$settings = Get-Settings -Path $SettingsPath
$Checks = Get-ChecksFromSettings -Settings $settings
if ($Checks.Count -eq 0) {
    throw "No checks found. Fill modules/custom_checks in settings: $SettingsPath"
}

$resolvedConfigPath = if ($ConfigPath) {
    $ConfigPath
} elseif ($settings.PSObject.Properties.Name -contains "source_config_path" -and $settings.source_config_path) {
    [string]$settings.source_config_path
} else {
    ".\config.json"
}

$venvs = if ($VenvPaths -and $VenvPaths.Count -gt 0) {
    $VenvPaths
} elseif ($settings.PSObject.Properties.Name -contains "venv_paths" -and $settings.venv_paths -and @($settings.venv_paths).Count -gt 0) {
    @($settings.venv_paths)
} else {
    Get-ConfigVenvPaths -Path $resolvedConfigPath
}

$venvList = @($venvs)
$shortNames = Get-ShortVenvNames -FullPaths $venvList
$venvEntries = @()
$totalVenvs = @($venvs).Count
$effectiveJobs = [Math]::Min([Math]::Max([Environment]::ProcessorCount, 2), 8)
if ($effectiveJobs -lt 1) { $effectiveJobs = 1 }
if ($totalVenvs -gt 0) {
    $effectiveJobs = [Math]::Min($effectiveJobs, $totalVenvs)
}

$workItems = @()
for ($i = 0; $i -lt $venvList.Count; $i++) {
    $workItems += [PSCustomObject]@{
        Index = $i
        Path = [string]$venvList[$i]
        Name = [string]$shortNames[$i]
    }
}

$jobScript = {
    param(
        [int]$Index,
        [string]$VenvPath,
        [string]$DisplayName,
        [object[]]$Checks
    )

    function Get-PythonExecutableLocal {
        param([string]$Path)
        if ([string]::IsNullOrWhiteSpace($Path)) {
            return $null
        }
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            if ([System.IO.Path]::GetFileName($Path).ToLowerInvariant() -eq "python.exe") {
                return (Resolve-Path -LiteralPath $Path).Path
            }
        }
        $candidate = Join-Path $Path "Scripts\python.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        return $null
    }

    function Get-VenvReportLocal {
        param(
            [string]$PythonExe,
            [object[]]$ChecksParam
        )
        $checksJson = $ChecksParam | ConvertTo-Json -Compress -Depth 6
        $probeScript = @'
import json
import sys
import importlib
import importlib.metadata

with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
    checks = json.load(f)
result = {}

def get_module_version(candidates):
    for name in candidates:
        try:
            return importlib.metadata.version(name)
        except Exception:
            pass
        try:
            m = importlib.import_module(name)
            v = getattr(m, "__version__", None)
            if v:
                return str(v)
        except Exception:
            pass
    return "-"

for c in checks:
    name = c["Name"]
    kind = c["Kind"]
    if kind == "python":
        result[name] = sys.version.split()[0]
    elif kind == "cuda_from_torch":
        try:
            import torch
            result[name] = str(torch.version.cuda or "-")
        except Exception:
            result[name] = "-"
    elif kind == "module":
        result[name] = get_module_version(c.get("Candidates", []))
    else:
        result[name] = "?"

print(json.dumps(result, ensure_ascii=True))
'@
        $tmpJson = $null
        $tmpPy = $null
        try {
            $tmpJson = New-TemporaryFile
            $tmpPy = [System.IO.Path]::ChangeExtension((New-TemporaryFile).FullName, ".py")
            Set-Content -LiteralPath $tmpJson.FullName -Value $checksJson -Encoding UTF8
            Set-Content -LiteralPath $tmpPy -Value $probeScript -Encoding UTF8
            $jsonLine = & $PythonExe $tmpPy $tmpJson.FullName 2>$null
            if (-not $jsonLine) {
                return $null
            }
            return $jsonLine | ConvertFrom-Json
        } catch {
            return $null
        } finally {
            if ($tmpJson -and (Test-Path -LiteralPath $tmpJson.FullName)) {
                Remove-Item -LiteralPath $tmpJson.FullName -Force -ErrorAction SilentlyContinue
            }
            if ($tmpPy -and (Test-Path -LiteralPath $tmpPy)) {
                Remove-Item -LiteralPath $tmpPy -Force -ErrorAction SilentlyContinue
            }
        }
    }

    $values = @{}
    $pythonExe = Get-PythonExecutableLocal -Path $VenvPath
    if (-not $pythonExe) {
        foreach ($check in $Checks) {
            $values[$check.Name] = "python.exe not found"
        }
    } else {
        $report = Get-VenvReportLocal -PythonExe $pythonExe -ChecksParam $Checks
        if (-not $report) {
            foreach ($check in $Checks) {
                $values[$check.Name] = "probe failed"
            }
        } else {
            foreach ($check in $Checks) {
                $value = $report.PSObject.Properties[$check.Name].Value
                if ([string]::IsNullOrWhiteSpace([string]$value)) {
                    $values[$check.Name] = "-"
                } else {
                    $values[$check.Name] = [string]$value
                }
            }
        }
    }

    return [PSCustomObject]@{
        Index = $Index
        Path = $VenvPath
        Name = $DisplayName
        Values = $values
    }
}

$activeJobs = New-Object System.Collections.Generic.List[object]
$jobMeta = @{}
$resultsByIndex = @{}
$nextToStart = 0
$completed = 0

while ($completed -lt $workItems.Count) {
    while ($nextToStart -lt $workItems.Count -and $activeJobs.Count -lt $effectiveJobs) {
        $item = $workItems[$nextToStart]
        $job = Start-Job -ScriptBlock $jobScript -ArgumentList @($item.Index, $item.Path, $item.Name, $Checks)
        $activeJobs.Add($job) | Out-Null
        $jobMeta[$job.Id] = $item
        $nextToStart++
    }

    $doneJobs = @(
        $activeJobs |
        Where-Object { $_.State -in @("Completed", "Failed", "Stopped") }
    )
    if ($doneJobs.Count -eq 0) {
        Start-Sleep -Milliseconds 150
        continue
    }

    foreach ($job in $doneJobs) {
        $item = $jobMeta[$job.Id]
        $activeJobs.Remove($job) | Out-Null
        $entry = $null
        try {
            $entry = Receive-Job -Job $job -ErrorAction Stop
        } catch {
            $entry = $null
        } finally {
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }

        if ($entry) {
            $first = @($entry)[0]
            $rawValues = $first.Values
            $values = @{}
            if ($rawValues -is [hashtable]) {
                foreach ($k in $rawValues.Keys) {
                    $values[[string]$k] = [string]$rawValues[$k]
                }
            } elseif ($rawValues) {
                foreach ($p in $rawValues.PSObject.Properties) {
                    $values[[string]$p.Name] = [string]$p.Value
                }
            }
            $resultsByIndex[[int]$first.Index] = [PSCustomObject]@{
                Path = [string]$first.Path
                Name = [string]$first.Name
                Values = $values
            }
        } else {
            $fallback = @{}
            foreach ($check in $Checks) {
                $fallback[$check.Name] = "probe failed"
            }
            $resultsByIndex[[int]$item.Index] = [PSCustomObject]@{
                Path = [string]$item.Path
                Name = [string]$item.Name
                Values = $fallback
            }
        }

        $completed++
        $percent = [int](($completed * 100) / [Math]::Max(1, $workItems.Count))
        Write-Progress -Activity "Comparing venvs" -Status ("{0}/{1}: {2}" -f $completed, $workItems.Count, $item.Name) -PercentComplete $percent
        $jobMeta.Remove($job.Id) | Out-Null
    }
}
Write-Progress -Activity "Comparing venvs" -Completed

for ($i = 0; $i -lt $workItems.Count; $i++) {
    if ($resultsByIndex.ContainsKey($i)) {
        $venvEntries += $resultsByIndex[$i]
        continue
    }
    $item = $workItems[$i]
    $fallback = @{}
    foreach ($check in $Checks) {
        $fallback[$check.Name] = "probe failed"
    }
    $venvEntries += [PSCustomObject]@{
        Path = $item.Path
        Name = $item.Name
        Values = $fallback
    }
}

if ($venvEntries.Count -eq 0) {
    Write-Host "No data."
    exit 0
}

$columns = @("Check") + @($venvEntries | ForEach-Object { $_.Name })
$rows = @()
foreach ($check in $Checks) {
    $row = [ordered]@{
        Check = $check.Name
    }
    foreach ($entry in $venvEntries) {
        if ($entry.Values.ContainsKey($check.Name)) {
            $row[$entry.Name] = [string]$entry.Values[$check.Name]
        } else {
            $row[$entry.Name] = "-"
        }
    }
    $rows += [PSCustomObject]$row
}

$widthByColumn = @{}

foreach ($col in $columns) {
    $maxWidth = $col.Length
    foreach ($row in $rows) {
        $value = [string]$row.$col
        if ($value.Length -gt $maxWidth) {
            $maxWidth = $value.Length
        }
    }
    $widthByColumn[$col] = $maxWidth
}

function Format-RowLine {
    param(
        [string[]]$Values,
        [string[]]$Cols,
        [hashtable]$Widths
    )

    $parts = @()
    for ($i = 0; $i -lt $Cols.Count; $i++) {
        $colName = $Cols[$i]
        $parts += $Values[$i].PadRight($Widths[$colName])
    }
    return "| " + ($parts -join " | ") + " |"
}

function Compare-VersionLike {
    param(
        [string]$A,
        [string]$B
    )

    if ([string]::IsNullOrWhiteSpace($A) -or [string]::IsNullOrWhiteSpace($B)) {
        return $null
    }

    $ma = [regex]::Match($A, '^\d+(?:\.\d+)*')
    $mb = [regex]::Match($B, '^\d+(?:\.\d+)*')
    if (-not $ma.Success -or -not $mb.Success) {
        return $null
    }

    $pa = $ma.Value.Split('.') | ForEach-Object { [int]$_ }
    $pb = $mb.Value.Split('.') | ForEach-Object { [int]$_ }
    $len = [Math]::Max($pa.Count, $pb.Count)
    for ($i = 0; $i -lt $len; $i++) {
        $va = if ($i -lt $pa.Count) { $pa[$i] } else { 0 }
        $vb = if ($i -lt $pb.Count) { $pb[$i] } else { 0 }
        if ($va -gt $vb) { return 1 }
        if ($va -lt $vb) { return -1 }
    }
    return 0
}

function Get-RowCellColors {
    param(
        [string[]]$Values,
        [string]$CheckName
    )

    $colors = @()
    for ($i = 0; $i -lt $Values.Count; $i++) {
        $colors += ""
    }

    $freq = @{}
    foreach ($v in $Values) {
        if (-not $freq.ContainsKey($v)) {
            $freq[$v] = 0
        }
        $freq[$v]++
    }

    if ($freq.Keys.Count -le 1) {
        return @{
            CheckColor = ""
            CellColors = $colors
        }
    }

    $checkColor = "Yellow"
    $maxCount = 0
    $baseValue = $null
    foreach ($k in $freq.Keys) {
        if ($freq[$k] -gt $maxCount) {
            $maxCount = $freq[$k]
            $baseValue = [string]$k
        }
    }

    $outliers = @()
    if ($freq.Keys.Count -eq 2) {
        foreach ($k in $freq.Keys) {
            if ($freq[$k] -eq 1) {
                $outliers += [string]$k
            }
        }
    }

    if ($outliers.Count -eq 1) {
        $outlierValue = $outliers[0]
        $outlierColor = "Yellow"
        if ($baseValue -eq "-" -and $outlierValue -ne "-") {
            $outlierColor = "Green"
        } else {
            $cmp = Compare-VersionLike -A $outlierValue -B $baseValue
            if ($cmp -eq 1) { $outlierColor = "Green" }
            elseif ($cmp -eq -1) { $outlierColor = "Red" }
        }

        for ($i = 0; $i -lt $Values.Count; $i++) {
            if ([string]$Values[$i] -eq $outlierValue) {
                $colors[$i] = $outlierColor
            }
        }

        return @{
            CheckColor = $checkColor
            CellColors = $colors
        }
    }

    $maxValue = $null
    $minValue = $null
    foreach ($v in $Values) {
        $value = [string]$v
        if ($value -eq "-") {
            continue
        }
        if ($null -eq $maxValue) {
            $maxValue = $value
            $minValue = $value
            continue
        }
        $cmpMax = Compare-VersionLike -A $value -B $maxValue
        if ($cmpMax -eq 1) {
            $maxValue = $value
        }
        $cmpMin = Compare-VersionLike -A $value -B $minValue
        if ($cmpMin -eq -1) {
            $minValue = $value
        }
    }

    for ($i = 0; $i -lt $Values.Count; $i++) {
        $value = [string]$Values[$i]
        if ($value -eq "-") {
            $colors[$i] = "Yellow"
            continue
        }
        if ($maxValue -and (Compare-VersionLike -A $value -B $maxValue) -eq 0) {
            $colors[$i] = "Green"
            continue
        }
        if ($minValue -and (Compare-VersionLike -A $value -B $minValue) -eq 0 -and $maxValue -and (Compare-VersionLike -A $maxValue -B $minValue) -ne 0) {
            $colors[$i] = "Red"
            continue
        }
        $colors[$i] = "Yellow"
    }

    return @{
        CheckColor = $checkColor
        CellColors = $colors
    }
}

function Paint-Text {
    param(
        [string]$Text,
        [string]$Color
    )

    if ([string]::IsNullOrWhiteSpace($Color)) {
        return $Text
    }

    $prefix = switch ($Color.ToLowerInvariant()) {
        "red" { $PSStyle.Foreground.Red }
        "green" { $PSStyle.Foreground.Green }
        "yellow" { $PSStyle.Foreground.Yellow }
        default { "" }
    }
    if ([string]::IsNullOrEmpty($prefix)) {
        return $Text
    }
    return "$prefix$Text$($PSStyle.Reset)"
}

function Format-RowLineColored {
    param(
        [string[]]$Values,
        [string[]]$Cols,
        [hashtable]$Widths,
        [string[]]$Colors
    )

    $parts = @()
    for ($i = 0; $i -lt $Cols.Count; $i++) {
        $colName = $Cols[$i]
        $plain = $Values[$i].PadRight($Widths[$colName])
        $color = ""
        if ($Colors -and $i -lt $Colors.Count) {
            $color = $Colors[$i]
        }
        $parts += (Paint-Text -Text $plain -Color $color)
    }
    return "| " + ($parts -join " | ") + " |"
}

$headerValues = @($columns)
$headerLine = Format-RowLine -Values $headerValues -Cols $columns -Widths $widthByColumn
$separatorParts = @()
foreach ($col in $columns) {
    $separatorParts += ("-" * $widthByColumn[$col])
}
$separatorLine = "|-" + ($separatorParts -join "-|-") + "-|"

Write-Host $headerLine
Write-Host $separatorLine
foreach ($row in $rows) {
    $values = @()
    foreach ($col in $columns) {
        $values += [string]$row.$col
    }

    $dataValues = @()
    for ($i = 1; $i -lt $values.Count; $i++) {
        $dataValues += $values[$i]
    }
    $rowColorInfo = Get-RowCellColors -Values $dataValues -CheckName $values[0]
    $rowColors = @($rowColorInfo.CheckColor) + @($rowColorInfo.CellColors)

    Write-Host (Format-RowLineColored -Values $values -Cols $columns -Widths $widthByColumn -Colors $rowColors)
}
