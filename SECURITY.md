# Security Policy

## Supported versions

The latest tagged release and the current `main` branch receive security and
safety fixes. Older releases may be asked to reproduce an issue on the current
version before a fix is developed.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting through **Security > Advisories >
Report a vulnerability**. Do not open a public issue for vulnerabilities,
credentials, or behavior that could unexpectedly start, stop, or overspeed
equipment. If private reporting is unavailable, open a public issue containing
only a request for a private contact channel and no sensitive details.

Include the affected release or commit, hardware model, wiring path, expected
behavior, actual behavior, and the smallest sanitized reproduction available.
Never submit Wi-Fi credentials, API keys, full device logs containing private
data, or extracted OEM firmware.

This project controls mains-powered rotating equipment over a low-voltage bus.
Disconnect power before changing wiring and never attach multiple active
RS-485 transmitters to the pump at the same time.
