# Replay 语料运输 + 懒加载 PPO + 大 batch 缩放（plan v8）

> 状态：**待评审 v8；P1 已实现并测试（2026-11-15）**（2026-11-15 已处理 qw v4 + k3 v6 两轮评审，并补用户核查：动作捕获完整性 / opening 接管。v8 新增：v3 move/fire/hold 逐 tick 记录契约、converter 只用 frames 不用 `a_*`、OpeningController 不重跑、`opening-null-move` 疑点探针。用户拍板见 §11.1，评审处置见 §1.8/§1.9，重新拍板见 §11.3。P1 进展见 §8 P1：`--corpus-fmt`、replay 录制、生产转换器、identity/零落盘/坏 replay 测试全绿。）
> 来源：g5 系列在 35 关上「每 it 轮换 7 关」的传输瓶颈；目标 35×5 / 35×20 样本量。
> 关联：AGENTS §15.1/§15.5/§0.2 · `plan/it-stage-ratation.plan.md` · `plan/autonomous-cloud-overrides.plan.md` · `docs/nn/remote-transport.md` · `docs/nn/training-stack.md`
> 非目标：不改 PPO 数学（engine 的 clip/GAE/entropy 一行不动）· 不改任务侧（关卡/命数/敌数/max_ticks/终局标准）· 不把游戏仿真搬到 GPU · 不删除 npy 路径 · 不在本 plan 内改 eval 口径或难度。

---

## 0. 一句话

请求（任务/rollout spec）显式声明 `corpus_fmt = full | replay`：`full` 走现有 obs npy；`replay` 让 rollout sampler 直接产出 replay + 非 obs 标签，**sampler→trainer 与 trainer→PPO worker 两段传输都变小**，worker 用 replay 重建 obs/scalars/extra。同时把 PPO 装载改成 BC 同款 **lazy mmap + 逐批上卡**，让每轮样本量由训练需求决定，而不是由传输体积决定。

预期（按 qw 评审修正后的保守口径）：7 关轮换的 wire 从 ~21 s/轮降到 ~5 s/轮（不是 ~1 s）；35×20 的 wire 从 ~430 s/轮降到 ~85 s/轮；**等样本墙钟收益约 15–25%/样本（待 P3 实测；lazy 吞吐尚未证明，见 §5.1）**，以及「能不能跑 35×20」从不可行变可行。代价是 worker 端多一次「replay → npy」的 CPU 转换（2 vCPU 约 1–2 min/35×20 轮，含写盘/重哈希需 P0 实测），以及一次 loader 重构。**不是 20× 加速**；35×20 的收益主要是解锁样本量与覆盖度，不是吞吐倍数。

---

## 1. 已测事实（评审依据，不是推断）

### 1.1 当前传输账（g5-safe iter231）

| 项 | 数值 |
|---|---:|
| 训练样本 | 24,576（trim 后） |
| 采集 tick（含超采） | 431,132 |
| payload | 4,316,596 B |
| pack（含 reader 自检） | 12.224 s |
| download（3.7 Mbps） | 9.188 s |
| unpack | 1.020 s |
| PPO cloud | 40.4 s |
| rollout（本机 8 核） | 27.0 s |

> payload 是 `_trim_shards_to_quota` 之后的数据（`trainer/loop_remote_job.py:331-362`），不是 43k 超采全集。

### 1.2 replay 体积

- 用户提供的 `replays/replays.zip`：**272,774 B / 681,120 tick = 0.40 B/tick**。
- 24,576 样本 ≈ 245,760 tick ⇒ replay ~98 KB；加非 obs 标签仍只有 ~0.25–0.4 MB。
- 35×5（122,880 = 120×1024 样本）⇒ trainer→worker ~0.5 MB replay + ~1.5–2.5 MB 标签 ≈ **2–3 MB**；sampler→trainer 还要带 metrics，约 8–9 MB。
- 35×20（491,520 = 480×1024 样本）⇒ trainer→worker ~2 MB replay + ~6–7 MB 标签 ≈ **8–9 MB**；sampler→trainer 含 metrics 约 30–35 MB。这是按 metrics 1.31 MB/24.5k 实测外推的保守口径（qw 评审）。
- 配置口径：`target_transitions` 必须取 `mb` 整数倍，本 plan 用 **122,880 / 245,760 / 491,520**，避免 ragged 末块。

### 1.3 replay 重建逐字节一致（本机验证）

| 用例 | tick / 决策 | obs | scalars | extra | mask | 终局 tickHash |
|---|---:|---|---|---|---|---|
| 真实关 stage 30 | 2,742 / 275 | 逐字节相同 | 相同 | 相同 | 相同 | 相同 |
| 自定义关 s2030（g5 关卡） | 11,163 / 1,117 | 逐字节相同 | 相同 | 相同 | 相同 | 相同 |

路径：`InputRecorder`（录制）→ replay JSON → `ReplayInput` + `restoreWorld`（重建）。自定义关不依赖 `STAGES` 表；快照自带地形/出生点/RNG/基地 HP。
注意：**同平台已验证；Windows→Linux 2 vCPU 尚未验证**，进 P0/P2 必须真机复测。

### 1.4 转换耗时（Ryzen 7 5800H，Bun 1.4.4）

| 段 | 单核成本 | 24,576 决策 / 245,760 tick |
|---|---:|---:|
| 仿真 tick | 10.0 µs/tick | 2.5 s |
| ObsEncoder（obs+scalars+extra） | 41.7 µs/决策 | 1.0 s |
| 解析 replay + 恢复 + 写 npy | — | 0.3–0.5 s |
| **合计（只重建三张张量）** | **~14.5 µs/tick** | **~3.6–4.0 s** |
| TS 策略前向（若重算 lp/value） | 1.58 ms/决策 | **38.8 s 单核 / 19.4 s 双核** |
| 遥测 + metrics（若重算 reward） | ~100 µs/tick（上界） | **~25 s 单核** |

结论：**worker 只做仿真 + 编码；不重算前向、不重算 metrics。** 上表是分段归因；四轮编码/哈希/落盘优化后（2026-11-16），本机整段 rebuild 重新实测为 verify-only **5.4 µs/tick**、含写盘 **7.5 µs/tick**（700 shard / 5,035,728 tick / 503,963 样本 / 5.9 GB，Ryzen 7 5800H / Bun 1.4.4）：

| 形态 · 700 shard 全量 | 墙钟 | 换算 | 结果 |
|---|---:|---|---|
| 单进程顺序 · write（基线） | 38 s | 7.5 µs/tick · 74.8 µs/样本 | 700/700 |
| 单进程顺序 · `--verify-only` | ~27 s | 5.4 µs/tick | 700/700 |
| **池化 7 worker · `--verify-only`** | **8.5–8.8 s** | **1.7 µs/tick · 12.2 ms/shard** | **700/700**，worker 利用率 99.9% |
| **池化 3 worker · write** | **15.5 s**（best-of-2；另一跑 31.0 s） | 3.1 µs/tick · 22 ms/shard | **700/700** |
| **池化 7 worker · write** | **10.7–12.8 s**（2026-11-17 修复后 5 轮；另有一轮 115.8 s 为盘噪声） | 2.2 µs/tick | **700/700**（5 轮 3500/3500） |
| 一次性静态 7×100 · write | 11.0 s | 2.2 µs/tick | 700/700（无 work-stealing） |

- 池化 = 常驻 `--serve` worker（生产协议 `serve-loop.ts`：`__SERVE_READY__` / 每行 `["conv","--shard-dir",dir]` / `__SERVE_OK__`），按 `replay_meta.ticks` 降序 LPT 派发、worker 一空即取下一个；驱动 `tmp/g5-conv-pool.ts`（临时脚本，未入库）。
- **写盘池宽度 ≤3 已解除（2026-11-17）**：此前的 1/700 `tickHash` 偏离**不是** JIT/写盘/哈希实现问题，而是 `restoreWorld` 装入跨进程快照后没有把本地 `genId()` 推进到快照 id 之上 ⇒ 新实体重发仍活着的 id，`tickHash` 首现重映射撞号。修复（`reserveSnapshotIds`）后 7-worker 写盘池 **700 shard × 5 轮 = 3500/3500**，每轮 `samples=503963 ticks=5035728` 逐项相等；7-worker verify-only 700/700。根因/证据/决策 → `plan/tickhash-flake.plan.md` §1.5 + `DECISIONS.md §2026-11-17-sim-tickhash-id-space`。写盘宽度可回到全核，写盘口径仍取 best-of-N。
- 写盘吞吐波动大（同一份 5.9 GB：11 s / 15.5 s / 31 s ≈ 190–536 MB/s）是盘回收/杀软噪声，写盘口径一律 best-of-N。
- 语料常数：35 关 × 20 seed 平均 **7,194 tick/局、720 样本/局**（decision-k 10）；早期 20× 语料实测 3.45M tick（**494 样本/局**，不是 702）。
- **worker 侧无重哈希**（obsHash 在转换循环里增量算，见 §8 P0 进展与 §9 转换预算），相对 PPO 13.5 min 可忽略。

> **云机口径（2026-11-16，Kaggle 2 vCPU / 4 逻辑核，350 shard 折 700；优化前代码，保留仅作跨机对照）**：纯转换 **88 s/核**（254 µs/样本，无进程开销）· **池化 = 43 s**（实测 350 shard：`w=2` 22.9 s · 自动宽度 `workers=4` **21.4 s**）· 逐 shard 冷进程 **bun = 181 s**（90.6 s / 350）· 逐 shard 冷进程 **node = 350 s**。本机同代码的单核顺序口径已降到 5.4 / 7.5 µs/tick，**云机需按当前代码复测**（§8 P0 的 21.4 µs/tick 同理）。

> 2026-11-16 云机实测把本表的三行都校准了（350 局 / 1,727,568 tick）：整段 rebuild **21.4 µs/tick**
> （本表 14.5 µs/tick 是**只重建三张张量**的口径，实测含 `sim.tick()` + `parseReplay` + 事件消费），
> 换算到每样本 254 µs（含每 shard 固定成本）。前向与 metrics 两行的「若重算」判定不变且更明确：
> 一旦重算 lp/value 就贵 30× 以上，**必须由 sampler 落盘后原样带回来**。

### 1.5 BC 已解决大语料内存（复用，不重造）

- `worker/data/npyio.py:198-221`：`CorpusMemoryPlan.lazy_peak_bytes = other_bytes + per_sample × inflight × 2 + 768MB`；不是 2× obs。
- `worker/data/npyio.py:224-286`：装载前内存预检（`MemoryError` 带处置建议）。
- `worker/data/dataset.py:65-87`：`_field` 在 dtype 匹配时保留 `np.load(mmap_mode="r")` 视图；`_writable` 只为交 collate 的那一帧复制。
- `worker/data/dataset.py:209-217`：训练默认 `lazy=True`，注释明确「9.8 GB 语料在 `np.concatenate` 处会吃 ~2× 内存」。
- `worker/train/bc.py:706-707`：每 batch 才 `[b.to(dev) for b in batch]`，小 VRAM 可跑。

### 1.6 当前 PPO 装载的峰值来自两处（必须改）

- `worker/ppo/np_core.py:484-527`：`chunk_episodes` 先 `np.concatenate` 全部 episodes，再 fancy-index 复制成 chunks ⇒ 峰值约 3× obs。491k 样本 = **3.97 GB** obs（实测 494 样本/局，不是按 702 算的 5.65 GB）⇒ **8–12 GB 峰值**。
- `worker/ppo/trainer.py:20-41` + `worker/ppo/engine.py:347`：`tensored_chunks` 一次性把全部 chunk 搬到 device ⇒ 20× obs **3.97 GB** 常驻 VRAM；小 VRAM 不可用。

### 1.7 大 batch 的 PPO 时间（线性外推）

当前 `24 chunks × 4 epochs = 96 步`，`ppo_cloud_sec 40.4 s` ⇒ 0.421 s/步。

| 样本量 | chunks | 步数（4 epoch） | PPO 单轮 |
|---|---:|---:|---:|
| 24.5k（1×） | 24 | 96 | 40.4 s |
| 123k（5×） | 120 | 480 | **~202 s** |
| 245k（10×） | 240 | 960 | ~404 s |
| 491k（20×） | 480 | 1,920 | **~808 s（约 13.5 min）** |

**1×20 与 20×1 的总 PPO GPU 时间相同**（按样本计，**不含 lazy loader 的额外开销**；后者待 P3 实测）；大 batch 省的是 wire/pack 和迭代开销，不是 20× 加速。

### 1.8 qw 评审处置（v5）

> 逐条核实后的结论。**接受**=评审正确且已改 plan；**修正**=方向对但数字/措辞改；**驳回**=核实后不成立。

| 评审点 | 判定 | 处置 |
|---|---|---|
| §3.1 三臂 eval 与 `eval_every=5` 矛盾（B/C 0 个 eval 点） | **接受** | 实验臂单独 `eval_every=1` + `eval_at` 样本锚点；A 5/10/15/20、B 1/2/3/4、C 终点单点 + CI；生产 eval 口径不动；墙钟单列 eval。详见 §6.3/§6.4。 |
| §3.2 等样本三臂的梯度步数相同，「大 batch 低方差/更新少 20×」不成立 | **接受** | D8 重写：真正变量是数据刷新频率/on-policy 滞后、adv 池大小、schedule 颗粒度；KL 判据改 per-step/每等效 it；lr 2–3e-4 拆独立 1× 腿。详见 D8/§6.2。 |
| §3.2 「20× 每 it KL 进 0.0015–0.0025」是错误目标 | **接受** | 该带是 1×（96 步/it）口径；20× 每 it 1920 步同 lr 自然 ~0.012。判据改「每 24.5k 样本累计 KL」或 per-step KL；per-it KL 只作读数。 |
| §3.3 体积估算偏乐观 1.5–3× | **接受** | 用 metrics f4 1.31 MB/24.5k 外推，更新 §3.2 表与 P2/DoD gate；P0 增加「现有 4.3 MB payload 逐成员压缩后字节」分解。 |
| §3.3 「replay.json 内部已 xz，双重压缩」 | **驳回** | `src/replay/pack.ts` 是位打包，`src/replay/file.ts` 是 JSON+base64，**没有压缩**；0.4 B/tick 来自外部 zip 实测。payload.tar.xz 只压一次，无需改 frames 存储。 |
| §4.1 `Math.hypot` 跨运行时风险 | **接受** | P0 identity 扩到 ≥1000 局 + Bun→Node / Win→Linux 交叉；`Math.hypot` 在 `SimulationEnemies.ts:526,599` / `SimulationPlayer.ts:416`；若分叉，改平方距离属玩法语义改动，走新纪元三件套，**不在本 plan 顺手改**。 |
| §4.2 bundle 构建/分发渠道未定；ts_code 按 job 不是全局 CAS | **接受** | 改为 trainer 侧预构建 bundle，按 `(ts_code_sha256, converter_version)` 缓存，随 kind=ppo job 附件下发；worker 只跑不建；hub 需允许 ppo 携带 ts_code/bundle 附件。身份键用 `ts_code_sha256`，不用 codeHash。 |
| §4.3 obsHash 规格/编码时点未写死 | **接受** | 写死：`sha256(concat(obs_i, i∈decisionTicks))`，u1 NCHW 展平；编码时点 = 决策 tick 的 **pre-tick** 状态；obsHash 由转换器在转换循环里**增量算**（含在 `convertMs`），**worker 侧无落盘后重哈希**（2026-11-16 云机核实：全仓无此路径）。 |
| §4.4 D9 缺 it 内 schedule 机制 | **接受** | v7 进一步改为**首轮三臂冻结调度**（k3 B3）；`it_equiv = cum_samples / 24576` 只作未来生产功能（需 worker 内逐 minibatch/epoch 求值 + 真 PPO 集成测试）。详见 D9。 |
| §4.5 幂等键归一、旧 worker 文案、兜底优先级 | **接受** | full 时键里省略 `corpus_fmt`（逐字节旧键），replay 时追加；hub 按 worker capability 门控 replay 单；`payload_slim` 的 replay DROP 是**新增**，且请求 full 时一律不 DROP。 |
| 旧 worker 收 replay 包会「当 0 样本训练」 | **修正** | 现有 `discover_shards` 已按 obs 存在性过滤 ⇒ 0 shards ⇒ `SystemExit` 响亮失败，不是静默错训；问题是文案误导 + 浪费派发。改为 hub capability 门控 + 清晰拒收文案。 |
| `mb=1024` 单批 obs u1 47 MB | **修正** | u1 = 1024×17×26×26 ≈ **11.8 MB**；f32 才是 ~47 MB。改文，不影响「产线 ≥16 GB 显存充足」的结论。 |
| P2 No-Go「转换失败率 >0」过严 | **接受** | 改为「确定性 identity 失败 >0 或可重试失败率 >1%」。 |
| 转换预算未含云盘写盘 | **接受，已实测** | 35×20 的 **3.97 GB** obs 写盘；云机 350 局实测**转换池化 43 s**（含写盘，宽度 = `effective_cores()`；同机 `w=2` 22.9 s、`w=4` 21.4 s）⇒ ≤2 min 有余量。**重哈希不进预算**（见 §4.3 那行）。 |
| identity 测试未覆盖 hybrid opening / state_init | **接受** | 测试矩阵加这两条路径；P0 语料也要覆盖。 |
| 录制开销未量 | **接受** | P1 加 `recordFrame`/`worldTickHash` 开销读数（35×20 约 49k 次 hash）。 |
| lazy 对账缺四条充要条件 | **接受** | §5.2 补 channels_last、demo_bank RNG 顺序、adv 归一含丢尾、per-epoch 不重排、trim 在 GAE 前。 |
| 「21 s→1 s」墙钟表述膨胀 | **接受** | §0 改为 ~21 s→~5 s（7×5）、~430 s→~85 s（35×20），等样本墙钟收益 15–25%/样本；不是 20× 加速。 |
| 生产切换后盯守不足 | **接受** | P2/P3 切换后前 2 个五点窗同时盯 identity 失败率、`convert_sec`、per-step KL/clip。 |
| `loop_remote_job.py:73-86` 行号 | **修正** | 实际到 `:103`；改文。 |
| `plan_corpus_memory` 用 BC `SHARD_FILES` | **接受** | PPO 字段表换 `_RL_SHARD_SPEC`；`scan_shards` 在转换后仍按 obs 发现即可，转换前预估用 replay_meta/标签。 |

**评审要求重新拍板的两点已确认（§11.3）**：
1. 实验臂 eval 派发改 `eval_at` 样本锚点（生产 eval 口径不动）——否则 B/C 无 eval 点。
2. KL 判据改 per-step/每等效 it，三臂同 lr/epochs 起步，lr 提升拆独立 1× 腿——否则会把「更新预算不足」误判成「大 batch 不行」。

### 1.9 k3 评审处置（v7）

> 核实后的结论。**接受**=评审正确且已改 plan；**修正**=方向对但事实/措辞改；**驳回**=核实后不成立。

| 评审点 | 判定 | 处置 |
|---|---|---|
| B1 P2 同时切 replay+lazy、顺序矛盾 | **修正/部分驳回** | v6 的 P2 只切 `replay`（lazy 在 P3 才实现），事实描述不成立；但「一次只切一个开关」正确。v7 写死：P2 = replay+eager；P3 = replay+lazy；回滚点分别独立。 |
| B2 每局写 replay.json 撞零落盘契约 | **接受** | exporter 同时产出 replay 字节；dispatch 腿把 replay 作为内存成员交给 `ShardSegments.add`，**不得创建 shard 目录**；磁盘/内存同一序列化实现；`ShardSegments.begin/take` 身份加 `corpus_fmt`；补零目录/内存段==磁盘段/热切换拒复用测试。 |
| B3 `it_equiv` 只有公式，无 it 内执行机制 | **接受** | 首轮 A/B/C **冻结调度**：关 kickstart、关分段 `ppo_schedule`，固定 lr/epochs/mb/ent/kl/value_anchor；`it_equiv` 仅作为未来生产调度功能（需 worker 内逐 minibatch/epoch 求值 + 真 PPO 集成测试），不作为首轮方案。 |
| B4 lazy 缺全局行映射 + 吞吐未测 | **接受** | D6 增 `row_map: global_row → (shard_id, local_row)`；shard 顺序/行序/permutation/quota 截断四点与 eager 逐字节一致；P3 加同机端到端 benchmark（冷/热 page cache、scan+GAE、gather、转换、H2D、update、总墙钟）+ 吞吐退化阈值（实测后填）。 |
| B5 单训练 seed 撑不起非劣配方结论 | **接受** | P4/P5 标为 pilot；主读 B 学习曲线 + C 终点；报告效应量/CI；A vs B 混入关卡覆盖差异，B vs C 才是数据刷新主对照；配方推广至少 3 个独立训练 seed；P3.5 是 lr+epochs 组合干预筛选，不叫单变量归因；KL 只保留一种主口径并写公式/聚合/目标带。 |
| B6 转换缺 shard 级事务/续跑 | **接受** | 转换到同级临时目录 → 校验 N/shape/dtype/obsHash/tickHash/outcome/非 obs 文件对齐 → 写完成清单（ts_code_sha256/converter_sha/runtime/文件 sha）→ 原子 rename 整个 shard 目录；重启只复用清单+哈希全匹配产物；两进程按 shard 分区；补三类故障注入。 |
| B7 capability 只闸 PPO worker 不够 | **接受** | 端到端协商：trainer 发布前确认 hub 支持；sampler 声明 `replay_corpus`，不支持不派 replay 采样；PPO worker 同名能力；结果 manifest 与请求 `corpus_fmt` 不一致即拒收；converter 契约版本进结果身份或要求新 runId。 |
| 4.1 墙钟账未覆盖两条腿/全部阶段 | **接受** | 统一记：sampler 编码/录制 → 回传压缩上传 → trainer 接收/段压缩 → pack → 下载 → unpack → replay 转换 → lazy 装载/update；eval 单列。实验主判排除 eval，生产收益看完整迭代墙钟。 |
| 4.2 identity 与可重试 I/O 未彻底分账 | **接受** | 确定性 identity 失败（obsHash/tickHash/shape/dtype/标签对齐/代码版本）**零容差**，不得用重采掩盖；网络/瞬时磁盘单独统计并定义分母/窗口/最小样本量；48h 不把已知不匹配转为可接受。 |
| 4.3 录制插入点/full 开销契约 | **接受** | 写死 `sim.tick() → recorder.recordFrame(scripted) → scripted.endFrame()`；full 模式不创建 recorder、不算 tickHash、不复制 snapshot；P1 测 full 路径墙钟/内存不退化。 |
| 4.4 固定样本量≠35×20 覆盖度 | **接受** | 每轮强制报告每关 games/unique seeds/trim 前后 samples/SHORT/丢弃整局数；硬闸「35 关均有有效样本」；同一 run 内不重复 `(stage,seed)`；其余覆盖目标用实测值描述。 |
| 4.5 replay 保留语义二选一 | **接受（按用户既有拍板）** | 用户已拍板复用当前 iteration 保留策略 ⇒ replay 是**临时训练物料**，不承诺长期可重建；DoD 不再写笼统「无数据丢失」。若要做可重建档案，replay+meta+converter+ts_code 必须同生命周期，另立 plan。 |
| 其他（B1 的「同时切」、P2 gate 等） | **修正** | P2 gate 只比较 replay+eager vs full+eager；lazy 的吞吐/数值 gate 留到 P3。 |

### 1.10 P0 opening-null-move 探针结果（2026-11-15 本地）

探针：`tmp/probe-opening-null-move.ts`，g5 自定义关 `classic35-safe-base`，seed 1，取 6 个 `openingWanted=true` 的关（s2001/2003/2004/2007/2012/2014），跑完 opening 直到 `done`。

| 变体 | opening ticks | null ticks | null 转向 | null 转向到 up | null 位移 | null moving=true |
|---|---:|---:|---:|---:|---:|---:|
| v6（hold=1 + move=up） | 1,974 | 30 | **4** | **4** | 0 | 0 |
| legacy（move=0=STOP） | 1,974 | 30 | **0** | 0 | 0 | 0 |

具体例子：
- s2003 t310：`left -> up`；s2004 t426：`left -> up`；s2007 t256：`left -> up`；s2012 t223：`left -> up`。
- 其余 null tick（如 t69–72）`right -> right`：转向被 `applyTurnCooldown` 按回，但移动仍为 0。
- 结论：v6 的 `move=null` **不是「原地不动」**，而是「请求转向 up + 不前进」；是否真的转，取决于转向冷却是否允许。legacy 的 `move=0` 才是 STOP。

**影响**：
- replay **不受影响**：frames 如实记录实际执行（含 `hold=1+move=up`），identity 仍一致。
- 训练/评估行为受影响：opening 的 `EMPTY_ACTION`/等待拍可能把坦克转到 up（cooldown 允许时），改变后续路径/炮口朝向。13 个 pocket 关都可能命中。
- **建议**：另立 opening 修复腿（`plan/opening-null-move.plan.md`），把 v6 的 `move=null` 映射成 `move=当前已提交朝向`（`p.prevMoveDir`/`world.player.dir`）+ `hold=1`，预期 0 转向 0 位移；A/B 对比后再决定是否入生产。**不在 replay plan 里顺手改玩法。**（已实施并采用，见下条。）
- **进展（2026-11-15）**：修复已实现并完成 it185 A/B——探针 4→0；700 局总体 81.86%→80.71%（−1.14pp），opening 13 关 −3.08pp（p=0.302），非 opening 440 局逐局相同。**用户已拍板直接采用修复、不保留开关**；采用后是新 baseline，跨采用前后的点不可横比。详见 `plan/opening-null-move.plan.md` §9。
- 备选：保持现状 + replay 记录（replay plan 可继续）；或定义 hold 忽略 move（用户已否决非正交语义，不建议）。

---

## 2. 设计决策

### D1. replay 必须带「非 obs 标签」，不能只发 replay

replay 只有输入流；PPO 需要 `lp_move/lp_fire/lp_hold/value/reward/done/mask/policy_mask`。让 worker 从 replay 重算这些，2 vCPU 上是净亏（前向 19–39 s + metrics 12–25 s）。因此：

- **sampler→trainer（replay 格式）发**：`replay.json` + `replay_meta.json` + 现有 shard 中除 `obs/scalars/extra` 外的全部 npy/JSON（actions/lp/value/done/mask/policy_mask + `metrics.npy` 或 `reward.npy`）+ manifest。
- **trainer→worker（replay 格式）发**：同上，但按现有 wire-diet 在 trainer 侧把 `metrics.npy` 算成 `reward.npy` 并丢掉 metrics。
- **不发**：`obs.npy`、`scalars.npy`、`extra.npy`（由 worker 从 replay 重建）。
- `scalars`/`extra` 由 `ObsEncoder.encode` 同一次产出，重建成本已包含在 41.7 µs/决策里。
- **动作记录完整性（NN move/fire/hold）**：
  - `InputRecorder` 逐 tick 记录 `direction`（v3 位域 0..4）、`firing`、`guard`、`frenzy`、`hold`、`fireBeforeTurn`；v6 的 `hold` 与 `fireBeforeTurn` 是 v3 位（`src/replay/pack.ts:9-33`）。
  - `ScriptedInput` 在 v6 下 `usesHoldFire3()=true`，`InputRecorder` 的 sticky `p1HoldFire3` 会强制写 v3 头；`hold`（turn-only）与 `fireBeforeTurn`（fire=1 先打后转）都不会被 v1 头丢掉。
  - **两套动作要分清**：shard 的 `a_move/a_fire/a_hold` 是**决策标签**（PPO 用）；replay frames 是**每 tick 实际执行**（世界重建用）。正常情况下两者一致；opening controller 接管时 `policy_mask=0`，`a_*` 仍是 NN 采样值但实际执行的是 controller 动作 ⇒ **converter 只能用 replay frames 驱动世界，绝不能用 `a_*` 标签驱动**。
  - L0/God dodge 覆盖会同步改写 `aMove/lpMove`（标签=执行），但 opening 覆盖不会改写 `a_*`；这是唯一的例外，必须靠 `policy_mask` 排除。

**口袋关 / OpeningController（确定性开路）的处理**：
- controller 只在开局 `openingWanted`（条件接管：strict corridor 不连通）或 `--opening-all` 时创建；它每 tick 产出 `{move, fire}`，经 `scripted.setAction` 变成**真实执行输入**，由 recorder 逐 tick 记录。
- **converter 不重新实例化 OpeningController、也不重跑 A***；replay frames 就是 controller 的输出，直接喂 `ReplayInput` 即可。这样跨平台/跨版本不会因 pathfinding/abort 规则漂移而分叉。
- opening 期间的决策样本 `policy_mask=0`，PPO 不算 policy/entropy loss；`a_move/a_fire/a_hold` 仍是 NN 采样标签，**不驱动世界**。converter 只复制 `policy_mask`，不拿 `a_*` 驱动。
- v6 下 controller 的 `move=null` 当前被导出器写成 `hold=1 + move=0(up)`（**不是当前朝向**；`MOVE4_DECODE[0]='up'`）。**P0 探针已实测**（6 关/1,974 opening tick/30 个 null tick）：v6 有 4 次 `left→up`/`right→up` 转向，legacy 0 次；位移均为 0。结论：v6 的 null-move 不是「原地不动」，而是「请求转向 up + 不前进」，是否真转取决于转向冷却。replay 如实记录实际执行，identity 不受影响；但若 controller 本意是原地不动，则属玩法行为 bug。处理：**另立 opening 修复腿**（映射成 `move=当前已提交朝向`+`hold=1`，A/B 后再入生产），**不在 replay plan 里顺手改**。详见 §1.10。
- `replay_meta.opening` 记录 `{connect, all, wanted, doneTick, abortReason, phase}` 供审计；converter 忽略它（frames 是唯一真源）。
- 如果未来 controller 开始用 active item（当前 AI 纪律不允许），recorder 已记录 `guard/frenzy` 位；converter 仍按 frames 执行。当前实现只产出 move/fire，guard/frenzy 恒 false。

### D2. 由请求显式指定 `corpus_fmt=full|replay`，两段传输都受益

**用户拍板：不是 packer 事后看 `replay.json` 猜格式，而是请求里就写明要 replay 还是 full。**

- 任务/rollout spec 新增 `corpus_fmt: "full" | "replay"`，缺省 `full`（旧任务逐字节不变）。
- sampler/agent 把 `corpus_fmt` 透传给导出器（`--corpus-fmt`）；`replay` 时导出器**只产** replay + 非 obs 标签，不产 `obs/scalars/extra`。
- 结果容器（BCV2）按同格式回传 trainer：`replay` 下 sampler→trainer 的体积从「obs 282 MB 原始」降到「replay + 标签 + metrics」，按 metrics f4 1.31 MB/24.5k 实测外推约 **1.7–1.9 MB/24.5k 样本**（35×20 约 30–35 MB；metrics 是压缩大头）。若把 reward 计算前移到 sampler 或对 metrics 做 delta 编码，可再降，但那是另一个决定（见 §11.2）。
- trainer 按同一个 `corpus_fmt` 打包给 PPO worker；`payload_slim` 的「见到 replay.json 就 DROP」是**新增逻辑（当前仓库不存在）**，且只在请求 `replay` 时启用；请求 `full` 时 `replay.json` 视为审计产物，一律不 DROP。
- 格式是传输选择，不是语料语义：`corpus_fmt` **不进 `corpus_fp`**；full 时幂等键里**省略**该分量（逐字节旧键），replay 时追加，避免升级瞬间旧任务与新 full 任务重复派单。
- `kind=iter`（云机自己 rollout + PPO）和 eval 任务**硬校验强制 `full`**：同机没有 wire 收益，转换只会白烧 CPU；请求 replay 直接拒。只有 `kind=ppo`（本机/节点 rollout → 远端 PPO）允许 `replay`。
- 旧 worker 收到 replay 包：现有 `discover_shards` 会因缺 `obs.npy` 得到 0 shards ⇒ `SystemExit`（`np_core.py:584-586`），**不会静默 0 样本训练**；问题是文案误导 + 浪费一次派发。新增 hub 侧 worker capability 门控（旧 worker 不派 replay 单）+ 清晰拒收文案。
- 回退：replay 缺失/损坏/校验失败 ⇒ 该 shard 不进入 replay 包；若请求是 `replay`，整个 job 失败（可重试采集），不静默降级成 full；若请求是 `full`，走旧路径。**绝不静默用坏 replay 训练。**
- **零落盘（k3 B2）**：`runOne` 同时产出 replay/replay_meta 的**字节**；local 腿可落盘，dispatch 腿把这两个成员作为内存 `files` 交给 `ShardSegments.add`，**不得创建 shard 目录**。磁盘与内存路径必须共用同一个序列化实现（同一份字节）。
- `ShardSegments.begin/take` 的段身份加 `corpus_fmt`（现只有 `(it, wire_diet)`）；full↔replay 热切换时旧段不得冒充新格式。
- **端到端能力协商（k3 B7）**：trainer 发布前确认 hub 支持 `corpus_fmt`；sampler/rollout 节点声明 `replay_corpus`，不支持则不派 replay 采样；PPO worker 同名能力；结果 manifest 与请求 `corpus_fmt` 不一致即拒收；converter 契约版本进结果身份，或明确要求新 runId。
- 零落盘测试补：replay 内存段 == 磁盘段、零目录、换轮隔离、`corpus_fmt` 热切换拒绝复用、full/replay 不混包。

### D3. 转换器是纯 JS 单文件 bundle，不碰模型/native

- 新增 `tools/sim/replay-to-shard.ts`：只 import `World/Simulation/ObsEncoder/ReplayInput/parseReplayFile/restoreWorld/npyBytes`。
- **不要** import `export-rl-rollout.ts`（它会拖入 `buildModelFromText`/`featuresEngine`，带来 native 依赖）。如需 `shardNpyEntries`，先抽到无模型依赖的模块。
- `bun build --target=node` 出单文件；worker 有 Node ≥22 就用 Node，否则用 Bun；无需 native 库。
- 一个进程处理整批 shard（避免每局 Bun 启动）；worker 按 vCPU 起 2 个进程。
- **输入侧必须用 `ReplayInput`**（不是 `ScriptedInput` + `a_*` 标签）：v3 的 `hold/fireBeforeTurn` 必须被 `Simulation` 读到；opening controller 的接管段只有 frames 是真实执行动作。`ReplayInput.usesHoldFire3()` 由 v3 头 flags 恢复，converter 不得自行推断。
- **不重新实例化 OpeningController、不重跑 A***；frames 是 controller 输出的唯一真源。`policy_mask` 原样复制，`a_*` 只作 PPO 标签。

### D4. replay_meta：把「对齐信息」和「校验」显式化

每个 shard 目录新增 `replay_meta.json`：

```jsonc
{
  "version": 1,
  "k": 10,
  "decisionEvents": false,
  "initTick": 0,
  "decisionTicks": [0, 10, 20, ...],   // u32，长度必须 == a_move.npy 行数
  "obsHash": "sha256:...",             // 全部决策 obs 的聚合哈希（节点侧算）
  "codeSha": "...",                    // rollout 侧 TS code sha
  "opening": {                         // 仅审计；converter 忽略（frames 是唯一真源）
    "connect": true, "all": false, "wanted": true,
    "doneTick": 123, "abortReason": null, "phase": "done"
  },
  "nSamples": 1234,
  "ticks": 12345
}
```

- 转换器**按 `decisionTicks` 编码**，不重算 decision gate；避免 gate 逻辑变化导致对齐漂移。
- **obsHash 规格写死**：`sha256( concat(obs_i for i in decisionTicks) )`，每个 `obs_i` 是 u1、NCHW（17×26×26）行优先展平的 11,492 B；节点与转换器同一算式。
- **编码时点写死**：决策 tick 的 **pre-tick 世界状态**（先 `encoder.encode(world)`，再喂该 tick 输入，再 `sim.tick()`）；与 `export-rl-rollout.ts:1569-1574` 一致。off-by-one 会被 obsHash 抓住，但规格必须先写对。
- 校验：replay tickHash 链、`decisionTicks.length == a_move.shape[0]`、obsHash、最终 `world.state` 与 manifest `outcome` 的映射。任一不符 = 确定性失败（响亮，不重试）。
- 节点侧 hash 决策 obs（24.5k × 11,492 B ≈ 282 MB，~0.3 s @255 MB/s）；**worker 侧无落盘后重哈希** —— obs 的 identity 由 `replay_meta.obsHash`（转换循环内增量算）承担，传输完整性由压缩包级 `payload_sha256` 承担（2026-11-16 核实）。
- **shard 级事务（k3 B6）**：转换到同级临时目录 → 校验 N/shape/dtype/obsHash/tickHash/outcome/所有非 obs 文件对齐 → 写完成清单（`ts_code_sha256`/`replay_converter_sha`/runtime/文件 sha）→ **原子 rename 整个 shard 目录**。重启只复用「完成清单 + 哈希全部匹配」的产物，否则响亮重建；两个转换进程按 shard 分区，不共享写目录。
- 故障注入测试：任一成员写完即崩溃、重试复用完整产物、重试拒绝不完整/旧 converter 产物。

### D5. worker pre-pass + ts_code/bundle 分发（qw 评审修正）

- `remote/worker.py` 在 `kind=ppo` 解包后、D14/`load_episodes` 前插入 pre-pass；只有请求/包声明 `corpus_fmt=replay` 才启用。
- **bundle 由 trainer 侧预构建**（trainer 有 bun、有同源 TS 树、零第三方依赖）：按 `(ts_code_sha256, converter_version)` 内容寻址缓存，随 kind=ppo job 附件下发；worker **只跑不建**。这样 2 vCPU 不付 `bun build` 的 5–10 s，Node 回退也能跑预建 bundle。
- `_ensure_ts_code` 现按 job 取件（`/jobs/{jid}/ts_code`），不是全局 CAS；kind=ppo 要携带 `ts_code_sha256` + bundle 附件，hub/manifest 需允许（`MANIFEST_ITER_EXTRA` 之外新增 ppo 附件字段）。
- **身份键用 `ts_code_sha256`（zip 字节 sha），不用 codeHash**：codeHash 只覆盖 SSOT 文件集，`src/replay`/`src/snapshot`/转换器自身不在其中时可能同 codeHash 不同树；运行时双哈希闸兜底。
- 取不到同 sha 的 TS/bundle ⇒ 响亮失败；trainer 重启换工作区后旧 sha 不可得时，保留归档或让该轮重采。转换器 bundle 的 sha 进 manifest（`replay_converter_sha`），并钉住构建环境（bun 版本）。

### D6. PPO 装载改成 BC 同款 lazy mmap + 逐批上卡

- 新增 PPO 专用 lazy 装载（`worker/ppo/lazy_corpus.py` 或 `np_core` 内新分支）：
  1. `scan_shards` → 逐 shard `read_npy(mmap=True)`；
  2. 逐 shard 算 GAE（保留现有 `load_episodes_common` 的数学），`adv/ret` 全量只有几 MB；
  3. 全局 `np.random.permutation`（保持现有可复现口径）；
  4. 每个 mb 用 fancy indexing 取行（只复制 mb 行），`_writable` 处理只读 mmap；
  5. `torch.from_numpy(...).to(device)` **逐批**，不再 `tensored_chunks` 一次性全搬。
- 旧路径保留为 `--ppo-loader=eager`（回滚开关）；测试绿后默认切 `lazy`（用户拍板，含 7×5 生产课）。
- 内存预检直接复用 `plan_corpus_memory`/`check_corpus_memory`；日志加 `loader=lazy peak=...MB`。
- VRAM 预算：mb=1024 时单批 obs **u1 ≈ 11.8 MB**（f32 才是 ~47 MB）；f32 激活由模型决定。
  **2026-11-16 修正（用户口径）**：生产平台（Kaggle / Colab / Baidu AIStudio）显存均 **≥16 GB**，
  35×20 最坏情况 eager 全量语料上卡 ≈ **4.1 GB**（obs 3.97 + scalars 64 MB + extra 79 MB）+ 模型/激活
  ~1.4 GB ⇒ 约 5.5 GB，16 GB 卡有 10 GB 余量。**原来的「4–6 GB 可跑」是 BC 小卡时代的历史目标，
  对产线平凡满足，从门中移除**。替代方案 = **上卡前查剩余显存，不足就响亮拒绝或自动切 lazy**
  （当前代码无此探测：`lazy_corpus.peak_mb` 是 CPU 物化估计，不是 GPU 余量）。OOM 时 `--mb 512` 回退仍在。

### D7. **固定样本量**（用户拍板）；fixed games 不做

- 现状 = 固定样本量（`per_stage_quota = ceil(target/关数)`，多余整局裁掉）。**用户拍板继续用固定样本量，不改 fixed games。**
- 代价必须写清楚：局长随 seed 方差大 ⇒ 35×20 下每关不一定凑满 20 seeds。collector 必须记录每关实际 `games` / `samples` / `seed 列表` / `shortfall`，训练账本按实际值记，不补造、不承诺「20 seeds」。
- 「每关 20 seeds」只能作为**观测目标**，不是本 plan 的验收项；若未来确实要它，另立 plan 切 fixed games（本 plan 不做）。
- 三个 scaling 档的 `target_transitions` 固定为 `mb` 整数倍：122,880（5×）/ 245,760（10×）/ 491,520（20×）；`chunk_episodes` 仍按 `n % mb` 丢尾。
- `est_samples_per_game` 只做 volume 分配，**不是保证**；日志/文档不得用它承诺 seed 数。
- **覆盖度报告（k3 4.4）**：每轮强制报告每关 `games` / `unique seeds` / trim 前后 samples / `SHORT` / 丢弃整局数；硬闸「35 关均有有效样本」；同一训练 run 内不重复使用 `(stage, seed)`；其余覆盖目标用实测值描述，不凭目标名称宣称达成。

### D8. 训练侧按「等总样本」三臂实验；变量是数据刷新频率，不是 minibatch 大小（qw 评审修正）

| 臂 | 配置 | 总样本（约） | chunks/it | epochs | 步/it | it 数 | **总步数** | 每样本复用 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A | 7×5，20 个 it | 490k | 24 | 4 | 96 | 20 | **1920** | 4 |
| B | 35×5，4 个 it | 490k | 120 | 4 | 480 | 4 | **1920** | 4 |
| C | 35×20，1 个 it | 490k | 480 | 4 | 1920 | 1 | **1920** | 4 |

**等总样本 + 等 mb + 等 epochs ⇒ 三臂梯度步数相同、每步梯度噪声同分布、每样本复用次数相同。** 所以「大 batch 低方差 / 更新次数少 20 倍」的说法不成立（minibatch 没变大，变大的只是池）。真正要检验的差异只有三个：

1. **数据刷新频率 / on-policy 滞后**：A 每 96 步换新池；C 在同一个冻结池上走 1920 步。后期步的 ratio 漂移、clip fraction 上升、有效学习饱和——这才是「1×20 vs 20×1」的核心假设。
2. **adv 归一化池大小**（24.5k vs 491k，二阶效应）。
3. **per-it schedule/熔断的颗粒度**（D9）。

**KL 判据（qw 评审修正，替换 v4 的 D8.1）**：
- 主口径 = **KL_cum(24.5k 样本)**，目标带 **0.0015–0.0025**；公式/聚合见 §6.2。`per-step KL` 只作读数，`per-it KL` 不作判据。
- v4 的「20× 每 it KL 进 0.0015–0.0025」是错误目标：该带是 1×（96 步/it）口径；C 每 it 1920 步同 lr 自然 ~0.012。硬压到 0.002 等于让 C 的每步学习量比 A 小 20 倍，会把「更新预算不足」误判成「大 batch 不行」；推到 0.03–0.05 又会撞上停腿线。
- **三臂起步同 lr/epochs/mb**（现值 1.2e-4 / 4 / 1024）；lr 2–3e-4 / epochs 4–8 拆成 **独立的 1× 实验腿**（新 `--out`、不动在跑的生产课，AGENTS §15.5），确认 1× 正向后再平移到三臂。
- 0.0006 的 KL 也可能是 clip/熵/价值头瓶颈，不先验认定「更新量不足」；先看 g5 两腿的 clip fraction 与 value loss。
- per-tick **没有 target_kl 早停**（`worker/ppo/config.py:39`）；C 的 1920 步没有 it 内护栏 ⇒ 加「逐 epoch KL/clip/ratio 分位数读数 + 可选 it 内中止阈值」，否则熔断只能在 491k 样本之后才响。
- 若 C 在等步数下确实劣化，正确杠杆是**更频繁刷新（缩小池）或降 epochs**，不是加 lr；把这条写进决策树。

### D9. 按样本计的 schedule + 实验臂 eval 派发（qw 评审修正）

**k3 B3 修正：首轮 A/B/C 冻结调度，不用 `it_equiv` 公式冒充 it 内调度。**

- 首轮 A/B/C **关闭 kickstart 和分段 `ppo_schedule`**，固定 lr/epochs/mb/ent/kl/value_anchor。这样才真正隔离「数据刷新频率」这一个变量；结论只代表该固定配置。
- 现有调度在轮边界求值一次（`trainer/loop_steps.py:340-368` → `resolve_ppo_schedule(..., it)`，随后固定本轮值；`worker/schedule.py::kickstart_coef` 按整数 it），C 的唯一 PPO job 会在开始时取一次值后整 1920 步吃同一段配置。**只把 `it` 改名成 `it_equiv` 不会改变生产语义**，纯函数单测会全绿。
- `it_equiv = cum_samples / 24576` 只保留为**未来生产调度功能**：必须在 worker 内按已消费样本逐 minibatch/逐 epoch 求值，并同步改 kickstart、熔断和账本；它是独立功能，不是换算公式。首轮不做。
- 若坚持首轮复现生产调度，则必须先实现真正的 it 内样本轴调度，并通过**真实 PPO 集成测试**（C 不能整 it 吃 it1 段），不能用 schedule 纯函数测试代替。
- `value_anchor`/熔断/`max_hours`/`iters` 仍按实验预算显式设定；不要沿用 20× 下会被拉长 20 倍的 per-it 默认。

**eval（生产口径不变，实验臂派发改）**：
- 生产课继续 `eval_every=5 it`，eval 网格/seed/难度不变。
- 实验臂单独 `eval_every=1` + `eval_at`：A 5/10/15/20；B 1/2/3/4（每 it = 123k 样本，与 A 的样本锚点对齐）；C 只有 1 it ⇒ 终点单点 + 跨 seed 置信区间，**放弃 C 的五点窗**。主判 = B 的学习曲线配对 + C 的终点配对差。
- 墙钟读数单列 eval 时间；主判用「训练墙钟（不含 eval）」，避免 C 因不付 eval 时间而占便宜。
- 三臂 it 节奏天然不同（20/4/1），**删掉「同 it 节奏比较」**；能对齐的只有样本轴。

### D10. 可观测与数据保留

- **统一墙钟账（k3 4.1）**：`sampler 编码/录制 → sampler 回传压缩与上传 → trainer 接收/段压缩 → pack → trainer→worker 下载 → unpack → replay 转换 → lazy 装载/update`；eval 单列。实验主比较排除 eval，生产收益看完整迭代墙钟（不能只看 wire）。
- iteration 的 `wire` 子字典新增：`replay_bytes` · `labels_bytes` · `convert_sec` · `convert_jobs` · `convert_fallback` · `corpus_fmt`。
- PPO 日志新增：`loader=lazy|eager` · `loader_peak_mb` · `ppo_vram_mb` · `chunks` · `steps` · `per_step_kl` · `clip_fraction`；eval 时间单列。
- replay/npy/ts_code **复用当前 iteration 保留策略**（用户拍板），不新增保留规则；npy 按现有 prune 清理。
- **保留语义（k3 4.5）**：按用户拍板，replay 是**临时训练物料**，随 job 回收，**不承诺长期可重建**；DoD 不写笼统「无数据丢失」。若未来要做可重建档案，replay+meta+converter+对应 ts_code/bundle 必须同生命周期保留，另立 plan。
- 控制台课程行可选显示 `corpus_fmt` / `convert_sec`（additive，不改旧字段）。

---

## 3. 线格式与体积

### 3.1 两段容器结构（请求 `corpus_fmt=replay`）

请求 `corpus_fmt=replay` 时，两段容器分别是：

```text
# ① sampler → trainer（BCV2 结果容器）
rl_s{stage}_seed{seed}/
  replay.json             # InputRecorder 产物，~0.4 B/tick（xz 后）
  replay_meta.json        # decisionTicks/obsHash/codeSha/nSamples
  a_move.npy a_fire.npy a_hold.npy
  lp_move.npy lp_fire.npy lp_hold.npy
  value.npy done.npy mask.npy policy_mask.npy
  metrics.npy             # TS 侧算好；trainer 侧 wire-diet 再折成 reward
  manifest.json           # corpus_fmt="replay"

# ② trainer → PPO worker（payload.tar.xz）
rl_s{stage}_seed{seed}/
  replay.json replay_meta.json
  a_move.npy a_fire.npy a_hold.npy
  lp_move.npy lp_fire.npy lp_hold.npy
  value.npy reward.npy reward_meta.json
  done.npy mask.npy policy_mask.npy
  manifest.json           # corpus_fmt="replay"
init_weights.json（按需）
```

- `full` 格式保持现状：包含 `obs.npy/scalars.npy/extra.npy/metrics.npy`，由 worker 直接训练。
- replay 格式的 sampler→trainer 结果容器也**不含** obs/scalars/extra，避免「先传 obs 再由 trainer 压缩」——这是用户要求的第二段减量。

### 3.2 体积对比

**2026-11-16 云机 P0 实测后重写**（Kaggle 2 vCPU，`g4-mobility.it380`，hard，maxTicks=12000，350 局 / 172,938 样本）：
逐成员原始字节 **obs 11,492.3 B/样本（= 17×26×26，零冗余）· metrics 361.0 · extra 228.3 · scalars 184.3 ·
replay.json 31.8 · replay_meta 11.1 · 其余标签 24.2 · manifest 0.1**；非 obs 三张合计 **436 B/样本**。
xz preset 3 实测压缩比 **13.2×**（10 shard / 4,740 样本，同一份字节在本机与云机逐字一致）。

| 规模 | full（trainer→worker，**本次未复测**） | **replay sampler→trainer（含 metrics）** | **replay trainer→worker（含 reward）** | pack+下载（3.7 Mbps，s2t） |
|---|---:|---:|---:|---:|
| 24,576 样本（≈50 局） | ~4.3 MB（旧基线） | **0.81 MB** | **0.35 MB** | ~1.8 s |
| 122,880 样本（≈250 局） | ~21.5 MB（旧基线） | **4.1 MB** | **1.7 MB** | ~8.9 s |
| 491,520 样本（≈700 局） | ~86 MB（旧基线） | **16.2 MB** | **6.9 MB** | ~35 s |

> 两条 replay gate（§9）**双绿**：35×20 的 16.2 MB / 6.9 MB 对 45 MB / 12 MB ⇒ 余量 **2.8× / 1.7×**。
> ⚠ **规模映射变了**：实测平均 **494 样本/局**（4941 tick），不是此前假设的 702 ⇒ 491,520 样本 = **700 局**
> （原按 702 记成 35×20 = 700 局恰好同数，但 7×5=35 局只有 17.3k 样本，不是 24.5k）。
> 收益来源单一且确定：full 的 obs 三张在 491,520 样本下是 **4.1 GB 原始 / 282 MB 每 24.5k**，replay 把它整段删掉。

### 3.3 血缘与幂等

- `data_fp` 只取 shard 的 `(name, wver, stage, seed)`，**不取 npy 字节** ⇒ replay 重建不改变 `data_fp`。
- `payload_sha256` 会变（正常）；job manifest、sampler 结果 manifest 都增 `corpus_fmt`；trainer→worker job manifest 另增 `ts_code_sha256` / `replay_converter_sha`。
- `corpus_fmt` 进 idempotency key（同一 `data_fp` 的 full/replay 两种包不能当同一单）；**不进 `corpus_fp`**（传输选择不是语料语义）。
- 旧 hub/旧 worker：`corpus_fmt` 缺席 ⇒ 旧 npy 路径；新 payload 到旧 worker 必须**明确拒收**（不能把缺 obs 的 shard 当 0 样本训练）。在 manifest 校验里加一条。

---

## 4. 转换器规格

### 4.1 CLI

```text
bun <bundle>.js --shard-dir <dir> [--jobs N] [--verify-only]
```

- 读 `replay.json` + `replay_meta.json` + manifest；
- 重建 `obs.npy`/`scalars.npy`/`extra.npy`（u1/f4/f4，形状同 `OBS_CHANNELS/BOARD/SCALAR_DIM/POLICY_EXTRA_DIM`）；
- 校验 tickHash / decisionTicks / obsHash / nSamples；
- 原子写（`.tmp` + rename），失败不留半文件。

### 4.2 明确不做

- 不做模型前向（不 import `infer`/`conv`）；
- 不算 reward/metrics：`metrics.npy` 由 sampler 的 TS 侧算好，`reward.npy` 由 trainer 的 wire-diet 在打包时折出；converter 只重建 obs/scalars/extra。
- 不重算 mask/policy_mask（随标签传；若后续为省字节改成重算，必须单独评审 `restrictAdvanceMask` 的 v6 耦合）。
- **不重跑 OpeningController / A***，也不拿 `a_move/a_fire/a_hold` 驱动世界；世界只由 replay frames 驱动。opening 段 `policy_mask=0`、`a_*` 是 NN 标签，二者不等价。

### 4.3 性能预算

| 目标 | 预算 |
|---|---:|
| 7×5（24.5k 样本） | ≤ 5 s（2 vCPU，2 进程） |
| 35×5（123k） | ≤ 30 s |
| 35×20（491k） | ≤ 2 min |
| 单进程内存 | ≤ 1 个 shard 的 obs（≤ 41 MB）+ replay |

---

## 5. PPO lazy loader 规格

### 5.1 数据流

```text
scan_shards → mmap 每 shard
  → 逐 shard GAE（保留 load_episodes_common 数学）
  → 全局 adv/ret normalize（现有口径）
  → 全局 permutation（seed 固定）
  → 每 mb fancy-index 取行 → _writable → torch.from_numpy → .to(device)
```

**全局行映射（k3 B4）**：必须显式建 `row_map: global_row → (shard_id, local_row)`，并满足：
1. shard 顺序与 eager 的 `discover_shards` 完全一致；
2. shard 内行序与 `np.concatenate` 完全一致；
3. 全局 `idx` 与 eager 使用同一次 `np.random.permutation(n)`，demo_bank 的 np RNG 消费顺序不变；
4. quota 截断在 GAE 之前，截断后的全局行数与 eager 相同。

**吞吐 gate（k3 B4）**：P3 必须做同机端到端 benchmark，分报 scan+GAE、随机 gather、逐批转换、H2D、纯 update、总墙钟。**2026-11-16 修正「冷/热 page cache」这一档**：训练落 tmpfs（`/dev/shm`）时 obs 是**常驻 RAM 页**，不存在冷/热之分 —— 该档改为「RAM 容量闸（3.97 GB obs 计入容器内存）」；只有 obs 确实落盘时才保留冷/热两读数。随机 gather 已在云机实测 **1,749 MB/s**（350 shard、168 批 ×1024 行、gate 500 MB/s ⇒ 3.5×）；除数值等价外，设「最大允许吞吐退化阈值」（P0/P3 实测后填，不先编数字）。在实测前，§0 的 15–25%/样本、§1.7 的线性 PPO 时间只能标记为**待验证假设**。

### 5.2 契约

- 旧 `eager` 路径保留，默认值切换需单独评审；先以 `--ppo-loader=lazy` 开关做 A/B。
- shuffle/reproducibility：沿用 `np.random.permutation` + 现有 main seed；A/B 时同 seed 的 mb 序列应可复现。
- `drop_last`：保留现有「尾部 `n % mb` 丢弃」；`n_used = (n // mb) * mb`。
- `per_stage_quota`：在 loader 前按 shard 截断（`trim_shard_arrays` 现有实现）；`games` 模式不做配额截断。
- 内存预检：复用 `plan_corpus_memory` 的公式，但 PPO 字段表换 `_RL_SHARD_SPEC`；`scan_shards` 在转换后按 `obs.npy` 发现即可，转换前预估用 `replay_meta`/标签。
- **逐字节对账的充要条件（qw 评审）**：
  1. 逐批应用 `channels_last`（`tensored_chunks` 的 2.08× 快路径，`trainer.py:25-28`）；否则数值对但慢 ~2×；
  2. demo_bank 混批的 np RNG 抽取**逐步**保持顺序（`engine.py:316-324`）；
  3. adv 全局归一**含被丢尾部**（`np_core.py:505-506`）；
  4. per-epoch **不重排**（`engine.py:345-348`）；
  5. `per_stage_quota` 截断在 GAE **之前**（`trim_shard_arrays`，`np_core.py:530-554`）；replay 路径在转换后截，顺序不变。

### 5.3 小 VRAM 支持

- 逐批 `.to(device)`；不保留 `tensored_chunks` 的全量副本。
- 目标平台 **≥16 GB VRAM**（Kaggle / Colab / AIStudio）。mb=1024 时 obs u1 ≈ 11.8 MB/批（f32 才 ~47 MB）；
  最坏全量上卡 ≈ 5.5 GB（语料 4.1 + 模型/激活 1.4）⇒ 余量充足。**不做 4–6 GB 小卡真机测试**
  （用户 2026-11-16 明确：产线没有这种卡）；改为上卡前显存预检 + 不足自动切 lazy / 响亮拒绝。
  若仍 OOM，`--mb 512` 回退；梯度累积不默认开（另立实验）。
- 16 GB 系统内存：lazy 峰值 ~1 GB + page cache；491,520 样本的 obs 实测 **3.97 GB**（不是 5.65 GB ——
  那条按 702 样本/局算，实测 494）。**落 tmpfs 时这 3.97 GB 是常驻 RAM**，落盘时才是 page cache。

---

## 6. 训练侧缩放与判据

### 6.1 缩放档位（S1/S2/S3，与 §8 的 P0–P5 实施阶段分开）

1. **S1：35×5**（第一目标）。trainer→worker ~1.2 MB（实测折算）；PPO ~202 s；**转换：86,450 样本 ×
   254 µs/样本 = 22 s 单核、池化后 11 s**（2026-11-16 云机实测口径，见 §9 转换预算）；RAM ~4 GB 峰值
   （eager 也不爆）；VRAM ~1.4 GB。
2. **S2：35×10**。PPO ~404 s；eager 峰值 ~8.5 GB（建议此时已切 lazy）；**转换 44 s 单核 / 22 s 池化**
   （350 局 = 172,938 样本，obs 1.99 GB）。
3. **S3：35×20**。PPO ~808 s；lazy 必需；**转换池化 ≈43 s（低配 4 核）**（700 局 = 345,876 样本，
   obs **3.97 GB** 写盘）；**重哈希不进预算**（见 §9 那条的 2026-11-16 修正）；
   **固定样本量**（用户拍板）⇒ seed 数按实际记录，接受 shortfall，不承诺 20 seeds/关。

#### S1 逐阶段基线（35×5 seeds = 107,520 样本，2026-11-16 云机实测常数外推）

> 107,520 ÷ 175 局 = **614 样本/局**，高于实测均值 494（g4-mobility/hard/12k）—— 这档语料局更长，
> 但下列**逐样本常数**来自同一批云机实测，与每局样本数无关。网络按 plan 的 **3.7 Mbps** 保守参考；
> 控制台/H2D 强依赖 GPU 型号，只有估值。

| 阶段 | 产物 | 原始体积 | xz -3 后 | 耗时 | 依据 |
|---|---|---:|---:|---:|---|
| ① rollout 仿真 + 录制 | replay + 标签 + metrics | 91.2 MB | **3.55 MB** | **679 s 单核**（≈11.3 min），录制仅 +0.8 s（0.12%） | 632 µs/tick × ≈1.074M tick |
| ② 写 replay shard | 同上，落 tmpfs | 91.2 MB | — | <0.1 s | 2 GB/s，且异步 |
| ③ sampler→trainer 回传 | 同一份包 | — | 3.55 MB | **7.7 s** | 3.7 Mbps；13.2× 压缩比 |
| ④ trainer 收包/解包 | shard 目录 | 91.2 MB | — | 秒级（未单独计） | — |
| ⑤ **pre-pass 重建** | obs + scalars + extra | **1,280.0 MB** | — | **27.3 s 单核 / 13.3 s @4 核** | 254 µs/样本纯转换；池化实测 124 µs/样本 @4 核 |
| ⑥ 落盘 | 1.28 GB obs + 52.8 MB 其余 | 1.33 GB | — | 0.6 s（tmpfs）/ 6.0 s（`/tmp`） | **异步写回后端到端只 +7.5%**，不按 6 s 计 |
| ⑦ trainer→worker 下载 | replay + 标签 + reward（**无 metrics/obs**） | 52.8 MB | **1.51 MB** | **3.3 s** | 345 kB/24.5k 实测 |
| ⑧ lazy 装载 mmap + 随机 gather | 不落盘，逐批 mb 行 | — | — | **0.73 s / 遍** | 1.28 GB @1,749 MB/s；峰值 ~0.9–1.0 GiB RAM |
| ⑨ PPO 训练 | ckpt | — | — | **~202 s（plan 旧估值，未实测）** | GPU 依赖，需真机量 H2D / update / VRAM |

**全链串行下限**（不含 rollout 并行与 PPO）：③+⑤+⑦+⑧ ≈ **25 s**；rollout 在采样节点并行，
8 进程时 679/8 ≈ **85 s**。

| 逐成员 raw | B/样本 | 107,520 样本 |
|---|---:|---:|
| obs.npy | 11,492.3 | 1,235.6 MB（= 17×26×26，零冗余） |
| extra / scalars | 228.3 / 184.3 | 24.6 / 19.8 MB |
| metrics.npy | 361.0 | 38.8 MB（trainer 折 reward 后丢） |
| replay.json / replay_meta | 31.8 / 11.1 | 3.42 / 1.19 MB |
| a_*/lp_*/value/done/mask | 31.5 | 3.39 MB |
| **合计** | **12,340** | **1,326.8 MB** |

⚠ **replay 省的是传输，不是磁盘**：落盘语料仍是 1.33 GB（full 也一样）；
收益在 trainer→worker 的 **1.51 MB vs full 约 18.8 MB（12.4×）** 与 sampler→trainer 的 3.55 MB。

### 6.2 超参调整顺序（qw + k3 评审修正）

1. **P3.5 是 lr+epochs 组合干预筛选腿**（新 `--out`，不动生产）：lr 1.2e-4 → 2–3e-4、epochs 4→8；结论只叫「筛选」，不做单变量归因；若要归因需拆单变量或析因。
2. P3.5 正向后再开三臂；**三臂用同一组固定配置并冻结调度**（关 kickstart、关分段 `ppo_schedule`，固定 lr/epochs/mb/ent/kl/value_anchor）。
3. 三臂判据用 **KL_cum(24.5k 样本)**：`KL_cum = Σ_{mb in 24.5k samples} mean( (logπ_new(a|s) − logπ_old(a|s))^2 )`（approx KL，非负；与 engine 的 approx KL 同口径）；三臂同带 **0.0015–0.0025**。`per-step KL = KL_cum / (24.5k/mb)` 只作读数；per-it KL 不作判据。
4. 若 C 在等步数下劣化，先试更频繁刷新（缩小池）或降 epochs，不要直接加 lr；KL >0.05 或 entropy 连续下降则停腿复盘。

### 6.3 三臂实验的判据（qw + k3 评审修正）

- **定位**：P4/P5 首轮是 **pilot**，单训练 seed；主读 B 的学习曲线 + C 的终点，报告效应量与 CI；不写成「非劣配方」结论。
- **主判**：B 的学习曲线 vs A（相同样本锚点配对差 ≥ −1pp 为工程止损线；≥ +1pp 为正向）；C 只有终点 ⇒ 终点配对差 + 跨 seed CI。
- **因果边界**：A vs B 同时改变每轮关卡数（7 vs 35）与数据池大小，不是干净对照；**B vs C 才是数据刷新频率的主对照**；等 mb 只保证每步样本数相同，不保证梯度噪声同分布。
- **统计边界**：同 eval seed 配对只减少环境评估噪声，不能估计训练 run 的方差；eval-seed CI 不是训练可复现性证据。配方推广至少补 **3 个独立训练 seed**，三臂按训练 seed 配对，eval 仍用完整面板。
- **墙钟**：单列 eval 时间；主判用「训练墙钟（不含 eval）」；生产收益另看完整迭代墙钟（两条传输腿 + 全部阶段）。
- **机制门只作解释项**，不作停止判据；三臂同起点权重、同 eval 网格；若不能同 seed，用跨 run 配对并记录置信区间。

### 6.4 eval 节奏（生产口径不变，实验臂单独派发）

- 生产课继续 `eval_every=5 it`，eval 网格/seed/难度不变（用户拍板⑤）。
- 实验臂单独 `eval_every=1` + `eval_at`：A 5/10/15/20；B 1/2/3/4；C 1 it 终点。`_eval_on_round` 的 `eval_every==1` 分支允许 `eval_at` 任意点（`loop_dispatch.py:77-84`）。
- 实验臂的 eval 时间单列，不混进训练墙钟；C 的终点 eval 至少跨 20 seed × 35 关（与生产同网格）。

---

### 6.5 三臂 pilot 可执行设计（2026-11-17 定稿，P4/P5 用）

D8/D9 只给了等样本框架，没钉「用哪门课、哪个起点、各臂 seeds/it」。本段把它钉成可直接开 jobs 的参数表。

#### 共同冻结项（三臂逐字相同，只有池大小/刷新频率不同）

| 项 | 值 | 理由 |
|---|---|---|
| 课程 | **新 `nn-training/curricula/replay-pilot.jsonc`**，逐字段复制已跟踪的 `g5-safe.jsonc`，只覆盖下表三处采样旋钮 | g5-safe 是已跟踪、已跑到 it230 的稳定课；不引用未跟踪的 g5-aim2/g5-pilot |
| 关卡 | `level=classic35-safe-base`，`eval_stages=2000-2034`（**35 关**），hard / 20 敌 / 3 命 / 36000 tick | 任务侧一字不动；与 g5-safe 已跑的 80.94% 基线同面 |
| 起点权重 | **同一份受控扰动的 g5-safe.it230**（`scale=5`，实测起始 pass 64.1%；见下「上坡硬闸」）；原始 it230（78.7% / 历史五点窗 80.94%）作**第二读数** | 用户 2026-11-17 指出：g5-safe it210–230 末三窗 80.74 / 81.31 / 80.94 已平台，三臂都「持平」时**无法区分「刷新频率无效」与「起点无梯度」**。必须制造可恢复的上坡 |
| 奖励 / 架构 / 超参 | 逐字节 = g5-safe：`lr=8e-5`、`epochs=4`、`mb=1024`、`kl_coef=0.05`、`kl_cap=0.1`、`value_anchor=0.001`、`gamma=0.998`、`lam=0.95`、`decision_k=10` | D8「起步三臂同 lr/epochs/mb」；8e-5 是 g5-safe 实际稳定值，**不采用 D8 文中的旧 1.2e-4** |
| 调度 | **删 `ppo_schedule`，`kickstart_ref=false`**（或 `kickstart_init=0`）；`breakers_off=true`、ent 熔断守底 | D9：首轮冻结调度，一次只验「数据刷新频率」 |
| 语料格式 | **`corpus_fmt=replay` + `ppo_loader=eager`**；eval 仍走 full | P4 只切 replay；lazy 留到 P5，不与本轮混淆 |
| 样本轴 | 三臂总样本都 = **491,520**（= 480×1024，mb 整倍数）；训练墙钟不含 eval | D8 等总样本；plan 硬闸 target_transitions 必须 mb 整倍数 |

#### 上坡硬闸（2026-11-17 新增；不过闸不许开三臂）

背景：g5-safe 主曲线末窗 80.94%、p=0.089、最后三窗跨度 0.57pp ⇒ **平台期**。在平台起点上，
三臂全平是**不可判决结果**（既可能是刷新频率无关，也可能是没有可学梯度）。所以首轮 pilot 不改任务侧，
改用**训练侧受控扰动**制造同一个上坡：

1. 取 `g5-safe.it230` 的权重 JSON，用固定 seed 20261117 给**三个 policy head 的 bias**
   （`move_head.bias` / `fire_head.bias` / `hold_head.bias`）加高斯噪声，σ = `scale × 该张量自身 std`；
   **value_head / backbone 一字不动** ⇒ 状态价值仍可读，恢复曲线可归因于策略漂移。
2. 对同一 35 关 × 20 seed 面板（700 局）扫 `scale ∈ {0.5, 1.0, 2.0, 3, 5, 10}`。
   **本机实测（seed0=2000000001）**：35×4 小面板 base 80.0% · s3 75.7% · s5 67.9% · s10 10.0%（崩）；
   **35×20 完整 700 局：base 551/700 = 78.7% · s5 449/700 = 64.1%** ⇒ 上坡 **14.6pp**。
   取 **scale=5**（完整面板略低于 65% 下沿，但远好于平台期的 0.57pp 跨度；硬闸按 ≥60% 判）。
   预生成脚本 `tmp/perturb-weights.py`（临时，不入库）；剂量与对应胜率**写进 pilot 任务 spec**，
   三臂必须消费**同一个**扰动 JSON（sha256 对账）。
3. **硬闸**：扰动起点在 eval 面板的 pass（win ∪ cleared）≤70% 且 ≥60%（**实测 64.1%**），三臂起点权重 sha 完全相同；
   否则停 pilot 调剂量。原始 it230（80.94%）同时评一次，作为「稳态平台第二读数」，不参与主判。
4. 主判改为 **恢复曲线/恢复速度**：A/B 在相同样本锚点、C 在终点，比较回到 ≥75%（原点的 −3pp；原点本次同面板 78.7%）所需样本数，
   以及各锚点 win/KL_cum/clip/entropy。若三臂恢复速度相同，才能说「在该配置下刷新频率无可测效应」——
   这是**有梯度下的阴性**，与平台期的不可判决严格区分。

> 这是训练侧扰动（起点权重），不是任务侧改难度；关卡/奖励/lives/max_ticks 一字不动，符合 AGENTS §0.2。
> 它只回答「策略被打偏后，不同刷新频率的恢复效率」，不冒充「稳态平台上的长期优劣」。

#### 三臂参数表

| 臂 | 每 it 关卡数 / seed 数 | `target_transitions`/it | iters | 总样本 | chunks/it | 步/it | 总步 | eval 锚点 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| **A**（7×5×20） | 7 / **5** | **24,576**（24×1024） | **20** | 491,520 | 24 | 96 | 1,920 | it 5 / 10 / 15 / 20 |
| **B**（35×5×4） | 35 / **5** | **122,880**（120×1024） | **4** | 491,520 | 120 | 480 | 1,920 | it 1 / 2 / 3 / 4 |
| **C**（35×20×1） | 35 / **20** | **491,520**（480×1024） | **1** | 491,520 | 480 | 1,920 | 1,920 | 终点 1 次 |

> 注意：P4 原文「`target_transitions=122,880`」是 **B 臂单 it** 的值，不是三臂通用值；A=24,576、C=491,520。
> 三臂 `rotation_mode=even`、`paired_rotate_seed=20261115`（与 g5-safe 同），A 的 7 关按轮次轮换，20 it 覆盖 35 关约 4 圈。
> 若实际 seed 供给不足，`coverage_report.py` 逐关报 SHORT；**硬闸仍是 35 关均有有效样本**，不靠补 seed 造满。

#### 训练 seed 与配对

- 首轮 **1 个训练 seed**：三臂同 `runId` 前缀 `replay-pilot-s1-{A,B,C}`，且**共用同一份扰动起点 JSON**
  ⇒ `init_weights_fp` 相同、每 it 的 `job_seed(runId,it,init_weights_fp)` 在三臂间可比；
  由训练核自动派生（train_core.py:434），不另加手填种子。
- 推广腿至少 **3 个独立训练 seed**：同一组三臂复制 3 份，`runId` 后缀 `-s2/-s3/-s4`；按训练 seed 配对做差，eval seed 面板不变（§6.3 统计边界）。
- eval 面板固定 **35 关 × 20 seed = 700 局/锚点**（生产口径），`eval_every=1` + `eval_at` 按表；A/B 各 4 锚点、C 1 锚点 ⇒ 首轮 eval 共 **6,300 局**，单列墙钟。

#### 决策规则（先写死，跑完不许改口）

- **主读**：扰动起点的**恢复曲线**——B vs A 在相同样本锚点（A 的 it5/10/15/20 ↔ B 的 it1/2/3/4）配对差
  （工程止损 **≥ −1pp**，正向 **≥ +1pp**）+ 回到 ≥78% 所需样本数；C 只有终点 ⇒ 终点配对差 + 跨 seed CI。
  原始未扰动起点另作稳态第二读数，**不进主判**。
- **主对照**：B vs C 才是「数据刷新频率」的干净对照；C 只有终点 ⇒ 终点配对差 + 跨 eval seed CI，不写五点窗。
- **机制门（解释项，不是停止判据）**：KL_cum(24.5k 样本) 目标带 **0.0015–0.0025**；逐 epoch 打 KL / clip fraction / ratio 分位数；KL > 0.05 或 entropy 连续下降则停腿复盘。
- **No-Go**：B 连续 2 锚点 < A −2pp；或任一臂 35 关有效样本硬闸失败；或 replay identity 失败率 > 0。
- **成功判据**：B 不低于 A 止损线、B vs C 终点差不劣、训练墙钟（不含 eval）不退化、体积/覆盖/identity 全部绿；输出效应量 + CI，**只叫 pilot，不写「配方已证」**。

#### 成本预估（按本轮实测外推，不是承诺）

- rollout：每臂 700 局；本机 7 并发导出实测 **3.38 局/s** ⇒ 单臂 ≈ 207 s，三臂 ≈ 10.4 min。
- replay→npy 转换：优化后本机 7 worker 写盘 ≈ **11 s/700 shard**（五轮 3500/3500），三臂 ≈ 33 s。
- PPO：按 plan 旧估值 ~202 s/491k 样本/臂，三臂 ≈ 10 min（需真机校正）。
- eval：6,300 局，按 god-ai 3 worker ≈ 30 s/2100 局 ⇒ ≈ 90 s。
- 首轮合计约 **23 min**（不含排队/上传），在 16 GB 显存机型上不会撞显存门。

## 7. 改动清单

### 7.1 TS / rollout

1. `tools/sim/export-rl-rollout.ts`
   - 新 flag `--corpus-fmt full|replay`（缺省 `full` = 旧路径逐字节不变）；由任务/rollout spec 透传，不再由 packer 猜。
   - `full`：现有行为，写 obs/scalars/extra + 标签 + manifest。
   - `replay`：只写 `replay.json` + `replay_meta.json` + 非 obs 标签 + `metrics.npy` + manifest；**不写** obs/scalars/extra。`ShardData` 里也不 push obs/scalars/extra（省内存），但每决策仍 encode + forward 用于选动作和 obsHash。
   - `runOne` 内：`world.loadStageData`/init snapshot 后 `InputRecorder.startNew(world)`；每 tick `recordFrame(scripted)`；结束后 `serializeReplayFile`。
   - `decisionTicks` 由主循环在 `decisionDue` 为真时 push（与现有 decision 计数同源）；`obsHash` 在每决策 obs 上滚动哈希。
   - 写入前校验 `decisionTicks.length === shard.n`；不满足即响亮失败。
   - `--pack` / `--pack-memory` 的文件清单按 `corpus_fmt` 选：`full` = `RL_SHARD_FILES`；`replay` = `replay.json/replay_meta.json` + 标签 + `metrics.npy`，不要按 `obs.npy` 存在性硬读。
2. 新 `tools/sim/replay-to-shard.ts`（纯 JS，可 bundle）。
3. 如需要，抽 `shardNpyEntries`/`npyBytes` 到无模型依赖的模块；`RL_SHARD_FILES` 常量保持单一来源。
   - `writeRlShard`/`shardNpyEntries` 增 `format` 维度：`replay` 只写标签 + `metrics.npy` + `replay_meta.json`，不写 `obs/scalars/extra`；`full` 逐字节不变。
4. 复用 `src/replay/{InputRecorder,ReplayInput,pack,file}.ts` 与 `src/snapshot/WorldSerializer.ts`，不改格式。
5. `tools/agent/sampler-agent.ts` / `tools/sim/serve-any.ts`：把任务 spec 的 `corpus_fmt` 透传给导出器；`kind=iter` 的任务强制 `full`，eval 任务强制 `full`。

### 7.2 Python / wire / worker

1. `nn-training/common/manifest.py` / `common/shard.py`：rollout spec 增 `corpus_fmt`（`full|replay`，缺省 `full`）；`validate_result` 按格式选期望文件集；manifest/idempotency 记录 `corpus_fmt`；旧包缺省 = full。
   - 现有 `_trim_shards_to_quota` 只读 manifest 的 `nSamples`（`trainer/loop_remote_job.py:73-103`），不依赖 `obs.npy` ⇒ replay shard 可直接参与打包前裁剪，不需要先重建 obs。
2. `nn-training/trainer/loop_steps.py` / `remote/hub_client.py` / `remote/payload_pack.py`：把 `corpus_fmt` 写进任务 spec、从结果 manifest 带到 trainer；full 时幂等键**省略**该分量（逐字节旧键），replay 时追加；`replay` 结果容器只收 replay+标签；请求/结果格式不符即拒收。
3. `nn-training/remote/payload_slim.py` + `remote/payload_pack.py`：新增「`corpus_fmt=replay` 时 DROP obs/scalars/extra」逻辑（当前仓库没有）；请求 `full` 时即使目录有 `replay.json` 也**不 DROP**；`ShardSegments.begin/take` 段身份加 `corpus_fmt`；dispatch 腿 replay bytes 走内存 `files`，不建目录。
4. `nn-training/remote/worker.py`：kind=ppo 解包后 pre-pass；bundle 由 trainer 预构建并随 job 附件下发（`ts_code_sha256` + `replay_converter_sha`）；worker 只跑不建；**shard 级事务**（临时目录→全量校验→完成清单→原子 rename）；失败分类（确定性 identity vs 可重试 I/O）；旧 worker 收到 replay 包清晰拒收。
5. `nn-training/remote/download.py` / `remote/rollout_node.py`：`_ensure_ts_code` 补 kind=ppo 附件接线；Bun 安装复用 `_ensure_bun` / `_install_bun_mirror`（镜像源 + 官方 curl 兜底、按版本缓存）；Bun 失败但 Node ≥22 ⇒ 用 Node 跑预建 bundle；两者皆无 ⇒ 响亮失败。
6. `nn-training/trainer/loop_remote_job.py` / hub publish：job manifest 增 `corpus_fmt`/`ts_code_sha256`/`replay_converter_sha`；hub 允许 kind=ppo 携带 ts_code/bundle 附件；**端到端能力协商**（trainer→hub→sampler→worker 同名 `replay_corpus`）；结果 manifest 与请求 `corpus_fmt` 不一致即拒收；converter 契约版本进结果身份或要求新 runId；`wire` 账增新字段；`kind=iter`/eval 强制 full。
7. `nn-training/worker/ppo/lazy_corpus.py`（新）+ `np_core.py`：lazy loader（obs 逐 shard mmap、`row_map`、`load_ppo_corpus` 分派）；eager 路径保留；`--ppo-loader` 默认 eager。
8. `nn-training/worker/ppo/trainer.py` / `engine.py`：逐批 `.to(device)`（`tensored_batch`，lazy 专用）；`tensored_chunks` 仅 eager 路径使用。逐 epoch KL/clip 读数 + 可选 it 内中止阈值留到 P3.5/D8（三臂实验前）。
9. `nn-training/worker/data/npyio.py`：复用 `plan_corpus_memory` 公式（`CorpusMemoryPlan.lazy_peak_bytes`）；PPO 足迹按 `_RL_SHARD_SPEC` 逐文件 stat（obs 单列 + 其他字段），在 `make_chunks` 前预检。

### 7.3 测试

- 根 `tests/`：
  - `tests/replay/replay-identity.test.ts`：真实关 + 自定义关 + **hybrid opening 接管段（policy_mask=0）** + **state_init 交棒段（initTick>0）**，obs/scalars/extra/mask 逐字节一致；同 seed 两遍一致。
  - `tests/replay/action-capture.test.ts`：v6 下 `move/fire(1|2)/hold` 三种动作逐 tick 与执行输入一致；`hold` 写 v3 bit、`fireBeforeTurn` 写 v3 bit；legacy v1 不被误升级；`ReplayInput.usesHoldFire3()` 恢复正确。
  - `tests/replay/opening-capture.test.ts`：opening 接管段 replay frames == `scripted` 实际执行动作；`policy_mask=0`；`a_*` 允许与 frames 不同（NN 标签）；converter 不重跑 OpeningController；`replay_meta.opening` 记录 doneTick/abortReason；**`opening-null-move` 探针**统计 `move=null` tick 的 `p.dir`/位移，锁定当前 `hold=1+move=up` 的实际语义。
  - `tests/replay/replay-meta.test.ts`：decisionTicks 长度/越界/重复、obsHash 不符（含 pre-tick off-by-one）、tickHash 断裂 ⇒ 各自响亮失败。
  - `tests/sim/export-rl-rollout-replay.test.ts`：`--corpus-fmt full` 与旧路径逐字节相同；`replay` 只产 replay+标签、不产 obs/scalars/extra；录制无副作用；P1 记录 recordFrame/hash 开销。
- `nn-training/tests/`：
  - `tests/remote/test_corpus_fmt.py`：任务 spec `full|replay` 透传；结果文件集按格式校验；请求/结果格式不符拒收；旧包缺省 full；旧 worker 收到 replay 显式拒收；full 幂等键省略 corpus_fmt。
  - `tests/remote/test_payload_replay.py`：sampler→trainer 结果容器不含 obs/scalars/extra；trainer→worker payload 只含 replay+标签+reward；请求 full 时 replay.json 不被 DROP；P0 逐成员字节分解；**零落盘内存段==磁盘段、零目录、换轮隔离、`corpus_fmt` 热切换拒绝复用、full/replay 不混包**。
  - `tests/remote/test_worker_replay_prepass.py`：伪 replay → 转换 → `load_episodes` 成功；坏 replay 不落盘；converter bundle/ts_code 缺失或 sha 不符响亮失败；Bun 失败回落 Node；**shard 级事务三类故障注入**（成员写完即崩溃、复用完整产物、拒绝不完整/旧 converter 产物）。
  - `tests/remote/test_corpus_capability.py`：trainer→hub→sampler→worker 端到端 `replay_corpus` 协商；旧 sampler/worker 不派/不领 replay；结果格式不符拒收；converter 契约版本进结果身份。
  - `tests/worker/test_ppo_lazy_loader.py`：**已绿（9/9）**——eager vs lazy 同 seed 的 `row_map`/全局行序/GAE/adv/ret/minibatch/demo RNG/最终权重一致；五条对账；冷/热 page cache 吞吐读数；**小 VRAM**：lazy 不调用 `tensored_chunks`、每批只搬 mb 行（u1 ≈11.8 MB/批，mb=1024）。
  - `tests/worker/test_fixed_samples_shortfall.py`：固定样本量下每关 `games/unique seeds/samples/SHORT/丢弃整局` 账本正确；35 关均有有效样本硬闸。
  - `tests/trainer/test_schedule_frozen.py`：首轮三臂冻结调度——kickstart/ppo_schedule 不被读取；真实 PPO 集成断言 C 不整 it 吃 it1 段。
  - `tests/trainer/test_eval_at_experiment.py`：实验臂 `eval_every=1` + `eval_at` 在 A/B/C 的样本锚点派发；生产 `eval_every=5` 不变。
  - `tests/trainer/test_wallclock_ledger.py`：两条传输腿 + 全部阶段的墙钟账；eval 单列；实验主判排除 eval。
  - `e2e/test_replay_corpus_e2e.py`：一个小语料走完整 `corpus_fmt=replay` → worker → PPO，结果与 npy 路径同权重/同 seed 的 PPO 指标一致（容差内）。
- 性能基准：`tools/sim/replay-to-shard.bench.ts`（或用现有 bench 脚本），输出 games/ticks/decisions/ms，进 CI 只做预算断言不跑大语料。

---

## 8. 分阶段实施与 Go/No-Go

### P0 原型与真机测量（不改生产；qw+k3 评审扩容）

- 先 20 局本机 canary，再扩到 **≥1000 局（35 关 × 30 seed）**；必须包含 **Bun 录制 → Node 跑 bundle** 与 **Windows 录制 → Linux 重建** 两个交叉组合，在目标云机上跑；覆盖真实关、自定义关、hybrid opening、state_init。
- **opening-null-move 探针（本地已完成，见 §1.10）**：6 关/1,974 opening tick/30 null tick；v6 4 次转向 up、legacy 0 次、位移均 0。结论：v6 null-move 是「转 up+不前进」，不是原地不动；replay 记录实际执行，玩法修复另立腿。目标云机可复跑，不作为 replay P0 的 No-Go。
- 增加「现有 4.3 MB payload 逐成员压缩后字节」分解（metrics/labels/replay/reward 各多少），所有体积 gate 从该表出，留 ±30% 裕量。
- 在目标机量写盘带宽（**已测**：tmpfs 2.0 GB/s、`/tmp` 207 MB/s）；**不做 worker 侧重哈希**；转换预算 = **池化 ≈43 s（低配 4 核）**（350 shard 实测 21.4 s，宽度 `effective_cores()`；纯转换 88 s/核），3.97 GB 写盘在内。
- **Gate**：obs/scalars/extra/mask 逐字节一致；**确定性 identity 失败零容差**（obsHash/tickHash/shape/dtype/标签对齐/代码版本任一不符即停，不得用重采掩盖）；网络/瞬时磁盘错误单独统计（定义分母=job 还是 shard、最小样本量、滚动窗口）；**35×20 等效语料转换 ≤2 min —— 2026-11-16 云机实测池化 43 s（350 shard 21.4 s 折 700，自动宽度 4）vs 逐 shard 冷进程 181 s（超 gate 1.5×）⇒ 必须走池 + 用完所有核**；**峰值 RAM ≤1 GB —— 实测 node 单进程吃 350 shard 峰值 180 MB**（池化前；不含 page cache 的说法在 tmpfs 上不成立，见 §1 P3 吞吐 gate 的修正）。
- **No-Go**：跨运行时 identity 失败未定位；`Math.hypot` 分叉未解决（改平方距离属玩法改动，另立新纪元）；**48h 不把已知 identity 不匹配转为可接受风险**。
- **进展（2026-11-15，本机 + WSL Linux 已完成）**：
  - Harness：`tools/sim/verify-replay-corpus.ts`（1015 局 = 35 关 × 29 seed，`hard`，maxTicks=1200，seed 2000000001…）。
  - 规模：**121,702 决策 / 1,216,991 tick**；Bun 进程内转换、Node bundle、WSL Linux Node 三端 **0 mismatch**（obs/scalars/extra 逐字节）。
  - 耗时：Node 15,437 ms / 1015 shard（**12.7 µs/tick**，15.2 ms/shard）；WSL Node 69,757 ms（**57 µs/tick**，主要是在 `/mnt/d` 9p 上读写，不是 CPU）；**云机原生盘已测**（2026-11-16）：node 单进程 **25.4 µs/tick**、bun **22.1 µs/tick**，但逐 shard 起进程的形态被 596 ms/shard 的冷启动主导（见 §8 P0 进展）。
  - 录制开销（60 局 / 7,190 样本 / 1200 tick）：full 14,454 ms vs replay 14,601 ms（**+1.02%**，+2.45 ms/局）；`recordFrame+hash` 合计 72 ms（**1.2 ms/局 / 0.01 ms/样本**）；Bun 进程内转换 1,348 ms（**22.5 ms/局**）。
  - 体积（60 样本 shard 实测，xz preset 3）：full_raw **10,976 B**；full_diet **9,200 B**；replay_diet **3,928 B**（= full_raw 的 36% / full_diet 的 43%）；`replay.json` 9,376 raw / 1,408 xz，`replay_meta` 1,153 / 548；obs 689,648 raw / 5,324 xz（本样本极稀疏，真实 24.5k 语料以 labels/metrics 为主，gate 需按 §1.2 的真实分解）。
  - 覆盖：真实关 0..34；hybrid opening 与 state_init 交棒段 identity 另在 TS 测试中逐字节验证。`Math.hypot` 在这 1015 局内未观察到任何分叉（不能证明全局，但已覆盖大量敌人/玩家移动）。
  - **P0 payload 逐成员压缩分解（2026-11-16，本机，不跑训练）**：`tmp/g5-safe/remote-jobs/77f06dcef522ecd6/payload.tar.xz`（4,316,596 B）逐成员 xz preset 3：obs/scalars/extra **3,881,184 B（88.9%）** · labels **302,028 B（6.9%）** · reward **119,020 B（2.7%）** · manifest **63,772 B（1.5%）**（raw 合计 338,237,328 B；metrics 已由 wire-diet 折进 reward，故该成员为 0）。⇒ replay 路径 DROP obs 三张后 trainer→worker 约 **0.49 MB**，低于 0.85 MB gate；**`replay.json`/`replay_meta` 的真实字节已由 2026-11-16 云机 350 局实测**：**31.8 / 11.1 B/样本**（`replay.json` 16 KB/shard 量级），见 §3.2 与 §8 P0 进展。
  - **P0 云机实测（2026-11-16，Kaggle 2 vCPU / 32 GB，`g4-mobility.it380` @ hard，350 局 × maxTicks=12000 = 172,938 样本 / 1,727,568 tick）**：
    - identity：**350/350，mismatches=0**（连跑两轮，samples 逐字一致）；Tier A 另跑 70 局 @1200 tick 亦 0 mismatch。
    - 墙钟：整批 **37.5 min = 6.43 s/局**（full + replay 两遍 + 转换，单进程）；full **632 µs/tick**、
      replay 形态 **636 µs/tick**（**两者等值是构造使然**：`corpusFmt=replay` 仍跑完整 rollout，
      差异只有建 recorder / 不压 obs / 记 decisionTickList 三处）。
    - **rebuild 成本 21.4 µs/tick = full 的 3.4%（29.5×）**；录制 **7.43 ms/局 = 0.12%**。
    - 转换（350 shard）：单进程 node **25.4 µs/tick**、bun **22.1 µs/tick**；逐 shard 起进程（生产形态）
      499 ms/shard —— ⚠ **这是 node 的口径**：596 ms 冷启动 − 126 ms 转换 ⇒ 75% 是启动成本。
      **同一形态换 bun 只要 259 ms/shard**（90.6 s / 350），**池化后 65 ms/shard**（22.9 s / 350，`served=350 fallback=0`）
      ⇒ 归因应是「node 启动 + 每 shard 重新解析 bundle」，不是「进程模型不可救」。`workers=4` 只到 21.6 s（2 物理核，超线程只值 6%）——**但这台机不是产线机型**：GPU 云机核数浮动
      （低配 2 物理/4 逻辑、中配 4/8、高配 24），所以宽度**不写死**，缺省用完 `effective_cores()` 全核。
      ⇒ 已改接长驻池（commit `f7f1bff2`，`serve_pool.pool_for_bundle` + 转换器 `--serve`），
      本机 60 shard **2.9×**、obs 逐字节不变；云机复测待办。
    - 体积：见 §3.2（**16.2 MB / 6.9 MB** 对 45 / 12 MB，双绿）。
    - gather（P3 CPU 侧）：mmap 顺序 32.4 GB/s、**随机 gather 1,749 MB/s**（350 shard / 168 批 ×1024 行）
      对 500 MB/s gate **3.5× 余量**。
    - **落盘 vs 内存盘（同批 120 shard，池化 w=4）**：tmpfs **9.6 s（80 ms/shard）** vs `/tmp`
      **10.4 s（86 ms/shard）⇒ 1.075×（+6 ms/shard）**。按 `dd` 的 207 MB/s 推应 +27 ms，实测只有 1/4.5 ——
      写回是异步的，且 4 个 worker 把 IO 与计算重叠掉了。⇒ **内存盘不是转换腿的必需品**（只值 7.5%），
      它的真正价值在 RAM 账（obs 3.97 GB 常驻、无 page cache 抖动）与训练侧装载，不在这一步。
    - **重哈希：生产不存在**。`payload_sha256` 只管压缩包、marker 的 `replay_sha256` 只管 `replay.json`，
      obs 的 identity 由 `replay_meta.obsHash`（转换循环内增量算）承担；落盘后重扫实测 255 MB/s
      （`sha256sum` 无 SHA-NI，**tmpfs 与 /tmp 同速** ⇒ 与介质无关），不进任何预算。
    - **eager/lazy 数值等价：已在本地 CPU 验证，不需要 GPU**（用户 2026-11-16 点明）。最小 job = 2 shard
      ×12 样本、mb=8、2 epoch，`test_ppo_update_eager_lazy_final_weights_identical` **5.34 s 绿**：
      `row_map`/全局行序/逐 chunk 字段、GAE/adv/ret、demo RNG、最终权重逐字节一致，stats 差 <1e-12。
      同文件另 8 条（quota 截断/drop_last/v6 可选列/逐批上卡/吞吐）全绿。**这条从待办里移除。**
    - `replay+eager` vs `full+eager` 的门**由两段已成立的证据闭合，无需另跑 PPO**：① replay→obs/scalars/extra 与 full 逐字节相同（TS identity 测试 + 云机 350 局 mismatches=0）；② 同一份 chunks 在同 seed 下 ppo_update 结果相同（上面 CPU 最小 job）。**4–6 GB VRAM gate 已移除**（产线均 ≥16 GB）。

### P1 rollout 录制（additive；k3 B2/4.3）

- `--corpus-fmt`、replay_meta、decisionTicks、obsHash；默认 `full`；`sim.tick() → recorder.recordFrame(scripted) → scripted.endFrame()`；full 模式不创建 recorder、不算 tickHash、不复制 snapshot。
- dispatch 腿：replay/replay_meta 作为内存 `files` 交给 `ShardSegments.add`，**不得创建 shard 目录**；磁盘/内存同一序列化字节；段身份加 `corpus_fmt`。
- **Gate**：`full` 路径 shard npy 逐字节不变且墙钟/内存不退化；`replay` 路径 identity 绿；零落盘（内存段==磁盘段、零目录、热切换拒复用、full/replay 不混包）；`bun run check` 绿。
- **进展（2026-11-15）**：
  - `tools/sim/export-rl-rollout.ts`：`--corpus-fmt full|replay`；replay 不 push/不写 obs/scalars/extra；`replay_meta.json`（decisionTicksList/nSamples/obsHash/tickHashes/recordMs/bytes/opening）；`sim.tick() → recordFrame → endFrame` 顺序；full 不创建 recorder。
  - 新 `tools/sim/replay-to-shard.ts`（D3 生产转换器）：只读 replay+meta+标签，`ReplayInput` 驱动世界，不重跑 OpeningController/不用 `a_*`；校验 decisionTicks/npy 行数/obsHash/tickHash 链/终局 status 与 killCount/lives；`.tmp`+rename 原子写。
  - 零落盘：`--pack-memory` 的 memory entries 含 replay 两成员，dispatch 腿不创建 shard 目录；`ShardSegments.begin/take` 身份加 `corpus_fmt`（缺省 full = 旧行为），`begin_payload_parts`/`_remote_ppo_publish` 透传。
  - 测试：`tests/sim/export-rl-rollout-replay.test.ts`（8 条：full/replay 文件集、meta 自洽、identity 逐字节、pack-memory 零目录、hybrid opening identity、坏 decisionTick/坏 obsHash/缺 replay 响亮失败）；`nn-training/tests/remote/test_payload_parts.py` 加 `corpus_fmt` 热切换用例。
  - 本地对账：stage 0 / seed 1 / 600 tick，replay→obs/scalars/extra 与 full 逐字节相同；`bun run check` 绿。
  - 跨运行时（本机 Bun→Node + Windows→WSL Linux）：`bun build --target=node` 出单文件 bundle；1015 局三端 0 mismatch（见 §8 P0 进展），转换器使用 `node:crypto`，无 Bun 运行时依赖。
  - `replay_meta` 审计面补齐：`codeSha`（rollout 侧 TS 代码 hash，惰性缓存）、`initTick`、`opening{doneTick,abortReason,phase}`；`decisionTicksList` 改为**相对 replay 帧序号**（state_init 从 initTick 起录时，绝对游戏时钟会错位）。
  - 测试覆盖：hybrid opening 接管段（policy_mask=0 + replay identity + opening 元数据）、state_init 交棒段（initTick=300 + 逐字节 identity）；P1 录制开销读数见 §8 P0（+1.02% 墙钟 / 1.2 ms 每局）。

### P2 两段 wire + worker pre-pass（只切 replay+eager）

- 任务 spec `corpus_fmt` 透传（sampler→trainer）；结果容器按格式；trainer→worker payload；trainer 预构建 bundle + ppo job 附件；pre-pass；端到端 capability 协商；shard 级事务。
- **Gate**：同机同数据 `replay+eager` vs `full+eager` 的 PPO 结果一致（固定确定性环境比最终权重或明确数值容差）；sampler→trainer ≤2.5 MB（**实测 0.81 MB / 24,576 样本，2.8× 余量**）；trainer→worker ≤0.85 MB（**实测 0.35 MB，2.4× 余量**）；**转换 ≤5 s（24,576 样本 = 6.2 s 单核 ⇒ 池化后按核数再除；必须走池，逐 shard 冷启动是 259 ms/shard 的量级）**；完整迭代墙钟不退化；旧 payload 兼容；坏 replay fallback 绿。
- **进展（2026-11-15，机制已落地；端到端 PPO 数值对账待跑）**：
  - Sampler：`/v1/task?corpusFmt=replay` → `export-rl-rollout --corpus-fmt replay`；eval/BC/intent/goal 直接 400（强制 full）；taskKey 加 `:cfr`；ping 新增 `replayCorpusSupport`。
  - Trainer：`--corpus-fmt full|replay`（缺省 full）；dispatch 透传 `corpusFmt` 且按能力位 fail-closed；`validate_result` 接受 replay 文件集（标签 + replay 两成员，无 obs 三张）；`payload_slim` 在 replay 请求下 DROP obs/scalars/extra，格式不符响亮拒收；`publish_job` manifest 写 `corpus_fmt`/`replay_converter_sha256`，幂等键第 6 分量仅 replay 追加（full 旧 job_id 逐字节不变）。
  - Bundle：`_ensure_replay_converter` 在 trainer 侧 `bun build --target=node` 预构建并缓存，随 kind=ppo payload 根下发；worker 只跑不建。
  - Worker：`_ensure_payload` 落地后、D14/PPO 装载前执行 pre-pass（`remote/replay_corpus.py`）：转换器 sha 闸、Node→Bun 运行时回落、逐 shard 子进程、产物齐全断言、`replay_converted.json` 完成标记（续跑只复用匹配产物）；kind≠ppo 的 replay 直接拒收。
  - 测试：`test_corpus_fmt` 覆盖已在 `test_dist_shard_split` / `test_payload_slim` / `test_payload_parts` / `test_worker_replay_prepass` 落地（能力位/文件集/DROP/混包/幂等键/转换器闸/失败与续跑）；`nn-python-gate` 6108 passed。
  - 身份：`corpus_fmt` + `replay_converter_sha256` 均进 job 幂等键（full 省略；换转换器 = 换 job/结果身份）。
  - 事务：worker pre-pass 改为 `<shard>.conv-tmp`（硬链接输入）→ 转换 + 校验 + 完成标记 → 原目录 `.conv-old` / 临时目录顶上 / 删除旧目录；失败原目录一字未动，崩溃残留由下一次 `_clean_stale_transaction` 复位。
  - **hub / PPO worker 能力协商（2026-11-16）**：新增 `CAP_REPLAY_CORPUS`；worker claim body 声明能力位，hub 在唯一租约入口（`_claim_locked`）对 `corpus_fmt=replay` fail-closed 拒绝缺能力者（拒因 `no_replay_corpus`）；hub `GET /ping` 与 worker_server `/ping` 均声明该能力；push 派发按 `/ping` 能力选机。测试：`tests/remote/test_corpus_capability.py`。
  - **D10 墙钟账（2026-11-16）**：worker `wire` 新增 `corpus_fmt`/`replay_bytes`/`labels_bytes`/`convert_sec`/`convert_jobs`/`convert_fallback`；trainer `_wire_from_result` 透传到 iteration `wire` 顶层。full 路径为 `full`/0。
  - 未完成：同机 `replay+eager` vs `full+eager` 的数值对账（**可在 CPU 最小 job 做**，不必等 GPU）、2 vCPU 云机转换墙钟**已测**（见 §8 P0 进展）。
- **Gate 绿后**：7×5 生产课只切 `corpus_fmt=replay`（仍是 eager）；`full` 保留回滚；前 2 个五点窗盯 identity 失败率/`convert_sec`/per-step KL/clip。**不在此阶段切 lazy。**
- **No-Go**：确定性 identity 失败 >0，或可重试失败率 >1%。

### P3 lazy loader + 小 VRAM（独立切换）

- 先实现确定性 `row_map` + 五条对账；再做同机 eager/lazy benchmark（冷/热 page cache、scan+GAE、gather、转换、H2D、update、总墙钟）；数值与吞吐 gate 都过，才在 7×5 生产切 lazy（用户拍板），eager 留作回滚。
- **Gate**：491k 样本 lazy 峰值 ≤2 GB（**落 tmpfs 时 obs 的 3.97 GB 是常驻 RAM，要单独计入容器内存账，不能按「page cache 可回收」豁免**）；**上卡前显存余量预检**（产线平台 ≥16 GB，最坏全量 ≈5.5 GB；不足则响亮拒绝/切 lazy，**不再做 4–6 GB 小卡真机验证**）；eager/lazy 同 seed 的全局行序/GAE/adv/ret/minibatch/demo RNG/最终权重一致；吞吐退化不超过 P0/P3 实测阈值。
- **Gate 绿后**：生产切 `replay + lazy`；回滚点 `corpus_fmt=full` 与 `--ppo-loader=eager` 分开。
- **进展（2026-11-16，实现 + 单测绿）**：
  - `worker/ppo/lazy_corpus.py`：`row_map`（`row_shard`/`row_local`）+ obs 逐 shard `mmap_mode="r"` + 小字段（≈200 MB）常驻 + 全局 permutation/归一；`load_ppo_corpus` 是 `--ppo-loader` 唯一分派点（eager 仍走 `np_core.load_episodes`，逐字节不变）。
  - `np_core.load_shard(mmap=True)` / `chunk_episodes` 鸭子类型分派；`ppo_update(lazy=True)` 逐批 `tensored_batch` + kickstart ref 首次使用现算；`tensored_chunks` 仅 eager 使用。
  - `--ppo-loader` 贯通 `worker/cli.py` → `build_ppo_job`（eager **省略** manifest 键）→ `manifest.py` 可选默认 + 值域校验 → `train_core`；Paddle 后端 `check_ppo_loader` 响亮拒收 lazy。
  - `tests/worker/test_ppo_lazy_loader.py` 9 条：row_map/行序/GAE/adv・ret/minibatch/demo RNG/kickstart/最终权重逐字节一致；quota 截断在 GAE 前；drop_last / 单 shard 不裁尾；v6 可选列；混装拒收；lazy 不调用 `tensored_chunks`（逐批 mb 行）；491k/mb=1024 峰值公式 ≈1.0 GiB ≤2 GiB；合成语料 gather 冷/热 ≈693/654 MB/s。
  - **未完成**：真机 35×20 的 scan+GAE/H2D/update 端到端墙钟（需真语料/GPU，**用产线的 ≥16 GB 卡**）；上卡前显存预检尚未实现；默认值仍 eager。
  - **D8/D10 读数（2026-11-16）**：`per_step_kl` / `clip_fraction` 已进 engine 逐步 stats 与 PPO 收尾日志（`kl` 保留给既有熔断）；可选 `target_kl` 在 epoch 末尾中止剩余 epoch，缺省 0 = 关闭、数学逐字节不变。测试：`tests/worker/test_ppo_scalar_sync.py`。

### P3.5 lr+epochs 组合干预筛选腿（k3 B5）

- 新 `--out`，不动生产；lr 1.2e-4 → 2–3e-4、epochs 4→8；结论只叫「组合筛选」，不做单变量归因。
- **Gate**：1× 腿正向（五点窗不劣化）且 per-step KL 进目标带；否则三臂维持 1.2e-4/4/1024，不先验加 lr。

### P4 35×5 pilot（冻结调度 + 单训练 seed）

- 课程 `rotation_per_it=35`、**固定样本量**，`target_transitions=122,880`；replay 路径；实验臂 `eval_every=1` + `eval_at`（A 5/10/15/20；B 1/2/3/4）；**关 kickstart、关分段 ppo_schedule**，固定 lr/epochs/mb/ent/kl/value_anchor。
- **Gate**：B 的学习曲线 vs A 不劣化（工程止损线 ≥−1pp）；B vs C 才是数据刷新主对照；C 终点配对差 + 跨 seed CI；训练墙钟（不含 eval）不退化；报告效应量/CI，不写「非劣配方」。
- **No-Go**：B 连续 2 个样本锚点 < A −2pp，或 entropy 连续下降/崩溃。

### P5 10× / 20×（pilot + 扩量 gate）

- 只有 P4 正向才做；**固定样本量**，20× 必须用 lazy；每关实际 seed 数按 `games/samples/shortfall` 记账；硬闸「35 关均有有效样本」。
- **Gate**：C 终点配对差 + 跨 seed CI 不劣化；无 OOM；转换/PPO 墙钟在预算内；数据覆盖/identity/转换失败/端到端墙钟/学习读数同时看；配方推广至少补 3 个独立训练 seed。

---

## 9. Definition of Done

- [ ] 请求 `corpus_fmt=full|replay` 两态可用；字段进 `rollout` spec + idempotency；`kind=iter`/eval 硬校验 full；端到端 `replay_corpus` 能力协商；`full` 逐字节兼容；旧 payload 可被新 worker 读；新 replay payload 对旧 worker **显式拒收**。
- [ ] P1：full 不创建 recorder/不算 tickHash/不复制 snapshot；replay dispatch 零目录；内存段==磁盘段；`ShardSegments` 身份含 `corpus_fmt`。
- [ ] P2：只切 `replay+eager`（不切 lazy）；同机同数据 vs `full+eager` 的 PPO 结果一致（固定确定性环境比最终权重或明确数值容差）；完整迭代墙钟不退化。
- [ ] P3：eager/lazy 的 `row_map`/全局行序/GAE/adv/ret/minibatch/demo RNG/最终权重对账；吞吐 gate 过（随机 gather 实测 1,749 MB/s；「冷/热 page cache」在 tmpfs 上不成立，改记 RAM 容量闸）；之后才单独切 lazy。
- [ ] sampler→trainer 的 replay 结果容器不含 obs/scalars/extra；7×5 该段 ≤2.5 MB（**实测 0.81 MB**）；35×20 ≤45 MB（**实测 16.2 MB，2.8× 余量**）。
- [ ] replay 重建 identity：真实关 + 自定义关 + hybrid opening + state_init，obs/scalars/extra/mask 逐字节一致；P0 ≥1000 局 + Bun→Node / Win→Linux 交叉；确定性 identity 失败零容差。
- [ ] 动作完整性：v6 `move/fire(1|2)/hold` 逐 tick 记录完整；opening 接管段 frames == 执行动作、`policy_mask` 复制正确、converter 不重跑 OpeningController、不拿 `a_*` 驱动世界。
- [x] 7×5 replay 路径与 npy 路径同权重/同 seed 的 PPO 指标一致（**2026-11-17 实测闭合**：真实导出 4 shard/240 样本/30 chunk，`replay→npy` 与 full 的 obs 三张 12/12 逐字节相同、chunks 全字段相同、真实 `ppo_update` 2 epoch 后权重逐字节一致、14 stats 差 0；报告 `reports/replay-vs-full-ppo-20261117.md`）。注：规模是最小 job，不是 7×5 全量；全量由 identity 350 局 + 本对账的逻辑链覆盖。
- [ ] 35×5 trainer→worker ≤4 MB（**实测折算 1.2 MB**）；35×20 ≤12 MB（**实测 6.9 MB，1.7× 余量**）；转换时间 ≤预算（**2026-11-16 重定：池化 ≈43 s（低配 4 核，宽度=`effective_cores()`，高配再降）/ 纯转换 88 s 单核；obs 3.97 GB 写盘；重哈希不进预算** —— obsHash 由转换器在转换循环里增量算，落盘后无任何生产路径会重扫 obs.npy）。
- [ ] lazy loader：491k 样本峰值 RAM ≤2 GB（tmpfs 下 obs 3.97 GB 另计、不可豁免）；**上卡前显存余量预检**（产线 ≥16 GB，不做小卡真机验证）；eager 路径不变。
- [ ] 三臂 pilot：冻结调度；B 学习曲线 + C 终点配对差/CI；报告效应量/CI，不写「非劣配方」；生产 eval 口径不变，实验臂 `eval_at` 样本锚点；训练墙钟不含 eval；配方推广至少 3 个独立训练 seed。
- [x] 覆盖度：**2026-11-17 实盘报表** `reports/coverage-g5-lives-700-20261117.md`（700 shard / 35 关 × 20 seed / `target_transitions=491520`）：每关 games / unique seeds / trim 前后 samples / SHORT / 丢弃整局逐关列出；**35/35 关有有效样本、700 个唯一 `(stage,seed)`、0 重复 ⇒ 硬闸 PASS**；trim 前 503,963 → 后 452,041、丢弃整局 47；**11 关 SHORT（读数，不是失败）**。工具 `nn-training/tools/coverage_report.py` 可复跑，单测 3 例钉住 SHORT/硬闸语义。
- [ ] 墙钟账覆盖两条传输腿 + 全部阶段；eval 单列；生产收益看完整迭代墙钟。
- [ ] replay/npy/ts_code 复用当前 iteration 保留策略（用户拍板）；replay 是临时训练物料，不承诺长期可重建；DoD 不写笼统「无数据丢失」。
- [ ] `bun run check` 绿；`bun run build` 过；子项目（`dashboard/**` 如改动）各自门禁绿。
- [ ] 无新增 Simulation `Math.random()` / 隐藏状态 / canvas UI；任务侧一字未动。

---

## 10. 风险与回滚

| 风险 | 影响 | 缓解 |
|---|---|---|
| 跨运行时确定性（`Math.hypot`） | worker 重建 obs 与节点不一致，静默错训/重采 | P0 ≥1000 局 + Bun→Node / Win→Linux 交叉；tickHash + obsHash 双闸；若 hypot 分叉，改平方距离属玩法改动，另立新纪元 |
| TS code / bundle 版本漂移 | 旧 replay 无法用新代码重建 | trainer 预构建 bundle + job 附件；身份键 `ts_code_sha256`；取不到即拒跑；replay_meta 记录 codeSha/bundle sha |
| worker 无 Bun/Node | 转换无法启动 | 复用 `rollout.ipynb`/`rollout_node._ensure_bun` 安装链；Bun 失败但 Node ≥22 ⇒ 用 Node 跑预建 bundle；两者皆无 ⇒ fail loud |
| 2 vCPU 争用/写盘 | 转换拖慢 PPO/loader | 转换先于 PPO（pre-pass）；**接长驻池 + 用完所有核**；**写盘几乎免费**：同批 120 shard 实测 tmpfs 80 ms/shard vs `/tmp` **86 ms/shard（+7.5%）** —— 写回异步 + 4 worker 重叠把 27 ms/shard 的持续带宽成本吃掉了 |
| 磁盘 | 35×20 npy **3.97 GB**/轮 | 复用当前 iteration 保留策略；npy 按现有 prune 清理；`disk_free` 预检 |
| loader 重构回归 | PPO 数值/顺序变化 | 测试绿后切 lazy（用户拍板）；`--ppo-loader=eager` 回滚；五条对账 |
| 大 batch 样本效率 | C 在冻结池上 1920 步后 ratio 漂移/clip 饱和 | 三臂等样本实验；per-step KL；若 C 劣化，先缩池/降 epochs，不直接加 lr |
| 无 it 内 KL 护栏 | C 的 1920 步没有 target_kl 早停 | 逐 epoch KL/clip/ratio 读数 + 可选 it 内中止阈值；否则熔断只在 491k 样本后响 |
| 请求/结果格式不符 | 缺 obs 的 shard ⇒ 0 shards ⇒ SystemExit + 浪费派发 | `corpus_fmt` 进 spec/manifest/idempotency；端到端 capability 协商；清晰拒收文案 |
| 零落盘契约被破坏 | dispatch 腿被迫落盘/段身份错配 | replay bytes 走内存 `files`；磁盘/内存同一序列化；`ShardSegments` 身份加 `corpus_fmt`；零目录测试 |
| 转换 shard 混合状态 | 半写目录被当完整 shard | shard 级临时目录 + 完成清单 + 原子 rename；只复用全匹配产物；故障注入 |
| lazy 吞吐未证 | 数值对但墙钟退化 | P3 冷/热 cache 端到端 benchmark；吞吐退化阈值实测后填 |
| 单训练 seed 结论外推 | 把 pilot 当配方结论 | 标 pilot；报告效应量/CI；配方推广至少 3 个独立训练 seed |
| 固定样本量覆盖偏离 | 号称 35×20 但实际缺关/缺 seed | 每轮报告 games/unique seeds/SHORT/丢弃；硬闸 35 关均有有效样本 |
| 动作捕获不完整 | hold/fireBefore 丢失，replay 分叉 | v6 sticky v3 头；action-capture 测试；converter 只认 ReplayInput |
| opening null-move 语义（已实测） | `hold=1+move=up` 在 cooldown 允许时转向 up（6 关 30 null tick 中 4 次） | replay 记录实际执行；另立 opening 修复腿（映射当前朝向）A/B 后再入生产 |
| P2/P3 同时切两个开关 | 退化时无法归因 | P2 只 replay+eager；P3 才 lazy；回滚点独立 |
| 固定样本量的 seed 数不足 | 35×20 不能保证 20 seeds/关 | 记录 `games/samples/seed 列表/shortfall`；不承诺、不补造；需要 fixed games 时另立 plan |
| 体积估算偏差 | gate 假红/假绿 | P0 逐成员字节分解；gate 留 ±30% 裕量 |
| 实验臂 eval 派发 | B/C 无 eval 点 | 实验臂 `eval_every=1` + `eval_at`；生产口径不变；C 终点 CI |
| schedule 未按样本求值 | C 整段吃 it1 kickstart/lr | 首轮三臂**冻结调度**；`it_equiv` 只作未来生产功能，需真 PPO 集成测试 |

**回滚**：`--corpus-fmt full`（默认）+ `--ppo-loader=eager` = 逐字节旧路径；不 bump shard schema；不删旧 npy 路径。replay 是 additive 产物，回滚不影响已训练权重。

---

## 11. 待评审问题（需要拍板）

### 11.1 已拍板（2026-11-15）

- **采样口径**：固定样本量，不做 fixed games；35×20 只记录 seed shortfall，不承诺 20 seeds/关。
- **VRAM**：按产线 **≥16 GB** 设计（最坏全量上卡 ≈5.5 GB）；上卡前查余量，不足响亮拒绝/切 lazy，OOM 用 `--mb 512` 回退；梯度累积另立实验。
- **训练超参**：首轮 A/B/C **冻结调度**（关 kickstart、关分段 `ppo_schedule`，固定 lr/epochs/mb/ent/kl/value_anchor）；lr 2–3e-4 / epochs 4–8 作为独立 1× 组合筛选腿；KL 判据用 per-step / 每 24.5k 样本累计 KL（§11.3 已确认 a）。
- **eval**：生产课口径不变（`eval_every=5 it`、网格/seed/难度不变）；实验臂单独 `eval_every=1` + `eval_at` 样本锚点；C 只有终点单点 + CI（§11.3 已确认 a）。
- **请求格式**：任务/rollout spec 显式 `corpus_fmt=full|replay`；sampler→trainer 与 trainer→worker 两段都按它走。
- **metrics 传输**：先原样带 `metrics.npy`（trainer 算 reward），不做 reward 前移、不做 delta 编码；35×20 的 sampler→trainer 约 30–35 MB（按 metrics 1.31 MB/24.5k 实测外推），等真成瓶颈再优化。
- **worker 运行时**：参照 `ipynb/rollout.ipynb` 的 Bun 安装链（`remote/rollout_node.py::_ensure_bun` / `_install_bun_mirror`，镜像源 + 官方 curl 兜底），复用安装包/缓存；Bun 装失败但本机有 Node ≥22 ⇒ 用 Node 跑 bundle；两者都没有 ⇒ 响亮失败。
- **lazy loader 放量**：先独立切 `replay+eager`（P2），再在 P3 测试绿后切 lazy；保留 `--ppo-loader=eager` 回滚。
- **数据保留**：复用当前 iteration 保留策略，不新增 replay/ts_code 保留规则；npy 按现有 prune 清理，replay 跟当前 it 产物同生命周期。
- **7×5 生产切换**：P2 测试绿后 7×5 生产课只切 `corpus_fmt=replay`（仍 eager），**不在此阶段切 lazy**；`corpus_fmt=full` 保留回滚。
- **格式字段**：`corpus_fmt` 进 `rollout` spec，并进 idempotency key；不进 `corpus_fp`。
- **kind=iter / eval**：硬校验强制 `full`；请求 replay 直接拒。
- **三臂比较**：首轮是 pilot（单训练 seed），主读 B 学习曲线 + C 终点配对差/CI；配方推广至少 3 个独立训练 seed；不按 it 数比较（§11.3 已确认 a）。

### 11.2 无需再拍板（实现细节，按推荐执行）

1. `corpus_fmt` 校验落在 `common/manifest.py`（rollout spec 白名单）与 `common/shard.py`（结果文件集）。
2. 三臂实验同时记录 it 数/墙钟，但主判按相同样本锚点；报告里注明 C 是终点单点比较。
3. lazy 切换后连续 2 个五点窗盯 KL/entropy/数据顺序对账，异常回滚 `--ppo-loader=eager`。
4. 7×5 生产切 replay 后，若出现转换失败/跨平台 identity 问题，立即用 `corpus_fmt=full` 回滚并保留 replay 产物排障。
5. 首轮三臂冻结调度（k3 B3）；`it_equiv` 只作为未来生产调度功能，不做公式冒充。
6. 零落盘 bytes 入口、`ShardSegments` 身份、端到端 capability、shard 级事务按 D2/D4/B2/B6/B7 执行，P1/P2 gate 必过。

### 11.3 评审后重新拍板（已确认，2026-11-15）

1. **实验臂 eval 派发**：确认 **a**——实验臂 `eval_every=1` + `eval_at` 样本锚点；A 5/10/15/20、B 1/2/3/4；C 只有终点单点 + CI；生产 eval 口径不动；墙钟单列 eval。
2. **KL 判据口径**：确认 **a**——per-step / 每 24.5k 样本累计 KL；三臂同带、同 lr/epochs/mb 起步；lr 2–3e-4 / epochs 4–8 拆独立 1× 腿。
3. 备选记录（未采用）：b=把 C 拉长到 5 it（预算 ×5）；c=只做 A/B 五点窗、C 只报终点；KL 备选 b=维持每 it 带、c=两者都报但 per-step 判。

---

## 12. 被否决的替代方案

- **只发 replay，不发标签**：worker 需重算前向 + metrics，2 vCPU 上 19–39 s + 12–25 s，吃掉全部收益。否决。
- **把仿真搬到 GPU（JAX/PyTorch 向量化环境）**：第二个引擎，违反 One Author/Determinism；工程量与风险远超收益。否决。
- **自定义 obs delta/bitpack 编解码**：能降 wire 但降不到 20× 量级，且要维护第二套 schema；作为 replay 不可行时的 fallback，不作为主方案。
- **只换 zstd/lz4**：能把 pack 12 s 降到 2–3 s，但 payload 仍 ~21/86 MB，解决不了 35×20。作为 P0 可选的低风险顺手项，不是主方案。
- **直接把 `chunk_episodes` 改成 lazy 但不做 replay**：解决内存，不解决 wire；可作为独立小改先上，但不满足 20× 传输目标。
- **用 `est_samples_per_game` 承诺固定 seeds**：局长随 seed 方差大，不可保证。否决。
- **fixed games（每关固定 20 局）**：用户拍板固定样本量，本 plan 不做；需要 20 seeds/关时另立 plan。
- **生产 eval 改成按样本**：用户拍板生产 eval 不变；实验臂用 `eval_at` 样本锚点属于实验设计，不改生产 `eval_every=5`。
- **由 packer 事后看 `replay.json` 猜格式**：用户拍板请求显式 `corpus_fmt`；事后猜只保留为本地 full shard 的兜底。
- **本次做 reward 前移 / metrics delta 编码**：用户拍板先原样带 `metrics.npy`（trainer 算 reward）；若未来 35×20 的 sampler→trainer 真成瓶颈，再另立 plan 评估（避免第二份 reward 实现或新 codec）。
- **P2 同时切 replay + lazy**：一次只动一个开关；P2 = replay+eager，P3 = replay+lazy，回滚点独立。
- **每局把 replay.json 落盘再传**：dispatch 腿必须走零落盘内存 bytes；磁盘只用于 local 腿。
- **首轮用 `it_equiv` 公式冒充 it 内调度**：首轮冻结调度；真正的 it 内样本轴调度另立功能 + 真 PPO 集成测试。
- **单训练 seed 直接下配方结论**：首轮只作 pilot；配方推广至少 3 个独立训练 seed。

---

## 13. 参考与复现

- 复现测量：
  - `bun tmp/bench-replay-convert.ts replays/hard-s2013-died-l0-t420-seed2000000001.replay tmp/g5-safe/weights.json`
  - `bun tmp/bench-replay-identity.ts`（真实关）· `bun tmp/bench-replay-identity.ts --custom`（s2030）
- 关键代码：
  - `tools/sim/export-rl-rollout.ts:1248-1277`（runOne）· `:2085-2174`（shard 写盘）
  - `src/replay/InputRecorder.ts:74-113` · `src/replay/ReplayInput.ts:27-54` · `src/replay/pack.ts:9-24`
  - `src/snapshot/WorldSerializer.ts:130-245`（restoreWorld）
  - `nn-training/remote/payload_slim.py:159-239`（ShardSlim.member_bytes）
  - `nn-training/remote/worker.py:720-800`（kind 分叉/pre-pass 位置）
  - `nn-training/remote/download.py:1073`（`_ensure_ts_code`）
  - `nn-training/worker/ppo/np_core.py:484-527`（当前 chunk_episodes）
  - `nn-training/worker/ppo/trainer.py:20-41` + `worker/ppo/engine.py:347`（当前 eager tensored_chunks）
  - `nn-training/worker/data/npyio.py:198-286`（lazy 内存预检）
  - `nn-training/worker/data/dataset.py:65-87, 209-217`（lazy mmap）
  - `nn-training/worker/train/bc.py:706-707`（逐批上卡）
  - `nn-training/trainer/loop_remote_job.py:73-103`（trim；只读 nSamples）· `trainer/loop_dispatch.py:70-84`（eval 派发）
  - `worker/ppo/config.py:39`（per-tick 无 target_kl）· `src/game/SimulationEnemies.ts:526,599` / `SimulationPlayer.ts:416`（Math.hypot）
  - `nn-training/trainer/loop_steps.py:340-368`（schedule 轮边界求值）· `worker/schedule.py::kickstart_coef`
  - `nn-training/remote/payload_pack.py::ShardSegments`（零落盘段账身份）· `docs/nn/training-stack.md §48` · `tests/remote/test_payload_parts.py`
  - `nn-training/curricula/g5-aim2.jsonc:61-64, 100-104, 240-248`（KL 目标 / 轮换 / lr）
- 评审：`plan/replay.review-qw.md`（v4 评审；v6 处置见 §1.8）· `plan/replay.review-k3.md`（v6 评审；v7 处置见 §1.9）
- 相关计划：`plan/it-stage-ratation.plan.md` · `plan/autonomous-cloud-overrides.plan.md` · `docs/nn/remote-transport.md` · `docs/nn/training-stack.md`

---

## 14. 实施后要补的 DECISIONS（草稿，待实施时落）

1. 为什么 replay + 非 obs 标签，而不是 replay-only（前向/metrics 在 2 vCPU 上的实测）。
2. 为什么请求显式 `corpus_fmt=full|replay`，而不是 packer 事后猜；为什么 `kind=iter`/eval 强制 full。
3. 为什么 worker pre-pass 只重建 obs/scalars/extra，不重算 reward/mask（reward 由 trainer wire-diet 折出，mask 随标签传）。
4. 为什么懒加载复用 BC 的 mmap 模式，而不是继续 `chunk_episodes` concatenate（491k 样本 3× 峰值实测）；为什么逐批上卡（降低峰值显存；**产线平台均 ≥16 GB，小卡兼容不再是门**，见 §5.3）。
5. 为什么固定样本量、接受 35×20 的 seed shortfall（用户拍板）；为什么不承诺 20 seeds/关。
6. 为什么 eval 保持 `eval_every=5 it` 不变，以及 20× 下 eval 变稀的后果。
7. 为什么 sampler→trainer 先原样带 metrics（不做 reward 前移/delta 编码），以及未来何时再评估。
8. 为什么 worker 运行时复用 `rollout.ipynb` 的 Bun 安装链、Bun 失败回落 Node；两者皆无即失败。
9. 为什么测试绿后 lazy 也切 7×5 生产课，以及 eager 回滚条件。
10. 为什么 replay/npy/ts_code 复用当前 iteration 保留策略，不新增保留规则。
11. 为什么 P2 绿后 7×5 生产课立即切 replay；`full` 回滚条件。
12. 为什么 `corpus_fmt` 进 rollout spec + idempotency，而不进 `corpus_fp`。
13. 为什么 `kind=iter`/eval 硬校验 full。
14. 三臂等样本实验的结论（按相同样本锚点；B 学习曲线 + C 终点 CI；1×20 vs 20×1 是否近似，per-step KL 与 lr/epochs 的实际取值）。
15. 为什么三臂的变量是数据刷新频率/on-policy 滞后，不是 minibatch 大小；为什么三臂起步同 lr/epochs/mb。
16. 为什么 KL 判据用 per-step / 每 24.5k 样本，per-it KL 只作读数。
17. 为什么实验臂用 `eval_every=1` + `eval_at`，生产 eval 口径不动；C 只报终点 CI。
18. 为什么 `Math.hypot` 若跨运行时不一致要另立新纪元（平方距离属玩法改动）。
19. 为什么 converter bundle 由 trainer 预构建并随 ppo job 附件下发；身份键用 `ts_code_sha256`。
20. 为什么 P0 identity 扩到 ≥1000 局 + Bun→Node/Win→Linux 交叉；体积 gate 从逐成员分解出。
21. 为什么实验臂 eval 用 `eval_every=1` + `eval_at` 样本锚点、生产口径不动（§11.3 a）。
22. 为什么 KL 判据用 per-step / 每 24.5k 样本、三臂同超参起步、lr 提升拆 1× 腿（§11.3 a）。
23. 为什么 P2 只切 replay+eager、P3 才切 lazy（k3 B1；一次只动一个开关）。
24. 为什么 replay bytes 走零落盘内存入口、`ShardSegments` 身份加 `corpus_fmt`（k3 B2）。
25. 为什么首轮三臂冻结调度、`it_equiv` 只作未来功能（k3 B3）。
26. 为什么 lazy 需要 `row_map` + 冷/热 cache 吞吐 gate（k3 B4）。
27. 为什么三臂首轮是单训练 seed pilot、配方推广要 3 seeds（k3 B5）。
28. 为什么转换要 shard 级事务、capability 要端到端协商、覆盖度要每轮报告（k3 B6/B7/4.4）。
29. 为什么 replay 是临时训练物料、不承诺长期可重建（k3 4.5 + 用户保留策略）。
30. 为什么 v6 的 move/fire(1|2)/hold 必须走 v3 位域逐 tick 记录，converter 必须用 `ReplayInput` 而不是 `a_*` 标签。
31. 为什么 OpeningController 不重跑、frames 是唯一真源；opening 段 `policy_mask=0`、`a_*` 允许与执行动作不同。
32. `opening-null-move` 探针结论（已实测）：6 关/1,974 opening tick/30 null tick；v6 4 次转向 up、legacy 0 次、位移均 0 ⇒ v6 null-move 是「转 up+不前进」，不是原地不动。另立 opening 修复腿（映射当前已提交朝向 + hold=1）A/B 后再入生产。
