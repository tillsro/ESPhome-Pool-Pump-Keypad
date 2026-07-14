[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [string]$Port = 'COM3',
    [int]$BaudRate = 38400,
    [ValidateRange(0, 1000)]
    [double]$ReplyDelayMs = 5,
    [ValidateRange(0, 100000)]
    [double]$RampRpmPerSecond = 1200,
    [ValidateRange(0, 3450)]
    [double]$InitialRpm = 0,
    [ValidateRange(0, 255)]
    [int]$FaultCode = 0,
    [switch]$FaultDemo,
    [switch]$KeypadFaultDemo,
    [ValidateRange(0, 3600)]
    [double]$FaultStartDelaySeconds = 2,
    [ValidateRange(0.01, 3600)]
    [double]$FaultHoldSeconds = 3,
    [ValidateRange(0, 3600)]
    [double]$FaultClearSeconds = 1,
    [ValidateRange(0, 3600)]
    [double]$KeypadRecoverySeconds = 2,
    [switch]$ExitAfterFaultDemo,
    [ValidateRange(0, 3600)]
    [double]$SummaryInterval = 2,
    [string]$LogFile = '',
    [switch]$VerboseFrames,
    [switch]$ListPorts,
    [switch]$ListFaults
)

$ErrorActionPreference = 'Stop'

$emulator = Join-Path $PSScriptRoot 'pump_emulator.py'
$pythonArguments = @()

if ($PythonPath) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python interpreter not found at $PythonPath"
    }
    $python = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $pythonCandidates = @(
        (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Join-Path $PSScriptRoot 'tmp\esphome-venv\Scripts\python.exe')
    )
    $python = $pythonCandidates | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1

    if (-not $python) {
        $pythonCommand = Get-Command 'py.exe' -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $python = $pythonCommand.Source
            $pythonArguments = @('-3')
        } else {
            $pythonCommand = Get-Command 'python.exe' -ErrorAction SilentlyContinue
            if ($pythonCommand) {
                $python = $pythonCommand.Source
            }
        }
    }
}

if (-not $python) {
    throw 'Python 3 was not found. Follow docs\keypad-compatibility-test.md to create .venv, or pass -PythonPath.'
}
if (-not (Test-Path -LiteralPath $emulator)) {
    throw "Pump emulator not found at $emulator"
}

if ($ListPorts) {
    & $python @pythonArguments $emulator '--list-ports'
    exit $LASTEXITCODE
}
if ($ListFaults) {
    & $python @pythonArguments $emulator '--list-faults'
    exit $LASTEXITCODE
}
if ($ExitAfterFaultDemo -and -not ($FaultDemo -or $KeypadFaultDemo)) {
    throw '-ExitAfterFaultDemo requires -FaultDemo or -KeypadFaultDemo.'
}

$emulatorArguments = @(
    '--port', $Port,
    '--baud', $BaudRate,
    '--reply-delay-ms', $ReplyDelayMs,
    '--ramp-rpm-per-sec', $RampRpmPerSecond,
    '--initial-rpm', $InitialRpm,
    '--fault-code', $FaultCode,
    '--summary-interval', $SummaryInterval
)

if ($VerboseFrames) {
    $emulatorArguments += '--verbose'
}
if ($FaultDemo) {
    $emulatorArguments += @(
        '--fault-demo',
        '--fault-start-delay-seconds', $FaultStartDelaySeconds,
        '--fault-hold-seconds', $FaultHoldSeconds,
        '--fault-clear-seconds', $FaultClearSeconds
    )
}
if ($KeypadFaultDemo) {
    $emulatorArguments += @(
        '--keypad-fault-demo',
        '--fault-start-delay-seconds', $FaultStartDelaySeconds,
        '--fault-hold-seconds', $FaultHoldSeconds,
        '--keypad-recovery-seconds', $KeypadRecoverySeconds
    )
}
if ($ExitAfterFaultDemo) {
    $emulatorArguments += '--exit-after-fault-demo'
}
if ($LogFile) {
    $emulatorArguments += @('--log', $LogFile)
}

& $python @pythonArguments $emulator @emulatorArguments
exit $LASTEXITCODE
