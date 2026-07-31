# 鱼跃 5 HSE（安耐糖 / Anytime CGM）完整逆向文档

> 2026-07-31，传感器于 7/30 12:19 初始化，14 天有效期。
> 问题：手机 App BLE 连接层故障，无法读取数据。通过逆向工程直接从 BLE 广播包解密血糖。
>
> 产品实物型号由用户确认为 **5 HSE**。现有逆向记录抄录过 `CT5` 日志字段和类名，但仓库未保留原始 logcat 与反编译产物用于复核，因此不能用这些内部字符串推断产品型号或官方协议名称。

---

## 硬件

| 项目 | 值 |
|------|-----|
| 产品型号 | 鱼跃 5 HSE |
| 芯片 | Renesas DA14535 |
| 固件 | V1130_20250618 |
| 设备名 | Anytime6011016862 |
| 传感器码 | Q36011016862 |
| 密钥 (sureClose) | **121** |
| 校准 k | 1.11 |
| 校准 r | 1.0 |
| 算法版本 | 1.20.1.9_20250822 |
| 寿命 | 14 天 (endNumber=6740, endHour=337) |

## BLE

| 项目 | 值 |
|------|-----|
| Service UUID | `0000fef5-0000-1000-8000-00805f9b34fb` |
| Manufacturer ID | `0x4743` |
| MTU | 512 |
| 广播间隔 | 60-120 秒（非常稀疏） |
| 连接方式 | BLE bonding + 定向广播 + 隐私地址轮换 |

### GATT Services

```
0x180A Device Information:
  0x2A29 Manufacturer: "Renesas"
  0x2A24 Model: "DA14535"
  0x2A26 Firmware: "V1130_20250618"

FEF5 Custom (9 characteristics):
  8082caa8 [write, read]     — 配置寄存器
  724249f0 [write, read]     — 命令寄存器（写 01 返回 ACK 08）
  6c53db25 [read]            — 数据端口
  9d84b9a3 [write, read]     — 配置寄存器
  457871e8 [write, write-without-response, read] — 写通道
  5f78df94 [read, notify]    — 通知通道
  64b4e8b5 [read]            — 状态 (0d=终止)
  42c3dfdd [read]            — 电压/参考 (f400)
  b7de1eea [read]            — 计数器 (0002)

0x1000 Nordic DFU: OTA 固件升级
```

---

## 加密算法

### 密钥来源
传感器元数据 `sureClose` 字段（字符串数值）。通过 BLE 握手 `onSetIDResponse` 返回，存于 SharedPreferences。本传感器值为 **121**。

### ConvertTools.encode（Java → Python）
```python
def encode(data: bytes, key: int) -> bytes:
    # Step 1: 每字节 XOR key → 8-bit binary 拼接
    bits = ""
    for b in data:
        bits += format((b & 0xFF) ^ key, "08b")

    # Step 2: 左→右单次扫描: 若 bit[i+1]=='0', 翻转 bit[i]
    bits_list = list(bits)
    for i in range(len(bits_list) - 1):
        if bits_list[i + 1] == "0":
            bits_list[i] = "1" if bits_list[i] == "0" else "0"

    # Step 3: 每 8 bits → byte
    result = bytearray()
    for i in range(0, len(bits_list), 8):
        result.append(int("".join(bits_list[i:i+8]), 2))
    return bytes(result)
```

### 解密后格式（18 bytes = 6 records × 3 bytes）
```
每条记录 3 bytes (uint24 BE):
  bits[23:10] = glucose_raw  (14 bits)
  bits[9:0]   = temp_raw     (10 bits)

glucose_mmol = glucose_raw × 0.01
temperature  = temp_raw × 0.1 − 40.0
```

---

## 广播包结构（24 bytes）

```
Offset  Size   Field
0       2      固定头 (4d01)
2       2      序列号 (uint16 BE)
4       1      类型 (01=数据)
5       18     加密 payload = 6 条记录 × 3 bytes
23      1      校验
```

---

## App 逆向

| 项目 | 值 |
|------|-----|
| 包名 | com.yuwell.cgm |
| 版本 | 3.8.20.2 |
| SDK | ist.com.sdk |
| BLE 库 | Nordic Semiconductor Scanner Compat v18 |
| 算法库 | libalgorithm_jni_1_29_0_0.so |
| 加密库 | ConvertTools (XOR + bit-flip) |
| API 域名 | cgm.yuwell.com |
| MQTT | 鱼跃 IoT |

### 关键类

现有逆向记录称 App 的日志、数据字段和类名出现过 `CT5` 标识。以下名称为追溯既有分析而保留，仍需用原始 logcat 或 APK 反编译产物复核；它们不是产品型号。

- `ist.com.sdk.AlgorithmTools` — JNI wrapper（`decodeCT`, `algorithm`）
- `ist.com.sdk.ConvertTools` — 加密/解密（`encode`, `decode`）
- `ist.com.sdk.ProtocolTools` — 协议解析
- `com.yuwell.cgm.utils.ProtocolToolsHolder_CT5` — 内部以 `CT5` 命名的 TLV 解析类
- `ist.com.sdk.DataInput` / `DataOutput` — 算法输入/输出
- `ist.com.sdk.KRDecodeData` — QR码/NFC解码数据

### DataInput（算法输入）
`Iws[]` (工作电流), `Ibs[]` (背景电流), `Ts[]` (温度), `K0`, `R`, `glucoseId`, `algorithm`

### DataOutput（算法输出）
`GLU_MG` (血糖mg/dL), `BGCount`, `BGICount`, `trend`, `errorCode`, `warnCode`, `calibrationStatus`, `data_quality`

---

## 故障分析

**症状**: App 显示"已连接"但无新数据

**根因**: 传感器 BLE bonding 密钥损坏/隐私地址轮换后 IRK 解析失败，App 的 Nordic BLE Scanner 库按旧地址过滤扫描结果，永远扫不到设备。传感器本身正常测量并在广播包中输出数据。

**Mac 能连接但 App 不能**: 强制解绑后传感器的 BLE bonding 短期解除，Mac 趁机连上并读取 GATT services。但 App 端被云端标记为"已解绑"拒绝重新配对。

---

## 使用方法

### 单次扫描
```bash
/tmp/bleak-venv/bin/python3 decode.py
```

### 持续监控 + Web 界面
```bash
# 终端1: 持续扫描写数据库
/tmp/bleak-venv/bin/python3 monitor.py

# 终端2: Web 服务器
python3 server.py

# 浏览器
open http://localhost:8520
```

### GATT 直连（仅传感器未绑定时可用）
```bash
# 成功概率低，需多次尝试
/tmp/bleak-venv/bin/python3 connect_explore.py
```

### 依赖
```bash
python3 -m venv /tmp/bleak-venv
/tmp/bleak-venv/bin/pip install bleak
```

---

## 文件清单

| 文件 | 用途 |
|------|------|
| `README.md` | 本文档 |
| `decode.py` | 单次扫描+解密 |
| `monitor.py` | 持续监控 → SQLite |
| `server.py` | Web API 服务器 |
| `dashboard.html` | 前端图表 |
| `connect_explore.py` | GATT 连接探索 |
| `raw_samples.txt` | 原始广播包样本 |
| `cgm.db` | SQLite 数据库（自动生成） |
| `cgm_*.jsonl` | JSONL 历史数据（自动生成） |
| `latest.txt` | 最新读数摘要（自动生成） |
