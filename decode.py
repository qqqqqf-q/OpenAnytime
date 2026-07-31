#!/usr/bin/env python3
"""Scan once and print a validated 5 HSE manufacturer packet."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Optional, Sequence

from openanytime.config import ConfigurationError, load_runtime_config
from openanytime.scanner import ScanError, scan_once


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decode one 5 HSE BLE broadcast")
    parser.add_argument("--key", type=int, help="sensor-specific sureClose value")
    parser.add_argument("--device", help="case-insensitive device name fragment")
    parser.add_argument("--timezone", help="IANA timezone")
    parser.add_argument("--timeout", type=float, help="BLE scan window in seconds")
    parser.add_argument("--json", action="store_true", help="print structured JSON")
    return parser


async def run(args: argparse.Namespace) -> int:
    config = load_runtime_config(
        key=args.key,
        device_name_fragment=args.device,
        timezone_name=args.timezone,
        scan_timeout=args.timeout,
    )
    outcome = await scan_once(config)
    if outcome.sample is None:
        if outcome.invalid_packets:
            print(
                f"未找到有效数据包（匹配设备 {outcome.matching_devices}，"
                f"无效包 {outcome.invalid_packets}）"
            )
        else:
            print("本次扫描未发现设备；这不是致命错误，可稍后重试。")
        return 0

    sample = outcome.sample
    payload = {
        "timestamp": sample.captured_at.isoformat(),
        "name": sample.name,
        "address": sample.address,
        "rssi": sample.rssi,
        "counter": sample.packet.counter,
        "checksum": sample.packet.checksum,
        "records": [
            {
                "offset": record.offset,
                "glucose_mmol": record.glucose_mmol,
                "glucose_mg": record.glucose_mg,
                "temperature_c": record.temperature_c,
            }
            for record in sample.packet.records
        ],
        "raw_hex": sample.packet.raw_hex,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        f"[{sample.captured_at:%H:%M:%S}] {sample.name} "
        f"RSSI={sample.rssi}dBm counter={sample.packet.counter}"
    )
    for record in sample.packet.records:
        print(
            f"  -{len(sample.packet.records) - 1 - record.offset}: "
            f"{record.glucose_mmol:.1f} mmol/L "
            f"({record.glucose_mg} mg/dL), {record.temperature_c:.1f} C"
        )
    print("校验字节已保留，但算法尚未从现有证据中确认。")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except ConfigurationError as exc:
        parser.error(str(exc))
    except ScanError as exc:
        logging.error("扫描失败：%s", exc)
        return 1
    except KeyboardInterrupt:
        print("扫描已取消")
        return 0


if __name__ == "__main__":
    sys.exit(main())
