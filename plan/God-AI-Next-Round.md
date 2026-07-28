# 下一轮 God AI 调优方向论证

> 结论先行：**下一轮不应再跑"纯 CMA-ES 参数搜索"。** 当前 20% 的胜率天花板是
> **行为/架构层面的决策流死锁**导致的，不是参数问题。在修复死锁之前再跑任何一轮
> CMA-ES，都会重新收敛到 ~20%。下一轮的最高杠杆是 **P0：修 T2a 死锁 + 加 anti-camp
> 逃逸 + walled-off 目标 systematic dig**；其次是 **P1：强化 hard/chaos 防御并让优化器
> 跨难度/跨 stage 评测**（因为 §6 门槛 Hard≥70%/Chaos≥30% 当前优化器根本没碰过）；
> **P2：架构修复之后再跑一轮 CMA-ES 对准 §6 门槛。**

---

## 1. 证据

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

### P0 — 修 T2a 死锁（架构/行为，最高杠杆）

目标：把 31 个卡死局中的相当一部分转为胜利。具体改动点（都在 `think()` / `FireControl` /
`StrategyPlanner`）：

1. **Anti-camp 逃逸**：记录"同格驻扎 tick 数"。若在某格 `t2a` 驻扎超过 `N` ticks（如 90）
   且期间无击杀，则**强制 fall-through 到 `navigate` / `selectTarget`** 主动狩猎最近敌人，
   而不是继续原地开火。
2. **T2a 驻扎门槛**：仅当 `scan.enemy == true`（真实弹道上有可击杀敌人）才进入"stop-and-aim
   驻扎"；若 `aimDir` 触发但 `scan.enemy` 为假（敌人在墙后/远处），**不驻扎**，改为走导航
   去绕/挖。
3. **Walled-off 目标 systematic dig**：当 `selectTarget` 选中的敌人被 A* 判定不可达
   （砖墙封死）时，让 AI 沿朝敌人的方向**系统性破墙掘进**（已有 `canMoveOrBreak` /
   `directMove` 做近距破墙，但 >5 格走 A* 会直接放弃 → 需要补"不可达则朝目标方向 dig"）。

预计收益：5 个 0-kill 死锁 + 多数"清到 17/20 收不掉"的局，应能显著转化。

### P1 — 强化 hard/chaos 防御 + 让优化器跨难度评测（对准 §6 门槛）

- 防御加固：预判性修基地保护墙、对多发基地弹道的多目标拦截（当前 T8 是单发拦截）。
- **优化器评测集改为多难度 × 多 stage**（至少 classic+hard+chaos，stage 0/1/2），
  fitness 直接对准 §6 门槛的聚合胜率。否则 Hard/Chaos 的 100% 基地被毁永远无法进入优化目标。
- 这一步可以和 P0 并行：即便 P0 修好 classic 进攻，hard/chaos 仍会因防御崩溃而 0 胜。

### P2 — 架构修复之后再跑一轮 CMA-ES

- 在 P0/P1 落地后，跑一轮 IPOP-CMA-ES，评测集覆盖 §6 门槛，fitness 保留 v4.1 的
  gameover 漏洞修补，并**加强 anti-stall 项**（对"超时且剩余敌人多"给更大惩罚）。
- **在 P0 之前再跑 CMA-ES 是浪费**：搜索空间会被同一个死锁主导，重新收敛到 ~20%。

---

## 4. 验证实验（廉价，先确认 P0 假设）

仅给当前 `DEFAULT_GOD_AI_PARAMS` + P0 的 anti-camp 行为改动，复测 Stage 0 Classic
（seeds 1–40, 36000 ticks）：

- 若胜率从 20% 明显跳升（例如 >40%）→ P0 假设成立，继续 P1/P2。
- 若几乎不变 → 死锁机制判断有误，需回看 `scanAhead`/`findEnemyDirection` 的微观命中线。

---

## 5. 风险与注意

- **Parity 测试**：`tests/godai-split-parity.test.ts` 在改动 `think()` 行为后必须重锁基线
  （§0.5 的"零行为漂移"保证只针对拆分，不覆盖本次行为改动）。
- **不要混淆两套体制**：classic 的修复（P0）不会自动修复 hard/chaos 防御（P1），需分开验证。
- **trace 工具**：后续分析一律用已修复 rules 的 `traceSimulation`；旧的 v3/v4.1 轨迹文件
  视为在 modern 规则下产出，结论不可直接套用到 classic。
