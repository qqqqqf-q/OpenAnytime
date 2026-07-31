#!/usr/bin/env python3
"""
鱼跃 5 HSE（安耐糖 / Anytime CGM）持续监控 — SQLite 存储
用法: /tmp/bleak-venv/bin/python3 monitor.py
"""
import asyncio, struct, json, sqlite3, os, time
from datetime import datetime, timezone, timedelta
from bleak import BleakScanner

DIR = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=8))  # UTC+8
DB = os.path.join(DIR, "cgm.db")
TARGET = "anytime"
KEY = 121  # sureClose
INTERVAL_READING = 180  # 每条记录 ~3 分钟
INTERVAL_SCAN = 120  # 每 2 分钟扫一次


def init_db():
    conn = sqlite3.connect(DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            counter INTEGER,
            reading_index INTEGER UNIQUE,
            glucose_mmol REAL,
            glucose_mg INTEGER,
            temperature_c REAL,
            rssi INTEGER,
            raw_hex TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT,
            address TEXT,
            rssi INTEGER,
            counter INTEGER UNIQUE,
            raw_hex TEXT,
            record_count INTEGER
        )
    """)
    conn.commit()
    return conn


def encode(bArr: bytes, key: int) -> bytes:
    bits = ""
    for b in bArr:
        bits += format((b & 0xFF) ^ key, "08b")
    bits_list = list(bits)
    for i in range(len(bits_list) - 1):
        if bits_list[i + 1] == "0":
            bits_list[i] = "1" if bits_list[i] == "0" else "0"
    result = bytearray()
    for i in range(0, len(bits_list), 8):
        result.append(int("".join(bits_list[i : i + 8]), 2))
    return bytes(result)


async def scan_once() -> dict | None:
    devices = await BleakScanner.discover(timeout=INTERVAL_SCAN, return_adv=True)
    for addr, (device, adv) in devices.items():
        if not device.name or TARGET not in device.name.lower():
            continue
        mfr = adv.manufacturer_data
        if 0x4743 not in mfr:
            continue
        raw = mfr[0x4743]
        if len(raw) < 23:
            continue

        ctr = struct.unpack(">H", raw[2:4])[0]
        encrypted = raw[5 : 5 + 18]
        decrypted = encode(encrypted, KEY)

        records = []
        for ri in range(0, len(decrypted) - 2, 3):
            record = int.from_bytes(decrypted[ri : ri + 3], "big")
            g = round((record >> 10) * 0.01, 1)
            t = round((record & 1023) * 0.1 - 40.0, 1)
            records.append({"glucose": g, "glucose_mg": round(g * 18), "temp": t})

        return {
            "ts": datetime.now(TZ),
            "name": device.name,
            "address": addr,
            "rssi": adv.rssi,
            "counter": ctr,
            "records": records,
            "raw": raw.hex(),
        }
    return None


def save_scan(conn, data: dict):
    now = data["ts"].astimezone(TZ).isoformat()
    conn.execute(
        "INSERT INTO scans (timestamp, name, address, rssi, counter, raw_hex, record_count) VALUES (?,?,?,?,?,?,?)",
        (now, data["name"], data["address"], data["rssi"], data["counter"], data["raw"], len(data["records"])),
    )

    for i, r in enumerate(data["records"]):
        reading_ts = datetime.fromtimestamp(
            data["ts"].timestamp() - (len(data["records"]) - 1 - i) * INTERVAL_READING, tz=TZ
        ).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO readings (timestamp, counter, reading_index, glucose_mmol, glucose_mg, temperature_c, rssi, raw_hex) VALUES (?,?,?,?,?,?,?,?)",
            (reading_ts, data["counter"], data["counter"] - (len(data["records"]) - 1 - i), r["glucose"], r["glucose_mg"], r["temp"], data["rssi"], data["raw"]),
        )
    conn.commit()


async def main():
    conn = init_db()
    print(f"[{datetime.now(TZ):%H:%M:%S}] CGM 持续监控启动")
    print(f"数据库: {DB}")
    print(f"扫描间隔: {INTERVAL_SCAN}s | 读取间隔: {INTERVAL_READING}s")
    print("Ctrl+C 停止\n")

    try:
        while True:
            data = await scan_once()
            if data:
                save_scan(conn, data)
                r = data["records"][-1]
                print(f"[{data['ts']:%H:%M:%S}] ctr={data['counter']} | "
                      f"{r['glucose']:.1f} mmol/L ({r['glucose_mg']} mg/dL) | "
                      f"{r['temp']:.1f}°C | RSSI={data['rssi']}")
            else:
                print(f"[{datetime.now(TZ):%H:%M:%S}] 未扫到设备")
            now = datetime.now(TZ)
            last_ts = data["ts"] if data else now
            await asyncio.sleep(max(0, INTERVAL_SCAN - (now - last_ts).total_seconds()))
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(main())
