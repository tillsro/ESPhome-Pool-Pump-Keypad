# Changelog

## v0.1.0 - 2026-07-15

First public-ready release of the independently implemented iLiving/Lingxiao
keypad-bus controller and emulator.

### Confirmed hardware

- iLiving `ILG8PP390-VS` pump and its original detachable keypad
- M5Stack AtomS3 with Atomic RS485 Base controlling the real pump
- Waveshare USB TO RS485 adapter controlling the real pump from Windows
- FX2LP Saleae-compatible logic analyzer for passive bus capture
- Original keypad and AtomS3 tested independently against the PC pump emulator

### Included

- Validated 38400-baud request/reply framing, additive checksums, and Modbus CRC
- Real-pump stop, restart, and 1000-3450 RPM demand control
- ESPHome entities, web interface, screen status, five speed levels, and
  state-aware button controls
- Stateful PC emulator with speed ramping, communication failures, and all 11
  documented keypad fault codes
- Offline keypad compatibility procedure, protocol tests, hardware photos, and
  M5Stack adapter-plate CAD

### Limitations

- Only the iLiving `ILG8PP390-VS` is confirmed against a real pump. Related
  Lingxiao and rebadged models remain candidates until their traffic is
  captured and compared.
- The normal keypad protocol uses the `485A` pair. The separate `485B` firmware
  path has not been characterized.
- The original keypad must be disconnected before the AtomS3, Waveshare, or
  another controller transmits on the pump bus.
- The supplied AtomS3 YAML targets the AtomS3 and Atomic RS485 Base pinout. It
  is not a generic ESP32 configuration.
- The PC emulator validates protocol and user-interface behavior; it does not
  reproduce the pump's electrical power stage, motor, or physical hazards.
- The default ESPHome startup mode is `STOPPED`, so rebooting the controller
  while connected sends a stop demand after the startup delay.
- Extracted OEM firmware is intentionally excluded. Only independently written
  code, protocol documentation, and firmware-analysis metadata are published.
