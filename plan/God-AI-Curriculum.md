# God AI 调校计划（分阶段验证版）— Curriculum-as-Verification

> 版本 1.4 · 2026-07-28（已完成验证）
> 状态：✅ 已完成（全部 5 个课程阶段通过；449 测试绿；见 DECISIONS.md §34–§35）
> 上位文档：God-AI-Tuning.md、god-ai-analysis.md、docs/god-ai-tuning-log.md
>
> **新鲜度说明（gac.review.md 核查）**：§5.2 所列 4 个 bug **已在代码中修复**（urgencyBonus / powerup score / killerKind / failure! 断言均有 "Fix Bug" 注释或测试守卫）→ 已从实施包移除，仅保留验证项；§5.3 S6 **已部分实现**（`canHunt` 存在但阈值硬编码、`endgameEnemyThreshold` 声明未用）；§4 阶段 4 的 "A* 必须被使用" 断言**有误**——代码刻意用 `directMove` 追击，已修正断言。
>
> **完成状态（2026-07-28 验证）**：§0.5 拆分**已完成**（见下方 §0.5 验收；`tests/godai-split-parity.test.ts` 锁时序通过，零行为改变；457 测试绿）；其余基础设施（缺口 A/B、`enemyCount`/`playerSpawn`/`enemySpawns`、`hasBase` 守卫、S6 参数化）+ §5.3 导航混合 + `tools/curriculum.ts` 全部落地。5 阶段结果（Kills / Ticks）：①1/141 ②3/333 ③20/2715 ④20/2208 ⑤20/2727，全部 `stage_clear` 且 Base ✅。下一步：真实 stage 0 回归门禁（Hard≥70% / Chaos≥30%，plan §6）。
>
> **补充（2026-07-28 晚）**：v3 调参写回时漏掉 `aimError`/`suboptimalPathProb` 两个浮点参数，已补回 `DEFAULT_GOD_AI_PARAMS`（全部 13 字段现与 optimizer `bestParams` 一致，见 DECISIONS §37）；新增 `tests/god-ai-regression-gate.test.ts` 宽 seed(1..30) 回归门禁（plan §6 真正调参门禁），测试数 457 → **458**，全绿。

---

## 0. 一句话立场

God AI 调校**不要**用玩具关当 CMA-ES 的训练/优化环境；玩具关只用于**逐子系统验证 + 回归**。
真实 stage 0（及少量真关）始终是唯一优化目标与校准门禁。

> 诊断纠正：当前 0% 过关**不是**"stage 0 太复杂"，而是 AI **缺决策分支**（攻守切换 S6、预判 M1）
> 且 **失败归因不可见**。CMA-ES 已陷"贴身龟缩"局部最优（基地 100% 存活 / 胜率 0%）。
> 玩具关的价值是**隔离并验收单个子系统**，让"改哪一处有用"变可观测，而非降低任务难度来刷分。

---

## 0.5 前置工作（P0，最先做）：拆分 GodAIInput 上帝类

`src/ai/GodAIInput.ts` 当前 ~1400 行（god-ai-analysis.md 问题 4 已标记为"上帝类"），单次改动牵一发动全身，是后续 S6 阈值参数化、M1 预判、以及 curriculum 调试的**主要摩擦源**。本计划把它列为**最先做的 P0 前置**，拆分后再做 §5 的其余改动。

**拆分原则**
- **纯重构、零行为改变**：不新增/修改任何决策逻辑，只搬方法。AGENTS §2.1（Input 只读 World）与 §2.3（确定性）不变；`Simulation`/`World` 不动。
- **对外契约不动**：`GodAIInput` 仍实现 `InputLike`；`GodAIParams` / `DEFAULT_GOD_AI_PARAMS` / `SKILLED_HUMAN_PARAMS` 仍从 `GodAIInput.ts` 导出（其余工具 `optimize-godai` / `simulation-runner` / `ai-calibrate` / `calibration` 直接 import 它们，不能断）。
- **共享状态收敛（实际落地方式）**：原计划拟引入独立 `GodAIState` 数据对象；实际采用 **`Impl(self: GodAIInput)` 自由函数模式**——每个子模块方法以 `XImpl(self, ...)` 形式导出，`GodAIInput` 保留全部共享可变状态（`world`/`params`/`rng`/`_moveDir`/`_fire`/`aggressive`/`path`/`replanTimer`/`reactionCounter`/`lastThreatId`/`branchCounts`/`hasBase` 等）并暴露一组 `public` 薄封装方法供子模块互调。子模块 `import type { GodAIInput }`（仅类型，运行期无循环依赖），`GodAIInput` 单向 `import` 各 `*Impl`。运行期无全局可变状态、无循环依赖，且**方法体逐字搬迁**风险最低（无需改写每个方法的内部 `this.` 引用）。

**建议的模块边界（按 code 实际分段，已 grep 校验）** — 新文件放 `src/ai/god/`：

| 文件 | 职责 | 包含的方法（行号据当前 GodAIInput.ts） |
|------|------|------------------------------------------|
| `god/StrategyPlanner.ts` | 目标选择 + 攻守决策（含 S6 `canHunt`） | `selectTarget`(1198)、`getDefaultDefensePosition`(1157)、拦截几何 `interceptCell`、威胁权重 `KIND_THREAT_WEIGHT` 使用处 |
| `god/ThreatAssessor.ts` | 威胁评估 + 闪避 + 基地子弹拦截 | `findMostDangerousBullet`(616)、`findBulletThreatToBase`(652)、`baseBulletInterceptCell`(718)、`dodgeDirection`(770)、`isSafeDir`(819) |
| `god/FireControl.ts` | 射击决策 + 瞄准几何 | `findEnemyDirection`(455)、`scanAhead`(517)、`shouldFireInDir`(862)、`isBaseProtectionBrick`(597) |
| `god/Navigator.ts` | 导航 + 拦截 + 破墙 | `navigateTowards`(1054)、`followPath`(1092)、`replan`(1162)、`directMove`(1327)、`canMoveOrBreak`(1378)、`canMoveDir`(1417)、`playerCell`(1040)、`tankCell`(1046)、`calculateRouteDanger`(996) |
| `GodAIInput.ts`（保留为编排层） | `InputLike` 实现 + `think()` 编排 + 参数/常量导出 | `constructor`/`reset`/`getMoveDirection`/`isFiring`/`wasItemPressed`/`endFrame`/`think`(234)、`GodAIParams` 等导出、`AIM_RANGE_CELLS` 等静态常量 |

> 注：`findPowerUpTarget`(933) 随 `StrategyPlanner`（道具经济属目标层）；`calculateRouteDanger`(996) 随 `Navigator`（路线危险度属导航层）。模块边界以"决策职责"切，不以字母序。

**验收（证明零行为改变）— ✅ 已完成（2026-07-28）**
- [x] `bun run check` 全绿（类型 + `god-ai-gates.test.ts` 不变绿）。拆分后 449 → **457** 测试（新增 8 个 parity 用例）。
- [x] 新增 `tests/godai-split-parity.test.ts`：对 8 个固定 seed × stage 0 跑 `runSimulation`，**锁定时序**——断言拆分前后 `outcome / ticks / score / lives / killCount / baseAlive / playerLevel` 完全一致。基线在拆分前从 `28683be` 的单类 `GodAIInput.ts` 实跑捕获，并用 git 旧版 + 新版双跑交叉验证 8 个 seed 全部逐字段相等（覆盖 gameover / max_ticks / stage_clear 三种结局）。
- [x] 交叉验证：临时从 `28683be` 取出旧 `GodAIInput.ts` 与重构版同跑 8 seed，输出 JSON 逐字段一致，确认零行为漂移；验证脚本与临时文件已删除，基线固化进 parity 测试。
- [x] （2026-07-28 晚补充）v3 调参后 parity 基线已重锁（doc 注释改为如实说明：当前基线锁"重构后 + v3 参数"行为，拆分等价性已在 `0d3275b` 证明）；新增 `tests/god-ai-regression-gate.test.ts` 宽 seed(1..30) @classic @18000 聚合下限门禁（wins≥6 / baseAlive≥25 / avgKills≥9，实测 7/26/11.3），即 plan §6 的真正调参回归门禁。测试数 457 → **458**，全绿。

> 风险已闭环：共享状态经 `self: GodAIInput` 显式传入，无全局可变状态；parity 测试作为重构行为锁 + `god-ai-regression-gate` 作为调参门禁双兜底。拆分作为**独立 phase commit** 提交，未夹带任何逻辑改动。

---

## 1. 分阶段验证 vs 分阶段训练（核心框架）

| 用法 | 是否推荐 | 理由 |
|------|----------|------|
| 玩具关 = **逐子系统验收 / 回归测试** | ✅ 强烈推荐 | 20 敌在真关一起失败时无法定位根因；迷你关能干净回答"火控打不打得中""攻守切换有没有生效" |
| 玩具关 = **CMA-ES 优化目标** | ❌ 反模式 | 优化器会找到更极端的龟缩/随机参数刷满分，但真关仍 0%；且 God AI 的用途是给关卡生成器当"理论上限对手"做校准门禁（Hard≥70%/Chaos≥30%），玩具校准出的基准对真人失真 |

**结论**：搭一套 `tools/curriculum.ts`，每个迷你关 = `(StageData + GodAIParams + 预期结果断言)`，
作为 CI 回归门禁；最终仍回真实 stage 0 跑 CMA-ES 与门禁。

---

## 2. 与现有 God-AI-Tuning 计划的关系

- 本计划**不替换** God-AI-Tuning，而是补一层**脚手架**：把其 §1 技巧库按"子系统"映射到可断言的迷你关。
- God-AI-Tuning 的 CMA-ES/门禁流程保持不变，fitness 环境不变（真实 stage 0）。
- 本计划交付的 `tools/curriculum.ts` 与 `tools/optimize-godai.ts` 并存：前者管"子系统对不对"，后者管"参数优不优"。

---

## 3. 落地可行性缺口（已查代码）

玩具关本身是纯数据（`runSimulation(stage, ...)` 直接吃 `StageData`），但有三处硬缺口必须先补：

### 缺口 A — 敌数不可控
- `src/constants.ts:43` `ENEMIES_PER_STAGE = 20` 写死。
- 引用点：`World.ts:337`（`enemiesRemaining`）、`World.ts:365`（刷怪循环 `for i<ENEMIES_PER_STAGE`）、`Simulation.ts:245`（`remainingSpawns = ENEMIES_PER_STAGE - enemiesSpawned`）、`World.ts` 的 `totalEnemiesLeft` getter。
- `StageData.enemies` 只决定**种类队列**，不决定数量。
- **改法**：`StageData` 加 `enemyCount?: number`；`World` 加 `enemiesTotal` 字段；上述四处改用 `enemiesTotal ?? ENEMIES_PER_STAGE`。
  - 注意：`WorldSerializer` 需把 `enemiesTotal` 纳入快照（恢复时不调 `loadStageData`，必须持久化），否则 RecoverySystem 下失真。

### 缺口 B — 无基地时 GodAIInput 会守幽灵基地
- `src/ai/GodAIInput.ts` 的防守逻辑**硬编码 `BASE_POS`（constants.ts:52）**，无基地时仍去 `(12,24)` 附近防守一个不存在的基地。
- **先例**：敌人 AI 已用 `perception.ts:170` `hasBase: !!base` 优雅处理无基地；GodAIInput 应镜像。
- **改法**：`TileMap.getBasePos()` 已存在（无基地时返回 `null`），加 `hasBase(): boolean { return this.getBasePos() !== null }` 即可；`GodAIInput.reset()` 缓存 `this.hasBase`；所有 BASE_POS 相关定位/urgency/T8 逻辑在 `!hasBase` 时跳过，降级为纯猎杀最近敌。**这是阶段 1–3 的硬前置**——否则无基地关里 `canHunt` 的 `baseUnderThreat`（指向 `BASE_POS`）与防守定位都会失真，迷你关变假阴性。

### 缺口 C — 8×8 无法原生表达
- 世界固定 26×26 网格（`GRID=26`、`FIELD=416`）。
- **改法（纯数据，无代码改动）**：用 26×26 中一片**开敞竞技场**近似 8×8——四周 `s`（steel）围一圈，内部全 `.`；无基地关则省略 `E` 瓦片。

---

## 3.5 玩具关简化对 AI / 战斗机制的影响与处置

> 用户关切：钢铁围栏要重设出生点、无基地要让敌我 AI 忽略基地目标。以下是**完整影响清单 + 处置**（均据代码核查，非推测）。

### 影响 1 — 出生点失效（玩家 + 敌人）【已查代码，确为硬伤】
- **现状**：`PLAYER_SPAWN={col:8,row:24}`（constants.ts:49）与 `ENEMY_SPAWNS=[{0,0},{12,0},{6,0}]`（constants.ts:55）都是**硬编码常量**；刷怪逻辑 `Simulation.ts:47` 直接 map 三者，`World.spawnPlayer()` 用 `PLAYER_SPAWN`。死亡重生也复用玩家出生点。
- **问题**：8×8 开敞竞技场若放在 26×26 内部（四周 steel），这些固定点几乎必然落在 steel 内或开敞区外 → 玩家/敌人卡墙或刷不出来。
- **处置**：`StageData` 加可选 `playerSpawn?: {col,row}` 与 `enemySpawns?: {col,row}[]`；`World.loadStageData` 缓存 `enemySpawnPoints = stage.enemySpawns ?? ENEMY_SPAWNS`、`playerSpawnPoint = stage.playerSpawn ?? PLAYER_SPAWN`；`spawnPlayer()` 与 `Simulation` 刷怪逻辑（:213/:544）改用这两个缓存值，缺省回退常量（data-over-code，AGENTS §2.4，对真关零影响）。`makeArena(opts)` 把出生点放进开敞区（玩家底部居中、敌人顶部 1~3 点）。

### 影响 2 — 敌我 AI 的"基地目标"必须忽略（无基地时）
- **God AI（缺口 B 展开）**：除防守定位外，以下子系统也引用 base，需在 `!hasBase` 时跳过/降级：
  - `urgencyBonus` 的 `distToBase`（改为以玩家/敌距离为基准或置 0）；
  - `findBulletThreatToBase` / `baseBulletInterceptCell` / T8 拦截（无基地子弹 → 不触发）；
  - `maxPlayerDistFromBase`、`defenseRowOffset` 等"离基地太远就回防"约束（无基地时**作废**，AI 应自由猎杀）；
  - `getDefaultDefensePosition`（返回 BASE_POS → 无基地时返回玩家当前格或最近敌）。
- **敌人 AI**：`perception.ts:170` 已有 `hasBase: !!base` 兜底（无基地时 `tx/ty` 回退到玩家、`baseDanger=0`，TacticalIntelligence.ts:334/207），**已基本健壮**。仍需**验证战略/指挥官层**不因 `!hasBase` 仍给"攻基地"加权（phantom-base rush）。处置：跑 `decision-trace` 抽 1 个无基地 seed，确认敌人不朝 `(12,24)` 空区域移动；若有，在敌人 AI 目标层加 `!hasBase` 早退。
- **语义变化（预期内）**：无基地关里敌人失去"攻基地获胜"目标，退化为**纯死斗（追玩家/游荡）**——这恰是阶段 1~3 想要的（只测 God AI 子系统，不测敌人保真度）。报告 curriculum 结果时要注明：无基地关**不反映敌人 AI 的真实分布**。

### 影响 3 — 胜负判定变化
- **现状（已查代码）**：`stage_clear` 触发条件为 `enemiesRemaining<=0 && 非 extra 敌全灭`（Simulation.ts:1617），**不要求基地存在**；`base_destroyed` 游戏结束在无基地时永不发生。所以无基地关只有 `lives_exhausted` / `timeout` / `stage_clear` 三种结局——与 curriculum 意图一致。
- **隐患 — BONUS TIME 窗口**：若场上还有存活 power-up，`stage_clear` 会延迟到拾取窗口结束（Simulation.ts:1617-1659）。阶段 1（火控）若敌人是 bonus carrier，掉道具会拖慢判定。
- **处置**：curriculum 关的敌种**避免 carrier**（或 `rules.dropSchedule` 关掉掉落），让 `enemiesRemaining` 归零即直接 `stage_clear`；断言对 `stage_clear` 的判定容忍短暂 BONUS TIME 窗口（或显式禁用掉落）。

### 影响 4 — 并发上限与竞技场尺寸
- **现状**：`MAX_ENEMIES_ALIVE=4`（constants.ts:40），同屏最多 4 敌。阶段 3（enemyCount=20）靠刷怪队列逐个补，同屏仍 ≤4。
- **问题**：8×8 开敞区（约 6×6 可走格）塞 4 坦 + 3 出生点，若出生点被占会刷怪阻塞 → `enemiesRemaining` 卡住 >0 → 误判 `timeout`。
- **处置**：`makeArena(opts)` 的竞技场尺寸随 `enemyCount` 缩放（阶段 3 用更大开敞区，如 16×16 内区），出生点分散且不易被堵；断言阶段 3 时监控 `enemiesSpawned` 单调推进，确认无刷怪死锁。

### 影响 5 — 非开敞瓦片（水/林/冰）的 AI 语义
- **处置（范围控制）**：阶段 1~4 竞技场**只用 open + steel 围栏**，不引入 water/forest/ice，以隔离被测子系统。若未来要测"林间隐蔽利用(S13)"等，再单独加 forest 瓦片并确认 God AI / 敌人 AI 的 LOS 处理——不在本阶梯内。

### 影响 6 — 渲染 / 相机 / 菜单（无影响，备忘）
- 战场恒为 416×416（26×26）；竞技场是其中一个子区域，steel 围栏 + 开敞区正常渲染，**无需相机改动**。
- 基地血条 HUD 在无基地时 `baseHp` 保持满值（cosmetic，无逻辑影响）。
- curriculum 关是**工具专用**（直接喂 `runSimulation`，不进 `STAGES` 数组），**不触发菜单预览 / 关卡选择**，无 UI 影响。

### 影响 7 — 确定性（无影响，备忘）
- 布局变化不改变 RNG 使用语义；curriculum 仍靠 `world.rng.reseed(seed)` 保持确定性，断言可复现。

---

## 4. 分阶段验证阶梯（5 关 + 集成回归）

每关绑定一个**可断言的子系统**。断言通过 = 该子系统在当前 GodAIInput 下成立；否则定位到具体技巧。

| # | 迷你关环境 | 验证的子系统（对应技巧） | 预期断言 |
|---|------------|--------------------------|----------|
| 1 | 开敞竞技场 · 1 basic · 无基地 | 火控命中（T2a/T2b）+ 无墙导航可达 | `outcome==stage_clear` 且 `firstKillTick` 较小（如 < 600） |
| 2 | 开敞竞技场 · 3~5 敌随机 · 无基地 | 威胁优先级（T3）+ 火控纪律（不被多目标扰乱） | `stage_clear` 且击杀 == 敌数（无漏杀/无自残） |
| 3 | 满编 `enemyCount=20` · 无基地 | **攻守切换 S6 / 终局猎杀 S10**（解锁 0%→过关的核心分支） | `stage_clear` 且未 `timeout`（限时内清场）。**本关是根因探针**：现有 `canHunt` 阈值 `enemiesRemaining<=3` 意味着整局 20 敌几乎都在"守幽灵基地"，直到最后 3 敌才猎杀——这极可能是 0% 胜率的真因；此关暴露它，修复见 §5.3 |
| 4 | 全 26×26 + 砖墙迷宫 · 无基地 | `directMove` 破墙追击能抵达并清掉需穿墙的敌（S2 咽喉若已实现也一并验） | `stage_clear` 且 `firstKillTick` 合理（不卡死）；**不再要求 A* 主导**——代码刻意用 `directMove` 追击（A* "太慢追不上游荡敌"，见 GodAIInput.ts 注释）。若坚持恢复 A* 需单独 DECISIONS 条目，不在本阶梯断言内 |
| 5 | 同 4 + 基地（经典规则） | 防守不被放弃（回归 shield 自毁 / 追敌外出 bug） | `baseAlive` 且 `stage_clear`（基地不被自己或放任的敌毁） |
| 集成 | **真实 stage 0（classic）** | 全系统集成 | CMA-ES fitness：`Hard≥70% / Chaos≥30%` 门禁（God-AI-Tuning §4） |

> 阶梯递进式循环：先在 1→5 逐级断言；任一关失败 → 只改对应子系统 → 重跑该关，再回 stage 0 回归。
> 玩具关**绝不**喂给 CMA-ES。

---

## 5. 实施包（具体改动清单）

> **执行顺序**：先完成 §0.5 的 GodAIInput 拆分（P0 前置，纯重构），再做以下 5.1–5.4。拆分后的小文件让 S6 阈值参数化（§5.3）与 curriculum 调试显著更容易。

### 5.1 基础设施（缺口 A/B + 出生点，详见 §3.5）
- [ ] `src/types.ts` — `StageData` 加 `enemyCount?: number`、**`playerSpawn?: {col,row}`、`enemySpawns?: {col,row}[]`**（影响 1：出生点失效）。
- [ ] `src/game/World.ts` — 加 `enemiesTotal`；`loadStageData` 用 `stage.enemyCount ?? ENEMIES_PER_STAGE`，缓存 `enemySpawnPoints = stage.enemySpawns ?? ENEMY_SPAWNS`、`playerSpawnPoint = stage.playerSpawn ?? PLAYER_SPAWN`；同步 `enemiesRemaining` 与刷怪循环；`totalEnemiesLeft` 改用 `enemiesTotal`；`spawnPlayer()` 用 `playerSpawnPoint`。
- [ ] `src/game/Simulation.ts` — 刷怪逻辑（:47/:213/:544）改用 `w.enemySpawnPoints`（替代模块级 `ENEMY_SPAWN_POINTS`）；`:245` `remainingSpawns = w.enemiesTotal - w.enemiesSpawned`。
- [ ] `src/snapshot/WorldSerializer.ts` — 纳入 `enemiesTotal`（保持快照/Recovery 安全）。
- [ ] `src/game/TileMap.ts` — 加 `hasBase(): boolean`。
- [ ] `src/ai/GodAIInput.ts` — `reset()` 缓存 `this.hasBase`；BASE_POS 相关逻辑（定位 / `urgencyBonus` 的 `distToBase` / `findBulletThreatToBase`+T8 拦截 / `maxPlayerDistFromBase`+`defenseRowOffset` 约束 / `getDefaultDefensePosition`）在 `!hasBase` 时跳过，降级为纯猎杀（影响 2 展开）。
- [ ] `src/ai/TacticalIntelligence.ts`（敌人 AI）— 抽 1 个无基地 seed 用 `decision-trace` 验证战略/指挥官层不因 `!hasBase` 仍朝 `(12,24)` 加权；若有，目标层加 `!hasBase` 早退（影响 2）。

### 5.2 低成本 Bug 修复 —— ✅ 已在代码中完成（仅验证，不重复实现）

经 gac.review.md 核查 + 代码确认，以下 4 项**已修复**，从实施包移除：

- `urgencyBonus` 反转（Bug 1）→ GodAIInput.ts:1288-1291 已有 "Fix Bug 1" 注释与正确公式。
- `findPowerUpTarget` 评分方向反转（Bug 2/3）→ GodAIInput.ts:955 / 975 已修，公式 `(6 - priority) * 1000`。
- `killerKind` 填充（Bug 5）→ tools/simulation-runner.ts:171-178 已回填。
- `failure!` 空断言（Bug 6）→ tests/god-ai-gates.test.ts:23-26 已加 `stage_clear` 守卫。

> 落地时只需用 `decision-trace.ts` 复验这 4 处行为仍正确，无需改代码。

### 5.3 解锁过关的核心分支（S6 已部分实现，重点在"参数化 + 调保守度"）

代码**已有** S6 雏形：`selectTarget`（GodAIInput.ts:1225）的 `canHunt = enemies.length <= 2 && w.enemiesRemaining <= 3 && !baseUnderThreat`，
命中时直接追最近敌（:1261）。但有两个问题：

1. **`endgameEnemyThreshold` 声明未用（潜在 bug）**：`GodAIParams` 有该字段（:87，默认 1），但 `canHunt` 里硬编码了 `2` / `3`，未读取它。
2. **阈值过保守（0% 胜率疑似真因）**：`enemiesRemaining <= 3` 让 AI 整局 20 敌几乎都在"防守模式"对抗（可能不存在的）基地，直到最后 3 敌才猎杀 → 清不完场 → timeout。

**改动清单**：
- [ ] 把 `canHunt` 的硬编码 `2` / `3` 替换为读取 `GodAIParams`：新增 `huntAllyCount`（场上敌数上限，替代 `2`）与复用/重命名 `endgameEnemyThreshold`（剩余敌数上限，替代 `3`）；删掉未用的孤立 `endgameEnemyThreshold` 或把它接上，消除潜在 bug。
- [ ] 在阶段 3（满编无基地）上**调保守度**：先放宽 `endgameEnemyThreshold`（如 5~8）与 `huntAllyCount`（如 4~6），让 AI 更早转入猎杀；用 `tools/curriculum.ts` 阶段 3 断言验证"限时清场"。**这步很可能直接把 0% 抬过门禁**。
- [ ] 若放宽后仍 timeout：再做 **M1 预判射击**（朝敌人未来位置射击，解决命中率），作为阶段 3 的后续项。
- [ ] 注意依赖：§3 缺口 B（hasBase 守卫）是阶段 1–3 的**硬前置**——`canHunt` 的 `!baseUnderThreat` 在无基地时指向 `BASE_POS`，必须先修。

### 5.4 验证脚手架
- [ ] 新建 `tools/curriculum.ts`：
  - `CurriculumStage` 类型 = `{ id, desc, stage: StageData, params: GodAIParams, assert: (SimResult)=>boolean }`。
  - 辅助 `makeArena(opts)` 程序化生成 26×26 开敞竞技场：参数含 `size`（开敞区边长，阶段 3 随 enemyCount 放大，影响 4）、`base`（是否含基地）、`enemyCount`、敌种（默认**非 carrier**，避免 BONUS TIME 拖慢判定，影响 3）、`playerSpawn`/`enemySpawns`（放进开敞区，影响 1/4）。四周 steel 围栏，内部全 open；无基地关省略 `E`。
  - 依次 `runSimulation` 并执行 `assert`，输出通过/失败表。
  - CLI：`bun tools/curriculum.ts`（可加 `--only 3` 只跑单关）。
- [ ] `package.json` 加 `scripts.curriculum`（与 `optimize-godai` 并列）。

---

## 6. 门禁与验收

- **每关断言全绿** = 对应子系统在当前 GodAIInput 下成立（CI 回归门禁）。
- **阶段 3（满编无基地）断言通过** = S6 攻守切换已解锁清场能力（0%→>0% 的关键信号）。
- **最终验收不变**：真实 stage 0（classic）Hard≥70% / Chaos≥30%（God-AI-Tuning §4 门禁）。
- 反模式守门：CI 中 `optimize-godai.ts` 的 `--stage` 只允许真实关索引，禁止把迷你关当 fitness。

---

## 7. 风险与反模式

- ❌ **玩具关当 CMA-ES fitness**：过拟合玩具、校准基准失真、真关仍 0%。
- ⚠️ `enemiesTotal` 漏进 `WorldSerializer` → RecoverySystem 下敌数错乱（确定性破坏）。
- ⚠️ 无基地守卫不彻底 → GodAIInput 仍在 `(12,24)` 空转，阶段 1/2/3 假阴性（看似能赢其实在守幽灵）。
- ⚠️ 阶段 4 断言需贴合现状：代码**刻意用 `directMove` 追击**（A* 被认为太慢追游荡敌），`followPath` 仅用于闪避/aggressive。不要再断言"A* 主导"，应断言"directMove 破墙能抵达并清掉需穿墙的敌、不卡死"。若想恢复 A* 追击，需单独 DECISIONS 条目，不在本阶梯内。
- ⚠️ **拆分 GodAIInput 引入行为漂移**：纯搬方法极易在重排时误改逻辑。必须用 §0.5 的 parity 测试（固定 seed 时序锁定）兜底；拆分 PR 单独提交、不夹带任何逻辑改动。

---

## 8. Definition of Done

- [x] §0.5 GodAIInput 拆分完成，`tests/godai-split-parity.test.ts` 锁定时序通过（零行为改变），`bun run check` 全绿（457 测试）。
- [ ] 缺口 A/B + 出生点字段（`playerSpawn`/`enemySpawns`）落地，`makeArena` 能生成合法开敞竞技场；`bun run check` 全绿。
- [ ] 无基地关：God AI 与敌人 AI 均忽略基地目标（`hasBase` 守卫 + `decision-trace` 抽验无 phantom-base rush），出生点重配进开敞区，`stage_clear` 不依赖基地（影响 1/2/3 已处置）；阶段 3 无刷怪死锁（`enemiesSpawned` 单调推进，影响 4）。
- [ ] `tools/curriculum.ts` 存在，5 关断言可在 CI 跑，输出通过表。
- [ ] `urgencyBonus` / `powerup score` / `killerKind` / `failure!` 断言 **已确认在代码中修复**（decision-trace 复验，不重复实现）。
- [ ] S6 `canHunt` 阈值参数化（`endgameEnemyThreshold` 接上、硬编码 2/3 移除），阶段 3 断言通过（满编无基地能限时清场）。
- [ ] 阶段 1→5 全绿后，真实 stage 0 回归：Hard≥70% / Chaos≥30% 门禁达成（或记录达极限的判定）。
- [ ] 调校日志（`docs/god-ai-tuning-log.md`）追加本轮：改动 / 各关断言结果 / 阶段 3 是否解锁 / 门禁实测值。
- [ ] DECISIONS.md 记录：分阶段验证框架的决策与"玩具关≠训练环境"约束。

---

*推导依据：docs/god-ai-tuning-log.md（Round 1–2 数据）、plan/god-ai-analysis.md（Bug 1–6 + 架构问题）、plan/god-ai.progress.md（当前 0% 胜率状态）、plan/gac.review.md（新鲜度核查）、src/ai/GodAIInput.ts / src/game/World.ts / src/game/Simulation.ts / src/ai/perception.ts 代码核查。*
