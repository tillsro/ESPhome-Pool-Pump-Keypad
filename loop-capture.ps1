[CmdletBinding()]
param(
    [string]$Label = 'session',
    [int]$SampleRate = 100000,
    [int]$Samples = 60000,
    [string]$Channels = 'D0,D1,D2,D3',
    [string]$SigrokCli = 'C:\Program Files\sigrok\sigrok-cli\sigrok-cli.exe'
)

$cli = $SigrokCli
$root = $PSScriptRoot
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    throw "sigrok-cli was not found at '$cli'. Pass its path with -SigrokCli."
}

$dir  = Join-Path $root ("loop-{0}-{1}" -f $Label, (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -Path $dir -ItemType Directory -Force | Out-Null

Write-Host "Capturing to $dir" -ForegroundColor Cyan
Write-Host "samplerate=$SampleRate samples=$Samples channels=$Channels" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor Yellow

$start = Get-Date
$i = 0
try {
    while ($true) {
        $i++
        $out = Join-Path $dir ('cap-{0:D4}.sr' -f $i)
        $t0  = Get-Date
        & $cli --driver fx2lafw --config "samplerate=$SampleRate" --samples $Samples `
               --channels $Channels --output-format srzip --output-file $out 2>$null | Out-Null
        $ms = ((Get-Date) - $t0).TotalMilliseconds
        $wall = ((Get-Date) - $start).TotalSeconds
        Write-Host ("[{0,7:N1}s] cap-{1:D4}  ({2,4:N0} ms)" -f $wall, $i, $ms)
    }
}
finally {
    $total = ((Get-Date) - $start).TotalSeconds
    Write-Host "`nStopped. $i captures in $([math]::Round($total,1))s." -ForegroundColor Green
    Write-Host "Files: $dir" -ForegroundColor Green
}
