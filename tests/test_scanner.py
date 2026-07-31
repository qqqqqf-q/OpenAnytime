import unittest
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from openanytime.config import RuntimeConfig
from openanytime.protocol import MANUFACTURER_ID
from openanytime.scanner import ScanError, scan_once


SAMPLE = bytes.fromhex("4d0116a4012e79b8d186472e76472e65b82e6a45d1924472")


@dataclass
class FakeDevice:
    name: str


@dataclass
class FakeAdvertisement:
    local_name: str
    manufacturer_data: dict
    rssi: int


def config():
    return RuntimeConfig(
        db_path=Path("/tmp/not-used.db"),
        key=121,
        device_name_fragment="anytime",
        timezone=ZoneInfo("UTC"),
        scan_timeout=0.01,
        scan_interval=1.0,
        reading_interval_seconds=180,
    )


class ScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_device_is_a_normal_outcome(self):
        async def discover(**_kwargs):
            return {}

        outcome = await scan_once(config(), discover=discover)
        self.assertIsNone(outcome.sample)
        self.assertEqual(outcome.matching_devices, 0)

    async def test_valid_device_is_decoded(self):
        async def discover(**_kwargs):
            return {
                "address": (
                    FakeDevice("Anytime-test"),
                    FakeAdvertisement(
                        "Anytime-test", {MANUFACTURER_ID: SAMPLE}, -55
                    ),
                )
            }

        outcome = await scan_once(config(), discover=discover)
        self.assertIsNotNone(outcome.sample)
        self.assertEqual(outcome.sample.packet.counter, 5796)

    async def test_invalid_packet_is_ignored(self):
        async def discover(**_kwargs):
            return {
                "address": (
                    FakeDevice("Anytime-test"),
                    FakeAdvertisement("Anytime-test", {MANUFACTURER_ID: b"bad"}, -55),
                )
            }

        outcome = await scan_once(config(), discover=discover)
        self.assertIsNone(outcome.sample)
        self.assertEqual(outcome.matching_devices, 1)
        self.assertEqual(outcome.invalid_packets, 1)

    async def test_platform_failure_is_wrapped(self):
        async def discover(**_kwargs):
            raise OSError("Bluetooth unavailable")

        with self.assertRaises(ScanError):
            await scan_once(config(), discover=discover)

    async def test_unexpected_backend_shape_is_wrapped(self):
        async def discover(**_kwargs):
            return []

        with self.assertRaises(ScanError):
            await scan_once(config(), discover=discover)


if __name__ == "__main__":
    unittest.main()
