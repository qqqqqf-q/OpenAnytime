#!/usr/bin/env python3
"""CLI for the GATT history-pull channel; logic lives in openanytime.history.

Kept as the manual probe/backfill entry point. The long-running monitor
(monitor.py) performs the same pull automatically; use this when you want a
one-off dump, e.g.:

    uv run python gatt_pull.py --start-id 0 --out output/history.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict
from typing import Optional, Sequence

from openanytime.config import ConfigurationError, load_runtime_config
from openanytime.history import HistoryPullError, pull_history


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pull 5 HSE on-device history")
    parser.add_argument("--key", type=int, help="sensor-specific sureClose value")
    parser.add_argument("--device", help="case-insensitive device name fragment")
    parser.add_argument("--timezone", help="IANA timezone")
    parser.add_argument("--scan-timeout", type=float, help="BLE scan window in seconds")
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument(
        "--random-b",
        nargs=4,
        type=int,
        metavar=("B0", "B1", "B2", "B3"),
        help="bound account randomB; defaults to all zeros (works when unbound)",
    )
    parser.add_argument("--out", help="write pulled records as JSONL to this path")
    return parser


async def run(args: argparse.Namespace) -> int:
    config = load_runtime_config(
        key=args.key,
        device_name_fragment=args.device,
        timezone_name=args.timezone,
        scan_timeout=args.scan_timeout,
    )
    records = await pull_history(
        key=config.key,
        device_name_fragment=config.device_name_fragment,
        start_id=args.start_id,
        timezone=config.timezone,
        random_b=args.random_b,
        scan_timeout=config.scan_timeout,
    )
    if not records:
        print("设备没有更多历史记录。")
        return 0

    for record in records:
        print(
            f"  id={record.glucose_id} Iw={record.iw:.2f} Ib={record.ib:.2f} "
            f"{record.temperature_c:.1f}C coarse={record.glucose_coarse:.1f} "
            f"trend={record.trend} err={record.error_code}"
        )
    print(f"共拉取 {len(records)} 条 (id {records[0].glucose_id}-{records[-1].glucose_id})")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(asdict(record)) + "\n")
        print(f"已写入 {args.out}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except ConfigurationError as exc:
        parser.error(str(exc))
    except HistoryPullError as exc:
        logging.error("拉取失败:%s", exc)
        return 1
    except KeyboardInterrupt:
        print("已取消")
        return 0


if __name__ == "__main__":
    sys.exit(main())
