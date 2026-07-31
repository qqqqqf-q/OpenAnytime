"""Long-running monitor loop with bounded retries and fail-closed storage."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from typing import Awaitable, Callable, Optional

from .config import RuntimeConfig
from .history import HistoryPullError, pull_history
from .scanner import ScanError, ScanOutcome, scan_once
from .storage import (
    DataConflictError,
    DatabaseNotFoundError,
    SchemaError,
    open_database,
    save_history_records,
    save_sample,
    validate_schema,
)

logger = logging.getLogger(__name__)

ScannerFunction = Callable[[RuntimeConfig], Awaitable[ScanOutcome]]
SleepFunction = Callable[[float], Awaitable[None]]

# Trailing ids each backfill leaves to the broadcast path. The broadcast
# channel owns the live edge; if history rows were inserted there first,
# later broadcast packets would write the same readings again with
# capture-anchored timestamps (visible as ~1-minute duplicate points).
# Margined ids simply get inserted by the next run once they have aged out.
BACKFILL_LIVE_MARGIN_IDS = 10  # 10 ids * 3 min = 30 minutes


async def run_backfill(config: RuntimeConfig) -> int:
    """Best-effort history repair; returns inserted row count.

    Mirrors the reference architecture: broadcasts are the primary channel,
    and this GATT pull is only the gap repair. It runs on startup (covering
    monitor downtime) and then periodically. Failures must never stop the
    monitor — the broadcast path keeps working without it — so every error
    is logged and swallowed here.
    """
    if config.init_time is None:
        return 0
    try:
        with closing(open_database(config.db_path, read_only=True)) as connection:
            row = connection.execute(
                "SELECT MAX(reading_index) AS latest FROM readings WHERE counter = -1"
            ).fetchone()
        start_id = 0 if row["latest"] is None else int(row["latest"]) + 1
        records = await pull_history(
            key=config.key,
            device_name_fragment=config.device_name_fragment,
            start_id=start_id,
            timezone=config.timezone,
            random_b=config.random_b,
            scan_timeout=config.scan_timeout,
        )
    except (HistoryPullError, sqlite3.DatabaseError) as exc:
        logger.warning("backfill skipped: %s", exc)
        return 0
    if not records:
        logger.info("backfill: no new history records (device latest reached)")
        return 0
    cutoff = records[-1].glucose_id - BACKFILL_LIVE_MARGIN_IDS
    aged_records = [r for r in records if r.glucose_id <= cutoff]
    if not aged_records:
        logger.info(
            "backfill: pulled %d records but all inside the live margin", len(records)
        )
        return 0
    inserted = save_history_records(
        config.db_path,
        aged_records,
        init_time=config.init_time,
        reading_interval_seconds=config.reading_interval_seconds,
    )
    logger.info(
        "backfill: pulled %d records (ids %d-%d), inserted %d",
        len(records),
        records[0].glucose_id,
        records[-1].glucose_id,
        inserted,
    )
    return inserted


async def run_monitor(
    config: RuntimeConfig,
    *,
    once: bool = False,
    scanner: ScannerFunction = scan_once,
    sleep: SleepFunction = asyncio.sleep,
    max_cycles: Optional[int] = None,
) -> int:
    # Validate the database in read-write mode once, but never create it here.
    connection = open_database(config.db_path, read_only=False)
    try:
        schema = validate_schema(connection)
    finally:
        connection.close()

    if schema.is_legacy:
        logger.warning("database uses legacy schema version 0; run db_tool.py check")

    logger.info("monitor started")
    logger.info("database=%s", config.db_path.expanduser().resolve())
    logger.info(
        "scan_timeout=%.1fs scan_interval=%.1fs",
        config.scan_timeout,
        config.scan_interval,
    )

    recoverable_failures = 0
    unexpected_failures = 0
    cycle = 0

    if config.init_time is None:
        logger.info("backfill disabled: OPENANYTIME_INIT_TIME not configured")
        last_backfill = None
    elif once:
        # Single-cycle mode is a scan probe; leave history repair to the
        # long-running service so --once stays fast and side-effect-light.
        last_backfill = None
    else:
        # Startup backfill covers any monitor downtime before the first scan.
        last_backfill = time.monotonic()
        await run_backfill(config)

    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        started = time.monotonic()
        extra_backoff = 0.0

        if (
            last_backfill is not None
            and started - last_backfill >= config.backfill_interval_seconds
        ):
            last_backfill = started
            try:
                await run_backfill(config)
            except (DataConflictError, sqlite3.DatabaseError) as exc:
                # run_backfill already treats pull failures as skip-worthy;
                # storage failures are likewise non-fatal to the broadcast
                # path, so they are logged rather than propagated.
                logger.error("backfill storage failure: %s", exc)

        try:
            outcome = await scanner(config)
            if outcome is None:
                logger.warning("scanner returned no outcome; treating it as no device")
            elif outcome.sample is None:
                if outcome.invalid_packets:
                    logger.warning(
                        "no valid packet; matching=%d invalid=%d",
                        outcome.matching_devices,
                        outcome.invalid_packets,
                    )
                else:
                    logger.info("device not found in this scan window")
            else:
                result = save_sample(
                    config.db_path,
                    outcome.sample,
                    reading_interval_seconds=config.reading_interval_seconds,
                    init_time=config.init_time,
                )
                latest = outcome.sample.packet.records[-1]
                if result.scan_inserted:
                    logger.info(
                        "counter=%d glucose=%.1f mmol/L temperature=%.1f C rssi=%d readings=%d",
                        outcome.sample.packet.counter,
                        latest.glucose_mmol,
                        latest.temperature_c,
                        outcome.sample.rssi,
                        result.readings_inserted,
                    )
                else:
                    logger.info(
                        "counter=%d already stored; skipped duplicate",
                        outcome.sample.packet.counter,
                    )
            recoverable_failures = 0
            unexpected_failures = 0
        except asyncio.CancelledError:
            raise
        except ScanError as exc:
            recoverable_failures += 1
            extra_backoff = min(300.0, 5.0 * (2 ** min(recoverable_failures - 1, 6)))
            logger.error("recoverable scan error: %s; backoff=%.1fs", exc, extra_backoff)
            if once:
                return 1
        except sqlite3.OperationalError as exc:
            recoverable_failures += 1
            extra_backoff = min(300.0, 5.0 * (2 ** min(recoverable_failures - 1, 6)))
            logger.error(
                "recoverable database operation error: %s; backoff=%.1fs",
                exc,
                extra_backoff,
            )
            if once:
                return 1
        except sqlite3.DatabaseError as exc:
            logger.critical("database rejected a write; monitor stopped: %s", exc)
            return 2
        except (DatabaseNotFoundError, SchemaError, DataConflictError) as exc:
            logger.critical("storage safety check failed; monitor stopped: %s", exc)
            return 2
        except Exception:
            unexpected_failures += 1
            logger.exception("unexpected monitor failure")
            if once:
                return 3
            if unexpected_failures >= 3:
                logger.critical("three consecutive unexpected failures; monitor stopped")
                return 3
            extra_backoff = min(60.0, 10.0 * unexpected_failures)

        if once:
            return 0

        elapsed = time.monotonic() - started
        delay = max(0.0, config.scan_interval - elapsed, extra_backoff)
        await sleep(delay)

    return 0
