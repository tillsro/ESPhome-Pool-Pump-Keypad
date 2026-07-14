[CmdletBinding()]
param(
    [string]$Port = 'COM3',
    [int]$BaudRate = 9600,
    [int]$Cycles = 30,
    [string]$LogFile = "replay-prime-$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
)

# Request frames extracted from cap-0006 of loop-power-on-prime - the exact
# moment the pump transitioned from OFF to PRIME mode.
# Original keypad timing: ~61 ms between request starts.
#
# Important: not every keypad request in this capture is 3 bytes. Several
# decode as 4 or 5 contiguous bytes before the pump reply. Earlier versions of
# this script only replayed the first 3 bytes, which is not a faithful replay.
$polls = @(
    @(0xB5, 0x83, 0x00, 0x00),
    @(0xA5, 0xA4, 0x09),
    @(0xD5, 0xB4, 0x00, 0x00),
    @(0xA5, 0x83, 0x07),
    @(0x95, 0x93, 0x00),
    @(0x8D, 0xA4, 0x05, 0xF8),
    @(0xB5, 0xB4, 0x48, 0x20, 0xFC),
    @(0xA5, 0x93, 0x01),
    @(0x7B, 0xB4, 0x04),
    @(0x85, 0xB4, 0x09)
)

$ErrorActionPreference = 'Stop'

Write-Host "Replay prime-trigger sequence" -ForegroundColor Cyan
Write-Host "Requests: $($polls.Count)  Cycles: $Cycles  Total requests: $($polls.Count * $Cycles)" -ForegroundColor Cyan
Write-Host "Log: $LogFile" -ForegroundColor Cyan
Write-Host "Watch the pump physically - any speed change?`n" -ForegroundColor Yellow

$sp = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, 'None', 8, 'One'
$sp.ReadTimeout  = 50
$sp.WriteTimeout = 100
$sp.Handshake    = 'None'
$sp.DtrEnable    = $false
$sp.RtsEnable    = $false
$sp.Open()

Start-Sleep -Milliseconds 100
while ($sp.BytesToRead -gt 0) { [void]$sp.ReadByte() }

$log = [System.IO.StreamWriter]::new($LogFile, $false, [System.Text.Encoding]::ASCII)
$log.AutoFlush = $true
$log.WriteLine("# replay cap-0006 requests (off->prime transition), $Cycles cycles")

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$pollIntervalMs = 61
$totalReplies = 0
$noiseBytes = 0

function Test-PumpReply {
    param([System.Collections.Generic.List[byte]]$Bytes)

    if ($Bytes.Count -lt 5) { return $false }

    # Captured pump replies are longer frames with a BD/F6/D6/BF-ish header near
    # the front. Single 00 bytes from the adapter are RX noise, not replies.
    $limit = [Math]::Min(3, $Bytes.Count - 1)
    for ($i = 0; $i -le $limit; $i++) {
        if ($Bytes[$i] -in 0xBD,0xF6,0xD6,0xBF) { return $true }
    }
    return $false
}

try {
    for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
        foreach ($poll in $polls) {
            $cycleStart = $sw.Elapsed.TotalMilliseconds

            # Drain any pending bytes
            $rx = New-Object System.Collections.Generic.List[byte]
            while ($sp.BytesToRead -gt 0) { $rx.Add([byte]$sp.ReadByte()) }
            if ($rx.Count -gt 0) {
                $hexRx = ($rx | ForEach-Object { $_.ToString('X2') }) -join ' '
                $log.WriteLine(("{0,9:N2}  RX_PRE   {1}" -f $cycleStart, $hexRx))
                Write-Host "  pre-rx: $hexRx" -ForegroundColor DarkGray
            }

            # Send poll
            $hexTx = ($poll | ForEach-Object { $_.ToString('X2') }) -join ' '
            $sp.Write([byte[]]$poll, 0, $poll.Length)
            $log.WriteLine(("{0,9:N2}  TX [{1,3}]  {2}" -f $cycleStart, $cycle, $hexTx))

            # Wait for reply (15 ms window)
            $waitDeadline = $sw.Elapsed.TotalMilliseconds + 15
            while ($sw.Elapsed.TotalMilliseconds -lt $waitDeadline) { } # busy-wait
            $rx = New-Object System.Collections.Generic.List[byte]
            while ($sp.BytesToRead -gt 0) { $rx.Add([byte]$sp.ReadByte()) }

            if ($rx.Count -gt 0) {
                $hexRx = ($rx | ForEach-Object { $_.ToString('X2') }) -join ' '
                if (Test-PumpReply $rx) {
                    $log.WriteLine(("{0,9:N2}  RX       {1}" -f $sw.Elapsed.TotalMilliseconds, $hexRx))
                    $totalReplies++
                    $color = 'Green'
                    if ($hexRx -match 'BD 99|BD D0') { $color = 'Magenta' }
                    if ($hexRx -match 'BD 19') { $color = 'Cyan' }
                    Write-Host (">> {0,-14} << {1}" -f $hexTx, $hexRx) -ForegroundColor $color
                } else {
                    $log.WriteLine(("{0,9:N2}  RX_NOISE {1}" -f $sw.Elapsed.TotalMilliseconds, $hexRx))
                    $noiseBytes += $rx.Count
                    Write-Host (">> {0,-14} << noise:{1}" -f $hexTx, $hexRx) -ForegroundColor DarkGray
                }
            } else {
                Write-Host (">> {0,-14} << ." -f $hexTx) -ForegroundColor DarkGray
            }

            # Maintain ~61 ms cycle
            $elapsed = $sw.Elapsed.TotalMilliseconds - $cycleStart
            $remaining = $pollIntervalMs - $elapsed
            if ($remaining -gt 0) {
                $deadline = $sw.Elapsed.TotalMilliseconds + $remaining
                while ($sw.Elapsed.TotalMilliseconds -lt $deadline) { }
            }
        }
        Write-Host "--- end cycle $cycle / $Cycles  (valid replies: $totalReplies, noise bytes: $noiseBytes)" -ForegroundColor Yellow
    }
}
finally {
    $log.WriteLine("# end  total_replies=$totalReplies noise_bytes=$noiseBytes")
    $log.Close()
    if ($sp.IsOpen) { $sp.Close() }
    Write-Host "`nDone. Valid replies: $totalReplies / $($polls.Count * $Cycles) requests  Noise bytes: $noiseBytes" -ForegroundColor Green
    Write-Host "Log: $LogFile" -ForegroundColor Green
}
