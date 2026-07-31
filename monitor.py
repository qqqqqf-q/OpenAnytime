#!/usr/bin/env python3
"""Run the resilient OpenAnytime BLE monitor."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Optional, Sequence

from openanytime.config import ConfigurationError, load_runtime_config
from openanytime.monitoring import run_monitor
from openanytime.storage import StorageError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor 5 HSE BLE broadcasts")
    parser.add_argument("--db", help="existing SQLite database path")
    parser.add_argument("--key", type=int, help="sensor-specific sureClose value")
    parser.add_argument("--device", help="case-insensitive device name fragment")
    parser.add_argument("--timezone", help="IANA timezone, for example Asia/Shanghai")
    parser.add_argument("--scan-timeout", type=float, help="BLE scan window in seconds")
    parser.add_argument("--scan-interval", type=float, help="scan start interval in seconds")
    parser.add_argument("--once", action="store_true", help="run exactly one scan cycle")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_runtime_config(
            db_path=args.db,
            key=args.key,
            device_name_fragment=args.device,
            timezone_name=args.timezone,
            scan_timeout=args.scan_timeout,
            scan_interval=args.scan_interval,
        )
        return asyncio.run(run_monitor(config, once=args.once))
    except ConfigurationError as exc:
        parser.error(str(exc))
    except StorageError as exc:
        logging.critical("monitor refused to start: %s", exc)
        return 2
    except KeyboardInterrupt:
        logging.info("monitor stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
