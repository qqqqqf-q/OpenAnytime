import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from openanytime.config import RuntimeConfig
from openanytime.monitoring import run_monitor
from openanytime.scanner import ScanError, ScanOutcome
from openanytime.storage import initialize_database


class MonitoringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "cgm.db"
        initialize_database(self.database)
        self.config = RuntimeConfig(
            db_path=self.database,
            key=121,
            device_name_fragment="anytime",
            timezone=ZoneInfo("UTC"),
            scan_timeout=0.01,
            scan_interval=0.01,
            reading_interval_seconds=180,
        )

    async def asyncTearDown(self):
        self.temporary_directory.cleanup()

    async def test_no_device_does_not_stop_monitor(self):
        calls = 0

        async def scanner(_config):
            nonlocal calls
            calls += 1
            return ScanOutcome(None, 0, 0)

        async def no_sleep(_delay):
            return None

        result = await run_monitor(
            self.config,
            scanner=scanner,
            sleep=no_sleep,
            max_cycles=3,
        )
        self.assertEqual(result, 0)
        self.assertEqual(calls, 3)

    async def test_scan_failures_are_retried(self):
        calls = 0

        async def scanner(_config):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ScanError("temporary failure")
            return ScanOutcome(None, 0, 0)

        async def no_sleep(_delay):
            return None

        result = await run_monitor(
            self.config,
            scanner=scanner,
            sleep=no_sleep,
            max_cycles=2,
        )
        self.assertEqual(result, 0)
        self.assertEqual(calls, 2)

    async def test_once_reports_scan_failure(self):
        async def scanner(_config):
            raise ScanError("temporary failure")

        result = await run_monitor(self.config, scanner=scanner, once=True)
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
