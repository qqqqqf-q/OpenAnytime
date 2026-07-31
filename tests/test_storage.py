import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from openanytime.protocol import decode_packet
from openanytime.scanner import CapturedSample
from openanytime.storage import (
    DataConflictError,
    DatabaseAlreadyExistsError,
    DatabaseNotFoundError,
    backup_database,
    database_summary,
    initialize_database,
    open_database,
    save_sample,
)


SAMPLE = bytes.fromhex("4d0116a4012e79b8d186472e76472e65b82e6a45d1924472")


def captured_sample():
    return CapturedSample(
        captured_at=datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
        name="Anytime-test",
        address="test-address",
        rssi=-55,
        packet=decode_packet(SAMPLE, 121),
    )


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database = self.root / "cgm.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_open_missing_database_does_not_create_file(self):
        with self.assertRaises(DatabaseNotFoundError):
            open_database(self.database, read_only=True)
        self.assertFalse(self.database.exists())

    def test_initialization_refuses_existing_path(self):
        initialize_database(self.database)
        with self.assertRaises(DatabaseAlreadyExistsError):
            initialize_database(self.database)

    def test_save_is_transactional_and_duplicate_is_idempotent(self):
        initialize_database(self.database)
        sample = captured_sample()

        first = save_sample(self.database, sample, reading_interval_seconds=180)
        second = save_sample(self.database, sample, reading_interval_seconds=180)
        summary = database_summary(self.database)

        self.assertTrue(first.scan_inserted)
        self.assertEqual(first.readings_inserted, 6)
        self.assertFalse(second.scan_inserted)
        self.assertEqual(summary.scan_count, 1)
        self.assertEqual(summary.reading_count, 6)

    def test_same_counter_with_different_payload_is_rejected(self):
        initialize_database(self.database)
        sample = captured_sample()
        save_sample(self.database, sample, reading_interval_seconds=180)
        changed_packet = replace(sample.packet, raw_hex="00" * 24)

        with self.assertRaises(DataConflictError):
            save_sample(
                self.database,
                replace(sample, packet=changed_packet),
                reading_interval_seconds=180,
            )

    def test_backup_creates_verified_new_file(self):
        initialize_database(self.database)
        save_sample(self.database, captured_sample(), reading_interval_seconds=180)
        backup = self.root / "cgm.backup.db"

        summary = backup_database(self.database, backup)

        self.assertEqual(summary.integrity, "ok")
        self.assertEqual(summary.reading_count, 6)
        self.assertTrue(backup.is_file())


if __name__ == "__main__":
    unittest.main()
