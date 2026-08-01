# 自动化关卡设计与仿真系统 — 实施计划

> 基于 `docs/automated-level-design-analysis.md`（**前置依赖，待补写，见 Phase 0 阻断项**）的完整工程化实施计划。
> 遵循 MANIFEST 三道关卡：更享受、架构简洁、尊重原版。

---

## 1. 目标与范围

### 1.1 核心目标

建立一套**自动化关卡生成 → 无头仿真 → 智能筛选 → 人工试玩**的完整流水线，使游戏能持续产出高质量、可玩性经验证的新关卡。

### 1.2 交付物

| 产出 | 说明 |
|------|------|
| `src/utils/pathfind.ts` | **新建**：通用 A* 寻路 + BFS 可达性 (纯函数，God AI 与关卡生成器共用) |
| `src/ai/GodAIInput.ts` | 完美玩家模拟器 (实现 `InputLike` 接口) |
| `tools/optimize/level-sim.ts` | 无头仿真 CLI，支持批量并行运行 |
| `tools/level/level-gen.ts` | 关卡生成器 (细胞自动机 + 约束覆盖 + A* 验证) |
| `tools/level-eval.ts` | 筛选器 (硬性门槛 + 软性复合指标 + 基准校准) |
| `evaluation-baseline.json` | 35 经典关卡实测反向拟合的黄金标准 |
| `ai-baseline.json` | God AI + 敌人 AI 联合调校后的固定对手基准 (含 Skilled Human 代理参数) |
| `generated-stages.json` | 通过筛选的新关卡库（单一 JSON 文件，含 `StageData[]`），供人工试玩/发布（运行期接入见 §3.6） |

### 1.3 非目标 (Out of Scope)

- 进化算法优化生成器参数 (后续扩展)
- 人类反馈闭环 / 偏好模型 (后续扩展)
- 关卡可视化编辑器 (后续扩展)
- Replay 系统集成 (独立任务)

---

## 2. 架构设计

### 2.1 系统组件图

```
┌─────────────────────────────────────────────────────────────────┐
│                    LevelGenerator (纯函数)                       │
│  输入: seed, difficulty, constraints, theme                     │
│  输出: StageData { tiles[26], enemies[20] }                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  SimulationRunner (无头驱动)                     │
│  - World.rng.reseed(seed)                                       │
│  - World.loadStageData(stage) 【新增 API, Phase 0.3】/ 复用 spawnQueue 构建 │
│  - Simulation + GodAIInput                                      │
│  - 循环 tick() 直到 stage_clear / gameover / maxTicks           │
│  - 收集: events, finalState, metrics (逐帧采样)                 │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Evaluator (筛选器)                            │
│  - 硬性指标: 过关率、游戏时间 P90、基地存活、玩家存活           │
│  - 软性指标: KPM、子弹密度、威胁事件率、阵型变化度、击杀多样性、│
│              道具拾取率、地形利用率、视觉连贯性、无死角         │
│  - 基准校准: evaluation-baseline.json (经典关卡实测反向拟合)    │
│  - 输出: PASS/FAIL + 详细报告 + 评分                             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              ▼                         ▼
       ┌─────────────┐           ┌─────────────┐
       │ 通过 → 入库  │           │ 失败 → 丢弃  │
       │ 供人工试玩   │           │ 记录失败原因  │
       └─────────────┘           └─────────────┘
```

### 2.2 核心数据流

```
Seed + Difficulty + Theme
        │
        ▼
LevelGenerator.generate() → StageData
        │
        ▼
SimulationRunner.run(seed, stage, difficulty) → SimResult
        │
        ├─ events[] (GameEvent 流)
        ├─ finalState (World 终局快照)
        └─ metrics (逐帧采样: bullets.length, tanks positions, enemyCount, ...)
        │
        ▼
Evaluator.evaluate(result, baseline) → EvaluationReport
        │
        ├─ hardPass: boolean (所有硬性门槛通过)
        ├─ softScore: number (0-100, 软性指标加权)
        ├─ totalScore: number (hardPass ? 1000 + softScore : 0)
        └─ details: { metricName: { value, target, normalized, weight } }
        │
        ▼
totalScore ≥ 700 且 hardPass → PASS → generated-stages.json
```

---

## 3. 关键设计决策

### 3.1 God AI 设计原则

| 原则 | 实现 |
|------|------|
| **复用而非重写** | 复用 `TacticalIntelligence.perception` (全图视野)、威胁评估；A* 寻路使用新建的 `src/utils/pathfind.ts` |
| **注入不完美** | 2-3 帧反应延迟、±2° 瞄准抖动、10% 次优路径概率，避免过强导致筛选失效 |
| **实现 `InputLike` 接口** | 需先从 `Input` 类提取 `InputLike` 接口 (`getMoveDirection()`, `isFiring()`, `endFrame()`, `reset()`)，`Simulation` 字段改为 `InputLike` 类型 (见 §3.4) |
| **战力评估驱动策略** | 实时计算我方/敌方战力比 → 动态激进/保守阈值 |

> **确定性约束**：God AI 注入的全部不完美（反应延迟、瞄准抖动、次优路径概率）**必须走 `world.rng`**，禁止 `Math.random()`（违反 AGENTS §2.3，且破坏基于 `reseed` 的校准可复现性）。`GodAIInput` 持有的 AI 状态需在每局开始 `reset()` / 重挂 `world.rng`，以保证同种子同结果。

### 3.2 战斗激烈度复合指标 (替代失效的"平均存活敌人数")

| 子指标 | 目标区间(初稿) | 权重 | 计算方式 |
|--------|----------------|------|----------|
| **KPM** (击杀/分) | 8~14 | 30% | `killCount / (playTimeMs / 60000)` |
| **子弹密度** (发/帧) | 15~35 | 25% | 逐帧 `world.bullets.length` 平均 |
| **威胁事件率** (次/秒) | 0.8~2.0 | 25% | 逐帧计算：敌人子弹预测轨迹与玩家当前列/行相交且 N tick 内到达的子弹数 (真实威胁代理，非 `bullet_fired` 总数) |
| **阵型变化度** (平均配对距离) | > 阈值 (校准定) | 20% | 逐帧存活敌人间平均配对曼哈顿距离，再取全帧平均 (空间分散度，避免扎堆) |

> **注**：`MAX_ENEMIES_ALIVE = 4` 导致同时存活数恒定饱和，单一指标无区分度，必须用复合指标。

### 3.3 两大强制校准流程 (执行顺序固定：先 AI 调校，后评分校准)

#### A. AI 联合调校 (AI Calibration) — **第一步，不可跳过**

> **必须先完成 AI 调校，确立"标准挑战强度"，再进行评分体系校准。**
> 否则评分基准会针对"未校准的 AI"，导致后续生成关卡对真人不公平。

| 阶段 | 动作 | 完成标准 |
|------|------|----------|
| 1. God AI 基准跑分 | 35关×100盘×2难度 | 记录过关率/时长/死亡数 |
| 2. God AI 迭代优化 | 针对失败盘分析优化寻路/火控/威胁/战力 | Hard ≥70%, Chaos ≥30% |
| 3. God AI 极限判定 | 连续3轮 Δ<2% 且逻辑完备 | 认定达极限 |
| 4. 敌人 AI 兜底调校 | 降 `INTELLIGENCE_LEVELS` 能力、调 `DIFFICULTY_TIER_DISTRIBUTION`、改 `COMMANDER_FLOOR` | 经典关卡过关率达标 |
| 5. 固化 AI 基准 | 输出 `ai-baseline.json` | 后续仿真固定对手 |

> **核心逻辑**：God AI 代表"理论上限玩家"，敌人 AI 代表"标准挑战强度"。联合调校确立固定对手基准，后续所有仿真均面对此标准对手。

#### B. 评分体系基准校准 (Evaluation Calibration) — **第二步，不可跳过**

> **在 AI 基准固定后，用经典关卡实测反向拟合评分阈值与权重。**

1. 跑 35 经典关卡 × 100 盘 × (hard/chaos) = 7000 盘基准仿真 (使用 `ai-baseline.json` 固定的对手)
2. 收集全量指标实测分布 (箱线图/小提琴图)
3. 标记"公认好玩" vs "有问题"关卡
4. 反向拟合：好关卡指标区间 → 目标区间；坏关卡指标区间 → 拒收区间
5. 区分度分析调整权重 (AUC/互信息)
6. 输出 `evaluation-baseline.json` (黄金标准)

#### C. Skilled Human 代理验证 — **第三步，防止 AI 调校陷阱**

> **AI 调校若将敌人 AI 调弱以让 God AI 达标，评分基准会针对"弱敌人"校准，导致生成关卡对真人 (面对更强敌人) 偏难。**

1. 定义 **Skilled Human 代理**：God AI + 双倍反应延迟 + 20% 瞄准误差 (模拟有经验但非完美的真人玩家)
2. 用 Skilled Human 代理重跑 35 经典关卡 (hard/chaos 各 100 盘)
3. **验证门槛**：Skilled Human 代理在经典关卡上 Hard 过关率 ≥ 50%、Chaos ≥ 15%
4. 若 Skilled Human 代理不达标 → 说明敌人 AI 仍过强，需回到步骤 A 继续调校
5. 将 Skilled Human 代理参数记录在 `ai-baseline.json` 中，作为评分校准的第二参考点

> **核心逻辑**：God AI 代表"理论上限玩家"，Skilled Human 代理代表"真人上限玩家"，敌人 AI 代表"标准挑战强度"。三者联合校准确保筛选器面对"校准后的标准对手"，生成关卡对真人公平。

### 3.4 提取 `InputLike` 接口 (前置依赖)

> **问题**：`Input` 是具体类 (`src/game/Input.ts:134`)，`Simulation` 以具体类型引用 (`input: Input`)。不存在 `InputLike` 接口，`GodAIInput` 无法"实现 Input 接口"。

**方案**：从 `Input` 类提取最小接口 `InputLike`，`Simulation` 改为依赖接口：

```typescript
// src/game/Input.ts — 新增导出
export interface InputLike {
  getMoveDirection(): Direction | null
  isFiring(): boolean
  endFrame(): void
  reset(): void
}
// class Input implements InputLike { ... }  // 现有类加 implements

// src/game/Simulation.ts — 类型改为接口
import type { InputLike } from './Input'
export class Simulation {
  input: InputLike  // ← 原为 Input
  constructor(world: World, input: InputLike) { ... }
}
```

**影响**：纯类型变更，无行为改变，不违反 One Author 不变量。`Simulation` 仅调用 `getMoveDirection()` 和 `isFiring()` 两个方法 (已验证)。需在 `DECISIONS.md` 记录。

### 3.5 新建通用寻路模块 `src/utils/pathfind.ts` (前置依赖)

> **问题**：代码库中**不存在**任何 A*/BFS/寻路算法 (已验证：`src/` 全量搜索 `aStar|pathfind|BFS|findPath` 零匹配)。God AI (导航) 和关卡生成器 `validateStage()` (可达性验证) 都需要寻路，必须从零实现。

**方案**：在 `src/utils/pathfind.ts` 实现纯函数寻路模块：

```typescript
// src/utils/pathfind.ts — 新建
/** A* 寻路：返回从 from 到 to 的方向序列，或 null (不可达) */
export function findPath(
  tileMap: TileMap, from: {col,row}, to: {col,row},
  constraints?: { ignoreWater?: boolean, tankSize?: number }
): Direction[] | null

/** BFS 可达性：返回 from 是否可达 to */
export function isReachable(
  tileMap: TileMap, from: {col,row}, to: {col,row}
): boolean

/** Flood-fill：返回从 from 可达的所有格子 (用于关卡生成器连通性验证) */
export function floodFill(
  tileMap: TileMap, from: {col,row}
): Set<string>
```

**设计原则**：
- 纯函数，不修改 World/TileMap
- 放在 `utils/` 因为 God AI (game 层) 和关卡生成器 (tools 层) 都需要
- 战斗单位占 2×2 格，寻路需考虑战斗单位尺寸 (constraints.tankSize)
- 需在 `DECISIONS.md` 记录
- **关卡字符契约**：生成器输出 `tiles: string[]` 必须使用 `TileMap.charToTerrain` 识别的字符集——`.`(空)/`b`(砖)/`s`(钢)/`w`(水)/`f`(林)/`i`(冰)/`E`(基地)。**不得使用**经典 `LEVELS` 的 13×13 数字编码（那是经 `stages.ts` 解码为 26×26 的）；生成器直接产出 26×26 `StageData`。

---

### 3.6 生成关卡的运行期接入 (Runtime Consumption)

筛选产出的 `generated-stages.json` **不会自动进入游戏**——当前游戏仅从 `STAGES` 配置按索引加载 (`World.loadStage(index)`)。必须定义明确的接入路径，否则「供人工试玩/发布」无法落地：

- **启动期注入**：在 `STAGES` 之外维护 `GENERATED_STAGES: StageData[]`，关卡选择 UI 提供「经典 / 生成」切换入口；
- **或合并构建**：在 `src/config/stages.ts` 组装 `STAGES` 时并入 `generated-stages.json` 内容（需 JSON 加载器，注意与现有 `LEVELS` 13×13→26×26 解码路径区分）；
- **人工试玩验证**：提供 `tools/level/play-generated.ts` 或复用 `previewStage` / 新增的 `loadStageData` 在本地直接加载单关验证。

无论哪种路径，生成的 `StageData` 必须完全符合 §3.5 的字符契约，且 `enemies` 为 `TankKind[]`（生成器填充 20 只编队，沿用 `ENEMIES_PER_STAGE` 循环取样逻辑，`World.loadStageData` 已内置）。

---

## 4. 实施路线图

### Phase 0: 前置依赖 (1-2 天)

> **前置依赖（阻断项）**：本计划的方法论论证（复合战斗激烈度指标、校准流程、基准阈值推导）原依赖 `docs/automated-level-design-analysis.md`。该文档当前**不存在**——执行前须先补写该分析文档（或将其核心论证内联到本计划 §3），否则 Phase 3 校准缺乏依据。分析文档至少应覆盖：(1) 为何 `MAX_ENEMIES_ALIVE=4` 导致单指标失效、需复合指标；(2) 各软性指标目标区间的初稿来源；(3) AI 调校→评分校准→Skilled Human 验证三步法的统计依据；(4) 35 经典关「公认好玩/有问题」标注的来源。

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 0.1 提取 `InputLike` 接口 | `src/game/Input.ts` + `src/game/Simulation.ts` 修改 | `Input` 类 `implements InputLike`；`Simulation.input` 类型改为 `InputLike`；`bun run check` 绿 |
| 0.2 实现通用寻路模块 | `src/utils/pathfind.ts` | `findPath()` / `isReachable()` / `floodFill()` 纯函数，含单元测试 |
| 0.3 新增自定义关卡加载 API | `src/game/World.ts` 增加 `loadStageData(stage: StageData)` | 复用 `loadStage(index)` 的 spawnQueue 构建与状态重置逻辑，但接受任意 `StageData`；无头仿真可注入生成关卡；`bun run check` 绿 |
| 0.4 DECISIONS.md 记录 | 三条新决策 | InputLike 提取 + pathfind.ts 新建 + loadStageData 新增 |
| 0.5 单元测试 | `tests/pathfind.test.ts` | A* 正确性、不可达返回 null、floodFill 连通性验证；另含 `loadStageData` 加载生成关卡的冒烟测试 |

### Phase 1: 核心仿真引擎 (2 天)

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 1.1 创建 `tools/optimize/level-sim.ts` | 无头仿真 CLI | `bun tools/optimize/level-sim.ts --stage 0 --difficulty hard --seed 123` 可跑通并输出 JSON |
| 1.2 实现 `GodAIInput` | `src/ai/GodAIInput.ts` | 实现 `InputLike` 接口，使用 `pathfind.ts` 寻路，能驱动玩家移动/开火 |
| 1.3 实现 `SimulationRunner` | `tools/sim/simulation-runner.ts` | `run(seed, stage, difficulty)` → `SimResult` |
| 1.4 实现 `Evaluator` | `tools/eval/evaluator.ts` | `evaluate(result, baseline)` → `EvaluationReport` |
| 1.5 单元测试 | `tests/level-sim.test.ts` | 确定性验证：同种子同结果；指标计算正确 |

**技术要点**：
- 复用 `tools/perf/sim-bench.ts` 的无头启动模式
- `World.rng.reseed(seed)` 保证确定性
- `world.consumeEvents()` 逐帧收集事件流
- 逐帧采样 `world.bullets.length`、`world.tanks` 位置、`world.enemyCount`

### Phase 2: 关卡生成器 (2-3 天)

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 2.1 实现 `LevelGenerator.generate()` | `tools/level/level-gen.ts` | 输入 seed/difficulty/theme → 输出合法 `StageData` |
| 2.2 分层生成算法 | 细胞自动机 + 约束覆盖 | 7 层生成流程完整跑通 |
| 2.3 连续性算法 | `growCluster()` 等 | 同类地形聚类，最小簇 ≥4 格 |
| 2.4 硬性约束验证 | `validateStage()` (使用 `pathfind.ts` 的 `isReachable`/`floodFill`) | A* 可达性、基地保护、出生点安全全通过 |
| 2.5 失败重试机制 | 最多 10 次重试 | 约束冲突时自动重试，最终产出合法关卡 |

**生成流程**：
```
1. 选主题种子 (森林/冰原/要塞/混合)
2. Layer 0: 26×26 全 '.'
3. Layer 1: 基地区 (24-25, 12-13) → 'E'
4. Layer 2: 核心屏障带 (18-23, 10-15) → 高概率 steel/brick
5. Layer 3: 主干道网络 → 保证 3出生点→基地连通
6. Layer 4: 战术掩体簇 (brick/steel, size 3-8)
7. Layer 5: 环境地形簇 (water/forest/ice, size 4-12)
8. Layer 6: 细节噪声 (单格 brick/steel, 5%)
9. Layer 7: 敌人编队 (20只，按难度分布采样)
10. 强制覆盖核心约束区
11. A* 可达性验证 → 失败重试 (≤10次)
12. 输出 StageData
```

### Phase 3a: 校准与批量仿真基础设施 (1-2 天)

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 3a.1 批量运行器 | `tools/sim/batch-sim.ts` | `for seed in 0..99: run() → collect` 并行执行 |
| 3a.2 统计聚合报告 | （已移除） | 通过率、P90时间、软指标分布、死亡热力图 |
| 3a.3 AI 调校脚本 | `tools/eval/ai-calibrate.ts` | God AI 迭代 + 敌人 AI 兜底 + Skilled Human 代理验证 |
| 3a.4 评分校准脚本 | `tools/eval/calibrate.ts` | 跑 35 经典关 (用 ai-baseline.json) → 反向拟合阈值权重 |

**并行化策略**：
- 使用 `worker_threads` 多进程跑仿真 (每进程独立 World/Simulation)
- 或加速模式：2× speed (tick 间隔减半，固定种子)

### Phase 3b: 校准执行与关卡筛选 (1-2 天，计算密集)

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 3b.1 执行 AI 调校 | `ai-baseline.json` | God AI 经典关卡 Hard ≥70%、Chaos ≥30%；Skilled Human 代理 Hard ≥50%、Chaos ≥15% |
| 3b.2 执行评分校准 | `evaluation-baseline.json` | 含最终阈值/权重/归一化参数/35关基准分布 |
| 3b.3 批量仿真筛选 | `generated-stages.json` | 100 生成关卡跑仿真，≥ 50 个通过筛选 |
| 3b.4 筛选器验证 | 一致率报告 | 筛选器对经典 35 关判定与"公认好玩/有问题"标记一致率 ≥ 90% |

> **注意**：Phase 3b 是计算密集阶段。AI 调校需 35关×100盘×2难度 = 7000 盘 (God AI) + 7000 盘 (Skilled Human) = 14000 盘；评分校准再需 7000 盘。即使 4 worker 并行 + 2× 加速，仍需数小时。若 God AI 需多轮迭代优化，耗时更长。

### Phase 4: 集成与工具化 (1 天)

| 任务 | 产出 | 验收标准 |
|------|------|----------|
| 4.1 CLI 入口 | `package.json` scripts | `bun run gen-levels --count 100 --difficulty hard` |
| 4.2 仿真 CLI | `package.json` scripts | `bun run sim-levels --input generated-stages.json --difficulty hard` |
| 4.3 AI 调校 CLI | `package.json` scripts | `bun run ai-calibrate --difficulty hard` |
| 4.4 评分校准 CLI | `package.json` scripts | `bun run calibrate --difficulty hard` |
| 4.5 关卡缩略图生成 | `tools/level/gen-thumbnails.ts` | 复用 `TileMap` + `SpriteCache` 离屏渲染 PNG |

---

## 5. 代码复用清单

| 现有文件 | 复用内容 | 新建文件 |
|----------|----------|----------|
| `tools/perf/sim-bench.ts` | 无头 World/Simulation 启动模式 | `tools/optimize/level-sim.ts` |
| `src/ai/TacticalIntelligence.ts` | 感知/寻路/威胁评估算法 | `src/ai/GodAIInput.ts` |
| `src/game/TileMap.ts` | 地形加载/查询/销毁 + 静态谓词 (`blocksTank`/`blocksBullet`/`isDestructible`/`isSteel`)。**不含**可达性/A*——寻路由新建的 `src/utils/pathfind.ts` 提供，生成器与 God AI 共用 | `tools/level/level-gen.ts` (复用地形查询) |
| `src/config/stages.ts` | `StageData` 结构、字符编码 | - |
| `src/snapshot/WorldSerializer.ts` | 完整状态序列化 (可选，回放用) | - |
| `src/utils/RNG.ts` | 确定性随机数 | - |
| `src/ai/config.ts` | `INTELLIGENCE_LEVELS`、分布表 | - (调校目标) |
| `src/constants.ts` | `MAX_ENEMIES_ALIVE`、`ENEMIES_PER_STAGE` 等 | - |

---

## 6. 验收标准 (Definition of Done)

### 6.1 Phase 1 完成标准

- [ ] `bun tools/optimize/level-sim.ts --stage 0 --difficulty hard --seed 1` 输出完整 JSON 报告
- [ ] `GodAIInput` 能在无渲染下驱动玩家完成基本移动/射击
- [ ] `SimulationRunner` 正确处理 `stage_clear`/`gameover`/`maxTicks` 三种终局
- [ ] `Evaluator` 硬性指标判定正确，软性指标计算与定义一致
- [ ] 同种子同关卡同难度跑 3 次，结果完全一致 (确定性验证)
- [ ] `bun run test` 通过 (含 `tests/level-sim.test.ts`)

### 6.2 Phase 2 完成标准

- [ ] `LevelGenerator.generate(seed, 'hard', 'forest')` 输出合法 `StageData`
- [ ] 生成关卡 100% 通过 `validateStage()` (A* 可达、基地保护、出生点安全)
- [ ] 地形连续性：同类地形平均簇大小 ≥ 4，无孤立单格 (除噪声层)
- [ ] 主题区分度：森林主题森林格子占比 ≥ 30%，冰原主题冰面 ≥ 25%，要塞主题钢铁 ≥ 20%
- [ ] 生成 100 关平均耗时 < 50ms/关 (含重试)

### 6.3 Phase 3 完成标准

- [ ] 批量跑 100 个生成关卡 (Hard) 全流程自动化，输出聚合报告
- [ ] **AI 联合调校完成 (先完成)**：`ai-baseline.json` 产出，God AI 在经典关卡 Hard ≥70%、Chaos ≥30%
- [ ] **评分体系校准完成 (后完成)**：`evaluation-baseline.json` 产出，包含最终阈值/权重/归一化参数/35关基准分布 (使用 ai-baseline.json 固定对手)
- [ ] 筛选器对经典 35 关的判定与"公认好玩/有问题"标记一致率 ≥ 90%
- [ ] `generated-stages.json` 产出 ≥ 50 个通过筛选的新关卡

### 6.4 Phase 4 完成标准

- [ ] `bun run gen-levels --count 100 --difficulty hard` 一键生成 100 关
- [ ] `bun run sim-levels --input generated-stages.json --difficulty hard` 一键仿真筛选
- [ ] `bun run ai-calibrate --difficulty hard` 一键跑 AI 联合调校
- [ ] `bun run calibrate --difficulty hard` 一键跑评分体系校准
- [ ] 关卡缩略图 PNG 生成正常，尺寸 256×256，视觉可辨识

### 6.5 全局质量门禁 (每 Phase 必须通过)

- [ ] `bun run check` 绿 (test + typecheck + lint + format)
- [ ] `bun run build` 成功
- [ ] 无新增 `Math.random()` 在 Simulation 路径
- [ ] 无新增模块级可变游戏状态
- [ ] 无新增 UI 绘制在游戏 Canvas 上
- [ ] 60 FPS 维持 (仿真模式下指 tick 成本 < 6ms p95)

---

## 7. 风险与对策

| 风险 | 可能性 | 影响 | 对策 |
|------|--------|------|------|
| God AI 过强 → 筛选失效 | 高 | 所有关卡通过率 100% | 注入不完美模型 (延迟/抖动/次优路径)；若仍过强则进入 AI 调校流程增强敌人智能 |
| 仿真太慢 (100盘×5分钟) | 中 | 迭代周期长 | `worker_threads` 多进程并行；加速模式 (2× speed)；CI 只跑采样 |
| 生成关卡同质化 | 中 | 玩家厌倦 | 4 种主题种子 + 连续性算法 + 熵值指标筛选 |
| 基地保护约束过严 → 生成失败率高 | 低 | 生成效率低 | "生成后修补"而非"约束采样"：先生成再强制覆盖核心区 |
| 校准阈值主观偏差 | 中 | 筛选器与真实体验脱节 | **强制用 35 经典关卡实测反向拟合**，而非主观拍脑袋 |
| God AI 优化陷入局部最优 | 中 | 过关率卡住 | 设定"连续 3 轮 Δ<2% 判定极限"，强制转入敌人 AI 兜底调校 |

---

## 8. 符合 MANIFEST 三道关卡验证

| 关卡 | 验证方式 |
|------|----------|
| **更享受** | 自动生成无限关卡 + 仿真保证质量 → 玩家永远有新鲜、公平、好玩的关卡 |
| **架构简洁** | 复用现有 Simulation/World/AI/TileMap/RNG；新增代码位于 `tools/`、`src/utils/pathfind.ts`、`src/ai/GodAIInput.ts`（**均不进入可玩构建**，符合 §2.5「展示层可丢弃」精神），无框架依赖 |
| **尊重原版** | 关卡格式完全兼容原版 26×26 网格，敌人编队沿用原版 20 只轮换，难度仅通过 AI 分布区分 |

---

## 9. 后续扩展 (不在本计划范围)

- **进化算法**：用遗传算法优化生成器参数，以通过率为适应度
- **人类反馈闭环**：人工试玩打分 → 训练偏好模型 → 指导生成器
- **Replay 系统集成**：保存 God AI 的完美通关录像作为"攻略演示"
- **关卡编辑器**：可视化调整生成参数，实时预览仿真结果
- **无尽模式 / 塔防模式 / Boss 模式** 的专用生成器变体

---

## 10. 里程碑时间表 (预估)

| 里程碑 | 预估工期 | 关键产出 |
|--------|----------|----------|
| M1: 核心仿真引擎就绪 | 2 天 | `level-sim.ts`、`GodAIInput`、`SimulationRunner`、`Evaluator` |
| M2: 关卡生成器就绪 | 3 天 | `level-gen.ts`、分层生成、约束验证、重试机制 |
| M3: AI 调校 + 评分校准 + 批量仿真就绪 | 3 天 | `ai-calibrate.ts` → `ai-baseline.json`、`calibrate.ts` → `evaluation-baseline.json`、`batch-sim.ts`、`report.ts` |
| M4: CLI 集成+缩略图 | 1 天 | `gen-levels`、`sim-levels`、`ai-calibrate`、`calibrate`、`gen-thumbnails` |
| **总计** | **~9 天** | **完整流水线可用** |

---

*计划版本: 1.1*
*创建日期: 2026-07-27*
*基于分析文档: `docs/automated-level-design-analysis.md`（**前置依赖，待补写，见 Phase 0 阻断项**）*
*遵循: `AGENTS.md`、`MANIFEST.md`、`DECISIONS.md`*
