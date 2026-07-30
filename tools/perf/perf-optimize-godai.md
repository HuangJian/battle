# God AI 仿真性能优化记录

**日期**：2026-07-30
**工作流**：性能优化（Workflow 2）
**基线**：`perTick=0.024ms` @120 games chaos/stage0 (`ticks=480386 win=6 go=114`)

---

## 优化轮次

### Round 1+2：热路径分配消除（~25% perTick 提升）

**测量**：`perTick=0.018ms` @120 games（冷却状态），确定性签名不变。

| 文件 | 优化 | 反模式（AGENTS §14） |
|------|------|----------------------|
| `ai/perception.ts` | `dirs` 数组提升为模块常量 `PERCEIVE_DIRS`；消除 `others` 数组（直接传 `all` 给 `canStep`）；`threats.sort()` 加空数组守卫；`scanAhead` 返回 `ScanHit` 字符串而非 `{hit, dist}` 对象 | §14.1, §14.2, §14.3 |
| `ai/god/FireControl.ts` | `offsets` 数组提升为模块常量 `VERTICAL_OFFSETS`/`HORIZONTAL_OFFSETS`；`scanAheadImpl` 结果写入 `self._scanResult` 可复用对象 | §14.1, §14.2 |
| `ai/god/ThreatAssessor.ts` | `dodgeDirectionImpl` 消除 `candidates`/`open` 数组，改用 `candA`/`candB` + `safeA`/`safeB` 局部布尔变量 | §14.1 |
| `ai/TacticalIntelligence.ts` | `reactiveDodge` 同上（消除 `candidates`/`safe`/`away` 数组） | §14.1 |
| `ai/GodAIInput.ts` | 添加 `_scanResult` 可复用字段 | §14.2 |

### Round 3：TileMap 数值编码（已回退）

**尝试**：将 `TerrainType[][]` 改为扁平 `Uint8Array`，添加 `TERRAIN_CODES`/`TERRAIN_NAMES`/`getRaw()`。

**结果**：`perTick=0.023ms`（`getRaw`+数值比较）和 `perTick=0.027ms`（`get()`+`TERRAIN_NAMES` 反查）—— 均比 Round 1+2 的 `0.018ms` **更慢**。

**根因**：V8 对字符串字面量做了驻留（interning），`type === 'brick'` 是指针比较，极快。而 `TERRAIN_CODES.brick` 是对导出可变对象的属性查找，V8 无法常量折叠。`TERRAIN_NAMES[code]` 反查也增加了开销，抵消了扁平数组的缓存局部性收益。

**决策**：回退到原始 `TerrainType[][]`。保留 `getRaw()` 方法供未来使用（§14.4 规则：使用内联数字字面量 `=== 1`，不用 `TERRAIN_CODES.xxx`）。

### 反模式扫描（Round 4）

| 文件 | 问题 | 修复 |
|------|------|------|
| `ai/TacticalIntelligence.ts` `reactiveDodge` | `candidates.filter()` + `safe.sort()` + `away.filter()` 每 tick 分配 3 个数组 | 改为 `candA`/`candB` 局部变量 + `aOpen`/`bOpen` 布尔 |
| `ai/god/StrategyPlanner.ts` `selectTargetImpl` | `w.tanks.filter()` 每 tick 分配过滤数组 | 改用 `self._enemies` 缓存（Cluster C 已在 `think()` 中构建） |

**验证**：530 tests pass，确定性签名不变 (`ticks=137139 win=3 go=27`)。

---

### Round 5：Classic-Mode 缓存与预过滤（~27% wall-time 提升）

**基线对齐**：实际调参负载 `optimize-godai.ts` 默认使用 **classic** 难度（非 chaos）。Classic 模式更轻（敌人少、bulletCap 射击模型），perTick ≈ 0.009ms（chaos ≈ 0.018ms）。本轮所有测量使用 classic/stage0/60 games。

**基线**：`perTick=0.009ms` @60 games classic/stage0 (`ticks=190184 win=59 go=1`)

| 优化 | 文件 | 瓶颈占比变化 | 说明 |
|------|------|-------------|------|
| **findPath 数组复用** | `utils/pathfind.ts` | 7.8% → 2.1% | A* 每次 call 分配 6 个 typed array（≈11KB）。改为模块级复用 + `.fill()` 重置，零分配，搜索结果不变 |
| **tankCellImpl 缓冲区复用** | `ai/god/Navigator.ts` | 3.1% → ~0% | `tankCell` 每次 call 分配 `{col,row}` 对象（~15×/think）。改为写入 `self._tankCellBuf` 可复用对象（与 `playerCellImpl` 同模式） |
| **scanAheadImpl 对齐预过滤** | `ai/god/FireControl.ts` | 11.9% → 3.6% | 每 cell 的坦克循环（2 offset × ≤26 cell × N tank aabb）是 #1 瓶颈。按垂直轴对齐预过滤（aabb 常数半条件），将循环从 O(N) 降至 O(alignedN≈0-2)。aabb 检查本身不变——仅减少迭代次数 |
| **findBulletThreatToBase 方向过滤** | `ai/god/ThreatAssessor.ts` | 6.4% → 2.6% | 跳过物理上不可能到达基地的子弹（up-bullet 远离基地；row 22 以上的 left/right-bullet 停留在错误行）。严格超集——无遗漏 |
| **canMoveDir 每 tick 缓存** | `ai/god/Navigator.ts` | 1.4% → ~0% | `canMoveDir` 每 think ~10× 调用，始终用 player，位置在 think() 期间不变。4-bit bitmask 缓存 4 方向结果。**Bug 发现**：passable→blocked 转变时 stale result bit 导致误返回 true——修复为 `&= ~bit` 清除 |
| **onCooldown 早退** | `ai/GodAIInput.ts` | 微小 | bulletCap 模式 cap=1，找到 1 颗 player bullet 即 break |
| **removeDeadEntities 内联** | `game/World.ts` | 无变化 | 内联 6 个泛型 `compact<T>` 调用避免 callback 开销。V8 已内联——无实测变化，但更清晰 |
| **惰性 openDirs** | `ai/perception.ts` + `ai/TacticalIntelligence.ts` | — | perceive 跳过 4×canStep openDirs 扫描；reactiveDodge 按需计算（仅威胁子弹存在时） |

**最终测量**：`perTick=0.006-0.007ms` @60 games classic/stage0。Wall time 1737ms → ~1275ms（~27%）。120 games: `perTick=0.006ms` (`ticks=383734 win=116 go=4`)。

**确定性签名**：全轮不变 (`ticks=190184 win=59 go=1 timeout=0` @60 games classic/stage0)。

**剩余瓶颈**：`think` 13%、`rectHitsTerrain` 8%、`updateNoneTank` 8% —— 均为已精简的核心仿真函数，进一步优化需算法级变更（空间索引等），违反 "simple beats clever"（MANIFEST §10）。

---

## 最终状态

- `bun run check` 全绿（test + typecheck + lint + format）
- 530 tests pass，0 fail
- 确定性签名不变：`ticks=190184 win=59 go=1 timeout=0` @60 games classic/stage0
- AGENTS.md §14 记录了 6 条性能反模式
- DECISIONS.md §45-46 记录了优化决策

## 测量注意事项

- 本机 i7-4770HQ 在长时间运行后会出现热降频，perTick 测量值波动 ±5-10%
- 确定性签名（ticks/win/go）是可靠的正确性指标，不受热降频影响
- Classic 模式复现：`bun tools/perf/profile-godai.ts --games=60 --diff=classic --stage=0 --warmup=5`
- CPU profile：`bun --cpu-prof --cpu-prof-dir=tools/perf/results --cpu-prof-name=sim-classic.cpuprofile tools/perf/profile-godai.ts --games=30 --diff=classic --stage=0 --warmup=5`
- 分析：`bun tools/perf/analyze-profile.ts tools/perf/results/sim-classic.cpuprofile 20`
