# Anytime CGM 逆向全记录

> 按时间顺序记录 2026-07-31 的所有操作、发现、死胡同、突破。
>
> 产品实物型号由用户确认为 **鱼跃 5 HSE**。本文出现的 `CT5` 保留自当时记录的日志字段和类名；由于原始 logcat 与反编译产物未收入仓库，这些内部字符串仍待复核，不能作为产品型号依据。

---

## Phase 1: 问题确认 (09:30-09:45)

### 用户描述
- 鱼跃 5 HSE（安耐糖 / Anytime CGM，动态血糖仪），贴在身上过了一晚上
- 手机蓝牙设置里能看到 "anytime" 设备，但 app 里显示"没新数据"
- 已重装 app、重启手机，无效
- 不能 ADB 连手机（后来解决了）

### Mac 蓝牙扫描
- `system_profiler SPBluetoothDataType` — 只能看到已配对设备，没有 anytime
- 安装 `blueutil` (`brew install blueutil`)：`blueutil --inquiry` 是经典蓝牙 inquiry，扫不到 BLE 设备
- 安装 `bleak` Python 库：`python3 -m venv /tmp/bleak-venv && pip install bleak`

### 第一次 BLE 扫描
```
发现 20 个设备，但没有 anytime
10 秒扫描窗口太短
延长到 30 秒 → 发现 Anytime6011016862, RSSI -75dBm
```
**结论**: 传感器硬件在广播，但广播间隔很长（>10 秒），需要长扫描窗口。

### 第一次连接尝试
Mac 能扫到但不能 GATT 连接 → `BleakDeviceNotFoundError`
**发现**: 传感器使用定向广播 (directed advertising)，只接受已配对手机的连接。

---

## Phase 2: BLE 原理讲解 (09:45-10:00)

向用户解释了 BLE vs 经典蓝牙的区别：
- BLE 不"配对"（bonding ≠ pairing），通过广告包广播 + 临时 GATT 连接交换数据
- 蓝牙设置能看到名字 ≠ 已连接
- 血糖仪每次连接只维持几秒：连接 → 读数据 → 断开
- 用户的症状（能看到名字、连上后不显示"已连接"、无数据）完全符合 BLE 工作模式

---

## Phase 3: 手机 ADB 抓日志 (10:00-10:15)

### ADB 连接
- 下载 Android Platform Tools (`/tmp/platform-tools/adb`)
- 手机是一加 13 (PJZ110)，Android 15
- `adb devices` → a048720a

### 尝试开启 HCI Snoop
- OnePlus 不走标准 `btsnoop_hci.log`，用的是高通私有 CFA 格式（全空）
- `setprop persist.bluetooth.btsnoopenable` 无权限
- 改用 `adb bugreport` 生成完整 Bug Report（43MB）

### logcat 抓蓝牙日志
清空 logcat → 用户打开 app 连接 CGM → 抓日志

**关键发现**:
```
[CGM] DataFragmentCT5 onBleConnecting        ← app 开始连
[CGM] CGMService startScan: deviceName:Anytime6011016862  ← 按名称扫描
[CGM] CGMController isDeviceConnected:false   ← 20秒后
[CGM] CGMStateManager State: DISCONNECTED_UI ← 35秒后放弃
bt_btm_pm: BTM_PM_ReadBleScanDutyCycle        ← BLE 扫描确实启动了
```

**结论**: App 的 BLE 扫描跑了但没找到设备。扫描窗口 120ms/1200ms（10% 占空比）。

### 广告包分析（同时）
从 Mac 端抓到广播包 Manufacturer Data:
```
Company ID: 0x4743 (= ASCII "GC")
Data: 4d0116a4012e79b8d186472e76472e65b82e6a45d1924472 (24 bytes)
Service UUID: FEF5 (非标准，不是 Bluetooth SIG 的 Glucose 0x1808)
Connectable: YES (kCBAdvDataIsConnectable = 1)
```

**死胡同**: 多种 key 尝试 XOR 解密广播包，未果。尝试连接始终失败（定向广播 + BLE 隐私地址）。

---

## Phase 4: 深度 logcat 分析 (10:15-10:30)

### 提升蓝牙日志级别
```bash
adb shell setprop log.tag.bt_btif DEBUG
adb shell setprop log.tag.bt_att DEBUG
```

### App 详细日志
```
[CGM] CGMService startConnect → startScan
[CGM] CGMStateManager need show ble connected in 20455 (20秒倒计时)
[CGM] getTransmitterPreload: ← 关键数据！
    deviceType: "CT5"
    name: "Anytime6011016862"
    address: "D1:6E:13:85:28:61"   ← App 持有的设备地址
    SensorInfo: "Q36011016862"      ← 传感器码
    sureClose: "121"                ← 后来才知道这是解密密钥！
    k: "1.11", r: "1.0"           ← 校准参数
    BloodCount: "0"                 ← 传感器报告 0 个读数
    dataflag: "0"
    endDay: "14", endNumber: "6740" ← 14天寿命
    algorithmVersion: "1.20.1.9_20250822"
    deviceId: "0a60820678e0402dbe079cf51b4924a1"
```

### 血糖缓存数据
```json
{
  "showGlu": "5.0",     ← App 显示 5.0（来自缓存）
  "bg": "0.0",          ← 实际血糖 0（当前无读数）
  "glucoseId": "243",
  "time": "1785429113034",  ← 2026-07-31 00:31:53 GMT+8
  "BGCount": "0",       ← 无新读数
  "T": "32.91",         ← 温度 32.91°C
  "Iw": "6.28",         ← 工作电流
  "Ib": "0.0",          ← 背景电流
  "voltage": "0.0"      ← 全部电压为 0
}
```

**初步结论（后被修正）**: 以为是传感器电极失效。后来发现这些默认值只是缓存。

### 地址不匹配发现
- App 存储: `D1:6E:13:85:28:61`（静态随机地址）
- Mac 扫到: `B6385879-5422-979E-1926-99556882A15B`（可解析隐私地址 RPA）
- 这是 BLE 隐私功能——设备定期更换广播地址，只有持有正确 IRK 密钥的手机能解析
- **根因假设**: 手机 IRK 失效 → 无法解析新地址 → 扫描永远对不上

### 用户确认
- 手机蓝牙设置能看到 "anytime"（未配对设备，无齿轮图标）
- 是未配对 BLE 设备，不是经典蓝牙配对

### 蓝牙关→开测试
关蓝牙 → 开 app → app 请求开蓝牙 → 开启 → 抓日志
结果: App 仍扫不到设备，BLE 扫描日志 `bt_btm_pm` 确认扫描在跑。
未发现任何 `onScanResult` 回调触发。

---

## Phase 5: 强制解绑 (10:30-10:45)

### 用户操作
- App 内点击"结束佩戴" → 失败（需要 BLE 连接才能解绑）
- App 弹窗："若设备已丢失或损坏，可选择强制解绑。强制解绑后，该设备将无法再次连接使用"
- 用户选择强制解绑

### 强制解绑后
- Mac **首次成功连接** CGM！
- 发现 GATT Services:
  ```
  0x180A Device Info: Renesas DA14535, FW V1130_20250618
  FEF5 Custom: 9 个 characteristic
  0x1000 Nordic DFU
  ```

### 传感器详细状态
```
8082caa8 (config): 0f60 (= 3936, app 的 ble config 值)
724249f0 (cmd):    0f60
64b4e8b5 (state):  0d (= 13, 状态码)
42c3dfdd (batt?):  f400
b7de1eea (cnt?):   0002
5f78df94 (notify): 00
6c53db25 (data):   空 (0 bytes)
```

### 命令探索
- 写 `01` → `724249f0` → NOTIFY `08` (ACK)
- 所有命令 (`02`,`03`,`04`,`05`,`aa`) 都返回 `08`
- 写 `0000` 可重置 config 值
- data port (`6c53db25`) 始终为空
- state (`0d`) 不可变

**结论**: 传感器在终止状态（state=13），不再产出新数据。但 BLE 通道全通。

---

## Phase 6: 真正的问题 (10:45-11:00)

### 重新评估
- 传感器昨天 12:19 初始化，最后读数 00:31
- `BGCount: 0`, `dataflag: 0` — 传感器报告无新数据
- 温度 `32.91°C` — 温度探针正常
- 电压全 0 — 可能是默认值，不一定是硬件故障

### 用户信息
- 睡觉时可能压到了传感器（导致探针移位）
- 传感器 12 小时就废了 14 天的寿命

### 尝试重新配对
- 强制解绑后无法重新配对（云端标记传感器已废弃）
- App 显示"设备已在使用中"

---

## Phase 7: 广播包解密突破 (11:00-11:30)

### 反编译 APK
- `adb pull` 拉取 APK (187MB)
- `jadx` 反编译 → `/tmp/cgm-jadx/`

### 关键类发现
```
ist.com.sdk.AlgorithmTools     ← JNI 封装 (decodeCT, algorithm)
ist.com.sdk.ConvertTools       ← 加密/解密 (encode, decode)
ist.com.sdk.KRDecodeData       ← QR码解码数据（不是实时血糖）
ist.com.sdk.DataInput/Output   ← 算法 I/O
com.yuwell.cgm.utils.ProtocolToolsHolder_CT5 ← 内部以 CT5 命名的协议类
```

### 加密算法提取
`ConvertTools.encode(byte[] data, int key)`:
1. 每字节 XOR key → 转二进制字符串
2. 左→右扫描: 若 `bit[i+1]=='0'`, 翻转 `bit[i]`
3. 每 8 位 → 字节

### 密钥发现
`ProtocolToolsHolder_CT5.verify()` 中:
```java
this.f29669i0.setKCipher(currentDevice.sureClose);
```
密钥 = `sureClose` 字段 = **121**

### 广播包结构
```
24 byte Manufacturer Data:
  [0:2]   4d01         固定头
  [2:4]   16a4         序列号 (uint16 BE)
  [4]     01           类型标志
  [5:23]  加密 payload  18 bytes = 6 records × 3 bytes
  [23]    校验
```

### 解密后格式
```
每条记录 3 bytes (uint24 BE):
  bits[23:10] = glucose_raw (14 bits)
  bits[9:0]   = temp_raw (10 bits)

glucose = glucose_raw × 0.01 mmol/L
temp = temp_raw × 0.1 - 40.0 °C
```

### 第一次成功解密
```
Key = 121
S1 (09:53): 4.3→4.5 mmol/L, 29.7→30.1°C
S2 (10:05): 4.7→4.8 mmol/L, 30.2→30.8°C
S3 (10:10): 4.7→4.8 mmol/L, 30.6→31.0°C
S4 (20:26): 4.6→4.8 mmol/L, 30.8→31.1°C
```

**传感器一直在正常工作！** 只是 App BLE 层故障读不到数据。

---

## Phase 8: 构建监控系统 (11:30-12:00)

### SQLite 数据库结构
```sql
CREATE TABLE readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,    -- ISO 8601 +08:00
    counter INTEGER,            -- 广播包序列号
    reading_index INTEGER,      -- 推算的读数编号
    glucose_mmol REAL,          -- 血糖 mmol/L
    glucose_mg INTEGER,         -- 血糖 mg/dL
    temperature_c REAL,         -- 温度 °C
    rssi INTEGER,               -- 信号强度 dBm
    raw_hex TEXT                -- 原始广播包 hex
);

CREATE TABLE scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    name TEXT,                  -- 设备名
    address TEXT,               -- BLE 地址
    rssi INTEGER,
    counter INTEGER,
    raw_hex TEXT,
    record_count INTEGER        -- 本次扫描解密出的记录数
);
```

### 系统架构
```
monitor.py (launchd, 每2分钟) → BLE扫描 → 解密 → SQLite
server.py  (launchd)           → 读取 SQLite → Web API (:8520)
dashboard.html                 → 前端图表 ← API
```

### launchd 服务
- `~/Library/LaunchAgents/com.qqqqqf.cgm-monitor.plist`
- `~/Library/LaunchAgents/com.qqqqqf.cgm-server.plist`
- 开机自启、崩溃重启、不依赖 Claude Code

### 数据目录
- 生产: `~/Library/Application Support/cgm-data/`
- 副本: `~/Documents/cgm-data/`

---

## 关键数字汇总

| 项目 | 值 |
|------|-----|
| 传感器码 | Q36011016862 |
| 设备名 | Anytime6011016862 |
| 解密密钥 | 121 (sureClose) |
| 校准 k | 1.11 |
| 校准 r | 1.0 |
| 算法版本 | 1.20.1.9_20250822 |
| 制造商 ID | 0x4743 |
| Service UUID | FEF5 |
| 芯片 | Renesas DA14535 |
| 固件 | V1130_20250618 |
| 初始化 | 2026-07-30 12:19 |
| 最后 App 读数 | 2026-07-31 00:31 (5.0 mmol/L) |
| 传感器状态 | 0d (13, 已终止) |

## 广播包样本

```
09:53  4d0116a4012e79b8d186472e76472e65b82e6a45d1924472  ctr=5796  g=4.3-4.5
10:05  4d0116bf01d151b9d15d92d15192d119922ee66dd11e6f80  ctr=5823  g=4.7-4.8
10:10  4d0116c1012eae6d2ee66dd119922ee1902ef590d10191d3  ctr=5825  g=4.7-4.8
11:35  4d0116cb012ec5912eea6f2efa6ed151922eae6fd159903c  ctr=5835  g=4.6-4.9
```

## GATT Characteristic 完整表

```
FEF5 Service:
  8082caa8 [write,read]     配置寄存器 (存 0f60=3936)
  724249f0 [write,read]     命令寄存器 (写01→NOTIFY 08)
  6c53db25 [read]           数据端口 (始终空)
  9d84b9a3 [write,read]     配置寄存器
  457871e8 [write,write-without-response,read] 写通道
  5f78df94 [read,notify]    通知通道 (值=00/08)
  64b4e8b5 [read]           状态 (0d=终止)
  42c3dfdd [read]           参考值 (f400)
  b7de1eea [read]           计数器 (0002)

0x180A Device Information:
  2a29 Manufacturer: "Renesas"
  2a24 Model: "DA14535"
  2a26 Firmware: "V1130_20250618"
  2a28 Software: "V1130_20250618"
  2a23 SystemID: 123456fffe9abcde
  2a50 PnPID: 01d20080050001

0x1000 Nordic DFU:
  00001001 [notify]
  00001002 [write-without-response]
```

## App 关键类

```
ist.com.sdk.AlgorithmTools          JNI封装 (decodeCT, algorithm)
ist.com.sdk.ConvertTools            加解密 (encode, decode)
ist.com.sdk.ProtocolTools           协议解析
ist.com.sdk.DataInput               算法输入 (Iws, Ibs, Ts, K0, R)
ist.com.sdk.DataOutput              算法输出 (GLU_MG, BGCount, trend, errorCode)
ist.com.sdk.KRDecodeData            QR/NFC解码
com.yuwell.cgm.utils.ProtocolToolsHolder_CT5  内部 CT5 命名的 TLV 解析 + BroadData
```

## 工具链

| 工具 | 用途 |
|------|------|
| bleak (Python) | BLE 扫描+GATT |
| jadx | APK 反编译 |
| adb (Android) | 手机日志/APK 提取 |
| blueutil | macOS 蓝牙工具 |
| SQLite | 数据存储 |
| launchd | 系统级进程管理 |
