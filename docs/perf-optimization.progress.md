# 性能优化进展

> 汇编自 DECISIONS.md §21–§26, §45–§46, §62–§63, §68。
> 此文档为只读汇总。新决策仍以 DECISIONS.md 为准。

---

## 0. 当前基线

**标准测量场景**: `classic / 全 35 关 / 各 10 games / warmup=2`

**复现命令**: `bun tools/perf/bench-all-stages.ts`

> 该 profile 跨越 35 个不同地形/敌人分布的关卡，覆盖 classic 难度下 None 层 AI、GodAI 主循环、碰撞、寻路、火控的全部代码路径，比单关场景更能反映真实调参负载。
>
> **关键指标**：
> - `wall(ms)` — 总 wall time（主指标，越小越好）
> - `perTick(ms)` — 平均每 tick 耗时（用于跨场景对比）
> - `ticks / win / go / timeout` — 确定性签名，优化前后必须一致（或胜率一致）

**历史基线汇总**:

| 阶段 | wall | perTick | 备注 |
|------|------|---------|------|
| Round 7 (f2a91fa) | 2731ms | 0.0022ms | dirty-flag 前基线 |
| Round 8 (60696d7) | 2568-2610ms | 0.0021ms | dirty-flag + switch-weight |
| Round 9 | 2364ms | 0.0019ms | nav-cache + mines-flag（warmup=2，签名放宽至胜率） |
| **当前** | **2531ms** | **0.0020ms** | warmup=2 固化，与 Round 9 同量级（系统负载差异） |

**确定性签名**: `ticks=1291545 win=297/350`（Round 9+ 胜率一致，非字节一致；RNG 调用顺序变化导致 ticks 偏移，胜率稳定）

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
| B. selectTarget 0.5s 缓存 | 中 | -2% | ❌ S6 Iron Curtain 胜率 72%→40%，回退 |
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

Round 9 中段尝试给 `selectTargetImpl` 加 30-tick（0.5s）缓存。实测导致 S6 Iron Curtain 胜率从 72% 暴跌至 40%。

**根因**: S6 有大量 steel 墙，player 必须频繁切换目标寻找突破口。0.5s 缓存让 player 在被钢铁墙阻挡时仍盯着同一目标太久，无法及时切换到正在接近基地的敌人，导致基地失守。

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
| `tools/perf/bench-all-stages.ts` | 35 关性能基线（默认 classic/10 games/warmup=2） |
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

- `bun run check` 全绿（test + typecheck + lint + format）
- 644 tests pass，0 fail
- 确定性签名：`ticks=190184 win=59 go=1 timeout=0` @60 games classic/stage0（Round 5 字节一致基线）
- 剩余瓶颈：`think` 13%、`rectHitsTerrain` 8%、`updateNoneTank` 8% —— 均为已精简的核心仿真函数，进一步优化需算法级变更（空间索引等），违反 "simple beats clever"（MANIFEST §10）