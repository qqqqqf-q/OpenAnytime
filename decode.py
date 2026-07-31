#!/usr/bin/env python3
"""
鱼跃安耐糖 CT5 (Anytime CGM) 广播包解密工具
============================================
通过 BLE 广告包实时读取血糖和温度数据，无需 GATT 连接。

用法: /tmp/bleak-venv/bin/python3 decode.py

硬件: Renesas DA14535 BLE SoC
固件: V1130_20250618
算法: ConvertTools.encode (XOR + bit manipulation)
密钥: sureClose 字段, 默认 121
"""

import asyncio
import struct
import json
import os
from datetime import datetime, timezone, timedelta
from bleak import BleakScanner

OUT = os.path.dirname(os.path.abspath(__file__))
TZ = timezone(timedelta(hours=8))  # UTC+8
TARGET = "anytime"
KEY = 121  # sureClose, 从传感器元数据获叭


def encode(bArr: bytes, key: int) -> bytes:
    """ConvertTools.encode — 解密广播包 18 字节 payload"""
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


def parse_records(decrypted: bytes, base_index: int = 0):
    """解析 3 字节记录 -> 血糖(mmol/L) + 温度(°C)"""
    records = []
    for ri in range(0, len(decrypted) - 2, 3):
        record = int.from_bytes(decrypted[ri : ri + 3], "big")
        glucose_raw = record >> 10  # 高 14 位
        temp_raw = record & 1023  # 低 10 位
        glucose = round(glucose_raw * 0.01, 1)
        temperature = round(temp_raw * 0.1 - 40.0, 1)
        records.append(
            {
                "index": base_index + ri // 3,
                "glucose_mmol": glucose,
                "glucose_mg": round(glucose * 18.0),
                "temperature_c": temperature,
            }
        )
    return records


async def scan_and_decode(timeout: float = 90.0):
    """扫描 CGM 广播包并解密"""
    print(f"[{datetime.now():%H:%M:%S}] 扫描中 (max {timeout}s)...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

    results = []
    for addr, (device, adv) in devices.items():
        if not device.name or TARGET not in device.name.lower():
            continue

        mfr = adv.manufacturer_data
        if 0x4743 not in mfr:
            continue

        raw = mfr[0x4743]
        if len(raw) < 23:
            continue

        # 解析包头
        hdr = raw[0:2]
        ctr_be = struct.unpack(">H", raw[2:4])[0]
        flag = raw[4]
        encrypted = raw[5 : 5 + 18]

        # 解密
        decrypted = encode(encrypted, KEY)
        records = parse_records(decrypted)

        ts = datetime.now(TZ).isoformat()
        result = {
            "timestamp": ts,
            "name": device.name,
            "address": addr,
            "rssi": adv.rssi,
            "counter": ctr_be,
            "records": records,
            "raw_hex": raw.hex(),
        }

        print(f"[{datetime.now():%H:%M:%S}] {device.name} RSSI={adv.rssi}dBm ctr={ctr_be}")
        for r in records:
            print(f"  #{r['index']:04d}: {r['glucose_mmol']:.1f} mmol/L ({r['glucose_mg']} mg/dL)  {r['temperature_c']:.1f}°C")
        print()

        results.append(result)

        # 落盘
        path = os.path.join(OUT, f"cgm_{ts[:10]}.jsonl")
        with open(path, "a") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

        # 最新数据摘要
        summary = os.path.join(OUT, "latest.txt")
        with open(summary, "w") as f:
            f.write(f"# Anytime CGM 最新数据\n")
            f.write(f"采集时间: {ts}\n")
            f.write(f"设备: {device.name}\n")
            f.write(f"信号: {adv.rssi} dBm\n\n")
            f.write(f"{'Index':>6} {'血糖 mmol/L':>12} {'mg/dL':>8} {'温度 °C':>10}\n")
            f.write("-" * 40 + "\n")
            for r in records:
                f.write(f"{r['index']:>6} {r['glucose_mmol']:>12.1f} {r['glucose_mg']:>8} {r['temperature_c']:>10.1f}\n")

    if not results:
        print("未找到 CGM 设备")

    return results


if __name__ == "__main__":
    asyncio.run(scan_and_decode(timeout=120.0))
