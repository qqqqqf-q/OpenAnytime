import unittest

from openanytime.protocol import PacketDecodeError, decode_packet


SAMPLE = bytes.fromhex("4d0116a4012e79b8d186472e76472e65b82e6a45d1924472")


class ProtocolTests(unittest.TestCase):
    def test_known_packet_decodes_to_expected_records(self):
        packet = decode_packet(SAMPLE, 121)

        self.assertEqual(packet.counter, 5796)
        self.assertEqual(packet.checksum, 0x72)
        self.assertEqual(
            [record.glucose_mmol for record in packet.records],
            [4.5, 4.5, 4.4, 4.4, 4.3, 4.3],
        )
        self.assertEqual(len(packet.records), 6)

    def test_rejects_truncated_packet(self):
        with self.assertRaises(PacketDecodeError):
            decode_packet(SAMPLE[:-1], 121)

    def test_rejects_unknown_header(self):
        packet = bytes([0]) + SAMPLE[1:]
        with self.assertRaises(PacketDecodeError):
            decode_packet(packet, 121)

    def test_flag_two_packet_decodes_with_same_record_format(self):
        # Captured on a live sensor 2026-07-31; both flags carry identical
        # 6x3-byte records, with independent counter series per flag.
        packet = decode_packet(
            bytes.fromhex("4d011668022bda63d4299d2bd1952e9591d15d942eb5919d"), 121
        )
        self.assertEqual(packet.flag, 2)
        self.assertEqual(packet.counter, 5736)
        self.assertEqual(
            [record.glucose_mmol for record in packet.records],
            [5.2, 5.2, 5.1, 5.0, 4.8, 4.9],
        )

    def test_flag_three_packet_decodes_with_same_record_format(self):
        # Captured on a live sensor 2026-08-01 after the device switched
        # from flag 0x02 to 0x03; identical format, own counter series.
        packet = decode_packet(
            bytes.fromhex("4d0116a30328a9bb287a41285242d579bfd55dbed53a4437"), 121
        )
        self.assertEqual(packet.flag, 3)
        self.assertEqual(packet.counter, 5795)
        self.assertEqual(
            [record.glucose_mmol for record in packet.records],
            [8.7, 8.3, 8.0, 7.7, 7.4, 7.2],
        )

    def test_flag_four_packet_decodes_with_same_record_format(self):
        # Captured on a live sensor 2026-08-01 after the switch to flag 0x04;
        # identical format, own counter series.
        packet = decode_packet(
            bytes.fromhex("4d01163b042a9e70d56272d56d8dd56e70282e7328367352"), 121
        )
        self.assertEqual(packet.flag, 4)
        self.assertEqual(packet.counter, 5691)
        self.assertEqual(
            [record.glucose_mmol for record in packet.records],
            [7.6, 7.6, 7.5, 7.5, 7.7, 7.8],
        )

    def test_rejects_unknown_flag(self):
        packet = SAMPLE[:4] + bytes([0x10]) + SAMPLE[5:]
        with self.assertRaises(PacketDecodeError):
            decode_packet(packet, 121)

    def test_rejects_out_of_range_key(self):
        with self.assertRaises(PacketDecodeError):
            decode_packet(SAMPLE, 256)


if __name__ == "__main__":
    unittest.main()
