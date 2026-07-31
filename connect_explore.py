#!/usr/bin/env python3
"""Read-only GATT exploration for a nearby 5 HSE device."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional, Sequence

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

DEFAULT_CHARACTERISTICS = {
    "C1": "8082caa8-41a6-4021-91c6-56f9b954cc34",
    "C2": "724249f0-5ec3-4b5f-8804-42345af08651",
    "state": "64b4e8b5-0de5-401b-a21d-acc8db3b913a",
    "battery": "42c3dfdd-77be-4d9c-8454-8f875267fb3b",
    "counter": "b7de1eea-823d-43bb-a3af-c4903dfce23c",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explore read-only GATT services")
    parser.add_argument(
        "--device",
        default=os.environ.get("OPENANYTIME_DEVICE_NAME", "anytime"),
        help="case-insensitive device name fragment",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    return parser


async def run(args: argparse.Namespace) -> int:
    target = args.device.casefold()
    print(f"监听中（最长 {args.timeout:g} 秒）...")

    try:
        device = await BleakScanner.find_device_by_filter(
            lambda candidate, advertisement: target
            in (advertisement.local_name or candidate.name or "").casefold(),
            timeout=args.timeout,
        )
    except (BleakError, OSError, asyncio.TimeoutError) as exc:
        print(f"BLE 扫描失败：{exc}", file=sys.stderr)
        return 1

    if device is None:
        print("本次扫描未发现设备；未启动连接任务。")
        return 0

    try:
        async with BleakClient(device, timeout=args.connect_timeout) as client:
            print(f"已连接，MTU={client.mtu_size}")
            for service in client.services:
                short_service = service.uuid.split("-")[0][-8:]
                print(f"\n[{short_service}] {service.uuid}")
                for characteristic in service.characteristics:
                    short = characteristic.uuid.split("-")[0][-8:]
                    properties = ",".join(characteristic.properties)
                    value = ""
                    if "read" in characteristic.properties:
                        try:
                            raw = await client.read_gatt_char(characteristic.uuid)
                            value = f" = {raw.hex()} ({len(raw)}b)"
                        except (BleakError, OSError, asyncio.TimeoutError) as exc:
                            value = f" = read-error:{exc}"
                    print(f"  {short} [{properties}]{value}")

            print("\n=== 关键值 ===")
            for name, uuid in DEFAULT_CHARACTERISTICS.items():
                try:
                    raw = await client.read_gatt_char(uuid)
                    print(f"{name}: {raw.hex()} ({int.from_bytes(raw, 'little')} LE)")
                except (BleakError, OSError, asyncio.TimeoutError) as exc:
                    print(f"{name}: read-error:{exc}")
    except (BleakError, OSError, asyncio.TimeoutError) as exc:
        print(f"GATT 连接失败：{exc}", file=sys.stderr)
        return 1

    print("\n已断开")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("操作已取消")
        return 0


if __name__ == "__main__":
    sys.exit(main())
