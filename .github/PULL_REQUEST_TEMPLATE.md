## Summary

Describe the problem and the change.

## Verification

- [ ] `python -m unittest tests.test_pump_emulator -v`
- [ ] `esphome config esphome/pool-pump-controller.yaml`
- [ ] `esphome compile esphome/pool-pump-controller.yaml` when firmware changed
- [ ] Real-pump testing, if performed, used only one RS-485 transmitter
- [ ] No credentials, proprietary firmware, private logs, or unrelated captures are included

## Hardware evidence

List the tested pump/keypad/controller models and attach sanitized evidence when
the change affects compatibility or on-wire behavior. State explicitly when a
change was tested only against the PC emulator.
