# 按样本量动态采集（Dynamic Rollout Volume）——全动态版实施计划

> 状态：**已实现并验收**（2026-09-15；P0+P1+P2 完成，见 `docs/nn/training-stack.md` §7
> 与 `DECISIONS.md §2026-09-15-goalnn-dynamic-rollout-volume`）。模拟课程落
> `nn-training/e2e/fixtures/tiny-vol.jsonc`（禁入 `curricula/`）。
> 关系：本计划是**全动态版**（轮中按 transitions 配额补采）；折中版（轮首按
> trailing 均值反解局数）见 `x3-power.jsonc`「批量附录」——若本计划受阻，
> 回落折中版仍能拿到 95% 收益。评估（eval）永不动：固定种子×固定局数是
> 一切可比性的地基（评审 §5.2）。

---

## 0. 一句话目标

课程用 `target_transitions`（/轮）代替固定局数做采集配额；loop 在轮中按
**已结算 transitions** 补波次，直到分关配额达标；同 `(runSeed, it)` 下
（含断点续跑） subunit 逐字节可复现；缺席该键 = 老 `seed_rotate` 语义逐字节不变。

**非目标（v1 不做）**：stream/double-buffer 路径、BC 语料采集、eval 改动、
dashboard 必改项、gate 改动、minibatch/PPO 超参联动。

---

## 1. 背景与动机（证据链，执行者勿重证，直接引用）

1. PPO 吃的是 transitions，不是局数。**量纲（2026-09-15 T9 勘误）**：账本口径的
   transitions = `nSamples` 之和（samples，不是 ticks）。x3：240 局/轮、局均 967 ticks
   但 samples/ticks≈0.1007 ⇒ **≈2.33 万 transitions/轮**（不是 23 万——那是 ticks）；
   c4-dodge 先例 600 局×1100+t ≈ **6.6 万** transitions/轮（原写 66 万+ 同为 ticks）。
   roadmap“600-1000 局”实为 transition parity 要求（对账结论）。
2. 固定局数下 transitions/轮随局长漂移（x3 局均 700-1150t ↔ 70-115 samples/局），
   PPO 更新量、advantage 归一稳定性跟着漂。
3. 反例在先：x2-acbc 3 倍密度 30 轮 pooled −3/400——**加量解决的是方差，
   不是信号**。本计划只承诺“量准”，不承诺“涨点”，DoD 里不许写胜率条款。

---

## 2. 语义规格（先定死，再写码；改动任何一条 = 新实验，§15.5）

### 2.1 课程键（全可选；缺席 = 老行为逐字节不变）

```jsonc
{
  // 本轮目标 transitions（已结算 shard 的 nSamples 之和口径 = samples，不是 ticks）。
  "target_transitions": 600000,
  // 局均 **samples** 估计 = 局均 ticks / K（首轮/无历史时用；之后用 jsonl 的
  // trailing **samples** 均值覆盖）。⚠ 旧键名 est_ticks_per_game 把 ticks 填进
  // samples 分母 = 10× 误采（600000 目标只兑现 ~35% 就触 wave_cap）——2026-09-15
  // T9 已改名；照写旧键会在启动期响亮报错（extra=forbid）。
  "est_samples_per_game": 90,
  // 单关单轮局数硬顶（防短局 pathological 下局数爆炸；默认 = 初波×4）。
  // "max_games_per_stage": 0,  // 0 = 按默认规则（初波×4）
}
```

### 2.2 波次协议（核心）

1. **初波**：每关 `G0 = max(1, ceil(target/n_stages/est))` 局（`est` = **samples/局**，
   与 `target` 同单位——见 §1 量纲勘误），种子流与今日
   `build_pairs` 同键（`(rotateSeed, it)`），老课程行为是其特例。
2. **结算计数**：只计已结算 shard 的 `nSamples` 之和（分关累加）。
   - 掉局（dropped）：零样本，**不计入**（天然触发补采——这是特性）。
   - 超时局：transitions 是真实 on-policy 数据，**计入**。
3. **补波**：某关未达 `target/n_stages` 即补一波，波大小
   `ceil(剩余/est)`（同单位 samples），**每关独立**（短局关淹不了长局关；
   x3 acd 733t vs abd 989t ↔ samples/局差 35% 就是前车）。
   ⚠ 3 波只够抹平 **est 偏差**，抹不平 **est 量纲错**——后者是 10×，补波量
   按同一个错估缩放，永远追不上配额（T9 的成因）。
4. **终止**：各关达标即停；波次数封顶（建议 ≤3 波，防长尾抖动）；触
   `max_games_per_stage` 即停并响亮日志（配额未满，iteration 事件打标）。
5. **种子派生**：补波种子流必须与初波**独立**——
   `rng = np.random.default_rng([rotate_seed, 0xWA9E, it, stage_id, wave_idx])，
   逐关独立流。**不变量**：给 A 关加波，不得改变 B 关任何一局的种子
   （单测钉死，见 §5.2）。

### 2.3 确定性与续跑（最难啃，单独成章）

1. 同 `(runSeed, it)` + 同观测史 ⇒ 同波次序列。注意“观测史”含已结算
   transitions——断点续跑时，已落盘 shard 照今日剔除语义复用，
   **补波决策必须能 replay**：每次补波决策（wave_idx、对局表、计数快照）
   进 commit journal（WAL）；重启后按 journal 重放，不重新抛硬币。
2. `it -= 1` 原地重试语义不变：失败轮不前跳，波次计数随轮清零重算。
3. 回归铁律：`target_transitions` 缺席的课程，`build_pairs` 输出与今日
   **逐字节一致**（单测钉死，见 §5.1）。

### 2.4 身份与隔离

1. `target_transitions`（+ 规则版本常量 `VOLUME_RULE_V1=1`）进
   `corpus_identity_fp`（采样参数，与 seed_rotate 同等待遇）；改动 = 新实验，
   fresh `--out/--traj`（§15.5）。
2. eval（`eval_games_per_stage`/`EVAL_SEEDS`）零改动；`firstKillTick` 等
   玩具力学约束不变。
3. iteration 事件追加 `transitions_target` / `transitions_collected`
  （dashboard 可选消费，P3）。

### 2.5 收官报告与配额账本同源（2026-09-20 补：修 it76 监控盲区）

**问题**（x20-steady it75→it76 实测）：配额在停机前已采满 ⇒ 重启后新进程
`batches=0`，`combine_reports([])` 的除零保护把 `winRate=0.0` 写进账本——账本上
「0 局胜率 0」与「打了 N 局全输」不可区分；而盘上 238 个 shard 的 `stage_clear`
无人重聚合。配额账本（`settled_stage_totals`）**早已**以盘上 manifest 为真，
报告必须与它同源。

**解法**：`rl/reports.py::merge_volume_report(wave_combined, disk_manifests)`——
盘上有局数 ⇒ **以磁盘 combine 为报告体**，wave 只覆盖时间锚点
（`pure_collect_sec` / `weights_dist_*` / `collect_end_ts`）；接线在
`loop_core._volume_collect_continuous` 收官处（`resumed_manifests(traj/it{N}, wver,
 extra_wver, course_fp)` + stages 过滤）。**否决**「仅 wave 为空时回填」：重启后本
进程可能只补了缺口批，wave 有局数但仍小于盘上全量 ⇒ 低估胜率与局数。

**遗留（未修，与本条独立）**：continuous `start_idx` 重启后从 0 重抽种子流，首批
可能整批命中盘上已 done 的 pair（磁盘回填、不新采）；配额靠后续批推进。

---

## 3. 实施分期

### P0 纯函数 + 单测（不动 loop，独立可审）

- `volume_waves.py`（新文件，`rl/` 下，禁 torch/numpy 以外重依赖）：
  `initial_games()` / `topup_games()` / `wave_seed_stream()` /
  `terminate()` 四纯函数。
- 单测（`tests/test_rollout_volume.py`）：配额数学（整除/ceil/封顶/地板）、
  跨关独立性（A 加波 B 种子不变）、终止谓词、journal schema round-trip、
  **老行为回归**（无键课程 pairs == 今日 `build_pairs` 输出逐字节）。
- 门禁：`ruff + mypy + pytest` 该文件全绿。

### P1 loop 接线（串行路径 only；stream 保持老语义并注释）

- `rollout_phase.py`（或 loop 侧等价点）：初波沿用今日派发；结算后按 §2.2
  循环补波（波内复用今日 dist 队列/竞速/掉局语义，不另起调度器）。
- journal 落盘/重放；`max_games` 熔断日志；iteration 事件新字段。
- resume 回归：模拟 crash（删一半 shard + journal 回放）单测 + e2e 一例。

### P2 验证（证据关门）

- e2e（FakeAgent）：① 定额达成（配额 600 局等价小目标，收敛误差 ±1 波）；
  ② 跨重启一致性（杀掉重跑，同 pairs）；③ 短局泛滥关不饿死长局关。
- 微课试点：tiny-a/b 同形微课（纯课程数据行，零算法改动先例见 §36），
  5 轮验证 transitions/轮方差收敛 + wall 可预期。
- 开销实测（§16.1）：补波决策/journal 的每轮 overhead 落数。

### P3 文档与台账（可选，不阻塞）

- 课程注释模板（含开工校准公式：`seed_rotate ＝ target ÷ 关数 ÷ 局均tick`）。
- dashboard iteration 行可选展示 `transitions_collected/target`。
- DECISIONS 条目（量纲从“局”切“transitions”的宪法级记录，过 §6.3 三问）。

---

## 4. Definition of Done

- [x] 无键课程：`build_pairs` 输出与今日逐字节一致（回归单测钉死）。
- [x] 有键课程：配额达成（分关，误差 ≤1 波）；跨重启 pairs 一致；短局关不饿死。
- [x] 失败语义：掉局触发补采；触顶熔断响亮日志 + iteration 打标；全程无静默丢数。
- [x] `bun run check`（根）+ `python-gate`（nn-training）全绿；新增测试命名 mirror concern。
- [x] 不碰：stream 路径、BC 采集、eval、gate、PPO 超参、运行中课程文件。
- [x] 开销：补波决策 overhead 实测落数（要求 < 轮 wall 的 1%）——3 波包 ~754µs / 24s ≈ 0.003%。

---

## 5. 风险与回落

| 风险 | 缓解 |
|---|---|
| 补波把单轮 wall 拉长（短局 pathological） | `max_games` 硬顶 + iteration 打标 + max_hours 总闸 |
| journal 与 shard 账本分叉 | journal 只记决策输入（wave_idx/计数快照），对局以 shard 落盘为准，单源 |
| 跨关配额 vs 全局池的争论 | v1 定分关（防淹没）；全局池是 v2 议题，不在本计划 |
| 并行会话改动同一文件 | 动工前 `git pull` + 开工后第一时间跑 `bun run check`；冲突只合不覆盖 |

**回落线**：P1 受阻 → 回落折中版（轮首 trailing 均值反解局数，x3-power 头已有公式），仍解决 95% 问题。
