#!/usr/bin/env python3
"""Channel probe: find which GATT characteristic the 5 HSE actually answers on.

The reference app only knows service 0x1000 (write 1002 / notify 1001), but a
plain setDate+pull there got complete silence. This probe exercises every
candidate channel in one session and tags each notify with its source:

- FEF5 command register (724249f0) with the known-good 0x01 poke, which in a
  previous exploration session always elicited 0x08 on 5f78df94 — establishes
  whether the device talks at all right now.
- checkId with a zero randomB on 1002 — a failure reply still proves the
  channel is alive; silence means auth-gated or dead.
- pull series on both write candidates (1002 and FEF5 457871e8).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import Dict, List, Tuple

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

WRITE_1002 = "00001002-1212-efde-1523-785feabcd123"
NOTIFY_1001 = "00001001-1212-efde-1523-785feabcd123"
FEF5_COMMAND = "724249f0-5ec3-4b5f-8804-42345af08651"
FEF5_WRITE = "457871e8-2f7b-4f31-9a26-e0f1d0b1a9a1"  # placeholder, resolved below
FEF5_NOTIFY = "5f78df94-0000-0000-0000-000000000000"  # resolved at runtime

# Full UUIDs observed in the earlier exploration session (TIMELINE.md table).
FEF5_WRITE_FULL = "457871e8-2f7b-4f31-9a26-e0f1d0b1a9a1"
FEF5_NOTIFY_FULL = "5f78df94-0000-4000-8000-000000000000"


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def frame(opcode: int, payload: bytes = b"") -> bytes:
    body = bytes([opcode]) + payload
    return body + bytes([checksum(body)])


async def main() -> int:
    print("扫描中(60 秒)...")
    device = await BleakScanner.find_device_by_filter(
        lambda candidate, advertisement: "anytime"
        in (advertisement.local_name or candidate.name or "").casefold(),
        timeout=60.0,
    )
    if device is None:
        print("未发现设备")
        return 1

    events: List[Tuple[str, bytes]] = []

    def make_handler(tag: str):
        def handler(_: int, data: bytearray) -> None:
            frame_bytes = bytes(data)
            events.append((tag, frame_bytes))
            print(f"  << notify[{tag}] {frame_bytes.hex()} ({len(frame_bytes)}B)")

        return handler

    try:
        async with BleakClient(device, timeout=20.0) as client:
            print(f"已连接,MTU={client.mtu_size}")

            # Resolve real UUIDs from the live service table rather than
            # trusting the remembered constants.
            fef5_write = fef5_notify = None
            fef5_command = FEF5_COMMAND
            for service in client.services:
                for char in service.characteristics:
                    short = char.uuid.split("-")[0]
                    if short == "457871e8":
                        fef5_write = char.uuid
                    elif short == "5f78df94":
                        fef5_notify = char.uuid
                    elif short == "724249f0":
                        fef5_command = char.uuid
                    else:
                        continue
            print(f"FEF5 write={fef5_write} notify={fef5_notify}")

            subscribed = []
            for uuid, tag in ((NOTIFY_1001, "1001"), (fef5_notify, "FEF5")):
                if uuid is None:
                    continue
                try:
                    await client.start_notify(uuid, make_handler(tag))
                    subscribed.append(tag)
                except BleakError as exc:
                    print(f"  订阅 {tag} 失败:{exc}")
            print(f"已订阅:{subscribed}")

            async def step(label: str, uuid: str, data: bytes, wait: float) -> None:
                print(f">> {label}: {data.hex()} -> {uuid.split('-')[0]}")
                try:
                    await client.write_gatt_char(uuid, data, response=False)
                except BleakError as exc:
                    print(f"   写入失败:{exc}")
                await asyncio.sleep(wait)

            await step("FEF5 已知探针(01)", fef5_command, b"\x01", 3.0)
            await step("checkId 零 randomB", WRITE_1002, frame(0x31, b"\x00\x00\x00\x00"), 3.0)
            await step(
                "setDate(本地时间)",
                WRITE_1002,
                frame(
                    0x03,
                    bytes(
                        [
                            datetime.now().year - 1900,
                            datetime.now().month,
                            datetime.now().day,
                            datetime.now().hour,
                            datetime.now().minute,
                            datetime.now().second,
                        ]
                    ),
                ),
                3.0,
            )
            await step("pull start=0 count=45 (1002)", WRITE_1002, frame(0x37, b"\x00\x00\x2d"), 8.0)
            if fef5_write:
                await step("pull start=0 count=45 (FEF5)", fef5_write, frame(0x37, b"\x00\x00\x2d"), 8.0)
            await step("单条 pull 0x55 id=1", WRITE_1002, frame(0x55, b"\x00\x01"), 5.0)
    except (BleakError, OSError, asyncio.TimeoutError) as exc:
        print(f"会话失败:{exc}", file=sys.stderr)
        return 1

    print(f"\n共收到 {len(events)} 条 notify")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("已取消")
        sys.exit(0)
