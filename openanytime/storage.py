"""SQLite access that never creates or replaces a database implicitly."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, Optional, Set, Tuple

if TYPE_CHECKING:
    from .history import HistoryRecord
    from .scanner import CapturedSample

SCHEMA_VERSION = 1
EXPECTED_COLUMNS: Dict[str, Set[str]] = {
    "readings": {
        "id",
        "timestamp",
        "counter",
        "reading_index",
        "glucose_mmol",
        "glucose_mg",
        "temperature_c",
        "rssi",
        "raw_hex",
    },
    "scans": {
        "id",
        "timestamp",
        "name",
        "address",
        "rssi",
        "counter",
        "raw_hex",
        "record_count",
    },
}

SCHEMA_SQL = """
CREATE TABLE readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    counter INTEGER NOT NULL,
    reading_index INTEGER NOT NULL UNIQUE,
    glucose_mmol REAL NOT NULL,
    glucose_mg INTEGER NOT NULL,
    temperature_c REAL NOT NULL,
    rssi INTEGER NOT NULL,
    raw_hex TEXT NOT NULL
);

CREATE TABLE scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    rssi INTEGER NOT NULL,
    counter INTEGER NOT NULL UNIQUE,
    raw_hex TEXT NOT NULL,
    record_count INTEGER NOT NULL
);

CREATE INDEX readings_timestamp_idx ON readings(timestamp);
CREATE INDEX scans_timestamp_idx ON scans(timestamp);
PRAGMA user_version = 1;
"""


class StorageError(RuntimeError):
    """Base class for storage failures with actionable semantics."""


class DatabaseNotFoundError(StorageError):
    pass


class DatabaseAlreadyExistsError(StorageError):
    pass


class SchemaError(StorageError):
    pass


class DataConflictError(StorageError):
    pass


@dataclass(frozen=True)
class SchemaStatus:
    version: int
    is_legacy: bool


@dataclass(frozen=True)
class SaveResult:
    scan_inserted: bool
    readings_inserted: int


@dataclass(frozen=True)
class DatabaseSummary:
    path: Path
    size_bytes: int
    version: int
    integrity: str
    reading_count: int
    scan_count: int
    first_timestamp: Optional[str]
    last_timestamp: Optional[str]


def _database_uri(path: Path, mode: str) -> str:
    return f"{path.expanduser().resolve().as_uri()}?mode={mode}"


def open_database(path: Path, *, read_only: bool) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise DatabaseNotFoundError(f"database does not exist: {resolved}")

    mode = "ro" if read_only else "rw"
    connection = None
    try:
        connection = sqlite3.connect(
            _database_uri(resolved, mode),
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise StorageError(f"unable to open database {resolved}: {exc}") from exc


def _table_columns(connection: sqlite3.Connection, table: str) -> Set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info('{table}')")}


def _unique_indexes(
    connection: sqlite3.Connection, table: str
) -> Iterable[Tuple[str, ...]]:
    for row in connection.execute(f"PRAGMA index_list('{table}')"):
        if not row[2]:
            continue
        index_name = row[1].replace("'", "''")
        columns = tuple(
            index_row[2]
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')")
        )
        yield columns


def validate_schema(connection: sqlite3.Connection) -> SchemaStatus:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = set(EXPECTED_COLUMNS) - tables
    if missing_tables:
        raise SchemaError(f"database is missing tables: {sorted(missing_tables)}")

    for table, expected in EXPECTED_COLUMNS.items():
        actual = _table_columns(connection, table)
        if actual != expected:
            raise SchemaError(
                f"unexpected {table} columns: expected {sorted(expected)}, got {sorted(actual)}"
            )

    if ("reading_index",) not in set(_unique_indexes(connection, "readings")):
        raise SchemaError("readings.reading_index must be unique")
    if ("counter",) not in set(_unique_indexes(connection, "scans")):
        raise SchemaError("scans.counter must be unique")

    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in (0, SCHEMA_VERSION):
        raise SchemaError(
            f"unsupported schema version {version}; expected 0 or {SCHEMA_VERSION}"
        )
    return SchemaStatus(version=version, is_legacy=version == 0)


def initialize_database(path: Path) -> SchemaStatus:
    resolved = path.expanduser().resolve()
    if resolved.exists():
        raise DatabaseAlreadyExistsError(f"refusing to replace existing file: {resolved}")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = sqlite3.connect(resolved)
    except sqlite3.Error as exc:
        raise StorageError(f"unable to initialize database {resolved}: {exc}") from exc
    try:
        connection.executescript(SCHEMA_SQL)
        connection.commit()
        return validate_schema(connection)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _stored_counter(flag: int, counter: int) -> int:
    """Namespace the device counter by packet flag for database identity.

    Each flag carries an independent counter series (see protocol.py), so the
    raw counter alone is not unique across flags. Flag 0x01 keeps the
    historical numbering already present in production databases; other
    flags are offset into their own range. The raw counter remains
    recoverable from raw_hex if ever needed.
    """
    return counter if flag == 0x01 else counter + flag * 100_000


def save_sample(
    path: Path,
    sample: "CapturedSample",
    *,
    reading_interval_seconds: int,
    init_time: Optional[datetime] = None,
) -> SaveResult:
    if sample.captured_at.utcoffset() is None:
        raise DataConflictError("captured_at must include timezone information")
    if not sample.packet.records:
        raise DataConflictError("decoded packet has no records")

    stored_counter = _stored_counter(sample.packet.flag, sample.packet.counter)

    # When a history (counter=-1) row already covers a slot, the broadcast
    # record for that same reading is redundant. Broadcast timestamps are
    # capture-anchored estimates while history rows sit exactly on the grid,
    # so a missed skip here shows up as ~1-minute duplicate points. The
    # check looks one slot to each side because the configured init time's
    # seconds are approximate; over-skipping is safe — the next backfill
    # re-inserts any slot left uncovered, with a grid-exact timestamp.
    history_slots: Set[int] = set()
    grid_step = timedelta(seconds=reading_interval_seconds)

    def history_covers(reading_time: datetime) -> bool:
        if init_time is None:
            return False
        slot = int((reading_time - init_time) // grid_step) - 1
        return bool(history_slots & {slot - 1, slot, slot + 1})

    with closing(open_database(path, read_only=False)) as connection:
        validate_schema(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_scan = connection.execute(
                "SELECT raw_hex FROM scans WHERE counter = ?",
                (stored_counter,),
            ).fetchone()
            if existing_scan:
                if existing_scan["raw_hex"] != sample.packet.raw_hex:
                    raise DataConflictError(
                        f"counter {stored_counter} already exists with different payload"
                    )
                connection.rollback()
                return SaveResult(scan_inserted=False, readings_inserted=0)

            connection.execute(
                """
                INSERT INTO scans
                    (timestamp, name, address, rssi, counter, raw_hex, record_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample.captured_at.isoformat(),
                    sample.name,
                    sample.address,
                    sample.rssi,
                    stored_counter,
                    sample.packet.raw_hex,
                    len(sample.packet.records),
                ),
            )

            if init_time is not None:
                history_slots.update(
                    row[0]
                    for row in connection.execute(
                        "SELECT reading_index FROM readings WHERE counter = -1"
                    )
                )

            inserted = 0
            record_count = len(sample.packet.records)
            for position, record in enumerate(sample.packet.records):
                distance = record_count - 1 - position
                reading_index = stored_counter - distance
                reading_time = sample.captured_at - timedelta(
                    seconds=distance * reading_interval_seconds
                )
                if history_covers(reading_time):
                    continue
                existing_reading = connection.execute(
                    """
                    SELECT glucose_mmol, glucose_mg, temperature_c
                    FROM readings
                    WHERE reading_index = ?
                    """,
                    (reading_index,),
                ).fetchone()
                if existing_reading:
                    observed = (
                        existing_reading["glucose_mmol"],
                        existing_reading["glucose_mg"],
                        existing_reading["temperature_c"],
                    )
                    incoming = (
                        record.glucose_mmol,
                        record.glucose_mg,
                        record.temperature_c,
                    )
                    if observed != incoming:
                        raise DataConflictError(
                            f"reading {reading_index} already exists with different values"
                        )
                    continue

                connection.execute(
                    """
                    INSERT INTO readings
                        (timestamp, counter, reading_index, glucose_mmol,
                         glucose_mg, temperature_c, rssi, raw_hex)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reading_time.isoformat(),
                        stored_counter,
                        reading_index,
                        record.glucose_mmol,
                        record.glucose_mg,
                        record.temperature_c,
                        sample.rssi,
                        sample.packet.raw_hex,
                    ),
                )
                inserted += 1

            connection.commit()
            return SaveResult(scan_inserted=True, readings_inserted=inserted)
        except Exception:
            connection.rollback()
            raise


def save_history_records(
    path: Path,
    records: Iterable["HistoryRecord"],
    *,
    init_time: datetime,
    reading_interval_seconds: int,
) -> int:
    """Insert GATT-pulled history records into readings; returns inserted count.

    Identity model: readings.reading_index holds the broadcast counter for
    live-captured rows and the true session glucoseId for backfilled rows.
    These are different numberings (the broadcast counter is shifted per
    flag), so backfilled rows are marked with counter = -1 and dedup maps
    broadcast rows to true ids via the per-flag offset before comparing.
    Remaining collisions resolve via INSERT OR IGNORE in favor of the
    already-stored row.

    glucose_mmol stores Iw, the same raw-current quantity the broadcast path
    decodes into that column — NOT the compensated value the reference app
    displays (that requires the proprietary algorithm).
    """
    records = list(records)
    if not records:
        return 0
    if init_time.tzinfo is None:
        raise DataConflictError("init_time must include timezone information")

    step = timedelta(seconds=reading_interval_seconds)

    def record_timestamp(record: "HistoryRecord") -> datetime:
        return init_time + (record.glucose_id + 1) * step

    def timestamp_to_slot(timestamp: datetime) -> int:
        # floor, not round: broadcast estimates are always LATE relative to
        # the true grid (a packet can only contain readings that already
        # exist), by anything from 0 to a full interval. Rounding therefore
        # biases derived offsets one slot low; flooring errs only within the
        # init-time seconds uncertainty, and the majority vote absorbs that.
        return int((timestamp - init_time) // step) - 1

    with closing(open_database(path, read_only=False)) as connection:
        validate_schema(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Dedup must map broadcast rows to true glucoseIds, not to
            # timestamp neighborhoods: broadcast timestamps are
            # capture-anchored and jitter around the grid, so any
            # time-proximity rule both duplicates and misses slots. The
            # broadcast counter relates to glucoseId by a fixed per-flag
            # offset (counter = glucoseId + offset), derived here by
            # majority vote over the rows' grid slots; capture anchoring
            # scatters votes across neighbors, but the winner is the exact
            # integer offset (verified against value continuity across the
            # observed flag switch).
            covered = {
                row["reading_index"]
                for row in connection.execute(
                    "SELECT reading_index FROM readings WHERE counter = -1"
                )
            }

            def raw_counter(stored: int) -> Tuple[int, int]:
                """Inverse of _stored_counter: (flag_namespace, raw counter)."""
                if stored < 100_000:
                    return 0, stored
                return stored // 100_000, stored % 100_000

            # Deriving the per-flag counter->glucoseId offset from timestamps
            # is unsound: broadcast est-times are capture-anchored and the
            # scan cadence can make them MORE than one interval late, which
            # biases any floor/round vote. Anchor on physics instead: match
            # broadcast rows to history rows by (glucose, temperature) within
            # a small neighborhood of the timestamp-implied slot, then vote.
            # The offset is an exact integer constant per flag (verified on
            # the 2026-07-31 flag switch: identical reading at flag-1
            # counter 5884 and flag-2 counter 5629).
            reference: Dict[int, Tuple[float, float]] = {}
            for row in connection.execute(
                "SELECT reading_index, glucose_mmol, temperature_c "
                "FROM readings WHERE counter = -1"
            ):
                reference[row["reading_index"]] = (
                    row["glucose_mmol"],
                    row["temperature_c"],
                )
            for record in records:
                reference.setdefault(
                    record.glucose_id, (record.iw, record.temperature_c)
                )

            broadcast_rows = connection.execute(
                "SELECT counter, timestamp, glucose_mmol, temperature_c "
                "FROM readings WHERE counter != -1"
            ).fetchall()
            namespaces = {raw_counter(row["counter"])[0] for row in broadcast_rows}
            for namespace in namespaces:
                members = [
                    row
                    for row in broadcast_rows
                    if raw_counter(row["counter"])[0] == namespace
                ]
                votes: Dict[int, int] = {}
                for row in members:
                    raw = raw_counter(row["counter"])[1]
                    est_slot = timestamp_to_slot(
                        datetime.fromisoformat(row["timestamp"])
                    )
                    matches = [
                        glucose_id
                        for glucose_id in range(est_slot - 3, est_slot + 3)
                        if glucose_id in reference
                        and abs(reference[glucose_id][0] - row["glucose_mmol"]) <= 0.06
                        and abs(reference[glucose_id][1] - row["temperature_c"]) <= 0.11
                    ]
                    if len(matches) == 1:
                        votes[raw - matches[0]] = votes.get(raw - matches[0], 0) + 1
                if votes:
                    best_offset = max(votes, key=votes.get)  # type: ignore[arg-type]
                else:
                    # No history reference to anchor on yet (first backfill
                    # against a broadcast-only database). Under-cover rather
                    # than duplicate: holes heal on the next run, which by
                    # then has history rows to anchor the vote.
                    floor_votes: Dict[int, int] = {}
                    for row in members:
                        raw = raw_counter(row["counter"])[1]
                        slot = timestamp_to_slot(
                            datetime.fromisoformat(row["timestamp"])
                        )
                        floor_votes[raw - slot] = floor_votes.get(raw - slot, 0) + 1
                    best_offset = max(floor_votes, key=floor_votes.get)  # type: ignore[arg-type]
                for row in members:
                    covered.add(raw_counter(row["counter"])[1] - best_offset)
            inserted = 0
            for record in records:
                if record.glucose_id in covered:
                    continue
                timestamp = record_timestamp(record)
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO readings
                        (timestamp, counter, reading_index, glucose_mmol,
                         glucose_mg, temperature_c, rssi, raw_hex)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp.isoformat(),
                        -1,
                        record.glucose_id,
                        record.iw,
                        round(record.iw * 18.0),
                        record.temperature_c,
                        0,
                        "",
                    ),
                )
                if cursor.rowcount:
                    inserted += 1
                    covered.add(record.glucose_id)
            connection.commit()
            return inserted
        except Exception:
            connection.rollback()
            raise


def database_summary(path: Path) -> DatabaseSummary:
    resolved = path.expanduser().resolve()
    with closing(open_database(resolved, read_only=True)) as connection:
        schema = validate_schema(connection)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        reading_row = connection.execute(
            """
            SELECT COUNT(*) AS count, MIN(timestamp) AS first_ts,
                   MAX(timestamp) AS last_ts
            FROM readings
            """
        ).fetchone()
        scan_count = int(connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0])
        return DatabaseSummary(
            path=resolved,
            size_bytes=resolved.stat().st_size,
            version=schema.version,
            integrity=integrity,
            reading_count=int(reading_row["count"]),
            scan_count=scan_count,
            first_timestamp=reading_row["first_ts"],
            last_timestamp=reading_row["last_ts"],
        )


def backup_database(source: Path, destination: Path) -> DatabaseSummary:
    source_path = source.expanduser().resolve()
    destination_path = destination.expanduser().resolve()
    if destination_path.exists():
        raise DatabaseAlreadyExistsError(
            f"refusing to overwrite backup: {destination_path}"
        )
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(open_database(source_path, read_only=True)) as source_connection:
        validate_schema(source_connection)
        with closing(sqlite3.connect(destination_path)) as destination_connection:
            source_connection.backup(destination_connection)

    summary = database_summary(destination_path)
    if summary.integrity != "ok":
        raise StorageError(f"backup failed integrity check: {summary.integrity}")
    return summary


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
