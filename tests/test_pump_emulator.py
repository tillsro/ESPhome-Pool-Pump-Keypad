import unittest

import pump_emulator as emulator


SPEED1_REQUEST = bytes.fromhex(
    "01 70 01 00 00 0C 00 00 0C 3A C4 00 DF 58"
)

SPEED1_REPLY = bytes.fromhex(
    "01 70 01 00 00 0C 00 65 00 02 00 65 0C 3A 0C 39 "
    "0A 7F 00 E4 0C 9F 01 0D 01 90 01 5F 00 45 00 00 "
    "0C 3A 77 05 85 76"
)


class ProtocolTests(unittest.TestCase):
    def test_known_request_decodes(self) -> None:
        request = emulator.decode_request(SPEED1_REQUEST)

        self.assertEqual(request.sequence, 1)
        self.assertEqual(request.status, 0)
        self.assertEqual(request.demand_value, 0x0C3A)
        self.assertAlmostEqual(request.demand_rpm, 1800.0, delta=0.5)

    def test_request_rejects_bad_additive_checksum(self) -> None:
        request = bytearray(SPEED1_REQUEST)
        request[10] ^= 0x01

        with self.assertRaisesRegex(ValueError, "additive checksum"):
            emulator.decode_request(bytes(request))

    def test_request_rejects_bad_crc(self) -> None:
        request = bytearray(SPEED1_REQUEST)
        request[12] ^= 0x01

        with self.assertRaisesRegex(ValueError, "CRC"):
            emulator.decode_request(bytes(request))

    def test_generated_reply_matches_pump_capture(self) -> None:
        reply = emulator.build_reply(
            sequence=1,
            fault_code=0,
            accepted_value=0x0C3A,
            actual_value=0x0C39,
            echoed_value=0x0C3A,
        )

        self.assertEqual(reply, SPEED1_REPLY)
        self.assertTrue(emulator.validate_reply(reply))

    def test_generated_fault_reply_is_valid(self) -> None:
        reply = emulator.build_reply(
            sequence=0xFE,
            fault_code=14,
            accepted_value=0,
            actual_value=0x0910,
            echoed_value=0x0C3A,
        )

        self.assertTrue(emulator.validate_reply(reply))
        self.assertEqual(reply[2], 0xFE)
        self.assertEqual(reply[3], 14)
        self.assertEqual(emulator.read_be16(reply, 12), 0)
        self.assertEqual(emulator.read_be16(reply, 14), 0x0910)
        self.assertEqual(emulator.read_be16(reply, 32), 0x0C3A)

    def test_every_documented_fault_builds_a_valid_reply(self) -> None:
        for code in emulator.FAULT_DESCRIPTIONS:
            with self.subTest(code=code):
                reply = emulator.build_reply(
                    sequence=0x42,
                    fault_code=code,
                    accepted_value=0,
                    actual_value=0,
                    echoed_value=0,
                )

                self.assertTrue(emulator.validate_reply(reply))
                self.assertEqual(reply[3], code)

    def test_rpm_scaling_matches_controller(self) -> None:
        self.assertEqual(emulator.rpm_to_value(1400), 0x0982)
        self.assertEqual(emulator.rpm_to_value(1800), 0x0C3A)
        self.assertEqual(emulator.rpm_to_value(2000), 0x0D96)
        self.assertEqual(emulator.rpm_to_value(3450), 0x1770)


class StreamParserTests(unittest.TestCase):
    def test_parser_recovers_split_request_after_noise(self) -> None:
        parser = emulator.RequestStreamParser()

        self.assertEqual(parser.feed(b"\x99\x00" + SPEED1_REQUEST[:5]), [])
        requests = parser.feed(SPEED1_REQUEST[5:])

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].demand_value, 0x0C3A)
        self.assertEqual(parser.discarded_bytes, 2)

    def test_parser_skips_bad_candidate_and_finds_next_request(self) -> None:
        parser = emulator.RequestStreamParser()
        bad = bytearray(SPEED1_REQUEST)
        bad[-1] ^= 0x01

        requests = parser.feed(bytes(bad) + SPEED1_REQUEST)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].sequence, 1)
        self.assertEqual(parser.invalid_candidates, 1)


class PumpModelTests(unittest.TestCase):
    def test_model_ramps_to_demand_and_back_to_stop(self) -> None:
        request = emulator.decode_request(SPEED1_REQUEST)
        model = emulator.PumpModel(initial_rpm=0, ramp_rpm_per_second=1000)

        accepted, actual = model.apply_request(request, now=0.0)
        self.assertEqual(accepted, 0x0C3A)
        self.assertEqual(actual, 0)
        self.assertEqual(model.state_name(), "RAMPING")

        model.apply_request(request, now=1.0)
        self.assertAlmostEqual(model.actual_rpm, 1000.0)
        model.apply_request(request, now=2.0)
        self.assertAlmostEqual(model.actual_rpm, 1800.0, delta=0.5)
        self.assertEqual(model.state_name(), "RUNNING")

        stop_frame = bytearray(SPEED1_REQUEST)
        stop_frame[8:10] = b"\x00\x00"
        additive = sum(stop_frame[:10]) & 0xFFFF
        stop_frame[10] = additive & 0xFF
        stop_frame[11] = additive >> 8
        crc = emulator.modbus_crc16(stop_frame[:12])
        stop_frame[12] = crc & 0xFF
        stop_frame[13] = crc >> 8
        stop_request = emulator.decode_request(bytes(stop_frame))

        accepted, _ = model.apply_request(stop_request, now=2.1)
        self.assertEqual(accepted, 0)
        self.assertEqual(model.state_name(), "STOPPING")
        model.apply_request(stop_request, now=4.1)
        self.assertEqual(model.actual_rpm, 0)
        self.assertEqual(model.state_name(), "STOPPED")

    def test_fault_rejects_demand(self) -> None:
        request = emulator.decode_request(SPEED1_REQUEST)
        model = emulator.PumpModel(initial_rpm=1800, ramp_rpm_per_second=1000)
        model.fault_code = 14

        accepted, actual = model.apply_request(request, now=0.0)

        self.assertEqual(accepted, 0)
        self.assertEqual(actual, 0x0C3A)
        self.assertEqual(model.target_rpm, 0)
        self.assertEqual(model.state_name(), "E014")


class FaultSequenceTests(unittest.TestCase):
    def test_sequence_injects_and_clears_each_fault(self) -> None:
        sequence = emulator.FaultSequence(
            codes=(1, 2),
            start_delay=1.0,
            fault_duration=2.0,
            clear_duration=0.5,
        )
        sequence.start(10.0)

        self.assertEqual(sequence.update(10.9), ([], False))
        self.assertEqual(sequence.update(11.0), ([1], False))
        self.assertEqual(sequence.update(13.0), ([0], False))
        self.assertEqual(sequence.update(13.5), ([2], False))
        self.assertEqual(sequence.update(15.5), ([0], False))
        self.assertEqual(sequence.update(16.0), ([], True))
        self.assertEqual(sequence.update(20.0), ([], False))

    def test_sequence_catches_up_after_a_delayed_update(self) -> None:
        sequence = emulator.FaultSequence(
            codes=(6, 9),
            start_delay=0.0,
            fault_duration=1.0,
            clear_duration=0.0,
        )
        sequence.start(5.0)

        self.assertEqual(sequence.update(7.0), ([6, 0, 9, 0], True))


class KeypadFaultSequenceTests(unittest.TestCase):
    def test_sequence_waits_for_stop_then_restart(self) -> None:
        sequence = emulator.KeypadFaultSequence(
            codes=(1, 2),
            start_delay=1.0,
            fault_duration=2.0,
            recovery_duration=0.5,
        )
        sequence.start(10.0)

        self.assertEqual(sequence.update(10.9, 0x1770), ([], False))
        self.assertEqual(sequence.update(11.0, 0x1770), ([("set", 1)], False))
        self.assertEqual(sequence.update(12.0, 0), ([], False))
        self.assertEqual(sequence.update(13.0, 0), ([("clear", 0)], False))
        self.assertEqual(sequence.update(20.0, 0), ([], False))
        self.assertEqual(sequence.update(20.1, 0x1770), ([("restart", 1)], False))
        self.assertEqual(sequence.update(20.6, 0x1770), ([("set", 2)], False))
        self.assertEqual(sequence.update(22.6, 0), ([("clear", 0)], False))
        self.assertEqual(sequence.update(23.0, 0x1770), ([("restart", 2)], True))
        self.assertEqual(sequence.update(30.0, 0x1770), ([], False))

    def test_sequence_does_not_treat_continuing_run_as_acknowledgement(self) -> None:
        sequence = emulator.KeypadFaultSequence(
            codes=(14,),
            start_delay=0.0,
            fault_duration=1.0,
            recovery_duration=0.0,
        )
        sequence.start(5.0)

        self.assertEqual(sequence.update(5.0, 0x1770), ([("set", 14)], False))
        self.assertEqual(sequence.update(6.0, 0x1770), ([("clear", 0)], False))
        self.assertEqual(sequence.update(7.0, 0x1770), ([], False))
        self.assertEqual(sequence.update(7.1, 0), ([], False))
        self.assertEqual(sequence.update(7.2, 0x1770), ([("restart", 14)], True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
