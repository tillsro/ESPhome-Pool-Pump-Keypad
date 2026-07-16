# AtomS3 iLiving pump controller

This configuration replaces the original Century/VGreen integration with a local
ESPHome component for the protocol validated on the iLiving ILG8PP390-VS.

## Protocol implemented

- 38400 baud, 8 data bits, no parity, 1 stop bit.
- A 14-byte `01 70` request every 61 ms.
- The keypad firmware's 16-bit additive checksum, followed by Modbus CRC-16.
- Complete 38-byte reply validation: header, echoed sequence, shape, additive
  checksum, and Modbus CRC-16.
- Demand scaling `floor(RPM * 6000 / 3450)`.
- Stop (`0`), start, 1000-3450 RPM demand, actual RPM, accepted RPM, online
  state, and decoded pump faults.

The compile-time protocol fixtures include the Waveshare-generated 1800 RPM
request and a complete pump reply from
`replay-rx-timing-soak-speed1-20260713_200032.log`.

## AtomS3 button controls

The AtomS3's front screen/button uses two distinct gestures:

- Short press (50-800 ms): start a stopped pump at the currently selected speed.
  While running, select the next speed level.
- Long press: stop the pump as soon as the hold reaches 1.5 seconds. Releasing
  the button is not required, and a long press never starts the pump.

Presses between 800 ms and 1.5 seconds do nothing. The five speed levels are:

| Level | Demand |
|---|---:|
| 1 | 1000 RPM |
| 2 | 1400 RPM |
| 3 | 1800 RPM |
| 4 | 2500 RPM |
| 5 | 3450 RPM |

The initial target is level 3 (1800 RPM). The first short press starts the pump
without advancing that target. Further short presses change speed immediately.
The screen shows both the selected level and target RPM. A custom demand set
through ESPHome or Home Assistant is labeled `CUSTOM SPEED`; while running, the
next short press selects the next higher preset, or wraps from level 5 to level
1. The five RPM values are kept in the `substitutions` block at the top of
`pool-pump-controller.yaml` so they can be changed in one place.

## Isolated Waveshare pump emulator

The PC can emulate the pump so the AtomS3 and Atomic RS485 Base can be tested
without the pump or keypad attached. Power both USB devices normally and use
three short jumper wires:

| Waveshare USB-RS485 | Atomic RS485 Base |
|---|---|
| `A+` | `A` |
| `B-` | `B` |
| `GND` | `G` |

Leave the Atomic base's `DC24V` terminal disconnected. Do not connect the pump,
keypad, or pump `+5V` wire anywhere in this setup. Use the straight-through
labels shown above. A logic-analyzer isolation test proved that crossing these
two adapters made their bias networks oppose one another and held the Atomic B
line high.

This bench wiring was validated on 2026-07-13: the emulator accepted and replied
to 204 of 204 AtomS3 requests, while the controller established communication
with no new missed replies.

After connecting the Waveshare to the PC, identify its port:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" -ListPorts
```

Then start the emulator from the repository root, substituting the Waveshare's
port if it is not `COM3`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" -Port COM3
```

To exercise every keypad-derived pump fault automatically, hold each fault for
three seconds, clear it for one second, and exit after the final clear:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" `
  -Port COM3 -FaultDemo -FaultHoldSeconds 3 -FaultClearSeconds 1 `
  -ExitAfterFaultDemo
```

The sequence covers `E001`, `E002`, `E006`, `E009`, `E010`, `E011`, `E013`,
`E014`, `E018`, `E021`, and `E048`. List their decoded descriptions without
opening a serial port with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" -ListFaults
```

The original keypad latches the first nonzero fault until its power button is
pressed once to acknowledge the error and a second time to restart. Use the
acknowledgement-gated mode when testing the keypad itself:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run-pump-emulator.ps1" `
  -Port COM3 -KeypadFaultDemo -FaultHoldSeconds 3 -KeypadRecoverySeconds 2
```

For power verification, complete keypad wiring, normal command checks,
pass/fail criteria, and a report template, follow the dedicated
[`Offline Keypad Compatibility Test`](../docs/keypad-compatibility-test.md).

After each code appears, wait at least three seconds and press the keypad power
button twice. The emulator waits for the second press's nonzero demand before
advancing to the next code.

The AtomS3 should change from `OFFLINE` to `STOPPED`. Short-press its button to
request 1800 RPM; the display should show `RAMPING` and then `RUNNING`, with
actual RPM climbing at the configured rate. Use further short presses to test
all five speed levels, then hold for 1.5 seconds to test stopping. The display
should turn red at the 1.5-second threshold, even if the button is still held.

While the emulator console is active, these single-key controls are available:

| Key | Test action |
|---|---|
| `S` | Print current emulator state and counters |
| `F` | Cycle to the next documented pump fault |
| `C` | Clear the injected fault |
| `O` | Toggle all replies off/on to test the 500 ms offline fail-safe |
| `D` | Drop the next reply |
| `X` | Corrupt the next reply's CRC |
| `R` | Force actual RPM to zero without changing accepted demand |
| `V` | Toggle per-frame console output |
| `Q` | Stop the emulator |

Every request and reply is written to a timestamped ASCII log. The protocol
fixture tests can be rerun from the repository root with:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_pump_emulator -v
```

## Build

1. From the repository root, create the pinned Python 3.12 environment:

   ```powershell
   py -3.12 -m venv .venv
   & '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
   & '.\.venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
   ```

   See the root README for macOS and Linux commands. The validated requirement
   is ESPHome `2026.6.5`.

2. Copy `esphome\secrets.example.yaml` to `esphome\secrets.yaml` and replace
   every placeholder before flashing hardware.

3. On Windows, if the repository path contains spaces, point ESPHome's build
   output at a path without spaces before validating or compiling:

   ```powershell
   $env:ESPHOME_BUILD_PATH = "$env:USERPROFILE\.esphome-build"
   ```

   Pioarduino can reject generated project paths containing whitespace. This
   environment variable is optional on other installations.

4. Validate from the repository root:

   ```powershell
   & '.\.venv\Scripts\esphome.exe' config .\esphome\pool-pump-controller.yaml
   ```

5. Compile and upload over USB-C:

   ```powershell
   & '.\.venv\Scripts\esphome.exe' run .\esphome\pool-pump-controller.yaml
   ```

## Startup behavior

The supplied YAML uses `startup_mode: STOPPED`. After the one-second startup
delay, it continuously sends valid stop frames. This is deliberately
deterministic: rebooting the Atom while attached to the pump stops the motor.

`startup_mode: PASSIVE` sends no pump traffic until `Pump Run` is explicitly
changed. Because the pump is silent until polled, passive mode cannot know or
display the pump's physical state.

Use `startup_mode: RUNNING` only when automatic motor start after every
controller boot is explicitly desired. It commands the configured RPM after
each boot.

## Hardware

### Known-good Waveshare reference

Keep using `replay-firmware-master-frame.ps1` as the known-good protocol oracle.
Do not connect the Waveshare and Atom transmitters to the pump at the same time.

The proven Waveshare wiring remains:

| Pump cable | Waveshare terminal |
|---|---|
| `485A-` | `A+` |
| `485A+` | `B-` |
| `GND` | `GND` |
| `+5V` | Not connected |

The Waveshare labels are reversed relative to the pump cable labels. Do not
reuse this crossed mapping for the M5Stack base.

### Proven AtomS3/Atomic RS485 Base wiring

Power the AtomS3 from USB-C. On the Atomic base's four-position terminal, leave
the `DC24V` input disconnected; the pump's `+5V` wire is not a suitable input for
that terminal.

Use the following mapping:

| Pump cable | Atomic RS485 Base |
|---|---|
| `485A+` | `A` |
| `485A-` | `B` |
| `GND` | `G` |
| `+5V` | Not connected |

This wiring was validated against the real iLiving ILG8PP390-VS on 2026-07-15.
The AtomS3 established communication and controlled the pump successfully. Do
not cross `A` and `B` for this pump/base combination.

The base has no built-in 120-ohm termination. Leave termination out for the
short-cable connection, matching the validated setup.
