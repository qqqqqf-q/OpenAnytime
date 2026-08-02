#!/usr/bin/env python3
"""BLE 采集看门狗:monitor 扫描栈停滞时自动重启。

故障形态(2026-08-01 两次、08-02 一次实测):monitor 进程的 CoreBluetooth
扫描会话会停滞——日志数小时无行或扫描窗口全空,但设备其实在线
(新进程一扫就能看到,GATT 也能连)。只有重启进程能恢复。

判据(刻意保守,避免重启风暴):
1. scans 表最新广播捕获超过 STALE_AFTER 没更新;
2. 看门狗自己扫 PROBE_SECONDS 秒,确实看到设备在广播;
两条同时成立才 kickstart monitor。设备真离境时扫描栈同样无捕获,
但探针也看不到设备,不会误重启。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bleak import BleakScanner

from openanytime.config import default_db_path

logger = logging.getLogger("ble_watchdog")

STALE_AFTER = timedelta(minutes=10)
PROBE_SECONDS = 40
MONITOR_LABEL = "com.qqqqqf.cgm-monitor"


def last_scan_age(db_path: Path) -> timedelta | None:
    """最新广播捕获距现在多久;库里没有扫描行时返回 None。"""
    now = datetime.now().astimezone()
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT MAX(timestamp) FROM scans").fetchone()
    if not row or not row[0]:
        return None
    return now - datetime.fromisoformat(row[0])


async def probe(device_name_fragment: str) -> bool:
    """独立扫一次,返回是否看到目标设备。"""
    seen = False

    def callback(device, _advertisement) -> None:
        nonlocal seen
        if device_name_fragment.lower() in (device.name or "").lower():
            seen = True

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(PROBE_SECONDS)
    await scanner.stop()
    return seen


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db_path = Path(
        os.environ.get("OPENANYTIME_DB", str(default_db_path()))
    ).expanduser()
    fragment = os.environ.get("OPENANYTIME_DEVICE_NAME", "anytime")

    age = last_scan_age(db_path)
    if age is not None and age < STALE_AFTER:
        return 0  # 广播采集新鲜,一切正常

    logger.warning(
        "broadcast capture stale (%s); probing for device",
        "no scans yet" if age is None else f"{age}",
    )
    if not asyncio.run(probe(fragment)):
        logger.info("device not advertising; leaving monitor alone")
        return 0

    logger.warning("device IS advertising but monitor is stale; restarting monitor")
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{MONITOR_LABEL}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("kickstart failed: %s", result.stderr.strip())
        return 1
    logger.info("monitor restarted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
