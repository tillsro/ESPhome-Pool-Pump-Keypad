# Offline Keypad Compatibility Test

This procedure tests a detached pump keypad against the PC pump emulator. It
can establish whether the keypad uses the same internal `01 70` request and
38-byte reply protocol as the iLiving ILG8PP390-VS without connecting the
candidate pump or running its motor.

Passing this test proves keypad-protocol compatibility. It does **not** prove
that the candidate pump drive uses the same pinout, voltage, firmware revision,
or external automation protocol.

## What the test checks

The emulator accepts only requests that match these framing and integrity
properties:

- 38400 baud, 8 data bits, no parity, 1 stop bit;
- 14-byte requests beginning with `01 70`;
- fixed request shape bytes `00 0C 00 00`;
- a valid 16-bit additive checksum;
- a valid Modbus CRC-16.

It then interprets the demand field using the `0..6000` scale for
`0..3450 RPM`. Values above that range are clamped by the simulated pump model,
so an implausible demand should be reported even if the frame itself validates.

It replies with the complete 38-byte frame captured from the real pump. The
test can also inject every documented fault code and verify that the keypad
understands the reply's fault field.

## Required equipment

- The detached keypad and its mating connector or a safe breakout harness.
- A Windows PC with Python 3.
- A Waveshare USB TO RS485 adapter or another automatic-direction USB-RS485
  adapter.
- A regulated, current-limited supply set to the keypad's **verified** supply
  voltage.
- A multimeter.
- Short jumper wires.
- Optional: a logic analyzer for investigating a failed or partial match.

Do not connect the pump, mains wiring, AtomS3, or another RS-485 transmitter
during this test.

## Verify power before wiring

The tested iLiving keypad harness has labeled `+5V` and `GND` pins. That fact
must not be generalized to an unknown keypad.

1. Find the keypad supply voltage in its service documentation, connector
   labels, or a measurement made by someone qualified to work around the pump.
2. Identify ground independently. Do not rely on wire color.
3. Set the bench supply to the verified voltage before connecting the keypad.
4. Use current limiting and stop immediately if the keypad draws unexpected
   current, repeatedly resets, becomes warm, or smells abnormal.

Do not power the keypad from the Waveshare adapter. Do not feed power into an
STM32 header pin labeled `3.3V`; that pin is normally the MCU rail or debugger
voltage reference, not the keypad's main power input.

The project has not established a universal safe current limit for other
keypads, so this guide intentionally does not prescribe one.

## Install the emulator

From the repository root in PowerShell:

```powershell
py -3 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install -r '.\requirements-emulator.txt'
```

The launcher automatically uses `.venv`. A specific Python interpreter can be
selected with `-PythonPath` if needed.

Plug in the Waveshare and list its serial port:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" -ListPorts
```

On Linux or macOS, install the same requirement and invoke the Python script
directly, replacing the serial port as appropriate:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-emulator.txt
python pump_emulator.py --list-ports
```

## Wire the tested six-pin keypad family

Use this table only when the candidate connector has the same explicit labels
as the tested iLiving keypad:

| Keypad connection | Bench connection |
|---|---|
| `+5V` | Regulated `+5V` supply output |
| `GND` | Supply negative **and** Waveshare `GND` |
| `485A-` | Waveshare `A+` |
| `485A+` | Waveshare `B-` |
| `485B-` | Leave disconnected |
| `485B+` | Leave disconnected |

Leave all SWD/debug pins disconnected. Do not add termination for the initial
short-cable test.

RS-485 `A` and `B` label conventions vary by manufacturer. The crossed mapping
above is the mapping validated with this keypad and Waveshare combination. If
power is verified and the keypad runs but the emulator receives no requests,
power down and swap only the two RS-485 data wires for one retry. Never swap or
guess the supply wires.

For an unlabeled or different connector, do not use this table. Determine its
power and differential-pair pinout first with documentation and passive
measurements.

## Stage 1: Capture startup and normal operation

Connect the Waveshare to the PC but leave keypad power off. Start the emulator,
substituting the detected port:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" `
  -Port COM3 -VerboseFrames -LogFile "keypad-compat-normal.log"
```

Now turn on only the keypad's regulated bench supply. Starting the emulator
first ensures the startup requests are recorded.

The tested iLiving keypad produces behavior similar to:

```text
RX seq=0x01 status=0x00 demand=0x1770/3450rpm
TX seq=0x01 fault=0x00 accepted=0x1770 actual=0x0000 state=RAMPING corrupted=false
[ONLINE] ... requests=32 replies=32 dropped=0 bad_rx=0
```

Expected observations:

- `requests` and `replies` begin increasing at roughly 16 per second.
- Each valid request has the `01 70` header and a new sequence number.
- `bad_rx` remains zero or very low.
- The tested keypad starts its prime cycle and requests `0x1770`, which maps to
  3450 RPM.
- The keypad accepts the simulated actual-speed ramp instead of remaining in a
  communication-error state.

Press `S` in the emulator console at any time to print the current counters and
demand. Press `Q` to stop cleanly and write final statistics to the log.

## Stage 2: Exercise keypad commands

While the normal emulator is running:

1. Press each speed-preset button and then press `S` in the emulator console.
   Record the demand RPM shown for each button.
2. Use the keypad's custom-speed controls, if present. Confirm that the demand
   changes in the console.
3. Press the keypad power button once. A compatible keypad should begin sending
   a zero demand and the emulator should report `STOPPING`, then `STOPPED`.
4. Press power again. It should return to a nonzero demand; the tested keypad
   starts prime again.

Preset values may have been reprogrammed by the owner, so exact preset RPM is
not a compatibility requirement. The important evidence is that button actions
change the validated request's demand field and that stop produces zero.

## Stage 3: Verify reply and fault decoding

Stop the normal emulator with `Q`, then start the keypad-aware fault sequence:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" `
  -Port COM3 -KeypadFaultDemo -FaultHoldSeconds 3 `
  -KeypadRecoverySeconds 2 -LogFile "keypad-compat-faults.log"
```

The emulator cycles through `E001`, `E002`, `E006`, `E009`, `E010`, `E011`,
`E013`, `E014`, `E018`, `E021`, and `E048`. For each code:

1. Confirm that the same code appears on the keypad.
2. Wait until the console says the fault replies were cleared.
3. Press keypad power once to acknowledge the fault and produce zero demand.
4. Press power a second time to restart.

The emulator waits for that zero-demand acknowledgement and subsequent restart
before advancing. Press `Q` after the final code, or add
`-ExitAfterFaultDemo` to exit automatically after the last successful restart.

Displaying the correct injected codes is strong evidence that the keypad parses
the same 38-byte reply layout, not merely the same request format.

## Interpret the result

### Strong match

Classify the keypad as a strong protocol match when all of these are true:

- Valid requests are accepted continuously at 38400 baud.
- Requests and replies stay approximately equal with few or no invalid frames.
- Sequence numbers increment and wrap normally.
- Keypad buttons produce sensible demand changes, including zero for stop.
- The keypad displays simulated speed/state changes.
- Injected fault codes appear correctly and follow the two-press recovery flow.

This is enough to add the keypad as **bench-confirmed**. The associated pump
still requires a passive bus capture before any command is transmitted to it.

### Partial match

If requests validate but the keypad rejects replies, shows incorrect speed, or
misidentifies faults, report a partial match. It may share the request protocol
but use a different 38-byte reply revision.

### No match

Do not classify the keypad as compatible when:

- no requests validate after power and polarity have been verified;
- traffic uses a different baud rate, frame length, header, checksum, or CRC;
- demand changes cannot be correlated with keypad actions; or
- the keypad never accepts replies from the emulator.

Do not modify random reply bytes to make an unknown keypad react. Capture its
traffic passively and implement a separate protocol variant.

## Troubleshooting order

1. Confirm the correct COM port and close PulseView or any other program using
   it.
2. Confirm the keypad is powered from its verified supply and shares ground
   with the Waveshare.
3. Confirm the emulator was running before keypad power was applied.
4. Power down and swap only the two RS-485 data wires once.
5. Run with `-VerboseFrames` and inspect `bad_rx` and request counters.
6. If requests remain at zero, attach a logic analyzer passively and determine
   baud rate, polarity, active pair, and frame length before further testing.

## Compatibility report template

Include the two emulator logs and fill in:

```text
Brand and pump model:
Keypad part/revision number:
Manual URL and revision:
Connector labels:
Verified keypad supply voltage:
Observed supply current:
USB-RS485 adapter model:
Working A/B mapping:
Validated request count:
Invalid request count:
Approximate requests per second:
Startup demand:
Preset demands:
Stop demand reached zero: yes/no
Speed/state reply accepted: yes/no
Fault codes displayed correctly:
Two-press fault recovery observed: yes/no
Result: strong match / partial match / no match
Notes:
```

Do not include Wi-Fi credentials, API keys, extracted proprietary firmware, or
photos containing personal information in a public report.
