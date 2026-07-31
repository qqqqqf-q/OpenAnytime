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

    def test_rejects_unknown_flag(self):
        packet = SAMPLE[:4] + bytes([2]) + SAMPLE[5:]
        with self.assertRaises(PacketDecodeError):
            decode_packet(packet, 121)

    def test_rejects_out_of_range_key(self):
        with self.assertRaises(PacketDecodeError):
            decode_packet(SAMPLE, 256)


if __name__ == "__main__":
    unittest.main()
