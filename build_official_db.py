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

# 广播 counter → glucoseId 的每 flag 偏移,经环境变量 OPENANYTIME_FLAG_OFFSETS
# 配置(格式 "1:5371,2:5115")。这是每个传感器会话的私有常量,必须用
# 取值锚定法实测(见 docs/history-backfill.md §4);不设置则广播行无法
# 映射回 gid,官方库只含历史行(滞后到最近一次 backfill,但不出错值)。
# 锚点校验经 OPENANYTIME_ANCHOR 配置(格式 "233:116",官方 App 真值
# id:mg/dL);不设置则跳过校验(仅告警),设置后校验失败拒绝替换。
FLAG_OFFSETS_ENV = "OPENANYTIME_FLAG_OFFSETS"
ANCHOR_ENV = "OPENANYTIME_ANCHOR"


def _parse_flag_offsets(raw: str | None) -> dict[int, int]:
    if not raw:
        return {}
    offsets: dict[int, int] = {}
    for part in raw.split(","):
        flag, _, offset = part.strip().partition(":")
        offsets[int(flag)] = int(offset)
    return offsets


def _offset_for(flag: int, offsets: dict[int, int]) -> int | None:
    """flag 的 counter→glucoseId 偏移;未配置时按 256 递减公式推导。

    实测规律(flag 1-7 逐次取值锚定验证):offset(flag) = offset(1) − 256×(flag−1)。
    设备切换 flag 越来越频繁(曾一天两次),等人工补配置意味着官方库
    在切换后停更数小时;公式已五次应验,自动推导 + 响亮告警是更优解。
    推导基于 flag 1 的配置值,故 flag 1 必须显式配置;锚点校验(若配置)
    与存储层取值锚定投票是独立防线。
    """
    if flag in offsets:
        return offsets[flag]
    base = offsets.get(1)
    if base is None:
        return None
    derived = base - 256 * (flag - 1)
    if derived <= 0:
        return None
    return derived


def _parse_anchor(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    gid, _, mg = raw.strip().partition(":")
    return int(gid), int(mg)


def _row_to_gid(reading_index: int, offsets: dict[int, int]) -> tuple[int | None, bool]:
    """返回 (gid, 偏移是否为公式推导)。flag 未配置且无法推导时为 (None, _)。"""
    if reading_index >= 100_000:
        flag = reading_index // 100_000
        offset = _offset_for(flag, offsets)
        if offset is None:
            return None, False
        return reading_index % 100_000 - offset, flag not in offsets
    offset = offsets.get(1)
    if offset is None:
        return None, False
    return reading_index - offset, False


def _load_readings(source: Path, offsets: dict[int, int]) -> dict[int, sqlite3.Row]:
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        by_gid: dict[int, sqlite3.Row] = {}
        skipped_flags: dict[int, int] = {}
        derived_flags: set[int] = set()
        for row in connection.execute(
            "SELECT counter, reading_index, glucose_mmol, temperature_c, rssi "
            "FROM readings"
        ):
            if row["counter"] == -1:
                gid = row["reading_index"]
            else:
                flag = (
                    row["reading_index"] // 100_000
                    if row["reading_index"] >= 100_000
                    else 1
                )
                mapped, derived = _row_to_gid(row["reading_index"], offsets)
                if mapped is None:
                    skipped_flags[flag] = skipped_flags.get(flag, 0) + 1
                    continue
                gid = mapped
                if derived:
                    derived_flags.add(flag)
            # 历史行优先(真网格 Iw);广播行仅补缺
            if gid not in by_gid or row["counter"] == -1:
                by_gid[gid] = row
        if derived_flags:
            logger.warning(
                "flags %s not in %s; using formula-derived offsets "
                "(offset(1)-256*(flag-1), verified flags 1-7) — verify by "
                "value anchoring and pin them in the config",
                sorted(derived_flags),
                FLAG_OFFSETS_ENV,
            )
        if skipped_flags:
            logger.warning(
                "skipped %d broadcast rows with unmappable flags %s "
                "(official db lags live edge meanwhile)",
                sum(skipped_flags.values()),
                sorted(skipped_flags),
            )
        return by_gid
    finally:
        connection.close()


def rebuild(
    source: Path,
    target: Path,
    init_time: datetime,
    *,
    offsets: dict[int, int],
    anchor: tuple[int, int] | None,
) -> int:
    by_gid = _load_readings(source, offsets)
    if not by_gid:
        raise RuntimeError(f"no readings in {source}")
    max_gid = max(by_gid)

    readings = []
    last_row = None
    for gid in range(max_gid + 1):
        row = by_gid.get(gid)
        if row is not None:
            last_row = row
        elif last_row is None:
            # 首个已知 id 之前没有可填充的值。算法从第一个被喂的 id 起
            # 建立内部计数,实践中 id 0 永远来自 backfill,不会走到这里。
            continue
        else:
            # 缺洞必须用最后已知值前向填充后照样喂——算法的内部调用计数
            # 与 glucoseId 强绑定,跳 id 不喂会立即进入 err=2(输出恒 0)
            # 且永不自愈(2026-08-01 事故:连续 3 个缺洞导致官方库
            # id 1005 起全线 0.0)。重复值本身算法完全耐受。
            row = last_row
        readings.append(
            SensorReading(gid, row["glucose_mmol"], 0.0, row["temperature_c"])
        )

    glu_mg = compute_official_glucose(readings)

    if anchor is not None:
        anchor_id, anchor_mg = anchor
        # ±2 mg 容差覆盖输入电流广播/历史通道 ±0.05 的量化差
        actual = glu_mg.get(anchor_id)
        if actual is None or abs(actual - anchor_mg) > 2:
            raise RuntimeError(
                f"anchor check failed: id {anchor_id} = {actual} mg/dL, "
                f"expected {anchor_mg}±2"
            )
    else:
        logger.warning("no %s configured; anchor check skipped", ANCHOR_ENV)

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
        inserted = rebuild(
            source,
            target,
            init_time,
            offsets=_parse_flag_offsets(os.environ.get(FLAG_OFFSETS_ENV)),
            anchor=_parse_anchor(os.environ.get(ANCHOR_ENV)),
        )
    except Exception as exc:
        logger.error("rebuild failed, previous official db kept: %s", exc)
        return 1
    logger.info("rebuilt %d rows -> %s", inserted, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
