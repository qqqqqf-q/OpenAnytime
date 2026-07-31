#!/usr/bin/env python3
"""
GATT 连接探索脚本 — 直接连接 CGM 读 GATT services
注意: 传感器使用定向广播, 连接成功率低, 需要多次尝试
"""
import asyncio
from bleak import BleakScanner, BleakClient

TARGET = "anytime"

async def main():
    done = asyncio.Event()
    client_ref = {}

    async def connect(d):
        try:
            c = BleakClient(d.address, timeout=10.0)
            await c.connect()
            client_ref["c"] = c
            done.set()
        except:
            pass

    def on_dev(d, adv):
        if done.is_set():
            return
        if d.name and TARGET in d.name.lower():
            asyncio.ensure_future(connect(d))

    print("监听中 (5分钟)...")
    async with BleakScanner(on_dev):
        try:
            await asyncio.wait_for(done.wait(), timeout=300)
        except asyncio.TimeoutError:
            print("超时, 没连上")
            return

    c = client_ref["c"]
    print(f"已连接! MTU={c.mtu_size}")

    # 读所有 GATT characteristics
    for svc in c.services:
        svc_id = svc.uuid.split("-")[0][-8:]
        print(f"\n[{svc_id}] {svc.uuid}")
        for char in svc.characteristics:
            short = char.uuid.split("-")[0][-8:]
            props = ",".join(char.properties)
            val = ""
            if "read" in char.properties:
                try:
                    v = await c.read_gatt_char(char.uuid)
                    val = f" = {v.hex()} ({len(v)}b)"
                except Exception as e:
                    val = f" = err:{e}"
            print(f"  {short} [{props}]{val}")

    # 配置寄存器
    configs = {
        "C1": "8082caa8-41a6-4021-91c6-56f9b954cc34",
        "C2": "724249f0-5ec3-4b5f-8804-42345af08651",
        "state": "64b4e8b5-0de5-401b-a21d-acc8db3b913a",
        "battery": "42c3dfdd-77be-4d9c-8454-8f875267fb3b",
        "counter": "b7de1eea-823d-43bb-a3af-c4903dfce23c",
    }
    print("\n=== 关键值 ===")
    for name, uuid in configs.items():
        v = await c.read_gatt_char(uuid)
        print(f"{name}: {v.hex()} ({int.from_bytes(v, 'little')} LE)")

    await c.disconnect()
    print("\n已断开")


if __name__ == "__main__":
    asyncio.run(main())
