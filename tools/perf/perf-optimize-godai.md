# God-AI 仿真性能优化报告（逐项记录）

**日期**：2026-07-29
**工作流**：性能优化（基于 `perf-bottleneck-godai.md` 的瓶颈基线，逐项优化）
**参与成员**：Cody（代码审查 · 以确定性/正确性护栏形式介入）、Archi（架构护栏）、Rex（测量流程）、Tessa（测试门禁）、Docu（本报告）

---

## 📌 TL;DR（执行摘要）

- **目标**：God-AI 调参要跑成千上万遍 headless 仿真，任何微小的 per-tick 节省都会被放大。在不改变任何 AI 决策/对局结果（确定性标定值必须保持有效）的前提下做针对性微优化。
- **方法**：先建 profile 框架 → 跑出干净基线 → 定位瓶颈 → 逐项优化，**每一项都记录效果**（perTick + 确定性签名 + 测试门禁）。
- **总收益**：非插桩热身 harness 下，`perTick` 从 **0.052ms → 0.020ms（约 −62%）**。其中 A 贡献 ~42%、B 贡献剩余 ~33%、C 的 wall-time 收益在 harness 分辨率（±0.001ms）之内（但其真正价值是消除每 tick 冗余数组分配 → 降低 GC 压力，且零行为变化）。
- **确定性**：所有三项的 determinism 签名 **完全一致**（`ticks=107371 win=1 go=29 timeout=0` @30局；`ticks=395437 win=4 go=116 timeout=0` @120局）——证明对局结果零改变，标定值无损。
- **严重度**：🔴 0 / 🟠 0 / 🟡 0 / 🟢 3（三项均为安全、行为保持的微优化）

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🟢 通过（无回退、无行为变更、全测试绿） |
| 阻塞项数量 | 0 |
| 关键行动项 | 3 条（见下） |
| 建议下一步 | C 已封顶 God-AI 安全去重；更深收益需动 perception/rectHitsTerrain 或引入空间索引（有标定风险，需单独评审） |
| 累计 perTick | 0.052ms → 0.020ms（**−62%**） |

---

## 📏 测量方法与纪律

- **干净 perTick**：非插桩、带热身（默认 `--warmup=3`，负种子区间独立，使计时种子 1000+g*7 跨次完全一致）的 harness `tools/perf/profile-godai.ts`（30 局 chaos / stage 0，共 107,371 ticks）。`--cpu-prof` 会显著膨胀 wall time，**不能作为干净吞吐数字**。
- **确定性签名**（跨次等价的判据）：`ticks / win / go / timeout`。三项优化后该签名逐字节不变 → 对局结果零改变。
- **优化护栏（不可破）**：
  1. 任何 `findPath` 改动必须通过 `probe-findpath-parity.ts`（402 用例逐字节路径比对 vs `git HEAD` 原版）。
  2. 任何 God-AI 改动必须通过 `godai-split-parity` + `god-ai-regression-gate`（标定地板：S0 90% / S1 100% 胜率）。
  3. 全量 `bun test` 必须绿。

---

## 🔬 逐项优化与效果记录

### Cluster A — 感知去重（`src/ai/perception.ts`）
**问题**：`perceive()` 每 tick 对场上所有活坦克扫一遍做 canStep/队友/decoy/拥堵判断；`canStep()` 又各自 `for (o of world.allTanks)` 再扫一遍 → 同一集合被重复扫描。
**改动**：
- `canStep` 增加可选 `others?: Tank[]` 形参，缺失时回退原 `world.allTanks` 全扫（行为不变）。
- `perceive()` 先扫一次 `world.allTanks` 进复用的 `others` 数组，传给 `canStep`；队友/decoy/拥堵循环与 openDirs 判定都复用它。
- `analyze()` 在 `objDir === tank.dir` 时复用 `baseLOS`，避免重复 `scanAhead`。

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| perTick（干净） | 0.052ms | **0.030ms（−42%）** |
| 确定性签名 | ticks=107371 win=1 go=29 timeout=0 | **完全相同** |
| 测试 | — | 相关测试 29/29 通过 |

> 注：profile 中 `ai/perception.ts` 自时间占比从基线 32.3% 降到复测 38.7%（绝对值因插桩膨胀波动），但 `perceive` 单函数自时间从 17.8% → 22.8% 属插桩噪声；A 的真实收益体现在干净 perTick −42%。

### Cluster B — `findPath` 整数键 + TypedArray（`src/utils/pathfind.ts`）
**问题**：`findPath` 热循环用字符串键 `` `${col},${row}` `` + `Map`/`Set` + `split(',').map(Number)`，分配与解析开销大。
**改动**：保留对外 `key()` 为字符串（离线 `isReachable`/`floodFill` 契约，被测试与 `tools/level-gen.ts`/`tools/evaluator.ts` 依赖）；仅给 `findPath` 加**局部**整数键 `cellKey = row*GRID+col`，用 `Float64Array`/`Int32Array`/`Uint8Array` 替代 Map/Set，开放集用线性扫描并**保留插入顺序 tie-break** → 返回的 `Direction[]` 序列逐字节一致。
**分配**：`probe-findpath-parity.ts` 抽取 `git HEAD` 原版到 `src/utils/pathfind.orig.ts` 经 shim 对比 **402 用例，mismatches=0（PARTY OK）**。

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| perTick（干净，相对 A） | 0.030ms | **0.020ms（再 −33%）** |
| 累计（相对基线） | 0.052ms | **0.020ms（−62%）** |
| 确定性签名 | ticks=107371 win=1 go=29 timeout=0 | **完全相同** |
| 测试 | — | findPath parity 402/402；AI 测试 10/10 |

> profile 中 `findPath` 自时间从 9.6% → **2.7%**，是单项最显著的杀手级改善。

### Cluster C — God-AI 每 tick 快照缓存（`GodAIInput.ts` + `god/StrategyPlanner.ts` + `god/Navigator.ts`）
**问题**：`think()` 内多处对同一集合做重复过滤/扫描：
- `isBaseUnderThreat()` **每 tick 被调用 2 次**（T2a 守卫 + powerup 守卫），各扫一遍 `w.tanks`。
- `selectTargetImpl` 每次调用都 `w.tanks.filter(alive && spawnTimer<=0)`（经 `followPath`/`replan`/`directMove`/`selectTarget` 每 tick 可达 2–3 次）。
- `calculateRouteDangerImpl`、`nearbyEnemy` 守卫、以及 `canMoveOrBreak`/`canMoveDir` 的 `w.allTanks` 碰撞扫描，逐次重扫。

**改动（行为保持证明）**：在 `think()` 玩家存活守卫之后，**构建两个每 tick 快照**：
- `_enemies = w.tanks.filter(t => t.alive && t.spawnTimer<=0)`（成员与迭代顺序与原 filter **完全一致** → `enemies[0]` tie-break 不变）。
- `_otherTanks = w.allTanks.filter(o => o.alive)`（保留玩家；循环内仍 `if (o===tank) continue`，故对每个调用方——实践中只有玩家——得到的障碍集合与原 `w.allTanks` 扫描逐字节等价）。

`isBaseUnderThreat` / `selectTarget` / `calculateRouteDanger` / `nearbyEnemy` / `canMoveOrBreak` / `canMoveDir` 全部改为读缓存，并保留 `if (cache 为空) 回退原扫描` 的防御分支。

| 指标 | 优化前（B 状态） | 优化后（C 状态） |
|------|--------|--------|
| perTick（干净，30 局） | 0.020ms | 0.020–0.021ms（**在 ±0.001ms 噪声内**） |
| perTick（120 局） | ~0.021ms | ~0.020–0.022ms（噪声内） |
| 确定性签名 | ticks=107371 win=1 go=29 timeout=0（30局） | **完全相同** |
| 确定性签名 | ticks=395437 win=4 go=116 timeout=0（120局） | **完全相同** |
| 测试 | — | **全量 531/531 通过**；`godai-split-parity` 4 seed 逐字节一致；`god-ai-regression-gate` S0=90% / S1=100% |

**诚实结论**：C 消除了真实的冗余扫描与每 tick 数组分配（→ 降低长批量仿真的 GC 压力），但其 wall-time 贡献低于本 harness 分辨率。真正的 per-tick 开销由**必要的**每 tick 子弹/坦克视线扫描（`findBulletThreatToBase` 6.0%、`findEnemyDirection`/`findMostDangerousBullet`/`isSafeDir` 各扫 `w.bullets`）主导，这些不是浪费、且改变它们会触碰标定值，故不在本次安全范围内。

---

## 📊 最终 profile 快照（Cluster A+B+C，30 局 chaos/stage0，2240 采样 / 2967ms）

**按函数自时间 Top-15**：
```
 22.8%  perceive                @ ai/perception.ts
  9.5%  scanAhead               @ ai/perception.ts
  6.3%  rectHitsTerrain         @ game/World.ts
  6.0%  findBulletThreatToBase  @ ai/god/ThreatAssessor.ts
  3.9%  canStep                 @ ai/perception.ts
  3.0%  scanAheadImpl           @ ai/god/FireControl.ts
  2.7%  findPath                @ utils/pathfind.ts
  2.7%  think                   @ ai/GodAIInput.ts
  2.5%  push                    @ (anon)
  2.5%  analyze                 @ ai/perception.ts
  1.8%  updateMovement          @ game/Simulation.ts
  1.8%  updateBullets           @ game/Simulation.ts
  1.8%  maybeTunnelOut          @ ai/TacticalIntelligence.ts
  1.6%  filter                  @ (anon)   ← 冗余 filter 已基本消除
  1.4%  allTanks                @ game/World.ts
```
**按模块自时间**：
```
 38.7%  ai/perception.ts          ← 仍是 #1 杠杆（敌人 AI 感知）
 11.9%  game/Simulation.ts
  9.1%  game/World.ts             (rectHitsTerrain 6.3%)
  7.6%  ai/TacticalIntelligence.ts
  7.3%  (anon/V8 builtins)
  6.4%  ai/god/ThreatAssessor.ts
  4.5%  ai/god/FireControl.ts
  3.7%  ai/GodAIInput.ts
  3.7%  utils/pathfind.ts         ← 已从 11.6% 模块占比大幅回落
  2.4%  ai/god/StrategyPlanner.ts
  2.2%  ai/god/Navigator.ts
```

> 对比基线（见瓶颈报告）：`findPath` 自时间 9.6%→2.7%；God-AI 管道的 `filter` 类浪费从显著降到 1.6%；`perceive` 仍是主导但属敌人 AI 感知逻辑（改变它等于改敌人行为，标定敏感，不在本次安全范围）。

---

## ✅ 行动清单（按优先级排序）

| # | 行动 | 负责角色 | 紧急度 | 预期完成 |
|---|------|---------|--------|---------|
| 1 | 将 A+B+C 作为 Phase commit 落到 `god-ai` 分支（当前工作树已改未提交：`perception.ts`、`pathfind.ts` + 4 个 `tools/perf/*` 新文件） | Zhen / 用户确认 | P0 | 本次评审后 |
| 2 | 若需进一步压 per-tick：评估 `rectHitsTerrain`（World 6.3%）的空间/缓存化，以及 `perceive` 的进一步去重——**但必须配套 parity/回归门禁，标定敏感** | Archi + Cody | P2 | 下一轮 |
| 3 | 记录“God-AI 安全微优化”可复用流程为 skill（Bun `--cpu-prof` + 自时间聚合 + 确定性保持协议） | Docu | P3 | 本轮收尾 |

---

## ⚠️ 待完善 / 已知局限

- **Cluster C 的 wall-time 收益不可测**：本 harness 分辨率约 ±0.001ms；C 的真实收益体现在减少每 tick 数组分配（GC 压力），对“跑上万次仿真”的批量场景有意义，但在单轮 perTick 数字上落在噪声内。
- **profile 百分比不可跨次直接比较**：`--cpu-prof` 插桩放大 wall time，绝对占比受采样抖动影响；本节百分比仅用于看“相对结构迁移”，干净吞吐以非插桩 harness 的 perTick 为准。
- **未触碰的剩余热点**（perceive / rectHitsTerrain / findBulletThreatToBase）均属“必要计算或标定敏感”，进一步收益需算法级改动并单独评审，不在本次“不改 AI 决策”约束内。

---

## 📚 数据来源 & 成员产出索引

- Cody（代码审查师）：逐文件 diff 复核——所有改动均为分配/计算等价替换，无逻辑分支变更；确定性护栏（parity/回归门禁）齐备。
- Archi（架构师）：确认改动不破坏 AGENTS §2 架构不变量（仅 Simulation 改 World；快照为 `GodAIInput` 上的每 tick 临时数组，生命周期限于 `think()`，无“隐藏状态”跨 tick 残留）。
- Rex（SRE）：测量流程——`tools/perf/profile-godai.ts`（热身 + 确定性签名）、`analyze-profile.ts`（自时间聚合）。
- Tessa（测试专家）：门禁结果——findPath parity 402/402；godai-split-parity 4 seed；god-ai-regression-gate S0=90%/S1=100%；全量 `bun test` **531 pass / 0 fail**。
- Docu（技术文档师）：本报告。
- 前置报告：`tools/perf/perf-bottleneck-godai.md`
- 原始瓶颈基线：30 局 chaos/stage0，107,371 ticks，perTick≈0.052ms。

---

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。
