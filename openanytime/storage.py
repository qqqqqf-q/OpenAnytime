"""SQLite access that never creates or replaces a database implicitly."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, Optional, Set, Tuple

if TYPE_CHECKING:
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


def save_sample(
    path: Path,
    sample: "CapturedSample",
    *,
    reading_interval_seconds: int,
) -> SaveResult:
    if sample.captured_at.utcoffset() is None:
        raise DataConflictError("captured_at must include timezone information")
    if not sample.packet.records:
        raise DataConflictError("decoded packet has no records")

    with closing(open_database(path, read_only=False)) as connection:
        validate_schema(connection)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_scan = connection.execute(
                "SELECT raw_hex FROM scans WHERE counter = ?",
                (sample.packet.counter,),
            ).fetchone()
            if existing_scan:
                if existing_scan["raw_hex"] != sample.packet.raw_hex:
                    raise DataConflictError(
                        f"counter {sample.packet.counter} already exists with different payload"
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
                    sample.packet.counter,
                    sample.packet.raw_hex,
                    len(sample.packet.records),
                ),
            )

            inserted = 0
            record_count = len(sample.packet.records)
            for position, record in enumerate(sample.packet.records):
                distance = record_count - 1 - position
                reading_index = sample.packet.counter - distance
                reading_time = sample.captured_at - timedelta(
                    seconds=distance * reading_interval_seconds
                )
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
                        sample.packet.counter,
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
