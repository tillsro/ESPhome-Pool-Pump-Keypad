[CmdletBinding()]
param(
    [string]$Port = 'COM3',
    [int]$BaudRate = 38400,
    [ValidateSet('None','Odd','Even','Mark','Space')]
    [string]$Parity = 'None',
    [int]$DataBits = 8,
    [ValidateSet('None','One','Two','OnePointFive')]
    [string]$StopBits = 'One',
    [string]$LogFile = "rs485-capture_$(Get-Date -Format 'yyyyMMdd_HHmmss').log",
    [double]$FrameGapMs = 4.0,
    [int]$ReadTimeoutMs = 50,
    [switch]$Raw
)

$ErrorActionPreference = 'Stop'

Write-Host "Opening $Port @ $BaudRate $DataBits$($Parity.Substring(0,1))$StopBits ..." -ForegroundColor Cyan
Write-Host "Logging to: $LogFile" -ForegroundColor Cyan
Write-Host "Type a label + Enter at any time to annotate the log (e.g. 'power-on', 'up')." -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop.`n" -ForegroundColor Yellow

$script:labelBuf = ''
$hasConsole = $true
try { [void][Console]::KeyAvailable } catch { $hasConsole = $false }

$sp = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, $Parity, $DataBits, $StopBits
$sp.ReadTimeout  = $ReadTimeoutMs
$sp.WriteTimeout = 500
$sp.Handshake    = 'None'
$sp.DtrEnable    = $false
$sp.RtsEnable    = $false
$sp.Open()

$log = [System.IO.StreamWriter]::new($LogFile, $false, [System.Text.Encoding]::ASCII)
$log.AutoFlush = $true
$log.WriteLine("# RS485 capture  port=$Port baud=$BaudRate parity=$Parity data=$DataBits stop=$StopBits raw=$Raw")
if ($Raw) {
    $log.WriteLine("# raw mode: each line = one Read() chunk.  columns: elapsed_ms  delta_ms  len  hex-bytes")
} else {
    $log.WriteLine("# columns: ISO-timestamp  elapsed_ms  gap_ms  len  hex-bytes")
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$frameStartMs = 0.0
$lastByteMs   = 0.0
$buf = New-Object System.Collections.Generic.List[byte]
$totalBytes = 0
$totalFrames = 0

function Flush-Frame {
    param([double]$nowMs)
    if ($buf.Count -eq 0) { return }
    $hex   = ($buf | ForEach-Object { $_.ToString('X2') }) -join ' '
    $gap   = if ($script:totalFrames -eq 0) { 0 } else { [math]::Round($frameStartMs - $script:prevFrameEndMs, 2) }
    $iso   = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fff')
    $start = [math]::Round($frameStartMs, 2)
    $line  = "{0}  {1,10}  {2,8}  {3,4}  {4}" -f $iso, $start, $gap, $buf.Count, $hex
    $log.WriteLine($line)
    Write-Host $line
    $script:totalBytes  += $buf.Count
    $script:totalFrames += 1
    $script:prevFrameEndMs = $lastByteMs
    $buf.Clear()
}

$script:prevFrameEndMs = 0.0
$script:prevChunkMs    = 0.0

try {
    while ($true) {
        $n = $sp.BytesToRead
        if ($n -gt 0) {
            $chunk = New-Object byte[] $n
            $read = $sp.Read($chunk, 0, $n)
            $nowMs = $sw.Elapsed.TotalMilliseconds
            if ($Raw) {
                $hex   = (& { foreach ($b in $chunk[0..($read-1)]) { $b.ToString('X2') } }) -join ' '
                $delta = if ($script:prevChunkMs -eq 0) { 0 } else { [math]::Round($nowMs - $script:prevChunkMs, 2) }
                $line  = "{0,10}  {1,8}  {2,4}  {3}" -f ([math]::Round($nowMs,2)), $delta, $read, $hex
                $log.WriteLine($line)
                Write-Host $line
                $script:prevChunkMs = $nowMs
                $script:totalBytes += $read
            } else {
                if ($buf.Count -eq 0) { $frameStartMs = $nowMs }
                for ($i = 0; $i -lt $read; $i++) { [void]$buf.Add($chunk[$i]) }
                $lastByteMs = $nowMs
            }
        } else {
            if (-not $Raw -and $buf.Count -gt 0) {
                $idleMs = $sw.Elapsed.TotalMilliseconds - $lastByteMs
                if ($idleMs -ge $FrameGapMs) { Flush-Frame $sw.Elapsed.TotalMilliseconds }
            }
            Start-Sleep -Milliseconds 1
        }

        if ($hasConsole) {
            while ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq 'Enter') {
                    if ($script:labelBuf.Length -gt 0) {
                        $iso     = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss.fff')
                        $elapsed = [math]::Round($sw.Elapsed.TotalMilliseconds, 2)
                        $annot   = "# LABEL  $iso  $elapsed  $($script:labelBuf)"
                        $log.WriteLine($annot)
                        Write-Host $annot -ForegroundColor Magenta
                        $script:labelBuf = ''
                    }
                } elseif ($key.Key -eq 'Backspace') {
                    if ($script:labelBuf.Length -gt 0) {
                        $script:labelBuf = $script:labelBuf.Substring(0, $script:labelBuf.Length - 1)
                        Write-Host "`b `b" -NoNewline
                    }
                } elseif ($key.KeyChar -and [char]::IsControl($key.KeyChar) -eq $false) {
                    $script:labelBuf += $key.KeyChar
                    Write-Host $key.KeyChar -NoNewline -ForegroundColor Magenta
                }
            }
        }
    }
}
finally {
    if ($buf.Count -gt 0) { Flush-Frame $sw.Elapsed.TotalMilliseconds }
    $log.WriteLine("# end  frames=$totalFrames bytes=$totalBytes duration_ms=$([math]::Round($sw.Elapsed.TotalMilliseconds,0))")
    $log.Close()
    if ($sp.IsOpen) { $sp.Close() }
    Write-Host "`nClosed. Frames=$totalFrames  Bytes=$totalBytes  Log=$LogFile" -ForegroundColor Green
}
