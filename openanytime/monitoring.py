"""Long-running monitor loop with bounded retries and fail-closed storage."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Awaitable, Callable, Optional

from .config import RuntimeConfig
from .scanner import ScanError, ScanOutcome, scan_once
from .storage import (
    DataConflictError,
    DatabaseNotFoundError,
    SchemaError,
    open_database,
    save_sample,
    validate_schema,
)

logger = logging.getLogger(__name__)

ScannerFunction = Callable[[RuntimeConfig], Awaitable[ScanOutcome]]
SleepFunction = Callable[[float], Awaitable[None]]


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

    while max_cycles is None or cycle < max_cycles:
        cycle += 1
        started = time.monotonic()
        extra_backoff = 0.0

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
