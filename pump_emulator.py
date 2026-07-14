#!/usr/bin/env python3
"""Emulate an iLiving ILG8PP390-VS pump for a keypad or controller."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import sys
import time
from typing import Iterable


REQUEST_SIZE = 14
REPLY_SIZE = 38
MAX_PROTOCOL_VALUE = 6000
MAX_RPM = 3450.0

FAULT_DESCRIPTIONS = {
    1: "IPM module failure",
    2: "Output current exceeds limit",
    6: "Input voltage too high",
    9: "Input voltage too low",
    10: "Inverter overload",
    11: "Motor overload",
    13: "Output phase loss or imbalance",
    14: "Inverter overheating",
    18: "Current sampling circuit failure",
    21: "Display board EEPROM or connection failure",
    48: "PFC overcurrent or PFC circuit failure",
}
FAULT_CYCLE = tuple(FAULT_DESCRIPTIONS)

# Complete pump reply captured at a stable 1800 RPM. Fields changed for each
# request are sequence, fault, accepted demand, actual speed, and demand echo.
REPLY_TEMPLATE = bytes.fromhex(
    "01 70 01 00 00 0C 00 65 00 02 00 65 0C 3A 0C 39 "
    "0A 7F 00 E4 0C 9F 01 0D 01 90 01 5F 00 45 00 00 "
    "0C 3A 77 05 85 76"
)


def modbus_crc16(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def read_be16(data: bytes | bytearray, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def write_be16(data: bytearray, offset: int, value: int) -> None:
    data[offset] = (value >> 8) & 0xFF
    data[offset + 1] = value & 0xFF


def rpm_to_value(rpm: float) -> int:
    rpm = max(0.0, min(MAX_RPM, rpm))
    return int(rpm * MAX_PROTOCOL_VALUE / MAX_RPM)


def value_to_rpm(value: int) -> float:
    value = max(0, min(MAX_PROTOCOL_VALUE, value))
    return value * MAX_RPM / MAX_PROTOCOL_VALUE


@dataclass(frozen=True)
class PumpRequest:
    sequence: int
    status: int
    demand_value: int
    raw: bytes

    @property
    def demand_rpm(self) -> float:
        return value_to_rpm(self.demand_value)


def decode_request(frame: bytes) -> PumpRequest:
    if len(frame) != REQUEST_SIZE:
        raise ValueError(f"request must be {REQUEST_SIZE} bytes")
    if frame[0:2] != b"\x01\x70":
        raise ValueError("request header is not 01 70")
    if frame[4:8] != b"\x00\x0c\x00\x00":
        raise ValueError("request shape bytes are invalid")

    expected_sum = sum(frame[:10]) & 0xFFFF
    wire_sum = frame[10] | (frame[11] << 8)
    if wire_sum != expected_sum:
        raise ValueError("request additive checksum is invalid")

    expected_crc = modbus_crc16(frame[:12])
    wire_crc = frame[12] | (frame[13] << 8)
    if wire_crc != expected_crc:
        raise ValueError("request Modbus CRC-16 is invalid")

    return PumpRequest(
        sequence=frame[2],
        status=frame[3],
        demand_value=read_be16(frame, 8),
        raw=frame,
    )


def build_reply(
    sequence: int,
    fault_code: int,
    accepted_value: int,
    actual_value: int,
    echoed_value: int,
) -> bytes:
    frame = bytearray(REPLY_TEMPLATE)
    frame[2] = sequence & 0xFF
    frame[3] = fault_code & 0xFF
    write_be16(frame, 12, accepted_value & 0xFFFF)
    write_be16(frame, 14, actual_value & 0xFFFF)
    write_be16(frame, 32, echoed_value & 0xFFFF)

    additive = sum(frame[:34]) & 0xFFFF
    frame[34] = additive & 0xFF
    frame[35] = (additive >> 8) & 0xFF
    crc = modbus_crc16(frame[:36])
    frame[36] = crc & 0xFF
    frame[37] = (crc >> 8) & 0xFF
    return bytes(frame)


def validate_reply(frame: bytes) -> bool:
    if len(frame) != REPLY_SIZE:
        return False
    if frame[0:2] != b"\x01\x70" or frame[4:6] != b"\x00\x0c":
        return False
    if (frame[34] | (frame[35] << 8)) != (sum(frame[:34]) & 0xFFFF):
        return False
    return (frame[36] | (frame[37] << 8)) == modbus_crc16(frame[:36])


class RequestStreamParser:
    """Recover validated 14-byte requests from arbitrary serial chunks."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.discarded_bytes = 0
        self.invalid_candidates = 0

    def feed(self, data: bytes) -> list[PumpRequest]:
        self.buffer.extend(data)
        requests: list[PumpRequest] = []

        while len(self.buffer) >= 2:
            header = self.buffer.find(b"\x01\x70")
            if header < 0:
                keep = 1 if self.buffer[-1] == 0x01 else 0
                self.discarded_bytes += len(self.buffer) - keep
                if keep:
                    self.buffer[:] = b"\x01"
                else:
                    self.buffer.clear()
                break

            if header:
                del self.buffer[:header]
                self.discarded_bytes += header

            if len(self.buffer) < REQUEST_SIZE:
                break

            candidate = bytes(self.buffer[:REQUEST_SIZE])
            try:
                request = decode_request(candidate)
            except ValueError:
                del self.buffer[0]
                self.discarded_bytes += 1
                self.invalid_candidates += 1
                continue

            del self.buffer[:REQUEST_SIZE]
            requests.append(request)

        return requests


class PumpModel:
    def __init__(self, initial_rpm: float, ramp_rpm_per_second: float) -> None:
        self.actual_rpm = max(0.0, min(MAX_RPM, initial_rpm))
        self.target_rpm = self.actual_rpm
        self.accepted_value = rpm_to_value(self.target_rpm)
        self.ramp_rpm_per_second = max(0.0, ramp_rpm_per_second)
        self.fault_code = 0
        self.last_update: float | None = None

    def _advance(self, now: float) -> None:
        if self.last_update is None:
            self.last_update = now
            return

        elapsed = max(0.0, now - self.last_update)
        self.last_update = now
        difference = self.target_rpm - self.actual_rpm
        if self.ramp_rpm_per_second == 0 or abs(difference) <= self.ramp_rpm_per_second * elapsed:
            self.actual_rpm = self.target_rpm
            return

        direction = 1.0 if difference > 0 else -1.0
        self.actual_rpm += direction * self.ramp_rpm_per_second * elapsed

    def apply_request(self, request: PumpRequest, now: float) -> tuple[int, int]:
        self._advance(now)
        requested = min(request.demand_value, MAX_PROTOCOL_VALUE)
        self.accepted_value = 0 if self.fault_code else requested
        self.target_rpm = value_to_rpm(self.accepted_value)
        if self.ramp_rpm_per_second == 0:
            self.actual_rpm = self.target_rpm
        return self.accepted_value, rpm_to_value(self.actual_rpm)

    def force_stopped(self, now: float) -> None:
        self.actual_rpm = 0.0
        self.last_update = now

    def state_name(self) -> str:
        if self.fault_code:
            return f"E{self.fault_code:03d}"
        if self.accepted_value == 0:
            return "STOPPED" if self.actual_rpm < 1.0 else "STOPPING"
        if abs(self.actual_rpm - self.target_rpm) <= 2.0:
            return "RUNNING"
        return "RAMPING"


@dataclass
class RuntimeState:
    online: bool = True
    drop_next: int = 0
    corrupt_next: int = 0
    verbose: bool = False
    quit_requested: bool = False


@dataclass
class Statistics:
    requests: int = 0
    replies: int = 0
    dropped: int = 0
    corrupted: int = 0
    sequence_jumps: int = 0
    last_sequence: int | None = None
    last_demand_value: int = 0


class FaultSequence:
    """Schedule fault/clear transitions against a monotonic clock."""

    def __init__(
        self,
        codes: Iterable[int],
        start_delay: float,
        fault_duration: float,
        clear_duration: float,
    ) -> None:
        self.codes = tuple(codes)
        if not self.codes:
            raise ValueError("fault sequence requires at least one code")
        if any(code < 1 or code > 255 for code in self.codes):
            raise ValueError("fault sequence codes must be between 1 and 255")
        if start_delay < 0 or fault_duration <= 0 or clear_duration < 0:
            raise ValueError("fault timing values are invalid")

        cursor = start_delay
        events: list[tuple[float, int]] = []
        for code in self.codes:
            events.append((cursor, code))
            cursor += fault_duration
            events.append((cursor, 0))
            cursor += clear_duration

        self.events = tuple(events)
        self.complete_offset = cursor
        self.started_at: float | None = None
        self.next_event = 0
        self.completed = False

    def start(self, now: float) -> None:
        self.started_at = now
        self.next_event = 0
        self.completed = False

    def update(self, now: float) -> tuple[list[int], bool]:
        if self.started_at is None:
            raise RuntimeError("fault sequence has not been started")
        if self.completed:
            return [], False

        elapsed = max(0.0, now - self.started_at)
        transitions: list[int] = []
        while self.next_event < len(self.events) and elapsed >= self.events[self.next_event][0]:
            transitions.append(self.events[self.next_event][1])
            self.next_event += 1

        completed_now = self.next_event == len(self.events) and elapsed >= self.complete_offset
        if completed_now:
            self.completed = True
        return transitions, completed_now


class KeypadFaultSequence:
    """Advance faults only after the keypad acknowledges and restarts."""

    def __init__(
        self,
        codes: Iterable[int],
        start_delay: float,
        fault_duration: float,
        recovery_duration: float,
    ) -> None:
        self.codes = tuple(codes)
        if not self.codes:
            raise ValueError("keypad fault sequence requires at least one code")
        if any(code < 1 or code > 255 for code in self.codes):
            raise ValueError("keypad fault sequence codes must be between 1 and 255")
        if start_delay < 0 or fault_duration <= 0 or recovery_duration < 0:
            raise ValueError("keypad fault timing values are invalid")

        self.start_delay = start_delay
        self.fault_duration = fault_duration
        self.recovery_duration = recovery_duration
        self.index = 0
        self.phase = "idle"
        self.deadline = 0.0
        self.saw_zero_demand = False
        self.completed = False

    def start(self, now: float) -> None:
        self.index = 0
        self.phase = "before_fault"
        self.deadline = now + self.start_delay
        self.saw_zero_demand = False
        self.completed = False

    def update(self, now: float, demand_value: int) -> tuple[list[tuple[str, int]], bool]:
        if self.phase == "idle":
            raise RuntimeError("keypad fault sequence has not been started")
        if self.completed:
            return [], False

        events: list[tuple[str, int]] = []
        completed_now = False

        if self.phase in ("before_fault", "recovering") and now >= self.deadline:
            code = self.codes[self.index]
            events.append(("set", code))
            self.phase = "fault"
            self.deadline = now + self.fault_duration
            self.saw_zero_demand = demand_value == 0
        elif self.phase == "fault":
            if demand_value == 0:
                self.saw_zero_demand = True
            if now >= self.deadline:
                events.append(("clear", 0))
                self.phase = "waiting_for_restart"
        elif self.phase == "waiting_for_restart":
            if demand_value == 0:
                self.saw_zero_demand = True
            elif self.saw_zero_demand:
                events.append(("restart", self.codes[self.index]))
                self.index += 1
                if self.index == len(self.codes):
                    self.completed = True
                    completed_now = True
                else:
                    self.phase = "recovering"
                    self.deadline = now + self.recovery_duration

        return events, completed_now


class EventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.started = time.monotonic()
        self.file = path.open("w", encoding="ascii", newline="\n")

    def write(self, event: str, details: str, raw: bytes | None = None) -> None:
        elapsed_ms = (time.monotonic() - self.started) * 1000.0
        suffix = f"  {raw.hex(' ').upper()}" if raw is not None else ""
        self.file.write(f"{elapsed_ms:11.2f}  {event:<12} {details}{suffix}\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


def describe_fault(code: int) -> str:
    if not code:
        return "None"
    return f"E{code:03d}: {FAULT_DESCRIPTIONS.get(code, 'Unknown pump fault/status')}"


def print_status(
    model: PumpModel,
    runtime: RuntimeState,
    stats: Statistics,
    parser: RequestStreamParser,
) -> None:
    link = "ONLINE" if runtime.online else "OFFLINE (replies suppressed)"
    print(
        f"[{link}] state={model.state_name()} demand={value_to_rpm(stats.last_demand_value):.0f} "
        f"accepted={value_to_rpm(model.accepted_value):.0f} actual={model.actual_rpm:.0f} RPM "
        f"fault={describe_fault(model.fault_code)} requests={stats.requests} replies={stats.replies} "
        f"dropped={stats.dropped} bad_rx={parser.invalid_candidates}"
    )


def read_console_keys() -> Iterable[str]:
    if os.name != "nt" or not sys.stdin.isatty():
        return ()
    import msvcrt

    keys: list[str] = []
    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in ("\x00", "\xe0"):
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        keys.append(key.lower())
    return keys


def handle_key(
    key: str,
    model: PumpModel,
    runtime: RuntimeState,
    stats: Statistics,
    parser: RequestStreamParser,
) -> None:
    if key == "q":
        runtime.quit_requested = True
    elif key == "o":
        runtime.online = not runtime.online
        print(f"Emulated link is now {'ONLINE' if runtime.online else 'OFFLINE'}.")
    elif key == "f":
        try:
            current = FAULT_CYCLE.index(model.fault_code)
            model.fault_code = FAULT_CYCLE[(current + 1) % len(FAULT_CYCLE)]
        except ValueError:
            model.fault_code = FAULT_CYCLE[0]
        print(f"Injecting {describe_fault(model.fault_code)}.")
    elif key == "c":
        model.fault_code = 0
        print("Fault cleared. The attached controller must issue RUN again after stopping.")
    elif key == "d":
        runtime.drop_next += 1
        print(f"Will drop the next {runtime.drop_next} reply/replies.")
    elif key == "x":
        runtime.corrupt_next += 1
        print(f"Will corrupt CRC on the next {runtime.corrupt_next} reply/replies.")
    elif key == "r":
        model.force_stopped(time.monotonic())
        print("Actual RPM forced to zero; accepted demand is unchanged.")
    elif key == "v":
        runtime.verbose = not runtime.verbose
        print(f"Per-frame console logging {'enabled' if runtime.verbose else 'disabled'}.")
    elif key == "s":
        print_status(model, runtime, stats, parser)
    elif key in ("h", "?"):
        print("Keys: S=status F=next fault C=clear O=offline D=drop X=bad CRC R=zero RPM V=verbose Q=quit")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Emulate the iLiving pump for a keypad or controller through a USB-RS485 adapter."
    )
    parser.add_argument("--port", default="COM3", help="Waveshare serial port (default: COM3)")
    parser.add_argument("--baud", type=int, default=38400, help="Serial baud rate (default: 38400)")
    parser.add_argument(
        "--reply-delay-ms",
        type=float,
        default=5.0,
        help="Delay after a complete request before transmitting (default: 5 ms)",
    )
    parser.add_argument(
        "--ramp-rpm-per-sec",
        type=float,
        default=1200.0,
        help="Acceleration and deceleration rate; zero is immediate (default: 1200)",
    )
    parser.add_argument("--initial-rpm", type=float, default=0.0, help="Initial simulated actual RPM")
    parser.add_argument("--fault-code", type=int, default=0, help="Initial reply fault/status byte")
    parser.add_argument(
        "--fault-demo",
        action="store_true",
        help="Cycle through every documented keypad fault, clearing between codes",
    )
    parser.add_argument(
        "--keypad-fault-demo",
        action="store_true",
        help="Cycle faults only after the original keypad is cleared and restarted",
    )
    parser.add_argument(
        "--fault-start-delay-seconds",
        type=float,
        default=2.0,
        help="Online settling time before the fault demo starts (default: 2)",
    )
    parser.add_argument(
        "--fault-hold-seconds",
        type=float,
        default=3.0,
        help="Seconds to hold each injected fault (default: 3)",
    )
    parser.add_argument(
        "--fault-clear-seconds",
        type=float,
        default=1.0,
        help="Seconds to send clear replies between faults (default: 1)",
    )
    parser.add_argument(
        "--keypad-recovery-seconds",
        type=float,
        default=2.0,
        help="Clear running time before the next keypad fault (default: 2)",
    )
    parser.add_argument(
        "--exit-after-fault-demo",
        action="store_true",
        help="Exit after the final fault has been cleared",
    )
    parser.add_argument(
        "--summary-interval",
        type=float,
        default=2.0,
        help="Seconds between console summaries; zero disables them",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every request and reply")
    parser.add_argument("--log", type=Path, help="ASCII event log path")
    parser.add_argument("--list-ports", action="store_true", help="List serial ports and exit")
    parser.add_argument("--list-faults", action="store_true", help="List documented keypad fault codes and exit")
    return parser


def list_serial_ports() -> int:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed. Use the project's ESPHome Python environment.", file=sys.stderr)
        return 2

    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports detected.")
        return 0
    for port in ports:
        print(f"{port.device:<8} {port.description} [{port.hwid}]")
    return 0


def list_faults() -> int:
    print("Code  Keypad fault description")
    for code, description in FAULT_DESCRIPTIONS.items():
        print(f"E{code:03d}  {description}")
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        import serial
    except ImportError:
        print("pyserial is not installed. Install requirements-emulator.txt in your Python environment.", file=sys.stderr)
        return 2

    if not 0 <= args.initial_rpm <= MAX_RPM:
        print(f"--initial-rpm must be between 0 and {MAX_RPM:.0f}.", file=sys.stderr)
        return 2
    if not 0 <= args.fault_code <= 255:
        print("--fault-code must be between 0 and 255.", file=sys.stderr)
        return 2
    selected_fault_modes = int(args.fault_code != 0) + int(args.fault_demo) + int(args.keypad_fault_demo)
    if selected_fault_modes > 1:
        print("--fault-code, --fault-demo, and --keypad-fault-demo are mutually exclusive.", file=sys.stderr)
        return 2
    if (
        args.reply_delay_ms < 0
        or args.ramp_rpm_per_sec < 0
        or args.summary_interval < 0
        or args.fault_start_delay_seconds < 0
        or args.fault_hold_seconds <= 0
        or args.fault_clear_seconds < 0
        or args.keypad_recovery_seconds < 0
    ):
        print("Delay, ramp rate, and summary interval cannot be negative.", file=sys.stderr)
        return 2

    log_path = args.log or Path(f"pump-emulator-{datetime.now():%Y%m%d_%H%M%S}.log")
    model = PumpModel(args.initial_rpm, args.ramp_rpm_per_sec)
    model.fault_code = args.fault_code
    runtime = RuntimeState(verbose=args.verbose)
    stats = Statistics()
    stream = RequestStreamParser()
    event_log = EventLog(log_path)
    fault_sequence = (
        FaultSequence(
            FAULT_CYCLE,
            start_delay=args.fault_start_delay_seconds,
            fault_duration=args.fault_hold_seconds,
            clear_duration=args.fault_clear_seconds,
        )
        if args.fault_demo
        else None
    )
    keypad_fault_sequence = (
        KeypadFaultSequence(
            FAULT_CYCLE,
            start_delay=args.fault_start_delay_seconds,
            fault_duration=args.fault_hold_seconds,
            recovery_duration=args.keypad_recovery_seconds,
        )
        if args.keypad_fault_demo
        else None
    )

    try:
        port = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=0.1,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
    except serial.SerialException as error:
        event_log.close()
        print(f"Could not open {args.port}: {error}", file=sys.stderr)
        print("Run with --list-ports after plugging in the Waveshare adapter.", file=sys.stderr)
        return 2

    try:
        try:
            port.dtr = False
            port.rts = False
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
        port.reset_input_buffer()

        print("iLiving pump emulator")
        print(f"Port: {args.port} at {args.baud} 8N1")
        print(f"Reply delay: {args.reply_delay_ms:g} ms  Ramp: {args.ramp_rpm_per_sec:g} RPM/s")
        print(f"Log: {log_path.resolve()}")
        if fault_sequence is not None:
            print(
                f"Fault demo: {len(FAULT_CYCLE)} codes, {args.fault_hold_seconds:g}s fault / "
                f"{args.fault_clear_seconds:g}s clear"
            )
        if keypad_fault_sequence is not None:
            print(
                f"Keypad fault demo: {len(FAULT_CYCLE)} codes, {args.fault_hold_seconds:g}s fault; "
                "waiting for a zero-demand acknowledgement and restart between codes"
            )
        print("Keys: S=status F=next fault C=clear O=offline D=drop X=bad CRC R=zero RPM V=verbose Q=quit")
        print("Waiting for validated 14-byte keypad/controller requests...")
        event_log.write(
            "START",
            f"port={args.port} baud={args.baud} delay_ms={args.reply_delay_ms:g} ramp={args.ramp_rpm_per_sec:g}",
        )

        last_summary = time.monotonic()
        if fault_sequence is not None:
            fault_sequence.start(last_summary)
        if keypad_fault_sequence is not None:
            keypad_fault_sequence.start(last_summary)
        while not runtime.quit_requested:
            now = time.monotonic()
            if fault_sequence is not None:
                transitions, completed_now = fault_sequence.update(now)
                for code in transitions:
                    model.fault_code = code
                    if code:
                        message = f"Injecting {describe_fault(code)}."
                        event_log.write("FAULT_SET", f"code={code} description={FAULT_DESCRIPTIONS[code]}")
                    else:
                        message = "Fault cleared for the inter-fault recovery interval."
                        event_log.write("FAULT_CLEAR", "code=0")
                    print(message)
                if completed_now:
                    print("Fault demo complete; final state is clear.")
                    event_log.write("FAULT_DONE", f"count={len(FAULT_CYCLE)}")
                    if args.exit_after_fault_demo:
                        runtime.quit_requested = True
                        break

            if keypad_fault_sequence is not None:
                keypad_events, completed_now = keypad_fault_sequence.update(now, stats.last_demand_value)
                for event, code in keypad_events:
                    if event == "set":
                        model.fault_code = code
                        print(f"Injecting {describe_fault(code)}.")
                        event_log.write("FAULT_SET", f"code={code} description={FAULT_DESCRIPTIONS[code]}")
                    elif event == "clear":
                        model.fault_code = 0
                        print("Fault replies cleared. Press keypad power once to acknowledge, then again to restart.")
                        event_log.write("FAULT_CLEAR", "code=0 waiting_for=keypad_restart")
                    elif event == "restart":
                        print(f"Keypad restarted after E{code:03d}; preparing the next fault.")
                        event_log.write("KEYPAD_RUN", f"after_code={code}")
                if completed_now:
                    print("Keypad fault demo complete; final state is clear and running.")
                    event_log.write("FAULT_DONE", f"count={len(FAULT_CYCLE)} mode=keypad")
                    if args.exit_after_fault_demo:
                        runtime.quit_requested = True
                        break

            for key in read_console_keys():
                handle_key(key, model, runtime, stats, stream)

            waiting = port.in_waiting
            chunk = port.read(waiting if waiting else 1)
            if not chunk:
                time.sleep(0.001)
            else:
                for request in stream.feed(chunk):
                    now = time.monotonic()
                    stats.requests += 1
                    stats.last_demand_value = request.demand_value
                    if stats.last_sequence is not None and request.sequence != ((stats.last_sequence + 1) & 0xFF):
                        stats.sequence_jumps += 1
                    stats.last_sequence = request.sequence

                    accepted, actual = model.apply_request(request, now)
                    details = (
                        f"seq=0x{request.sequence:02X} status=0x{request.status:02X} "
                        f"demand=0x{request.demand_value:04X}/{request.demand_rpm:.0f}rpm"
                    )
                    event_log.write("RX_REQUEST", details, request.raw)

                    if not runtime.online:
                        stats.dropped += 1
                        event_log.write("TX_SUPPRESS", "reason=offline")
                        continue
                    if runtime.drop_next:
                        runtime.drop_next -= 1
                        stats.dropped += 1
                        event_log.write("TX_SUPPRESS", "reason=drop-next")
                        continue

                    if args.reply_delay_ms:
                        time.sleep(args.reply_delay_ms / 1000.0)
                    reply = bytearray(
                        build_reply(
                            sequence=request.sequence,
                            fault_code=model.fault_code,
                            accepted_value=accepted,
                            actual_value=actual,
                            echoed_value=request.demand_value,
                        )
                    )
                    corrupt = runtime.corrupt_next > 0
                    if corrupt:
                        runtime.corrupt_next -= 1
                        stats.corrupted += 1
                        reply[36] ^= 0x01

                    port.write(reply)
                    port.flush()
                    stats.replies += 1
                    reply_details = (
                        f"seq=0x{request.sequence:02X} fault=0x{model.fault_code:02X} "
                        f"accepted=0x{accepted:04X} actual=0x{actual:04X} "
                        f"state={model.state_name()} corrupted={str(corrupt).lower()}"
                    )
                    event_log.write("TX_REPLY", reply_details, bytes(reply))
                    if runtime.verbose:
                        print(f"RX {details}")
                        print(f"TX {reply_details}")

            now = time.monotonic()
            if args.summary_interval and now - last_summary >= args.summary_interval:
                print_status(model, runtime, stats, stream)
                last_summary = now

    except KeyboardInterrupt:
        print("\nStopping on Ctrl+C.")
    except serial.SerialException as error:
        print(f"\nSerial error: {error}", file=sys.stderr)
        return 1
    finally:
        event_log.write(
            "STOP",
            f"requests={stats.requests} replies={stats.replies} dropped={stats.dropped} "
            f"corrupted={stats.corrupted} invalid={stream.invalid_candidates} "
            f"discarded_bytes={stream.discarded_bytes} sequence_jumps={stats.sequence_jumps}",
        )
        event_log.close()
        port.close()

    print_status(model, runtime, stats, stream)
    print(f"Log written to {log_path.resolve()}")
    return 0


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    if args.list_ports:
        return list_serial_ports()
    if args.list_faults:
        return list_faults()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
