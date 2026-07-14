[CmdletBinding()]
param(
    [string]$Port = 'COM3',
    [int]$BaudRate = 9600,
    [ValidateSet('1','2','3')]
    [string]$InitialSpeed = '3',
    [int]$PollIntervalMs = 60,
    [int]$ReplyWaitMs = 20,
    [string]$LogFile = "emulator-$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
)

# Polls captured from the real keypad in steady state.
# Each entry = (byte1, byte2, byte3). Byte2 carries the speed-family bits.
# Replayed in rotation; pump sees the bytes the same as if the keypad sent them.
$pollSets = @{
    '1' = @(
        @(0xA5, 0xCA, 0x00),
        @(0x7B, 0xCA, 0x00),
        @(0x7E, 0xD2, 0x04),
        @(0xB5, 0xD2, 0x00),
        @(0x7A, 0xCA, 0x04),
        @(0xBD, 0xC2, 0x07),
        @(0xA5, 0xCA, 0x03),
        @(0xB5, 0xDA, 0x00),
        @(0x7F, 0xD2, 0x01),
        @(0xA5, 0xC2, 0x04)
    )
    '2' = @(
        @(0x95, 0x99, 0x01),
        @(0x78, 0x89, 0x08),
        @(0x78, 0x99, 0x05),
        @(0x78, 0xA9, 0x01),
        @(0x79, 0xBD, 0x0D),
        @(0x78, 0xAD, 0x00),
        @(0x79, 0xB9, 0x00),
        @(0x85, 0x8D, 0x07),
        @(0xB5, 0x9D, 0x00),
        @(0x7E, 0x99, 0x01)
    )
    '3' = @(
        @(0xA5, 0xD9, 0x07),
        @(0x79, 0xD9, 0x01),
        @(0x95, 0xC9, 0x01),
        @(0x7C, 0xD9, 0x00),
        @(0x95, 0xD9, 0x02),
        @(0xAD, 0xC9, 0x01),
        @(0x7D, 0xC9, 0x00),
        @(0xAD, 0xC9, 0x06),
        @(0x78, 0xB9, 0x00),
        @(0xA5, 0xB9, 0x00)
    )
}

$ErrorActionPreference = 'Stop'

Write-Host "Opening $Port @ $BaudRate 8N1 (emulating keypad)" -ForegroundColor Cyan
Write-Host "Logging to: $LogFile" -ForegroundColor Cyan
Write-Host "Keys: 1/2/3 to change speed, Q to quit`n" -ForegroundColor Yellow

$sp = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, 'None', 8, 'One'
$sp.ReadTimeout  = 50
$sp.WriteTimeout = 100
$sp.Handshake    = 'None'
$sp.DtrEnable    = $false
$sp.RtsEnable    = $false
$sp.Open()

# Drain any stale bytes
Start-Sleep -Milliseconds 100
while ($sp.BytesToRead -gt 0) { [void]$sp.ReadByte() }

$log = [System.IO.StreamWriter]::new($LogFile, $false, [System.Text.Encoding]::ASCII)
$log.AutoFlush = $true
$log.WriteLine("# Pump-keypad emulator  port=$Port baud=$BaudRate interval=${PollIntervalMs}ms")
$log.WriteLine("# columns: elapsed_ms  dir  speed  bytes")

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$currentSpeed = $InitialSpeed
$pollIdx = 0
$txCount = 0
$rxCount = 0
$replyCount = 0

function Get-RxChunk {
    param($maxWaitMs)
    $deadline = $sw.Elapsed.TotalMilliseconds + $maxWaitMs
    $bytes = New-Object System.Collections.Generic.List[byte]
    while ($sw.Elapsed.TotalMilliseconds -lt $deadline) {
        if ($sp.BytesToRead -gt 0) {
            $bytes.Add([byte]$sp.ReadByte())
            # Once we have data, wait for end-of-reply (gap > 4ms)
            $lastByte = $sw.Elapsed.TotalMilliseconds
            while (($sw.Elapsed.TotalMilliseconds - $lastByte) -lt 4) {
                if ($sp.BytesToRead -gt 0) {
                    $bytes.Add([byte]$sp.ReadByte())
                    $lastByte = $sw.Elapsed.TotalMilliseconds
                } else {
                    Start-Sleep -Milliseconds 1
                }
            }
            break
        }
        Start-Sleep -Milliseconds 1
    }
    return $bytes
}

try {
    while ($true) {
        $cycleStart = $sw.Elapsed.TotalMilliseconds
        $pollSet = $pollSets[$currentSpeed]
        $poll = $pollSet[$pollIdx % $pollSet.Count]
        $pollIdx++

        # Wait for bus silence. Tight loop - PS Start-Sleep is too coarse (~15 ms).
        $waitStart    = $sw.Elapsed.TotalMilliseconds
        $silenceStart = $waitStart
        while ($true) {
            if ($sp.BytesToRead -gt 0) {
                while ($sp.BytesToRead -gt 0) { [void]$sp.ReadByte() }
                $silenceStart = $sw.Elapsed.TotalMilliseconds
            }
            $now = $sw.Elapsed.TotalMilliseconds
            if (($now - $silenceStart) -ge 5)  { break }   # got our gap
            if (($now - $waitStart)    -ge 100) { break }  # bus too busy, send anyway
        }

        # TX
        $hexTx = ($poll | ForEach-Object { $_.ToString('X2') }) -join ' '
        $sp.Write([byte[]]$poll, 0, $poll.Length)
        $log.WriteLine(("{0,9:N2}  TX  spd{1}  {2}" -f $cycleStart, $currentSpeed, $hexTx))
        $txCount++

        # RX - wait for pump reply
        $rx = Get-RxChunk -maxWaitMs $ReplyWaitMs
        if ($rx.Count -gt 0) {
            $rxNow = $sw.Elapsed.TotalMilliseconds
            $hexRx = ($rx | ForEach-Object { $_.ToString('X2') }) -join ' '
            $log.WriteLine(("{0,9:N2}  RX        {1}" -f $rxNow, $hexRx))
            $rxCount += $rx.Count
            $replyCount++
            # Color-code by reply type
            $color = 'Cyan'
            if ($hexRx -match 'BD 19') { $color = 'White' }
            if ($hexRx -match 'BD 29') { $color = 'Yellow' }
            if ($hexRx -match 'BD BD') { $color = 'Magenta' }
            if ($hexRx -match 'BD 3D') { $color = 'Red' }
            if ($hexRx -match 'BD A9') { $color = 'DarkYellow' }
            Write-Host (">> {0,-12} << {1}" -f $hexTx, $hexRx) -ForegroundColor $color
        } else {
            Write-Host (">> {0,-12} << (no reply)" -f $hexTx) -ForegroundColor DarkGray
        }

        # Console input - speed change / quit
        while ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            switch ($key.KeyChar) {
                '1' { if ($currentSpeed -ne '1') { $currentSpeed = '1'; $pollIdx = 0; Write-Host "==> SWITCH to speed 1" -ForegroundColor Green; $log.WriteLine("# user switched to speed 1") } }
                '2' { if ($currentSpeed -ne '2') { $currentSpeed = '2'; $pollIdx = 0; Write-Host "==> SWITCH to speed 2" -ForegroundColor Green; $log.WriteLine("# user switched to speed 2") } }
                '3' { if ($currentSpeed -ne '3') { $currentSpeed = '3'; $pollIdx = 0; Write-Host "==> SWITCH to speed 3" -ForegroundColor Green; $log.WriteLine("# user switched to speed 3") } }
                { $_ -in 'q','Q' } { throw 'Quit requested' }
            }
        }

        # Maintain poll interval
        $elapsed = $sw.Elapsed.TotalMilliseconds - $cycleStart
        $remaining = [math]::Max(0, $PollIntervalMs - $elapsed)
        if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int]$remaining) }
    }
}
catch {
    if ($_.Exception.Message -ne 'Quit requested') {
        Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}
finally {
    $log.WriteLine("# end  tx_polls=$txCount rx_replies=$replyCount rx_bytes=$rxCount duration_ms=$([int]$sw.Elapsed.TotalMilliseconds)")
    $log.Close()
    if ($sp.IsOpen) { $sp.Close() }
    Write-Host "`nDone. Sent $txCount polls, got $replyCount replies ($rxCount bytes). Log: $LogFile" -ForegroundColor Green
}
