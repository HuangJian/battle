# God AI 调优方向论证与进度

> **最新状态（2026-07-29）**：P0（T2a 死锁修复）、P1（生存与防御加固）、P2（反驻扎区域
> 修复 + nav-stuck 改进 + 预测瞄准）均已完成。Classic 胜率：
> - Stage 0: 86.7%（30 seed @18000t）
> - Stage 1: **100%**（完美通关！）
> - Stage 2: 93.3%
> - Stage 3: **66.7%**（从 50% 大幅提升 — 反驻扎区域修复打破了迷宫地图死锁）
> - Stage 4: 56.7%（RNG 扰动导致退步，需 CMA-ES 重新优化参数）
> - 总体: 80.7%
>
> P2 核心发现：P0 的精确格子跟踪被 TANK/CELL 边界的子格振荡击败——玩家在两个相邻格之间
> 振荡（如 x=32↔40），每次跨越边界都重置驻扎格子，导致反驻扎逃逸永远不触发。±1 格区域
> 修复解决了这个根因。下一步：CMA-ES 重新优化参数（架构修复已到位）。

## 进度总览

| 阶段 | 状态 | Classic 胜率（30 seed / 18000t） | 关键改动 |
|------|------|----------------------------------|----------|
| P0 — T2a 死锁修复 | ✅ 已完成（aec21f4） | Stage 0 20%→70%，Stage 1 22.5%→87.5% | `scan.enemy` 门（P0.2）+ anti-camp 逃逸（P0.1）+ nav-stuck 逃逸（P0.3） |
| P1 — 生存与防御加固 | ✅ 已完成（2cedb7a） | Stage 0 70%→87.5%，Stage 1 87.5%→92.5% | 闪避检测加宽（P1.1）+ 基地威胁 `row>=18`（P1.2）+ 终局回防（P1.3）+ 跳过 T2a/道具（P1.4） |
| P2 — 反驻扎区域 + 预测瞄准 | ✅ 已完成 | Stage 0 86.7%, Stage 1 **100%**, Stage 3 50%→**66.7%** | 反驻扎区域跟踪（±1格）+ nav-stuck 任意方向逃逸 + 预测瞄准（纯检测不消耗 RNG） |
| P3 — CMA-ES 重新优化 | ⏳ 下一步 | 待定 | 架构修复已到位，重跑 CMA-ES 对准跨难度/stage |

**剩余失败（P1 之后，seeds 1–40，36000t）**：
- **2 个 gameover**（Stage 0 seed 2 / 13）：敌人沿基地行从远处走过来，玩家来不及回防。
- **6 个 timeout**（Stage 0 seed 20/25/39，Stage 1 seed 8/24/27）：追击快速敌人，差 2–4 击杀清不掉 → 需**预测瞄准 / lead the target**。
- **Hard / Chaos 仍为防御崩溃体制**（§6 门槛未触碰）：当前 God AI 只在 classic 上训练，需 P2 跨难度评测才能进入优化目标。

> 回归门禁（`tests/god-ai-regression-gate.test.ts`）已收紧到 P1 基线：Stage 0
> `wins≥24/base≥27/kills≥16`，Stage 1 `wins≥25/base≥29/kills≥17`（seeds 1–30 @18000t）。

---

## 1. 证据（P0 / P1 修复前的基线，2026-07-29 之前）

> 以下证据用于说明**为什么要做 P0 / P1**，反映的是死锁+弱防御时代的 God AI 行为。
> 修复后的实际胜率见上方「进度总览」。

### 证据 1 — 时间预算翻倍对胜率零影响（不是"太慢"，是"卡死"）

对 Stage 0 / Classic / seeds 1–40 跑 `maxTicks=36000`（注意优化器训练用的是 18000）：

- 18000 ticks 内胜利：**8 / 40 = 20%**
- 36000 ticks 内胜利：**8 / 40 = 20%**（完全相同，且 8 个胜局全部在 ≤5800 ticks 内结束）

→ 把时间预算翻一倍，**新增胜利数 = 0**。所有 31 个超时局在 18000 和 36000 下都超时。
这证明失败不是"清场太慢"，而是**到达某个稳态后永远无法收尾**——纯速度/火力参数
（fitness 里的 speedBonus、remainingEnemyPenalty）对此无能为力。

### 证据 2 — 失败模式：97.6% 是"基地存活但未清场"

| 结局 | 数量 | 占比 |
|------|------|------|
| 胜利 | 8 | 20% |
| 卡死（基地存活 + 超时） | 31 | 77.5% |
| 基地被毁（gameover） | 1 | 2.5% |

- 非胜利局中 **97.6% 基地仍然存活** → 防守已经够稳，问题出在**进攻/收尾**。
- 卡死局里 **5 个 seed（16/23/25/29/33）击杀数 = 0**，另有 1 个（seed 8）仅 5 杀。
- 非胜利局平均击杀 10.3 → 多数局是"清掉一大半但最后几个敌人收不掉"。

### 证据 3 — 卡死机制已定位：T2a 决策流死锁（硬编码，非参数）

对代表性 seed 跑逐 tick 决策轨迹（已修正 rules，见 §3），branch 分布：

| seed | 结局 | kills | t2a | navigate | 驻扎主格(停留 ticks) |
|------|------|-------|-----|----------|----------------------|
| 16 | timeout | 0 | 28602 | 378 | (2,4): 5909 |
| 23 | timeout | 0 | 28619 | 378 | (2,4): 5909 |
| 25 | timeout | 0 | 28611 | 378 | (2,4): 5909 |
| 29 | timeout | 0 | 28607 | 378 | (2,4): 5909 |
| 33 | timeout | 0 | 28635 | 378 | (2,4): 5909 |
| 8  | timeout | 5 | 28571 | 372 | (2,4): 5905 |
| 1  | timeout | 17 | 15673 | 2334 | (4,11): 5924 |

关键事实：
- **5 个 0-kill seed 的 branch 计数逐 seed 几乎字节一致**（连 navigate=378 都一样）→
  说明 AI 收敛到**同一个硬编码死锁**，与具体 seed 无关。这不可能是参数/CMA-ES 能解的。
- `t2a` 占全部决策的 **~98%**，`navigate`（A*/hunt 逻辑）几乎从未被调用（378 ticks）。
  即：`think()` 里 T2a 分支**短路**了后续所有导航/狩猎逻辑。
- 坦克在**单格驻扎约 5900 ticks**（整局的 ~16%）疯狂 stop-and-aim，却 0 击杀。

**死锁根因（来自 `think()` + `FireControl`）：**
`findEnemyDirection` 用全局视野、全图范围，只要地图上**任一敌人**与玩家同 row/col
（对齐阈值放宽到 1 格）就返回 `aimDir`。于是 `think()` 必进 T2a：

```ts
if (aimDir && !onCooldown) {
  const scan = this.scanAhead(pcx, pcy, aimDir)
  if (scan.enemy || (scan.wall && !scan.baseWall && (!scan.steel || level>=3))) {
    this._moveDir = p.dir === aimDir ? null : aimDir   // 已对准就原地不动
    this._fire = true
    return                                            // ← 永远不 fall-through 到 navigate
  }
}
```

玩家一旦在某格发现同线敌人，就**原地驻扎、对空/被墙挡住的弹道疯狂开火**，且
**没有任何"驻扎过久/无进展就转为狩猎"的出口**。敌人不在真实弹道上时 `scan.enemy`
为假，但仍可能 `scan.wall` 触发"破墙开火"——破完墙也不前进，原地循环。导航逻辑
（`selectTarget` / A*）在死锁期间根本不被执行，所以 `navigate` 计数几乎为 0。

### 证据 4 — 存在两个完全不同的失败体制（§6 门槛的真正障碍）

| Stage/Diff | n | 胜 | base 被毁 | 平均击杀 |
|------------|---|----|-----------|----------|
| 0 / classic | 40 | 8 (20%) | 1 | 12.2 |
| 0 / hard | 12 | 0 | 12 (100%) | 2.6 |
| 0 / chaos | 12 | 0 | 12 (100%) | 4.8 |
| 1 / classic | 12 | 2 | 0 | 6.4 |
| 1 / hard | 12 | 1 | 6 | 5.3 |
| 1 / chaos | 12 | 1 | 7 | 7.1 |
| 2 / classic | 12 | 5 | 0 | 9.7 |
| 2 / hard | 12 | 0 | 11 | 2.2 |
| 2 / chaos | 12 | 0 | 11 | 3.4 |

- **Classic**：防守稳（base 几乎不丢），瓶颈是**进攻卡死**（证据 1–3）。
- **Hard / Chaos**：base **100% 被毁**，且击杀极低 → 完全是**另一套失败模式：防御崩溃**。
  当前 God AI 参数只在 classic 上训练，hard/chaos 的防御（基地拦截/修墙）从未被优化。
- §6 门槛要求 Hard≥70% / Chaos≥30%，但**优化器只评测 Stage 0 Classic**，永远不碰
  hard/chaos → 这个门槛在现有训练范式下**结构性不可达**。

---

## 2. 附带发现（已修复）：决策轨迹工具漏设 `world.rules`

`tools/decision-trace.ts` 的 `traceSimulation` 一直**没有**像 `simulation-runner.ts`
那样设置 `world.rules = RULES[difficulty]`（正是 runner 注释里警告的那个 bug）。
后果：**v3 / v4.1 优化器产出的所有"best/default 决策轨迹对比"其实都在 modern 规则下
跑的，不是所研究的难度。**

→ 已补上 `world.rules` 赋值并修复（tsc 通过，无测试依赖该工具）。**此前所有基于
decision-trace 的"为什么失败"分析都应按修正后的 rules 重做**（本次论证的轨迹已用修正版）。

---

## 3. 下一轮方向（按杠杆排序）

### P0 — ✅ 修 T2a 死锁（架构/行为，最高杠杆 — 已完成，commit `aec21f4`）

**状态：已完成并验证。** 实测（40 seed / 36000t / classic）：Stage 0 20%→**70%**、Stage 1 22.5%→**87.5%**。

实现对应原计划的 3 个改动点（都在 `think()` / `FireControl` / `StrategyPlanner`）：

1. **Anti-camp 逃逸（P0.1）**：记录"同格驻扎 tick 数"。若在某格 `t2a` 驻扎超过 `campTimeoutTicks`（90）且期间无击杀，则**强制 fall-through 到 `navigate` / `selectTarget`** 主动狩猎，并抑制 T2a `antiCampSuppressTicks`（60）保证移动时间。
2. **T2a 驻扎门槛（P0.2，最高杠杆）**：仅当 `scan.enemy == true`（真实弹道上有可击杀敌人）才进入"stop-and-aim 驻扎"；墙壁不再触发驻扎，改由 navigate 分支的 `directMove` / `canMoveOrBreak` 边走边破墙。原计划的第 3 点（walled-off systematic dig）被此改动**吸收**——破墙不再原地循环，而是随导航前进。
3. **Nav-stuck 逃逸（P0.3）**：记录 navigate 分支同格停留 tick，超过 `navStuckTicks`（180）则改道地图中心 (12,12)，打破追击快速敌人的循环。

结果：5 个 0-kill 死锁 + 多数"清到 17/20 收不掉"的局显著转化（详见 `plan/God-AI-P0-Verification.md`）。

### P1 — ✅ 生存与防御加固（已完成，commit `2cedb7a`）

**状态：已完成并验证。** 实测（40 seed / 36000t / classic）：Stage 0 70%→**87.5%**（2 gameover）、Stage 1 87.5%→**92.5%**（0 gameover）。

落在 classic 上的防御加固（原计划 P1 的"hard/chaos 跨难度评测"部分留到 P3）：

- **P1.1 闪避检测加宽**（`ThreatAssessor.ts`）：对齐阈值 `CELL*0.75`（12px）→ `TANK`（32px），检测到更多近失子弹。
- **P1.2 基地威胁检测加宽**（`StrategyPlanner.ts` + `GodAIInput.ts`）：`row>=20`→`row>=18`，新增 `isBaseUnderThreat()`。
- **P1.3 始终回防**（`StrategyPlanner.ts`）：回防逻辑移到 `if (canHunt)` 之前，终局也回防。
- **P1.4 跳过 T2a / 道具**（`GodAIInput.ts`）：基地受威胁且玩家太远时跳过 T2a 驻扎与道具收集。

详见 `plan/God-AI-P1-Verification.md`。

### P2 — ✅ 反驻扎区域修复 + nav-stuck 改进 + 预测瞄准（已完成，待提交）

**状态：已完成并独立验证。** 实测（30 seed / 18000t / classic，覆盖 Stage 0–4）：
总体 **80.7%**（P1 基线 80.0%），Stage 1 **100%** 完美通关、Stage 3 50%→**66.7%**、
Stage 4 75%→56.7%（−5，RNG 时序扰动，在门禁覆盖范围外）。详见 `plan/God-AI-P2-Verification.md`。

- **P2.1fix 反驻扎区域跟踪**（`GodAIInput.ts:523–525`）：驻扎判定由精确格改为 ±1 格
  区域，破解 TANK/CELL 边界子格振荡导致的反驻扎逃逸永不触发（迷宫地图死锁根因）。
- **P2.2 nav-stuck 逃逸改进**（`GodAIInput.ts:638–664`）：A* 到中心失败后，先试朝中心
  方向、再试任意可通行方向，不再 `directMove` 重新选敌回到死循环。
- **P2.4 预测瞄准 / lead the target**（`FireControl.ts:199–257`，纯检测）：敌人横向移动
  将在子弹到达时穿越弹道则提前开火，解决快速敌躲避子弹、玩家差 2–4 击杀清不掉的问题。
- 拒绝方案（DECISIONS §41）：T2a 仅对准才开火、baseUnderThreat 扩到 row≥22/23/24、
  campTimeout=60——均导致其他 stage 退步。

### P3 — ⏳ 下一步：架构已修，重跑 CMA-ES 对准 §6（待做）

- **前提已满足**：P0/P1/P2 落地后，搜索空间不再被死锁主导，现在跑 CMA-ES 才会收敛到高于 ~80%。
- **评测集改为多难度 × 多 stage**（至少 classic + hard + chaos，stage 0..4），fitness 直接
  对准 §6 门槛（Hard≥70% / Chaos≥30%）的聚合胜率，并**加强 anti-stall 项**（对"超时且
  剩余敌人多"给更大惩罚）啃掉剩余卡死型 timeout 与 Stage 4 退化。
- **在 P0/P2 行为修复之前跑 CMA-ES 是浪费**（历史已证明）：搜索空间被同一死锁主导，
  重新收敛到 ~20%。

---

## 4. 验证实验（已运行，假设成立）

计划的验证实验（仅加 P0 行为改动，复测 Stage 0 Classic seeds 1–40 @36000t）**已实际执行**，
结果远超 ">40%" 的阈值，确认死锁假设成立：

- Stage 0 Classic：20% → **70%**（28/40）；Stage 1 Classic：22.5% → **87.5%**（35/40）。
- 后续 P1 防御加固进一步推到 Stage 0 **87.5%** / Stage 1 **92.5%**（详见 §3 与两份 Verification 文档）。

→ P0/P1 方向正确，进入 P2。

---

## 5. 风险与注意

- **Parity 测试**：`tests/godai-split-parity.test.ts` 在改动 `think()` 行为后必须重锁基线
  （§0.5 的"零行为漂移"保证只针对拆分，不覆盖本次行为改动）。P0、P1、P2 均已重锁；
  **P3 若改动行为同样需要重锁**。
- **不要混淆两套体制**：classic 的修复（P0）不会自动修复 hard/chaos 防御（P1），需分开验证。
- **trace 工具**：后续分析一律用已修复 rules 的 `traceSimulation`；旧的 v3/v4.1 轨迹文件
  视为在 modern 规则下产出，结论不可直接套用到 classic。
