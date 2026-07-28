# God AI 调校日志

> 每轮一行表格 + 简注。完整仿真过程记录在 `.workbuddy/optimization-*/` 目录中。

---

## Round 1 — CMA-ES 参数优化 + T2a 冷却修复 (2026-07-28)

### 改动

| 项目 | 详情 |
|------|------|
| 技巧# | CMA-ES 自动参数优化 + T2a 冷却中停车修复 |
| 代码改动 | `GodAIInput.ts`: 阈值常量移入 `GodAIParams`；T2a 仅在非冷却时触发 |
| 工具新增 | `tools/optimize-godai.ts` (CMA-ES), `tools/decision-trace.ts` (决策追踪), `tools/analyze-trace.ts` (分析) |

### 方法

使用 sep-CMA-ES (可分离协方差矩阵适应进化策略) 优化 12 个参数：
- 3 个不完美参数：reactionDelay, aimError, suboptimalPathProb
- 9 个策略阈值：defenseRowOffset, defenseColSpread, threatRangeCells, maxPlayerDistFromBase, t8MaxInterceptDistCells, baseWallScanRadius, replanInterval, powerupMaxDivertDistance, endgameEnemyThreshold

优化配置：stage 0 (Outpost), classic 难度, 5 seeds, 18000 max ticks, 30 代 × 11 个体

### 关键决策失误分析（通过决策追踪发现）

#### 失误 #1：T2a 停车瞄准时冷却中不开火也不移动（头号败因）

**现象**：玩家 10116 tick 中仅开火 7 次！最大开火间隔 4896 tick（81 秒）。
307 个空转 tick — 玩家停在 (9,23) 朝上，有敌人在场，但不移动也不开火。

**根因**：T2a 分支检查 `scanAhead` 发现墙/敌人后停车，但 `_fire = !onCooldown && rng.next() >= aimError`。
冷却期间（~74 tick）`onCooldown=true` → `_fire=false`，玩家原地等待。
冷却结束后玩家可能已不在 T2a 分支（敌人移出同列/同行），进入 navigate 分支，
navigate 中 `shouldFireInDir` 检查移动方向无目标 → 不开火。循环往复。

**修复**：T2a 条件改为 `aimDir && !onCooldown` — 冷却时不进入 T2a，直接 fall through 到导航。
这样玩家在冷却期间继续移动，不会原地空等。

#### 失误 #2：玩家防守位置偏左

**现象**：玩家 95% 时间停留在 (9,23) 和 (8,23)，但基地在 (12,24)。
敌人从右侧（col 14-17）攻击时玩家来不及回防。

**根因**：`interceptCell` 将目标列 clamp 到 `baseCol ± defenseColSpread`。
优化前 `defenseColSpread=8`，但拦截逻辑优先追击最近的敌人，导致玩家偏向左侧。

**优化结果**：CMA-ES 发现 `defenseColSpread=3` + `threatRangeCells=8` 最优 —
紧贴基地、只响应极近威胁。基地存活率从 40% 升至 80%。

#### 失误 #3：首杀太慢

**现象**：首杀在 tick 1149-2528（19-42 秒），远超合理范围。

**根因**：玩家在游戏初期花太长时间导航到防守位置，期间不主动交战。
加上 T2a 冷却空转问题，有效开火极少。

### 优化结果

| 指标 | 默认参数（优化前） | CMA-ES 优化后 | Δ |
|------|-------------------|--------------|---|
| 适应度 | 145 | 394 | +249 |
| 胜率 | 0% | 0% | 0% |
| 基地存活率 | 40% | **80%** | +40% |
| 平均击杀 | 2.2 | 5.6 | +3.4 |
| 平均存活 tick | 2980 | 4247 | +1267 |

### 优化后参数

```typescript
DEFAULT_GOD_AI_PARAMS = {
  reactionDelay: 0,          // 2 → 0  (无延迟，立即反应)
  aimError: 0,               // 0.02 → 0  (完美瞄准)
  suboptimalPathProb: 0.3,   // 0.1 → 0.3  (更多随机路径)
  defenseRowOffset: 1,       // 3 → 1  (紧贴基地)
  defenseColSpread: 3,       // 8 → 3  (窄防守)
  threatRangeCells: 8,       // 30 → 8  (只响应近敌)
  maxPlayerDistFromBase: 4,  // 12 → 4  (不离基地)
  t8MaxInterceptDistCells: 8, // 6 → 8  (更大拦截范围)
  baseWallScanRadius: 5,     // 3 → 5  (更大基地墙扫描)
  replanInterval: 50,        // 20 → 50  (更稳定的路径)
  powerupMaxDivertDistance: 3, // 15 → 3  (不追道具)
  endgameEnemyThreshold: 1,  // 2 → 1  (更早进入终局)
}
```

### 参数解读

优化器找到了「**贴身龟缩**」策略：
- 紧贴基地（offset=1, dist=4）—— 最大化基地保护
- 窄防守（spread=3）—— 集中在基地列附近
- 只响应近敌（threat=8）—— 不追远敌
- 不追道具（divert=3）—— 专注防守
- 完美瞄准（aimError=0）—— 每发必中

这套策略将基地存活率从 40% 提升到 80%，但无法过关（0% 胜率），
因为玩家过于保守，不主动清场。

### 下一步方向

1. **S6 攻守切换**：根据场上敌人数量/类型/基地墙完整度动态切换激进/保守模式。
   当前优化器找到的「贴身龟缩」是纯防守策略，需要加入进攻切换才能过关。
2. **S10 终局猎杀**：`endgameEnemyThreshold=1` 已设置，但终局逻辑太弱。
   需要更强的主动追杀最后 1-2 敌的能力。
3. **T2a 对齐阈值调优**：当前 `findEnemyDirection` 使用 `halfT=16` 检测同列/同行，
   边缘对齐会导致 T2a 触发但 `scanAhead` 找不到目标。需要更严格的对齐检查。
4. **开火效率**：即使修复了 T2a 冷却问题，玩家每场仅开火 7-8 次。
   需要让玩家在导航时更积极地朝目标方向开火（当前 `shouldFireInDir` 太保守）。

---

## Round 2 — Classic 模式适配 + 13 项修复 (2026-07-28)

### 背景

Classic 模式（§32）将敌人 AI 和战斗能力调整为忠实原版（难度下降）。但仿真工具
（`simulation-runner.ts`）未设置 `world.rules`，所有仿真实际跑的是现代规则。
God AI 的 `onCooldown` 也用了时间冷却（classic 应为子弹数冷却），导致开火极少。
两项根本性 bug 叠加，AI 表现为 0% 基地存活 / 0 击杀。

### 改动

| 项目 | 详情 |
|------|------|
| 技巧# | 13 项修复（见 DECISIONS §33） |
| 代码改动 | `GodAIInput.ts`: onCooldown 子弹数感知；shield 不触发 aggressive；navigate 不打基地；canHunt 收紧；baseUnderThreat 提前；selectTarget 追击最近敌；directMove 垂直优先；T2a-hold；智能打墙；AIM_RANGE=15；followPath 无路径不 fallback；POWERUP_PRIORITY 补全 |
| 工具修复 | `simulation-runner.ts`: 设置 `world.rules = RULES[difficulty]` |

### 优化结果

| 指标 | Round 1 (modern rules, CMA-ES) | Round 2 (classic rules, 13 fixes) | Δ |
|------|------|------|---|
| 基地存活率 | 80% | **100%** | +20% |
| 平均击杀 | 5.6 | 2.6 | -3.0 |
| 最佳种子击杀 | 8 | **17** | +9 |
| 胜率 | 0% | 0% | 0% |
| 0 杀种子占比 | 0% | 55% | +55% |

### 关键发现

1. **仿真工具漏设 `world.rules`** — 头号 bug。Classic 规则从未生效。
2. **`onCooldown` 时间冷却 vs 子弹数冷却** — AI 每场仅开火 1-2 次的根因。
3. **navigate 分支无条件开火摧毁自家基地** — classic 基地 HP=1，一枪自毁。
4. **shield 触发 aggressive 导致放弃防守** — 玩家追敌外出，基地裸奔。
5. **directMove 水平优先** — 玩家横向移动无法进入敌人所在行，T2a 无法触发。
   改为垂直优先后，击杀从 0 跃升至 17（seed 3）。
6. **T2a-hold** — 冷却中保持对齐，不追其他目标。显著提升有效开火率。
7. **55% 种子 0 击杀** — 敌人移动模式导致玩家无法进入同行/列。需 M1 预判射击。

### 当前参数

```typescript
DEFAULT_GOD_AI_PARAMS = {
  reactionDelay: 0,
  aimError: 0,
  suboptimalPathProb: 0.05,
  defenseRowOffset: 1,
  defenseColSpread: 3,
  threatRangeCells: 26,
  maxPlayerDistFromBase: 7,
  t8MaxInterceptDistCells: 8,
  baseWallScanRadius: 5,
  replanInterval: 50,
  powerupMaxDivertDistance: 3,
  endgameEnemyThreshold: 1,
}
AIM_RANGE_CELLS = 15
```

### 下一步方向

1. **M1 预判射击**：当前玩家朝敌人当前位置射击，但敌人移动导致命中率极低。
   需要预测敌人移动方向，射击其未来位置。这是解决 0 击杀种子的关键。
2. **S6 攻守切换**：当前玩家过于防守（baseUnderThreat 时紧贴基地），需要根据
   场上敌人数量/类型动态切换进攻/防守。在敌人少且基地安全时应主动追击。
3. **T2a-hold 死亡问题**：玩家在冷却中原地等待，被敌方子弹击杀。
   需要在 T2a-hold 中加入威胁检测，有来袭子弹时放弃 hold 转为闪避。
4. **AIM_RANGE_CELLS 调优**：15 是当前值，但可能不是最优。需要实验不同值。
