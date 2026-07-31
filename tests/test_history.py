"""Checks for the GATT history channel: frame parsing and backfill storage."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from openanytime.history import (
    HistoryRecord,
    build_check_id,
    build_pull_series,
    build_set_date,
    parse_frame,
    transform,
)
from openanytime.storage import (
    initialize_database,
    open_database,
    save_history_records,
)

KEY = 121
TZ = ZoneInfo("Asia/Shanghai")


def wire_encode(plain: bytes, key: int) -> bytes:
    """The device-side encryption: inverse of transform().

    The reference app decrypts responses with the forward bit-flip
    (our transform), so the wire bytes must be produced with its inverse:
    the same conditional flip scanned right to left, then XOR with key.
    """
    bits = [int(bit) for byte in plain for bit in format(byte, "08b")]
    for index in range(len(bits) - 2, -1, -1):
        if bits[index + 1] == 0:
            bits[index] ^= 1
    return bytes(
        int("".join(map(str, bits[i : i + 8])), 2) ^ key
        for i in range(0, len(bits), 8)
    )


def make_record_block(
    *,
    ib: float,
    iw: float,
    temperature: float,
    trend: int,
    glucose_raw: int,
    error_code: int,
    voltage_tail: bytes = b"\x00\x00\x00\x00",
) -> bytes:
    ib_raw = round(ib * 100)
    iw_raw = round(iw * 100)
    temp_int = round(temperature) + 40
    temp_frac = round((temperature - round(temperature)) * 100)
    packed = bytes(
        [
            (ib_raw >> 8) & 0xFF,
            ib_raw & 0xFF,
            (iw_raw >> 8) & 0xFF,
            iw_raw & 0xFF,
            temp_int & 0xFF,
            temp_frac & 0xFF,
            ((trend & 0xF) << 4) | ((glucose_raw >> 8) & 0xF),
            glucose_raw & 0xFF,
            error_code & 0xFF,
            0x00,
            0x00,
        ]
    )
    return packed + voltage_tail


def make_frame(start_id: int, blocks: list[bytes], key: int = KEY) -> bytes:
    # Records travel encrypted; 0xFC padding is appended in the clear, which
    # is exactly how the parser can spot it before decryption.
    region = b"".join(
        block if block == b"\xfc" * len(block) else wire_encode(block, key)
        for block in blocks
    )
    body = bytes([0x37, start_id & 0xFF, (start_id >> 8) & 0xFF]) + region
    return body + bytes([sum(body) & 0xFF])


class TransformTests(unittest.TestCase):
    def test_wire_roundtrip(self) -> None:
        for key in (0, 1, 121, 255):
            plain = bytes(range(15))
            self.assertEqual(transform(wire_encode(plain, key), key), plain)

    def test_command_builders(self) -> None:
        self.assertEqual(build_check_id([0, 0, 0, 0]).hex(), "310000000031")
        self.assertEqual(build_pull_series(0, 45).hex(), "3700002d64")
        set_date = build_set_date(datetime(2026, 7, 31, 16, 38, 20))
        self.assertEqual(set_date[0], 0x03)
        self.assertEqual(set_date[1], 126)  # year - 1900
        self.assertEqual(set_date[-1], sum(set_date[:-1]) & 0xFF)


class ParseFrameTests(unittest.TestCase):
    def test_voltage_mode_records(self) -> None:
        blocks = [
            make_record_block(
                ib=0.0,
                iw=6.16,
                temperature=30.4,
                trend=0,
                glucose_raw=0,
                error_code=0,
            ),
            make_record_block(
                ib=0.0,
                iw=7.68,
                temperature=31.5,
                trend=4,
                glucose_raw=86,
                error_code=0,
            ),
        ]
        frame = make_frame(0, blocks)
        start_id, records = parse_frame(frame, KEY)
        self.assertEqual(start_id, 0)
        self.assertEqual(len(records), 2)
        first, second = records
        self.assertEqual(first.glucose_id, 0)
        self.assertAlmostEqual(first.iw, 6.16, places=2)
        self.assertAlmostEqual(first.temperature_c, 30.4, places=1)
        self.assertEqual(second.glucose_id, 1)
        self.assertAlmostEqual(second.iw, 7.68, places=2)
        self.assertEqual(second.trend, 4)
        self.assertEqual(second.glucose_coarse, round(86 / 18.0, 1))

    def test_fc_padding_ends_the_stream(self) -> None:
        blocks = [
            make_record_block(
                ib=0.0, iw=5.0, temperature=30.0, trend=0,
                glucose_raw=50, error_code=0,
            ),
            b"\xfc" * 15,
        ]
        _, records = parse_frame(make_frame(10, blocks), KEY)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].glucose_id, 10)

    def test_non_pull_frame_is_rejected(self) -> None:
        self.assertEqual(parse_frame(b"\x31\x00\x00\x00\x00\x01\x32", KEY), (None, []))


class SaveHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "cgm.db"
        initialize_database(self.db)
        self.init_time = datetime(2026, 7, 30, 12, 19, tzinfo=TZ)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def record(self, glucose_id: int, iw: float) -> HistoryRecord:
        return HistoryRecord(
            glucose_id=glucose_id,
            iw=iw,
            ib=0.0,
            temperature_c=30.0,
            glucose_coarse=3.0,
            trend=4,
            error_code=0,
        )

    def test_insert_and_idempotency(self) -> None:
        records = [self.record(0, 6.16), self.record(1, 6.43)]
        inserted = save_history_records(
            self.db, records, init_time=self.init_time, reading_interval_seconds=180
        )
        self.assertEqual(inserted, 2)
        again = save_history_records(
            self.db, records, init_time=self.init_time, reading_interval_seconds=180
        )
        self.assertEqual(again, 0)

        from contextlib import closing

        with closing(open_database(self.db, read_only=True)) as connection:
            rows = connection.execute(
                "SELECT timestamp, counter, reading_index, glucose_mmol "
                "FROM readings ORDER BY reading_index"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["counter"], -1)
        self.assertEqual(rows[0]["reading_index"], 0)
        self.assertAlmostEqual(rows[0]["glucose_mmol"], 6.16, places=2)
        expected = (self.init_time + timedelta(minutes=3)).isoformat()
        self.assertEqual(rows[0]["timestamp"], expected)

    def test_timestamp_overlap_with_broadcast_row_is_skipped(self) -> None:
        from contextlib import closing

        covered_time = self.init_time + timedelta(minutes=6)  # id 1's slot
        with closing(open_database(self.db, read_only=False)) as connection:
            connection.execute(
                "INSERT INTO readings (timestamp, counter, reading_index,"
                " glucose_mmol, glucose_mg, temperature_c, rssi, raw_hex)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (covered_time.isoformat(), 5800, 5800, 4.5, 81, 30.0, -70, "ab"),
            )
            connection.commit()
        inserted = save_history_records(
            self.db,
            [self.record(0, 6.16), self.record(1, 6.43), self.record(2, 6.62)],
            init_time=self.init_time,
            reading_interval_seconds=180,
        )
        # id 1's timestamp is covered by the broadcast row, so only 0 and 2 land.
        self.assertEqual(inserted, 2)


if __name__ == "__main__":
    unittest.main()
