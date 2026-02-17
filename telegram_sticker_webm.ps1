param(
    [string]$InputPath,
    [string]$OutputPath,
    [int]$MaxSizeKB = 256,
    [int]$DurationSec = 3,
    [int]$Fps = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Command '$Name' not found. Install it and add it to PATH."
    }
}

function Normalize-InputPath {
    param([string]$PathValue)
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $null
    }
    return $PathValue.Trim().Trim('"')
}

function Get-BaseNameWithoutTrailingDigits {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return "sticker"
    }
    $clean = [regex]::Replace($Name, '([._-]?\d+)+$', '')
    $clean = $clean.Trim().Trim('.', '_', '-')
    if ([string]::IsNullOrWhiteSpace($clean)) {
        return "sticker"
    }
    return $clean
}

function Get-SourceInfo {
    param([string]$SourcePath)

    $ext = [System.IO.Path]::GetExtension($SourcePath).ToLowerInvariant()
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($SourcePath)
    $imageExts = @(".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")

    $info = @{
        Mode = "video_or_file"
        SourcePath = $SourcePath
        SuggestedBaseName = $stem
        SequencePattern = $null
        StartNumber = $null
        FrameCount = 0
        IsImage = ($imageExts -contains $ext)
    }

    if (-not $info.IsImage) {
        return $info
    }

    $m = [regex]::Match($stem, '^(.*?)(\d+)$')
    if (-not $m.Success) {
        $info.Mode = "single_image"
        return $info
    }

    $prefix = $m.Groups[1].Value
    $digits = $m.Groups[2].Value
    if ([string]::IsNullOrWhiteSpace($prefix)) {
        $info.Mode = "single_image"
        return $info
    }

    $dir = [System.IO.Path]::GetDirectoryName($SourcePath)
    $pad = $digits.Length
    $rx = '^' + [regex]::Escape($prefix) + '(\d+)' + [regex]::Escape($ext) + '$'

    $frames = Get-ChildItem -LiteralPath $dir -File -Filter ("*" + $ext) | ForEach-Object {
        $mm = [regex]::Match($_.Name, $rx, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($mm.Success -and $mm.Groups[1].Value.Length -eq $pad) {
            [PSCustomObject]@{
                Number = [int]$mm.Groups[1].Value
                Path = $_.FullName
            }
        }
    } | Sort-Object Number

    if ($frames.Count -lt 2) {
        $info.Mode = "single_image"
        return $info
    }

    $info.Mode = "image_sequence"
    $info.SequencePattern = Join-Path $dir ($prefix + ("%0" + $pad + "d") + $ext)
    $info.StartNumber = $frames[0].Number
    $info.FrameCount = $frames.Count
    $info.SuggestedBaseName = $prefix.TrimEnd('.', '_', '-')
    return $info
}

function Build-InputArgs {
    param(
        [hashtable]$SourceInfo,
        [int]$DurationValue,
        [int]$FpsValue
    )

    if ($SourceInfo.Mode -eq "image_sequence") {
        return @(
            "-framerate", $FpsValue,
            "-start_number", $SourceInfo.StartNumber,
            "-i", $SourceInfo.SequencePattern,
            "-t", $DurationValue
        )
    }

    if ($SourceInfo.Mode -eq "single_image") {
        return @("-loop", "1", "-t", $DurationValue, "-i", $SourceInfo.SourcePath)
    }

    return @("-t", $DurationValue, "-i", $SourceInfo.SourcePath)
}

function Encode-WebM {
    param(
        [hashtable]$SourceInfo,
        [string]$OutPath,
        [int]$BitrateKbps,
        [int]$DurationValue,
        [int]$FpsValue
    )
    $vf = "scale='if(gt(iw,ih),512,-1)':'if(gt(iw,ih),-1,512)':flags=lanczos,pad=512:512:(ow-iw)/2:(oh-ih)/2:color=0x00000000,fps=$FpsValue"
    $passLog = Join-Path $env:TEMP ("tg_sticker_" + [guid]::NewGuid().ToString("N"))
    $inputArgs = Build-InputArgs -SourceInfo $SourceInfo -DurationValue $DurationValue -FpsValue $FpsValue

    try {
        $pass1Args = @(
            "-y",
            "-hide_banner",
            "-loglevel", "error"
        ) + $inputArgs + @(
            "-an",
            "-vf", $vf,
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-b:v", ("{0}k" -f $BitrateKbps),
            "-maxrate", ("{0}k" -f $BitrateKbps),
            "-bufsize", ("{0}k" -f ($BitrateKbps * 2)),
            "-row-mt", "1",
            "-tile-columns", "2",
            "-frame-parallel", "0",
            "-auto-alt-ref", "0",
            "-pass", "1",
            "-passlogfile", $passLog,
            "-f", "webm",
            "NUL"
        )
        & ffmpeg @pass1Args
        if ($LASTEXITCODE -ne 0) {
            throw "ffmpeg pass 1 failed."
        }

        $pass2Args = @(
            "-y",
            "-hide_banner",
            "-loglevel", "error"
        ) + $inputArgs + @(
            "-an",
            "-vf", $vf,
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-b:v", ("{0}k" -f $BitrateKbps),
            "-maxrate", ("{0}k" -f $BitrateKbps),
            "-bufsize", ("{0}k" -f ($BitrateKbps * 2)),
            "-row-mt", "1",
            "-tile-columns", "2",
            "-frame-parallel", "0",
            "-auto-alt-ref", "0",
            "-pass", "2",
            "-passlogfile", $passLog,
            $OutPath
        )
        & ffmpeg @pass2Args
        if ($LASTEXITCODE -ne 0) {
            throw "ffmpeg pass 2 failed."
        }
    }
    finally {
        Remove-Item -LiteralPath ($passLog + "-0.log") -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath ($passLog + "-0.log.mbtree") -ErrorAction SilentlyContinue
    }
}

Require-Command -Name "ffmpeg"
Require-Command -Name "ffprobe"

$InputPath = Normalize-InputPath -PathValue $InputPath
if (-not $InputPath) {
    $InputPath = Normalize-InputPath -PathValue (Read-Host "Enter input file path")
}

if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw "Input file not found: $InputPath"
}

$InputPath = (Resolve-Path -LiteralPath $InputPath).Path
$sourceInfo = Get-SourceInfo -SourcePath $InputPath

if ($sourceInfo.Mode -eq "image_sequence") {
    Write-Host ("Sequence detected: {0} frames, pattern: {1}" -f $sourceInfo.FrameCount, $sourceInfo.SequencePattern)
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $dir = [System.IO.Path]::GetDirectoryName($InputPath)
    $name = Get-BaseNameWithoutTrailingDigits -Name $sourceInfo.SuggestedBaseName
    $OutputPath = Join-Path $dir ($name + "_tg_sticker.webm")
}

$OutputPath = $OutputPath.Trim().Trim('"')
$maxBytes = $MaxSizeKB * 1024
$targetKbps = [Math]::Max(120, [int][Math]::Floor(($maxBytes * 8 * 0.95) / ($DurationSec * 1000)))

if ($sourceInfo.Mode -eq "image_sequence" -and $sourceInfo.FrameCount -gt 0) {
    $seqDuration = $sourceInfo.FrameCount / [double]$Fps
    if ($seqDuration -gt 0 -and $seqDuration -lt $DurationSec) {
        $DurationSec = [Math]::Max(1, [int][Math]::Ceiling($seqDuration))
    }
}
elseif ($sourceInfo.Mode -ne "single_image") {
    $probeArgs = @(
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        $InputPath
    )
    $realDurationRaw = & ffprobe @probeArgs
    $realDuration = 0.0
    [void][double]::TryParse($realDurationRaw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$realDuration)
    if ($realDuration -gt 0 -and $realDuration -lt $DurationSec) {
        $DurationSec = [Math]::Max(1, [int][Math]::Ceiling($realDuration))
    }
}

$low = 80
$high = $targetKbps
$bestTmp = $null
$attempt = 0

while ($low -le $high -and $attempt -lt 8) {
    $attempt++
    $mid = [int](($low + $high) / 2)
    $tmpOut = Join-Path $env:TEMP ("tg_sticker_tmp_" + [guid]::NewGuid().ToString("N") + ".webm")

    try {
        Encode-WebM -SourceInfo $sourceInfo -OutPath $tmpOut -BitrateKbps $mid -DurationValue $DurationSec -FpsValue $Fps
    }
    catch {
        Remove-Item -LiteralPath $tmpOut -ErrorAction SilentlyContinue
        throw
    }

    $size = (Get-Item -LiteralPath $tmpOut).Length
    if ($size -le $maxBytes) {
        if ($bestTmp -and (Test-Path -LiteralPath $bestTmp)) {
            Remove-Item -LiteralPath $bestTmp -ErrorAction SilentlyContinue
        }
        $bestTmp = $tmpOut
        $low = $mid + 20
    }
    else {
        Remove-Item -LiteralPath $tmpOut -ErrorAction SilentlyContinue
        $high = $mid - 20
    }
}

if (-not $bestTmp) {
    $fallbackOut = Join-Path $env:TEMP ("tg_sticker_fallback_" + [guid]::NewGuid().ToString("N") + ".webm")
    Encode-WebM -SourceInfo $sourceInfo -OutPath $fallbackOut -BitrateKbps 80 -DurationValue $DurationSec -FpsValue $Fps
    $fallbackSize = (Get-Item -LiteralPath $fallbackOut).Length
    if ($fallbackSize -gt $maxBytes) {
        Remove-Item -LiteralPath $fallbackOut -ErrorAction SilentlyContinue
        throw "Could not fit into $MaxSizeKB KB. Try shorter/cleaner source."
    }
    $bestTmp = $fallbackOut
}

Move-Item -LiteralPath $bestTmp -Destination $OutputPath -Force
$finalSizeKB = [Math]::Round((Get-Item -LiteralPath $OutputPath).Length / 1KB, 1)
Write-Host "Done: $OutputPath"
Write-Host "Size: $finalSizeKB KB (limit: $MaxSizeKB KB)"
