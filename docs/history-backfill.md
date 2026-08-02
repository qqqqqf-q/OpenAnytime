# 5 HSE 历史回补(GATT Backfill)协议与实现

> 2026-07-31 实测验证。本文档是历史回补通道的权威记录;广播协议与逆向过程
> 见 [TIMELINE.md](TIMELINE.md)。协议证据来自官方 App 反编译(jadx 产物在
> `/tmp/cgm-jadx`,易失)+ 实机 GATT 抓包,凡冲突处以实机为准。

## 1. 双通道架构

| | 广播(被动) | GATT 历史(主动) |
|---|---|---|
| 角色 | 主通道,常态 | 补洞机制 |
| 内容 | 6 条 × 3 字节滑动窗口(18 分钟) | 设备缓冲全量(实测 28h+,远超传闻的 12h) |
| 每条字段 | 14bit 电流 + 10bit 温度 | Iw/Ib/温度/trend/12bit 粗值/errorCode/4 路电压(15 字节) |
| 触发 | 监听即得 | 断档 > 6 条时才有意义;官方 App 也在此时才连接 |

官方策略(反编译确认):广播连续性检查失败(日志 "device disconnected for
more than 18 minutes")→ 发起 GATT 连接 → 拉取缺失段 → 断开继续听广播。
连接是独占链路:一台设备连上时,其他中心无法连接,广播也可能暂停。

## 2. GATT 通道

### 2.1 UUID(实测)

| 角色 | UUID |
|---|---|
| Service | `00001000-1212-efde-1523-785feabcd123` |
| 写命令 | `00001002-1212-efde-1523-785feabcd123`(write-without-response) |
| 响应 notify | `00001001-1212-efde-1523-785feabcd123` |

**FEF5 不是数据通道**:同样的 pull 命令写入 FEF5 写通道只回 `0x08`(实测)。
早期探查把 0x1000 误认为 Nordic DFU 而忽略,是此前找不到历史 API 的原因。

### 2.2 会话时序

```
连接 → 订阅 1001 notify
     → checkId [0x31, b0..b3, sum]        # 硬门禁:跳过则后续全部沉默
     → setDate [0x03, y-1900, M, D, h, m, s, sum]
     → 循环:pull [0x37, idLo, idHi, count, sum]
            ← notify 返回一帧(见 2.3)
            下一帧 start = 本帧末条 id + 1,直到空帧/无响应
```

- checksum = 前序字节和的低 8 位;命令均为明文。
- checkId 响应 `[0x31, ..., 0x01]` 为通过。**解绑态设备接受全零 randomB**(实测);
  绑定态需要账号派生 randomB(绑定账号 id 第 9-12 位,反编译证据)。
- **一个 notify 携带完整一帧**,故 count 必须按协商 MTU 计算:`(mtu-7)//11`,上限 45。
- setDate 写设备时钟,用本地时区时间。

### 2.3 响应帧

```
[0x37, startIdLo, startIdHi, <加密的记录区>, checksum]
```

- 记录区用与广播相同的位翻转变换 + cipher(本机 121)解密(见 TIMELINE)。
- **0xFC 整块 = 数据结束填充(明文,不加密);0xFF 整块 = 无效记录,跳过。**
- 结束 sentinel:解密后 Ib≈Iw>655 且 T>215。
- **实测为电压模式:15 字节/条**,MTU 512 时每帧 33 条(499 字节)。
  495 字节的记录区同时被 11 和 15 整除,长度无法区分模式;解析器按
  「解密后物理合理(温度/粗值在界内)的记录数减乱码数」选步长。
  反编译中另有 11 字节普通模式,本机未出现。

15 字节记录布局(解密后):

```
[0:2] Ib (BE ushort / 100)   [2:4] Iw (BE ushort / 100)
[4]   温度整数 (byte - 40)   [5]   温度小数 (byte / 100)
[6]   高 4 位 trend,低 4 位血糖高位
[7]   血糖低位(粗值 = (高4位<<8 | 低位) / 18.0)
[8]   errorCode              [9:11] 保留
[11:15] 4 路电极电压 (byte × 6)
```

## 3. 字段语义与「血糖」真相

- **glucoseId 与时间**:`t = init_time + (glucoseId + 1) × 3min`。
  initNumber=15(id 0-13 为预热期,粗值 0),endNumber=7695。
  init_time 是每传感器参数,已知命令无法查询,需配置(本机 2026-07-30T12:19+08:00,
  已由官方 App logcat 缓存双重锚定:id 243 @ 00:31:53)。
- **广播 counter ≠ glucoseId**:实测 counter = glucoseId + 偏移(本机 ~5370)。
  反编译里官方 App 把广播 counter 直接当 glucoseId 用,但数值上两者明显不同域,
  此矛盾未解,以实测为准。
- **广播的 packet flag 会不定期切换(2026-08-02 五次确认)**:flag 携带完全相同的
  6×3 字节记录格式、同一 cipher,但**各自独立的计数序列**。已观测五次切换,
  flag 单调递增:0x01 →(07-31)0x02 →(08-01 凌晨)0x03 →(08-01 下午)0x04
  →(08-02 17:07)0x05 →(08-02 17:12)0x06,且**偏移严格递减 256**:
  `offset(flag) = 5627 − 256 × flag`(flags 1-6 全部取值锚定验证成立)。
  新 flag 出现时可用该公式预判偏移,再取值锚定复核。
  **解析器按有界范围(0x01-0x0F)放行**,不再枚举——枚举已造成两次全线停流事故。
  **包身份必须是 (flag, counter)**:两个序列数值区间会随时间重叠,若只按
  counter 存库,新序列爬升数小时后 会撞上旧序列的旧行(UNIQUE 冲突)。
  本地存储对 flag≠0x01 的 counter 做 `+ flag*100000` 命名空间隔离。
  **运维教训**:广播停流先查监控日志 `unsupported packet flag`;
  官方口径重建日志出现 `unconfigured flags [N]` 时,说明设备切了新 flag,
  按公式补偏移到 `OPENANYTIME_FLAG_OFFSETS` 即可。
- **Iw ≡ 广播 14bit 字段**:13 个重叠点比值 1.00±0.05。本地 readings.glucose_mmol
  存的就是这个原始工作电流值,不是官方 App 的显示值。
- **官方显示值 = 私有算法输出**:App 把 Iw/Ib/T/k/r(k=1.11, r=1.0)+ 指血校准事件
  喂给 native 库(`libalgorithm_jni_1_29_0_0.so`)。实测官方 vs Iw:
  平稳期基本相等,快速变化段可差 1+ mmol/L(限速/平滑行为,非恒定比例)。
  12bit 粗值字段 ≈ Iw/1.6,官方 App 也不直接显示它。
- **官方曲线的像素级数字化方法**(用于获取真值基准):曲线为等距黑点(3min/点),
  y 用目标线标签(7.8/3.9)自校准,x 用刻度标签或绿点(当前值)锚定,
  可用官方统计值(最高/最低及其时刻)交叉校验。见傍晚案例:绿点 19:52=5.80、
  min 4.82@18:43、max 6.88@19:31,与统计值分毫不差。

## 4. 本地实现

- `openanytime/history.py`:GATT 拉取(会话、帧解析、步长识别)。
- `openanytime/monitoring.py`:`run_backfill` 启动时 + 每
  `OPENANYTIME_BACKFILL_INTERVAL` 秒(默认 3600)执行;失败只告警,
  绝不影响广播主循环。
- `openanytime/storage.py:save_history_records`:写入 readings,
  `reading_index = glucoseId`、`counter = -1`(标记无广播 counter)。
  **去重必须按真 glucoseId,不能按时间邻近**:广播行时间戳以捕获时刻为锚,
  相对真网格可晚达 4 分钟(扫描节奏 + 设备处理延迟),任何时间取整/邻近规则
  都会错位。偏移(counter = glucoseId + 每 flag 常数)的推导**必须用取值锚定**:
  在时间邻域 ±3 槽内按 (血糖, 温度) 匹配历史行后投票。本机偏移满足
  `offset(flag) = 5627 − 256 × flag`(flag 1-6 = 5371/5115/4859/4603/4347/4091,
  逐 flag 取值锚定验证:flag-1 5887=4.0 对应 id 516,flag-2 5632=4.2 对应 id 517,
  flag-3 counter 5794-5797 末条对应 id 935-938,flag-5 包 5887 序列 [8.6,8.4,8.2,
  8.0,7.9,7.7] 对应 id 1535-1540;注意广播 est 时间与槽位不是一回事,
  同 est 时刻的两行未必是同一读数)。
- `openanytime/storage.py:save_sample`:广播入库时若该槽已有历史行(counter=-1)
  则跳过(±1 槽容差,宁多跳;漏了会被下一次 backfill 以网格时间补回,不会双点)。
- `openanytime/monitoring.py:run_backfill`:每次拉取**末尾 10 个 id 让给广播通道**
  (live 边缘 30 分钟),杜绝「历史先写、广播后写」的反向重复;让出的 id 下一轮
  自然补入。
- `openanytime/storage.py:save_sample`:广播行以 `_stored_counter(flag, counter)`
  命名空间隔离(flag 0x01 保持原值,其余 `+ flag*100000`),scans 与 readings
  的 reading_index 同步使用该值。
- `gatt_pull.py`:手动拉取 CLI(`uv run python gatt_pull.py --start-id 0 --out ...`)。

配置(launchd plist EnvironmentVariables):

```
OPENANYTIME_KEY=<sureClose>          # 广播/历史 cipher(设备专属,勿提交真实值)
OPENANYTIME_DEVICE_NAME=anytime
OPENANYTIME_TIMEZONE=Asia/Shanghai
OPENANYTIME_INIT_TIME=2026-07-30T12:19:00   # 每传感器,换传感器必须更新
OPENANYTIME_BACKFILL_INTERVAL=3600
# OPENANYTIME_RANDOM_B=0,0,0,0      # 绑定态才需要
```

部署:`~/Library/LaunchAgents/com.qqqqqf.cgm-monitor.plist` 以
`uv run --no-sync python monitor.py`(工作目录为仓库)运行。
回滚:ProgramArguments 改回 `/tmp/bleak-venv/bin/python3
/Users/qqqqqf/Library/Application Support/cgm-data/monitor.py` 即可(旧独立脚本,
无 backfill)。

## 5. 事故与坑(不要再踩)

1. **握手响应必须排空再拉取**:checkId/setDate 的响应与数据帧同通道。
   若积压在队列里,拉取循环会把它们当噪声帧并因此**重发同一 pull**,
   设备对每次重发都回一帧,级联成每帧三份(2026-07-31 首次自动 backfill
   拉了 1860 条 = 620 id × 3,靠时间戳去重才没弄脏库)。
2. **checkId 是硬门禁**:不发则 setDate/pull 全部沉默(不是报错,是沉默)。
3. **flag 会切换,解析器不能写死 0x01**:严格解析曾只认 flag 0x01,设备切换到
   flag 0x02 后新 monitor 全线丢包(2026-07-31 晚发现并修复)。同理,
   计数序列以 (flag, counter) 为身份,见第 3 节。
4. **自动化 shell 的 TZ 可能不是本机时区**(观测到 UTC-7):所有时间对比
   一律显式 `TZ=Asia/Shanghai`,setDate 同理。
5. GATT 连接与官方 App/手机互斥:backfill 期间(秒级)手机侧可能连不上,会自愈。

## 6. 验证记录(2026-07-31)

- 全量拉取:566 条(id 0-565,07-30 12:19 → 07-31 16:37),`output/history_2026-07-31.jsonl`。
- 与官方 App 截图对齐:傍晚段峰/谷时刻分毫不差(峰值 20:37/20:40 差一个刻度);
  凌晨段 id 243 的 Iw=6.28 与 App 缓存字段级一致。
- 凌晨 V 型急跌(07:43-07:55 Iw 5.5→1.2→4.4,伴随温度冲高)判读为压迫假低,
  说明 Iw 口径在判读时必须结合形态,不能单点读数。
- 自动 backfill 首跑:修正级联 bug 后,插入 484 行历史(其余时段广播已覆盖,按时间戳跳过)。
