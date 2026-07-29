# God AI 调优进度

> 最新在前。历史轮次保留在文末。

---

## Round 3 — P4 战役收官：全 35 关 floor-aware 调优 + 逐关覆盖表（2026-07-29，commit `83e5370`，DECISIONS #36）

### 目标与最终成绩（真值口径：35 关 × 60 seeds，classic，18000t）

| 目标 | 结果 | 判定 |
|------|------|------|
| 平均过关率 > 80% | **81.9%** | ✅ PASS |
| 全部 35 关 > 60% | **34/35 达标** | ⚠️ 仅 S32 Diamond 52% |

### 战役过程

- **7 轮 CMA-ES**（IPOP，15-worker 并行池，fitness v5.0 = 逐关胜率块 + deficit×8000 floor 惩罚），搜索空间为 20 维 `GodAIParams`，内环 = 全 35 关 × 20 seeds。R7 warm-start R6、σ=0.3 收敛，`DEFAULT_GOD_AI_PARAMS` 已更新为 R7 最优（关键变化：threatRangeCells 20→10、maxPlayerDistFromBase 19→26、powerupMaxDivertDistance 3→16、huntAllyCount 6→1、aimError 0→0.03 —— 微量瞄准噪声可全局打破互堵僵局）。
- **保留的结构性行为**：race-to-base 判定并入 `isBaseUnderThreat()`（P4.1）、寡不敌众回撤规则进 `selectTargetImpl`（P4.2）。
- **A/B 否决并回滚的行为**：race 路径膨胀（P4.3）、猎杀出生带规避（P4.4）—— 20-seed 上看似有效，60-seed 证伪。

### 两个关键发现（方法论）

1. **单一全局参数集无法满足全部 35 关**：弱关失败家族需求相反 —— S6 Iron Curtain 要"禁止回撤"，S18 Frozen Field 要"更宽回撤半径"；全局调任一方向都会让另一关掉最多 −30pp（60 seeds）。
2. **20-seed 探针有 ±11pp 二项噪声，会选中海市蜃楼**（例：S32 单关 CMA-ES 的 60% @30 seeds，60 fresh seeds 复测仅 43%）。**所有决定性结论必须 60 seeds。**

### 解法：逐关参数覆盖表（data over code）

`src/ai/godai-stage-overrides.ts` —— `Record<关名, Partial<GodAIParams>>`，由 `applyStageOverrides()` 在 `tools/simulation-runner.ts` 中按关名合并到全局最优之上。每条覆盖必须 ≥60 seeds 与同 base 无覆盖对照验证。当前 4 条：

| 关 | 覆盖 | 战术依据 | 60-seed 提升 |
|----|------|----------|--------------|
| S6 Iron Curtain | `outnumberedEnemyCount:5, threatRangeCells:14` | 钢墙迷宫中回撤=让出地图，侧翼直冲基地 | 57→63% |
| S18 Frozen Field | `outnumberedRadiusCells:14, aimError:0` | 开阔冰面三向合围前提早后撤；8/20 敌是 4 血重甲，浪费弹药致命 | 52→67% |
| S25 Ice Palace | `aimError:0` | 同为重甲+冰面，弹药经济决定成败 | 57→**73%** |
| S26 Brick Maze | `replanInterval:30, suboptimalPathProb:0.05` | 密砖迷宫需要更快重规划+路径扰动防死锁 | 53→**65%** |

### 测试体系翻新

- 回归门禁重写：从 2 关 → **全 35 关 × 20 seeds**，逐关 floor（60-seed 真值 − 4 胜裕量）+ 聚合 77%，**仅 ~11 秒**（实测 700 局吞吐 ~64 局/s）。旧 S0/S1 门禁曾静默掩盖真回归。
- parity 基线重锁（`tools/relock-parity.ts`，8 seeds 全过）；curriculum 玩具关 4 重钉（aimError=0, seed 7，继续隔离导航子系统）。
- 全套 **530 测试通过，tsc 干净**。
- 新工具：`tools/probe-params.ts`（单关参数敏感度探针，`--skipStageOverrides` 量纯参数效果）、`tools/validate-p4.ts --seeds N`（全关扫描）。

---

## Round 4（下一轮）— S32 Diamond 攻坚方向论证

### 现状与证据

- **S32 = 52% @60 seeds（定稿参数+覆盖表），全 35 关唯一低于 60% floor 的关。**
- 失败归因（60 seeds，29 败）：**17 场基地被拆（59%）+ 12 场耗尽命数（41%）** —— 双失败模式并存。
- 敌力：**8 armor（4 血）+ 8 fast + 4 power**，全 35 关最重的装甲配置之一。
- 地图：大片森林 + 对角碎片化钢墙通道；基地仅标准砖 U 型护墙，**底部 18–22 行几乎全开阔** —— 漏网敌人可沿底带直冲基地，无地形迟滞。

### 为什么参数手段已穷尽（本轮已证明，勿重复）

1. R6、R7 两套 base 上的手工探针（aimError、回撤族、威胁圈族、站位族、camp/replan 族）全部 ≤ base（60 seeds）。
2. 站位家族扫描（maxPlayerDistFromBase、defenseRow/ColSpread、baseRaceRangeCells）全部无效。
3. 单关专属 CMA-ES（30 gens, σ0.5）的"最优"在 60 fresh seeds 复测 43% < base 52% —— 教科书式过拟合。

**根因判定**：两个失败模式对姿态的要求互斥 —— 前压速杀重甲（治 lives_exhausted）意味着远离开阔底带（加剧 base_destroyed），反之亦然。现有 20 维参数只能在既有决策分支间调权重，**没有任何分支表达"边守开阔底带边磨重甲"这一战术**。这是结构性缺口，必须改代码。

### 候选方向（按推荐顺序）

**D1 — 底带近卫模式（首选）**
- 内容：新增一个决策行为 —— 当（重甲占比高 && 基地进近通道开阔）时，玩家保持在"底带拦截包线"内活动：在基地两侧咽喉点之间巡逻换位，只攻击进入包线的敌人，不深入上半场猎杀。
- 论证：直接针对占 59% 的 base_destroyed 败局；重甲敌 4 血意味着放它靠近再打并不亏（反正要 4 枪），在底带打还自带防守价值。S32 的 timeout 风险低（12 败全是 lives，不是 timeout），所以放弃前压不太会把败局转成超时。
- 落地方式（零回归风险）：新参数 `guardBandMode`（默认 off）+ `guardBandRow/HalfWidth`，**只通过 S32 的覆盖表条目开启** —— 其余 34 关行为逐 bit 不变，回归门禁天然守护。
- 验收：S32 ≥60% @60 seeds，且 35×60 mean ≥80%、无其他关跌破 floor。

**D2 — 重甲目标优先级（次选，可与 D1 叠加）**
- 内容：目标选择时优先补刀已受伤的 armor（伤害是持久的），避免在多个满血 armor 间换目标摊薄伤害；fast 类入侵者优先级高于远处 armor。
- 论证：治 lives_exhausted 一侧 —— 当前 `selectTargetImpl` 以距离为主，重甲关会出现"每个 armor 都打了 1-2 枪没死一个"的伤害稀释。改动小（打分函数加一项 hp 权重），且对 S18/S25 这类重甲关可能白送增益。
- 风险：全局改动，需全 35 关回归验证；可先做成参数（`damagedArmorBonus`，默认 0）保守上线。

**D3 — 迷宫感知导航（末选，本轮不建议）**
- 内容：A* 代价函数偏好有钢墙掩体的走廊、惩罚开阔带暴露。
- 论证：理论上同时改善两种败局，但改动面最大（影响全部 35 关的路径选择），验证成本最高，且 D1+D2 大概率已够到 60%。留作 D1+D2 仍不达标时的后手。

### 执行协议（沿用 P4 方法论）

1. 先写 `docs/` audit note（AGENTS §4 Step 2，行为级改动必需）。
2. D1 实现 → S32 单关 60-seed A/B（覆盖表开 vs 关）→ 达标则全量 35×60 终审。
3. 不达标再叠 D2（先 S32/S18/S25 三关探针，再全量）。
4. 每一步跑回归门禁（~11s）+ parity；新行为参数默认 off，parity 不应破。
5. 定稿：更新覆盖表注释、DECISIONS 新条目、重锁受影响基线。

---

## 历史记录

### Round 2 — Classic 模式适配 + 13 项修复（2026-07-28）

修复了仿真工具和 AI 在 classic 模式下的 13 个根本性 bug（详见 DECISIONS §33）。核心：`world.rules` 漏设（classic 规则从未生效）、`onCooldown` 子弹数冷却、navigate 分支自毁基地、shield 触发弃防、directMove 垂直优先、T2a-hold、selectTarget 发呆、canHunt 过激、baseUnderThreat 触发太晚。修复后 stage 0 基地存活率 100%，但胜率 0/20（全 timeout）—— 由后续 P0–P3（T2a 死锁修复、反驻扎区域、预测瞄准、CMA-ES P3）逐步解决。

### Round 1 — 基础设施（2026-07-27）

1. 阈值常量全部移入 `GodAIParams`，可被 CMA-ES 自动调参
2. CMA-ES 优化器 `tools/optimize-godai.ts`（sep-CMA-ES）
3. 决策追踪系统 `tools/decision-trace.ts`
4. T2a 冷却中停车修复
5. 首轮 CMA-ES 默认参数：基地存活率 40%→80%，击杀 2.2→5.6
