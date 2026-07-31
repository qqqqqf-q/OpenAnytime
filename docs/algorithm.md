# 官方血糖算法的本地执行与「官方口径」数据管线

> 2026-07-31 落地。官方 App 显示的血糖不是传感器原始电流(Iw),
> 而是闭源 native 库 `libalgorithm_jni_1_29_0_0.so` 的输出(`GLU_MG/18`,
> 四舍五入 1 位小数;Java 层零加工,证据 `CGMCallbackHandler.java:1343-1352`)。
> 本文档描述我们如何在没有官方 App 的情况下得到同样的值。

## 1. 方法:原样执行,不是拟合

库内部(多级 EMA、滑动窗口、温度补偿、限速)静态还原尚未完成,但
`openanytime/native_emulator.py` 用 Unicorn CPU 模拟器在 Mac 上**逐条执行**
.so 的 arm64 指令:ELF 加载、重定位、PLT 外部符号桩(exp/rand/time 等)
全部手工补齐,调真实入口 `CGM_algorithm`(0x820B4)。

**零拟合参数**。交叉验证(2026-07-31):与手机 adb 执行同一 .so,
566/566 点 GLU_MG 完全一致;凌晨段与官方 App 截图真值逐点一致。

- .so 不入 git(闭源):`~/Library/Application Support/cgm-data/native/libalgorithm_jni_1_29_0_0.so`,
  sha256 `34feac04…0d9`;路径可用 `OPENANYTIME_NATIVE_LIB` 覆盖。
- 输入要求:glucose_id 从 0 连续(算法有跨点状态);本传感器 Ib 恒 0。
- 模拟器对未实现的外部符号宁可抛错也不编造返回值。

## 2. 官方口径派生库

`build_official_db.py`(launchd `com.qqqqqf.cgm-official`,每 180s):
读 `cgm.db`(只读)→ 每 glucoseId 取一行(历史行优先,广播行按
flag 偏移 5371/5115 映射补缺)→ 全量回放算法 → 写 `cgm-official.db`:
网格时间戳、`reading_index = glucoseId`、`glucose_mmol = round(GLU_MG/18, 1)`。

安全性质:

- 临时文件 + `os.replace` 原子替换,读方不见半截文件;
- **锚点校验**:id 233 必须 ≈116 mg/dL(官方真值 6.4),失败拒绝替换
  (防 .so 损坏、模拟器回归、flag 偏移漂移);
- 重建 <1s(677 点),失败只记日志,下轮自愈;
- 日志:`~/Library/Application Support/cgm-data/official-rebuild.{log,err}`。

## 3. 服务拓扑(2026-07-31 起)

| 端口 | 进程 | 数据 |
|---|---|---|
| 18520 | Vite dev(0.0.0.0,代理 /api→8520) | **主页面:官方算法口径** |
| 8520 | launchd `com.qqqqqf.cgm-server`(repo `server.py`) | cgm-official.db(只读 API) |
| 18522 | nohup `server.py`(0.0.0.0) | **对比页:原始 Iw 口径**(cgm.db) |
| — | launchd `com.qqqqqf.cgm-monitor` | 广播监听 + 每小时 GATT backfill → cgm.db |
| — | launchd `com.qqqqqf.cgm-official` | 每 180s 重建 cgm-official.db |

官方口径链路:传感器 → monitor → cgm.db → 重建任务 → cgm-official.db
→ 8520 → 18520。端到端延迟:≤3min(采集)+ ≤3min(重建)。

回滚 8520 到旧独立服务器:
`cp ~/Library/Application Support/cgm-data/com.qqqqqf.cgm-server.plist.bak-20260731
~/Library/LaunchAgents/com.qqqqqf.cgm-server.plist && launchctl unload/load`。

## 4. 已知边界

- 广播补位行(最新 ~10 个 id,历史通道未补到)的 Iw 与历史通道逐点一致
  (已验证),Ib 用 0(本传感器恒 0);
- 换传感器后:`OPENANYTIME_INIT_TIME`(monitor plist)与 flag 偏移
  (`build_official_db.py` 顶部)都必须重测;锚点校验会拦住偏移漂移,
  但 id 233 的锚点值本身也是本传感器专属;
- 纯 Python 静态还原(去掉 .so 依赖)仍在进行,见
  `native/algorithm_research.py` 的地址注释;core 函数
  `rfbNsvpyGtgjJmylzyuGrmxbnc` @0x7c2c8 的 n≥11 分段窗口未还原。
