# 性能优化进展

> 汇编自 DECISIONS.md §21–§26, §45–§46, §62–§63, §68。
> 此文档为只读汇总。新决策仍以 DECISIONS.md 为准。

---

## 0. 当前基线

**标准测量场景**（§128，2026-08-05 起）: **三难度各 1/3** — `classic / hard / chaos` × 全 35 关 × 各 10 games / warmup=2（每难度各 350 场，共 1050 场）

**复现命令**: `bun tools/perf/bench-all-stages.ts`（默认三难度；`--diff=classic|hard|chaos` 限单难度，恢复 pre-§128 行为）

> 该 profile 跨越 35 个不同地形/敌人分布的关卡 × 三个难度，覆盖 classic（replanInterval=50、None 层 AI 占比高）与 hard/chaos（replanInterval=1、GodAI 重负载、pool 战斗 HP 缓冲）的全部代码路径。§127 证明 classic-only 基线会隐藏 God-AI 主导路径上的收益（chaos −27~32% 在 classic ±2% 噪声下不可见），因此三难度各 1/3 是更诚实的基线。
>
> **关键指标**：
> - `wall(ms)` — 总 wall time（主指标，越小越好；含分难度小计 + 占比）
> - `perTick(ms)` — 平均每 tick 耗时（用于跨场景对比）
> - `ticks / win / go / timeout` — 确定性签名，优化前后必须一致（或胜率一致）；签名按难度分别记录

**历史基线汇总**:

| 阶段 | wall | perTick | 备注 |
|------|------|---------|------|
| Round 7 (f2a91fa) | 2731ms | 0.0022ms | dirty-flag 前基线 |
| Round 8 (60696d7) | 2568-2610ms | 0.0021ms | dirty-flag + switch-weight |
| Round 9 | 2364ms | 0.0019ms | nav-cache + mines-flag（warmup=2，签名放宽至胜率） |
| **当前（2026-08-05 同机 A/B）** | **HEAD 3211/3289ms → WIP 2988/3219/3133ms** | **0.0027-0.0028ms** | Round 10（§122-§126）落地后；字节级确定性 |
| **Round 11（§127 replan 缓存）** | **chaos 缓存开 −27.0% / −31.6%**；classic ±2% 噪声 | chaos 0.0054-0.0055 vs 0.0075-0.0079 | replanCache 默认 1；classic 签名 `ticks=1169769 win=317/350` 字节一致 |
| **Round 12（§128 三难度基线）** | 三难度合计 ~22.6s；classic 3.5s(16%) / hard 9.6s(42%) / chaos 9.5s(42%) | 合计 0.0049 | 标准场景 = classic/hard/chaos 各 1/3；分难度签名见 §2.12 |
| **Round 13（§129 pickup memo）** | **chaos 缓存开 −27.7% / −8.3%**；classic ±14% 噪声带内 | chaos 0.0052-0.0055 vs 0.0057-0.0075 | pickupReachCache 默认 1；三难度签名逐字节不变 |

**确定性签名**（§128 起按难度记录）: classic `ticks=1169769 win=317/350`、hard `ticks=1639097 win=177/350`、chaos `ticks=1777415 win=195/350`（各与单难度 baseline/§127 A/B 一致）。Round 9 时代签名 `ticks=1291545 win=297/350` 已随 §104–§121 godai 特性（M6 一星、M13 站位、§121 自毁守卫等）陈旧。

---

## 1. 渲染层优化（2026-07-22）

### 1.1 消除逐帧分配（GC 压力）
- 事件双缓冲交换（`consumeEvents`）：两个数组交替使用，零分配
- 复用对象：`Camera._offset`、`EffectsSystem._flashResult`
- 标记清除替代 Set 分配：`AnimationSystem.cleanup` 用帧戳代替 `new Set<number>()`

### 1.2 增量地形缓存
- 打碎一块砖只重绘脏格（`dirtyCells`），不再全图 676 格重画
- 全量重建仅保留给：关卡加载、主题切换、基地摧毁

### 1.3 渲染热路径
- 水面：预光栅化两个相位动画位图，静态 SVG 不再逐帧绘制
- `drawSvgCentered` 无旋转快速路径：跳过 `save()/restore()`
- 模块级常量提升：`TANK_KEY_MAP`、`ITEM_KEY_MAP`、`POWERUP_TYPES`
- 碎片粒子：`setTransform` 替代 `save()/restore()` 逐粒子调用
- `renderPopups` 早返回：无 popup 时跳过 CSS-font 解析

### 1.4 仿真逐 tick 分配消除
- `tryFire` 玩家子弹计数：`filter().length` → 分配自由的计数循环
- `spawnPowerUp` 掉落表：数组字面量 → 模块级常量
- `updateSpawning` 出生矩形：`{x,y,w,h}` 对象 → 内联 AABB
- `World.removeBullet`：`filter()` → 原地 swap-and-pop

### 1.5 按需渲染 + 可见性暂停
- `PresentationLayer.shouldRender`：场景签名不变则跳过整帧重绘
- `document.hidden` 时完全停止 rAF 循环
- 静态画面（菜单/暂停/结算）事件驱动输入，零轮询

---

## 2. 仿真层性能优化

### 2.1 Round 1+2：热路径分配消除（~25% perTick 提升）

**基线**: `perTick=0.018ms` @120 games（冷却状态），确定性签名不变。

| 文件 | 优化 | 反模式（AGENTS §14） |
|------|------|----------------------|
| `ai/perception.ts` | `dirs` 数组提升为模块常量 `PERCEIVE_DIRS`；消除 `others` 数组（直接传 `all` 给 `canStep`）；`threats.sort()` 加空数组守卫；`scanAhead` 返回 `ScanHit` 字符串而非 `{hit, dist}` 对象 | §14.1, §14.2, §14.3 |
| `ai/god/FireControl.ts` | `offsets` 数组提升为模块常量 `VERTICAL_OFFSETS`/`HORIZONTAL_OFFSETS`；`scanAheadImpl` 结果写入 `self._scanResult` 可复用对象 | §14.1, §14.2 |
| `ai/god/ThreatAssessor.ts` | `dodgeDirectionImpl` 消除 `candidates`/`open` 数组，改用 `candA`/`candB` + `safeA`/`safeB` 局部布尔变量 | §14.1 |
| `ai/TacticalIntelligence.ts` | `reactiveDodge` 同上（消除 `candidates`/`safe`/`away` 数组） | §14.1 |
| `ai/GodAIInput.ts` | 添加 `_scanResult` 可复用字段 | §14.2 |

### 2.2 Round 3：TileMap 数值编码（已回退）

**尝试**: 将 `TerrainType[][]` 改为扁平 `Uint8Array`，添加 `TERRAIN_CODES`/`TERRAIN_NAMES`/`getRaw()`。

**结果**: `perTick=0.023ms`（`getRaw`+数值比较）和 `perTick=0.027ms`（`get()`+`TERRAIN_NAMES` 反查）—— 均比 Round 1+2 的 `0.018ms` **更慢**。

**根因**: V8 对字符串字面量做了驻留（interning），`type === 'brick'` 是指针比较，极快。而 `TERRAIN_CODES.brick` 是对导出可变对象的属性查找，V8 无法常量折叠。`TERRAIN_NAMES[code]` 反查也增加了开销，抵消了扁平数组的缓存局部性收益。

**决策**: 回退到原始 `TerrainType[][]`。保留 `getRaw()` 方法供未来使用（§14.4 规则：使用内联数字字面量 `=== 1`，不用 `TERRAIN_CODES.xxx`）。

### 2.3 反模式扫描（Round 4）

| 文件 | 问题 | 修复 |
|------|------|------|
| `ai/TacticalIntelligence.ts` `reactiveDodge` | `candidates.filter()` + `safe.sort()` + `away.filter()` 每 tick 分配 3 个数组 | 改为 `candA`/`candB` 局部变量 + `aOpen`/`bOpen` 布尔 |
| `ai/god/StrategyPlanner.ts` `selectTargetImpl` | `w.tanks.filter()` 每 tick 分配过滤数组 | 改用 `self._enemies` 缓存（Cluster C 已在 `think()` 中构建） |

**验证**: 530 tests pass，确定性签名不变 (`ticks=137139 win=3 go=27`)。

### 2.4 Round 5：Classic-Mode 缓存与预过滤（~27% wall-time 提升）

**基线**: `perTick=0.009ms` @60 games classic/stage0 (`ticks=190184 win=59 go=1`)

| 优化 | 文件 | 瓶颈占比变化 | 说明 |
|------|------|-------------|------|
| **findPath 数组复用** | `utils/pathfind.ts` | 7.8% → 2.1% | A* 每次 call 分配 6 个 typed array（≈11KB）。改为模块级复用 + `.fill()` 重置，零分配 |
| **tankCellImpl 缓冲区复用** | `ai/god/Navigator.ts` | 3.1% → ~0% | `tankCell` 每次 call 分配 `{col,row}` 对象。改为写入 `self._tankCellBuf` 可复用对象 |
| **scanAheadImpl 对齐预过滤** | `ai/god/FireControl.ts` | 11.9% → 3.6% | 按垂直轴对齐预过滤（aabb 常数半条件），将循环从 O(N) 降至 O(alignedN≈0-2) |
| **findBulletThreatToBase 方向过滤** | `ai/god/ThreatAssessor.ts` | 6.4% → 2.6% | 跳过物理上不可能到达基地的子弹 |
| **canMoveDir 每 tick 缓存** | `ai/god/Navigator.ts` | 1.4% → ~0% | 4-bit bitmask 缓存 4 方向结果。**Bug 发现**：passable→blocked 转变时 stale result bit 导致误返回 true——修复为 `&= ~bit` 清除 |
| **onCooldown 早退** | `ai/GodAIInput.ts` | 微小 | bulletCap 模式 cap=1，找到 1 颗 player bullet 即 break |
| **removeDeadEntities 内联** | `game/World.ts` | 无变化 | 内联 6 个泛型 `compact<T>` 调用避免 callback 开销。V8 已内联——无实测变化，但更清晰 |
| **惰性 openDirs** | `ai/perception.ts` + `ai/TacticalIntelligence.ts` | — | perceive 跳过 4×canStep openDirs 扫描；reactiveDodge 按需计算（仅威胁子弹存在时） |

**最终测量**: `perTick=0.006-0.007ms` @60 games classic/stage0。Wall time 1737ms → ~1275ms（~27%）。确定性签名不变。

### 2.5 Round 6：Perception 接口扁平化（~44% wall-time 提升）

**日期**: 2026-07-31
**基线**: `wall=1121ms` @30 games chaos/stage0 (`ticks=112788 win=3 go=27`)

**核心洞察**: 旧 `perceive()` 每次调用分配 3 个数组（`threats[]`、`teammates[]`、隐式 `others[]`）+ N 个对象，但**所有消费者只读取聚合数据**：
- `threats[]` 实际只用 `threats[0]` → 用 `hasThreat + threatDir` 两个标量替代
- `teammates[]` 实际只算 centroid → 用 `teammateCount + teammateSumX/Y` 三个标量替代
- `nearestDecoy` 对象 → 用 `hasDecoy/decoyX/decoyY` 三个标量替代

| 优化 | 文件 | 瓶颈占比变化 | 说明 |
|------|------|-------------|------|
| **`threats[]` → 标量** | `ai/perception.ts` + `ai/types.ts` | perceive 22.4% → 9.3% | 运行 min-replace 取代 sort + 数组 |
| **`teammates[]` → 标量** | 同上 | (含上面) | `spreadOut` 指令直接除得 centroid |
| **`nearestDecoy` 对象 → 标量** | 同上 | (含上面) | 内联 `hasDecoy/decoyX/decoyY` 局部变量 |
| **`Situation.threat` → `hasThreat` + `threatDir`** | `ai/types.ts` + `ai/TacticalIntelligence.ts` | (含上面) | 5 处 `s.threat` 真值检查改为 `s.hasThreat` |
| **`perceive` 新增 `all` 参数** | `ai/perception.ts` + `ai/TacticalIntelligence.ts` | allTanks 3.4% → ~0% | 调用方传入已计算的 `allTanks`，消除内部 getter 调用 |
| **`chooseDirective` 跳过 openDirs** | `ai/TacticalIntelligence.ts` | (含上面) | directive 选择只读 player/base/congestion |
| **`for...of` → 索引循环** | `ai/perception.ts` perceive + scanAhead | (含上面) | V8 不消除 for...of 迭代器对象 |
| **`for...of` → 索引循环** | `ai/god/ThreatAssessor.ts` ×2 | 7.2% → ~1.8% | findMostDangerousBullet + findBulletThreatToBase |
| **`for...of` → 索引循环** | `ai/god/StrategyPlanner.ts` ×5 | 2.6% → 5.5% | selectTargetImpl |
| **Simulation.ts 迭代器→索引** | `game/Simulation.ts` | updateMines/updateBullets/bulletHitsBullet/updateGuards/tryFire | `updateMines` 用就地压缩（swap-and-pop）替代 `.filter()` |

**对比之前轮次**:

| 阶段 | wall (30 games chaos/stage0) | 备注 |
|------|------------------------------|------|
| Round 1+2 完成 | 826ms | 已消除 PERCEIVE_DIRS / scanAhead ScanHit 等 |
| 本轮前基线 | 1121ms | （环境差异 / 主线代码新增） |
| Simulation.ts 优化 | 1091ms | for...of → 索引；filter → 就地压缩 |
| perception.ts 接口扁平化 | 672ms | threats[]/teammates[]/nearestDecoy 全部消除 |
| ThreatAssessor + StrategyPlanner | **629ms** | for...of → 索引 |
| **本轮总提升** | **1121ms → 629ms (-44%)** | |

**验证**: 644 tests pass，确定性签名完全一致。

### 2.6 Round 7：rectHitsTerrain 内联 + 迭代器消除（~15% wall-time 提升）

**日期**: 2026-07-31
**场景**: classic/stage32（classic 难度下 None 层 AI 占比上升，GodAI 主循环、碰撞、寻路成为主要瓶颈）

**基线**: `wall=1240-1298ms` @30 games classic/stage32
**优化后**: `wall=1057-1113ms`（-15%），确定性签名字节一致（`ticks=137905 win=22 go=8`）

| 优化 | 文件 | 原 self-time | 说明 |
|------|------|-------------|------|
| **`rectHitsTerrain` 内联 `tileMap.get` + `blocksTank`** | `game/World.ts` | 11.2% (#2) → 6.7% | 缓存 `grid` 到局部 + 缓存 `row` 到外层循环；消除每 cell 两次方法调用。出界 cell 直接 `return true` |
| **`think` 内 bullet 扫描 `for...of` → 索引** | `ai/GodAIInput.ts` | think 12.2% (#1) → 6.1% | bulletCap 模式下每 tick 扫描 `w.bullets` 数 onCooldown |
| **`think` 内 nearbyScan `for...of` → 索引** | 同上 | (含 think) | P3.2 powerup gate 的 near-enemy 检测 |
| **`isBaseUnderThreat` `for...of` → 索引** | 同上 | 3.8% | 已 per-tick cache |
| **`hasFastThreatNearBase` `for...of` → 索引** | 同上 | (含上面) | guardBandMode 路径 |
| **`findEnemyDirectionImpl` `for...of` → 索引** | `ai/god/FireControl.ts` | 9.8% → ~4% | GodAI 每 tick 调用的目标对齐扫描 |
| **`TacticalIntelligence.update` 主循环 `for...of` → 索引** | `ai/TacticalIntelligence.ts` | 7.6% → 2.2% | 每 tick 遍历所有 tank 分派到 None/战术分支 |

**拒绝的方案**:
- `maybeTunnelOut` 加 brain 状态位跳过 canStepLat — 当前 5.8% 主要是已优化的 rectHitsTerrain 调用，加状态位增加复杂度收益小（§10 Simple beats clever）
- `findPath` A* 缓存 — player 每 tick 移动，path 失效快；缓存命中率低且引入复杂性
- `rectHitsTerrain` 数值编码 TileMap — AGENTS §14.4 已证明回归 28%

**两场景累计成果**:

| 场景 | 基线 | 优化后 | 提升 |
|------|------|--------|------|
| chaos/stage0 (Round 6) | 1121ms | 629ms | -44% |
| classic/stage32 (Round 7) | 1240ms | 1057ms | -15% |

### 2.7 Round 8：Dirty-Flag + Switch 威胁权重（~5-6% wall-time 提升）

**日期**: 2026-07-31
**场景**: classic / 全 35 关（固化为本项目标准 profile）

**基线**: `wall=2731ms` @10 games × 35 stages classic（Round 7 提交 `f2a91fa`），`ticks=1249555 win=298/350`
**优化后**: `wall=2568-2610ms`（提交 `60696d7`），确定性签名字节一致

| 优化 | 文件 | 原 self-time | 说明 |
|------|------|-------------|------|
| **`_needsCleanup` 脏标记跳过 `removeDeadEntities`** | `game/World.ts` + `game/Simulation.ts` | 5.5% → ~0% | 大多数 tick 无任何实体死亡，但旧实现仍每 tick 扫描 6 个数组做就地压缩。新增 `_needsCleanup: boolean` 字段：Simulation 在所有 `alive=false` 处和 explosion/popup 计时器归零处显式置位；入口检查，false 则直接 return |
| **`KIND_THREAT_WEIGHT` 字典 → `kindThreatWeight()` switch** | `ai/god/constants.ts` + `FireControl.ts` + `StrategyPlanner.ts` | 微小 | 旧实现 `KIND_THREAT_WEIGHT[t.kind] ?? 1` 对每个 tank 做字符串键哈希查找。改为 switch 函数后，V8 可基于 `TankKind` 联合类型做 jump table 优化。3 处调用点全部切换 |

**测试同步修复**: `tests/tactical-ai.test.ts` 中 "floor guarantee" 测试手动设置 `t.alive = false` 绕过 Simulation，需显式 `world._needsCleanup = true` 才能触发清理。

**拒绝的方案**:
- `findPath` 改用 bucket queue 替代 O(N) openList 扫描 — 4.4% 占比，但 A* 寻路正确性敏感，风险/收益比不划算
- `scanAhead` 调用点合并（base dir + objDir）— 9.3% 占比，但两个方向语义独立，合并会引入复杂分支判断

### 2.8 Round 9：Navigate 缓存 + Mines 脏标记（~7.9% wall-time 提升）

**核心洞察**: 敌人位置和障碍物摧毁是秒级变化，但 `navigateTowardsImpl` 每 tick 都调用 `findPath`——player 每 ~23 ticks 才进入新 cell，target cell 也秒级才变，绝大多数 A* 调用计算的输入与上一 tick 完全相同。

**方案评估**:

| 方案 | 风险 | 预期收益 | 实际结果 |
|------|------|----------|----------|
| A. navigateTowards cell 门控 | 低 | -3~4% | ✅ 采纳 |
| B. selectTarget 0.5s 缓存 | 中 | -2% | ❌ S7 Iron Curtain 胜率 72%→40%，回退 |
| C. updateMines 脏标记 | 低 | -1% | ✅ 采纳 |
| D. findPath bucket queue | 高 | -1~2% | 未做（收益低于 A，风险高） |

**实施改动**:

| 文件 | 改动 | 收益 |
|------|------|------|
| `ai/god/Navigator.ts` | `navigateTowardsImpl` 加 `(playerCell, target)` 缓存，相同输入直接返回上次结果；60-tick 安全 timer 强制 replan | findPath 调用次数 -90% |
| `ai/GodAIInput.ts` | 新增 `_navCache*` 字段和 `reset()` 清缓存 | 缓存状态承载 |
| `game/Simulation.ts` | `updateMines` 加 `_hasActiveMines` 早返回；`updatePlaying` 中 arm-timer 循环也加门控 | classic 无地雷时跳过 mines 扫描 |
| `game/World.ts` | 新增 `_hasActiveMines` 字段；placeMine 设置 true；updateMines 末尾 mines.length===0 时清 false | 脏标记承载 |
| `tests/godai-split-parity.test.ts` | 放宽断言：从字节一致改为仅 outcome 一致 | 适配 RNG 顺序变化 |

**关键决策：签名一致性放宽至胜率一致性**

navigateTowards 缓存命中时跳过 `rng.next()` 调用（原代码每 tick 调用一次用于 suboptimalPathProb 门控，即使 prob=0 也推进 RNG 状态）。这导致 RNG 调用顺序与基线不同，下游 enemy AI 决策偏移，ticks 不再字节一致。

用户决策（2026-07-31）：**不强求签名一致性，只要仿真胜率没有明显下降就可以优化**。

- 签名变化：`ticks=1249555 → 1239433`（偏移 ~8000 ticks，~0.6%）
- 胜率变化：`win=298/350 → 295/350`（-3，~0.9%，在统计噪声内）

**selectTarget 缓存方案回退**

Round 9 中段尝试给 `selectTargetImpl` 加 30-tick（0.5s）缓存。实测导致 S7 Iron Curtain 胜率从 72% 暴跌至 40%。

**根因**: S7 有大量 steel 墙，player 必须频繁切换目标寻找突破口。0.5s 缓存让 player 在被钢铁墙阻挡时仍盯着同一目标太久，无法及时切换到正在接近基地的敌人，导致基地失守。

**教训**: selectTarget 的响应性是关键——目标选择延迟会级联影响 navigateTowards 的 A* 计算，最终影响 player 的实际移动决策。与 navigateTowards 不同（其输入是 cell 坐标，秒级变化），selectTarget 的输入是 enemy 数组，enemy 的 HP/alive 状态变化会影响威胁评分，0.5s 延迟太大。

**测量结果**（35-stage classic, 10 games, warmup=2）:

| 指标 | Round 8 基线 | Round 9 完成 | 变化 |
|------|-------------|-------------|------|
| wall | 2568ms | **2364ms** | **-204ms (-7.9%)** |
| perTick | 0.0021ms | 0.0019ms | -9.5% |
| win | 298/350 | 295/350 | -3（统计噪声内） |

**累计提升**（自 Round 7 基线）:

| 阶段 | wall | 相对 Round 7 |
|------|------|--------------|
| Round 7 (f2a91fa) | 2731ms | 基线 |
| Round 8 (60696d7) | 2568ms | -6.0% |
| Round 9 | 2364ms | **-13.4%** |

### 2.9 Round 10：Within-tick memo 族 + Chokepoint 对齐枚举（~4% wall，字节级确定性）

**日期**: 2026-08-05
**同机 A/B**: HEAD（无 WIP）3211/3289ms → WIP（§122-§126）2988/3219/3133ms（均值 ~3113ms，~4%；热噪声 ±5-10%，方向为正）。**确定性签名字节一致**：`ticks=1169769 wins=317/350` 两侧完全相同。

| 优化 | 文件 | 说明 |
|------|------|------|
| **computeThreatPoints 对齐枚举** | `ai/god/Chokepoint.ts`（§122） | `canShootBaseFrom` 只对 col===12 / row∈{24,25} 返回 true——只枚举对齐格，676 次调用中 ~600 次早退归零；push 顺序逐字节一致 |
| **scanAheadImpl per-tick memo** | `ai/god/FireControl.ts` + `ai/GodAIInput.ts`（§123） | 4 个 per-direction 缓冲兼作 memo（`_scanCacheMask` 按原点+方向位）；同 tick 内重复扫描（shouldFireInDir/候选/ThreatAssessor 同原点同方向）直接命中。endFrame 每 tick 清。scanAheadImpl self-time ~9% → ~0.5-1.7% |
| **selectTarget within-tick memo** | `ai/god/StrategyPlanner.ts` + `ai/GodAIInput.ts`（§125） | HUNT 同 tick 2-3 次冗余查询去重；`_selTargetBuf` 稳定结果格消除 _tankCellBuf 别名风险 |

**字节等价的关键前提**（§123 注释内核对）：
- think() 每 tick（runner 每 tick 调 endFrame）或每帧至多一次（浏览器 `_thought` 守卫）执行；期间 World 不被修改（One Author §2.1）。
- memo 生命周期严格在单 tick 内——**零陈旧**，与 §68 否决的 cross-tick 缓存（0.5s 陈旧崩 S7）粒度不同。
- 均不耗 RNG、不改变调用次数。

**否决并留注释（诚实阴性）**: rectHitsTerrain 比较链重排/terrain 短路（+4.5% 更慢，§124）、canStepLat 手内联（中性偏慢，§126）——与 §14.4 同教训：不要对抗 V8 的比较链折叠与 JIT 类型反馈。

**新方向测量（未采纳）**: pickup 可达性 A*（findPowerUpTargetImpl/Urgent 每 tick 对范围内道具跑 1-2 次 findPath）关停探针（pickupPriorityMode=0，90 games chaos/stage0）perTick 0.00584→0.00610ms 反而上升——不捡星→游戏变长→敌人 AI 成本增加，非优化方向。

### 2.10 findPath 调用分布研究（2026-08-05，插桩计数，测后回退）

**动机**: chaos profile 中 findPath 为 #1 热点（17%）。为判断「跨 tick 缓存 / 节流」哪个方向值得 A/B，临时给 findPath 加调用点 tag 计数（`nav`/`replan`/`choke`/`pickup`，测后完全回退），跑双场景：

| 调用来源 | chaos/stage0 90 games | classic/35 350 games | 说明 |
|----------|----------------------|----------------------|------|
| **replan**（followPath→replanImpl 走廊） | **198,064（78.9%）** | **53,809（41.7%）** | 每 tick 全量 A*，`replanInterval: 1` |
| **replan-dig**（同上 dig 回退） | 24,897（9.9%） | **40,495（31.3%）** | classic 砖墙多，dig 占比高 |
| pickup（powerUpCellReachable） | 20,871（8.3%） | 25,594（19.8%） | 每 tick 每范围内道具 1-2 次 |
| choke（enemyThreatPath） | 3,211（1.3%） | 3,174（2.5%） | 30-tick 节流，每敌×最近4威胁点 |
| nav（navigateTowards） | 3,918（1.6%） | 6,051（4.7%） | §68 已跨 tick 缓存，仅 miss 计数 |
| **合计** | **250,961（0.52 次/tick）** | **129,179** | |

**根因**: `followPathImpl` 每 tick 调 `replanImpl`（`replanInterval` 默认 **1**），而 replanImpl 的 A* **无缓存**——§68 的跨 tick 缓存只加在了 `navigateTowardsImpl`（think 的次级分支：道具追击/T8 拦截/aggressive），主导航分支（think.ts 223/504/910）的 followPath→replan 路径被遗漏。**replan+replan-dig 占 findPath 的 73-89%。**

**关键优势（与 §68 的 navigateTowards 缓存不同）**: `replanImpl` 全链 **不耗 RNG**（selectTarget 已被 §125 within-tick memo 去重且无 RNG；findPath 无 RNG）——缓存后**确定性签名字节不变**，无需像 §68 那样放宽到胜率一致。缓存键 `(playerCell, target)`：player cell 每 ~23 tick 才变、target 随 selectTarget 变化，命中率 ~95%；60-tick 安全计时器兜底地形变化（§68 同款纪律）。路径引用别名安全：followPath 仅在 player 换格时 shift 路径，换格即换缓存键（miss → 重建），缓存内数组永不被消费路径污染；fence 钢墙堵死缓存的路径时，followPath 的 stuck 分支已 `path=[]; replanTimer=0`，需同步失效 replan 缓存作为安全阀。

**评估结论（A/B 建议）**:
- ✅ **replanImpl 跨 tick 缓存：值得 A/B（最高优先级）**——预计消灭 findPath 的 73-89% 调用 → sim wall 估算 -10% 上下；字节级确定性使 A/B 判据就是 35×120×2 胜率不降 + per-seed tick-diff 一致。§68 先例已证明该缓存形态安全（同键 + 安全计时器）。
- ⚠️ **pickup 可达性缓存（8-20%）**: 收益次之，但属 §68 风险类（跨 tick 陈旧，道具决策虽低危）——若 replan 缓存落地后仍有余力再评估。
- ❌ **chokepoint 节流（1-2%）**: 已 30-tick 节流，量级可忽略，不做。
- ❌ **bucket queue（Round 8 否决）**: findPath 单次成本，风险/收益比仍不划算。

### 2.11 Round 11：followPath→replanImpl 跨 tick 缓存（§127，SHIPPED）

**日期**: 2026-08-05
**依据**: §2.10 分布研究——replan+replan-dig 占 findPath 的 73-89%，`replanInterval: 1` 使主导航分支每 tick 全量 A*，而 §68 缓存只覆盖了 navigateTowardsImpl 次级分支。

**实施**: `replanImpl` 缓存键 `(playerCell, target)` + `tileMap.revision`（新增单调地形修订号，覆盖 loadStage/set/destroy/destroyAllBaseCells/快照恢复所有写路径）+ 60-tick 防御计时器 + followPath stuck 自愈阀 + reset 清理。Gate `params.replanCache`（默认 1，0 = pre-§127 字节等价）。**关键**：缓存与 `self.path` 必须分离——命中返回 `_replanCache.slice()`、写入存 `self.path.slice()` 独立副本。

**别名 bug（初版）**: 引用赋值 `self.path = _replanCache` 使 followPath 换格时的 `shift()` 原地消费缓存数组（S16 seed1007 tick 1536 把 len=2 缓存吃成 len=0），随后每 tick 命中空缓存死循环。初版 A/B 冒烟 score 700/700 tied 但 bench 揭穿：ticks=1172649 vs 1169769、13/350 单元分歧（含 outcome 翻转）。**eval-suite paired 比较是 score 粒度，抓不住 tick 级分歧——per-seed tick-diff 才是诚实判据**。

**结果（同机 A/B）**:
- 确定性：修复后 classic `ticks=1169769 win=317/350` 与关闭版逐字节一致；350 单元扫描 **0/350 分歧**；per-seed-diff S16 seed1007 IDENTICAL；eval-suite hard/chaos 各 4200/4200 tied。
- **chaos wall −27.0% / −31.6%**（两轮）；classic ±2% 噪声（CLASSIC_MODEL_PARAMS 把 classic replanInterval 恢复为 50 → 命中率低，收益在主战场 hard/chaos）。

**后续缓存类优化纪律**（§127 Implications）: ① tick 级 A/B 而非 score 级；② 缓存返回对象与消费路径（shift/变异）解引用分离；③ 失效信号覆盖所有写路径（含快照直写 grid）。

### 2.12 Round 12：标准基线改为 classic/hard/chaos 各 1/3（§128，工具链变更）

**日期**: 2026-08-05
**变更**: `bench-all-stages.ts` 默认跑 **classic / hard / chaos 三难度各全 35 关 × 10 games**（每难度 1/3 负载，共 1050 场），输出分难度小计 + GRAND TOTAL（wall/ticks/wins/perTick + 难度占比）。`--diff=` 限单难度，恢复 pre-§128 行为。

**动机**: §127 A/B 揭穿 classic-only 基线的问题——replan 缓存在 chaos（replanInterval=1）实测 −27~32%，classic（replanInterval=50 via CLASSIC_MODEL_PARAMS）只有 ±2% 噪声。classic-only 标准场景会系统性低估 God-AI 主导路径（hard/chaos）的优化收益与回归风险；三难度各 1/3 使基线覆盖全部战斗模型（instant vs pool）与 AI 负载形态。

**首次基线（2026-08-05 同机）**: 合计 wall=22577ms / ticks=4586281 / wins=689/1050 / perTick=0.0049ms；分难度 classic 3537ms(16%) / hard 9590ms(42%) / chaos 9450ms(42%)——hard+chaos 占 84%，印证 God-AI 重负载占比。**分难度签名**：classic `ticks=1169769 win=317/350`、hard `ticks=1639097 win=177/350`、chaos `ticks=1777415 win=195/350`（各与单难度 baseline 逐字节一致）。

> **基线过期提醒（2026-08-10）**：上表 ~22.6s / 689 胜率为 2026-08-05 旧机测量，其后 God-AI 强度已提升，且本机连续高负载后存在降频，**不可再直接横向对比**。当前行为契约以 `tests/god-ai-gate.test.ts` 的 **620/508/473** 为准（截至 2026-08-10 仍严格通过）。新鲜三难度基线见 §2.17。

### 2.13 Round 13：pickup 可达性 dig-only + 跨 tick memo（§129，SHIPPED）

**日期**: 2026-08-05
**依据**: §127 后 chaos/stage0 profile——findPath 17%→7.5%；插桩分布 pickup 占 findPath 调用 28.4%（chaos）/40.4%（classic），为 replan 缓存后最大可砍项（§2.10/§127 Implications 预留方向）。

**实施**: `powerUpCellReachable`（StrategyPlanner）两机制，Gate `params.pickupReachCache`（默认 1，0 = pre-§129 字节等价）：
1. **dig-only**：corridor A* 纯冗余——`breakBrick` 搜索空间严格包含 corridor 空间，布尔结果恒等；corridor-fail（全可达组件探索）恰是最贵路径，删除后单查询 A* 减半。
2. **跨 tick memo**：8 槽直映（target 坐标哈希），键 `(playerCell, target)` + `tileMap.revision`（§127 修订号复用）→ 严格纯 memo；player 每 ~8-23 tick 换格 + urgent/bonus-window 每 think 重查同批道具 → 命中率主导。

**插桩计数**（测后回退）: chaos/stage0 pickup 20603→2708（−87%），总 findPath 72533→54638（−24.7%）；classic/stage0 pickup 6206→613（−90%），总 15366→9773（−36.4%）。

**结果（同机 A/B）**: 确定性 350 单元扫描 **0/350 分歧**；per-seed-diff 4/4 IDENTICAL；eval-suite hard/chaos 各 4200/4200 tied；三难度基线签名逐字节不变（classic 1169769/317、hard 1639097/177、chaos 1777415/195）。**chaos wall −27.7% / −8.3%**（带热身轮双向；ab 序 B 臂 13401ms 疑似系统负载离群；perTick 两轮均 favor A）；classic ±14% 噪声带内。

**测量教训**: wall A/B 必须先热身（首轮 JIT/页面缓存 ~700-1000ms 会被误记到被测开关头上——无热身首测双臂差异完全被顺序支配）；并双向跑 order=ab/ba 交叉验证。

### 2.14 Round 14：enemy perception 分布插桩研究（诚实阴性，不发货）

**日期**: 2026-08-05
**动机**: §129 后 chaos/stage0 profile——`ai/perception.ts` 模块 **18.6%** 最大热点（perceive 9.1% + scanAhead 6.0% + analyze 1.8% + manhattan 1.1%），超过 findPath（6.6%）。检查是否还有同 tick 可去重重复调用（§123/§125 对 God AI 的玩法）。

**插桩**（测后完全回退，src 与 HEAD 逐字节一致）: perceive/scanAhead/computeOpenDirs 调用计数 + 同 tick (tankId, dir) 重复检测 + 步数/盟友/子弹迭代统计。90 games chaos+hard/stage0：

| 指标 | chaos | hard | 结论 |
|------|-------|------|------|
| perceive | **1.00/tank-tick** | 1.00 | 无同 tick 重复——"Observe once" 整合已彻底 |
| scanAhead 同 (tank,dir) 重复 | **0**（2.4M 次扫描） | 0 | analyze 的 objDir===tank.dir 短路完美，零浪费 |
| scanAhead | 5.79/tick（1 base + 0.69 second） | 5.83 | 2.8 步/调用早退；second 仅消费 pathBlocked |
| openDir onDemand | 1145 次（0.08% perceive） | 1043 | 惰性 openDirs 生效，on-demand 可忽略 |
| 威胁扫描 | 1.7 子弹/tank-tick，aligned 仅 1.7% | 同 | 非成本驱动 |
| alliesAtScan | 15.9% 扫描有盟友 | 14.2% | decoy 循环仅 guard 道具期活跃 |
| classic | **0 perception** | — | classic 全 None 层，无感知成本（热点纯 hard/chaos） |

**结论（诚实阴性，不发货）**: 无同 tick 可去重调用——perception 已是每坦克每 tick 一次的统一观察，18.6% 是固有成本（扁平数学 + Perception 字面量 + 24% tick 的 4×canStep + scanAhead 固有步数）。残余微优化（scanAhead 预验证边界后直读 grid、hoist allies）预计 <1% 总 wall，淹没在 ±5-10% 热噪声，按 §124/§126 纪律不做。跨 tick 缓存被否决（'player'/'decoy' 命中依赖玩家位置每 tick 变化，非纯 memo）。**再压此模块需算法级变更**（如跨坦克共享 hostile-bullet 预计算——但威胁循环仅 1.7 子弹/tank-tick，收益有限），违反 simple-beats-clever（MANIFEST §10）。

### 2.15 Round 15：findPath 内核三连（§130，SHIPPED）

**日期**: 2026-08-10
**依据**: §129 后 classic profile（`tmp/profile-sim.ts` 800 sims 单核）——`findPath` self-time **20.9%**，为单函数最大自耗时项。

**实施**（三个字节等价改动，同一内核）:
1. **二进制最小堆 open set**（先行 commit `be8fbb0`）：线性扫描 extract-min → 无分配二叉堆，键 `(fScore, firstSeq)` 与原线性扫描的 (最小 f, 最早入堆) 决胜顺序完全一致 → 返回路径逐字节相同，只有取最小的复杂度从 O(n) 变 O(log n)。三条并行 typed array（struct-of-arrays）避免 JS 数组 push/装箱；容量 `4·N+8` 可证充足（仅在 gScore 严格改善时重入堆，每条入边至多一次）。
2. **世代戳（generation stamp）取代四次 fill()**：原来每次调用都重置 `gScore/firstSeq/closed/inOpen` ≈ 9.4 KB memset，哪怕只扩展 3 个节点也照付。`_pfState` 用一条 Int32 通道折叠「本次已见」(`g2 = 2·gen`) 与「本次已闭」(`g2+1`)；旧调用写入的值至多 `g2−1`，故单次 `state[k] < g2` 比较就是精确的「gScore === Infinity」判定。首次松弛的顺序不变 → `firstSeq` 赋值顺序不变 → 堆决胜不变 → 路径不变。顺带删掉两个只写不读的缓冲区（`_pfFScore` 只在写它那一行被读回、`_pfInOpen` 从未被读）。
3. **前缘足迹扫描**：2×2 坦克从一个已知可通行的格子迈一步，候选足迹与当前足迹重叠恰好 2 个子块——那两块不可能挡路。corridor 模式只查前缘 2 块（4 次地形读 → 2 次）；breakBrick 模式则把原本「isPassable 4 读 + 砖块代价重扫 4 读」的 8 次读合并成**一次** 2×2 扫描同时产出可通行性与步代价。另加 key→col/row 查表（GRID=26 非 2 的幂，`key % GRID` 每次 pop 都编译成整数除法）。

**结果**: 微基准（4000 条真实关卡查询、12 轮交错）**内核快 12.1%**，等价性断言 IDENTICAL；profile `findPath` self-time **20.9% → 15.3%**；交替 A/B 端到端 95.3 → 96.7 sims/s（**+1.8%**，内核占比有限）。BFS 交叉验证 corridor 1687/0、breakBrick 4116/0 分歧；god-ai-gate **620/508/473** 逐关不变。

**编号更正**: 代码注释一度写作「perf §129」，与 Round 13 的 pickup 可达性缓存撞号，已统一改为 §130。

### 2.16 Round 16：carve 路径二级 memo（§131，SHIPPED）

**日期**: 2026-08-10
**依据**: §130 后调用树（`tmp/prof-tree.ts`）——`evaluate → findLaneDefensePointImpl` 1247ms/8161ms（**15.3%**），其中 1241ms 全部下沉到 `carvePathInfoCached`，再到 `findCarvePathImpl` 1209ms → `findPath`(挖墙) 1189ms。**四条几乎等长的柱 = 纯转发链，成本 100% 在底层挖墙 A\***。

**插桩测量**（200 sims classic，测后完全回退）:

| 指标 | 值 | 结论 |
|------|-----|------|
| `findLaneDefensePointImpl` 调用 | 1568（avg 扫 3.58 候选） | 每候选一次独立 (from,to) 查询 |
| **车道防守子集的单槽缓存命中率** | **0.0%**（0/5612） | 单槽缓存对该扫描完全失效 |
| 同子集多槽 memo 可命中 | **91.0%**（5105/5612） | 逐 tick 重复问同一批候选 |
| memo 活跃条目峰值 | **28** | 工作集极小，Map 开销可忽略 |
| `findCarvePathImpl` 返回 null | **81.0%**（5401/6669） | 其中 4044 是「挖到了但不 carve-safe」 |
| 无限制挖墙搜索成功 | **0**（0/5401） | 受限搜索失败时，无限制搜索从未救回过 |

**根因**: `_carvePathCache` 只记**一对** (from,to)。`findLaneDefensePointImpl` 沿基地列自下而上扫候选（rows br−1..0 × cols bc..bc+1），每个候选的 `to` 都不同 → 必然打穿单槽缓存 → 每候选重跑一次 corridor A\* 加一到两次全图挖墙 A\*，而且逐 tick 重复。挖墙模式下砖块可通行、且 `carveBaseColumnCost = 1e9` 让启发式失效，A\* 近乎要遍历整张图才肯付那 1e9——这正是最贵的一类搜索。

**实施**: 在单槽缓存**之后**加一层 `Map<packedKey, {path, corridor}>` 二级 memo（`GodAIInput._carveMemo`）。
- 键：`fromKey * 676 + toKey`（打包成 int32，Map 走整数快路径）。
- 有效性：`tileMap.revision` + `carveBaseColumnCost` + `carveMaxBaseColumn`（§178 会在 dual central breach 里覆写后两者），任一不符即整表清空；另有 `carveReplanTicks` 陈旧上界，与单槽缓存同口径。
- **单槽缓存行为零改动**：仍然先查它；memo 命中路径照样执行重算路径会做的全部字段写入与计时器重置，因此之后每一次命中/未命中的判定都逐位不变。唯一被跳过的只有纯函数重算本身。
- 共享 `Direction[]` 安全：所有调用点只读 `.length` / `[0]`，无一改写（Navigator 的 replan 缓存正是因此才 `.slice()`）。

**结果（交替 A/B，5 轮×2 臂，800 sims/轮）**: OLD 97.5/95.4/94.0/94.0/93.6（中位 **94.0**）vs NEW 107.3/111.5/107.9/105.0/107.2（中位 **107.3**）→ **端到端 +14.2%**，两组分布完全不重叠。god-ai-gate **620/508/473** 逐关不变；全量 1223 tests 全绿。

**遗留方向**: 「无限制挖墙搜索 0/5401 成功」说明受限搜索失败时它几乎必然白跑（受限搜索最小化受罚格用量，它失败则无限制版更不可能 carve-safe——两者不完全等价仅因 A\* 按格计价而 `pathCarveSafeImpl` 按 2×2 足迹判定）。删掉它可再省一批全图搜索，但那是**行为变更**（尾部场景可能翻盘），需单独立项走 A/B 与 gate。

### 2.17 优化线程收尾（2026-08-10，STOP — dig-search 项已被 §2.18 推翻）

**决策**：用户确认行为保真优化线程到此为止。Round 11/13/15/16 已落地全部清晰可优化项（replan 缓存 / pickup memo / findPath 内核三连 / carve 二级 memo）。剩余成本项均不属行为保真可优化范围：
- God-AI 决策 `evaluate` 链（chaos 42.7% 含）属 `think` 决策脑，用户要求不碰（避免重校真值表）；
- 敌方 `perceive`/`analyze`（chaos ~33% 含）§2.14 已判诚实阴性（无可去重重复调用，再压需算法级变更，违反 simple-beats-clever）；
- 无限制挖墙搜索（§2.16 遗留：5401 次全图 A* 0 成功）为**行为变更**——用户 8-11 重新立项探索（见 §2.18），该 STOP 对此项失效。

**当前三难度基线（2026-08-10 单测，热机状态，仅作状态记录，非干净热基准）**：

| 难度 | wall | 占比 |
|------|------|------|
| classic | 3728ms | 10% |
| hard | 20975ms | 55% |
| chaos | 13428ms | 35% |
| **GRAND** | **38132ms** | ticks=4847091 wins=791/1050 perTick=0.0079ms |

> 此数高于 §2.12 旧基线（22.6s）因：① God-AI 强度提升使对局更长（同 350 场/难度下胜率 689→791）；② 测量于连续 cpu-prof + gate 后的热机状态，含降频。行为契约 god-ai-gate 620/508/473 仍严格通过，无回退。后续若需干净基准，应在冷机下重测。

**结论**：行为保真优化已达当前架构下的收益上限（findPath 自耗从 17–20% 压到 5.7%），停止线程。但「无限制挖墙搜索」一项由用户 8-11 重新开启专项（§2.18），该 STOP 对此项失效。

### 2.18 Round 17：删除无限制挖墙搜索（§132，SHIPPED）

**背景**：§2.16 遗留——`findCarvePathImpl` 在 `restricted` 失败后无条件再跑一次「无限制挖墙 A*」（`findPath(tm, from, to, { breakBrick: true })`，无 threatCosts）。这是第二次全图 A*，chaos 下单 `findCarvePathImpl` 调用平均约 1.8 次 A*。用户 8-11 指令：「探索无限制挖墙搜索，保证胜率无回归前提下极限优化」。

**插桩取证（2100 场 gate sim，单进程计数 `globalThis.__CARVE_DIG`）**：

| 难度 | 胜率 | dig 回退尝试 | dig 回退成功(carve-safe) |
|------|------|------|------|
| classic | 620/700 | 2164 | 0 |
| hard | 508/700 | 7290 | 0 |
| chaos | 473/700 | 8229 | **3**（均 `from{20,20}→to{12,21}`，均 restricted-unsafe） |

合计 **3/17683** 成功——即 99.98% 的第二次全图 A* 纯白跑。classic-only 旧测「0/5401」漏掉了 chaos 的 3 例尾部场景。

**行为影响**：3 次成功全部是 restricted-unsafe 情形（restricted 路径因 2×2 足迹超 `carveMaxBaseColumn` 被拒，无限制版换条更省的砖路通过 `pathCarveSafeImpl`）。删除它 = 这 3 个查询从「有路径」变 `null`——属**行为变更**，非行为保真。

**接受依据（gate = 胜率契约）**：gate 逐关 floor = truth−4 胜/关；3 次成功至多影响 3 局、无单关连失 >1 胜 → 任何单关 floor 都不破；chaos 聚合 473→最坏 470 ≥ 447 floor。实证：删除后重跑 gate **620/508/473 逐字节不变**——3 个变更查询在 gate 分布内未改变任何一胜。故按 gate 契约接受为「无胜率回归」。

**交替 A/B（同模块，OLD=dig ON / NEW=dig OFF，各 6 轮×400 sims，相邻臂消除热机漂移）**：

- classic: OLD 111.4 → NEW 114.1 sims/s → **+2.4%**
- chaos: OLD 34.1 → NEW 36.0 sims/s → **+5.2%**（chaos dig 回退最多，收益最大）
- 综合 hard 居中，端到端约 **+3~4%**

**改动**：`findCarvePathImpl` 删除 dig 回退分支 + 头部注释步骤 3；`buildCarveCosts` 已按 `tileMap.revision` 缓存（§161），无 per-call 分配，非进一步优化点；`pathCarveSafeImpl` 为 O(path) 轻量步行，非热点。

**验证**：god-ai-gate **620/508/473 逐字节不变**（worker pool 19.1s）；全量 test/typecheck/lint 通过（提交前 hook）。

**结论**：行为保真线程见底后，dig 回退是首个被批准的**行为变更**优化（用户 8-11 明确授权），且 gate 守住。至此 carve/dig 链全量 A* 已压到单次 restricted 搜索。剩余成本 = `think` 决策脑（用户要求不碰）+ `perceive`（诚实阴性）。

### 2.19 全矩阵 Profile（35 关 × 60 seed × 3 难度，2026-08-11）

用户要求不再只跑 stage 1，改跑完整矩阵。3 份独立 cpu-prof（各覆盖 35×60 全矩阵，并行运行）：chaos 96s / hard 98s / classic 29s，合计 **6300 sims**。与 stage-1 单点结论**显著不同**——stage-1 只跑了第 1 关，严重低估了敌方 AI 感知的规模。

**跨难度聚合 Top 10（自耗时，三难度等权，各 2100 sims）**：

| # | 函数 @ 模块 | 聚合% | chaos | hard | classic |
|---|---|---|---|---|---|
| 1 | `evaluate` @ ai/god/think.ts | 7.4% | 7.0 | 6.8 | 10.4 |
| 2 | `findPath` @ utils/pathfind.ts | 6.9% | 7.0 | 7.5 | 4.2 |
| 3 | `perceive` @ ai/perception.ts | 6.6% | 7.1 | 7.9 | ~0.3 |
| 4 | `updateMovement` @ game/SimulationCombat.ts | 6.4% | 6.4 | 6.0 | 7.9 |
| 5 | `scanAhead` @ ai/perception.ts | 5.3% | 6.0 | 5.4 | ~0.3 |
| 6 | `rectHitsTerrain` @ game/World.ts | 3.8% | 3.5 | 3.4 | 6.4 |
| 7 | `analyze` @ ai/perception.ts | 3.5% | 3.9 | 3.9 | ~0.3 |
| 8 | `thinkImpl` @ ai/god/think.ts | 3.5% | 3.4 | 3.7 | 3.1 |
| 9 | `scanAheadImpl` @ ai/god/FireControl.ts | 3.5% | 3.1 | 3.0 | 6.5 |
| 10 | `runChain` @ ai/god/DecisionCore.ts | 2.9% | 2.6 | 2.7 | 4.6 |

`ai/perception.ts` 整模块在 **chaos 17.3% / hard 18.2%**（perceive+scanAhead+analyze 三连），是单模块最大头；classic 仅 ~3%（经典关敌人少/简单）。

**含时宏块（call-tree inclusive）**：

| 宏块 | chaos | hard | classic |
|---|---|---|---|
| God-AI `think` 决策树（含 `evaluate`） | 48.8% | 47.8% | 57.6% |
| 敌方 AI `updateEnemyAI` 链 | 27.3% | 29.1% | 16.6% |
| 物理/碰撞（updateMovement 等） | ~10% | ~9.5% | ~10% |

**关键反差**：chaos/hard 最大成本是**敌方 AI 感知**（perception 模块 17-18%）；classic 最大成本是 God-AI `evaluate`（10.4% 自耗，含时 57%）。

**极限优化候选（按潜力）**：
1. `ai/perception.ts` 敌方感知（chaos/hard 17-18%）——全矩阵最大鲸鱼。§2.14 曾判"诚实阴性"，但基于 stage-1 单点，规模被低估一个数量级。需用全矩阵重新插桩验证跨坦克复用度（同 tick 战场快照可能让 scanAhead 跨坦克复用）。砍 20% ≈ +3.5% 全局，比 findPath 收益大。
2. `scanAheadImpl`（开火威胁扫描，classic 6.5%）——tick 级去重候选，classic 尤热。
3. `isBaseUnderThreat`（classic 4.5%）——God-AI 基地威胁检查，调用频次高，疑似冗余。
4. `findPath`（6.9% 聚合）——已 Round 15 见底。
5. `evaluate`/`think`（God-AI 决策脑）——用户要求不碰。

### 2.20 瓶颈优化尝试 + 全矩阵插桩（2026-08-11）

用户要求「在保证胜率不下降的严格前提下，依次尝试优化上述 top-10 瓶颈」。按项目约定**先测量再动手**：在 `perceive` / `scanAheadImpl` / `isBaseUnderThreat` 三处插桩，跑完整矩阵（classic+chaos 各 35×60=2100 sims，单进程聚合计数器），得到子成本拆分。插桩已回退，gate 重跑 **620/508/473 逐字节不变**。

**插桩实测（全矩阵 35×60×2 难度）**：

| 函数 | diff | 调用 | 关键子成本 | 缓存命中 |
|---|---|---|---|---|
| `perceive` | classic | **0** | — | — |
| `perceive` | chaos | 32.35M | bulletIters 55.7M · listIters 161.3M(≈5 坦/调用) · canStepTankIters 14.1M(rarely needOpenDirs) | — |
| `scanAheadImpl` | classic | 7.78M | — | **5.3%** |
| `scanAheadImpl` | chaos | 11.74M | — | **6.2%** |
| `isBaseUnderThreat` | classic | 19.9M | — | **70.0%** |
| `isBaseUnderThreat` | chaos | 33.3M | — | **69.0%** |

**逐项结论（均行为保真无解，诚实阴性）**：

1. **`perceive`（chaos/hard 专属；classic 0 调用）**：每调用 161.3M/32.35M ≈ **5 坦均长**的列表遍历——整矩阵累计 161M 次 ≈ chaos wall 0.8%，循环本身极小。真实成本在 **32M 次 `Perception` 对象分配 + 函数调用开销 + `analyze`→`scanAhead` 网格步行**（位置相关，不可跨坦复用）。 teammates 聚合循环**已是与 congestion/nearestDecoy 合并的单循环**，把 teammateCount/SumX/Y 提升为 per-tick 全局预计算**并不能消除该循环**（congestion/decoy 仍需 per-调用遍历）→ 净行为保真收益 ≈ 0。要砍需改 `perceive` 返回契约（复用 buffer）或降调用频次（行为变更）。
2. **`scanAheadImpl`**：per-tick memo（§123）命中率仅 **5-6%**——因为 `think()` 每 tick 约只调它 ~1 次（classic 7.78M / (2100 × ~3500 tick) ≈ 1 次/tick），同 tick 内无同 (origin,dir) 重复可去重。成本是**固有网格步行扫描**，不可在 per-tick 层消除。§123 memo 近乎死重但无害，保留。
3. **`isBaseUnderThreat`**：已 70% per-tick 缓存，余下成本是 **19.9M 次函数调用开销**（classic）。降调用需重构 `think()` 各分支改为传值（行为风险），非便宜的胜。
4. **`updateMovement` / `rectHitsTerrain`**（物理/碰撞 6.4%/3.8%）：扁平数学 + 碰撞查询，再压需 SIMD/空间索引（算法级变更），违反 simple-beats-clever。
5. **`evaluate` / `thinkImpl` / `runChain`**（God-AI 决策脑 7.4%/3.5%/2.9%）：属用户要求不碰的真值表决策逻辑；纯微优化（去分配/早退）收益小、风险高。

**结论**：top-10 瓶颈在**行为保真层已到收益上限**——要么已是 per-tick memo/allocation-free 极限（scanAheadImpl/isBaseUnderThreat），要么成本是固有分配/调用/扁平数学（perceive/物理），要么属禁碰的决策脑。进一步收益**只能来自受控行为变更**（降调用频次，如 `perceive` 节流 / `isBaseUnderThreat` 调用合并），须经 god-ai-gate（620/508/473）守门。未经用户授权不单方面跨行为变更线。

---

## 3. 性能反模式（AGENTS.md §14）

| 规则 | 说明 |
|------|------|
| §14.1 | 禁止 per-tick 数组分配（常量数组提升、filter → 内联 if、result 数组 → 局部布尔） |
| §14.2 | 禁止 per-tick 返回对象（用可复用字段 `self._result` 或返回 primitive） |
| §14.3 | `.sort()` 前加空数组守卫 |
| §14.4 | 保持字符串地形类型（V8 驻留，`=== 'brick'` 是指针比较） |
| §14.5 | 避免 `.filter()` + `.sort()` 链 |
| §14.6 | 复用 `allTanks` 缓冲区 |

---

## 4. 浏览器端性能优化

详见 `docs/performance-report.md`（原报告，2026-07-25）。关键措施：
- **0-Loop 空闲**：菜单/暂停/结算画面事件驱动，主线程完全休眠
- **Performance Mode**：DPR cap=1 + 30 FPS 渲染 + pixelated 缩放，~87% GPU fill-rate 削减
- **F6 性能浮层**：`PerfOverlay` 实时显示 FPS/Sim/Render/UI/DrawCalls

---

## 5. 工具链

| 工具 | 用途 |
|------|------|
| `tools/perf/bench-all-stages.ts` | 35 关性能基线（默认三难度 classic/hard/chaos 各 1/3；`--diff=` 限单难度） |
| `tools/perf/profile-and-analyze.ts` | CPU profile 生成 + 分析 |
| `tools/perf/sim-bench.ts` | 无头仿真基准 |

---

## 6. 测量注意事项

- 本机 i7-4770HQ 在长时间运行后会出现热降频，perTick 测量值波动 ±5-10%
- 确定性签名（ticks/win/go）是可靠的正确性指标，不受热降频影响
- Classic 模式复现：`bun tools/perf/profile-and-analyze.ts --games=60 --diff=classic --stage=0 --warmup=5`
- CPU profile：`bun --cpu-prof --cpu-prof-dir=tools/perf/results --cpu-prof-name=sim-classic.cpuprofile tools/perf/profile-and-analyze.ts --games=30 --diff=classic --stage=0 --warmup=5`
- 分析：`bun tools/perf/profile-and-analyze.ts tools/perf/results/sim-classic.cpuprofile 20`

---

## 7. 相关决策索引

| 决策 | 出处 |
|------|------|
| 渲染层分配消除 | DECISIONS §21–§26 |
| 仿真热路径分配消除 | DECISIONS §45 |
| Classic-Mode 缓存预过滤 | DECISIONS §46 |
| Perception 接口扁平化 | DECISIONS §62 |
| scanAheadImpl 内联展开 | DECISIONS §63 |
| 性能基线脚本固化 | DECISIONS §68 |

---

## 8. 最终状态

- `bun run check` 全绿（test + typecheck + lint + format）；截至 Round 16 全量 **1223 tests / 0 fail**（2026-08-10）
- **行为契约（权威）**：`tests/god-ai-gate.test.ts` → classic **620/700**、hard **508/700**、chaos **473/700**，逐关不变（2026-08-10 重测通过）。§2.12 的 §128 三难度确定性签名（classic 1169769/317 等）为 2026-08-05 旧测，因 God-AI 强度提升已不可直接对比，见 §2.17。
- Round 11（§127）replan 缓存：**chaos wall −27~32%**（replanInterval=1 主战场），classic ±2% 噪声；replanCache 默认 ON
- Round 12（§128）：标准基线改为 **classic/hard/chaos 各 1/3**（每难度全 35 关 × 10 games）；该基线数字已过期（§2.17）
- Round 13（§129）pickup 可达性 memo：**chaos wall −27.7% / −8.3%**（dig-only + 跨 tick 纯 memo，pickupReachCache 默认 ON）
- Round 15（§130）findPath 内核三连：内核快 12.1%，profile 中 findPath 自耗 20.9%→15.3%，交替 A/B 端到端 +1.8%；god-ai-gate 620/508/473 不变
- Round 16（§131）carve 二级 memo：交替 A/B 端到端 **+14.2%**（94.0→107.3 sims/s，分布不重叠），全量 1223 tests 全绿；god-ai-gate 620/508/473 不变
- Round 17（§132）删除无限制挖墙搜索：交替 A/B 端到端 **classic +2.4% / chaos +5.2%**（≈+3~4% 综合）；god-ai-gate **620/508/473 逐字节不变**（3 个 chaos 行为变更查询在 gate 分布内未改任何一胜，按 gate 契约接受为无胜率回归）。carve/dig 链全量 A* 已压到单次 restricted 搜索。
- **当前剩余瓶颈（2026-08-10 chaos/stage1 profile，Round 15/16/17 后）**：God-AI 决策 `evaluate` 链 **42.7% 含**（属 `think` 决策脑，用户要求不碰）、敌方 `perceive`/`analyze` **~33% 含**（§2.14 诚实阴性）、`updateMovement` 8.7%（game/SimulationCombat，物理扁平数学）、`findPath` 已降至 **5.7%**、carve/dig 链再无冗余全图 A*。行为保真 + 已授权行为变更优化均已见底；进一步需触及 `think` 决策逻辑或算法级变更（空间索引等），违反 "simple beats clever"（MANIFEST §10）或用户设定的保行为边界。
- **线程状态（2026-08-11）**：行为保真优化已于 8-10 STOP，但「无限制挖墙搜索」项经用户 8-11 重新立项（§2.18 / Round 17），已 SHIPPED 并通过 gate。其余 STOP 结论不变。

## Performance Optimization

Full history in `docs/perf-optimization.progress.md`.

| Phase | Improvement | Detail |
|-------|-------------|--------|
| Render layer (alloc-free, incremental terrain, on-demand render, 0-loop idle) | `docs/perf-optimization.progress.md` §1 |
| Sim hot-path alloc elimination | ~25% perTick | `docs/perf-optimization.progress.md` §2.1 |
| Classic-mode caching & pre-filtering | ~27% wall | `docs/perf-optimization.progress.md` §2.2 |
| Perception interface flattening | ~44% wall | `docs/perf-optimization.progress.md` §2.3 |
| rectHitsTerrain inline + iterator elimination | ~15% wall | `docs/perf-optimization.progress.md` §2.4 |
| Dirty-flag + switch-weight | ~5-6% wall | `docs/perf-optimization.progress.md` §2.5 |
| Nav-cache + mines-flag | ~7.9% wall | `docs/perf-optimization.progress.md` §2.6 |
| Within-tick memo 族（scanAhead/selectTarget）+ chokepoint 对齐枚举 | ~4% wall，字节级确定性 | `docs/perf-optimization.progress.md` §2.9（DECISIONS §122–§126） |

**Current baseline (2026-08-05, 同机 A/B)**: HEAD `wall≈3250ms perTick≈0.0028ms` → WIP `wall≈3113ms perTick≈0.0027ms`（classic/35 stages/10 games/warmup=2；字节一致 `ticks=1169769 wins=317/350`）。
> 注：旧基线 `2531ms/0.0020ms` 早于 §104–§121 godai 特性（M6/M13/§121 等）落地，胜率 297→317 已证明其陈旧；同机 A/B 以 HEAD 为基准。
Standard command: `bun tools/perf/bench-all-stages.ts`.

## 122. 仿真性能 Round 10：computeThreatPoints 对齐枚举（SHIPPED，2026-08-05）

**Decision:** `computeThreatPointsImpl`（Chokepoint.ts）不再全扫 26×26 网格。`canShootBaseFrom` 只对 `col === BASE_POS.col`（12）或 `row ∈ {24, 25}` 的格子返回 true（SmartThreatModel 的列/行对齐早退），故只枚举对齐格：对齐行（24/25）保持全列扫描，其余行仅测 `col === bc`。push 顺序与原始行主序逐字节一致。

**Rationale:**
- 原实现 676 次 `canShootBaseFrom` 调用中 ~600 次浪费在对齐早退；chokepoint 计划每 `chokepointReplanTicks`（30）tick 重算一次，这是该节流路径中最贵的组件之一。
- 已逐行核对 `canShootBaseFrom` 语义：`col === bc+1`（13）在非对齐行必然 false（列检查只认 12、行检查只认 24/25），只枚举 `col === bc` 不会漏任何合格格——字节等价性有据。

**Implications:** 确定性签名字节不变（Round 10 汇总见 §123 Results）。

## 123. 仿真性能 Round 10：scanAheadImpl per-tick memo（SHIPPED，2026-08-05）

**Decision:** `scanAheadImpl` 的四个 per-direction 结果缓冲 `_scanResults[0..3]` 兼作 **per-tick memo**：`_scanCacheMask` 按扫描原点 `(pcx, pcy)` 记录已计算的方向位，同原点同方向命中直接返回同一对象；原点变化清掩码；`endFrame()` 每 tick 清掩码。`ScanResult` 接口 + `makeScanResult()` 工厂从 GodAIInput 字段提升到 FireControl 模块。

**Rationale:**
- think() 每 tick（headless runner 每 tick 调 `input.endFrame()`）或每帧至多一次（浏览器 `_thought` 守卫，同帧多次 tick 复用同一次 think 的决策）执行，期间 World 不被修改（One Author §2.1）——同原点同方向的重复扫描（shouldFireInDir 内联、aggro/engage/hunt 候选、ThreatAssessor.findMostDangerousBullet 均从 player 中心扫同一方向）结果逐字节相同。
- **零陈旧**：memo 生命周期严格在单 tick 内，不跨 tick——这正是 §68 否决的 cross-tick 缓存（0.5s 陈旧致 S7 胜率 72%→40%）缺失的粒度保证。
- 不耗 RNG、不改变调用次数（只跳过重复计算），确定性签名逐字节不变：**实测 `ticks=1169769 wins=317/350` 与 HEAD 基线完全一致**（同机 A/B，bench-all-stages classic/35/10 games/warmup=2）。

**Results:** scanAheadImpl self-time 从 Round 7-8 的 ~9%（chaos/stage0 profile）降至 ~0.5%（classic profile）/ 1.7%（chaos profile，含未命中原点成本）。Round 10 整体 wall：HEAD 3211/3289ms → WIP 2988/3219/3133ms（均值 ~3113ms，~4%；样本含热噪声 ±5-10%，方向为正）。

## 124. 仿真性能 Round 10（REJECTED）：rectHitsTerrain 比较链重排 / terrain 短路

**Decision:** 两个实测更慢的「优化」被否决，代码留注释防复发：
1. `World.rectHitsTerrain` 把比较链重排为先测 `'empty'` 短路——实测 **+4.5% 更慢**（2499→2610ms，3 runs，classic/35）。
2. `FireControl.scanAheadImpl` 用 `terrain !== 'empty'` 守卫包裹比较链——同样更慢。

**Rationale:** 与 §14.4 同一教训：地形字符串被 V8 驻留，`=== 'brick'` 是指针比较，V8 把 flat 比较链折叠成紧凑分支；插入短路分支比省下的比较更贵。不要对抗 V8 的比较链折叠。

## 125. 仿真性能 Round 10：selectTarget within-tick memo（SHIPPED，2026-08-05）

**Decision:** `selectTargetImpl` 加 **within-tick memo**：`_selTargetValid` + 键 `(col, row)` + `_selTargetBuf` 稳定结果格 + `_selTargetNull`。HUNT 分支同 tick 内 2-3 次以相同 playerCell 调用 selectTarget（`navTarget = selectTarget(pc)` 后经 followPath→replan 或 directMove 再调），中间 World 不变，重复查询冗余。

**Rationale:**
- §68 否决的是 **cross-tick** 缓存（0.5s 陈旧崩 S7）；within-tick memo **零陈旧**（endFrame 每 tick 清），是 §68 响应性要求粒度下的安全形态。`selectTargetUncached` 只读 World 状态与 params、不耗 RNG。
- 原 uncached 路径可能返回共享 `_tankCellBuf`（下一次 tankCell() 调用即覆写）或新分配的 defense cell——统一稳定缓冲同时消除别名风险与每 tick 对象分配。
- Telemetry note: `branchCounts.chokepoint` 从「每次冗余查询计数」改为「每 tick 一次」——纯观测计数（tools/diag），不影响游戏。

## 126. 仿真性能 Round 10（REJECTED）：canStepLat 手内联 rectHitsTerrain

**Decision:** `TacticalIntelligence.canStepLat` 手写内联 `World.isInBounds` + `rectHitsTerrain`（rect 恒为 CELL 对齐 64×32，`Math.floor` 与 OOB 分支证明冗余）——**3 次测量中性偏慢**，否决并留注释。

**Rationale:** `rectHitsTerrain` 被 4 个热站点调用且保持全 JIT 热；私有副本不更快，只分裂类型反馈（与 §123 同一教训——保持普通调用）。

**Implications:** Round 10 收官：剩余热点（findPath 17%、enemy perception 15%、updateMovement 5%）均为已优化到位的核心函数或已被否决的方案（bucket queue §68/§88、cross-tick 缓存 §68、比较链重排 §124）；pickup 可达性 A* 关停实验（tmp 探针，pickupPriorityMode=0）显示 perTick 反而上升——不构成优化方向。进一步优化需算法级变更，违反 simple-beats-clever（MANIFEST §10）。

## 127. 仿真性能 Round 11：followPath→replanImpl 跨 tick 缓存（SHIPPED，2026-08-05，含引用别名修复）

**Decision:** `replanImpl`（Navigator.ts）加 **cross-tick 缓存**，消灭 findPath 调用分布中的主导项：
- 键 `(playerCell, target)` + `world.tileMap.revision`（地形修订号，TileMap 新增单调计数器，loadStage/set/destroy/destroyAllBaseCells/快照恢复均 bump）+ `_replanTimer` 60-tick 安全计时器；followPath 的 stuck 分支清 `_replanCacheValid` 作自愈阀；reset() 清理。Gate：`params.replanCache`（默认 1；0 = 字节级等于 pre-§127）。
- **别名修复（关键）**：缓存命中/写入必须与 `self.path` 分离——命中返回 `_replanCache.slice()` 副本，写入存 `self.path.slice()` 独立副本。初版直接引用赋值 `self.path = _replanCache`，followPath 换格时的 `self.path.shift()` 原地消费了缓存数组本身（1536 的 shift 把 len=2 缓存吃成 len=0），之后每 tick 命中空缓存死循环直到键变。

**Rationale:**
- §2.10 调用分布测量（临时插桩，已回退）：replan 占 findPath 78.9%（chaos/stage0）+ 9.9% dig = **88.8%**；classic/35 为 41.7%+31.3% = 73%。根因：`replanInterval` 默认 1，followPath→replanImpl 每 tick 全量 A*，而 §68 缓存只加在了 navigateTowardsImpl（次级分支）——主导航分支被遗漏。
- `replanImpl` 全链不耗 RNG（findPath 确定性 A*，selectTarget 只读 World 状态），key 完整覆盖输入 ⇒ 严格纯 memo ⇒ **字节级确定**。地形修订号使缓存在地形变化的同一 tick 失效，无 staleness 窗口；60-tick 计时器降为防御性兜底。
- 首个实现（无 revision、60-tick 兜底）A/B 冒烟 score 700/700 tied，但 **bench 揭穿**：ticks=1172649 vs 基线 1169769、wins 316 vs 317，13/350 单元分歧（含 S33 seed1021 outcome 翻转）。eval-suite 的 paired 比较是 score 粒度，抓不住 tick 级分歧——**per-seed tick-diff 才是诚实判据**。

**Results（同机 A/B，经典/35 10 games/warmup=2 与 chaos/35）：**
- 确定性：修复后 classic bench `ticks=1169769 wins=317/350` 与关闭版逐字节一致；全量 350 单元扫描 **0/350 分歧**；per-seed-diff S16 seed1007 IDENTICAL；eval-suite hard/chaos 各 4200/4200 tied。
- 收益：**chaos wall −27.0% / −31.6%**（缓存开 vs 关，两轮）；classic ±2% 噪声（CLASSIC_MODEL_PARAMS 把 classic 的 replanInterval 恢复为 50，replan 每 50 tick 一次，命中率低——收益在主战场 hard/chaos）。

**Implications:** 后续缓存类优化（pickup 可达性等）必须：① 先做 tick 级 A/B 而非 score 级；② 缓存返回对象与消费路径解引用（shift/变异）分离；③ 失效信号（revision）覆盖所有写路径（含快照直写 grid）。

## 128. 性能基线标准场景改为 classic/hard/chaos 各 1/3（SHIPPED，2026-08-05）

**Decision:** `bench-all-stages.ts`（标准性能基线）默认从「classic / 全 35 关 / 10 games」改为 **三难度各 1/3**：`classic / hard / chaos` × 全 35 关 × 各 10 games / warmup=2（每难度 350 场，共 1050 场）。保留 `--diff=classic|hard|chaos` 限单难度（恢复 pre-§128 行为）。输出分难度小计（wall/ticks/wins/perTick）+ GRAND TOTAL + 难度占比。

**Rationale:**
- §127 A/B 揭穿 classic-only 基线的盲区：replan 缓存在 chaos（replanInterval=1）实测 **−27~32%**，而 classic（CLASSIC_MODEL_PARAMS 把 replanInterval 恢复为 50）只有 ±2% 噪声——classic-only 标准场景会系统性低估/隐藏 God-AI 主导路径（hard/chaos、pool 战斗 HP 缓冲）上的优化收益与回归风险。
- 三难度各 1/3 使基线覆盖全部战斗模型（instant vs pool）与 AI 负载形态（classic None 层占比高 vs hard/chaos GodAI 重负载），是更诚实的基线。用户确认按「每难度全 35 关 × 10 games」分配（总时长约 3 倍 ~20-30s，每难度样本量最大、最稳）。
- 签名按难度分别记录（classic/hard/chaos 各 `ticks/win`），优化前后逐难度比较——单难度签名不受其他难度噪声影响。

**Results（首次基线，2026-08-05 同机）:** GRAND TOTAL wall=22577ms / ticks=4586281 / wins=689/1050 / perTick=0.0049ms；分难度 classic 3537ms(16%) / hard 9590ms(42%) / chaos 9450ms(42%)。分难度签名：classic `ticks=1169769 win=317/350`、hard `ticks=1639097 win=177/350`、chaos `ticks=1777415 win=195/350`——各与单难度 baseline/§127 A/B 逐字节一致（hard+chaos 占 84% wall，印证 God-AI 重负载占比）。

**Implications:** 后续性能优化的 A/B 判据建议按难度分别对比签名与 wall；classic 与 hard/chaos 的差异（replanInterval 50 vs 1）是分析优化落点的重要维度。

## 129. pickup 可达性 A*：dig-only + 跨 tick 纯 memo（SHIPPED，2026-08-05）

**Decision:** `powerUpCellReachable`（StrategyPlanner，powerUpCollectCell 的可达性查询）两处字节级优化，Gate `params.pickupReachCache`（默认 1，0 = pre-§129 corridor+dig 无缓存字节等价 A/B 臂）：
1. **dig-only**：删除 corridor A* 调用——`breakBrick` 的搜索空间严格包含 corridor 空间（brick 通行成本 5，steel/water/base 仍阻挡），corridor 有路径 ⟺ dig 有路径，布尔结果恒等。corridor-fail 场景（全可达组件探索）恰是最贵路径，dig-only 直接短路它。
2. **跨 tick memo**：8 槽直映缓存（target 坐标哈希），键 `(playerCell, target)` + `tileMap.revision`（§127 新增的地形修订号复用）→ 严格纯 memo。player 每 ~8-23 tick 才换格，urgent/bonus-window 拾取路径每 think 重查同一批道具，重复查询主导。

**Rationale:**
- §2.10/§127 Implications 预留方向：pickup 是 replan 缓存后最大可砍项（chaos 28.4% / classic 40.4% 的 findPath 调用）。
- 与 §127 同构：findPath 是纯函数无 RNG → 同款键缓存字节级一致；revision 已覆盖全部地形写路径（§127 验证）。
- 插桩计数（测后回退）：chaos/stage0 pickup 20603→2708（−87%），总 findPath 72533→54638（−24.7%）；classic/stage0 pickup 6206→613（−90%），总 15366→9773（−36.4%）。
- 确定性：350 单元扫描 0/350 分歧（outcome/ticks/killCount）；per-seed-diff 4/4 敏感种子 IDENTICAL（S16 seed1007 / S33 seed1021 / S7 seed41 / S27 seed12）；eval-suite hard/chaos 各 4200/4200 tied；三难度基线签名逐字节不变（classic 1169769/317、hard 1639097/177、chaos 1777415/195）。
- Wall（同机 A/B，带热身轮 + order=ab/ba 双向）：**chaos −27.7% / −8.3%**（ab 序 B 臂 13401ms 疑似系统负载离群；perTick 两轮均 favor A）；classic ±14% 噪声带内（单场 ~3ms，收益被机器负载淹没）。chaos 收益超过 §127 profile 预估（dig A* 单次成本高于 corridor，corridor-fail 是全网格探索）。

**Implications:**
- 测量纪律（§127 教训延续）：wall A/B 必须先热身并双向跑（order=ab/ba）——首轮 JIT/页面缓存 ~700-1000ms 会被误记到被测开关头上（无热身首测时双臂差异完全被顺序支配）。
- pickup 可达性已达纯 memo 上限；再压需算法级变更（空间索引等），违反 simple-beats-clever（MANIFEST §10）。

---

### 2.21 bun test 全套 <20s 攻坚（2026-08-17，§230-§233）

**目标**: `bun test --parallel --timeout=50000` < 20s。基线 54.5s → **37.5s**（−31%）。

| 轮次 | 改动 | 结果 | 行为 |
|------|------|------|------|
| §230 | gate runner 瘦身: `collectMetrics/collectEvents: false`（scorer 不读它们）+ telemetry census `new Set()` → 双缓冲 ping-pong | 全套 54.5 → 41.4s | 字节等价（score 逐位不变） |
| §231 | thinkInterval=2 决策链节流 A/B | **否决**: hard 35×60 win 75.6→72.8%（−2.8pp）、score −0.022、691/2100 翻转 | 保留旋钮，默认 1 |
| §232 | 决策链 3 处小数组 → 局部变量 + scanAhead 整数 cell 步进 | 全套 41.4 → 37.5s | stash 前后 45-sim 签名 IDENTICAL |

**机器吞吐墙（决定性测量）**: Ryzen 5800H 8C16T 对该负载有效并行度仅 **~2.5-2.7×**——
1 worker 40.9ms/sim（= 主线程同速）；4 workers 65ms/sim；2/4/6/8 workers 全门禁
34.7/34.3/35.2/-s 无改善；进程级拆分（3 proc × 2 workers）仅 34.7→31.8s。per-sim
~41ms（hard/chaos ~55-60ms）由决策脑 ~30% + 敌方感知 ~16% + 仿真核心 ~15% +
findPath 残差 ~9% 构成；行为保真优化已扫净（§232 后 src/ai + game 无剩余 per-tick
小分配），行为级杠杆（节流）A/B 否决。

**结论（§233，已完成）**: 用户拍板 20→10 种子（保住统计功效）。truth 重标定（seeds
1-10），margin 加宽（0.05→0.07 / 0.03→0.04）。全套 **~25.9s**（基线 54.5s，**−52%**），
1385 tests 全绿；<20s 未达成（10 种子 gate 19.7s 已是机器地板 ~16s 的 88%）。
8 种子预估 ~21-22s 仍贴边；6 种子 ~17-18s 达标但牺牲每关灵敏度——用户选择 10。
