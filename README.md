# 鱼跃 5 HSE（安耐糖 / Anytime CGM）完整逆向文档

> 2026-07-31，传感器于 7/30 12:19 初始化，14 天有效期。
> 问题：手机 App BLE 连接层故障，无法读取数据。通过逆向工程直接从 BLE 广播包解密血糖。
>
> 产品实物型号由用户确认为 **5 HSE**。现有逆向记录抄录过 `CT5` 日志字段和类名，但仓库未保留原始 logcat 与反编译产物用于复核，因此不能用这些内部字符串推断产品型号或官方协议名称。
>
> 本项目是未经认证的逆向研究工具，不用于医疗诊断或治疗决策。协议末尾校验算法尚未确认，界面展示的是现有证据支持的解码结果。

---

## 硬件

| 项目 | 值 |
|------|-----|
| 产品型号 | 鱼跃 5 HSE |
| 芯片 | Renesas DA14535 |
| 固件 | V1130_20250618 |
| 设备名 | `Anytime<设备序列>` |
| 传感器码 | 设备专属，不纳入代码 |
| 密钥 (sureClose) | 设备专属，通过环境变量配置 |
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
传感器元数据 `sureClose` 字段（字符串数值）。现有逆向记录称它通过 BLE 握手 `onSetIDResponse` 返回并存于 SharedPreferences。该值属于设备配置，不应硬编码或提交到仓库。

### ConvertTools.encode（Java → Python）
```python
def decode_payload(data: bytes, key: int) -> bytes:
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

最后一个字节会被解析并保留，但仓库现有证据不足以确认其算法，因此当前实现不会声称已经完成校验。

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

**当前假设**: BLE bonding、隐私地址轮换或 App 的扫描过滤可能导致 App 找不到设备。由于原始 logcat、HCI 抓包和反编译产物未收入仓库，这条因果链尚未达到可复核的“已确认根因”标准。

**Mac 能连接但 App 不能**: 强制解绑后传感器的 BLE bonding 短期解除，Mac 趁机连上并读取 GATT services。但 App 端被云端标记为"已解绑"拒绝重新配对。

---

## 使用方法

### 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
pnpm --dir web install
pnpm --dir web build
```

Python 需要 3.10 或更高版本；前端使用 Vite、React、Tailwind CSS v4 和 shadcn/ui Base Nova。

### 配置

设备密钥是必填项。数据库默认位于 `~/Library/Application Support/cgm-data/cgm.db`，可以显式覆盖：

```bash
export OPENANYTIME_KEY='<sureClose>'
export OPENANYTIME_DEVICE_NAME='Anytime<设备序列>'
export OPENANYTIME_DB="$HOME/Library/Application Support/cgm-data/cgm.db"
export OPENANYTIME_TIMEZONE='Asia/Shanghai'
```

可选参数包括 `OPENANYTIME_SCAN_TIMEOUT`、`OPENANYTIME_SCAN_INTERVAL` 和 `OPENANYTIME_READING_INTERVAL`。

持续写库时必须指定设备名片段，并建议使用完整广播名；一个数据库只对应一个传感器，防止附近其他设备使用相同 counter 时污染历史数据。

### 显式初始化数据库

普通服务永远不会自动创建数据库；首次使用必须单独初始化：

```bash
.venv/bin/python db_tool.py --db "$OPENANYTIME_DB" init
.venv/bin/python db_tool.py --db "$OPENANYTIME_DB" check
```

如果目标文件已经存在，`init` 会拒绝覆盖。修改 schema 不能通过删库重建完成，详细规则见 `AGENTS.md`。

### 单次扫描

```bash
.venv/bin/python decode.py --key "$OPENANYTIME_KEY"
```

本次扫描没有发现设备属于正常结果，命令会说明状态而不是抛出 traceback。

### 持续监控与 Web 界面

```bash
# 终端 1：持续扫描；扫描失败会有限退避，数据库异常会 fail closed
.venv/bin/python monitor.py

# 终端 2：只读 API + 已构建前端，仅监听 loopback
python3 server.py

open http://localhost:8520
```

前端开发时先运行只读 API，再启动 Vite：

```bash
pnpm --dir web dev
# http://localhost:5173，/api 自动代理到 127.0.0.1:8520
```

### 数据库检查与备份

```bash
.venv/bin/python db_tool.py --db "$OPENANYTIME_DB" check
.venv/bin/python db_tool.py --db "$OPENANYTIME_DB" backup
```

备份使用 SQLite Backup API，在新路径创建并验证，绝不覆盖既有文件。数据库、WAL、SHM、JSONL 和日志都是受保护数据，不能使用 `git clean -fdx` 或运行目录同步删除。

### GATT 只读探索

```bash
.venv/bin/python connect_explore.py --device anytime
```

脚本只创建一个受控连接任务，未发现设备时正常返回，并确保连接退出时断开。

### 测试

所有测试使用临时数据库，不接触生产数据：

```bash
.venv/bin/python -m unittest discover -v
pnpm --dir web typecheck
pnpm --dir web lint
pnpm --dir web build
```

---

## 文件清单

| 文件 | 用途 |
|------|------|
| `README.md` | 本文档 |
| `openanytime/` | 配置、协议、扫描、存储和监控核心 |
| `decode.py` | 单次验证、扫描和解码 |
| `monitor.py` | 有限退避的持续监控 → SQLite |
| `db_tool.py` | 显式建库、只读检查和安全备份 |
| `server.py` | 只读 Web API 与静态资源服务器 |
| `web/` | shadcn/ui React 仪表盘 |
| `connect_explore.py` | 只读 GATT 连接探索 |
| `raw_samples.txt` | 原始广播包样本 |
| `tests/` | 协议、扫描、事务、重试与 API 边界测试 |

运行时数据库和采集数据不属于 Git 仓库，也不会由普通启动流程自动生成。
