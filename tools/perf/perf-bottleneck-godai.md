# God AI 仿真性能瓶颈分析报告

**日期**：2026-07-29
**工作流**：性能剖析 / 瓶颈定位（Workflow 0 — 尚未进入优化阶段）
**参与成员**：Zhen（工程督导，主导）；剖析框架由 `profile-godai.ts` + `analyze-profile.ts` 承载

---

## 📌 TL;DR（执行摘要，3-5 行）

- 测量对象：God AI 真实调参负载 `runSimulation`（World + `Simulation.tick` + `GodAIInput.endFrame`），30 局 chaos / stage 0，共 107,371 ticks。
- 已建立可复用剖析框架：Bun 原生 `--cpu-prof` 采集 V8 `.cpuprofile`，`analyze-profile.ts` 按「自耗时（self-time）」聚合为函数级与模块级热点。
- 瓶颈高度集中：**前 5 个函数占全部自耗时的 47.1%，前 10 个占 60.1%**。
- 最大单点：`perceive`(17.8%) + `scanAhead`(8.7%) + `canStep`(4.1%) + `analyze`(1.6%) = `ai/perception.ts` 独占 **32.3%**，是绝对第一杠杆。
- 次高：`findPath` 9.6%（A* 字符串键 + Map/Set + `split` 解析）、God AI 决策管线 ~16.7%（ThreatAssessor/FireControl/Navigator/StrategyPlanner/GodAIInput）。
- **本次仅定位瓶颈，未改动任何业务代码**（依用户指示）。所有热点均为「冗余扫描 / 分配 / 字符串键」类纯开销，优化不改变 AI 决策，校准有效性可保留。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🟡 已定位瓶颈，待优化（尚未动手） |
| 阻塞项数量 | 0（无正确性 / 架构阻塞） |
| 关键行动项 | 4 个候选优化集群（见下，均未执行） |
| 建议下一步 | 经确认后按 Cluster A → B → C 顺序优化，每步复核确定性 |

---

## 🔍 瓶颈发现（按自耗时排序）

### 函数级 Top 10（累计占全部自耗时 60.1%）

| # | 自耗时 | 占比 | 累计 | 函数 @ 模块 |
|---|--------|------|------|------|
| 1 | 562.0ms | 17.8% | 17.8% | perceive @ ai/perception.ts |
| 2 | 302.3ms | 9.6% | 27.4% | findPath @ utils/pathfind.ts |
| 3 | 273.5ms | 8.7% | 36.0% | scanAhead @ ai/perception.ts |
| 4 | 184.0ms | 5.8% | 41.9% | findBulletThreatToBaseImpl @ ai/god/ThreatAssessor.ts |
| 5 | 166.6ms | 5.3% | 47.1% | rectHitsTerrain @ game/World.ts |
| 6 | 128.3ms | 4.1% | 51.2% | canStep @ ai/perception.ts |
| 7 | 101.8ms | 3.2% | 54.4% | scanAheadImpl @ ai/god/FireControl.ts |
| 8 | 73.1ms | 2.3% | 56.8% | updateBullets @ game/Simulation.ts |
| 9 | 54.2ms | 1.7% | 58.5% | think @ ai/GodAIInput.ts |
| 10 | 51.9ms | 1.6% | 60.1% | analyze @ ai/perception.ts |

> 注：截图自 `analyze-profile.ts` 的 Top-40（`bun tools/perf/analyze-profile.ts tools/perf/results/sim-godai.cpuprofile 40`）；完整 40 行见原始 cpuprofile。V8 内建/匿名模块（含 `Array.sort`/`filter`/`map`、`stringSplitFast`、`loadAndEvaluateModule` 等）合计 332.7ms / 10.5%，属运行时原语，不在「业务可优化」范畴内单独归因。

### 模块级（自耗时）

| 模块 | 自耗时 | 占比 |
|------|--------|------|
| ai/perception.ts | 1020.7ms | 32.3% |
| utils/pathfind.ts | 364.7ms | 11.6% |
| game/Simulation.ts | 355.3ms | 11.3% |
| （V8 内建/匿名） | 332.7ms | 10.5% |
| game/World.ts | 266.6ms | 8.4% |
| ai/TacticalIntelligence.ts | 199.2ms | 6.3% |
| ai/god/ThreatAssessor.ts | 190.8ms | 6.0% |
| ai/god/FireControl.ts | 133.9ms | 4.2% |
| ai/GodAIInput.ts | 79.1ms | 2.5% |
| ai/god/Navigator.ts | 70.9ms | 2.2% |
| ai/god/StrategyPlanner.ts | 57.3ms | 1.8% |
| game/TileMap.ts | 47.5ms | 1.5% |

---

## 🧩 瓶颈聚类与根因（供后续优化参考，本次未执行）

**Cluster A — 敌方感知 `ai/perception.ts`（32.3%，第一杠杆）**
- `perceive` 每 tick 为每个敌人重建 `world.allTanks`（其它存活坦克数组）约 5 次：4 次在 `canStep` 计算 `openDirs` + 1 次用于队友/诱饵/拥堵分类；`canStep` 每次又遍历该数组做 AABB 碰撞。
- `scanAhead` 沿方向逐 CELL 步进扫描至 `FIELD` 像素；`perceive`（基础视线）与 `analyze`（目标视线）各自调用 → 每敌人每 tick 至少 2 次整段扫描。
- 优化方向（不改变结果）：`perceive` 一次性扫描其它存活坦克进复用数组并传给 `canStep`；`analyze` 在目标恰在朝向时复用 `baseLOS` 避免重复扫描。

**Cluster B — God AI 寻路 `utils/pathfind.ts`（11.6%）**
- `findPath` 用字符串键 `` `${col},${row}` `` + `Map`/`Set`，每个 A* 循环 `currentKey.split(',').map(Number)`（profile 中 `key` 1.0% + `stringSplitFast` 0.8% + `isPassable` 0.9% 印证）。
- 优化方向：整数键 `row*GRID+col` + 扁平 TypedArray（gScore/fScore/cameFrom）+ 开放集线性扫描（保留插入序 tie-break，路径输出逐字节一致）→ 消除热循环全部分配。

**Cluster C — God AI 决策管线 `god/*` + `GodAIInput`（~16.7%）**
- `findBulletThreatToBaseImpl` 单点 5.8%：每 tick 扫描子弹 + 坦克。
- `think()` 多次重扫 `w.allTanks`/`w.tanks`/`w.bullets`（`isBaseUnderThreat`、`findEnemyDirection`、`selectTarget`、`findPowerUpTarget`→`calculateRouteDanger`、`canMoveOrBreak`/`canMoveDir`）。
- 优化方向：每 tick 缓存一次敌方/子弹快照，供各子模块只读复用（不改变决策）。

**Cluster D — 仿真核心 + World（Simulation 11.3% + World 8.4% + TileMap 1.5% ≈ 21.2%）**
- `rectHitsTerrain` 5.3%（`canStep`/移动中的地形碰撞，热点）、`updateBullets` 2.3%、`allTanks` 重建 0.7%+0.5%、`TileMap.get` 1.3%。
- 多为必要仿真工作；`rectHitsTerrain` 与瓦片查询可评估空间索引，但改动风险高于 A/B/C，需更谨慎（不得改变碰撞/时序行为）。

**Cluster E — 敌方 AI 决策 `ai/TacticalIntelligence.ts`（6.3%）**
- `evaluateGoals` 0.7% 用 `scores.sort`（分配 + 排序），受 thinkTimer 节流，优先级低。

---

## 📏 测量口径与注意事项

- **自耗时（self-time）** 才是指向「可优化代码」的正确透镜（指向函数自身执行，而非其调用方）。本报告全部排名基于 self-time。
- 墙钟 `perTick` 在 `--cpu-prof` 下受采样插桩影响，不能当作干净吞吐；**绝对每 tick 成本需用无 `--cpu-prof` 的计时跑单独评估**。本报告的瓶颈排序不受此影响。
- 采样总数 2381，覆盖 30 局，统计稳定（与上一轮快照一致）。
- 确定性约束：任何后续优化必须保持 AI 决策与对局结果完全一致（God AI 调参依赖确定性校准）；上述 A/B/C 方向均为纯计算/分配优化，结果不变。

---

## ✅ 建议优化目标（按优先级，尚未执行）

| # | 目标 | 预期杠杆 | 负责角色 | 紧急度 | 状态 |
|---|------|---------|---------|--------|------|
| 1 | Cluster A：perceive/canStep/analyze 去重扫描，复用 others 数组 | ~32% 自耗时 | 实现 + Cody 复核 | P0 | 提议，未做 |
| 2 | Cluster B：findPath 整数键 + TypedArray（路径逐字节不变） | ~12% 自耗时 | 实现 | P0 | 提议，未做 |
| 3 | Cluster C：God AI think() 每 tick 缓存敌方/子弹快照 | ~17% 自耗时 | 实现 | P1 | 提议，未做 |
| 4 | Cluster D：rectHitsTerrain / 瓦片查询评估空间索引（高风险，需复核确定性） | ~5-8% 自耗时 | Archi 评估 | P2 | 提议，未做 |

---

## ⚠️ 待完善 / 已知局限

- 当前 profile 仅覆盖 chaos / stage 0。后续应补 stage 多样本与不同难度，确认瓶颈分布稳定。
- 墙钟吞吐基线尚未在无插桩下单独测定，优化前后对比需用同一计时脚本。
- 未覆盖 presentation/audio 层（调参负载为 headless，不渲染，符合预期）。

---

## 📚 数据来源 & 成员产出索引

- 剖析框架：`tools/perf/profile-godai.ts`（真实负载复现）、`tools/perf/analyze-profile.ts`（V8 cpuprofile → self-time 聚合，按函数/模块）。
- 原始采样：`tools/perf/results/sim-godai.cpuprofile`（V8 格式，3157ms 采样 / 2381 样本）。
- 复现命令：
  `bun --cpu-prof --cpu-prof-dir=tools/perf/results --cpu-prof-name=sim-godai.cpuprofile tools/perf/profile-godai.ts --games=30 --diff=chaos --stage=0`
- 聚合命令：
  `bun tools/perf/analyze-profile.ts tools/perf/results/sim-godai.cpuprofile 40`

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。
