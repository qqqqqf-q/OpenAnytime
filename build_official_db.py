#!/usr/bin/env python3
"""重建「官方口径」派生数据库(cgm-official.db)并原子替换。

每个 glucoseId 一行:网格时间戳、官方 native 算法血糖(round(GLU_MG/18, 1))。
输入序列以历史行(counter=-1)为准,缺口用广播行(按 flag 偏移映射回 gid)
填补;两通道都没有的 id 用邻近值喂算法保持状态连续,但不产出数据点。

安全性质:
- 源库 cgm.db 只读;目标库先写到临时文件再 os.replace 原子替换,
  读方(server.py)永远看到完整文件;
- 每次重建校验锚点(id 233 必须 ≈116 mg/dL,官方真值 6.4 mmol/L),
  失败拒绝替换——防 .so 损坏、模拟器回归或 flag 偏移漂移;
- 幂等:任何时刻重跑都产出同一内容(输入库不变时)。

由 launchd(com.qqqqqf.cgm-official)每 180s 触发;失败只记日志,
下一轮自愈。算法背景见 openanytime/native_emulator.py 模块 docstring。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openanytime.config import default_db_path
from openanytime.native_emulator import SensorReading, compute_official_glucose
from openanytime.storage import initialize_database

logger = logging.getLogger("build_official_db")

# 广播 counter → glucoseId 的每 flag 偏移(2026-07-31 取值锚定法测定,
# 详见 docs/history-backfill.md §4)。换传感器/换会话后必须重新测定,
# 锚点校验会在偏移漂移时拦截。
FLAG_OFFSET = {1: 5371, 2: 5115}

# 锚点:凌晨官方 App 真值 id 233 → 6.4 mmol/L(116 mg/dL)。±2 mg 容差
# 覆盖输入电流广播/历史通道 ±0.05 的量化差。
ANCHOR_ID, ANCHOR_MG, ANCHOR_TOLERANCE = 233, 116, 2


def _row_to_gid(reading_index: int) -> int:
    if reading_index >= 100_000:
        flag = reading_index // 100_000
        return reading_index % 100_000 - FLAG_OFFSET[flag]
    return reading_index - FLAG_OFFSET[1]


def _load_readings(source: Path) -> dict[int, sqlite3.Row]:
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        by_gid: dict[int, sqlite3.Row] = {}
        for row in connection.execute(
            "SELECT counter, reading_index, glucose_mmol, temperature_c, rssi "
            "FROM readings"
        ):
            gid = (
                row["reading_index"]
                if row["counter"] == -1
                else _row_to_gid(row["reading_index"])
            )
            # 历史行优先(真网格 Iw);广播行仅补缺
            if gid not in by_gid or row["counter"] == -1:
                by_gid[gid] = row
        return by_gid
    finally:
        connection.close()


def rebuild(source: Path, target: Path, init_time: datetime) -> int:
    by_gid = _load_readings(source)
    if not by_gid:
        raise RuntimeError(f"no readings in {source}")
    max_gid = max(by_gid)

    readings = []
    for gid in range(max_gid + 1):
        row = by_gid.get(gid)
        if row is None:
            # 缺洞:用前一点的输入保持算法状态连续,但不产出数据点
            prev = by_gid[gid - 1] if gid > 0 and gid - 1 in by_gid else None
            if prev is None:
                continue
            readings.append(
                SensorReading(gid, prev["glucose_mmol"], 0.0, prev["temperature_c"])
            )
        else:
            readings.append(
                SensorReading(gid, row["glucose_mmol"], 0.0, row["temperature_c"])
            )

    glu_mg = compute_official_glucose(readings)

    anchor = glu_mg.get(ANCHOR_ID)
    if anchor is None or abs(anchor - ANCHOR_MG) > ANCHOR_TOLERANCE:
        raise RuntimeError(
            f"anchor check failed: id {ANCHOR_ID} = {anchor} mg/dL, "
            f"expected {ANCHOR_MG}±{ANCHOR_TOLERANCE}"
        )

    temp_target = target.with_suffix(".building")
    temp_target.unlink(missing_ok=True)
    initialize_database(temp_target)
    connection = sqlite3.connect(temp_target)
    try:
        inserted = 0
        for gid in range(max_gid + 1):
            row = by_gid.get(gid)
            if row is None:
                continue
            timestamp = (init_time + (gid + 1) * timedelta(minutes=3)).isoformat()
            connection.execute(
                "INSERT INTO readings (timestamp, counter, reading_index,"
                " glucose_mmol, glucose_mg, temperature_c, rssi, raw_hex)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    timestamp,
                    -1,
                    gid,
                    round(glu_mg[gid] / 18.0, 1),
                    glu_mg[gid],
                    row["temperature_c"],
                    row["rssi"],
                    "",
                ),
            )
            inserted += 1
        connection.executescript(
            f"ATTACH '{source}' AS src;"
            "INSERT INTO scans SELECT * FROM src.scans;"
            "DETACH src;"
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(temp_target, target)
    return inserted


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    source = Path(
        os.environ.get("OPENANYTIME_DB", str(default_db_path()))
    ).expanduser()
    target = Path(
        os.environ.get(
            "OPENANYTIME_OFFICIAL_DB",
            str(source.with_name("cgm-official.db")),
        )
    ).expanduser()
    init_time_raw = os.environ.get("OPENANYTIME_INIT_TIME")
    if not init_time_raw:
        logger.error("OPENANYTIME_INIT_TIME is required")
        return 2
    init_time = datetime.fromisoformat(init_time_raw)
    if init_time.tzinfo is None:
        init_time = init_time.replace(tzinfo=timezone(timedelta(hours=8)))

    try:
        inserted = rebuild(source, target, init_time)
    except Exception as exc:
        logger.error("rebuild failed, previous official db kept: %s", exc)
        return 1
    logger.info("rebuilt %d rows -> %s", inserted, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
