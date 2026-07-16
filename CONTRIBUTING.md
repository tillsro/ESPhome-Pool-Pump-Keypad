# Contributing

Contributions are welcome when they preserve the evidence-first approach used
to recover this protocol. Clearly separate behavior observed on real hardware
from behavior demonstrated only with the PC emulator.

## Development setup

Follow the fresh-clone setup in `README.md`. Before submitting a change, run:

```text
python -m unittest tests.test_pump_emulator -v
esphome config esphome/pool-pump-controller.yaml
```

Compile the firmware when changing the ESPHome YAML or local component:

```text
esphome compile esphome/pool-pump-controller.yaml
```

## Hardware and protocol changes

- Disconnect the original keypad before another controller transmits.
- Do not infer compatibility from connector appearance or product branding.
- Record exact models, wiring, baud rate, polarity, complete frames, checksum,
  CRC, and observed pump behavior.
- State whether results came from the real pump, original keypad, logic
  analyzer, firmware analysis, or emulator.
- Add focused protocol tests for parser, scaling, checksum, CRC, or state
  changes affected by the contribution.

Use `docs/keypad-compatibility-test.md` and the compatibility issue template
when evaluating another model.

## Material that must not be submitted

- Wi-Fi credentials, API keys, tokens, or private network details
- Extracted proprietary firmware binaries
- Copyrighted manuals or third-party files without redistribution rights
- Large raw logs and captures that are not required as a minimal test fixture
- Personal information or location metadata from photographs

## Licensing

By contributing, you agree that software contributions are provided under the
MIT License and documentation or media contributions are provided under CC BY
4.0, according to `LICENSES/README.md`.
