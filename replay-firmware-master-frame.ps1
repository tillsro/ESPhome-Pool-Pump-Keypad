[CmdletBinding()]
param(
    [string]$Port = 'COM3',
    [int]$BaudRate = 38400,
    [int]$Cycles = 300,
    [int]$IntervalMs = 61,
    [int]$ReplyWaitMs = 55,
    [byte]$StartSeq = 0x01,
    [byte]$Status = 0x00,
    [ValidateSet('None','Speed1','Speed2','Speed3')]
    [string]$Preset = 'None',
    [ValidateRange(0,3450)]
    [int]$Rpm = 0,
    [UInt16]$Value = 0,
    [switch]$PrimeFirst,
    [int]$PrimeCycles = 60,
    [UInt16]$PrimeValue = 0x1770,
    [switch]$StopOnMeaningfulReply,
    [switch]$DryRun,
    [string]$LogFile = "replay-fw-master-$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
)

$ErrorActionPreference = 'Stop'

switch ($Preset) {
    'Speed1' { $Value = 0x0C3A } # user-confirmed 1800 RPM
    'Speed2' { $Value = 0x0982 } # user-confirmed 1400 RPM
    'Speed3' { $Value = 0x1770 } # user-confirmed 3450 RPM
}

if ($Rpm -gt 0) {
    # Firmware/capture-derived scaling: 3450 RPM maps to 0x1770 / 6000.
    $Value = [UInt16][Math]::Floor(($Rpm * 6000.0) / 3450.0)
}

function ConvertTo-HexString {
    param([byte[]]$Bytes)

    if ($null -eq $Bytes -or $Bytes.Count -eq 0) { return '' }
    return (($Bytes | ForEach-Object { $_.ToString('X2') }) -join ' ')
}

function Get-ModbusCrc16 {
    param(
        [byte[]]$Bytes,
        [int]$Count = -1
    )

    if ($Count -lt 0) { $Count = $Bytes.Count }
    if ($Count -gt $Bytes.Count) {
        throw "CRC byte count $Count exceeds buffer length $($Bytes.Count)."
    }

    [int]$crc = 0xFFFF
    for ($i = 0; $i -lt $Count; $i++) {
        $crc = $crc -bxor [int]$Bytes[$i]
        for ($bit = 0; $bit -lt 8; $bit++) {
            if (($crc -band 0x0001) -ne 0) {
                $crc = (($crc -shr 1) -bxor 0xA001) -band 0xFFFF
            } else {
                $crc = ($crc -shr 1) -band 0xFFFF
            }
        }
    }

    return [UInt16]$crc
}

function Get-PumpFaultDescription {
    param([byte]$Code)

    switch ([int]$Code) {
        1  { return 'IPM module failure' }
        2  { return 'Output current exceeds limit' }
        6  { return 'Input voltage too high' }
        9  { return 'Input voltage too low' }
        10 { return 'Inverter overload' }
        11 { return 'Motor overload' }
        13 { return 'Output phase loss or imbalance' }
        14 { return 'Inverter overheating' }
        18 { return 'Current sampling circuit failure' }
        21 { return 'Display board EEPROM or connection failure' }
        48 { return 'PFC overcurrent or PFC circuit failure' }
        default { return 'Unknown pump fault/status' }
    }
}

function New-FirmwareMasterFrame {
    param(
        [byte]$Seq,
        [byte]$FrameStatus,
        [UInt16]$FrameValue
    )

    # The firmware builds a 12-byte inner frame with an additive checksum, then
    # appends a Modbus CRC-16 over all 12 bytes. Both checksums are low-byte first.
    $valueHigh = [byte](($FrameValue -shr 8) -band 0xFF)
    $valueLow  = [byte]($FrameValue -band 0xFF)
    [byte[]]$innerFrame = @(0x01, 0x70, $Seq, $FrameStatus, 0x00, 0x0C, 0x00, 0x00, $valueHigh, $valueLow, 0x00, 0x00)

    $sum = 0
    for ($i = 0; $i -lt 10; $i++) {
        $sum = ($sum + $innerFrame[$i]) -band 0xFFFF
    }

    $innerFrame[10] = [byte]($sum -band 0xFF)
    $innerFrame[11] = [byte](($sum -shr 8) -band 0xFF)

    $crc = Get-ModbusCrc16 -Bytes $innerFrame
    [byte[]]$frame = New-Object byte[] 14
    [Array]::Copy($innerFrame, 0, $frame, 0, $innerFrame.Count)
    $frame[12] = [byte]($crc -band 0xFF)
    $frame[13] = [byte](($crc -shr 8) -band 0xFF)
    return ,$frame
}

function Read-PendingBytes {
    param([System.IO.Ports.SerialPort]$SerialPort)

    $rx = New-Object System.Collections.Generic.List[byte]
    while ($SerialPort.BytesToRead -gt 0) {
        $rx.Add([byte]$SerialPort.ReadByte())
    }
    return ,([byte[]]$rx.ToArray())
}

function Test-FirmwareReply {
    param(
        [byte[]]$Frame,
        [byte]$ExpectedSeq
    )

    if ($null -eq $Frame -or $Frame.Count -ne 38) { return $false }
    if ($Frame[0] -ne 0x01 -or $Frame[1] -ne 0x70) { return $false }
    if ($Frame[2] -ne $ExpectedSeq) { return $false }
    if ($Frame[4] -ne 0x00 -or $Frame[5] -ne 0x0C) { return $false }

    [int]$sum = 0
    for ($i = 0; $i -lt 34; $i++) {
        $sum = ($sum + $Frame[$i]) -band 0xFFFF
    }
    [int]$wireSum = [int]$Frame[34] -bor ([int]$Frame[35] -shl 8)
    if ($sum -ne $wireSum) { return $false }

    [UInt16]$crc = Get-ModbusCrc16 -Bytes $Frame -Count 36
    [int]$wireCrc = [int]$Frame[36] -bor ([int]$Frame[37] -shl 8)
    return ([int]$crc -eq $wireCrc)
}

function Find-FirmwareReply {
    param(
        [byte[]]$Bytes,
        [byte]$ExpectedSeq
    )

    if ($null -eq $Bytes -or $Bytes.Count -lt 38) { return $null }

    for ($offset = 0; $offset -le ($Bytes.Count - 38); $offset++) {
        if ($Bytes[$offset] -ne 0x01 -or $Bytes[$offset + 1] -ne 0x70) { continue }

        [byte[]]$candidate = New-Object byte[] 38
        [Array]::Copy($Bytes, $offset, $candidate, 0, 38)
        if (Test-FirmwareReply -Frame $candidate -ExpectedSeq $ExpectedSeq) {
            return [PSCustomObject]@{
                Offset = $offset
                Frame  = $candidate
            }
        }
    }

    return $null
}

Write-Host "Replay firmware-derived keypad master frame" -ForegroundColor Cyan
Write-Host "Frame family: 01 70 <seq> <status> 00 0C 00 00 <value_hi> <value_lo> <sum_lo> <sum_hi> <crc_lo> <crc_hi>" -ForegroundColor Cyan
Write-Host "Baud: $BaudRate  Cycles: $Cycles  Interval: ${IntervalMs}ms  StartSeq: 0x$($StartSeq.ToString('X2'))  Status: 0x$($Status.ToString('X2'))  Preset: $Preset  RPM: $Rpm  Value: 0x$($Value.ToString('X4'))" -ForegroundColor Cyan
if ($PrimeFirst) {
    Write-Host "Prime-first bootstrap: first $PrimeCycles cycles use Value: 0x$($PrimeValue.ToString('X4')) before switching to target value." -ForegroundColor Cyan
}
if ($StopOnMeaningfulReply) {
    Write-Host "Stop-on-valid-reply: enabled." -ForegroundColor Cyan
}
Write-Host "Log: $LogFile" -ForegroundColor Cyan
Write-Host "Logic analyzer check: replay traffic should decode as D1 inverted at 38400. If it decodes as D1 normal, swap adapter A/B." -ForegroundColor Yellow
Write-Host "This is experimental. Watch the pump physically for a reply, display change, or speed change.`n" -ForegroundColor Yellow

if ($DryRun) {
    Write-Host "Dry run: generating frames only, no serial port opened." -ForegroundColor Yellow
    $seq = $StartSeq
    for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
        $frameValue = $Value
        if ($PrimeFirst -and $cycle -le $PrimeCycles) {
            $frameValue = $PrimeValue
        }

        [byte[]]$frame = New-FirmwareMasterFrame -Seq $seq -FrameStatus $Status -FrameValue $frameValue
        Write-Host ("[{0,4}] value=0x{1}  {2}" -f $cycle, $frameValue.ToString('X4'), (ConvertTo-HexString $frame))
        $seq = [byte](($seq + 1) -band 0xFF)
    }
    return
}

$sp = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, 'None', 8, 'One'
$sp.ReadTimeout  = 50
$sp.WriteTimeout = 100
$sp.Handshake    = 'None'
$sp.DtrEnable    = $false
$sp.RtsEnable    = $false
$sp.Open()

Start-Sleep -Milliseconds 100
[void](Read-PendingBytes -SerialPort $sp)

$log = [System.IO.StreamWriter]::new($LogFile, $false, [System.Text.Encoding]::ASCII)
$log.AutoFlush = $true
$log.WriteLine("# replay firmware-derived USART2 keypad-master frame")
$log.WriteLine("# cycles=$Cycles interval_ms=$IntervalMs start_seq=0x$($StartSeq.ToString('X2')) status=0x$($Status.ToString('X2')) value=0x$($Value.ToString('X4'))")
$log.WriteLine("# prime_first=$PrimeFirst prime_cycles=$PrimeCycles prime_value=0x$($PrimeValue.ToString('X4')) stop_on_meaningful_reply=$StopOnMeaningfulReply")
$log.WriteLine("# request_length=14 reply_length=38 reply_wait_ms=$ReplyWaitMs validation=header+seq+shape+sum+crc16_modbus reply_status=decoded_not_matched")

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$seq = $StartSeq
$validReplies = 0
$faultReplies = 0
$noiseBytes = 0
$runCompleted = $false

try {
    for ($cycle = 1; $cycle -le $Cycles; $cycle++) {
        $cycleStart = $sw.Elapsed.TotalMilliseconds

        $preRx = Read-PendingBytes -SerialPort $sp
        if ($preRx.Count -gt 0) {
            $hexPreRx = ConvertTo-HexString $preRx
            $log.WriteLine(("{0,9:N2}  RX_PRE   {1}" -f $cycleStart, $hexPreRx))
            Write-Host "  pre-rx: $hexPreRx" -ForegroundColor DarkGray
        }

        $frameValue = $Value
        if ($PrimeFirst -and $cycle -le $PrimeCycles) {
            $frameValue = $PrimeValue
        }

        [byte[]]$frame = New-FirmwareMasterFrame -Seq $seq -FrameStatus $Status -FrameValue $frameValue
        $hexTx = ConvertTo-HexString $frame
        $sp.Write($frame, 0, $frame.Length)
        $log.WriteLine(("{0,9:N2}  TX [{1,4}] value=0x{2}  {3}" -f $cycleStart, $cycle, $frameValue.ToString('X4'), $hexTx))

        $rxBuffer = New-Object System.Collections.Generic.List[byte]
        $replyMatch = $null
        $waitDeadline = $sw.Elapsed.TotalMilliseconds + $ReplyWaitMs
        do {
            [byte[]]$chunk = Read-PendingBytes -SerialPort $sp
            if ($chunk.Count -gt 0) {
                $rxBuffer.AddRange($chunk)
                $replyMatch = Find-FirmwareReply -Bytes $rxBuffer.ToArray() -ExpectedSeq $seq
            }

            if ($null -eq $replyMatch -and $sw.Elapsed.TotalMilliseconds -lt $waitDeadline) {
                # A 1 ms PowerShell sleep can last about 15 ms on Windows and
                # split a complete pump reply across adjacent polling cycles.
                [System.Threading.Thread]::SpinWait(256)
            }
        } while ($null -eq $replyMatch -and $sw.Elapsed.TotalMilliseconds -lt $waitDeadline)

        if ($null -eq $replyMatch) {
            [byte[]]$finalChunk = Read-PendingBytes -SerialPort $sp
            if ($finalChunk.Count -gt 0) {
                $rxBuffer.AddRange($finalChunk)
                $replyMatch = Find-FirmwareReply -Bytes $rxBuffer.ToArray() -ExpectedSeq $seq
            }
        }

        [byte[]]$rx = $rxBuffer.ToArray()
        if ($null -ne $replyMatch) {
            [byte[]]$replyFrame = $replyMatch.Frame
            [byte]$replyStatus = $replyFrame[3]
            $hexRx = ConvertTo-HexString $replyFrame
            $validReplies++

            if ($replyStatus -eq 0) {
                $log.WriteLine(("{0,9:N2}  RX_VALID status=0x00  {1}" -f $sw.Elapsed.TotalMilliseconds, $hexRx))
                Write-Host (">> {0} << valid:{1}" -f $hexTx, $hexRx) -ForegroundColor Green
            } else {
                $faultReplies++
                $faultCode = 'E{0:D3}' -f [int]$replyStatus
                $faultDescription = Get-PumpFaultDescription -Code $replyStatus
                $log.WriteLine(("{0,9:N2}  RX_FAULT status=0x{1} code={2} description={3}  {4}" -f $sw.Elapsed.TotalMilliseconds, $replyStatus.ToString('X2'), $faultCode, $faultDescription, $hexRx))
                Write-Host (">> {0} << FAULT {1} (status 0x{2}): {3}`n   {4}" -f $hexTx, $faultCode, $replyStatus.ToString('X2'), $faultDescription, $hexRx) -ForegroundColor Red
            }

            $extraByteCount = $rx.Count - 38
            if ($extraByteCount -gt 0) {
                $hexRaw = ConvertTo-HexString $rx
                $log.WriteLine(("{0,9:N2}  RX_RAW   {1}" -f $sw.Elapsed.TotalMilliseconds, $hexRaw))
                $noiseBytes += $extraByteCount
            }

            if ($replyStatus -ne 0) {
                Write-Host "Stopping because the pump returned a valid fault response." -ForegroundColor Red
                break
            }

            if ($StopOnMeaningfulReply) {
                Write-Host "Stopping after first valid 38-byte reply." -ForegroundColor Green
                break
            }
        } elseif ($rx.Count -gt 0) {
            $hexRx = ConvertTo-HexString $rx
            $log.WriteLine(("{0,9:N2}  RX_INVALID {1}" -f $sw.Elapsed.TotalMilliseconds, $hexRx))
            $noiseBytes += $rx.Count
            Write-Host (">> {0} << invalid:{1}" -f $hexTx, $hexRx) -ForegroundColor DarkGray
        } else {
            Write-Host (">> {0} << ." -f $hexTx) -ForegroundColor DarkGray
        }

        $seq = [byte](($seq + 1) -band 0xFF)

        $elapsed = $sw.Elapsed.TotalMilliseconds - $cycleStart
        $remaining = $IntervalMs - $elapsed
        if ($remaining -gt 0) {
            $deadline = $sw.Elapsed.TotalMilliseconds + $remaining
            while ($sw.Elapsed.TotalMilliseconds -lt $deadline) { }
        }
    }

    $runCompleted = $true
}
catch {
    $log.WriteLine("# error=$($_.Exception.Message)")
    throw
}
finally {
    $runStatus = if ($runCompleted) { 'complete' } else { 'aborted' }
    $log.WriteLine("# end status=$runStatus valid_replies=$validReplies fault_replies=$faultReplies noise_bytes=$noiseBytes")
    $log.Close()
    if ($sp.IsOpen) { $sp.Close() }
    $summaryColor = if ($runCompleted) { 'Green' } else { 'Red' }
    Write-Host "`n$runStatus. Valid replies: $validReplies / $Cycles  Fault replies: $faultReplies  Noise bytes: $noiseBytes" -ForegroundColor $summaryColor
    Write-Host "Log: $LogFile" -ForegroundColor Green
}
