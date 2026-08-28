# Design Decisions — Details & Rationale (docs/decisions.details.md)

> Companion to the slim `DECISIONS.md` index. Same § numbering; every decision's full
> **Decision / Rationale / Implications** body lives here when it is not already archived
> in a topic progress doc. When this file and `DECISIONS.md` disagree, `DECISIONS.md` wins
> (the index is the contract); file a fix rather than drifting.
>
> 归档原则：被移出 DECISIONS.md 的条目，正文统一落此文件（Part A–E）；已归档至主题
> progress 文档的条目不在此重复全文，DECISIONS.md 直接指其落点（见文末「已归档索引」）。

---

# Part A. 基石决策（Foundational, §1–§10）

> DECISIONS.md 中每条压缩为一行 + 指针；以下为完整正文。

## 1. Sprite Rendering: SVG → Pre-Rasterized Cache

**Decision:** All sprites are hand-authored SVG (96×96 viewBox), registered in `SPRITE_URLS`,
pre-rasterized at load time by `SpriteCache` into DPR-scaled bitmaps. No PNG assets.

**Rationale:** Zero binary assets, theme colors applied at draw time. Future bitmap
sprites extend the registry without replacing it.

---

## 2. Audio: Web Audio API Synthesis

**Decision:** All sound effects synthesized at runtime via Web Audio API. No audio files.

**Rationale:** Zero audio assets, retro 8-bit aesthetic, no external dependency.

---

## 3. Tile System: 26×26 Sub-block Grid

**Decision:** 26×26 grid of 16px sub-blocks. Tanks = 2×2 sub-blocks (32×32px). Playfield = 416×416px.

**Rationale:** Matches classic Battle City proportions. Sub-block granularity enables precise brick destruction.

---

## 4. Stage Data: TypeScript Config (not JSON)

**Decision:** Stage data in TypeScript config files (`src/config/stages.ts` + `stageData.ts`).
No async loading needed.

**Rationale:** Type safety, IDE autocompletion, bundled at build time. JSON-compatible structure
enables future externalization via `fetch()`.

---

## 5. Classic Stages: 35 Authentic NES Layouts

**Decision:** Ship 35 original Famicom stages. Raw 13×13 numeric grids in `stageData.ts`
decoded to 26×26 char grids by `stages.ts`. Enemy forces from authentic data.

**Rationale:** Authentic layouts with partial brick/steel pieces preserved losslessly.
Data is diffable against reference; appending a stage = adding a grid row.

---

## 6. Movement: Perpendicular Axis Snapping

**Decision:** Perpendicular axis snapped to nearest 16px cell boundary every frame.

**Rationale:** Enables navigation through 1-tile corridors. Turning only at grid intersections (classic behavior).

---

## 7. Enemy AI: Tactical Intelligence Framework

**Decision:** Every enemy runs one pipeline: `Perception → Situation → Goal → Decision → Action`.
Three time scales (strategic ~20s, tactical ~5s, reactive per-tick). Intelligence is config, not code
(`src/ai/config.ts`). Tiers: `none/rookie/soldier/veteran/commander`. Tier rolled at spawn
from per-difficulty distribution. Commander broadcasts influencing (non-controlling) directives.

**Rationale:** Data over code. New tier = one registry entry. Full detail in DECISIONS §29 and `docs/features.md` §4.

---

## 8. Game Loop: Fixed Timestep with Accumulator

**Decision:** Fixed 1000/60ms timestep, max 5 sim steps per render frame.

**Rationale:** Deterministic simulation, stable physics regardless of frame rate.

---

## 9. Input: Per-Frame Edge Detection + Last-Pressed-Wins

**Decision:** `endFrame()` clears edge state once per render frame. `moveStack` resolves
held keys by "last pressed wins" order.

**Rationale:** Per-frame edge detection for menus; last-pressed-wins for intuitive tank control.

---

## 10. Base Destruction: All Cells at Once

**Decision:** Any bullet hit on any base sub-block destroys all base sub-blocks simultaneously.

**Rationale:** Classic Battle City behavior — any base hit = game over.

---

## 附录 A1. Recovery-Screen UI State Guards → uiFlowGates.ts（Gameplay 索引表条目）

**Decision:** Recovery 屏按钮（Replay Browser / Lie-Back Win / Key Bindings）因 `Game.ts` 的状态守卫
忘记 `'recovery'` 状态而失效/报错；守卫逻辑抽取为无 DOM 的纯谓词模块 `uiFlowGates.ts`。

**Rationale:** Game.ts 逐处内联守卫是回归温床（漏状态即死按钮）；抽为纯谓词后可由
`tests/recovery-screen-flow.test.ts` 无头回归，Game.ts 与测试共用同一份判据——单一事实源。

---

# Part B. 重构与工程（Refactor & Engineering, §239–§271）

> plan/refactor.agy.md / refactor.trae.md 三轮重构的逐条落地记录与否决论证；
> DECISIONS.md 中每条压缩为一行 + 指针。

## 239. §1.6 魔法数字 → 命名常量 (STATUS: 已实施, plan/refactor.agy.md Phase 1)

**Decision:** 在 `constants.ts` 新增 `SEED_HASH` (0x9e3779b9)、`P2_SEED_OFFSET` (0xdeadbeef)、
`TURN_SENTINEL_MS` (-9999)、`POPUP_DURATION_MS` (1500)、`GAME_OVER_TIMER_MS` (3000)、
`SMALL_EXPLOSION_MS` (200)、`BIG_EXPLOSION_MS` (500)；将 `src/game/` 全部 21 处 `1000 / 60`
字面量替换为既有 `TICK_MS`。纯机械替换，数值逐一相等。

**Rationale:**
- plan/refactor.agy.md §1.6：散落的魔法值迫使 agent 逐处确认语义；命名后可检索、可审计。
- 保护文件豁免：`src/ai/god/think.ts` / `ActionCandidates.ts`（AGENTS §5.1 God AI 禁区）内的
  同字面量保持原样；`ThreatBudget.ts` 不在禁区，已一并替换。
  *（§262 修订：禁区已废除，豁免条款失效。）*
- BONUS TIME 弹出（1800ms）语义独立于击杀 popup，保留字面量并加注释，不强行绑定常量。
- `{ col: 8, row: 24 }` P2 默认出生点一项在计划中已过时（现由 PLAYER_SPAWN /
  player2SpawnPoint 集中管理），无需改动。
- 全套测试（含 God-AI 确定性门禁）通过 = 字节级零行为变化的实证。

**Implications:** 后续新增计时/种子派生逻辑必须引用这些常量；`1000 / 60` 字面量回归视为 bug。

## 240. §1.5 Option C — WorldSerializer 字段覆盖测试守卫 (STATUS: 已实施, plan/refactor.agy.md Phase 1)

**Decision:** 新增 `tests/serializer-field-guard.test.ts`：断言 (a) World 每个实例字段要么出现在
`cloneWorld` 输出、要么登记在带理由的 `EXCLUDED` 豁免表中；(b) 快照每个字段能映射回活的
World 字段（防改名后陈旧序列化）。已验证守卫有效性——临时给 World 加字段测试即红。

**Rationale:**
- 手工 45+ 字段枚举的失败模式是静默的（游戏正常跑，快照悄悄丢字段），测试无法靠运气覆盖。
- EXCLUDED 表把每个"故意不序列化"的决定显式化（transient 视觉态、UI 态、重推导字段、
  perf 缓存、rewindPending 单 tick 信号等 19 项），agent 加新字段时被迫二选一：进序列化器
  或写明豁免理由。
- 零运行时成本（Option C 的优势）；tileGrid→tileMap、rngState→rng 两个改名映射内置于 KEY_MAP。

**Implications:** 给 World/Tank 加字段时该测试是强制关卡；Tank 字段由 cloneTank 的展开运算符
天然覆盖，不在本守卫范围。

## 241. §3.2 快照/回放基础设施去重 (STATUS: 已实施, plan/refactor.agy.md Phase 1)

**Decision:** 抽出 `src/utils/idb-store.ts`（泛型 `IndexedDBStore<T>`，封装 open/tx/request
样板）与 `src/utils/uuid.ts`（唯一 `generateUUID`）。`snapshot/storage.ts` 与
`replay/storage.ts` 变成薄包装（各自保留独立 DB 名/存储名与领域接口）；删除
`replay/uuid.ts`，`SnapshotManager` 内联副本一并移除。

**Rationale:**
- plan §3.2：两份 IndexedDB 包装逐行相同、两份 UUID 实现语义相同——改一处漏一处的经典温床。
- 领域接口（SnapshotStorageBackend / ReplayStorageBackend）与 DB 命名不变 → 消费方零改动，
  持久化布局不变。
- uuid 的 Math.random 回退仅用于元层 ID，不进模拟层，符合 AGENTS §2.3。

**Implications:** 新持久化域（如设置云备份）直接复用 IndexedDBStore；generateUUID 只允许从
utils/uuid 导入。

## 242. §2.8 方向助手整合 → utils/direction.ts (STATUS: 已实施, plan/refactor.agy.md Phase 1)

**Decision:** 新建 `src/utils/direction.ts` 作为方向数据/助手的唯一定义源（`Direction` 类型、
`DIR_VECTORS`、`DIR_DX/DY`、`dirIdx`、`ALL_DIRS`、`opposite/turnCW/turnCCW/moveDir`）。
constants.ts 与 helpers.ts 保留兼容再导出；pathfind.ts 私有的 STEP_DC/STEP_DR/STEP_DIR
逐字节重复表替换为共享 DIR_DX/DIR_DY/ALL_DIRS 别名；EDGE_* 表是坦克足迹专属，留在原地。

**Rationale:**
- plan §2.8：三处独立定义同一语义，改一处漏两处。
- 不整体搬迁 `Direction` 导入路径（79 文件引用）：80 文件 churn 收益不成比例，违反
  Three Gates "保持简单"；再导出 shim 达成"单一来源"目标且零消费方破坏。
- helpers 的再导出因保护文件 ai/god/think.ts 引用 ALL_DIRS 而必须保留（AGENTS §5.1 禁区
  不可触碰）；其余非保护消费方已改为直连 direction.ts。
  *（§262 修订：禁区已废除，think.ts 已直连 direction.ts，helpers 再导出已移除。）*
- pathfind 热循环语义不变：同名同值模块级常量，索引访问无分配。

**Implications:** 新代码从 utils/direction 导入方向符号；constants/helpers 的再导出仅为
兼容层，待保护文件解禁后可移除。

## 243. §3.4 共享测试 fixtures → tests/helpers.ts (STATUS: 已实施, plan/refactor.agy.md Phase 1)

**Decision:** 新增 `tests/helpers.ts`：`createTestWorld`（种子化 RNG）、`clearArena`
（清场 + 恢复基地 2×2，30 处复制的规范形态）、`placeEnemy(world,col,row,kind?,dir?)`、
`positionPlayer(world,col,row,dir?)`。用严格 codemod 迁移**逐字节语义等价**的本地副本：
28 个测试文件净删 ~200 行。语义变体（像素参数、(col-1) 映射、dir/hp 参数、ringArena
关卡生成器、makeBullet/makeTank 各自为政的字段差异）**有意保留本地实现**。

**Rationale:**
- plan §3.4：41× clear-arena、22+ placeEnemy、19+ positionPlayer 的复制粘贴。
- 测试几何是断言的一部分：`col*16 - 8` 与 `col*CELL` 是不同的中心语义，强行统一会静默
  改变测试含义。只迁移可证明等价的形态；变体是合法的领域特定 setup。
- codemod 带调用点 arity 门禁与 `alive = true` 冗余行豁免（createTank 已保证）；确定性
  全套门禁通过 = 迁移零行为变化的实证。
- makeBullet 变体（damage/speed/ownerKind 参数）互不兼容，收益低于风险，未迁移。

**Implications:** 新测试一律从 tests/helpers.ts 取 fixture；helpers 语义已冻结——修改前先
grep 调用点。变体族如需统一须逐个验证几何关系。

## 244. §1.2 GameLoop.loop 分解为命名步骤方法 (STATUS: 已实施, plan/refactor.agy.md Phase 2)

**Decision:** `GameLoop.loop`（322 行）分解为 13 个单一职责方法：`computeDelta` /
`beginPerfProbe` / `handleFrameInput` / `stepSimulation`（含 stage 检测、终局拦截、防螺旋钳
制、时光宝盒信号）/ `stepRecovery` + `rebuildAfterRestore` / `stepSnapshots` /
`dispatchWorldEvents` / `stepRender` / `captureSnapshotThumbnails` / `syncUI` /
`endFrameInputs` / `samplePerformance` / `updateStateTracking`。loop 本体缩至 ~20 行调度器。

**Rationale:**
- plan §1.2：任何循环行为改动都需通读 322 行交织关注点；插入新"阶段"需在巨石中找位置。
- 执行顺序是确定性承重墙（AGENTS §2.3）：语句逐一原位搬移，零重排；全套测试含 God-AI
  确定性门禁通过 = 语义不变实证。
- perf 探针计时窗口逐字节保持：simMs/renderMs/uiMs 的起止点随所属步骤闭合在方法内，
  probe 状态放实例字段 `_probe`（构造期一次分配，帧路径零分配，AGENTS §14.1）。

**Implications:** 新增循环阶段 = 新增一个 step 方法并在 loop 调度器中登记；各 step 可独立
阅读/测试。禁止对 loop 内调用顺序做任何重排。

## 245. §2.1 击杀管线抽取 → KillPipeline.ts (STATUS: 已实施, plan/refactor.agy.md Phase 2)

**Decision:** 新建 `src/game/KillPipeline.ts`：`recordEnemyKill(w, victim, opts)` 统一
"击杀计分→入账→计数→弹分数字幕"四步（`toScore2` 支持 Lie-Back-Win P2 分流，
`countsTowardStage` 支持 isExtra 余额兵不计入场配额）；`destroyBrickAoE(w,cx,cy,r)`
统一两处逐字节相同的砖墙 AoE 循环。四处调用点（bulletHitsTank / updateMines /
triggerSacrificeAoE / applyPowerUp('bomb')）改为一行调用。

**Rationale:**
- plan §2.1：同一管线复制五份，改计分规则需改五处。
- 差异点参数化而非复制：combat 的 God 分流与 isExtra 豁免是 opts 字段；爆炸/事件/掉落
  语义各站点不同，留在调用点（函数只做共同核心，不做上帝函数）。
- 仅由 Simulation mixin 调用 → One-Author 不变。击杀是稀有事件，opts 对象分配无热路径
  顾虑（AGENTS §14 针对每 tick 路径）。
- combat 站点 `gained` 返回值被后续里程碑掉落逻辑复用（w.score - gained），保留返回值。

**Implications:** 新增击杀来源必须走 recordEnemyKill；直接写 w.score/enemiesRemaining 的
新代码视为 bug。

## 246. §2.2 P1/P2 生命周期集中化 → World.enablePlayer2/disablePlayer2 (STATUS: 已实施)

**Decision:** `World` 新增 `enablePlayer2({ respawnShield? })`（难度推导 lives2/星级、镜像
出生点、生成坦克）与 `disablePlayer2()`（清 tank/lives2/playerLevel2）。替换全部 11 处
复制站点：SimulationCore 2 处（coop 切换 + 督战双玩家）、GameCore 5 处（requestCoopToggle
开/关、enableSpectate coop 退出、disableSpectateDual、disableSpectate wasDual）、
resetToMenu 及其余。

**Rationale:**
- plan §2.2：P2 设置/拆除六处复制，改一处漏五处是状态腐化的温床。
- respawnShield 参数保留既有行为差异：sim tick 内启用给护盾，菜单时启用不给——逐字节
  保持原语义，不做"顺手修正"。
- disablePlayer2 不动 score2：中途退出 coop 不清分是既有行为；resetToMenu 单独清。
- GameCore 直接改 World 仍是 §1.4 违规，但改动收口到 World 方法后，§1.4 的完整 One-Author
  路由有了明确落点。

**Implications:** P2 生命周期只有两个入口；新代码禁止手写 player2=null/lives2=0 三连。

## 247. §2.3 自由格搜索统一 → GridQuery.findNearestFreeCell (STATUS: 已实施)

**Decision:** 新建 `src/game/GridQuery.ts`：`findNearestFreeCell(originX, originY, free)` 统一
World.findFreeSpawnCell 与 SimulationPowerUps.findFreeDropCell 共享的"32px 网格最近自由格
全场扫描"骨架，freedom 谓词由调用方注入（spawn=地形+坦克不重叠；drop=地形+避开出生点，
允许坦克）。isTankPositionClear 的内联地形四连检查替换为语义相同的 rectHitsTerrain
（brick/steel/water/base 同集合）。decoySpawnCell **有意保留**本地实现。

**Rationale:**
- plan §2.3：四处独立"找空位"，其中两处骨架逐行相同仅谓词不同——谓词注入是忠实抽象。
- 提前返回 `free(rx,ry)→origin` 对两个调用方均行为等价（drop 路径原为 d=0 严格小于胜出，
  结果相同），故共享版保留早退。
- decoySpawnCell 是环形 ±3 受限扫描 + 非正交偏好层 + 可空回退——契约不同不是复制，强行
  统一需回调堆叠，违反 Three Gates "保持简单"。
- findNearestFreeCell 不收 World 参数：谓词闭包自持引用，签名更诚实。

**Implications:** 新增"全场最近空位"需求一律走 GridQuery；受限扫描/偏好扫描属不同契约。

## 248. §2.9 + §3.8 时间单位命名约定 + AI 常量归位 (STATUS: 已实施)

**Decision:** (a) constants.ts 顶部新增 `*_MS / *_FRAMES / *_TICKS` 命名约定注释块——后缀即
契约，单位必须在调用点一目了然。(b) 九个 AI 专属常量（TACTICAL/STRATEGIC/COMMANDER_INTERVAL_MS、
DODGE_LOCK_MS、NONE_TURN_MIN/JITTER_MS、NONE_FIRE_JITTER_MS、VERT_TUNNEL_THRESHOLD_MS、
CORRIDOR_ESCAPE_CHANCE）从 constants.ts 迁至 `src/ai/config.ts`；消费方
TacticalIntelligence/World/corridor-escape 测试改从 ai/config 导入。

**Rationale:**
- plan §2.9：毫秒与帧数混排无约定可循；plan §3.8：AI 调参值住在共享常量文件里误导检索。
- ai/config.ts 本就是 AI 数据的家（INTELLIGENCE_LEVELS 等），World 已依赖它——迁移零新边。
- 数值逐字节不动（含 CORRIDOR_ESCAPE_CHANCE 的 0.005601），全套确定性门禁通过。

**Implications:** 新时间常量必须带单位后缀；AI 行为调参只动 src/ai/config.ts。

## 249. §3.6 四套 Worker Pool 统一 → tools/lib/worker-pool.ts (STATUS: 已实施)

**Decision:** 新建 `tools/lib/worker-pool.ts`：`WorkerPool<TTask, TResult>`（常驻池，任务逐个
派发，结果按 id 重排——与串行 for 循环同序，浮点聚合稳定）+ `runChunkedWorkers`（每块一个
一次性 worker，块内聚合 `{results}`，返回展平数组）。四个消费方改薄包装/一行调用：
SimWorkerPool、ForensicPool 继承泛型池；gate-core / score-gate-core 换 runChunkedWorkers。

**Rationale:**
- plan §3.6：四份相同的 dispatch/error/termination 循环，修 bug 需四处同步。
- gate-core 的实测注释保留为规范：Bun Worker 属性赋值 onmessage/onerror 在部分版本不触发，
  addEventListener 是已验证安全路径——共享实现统一采用。
- SimWorkerPool/defaultWorkerCount 导出名不变 → 十余个 diag 脚本零改动。
- 烟测验证常驻池路径（tools 不在 bun test 覆盖内）：4 任务按 id 有序返回；重型 God-AI
  门禁经 runChunkedWorkers 全绿。

**Implications:** 新批量工具一律复用本模块；禁止再手写 worker 循环。物理核检测
(physicalCores) 一并归位 lib/。

## 250. §2.7 pathfind.ts 解耦：utils → ai/god + grid-search (STATUS: 已实施)

**Decision:** `src/utils/pathfind.ts`（792 行）拆分：(a) 离线连通性助手（Cell/isPassable/
isReachable/floodFill/pxToCell）→ `src/utils/grid-search.ts`（tools 关卡生成器与评估器的
通用工具）；(b) God-AI A* 导航引擎（PathConstraints/fireClearStopTicks/A* 缓冲+findPath）
整体迁 `src/ai/god/pathfind.ts`。原文件变兼容再导出 shim（保护文件 think.ts 仍从旧路径引
Cell）。非保护消费方（8 个 AI 文件、3 tools、2 tests）改直连。

**Rationale:**
- plan §2.7：AI 领域逻辑不应住在 utils——agent 浏览 utils 找"通用工具"时会撞上 700 行
  带火控模型/破砖语义的 A*。
- 实际考察修正计划表述：PathConstraints 本就是"代价函数参数注入"设计（§2.7 的目标形态），
  fireClearStopTicks 无外部引用——真正的错位是文件位置，而非 API 形态。
- 切片脚本按行号搬移保证字节不变；A* 内循环顺序未动（确定性）；全套门禁含 God-AI 门禁通过。
- probe-findpath-parity 从 git 历史读原文做对齐检查，不受影响。

**Implications:** 新导航代码进 ai/god/pathfind；新离线几何工具进 grid-search；
utils/pathfind 仅作兼容层存在。

## 251. §1.3 Phase C — highScore 持久化 I/O 迁 settings.ts (STATUS: 已实施)

**Decision:** `localStorage` 读写（loadHighScore/persistHighScore + HIGH_SCORE_KEY）从 World
迁至 `src/game/settings.ts`（与 SETTINGS_KEY 同居）。World 的 `highScore` **字段保留**——它是
序列化游戏状态（快照携带、UIManager 读取）；World.saveHighScore() 公共签名不变，内部改调
settings 模块。

**Rationale:**
- plan §1.3 Phase C：浏览器 I/O 不属于 God Object；但整个字段外移会破坏快照契约与 UI 读点。
- 行为逐字节不变：同样的 key、同样的 try/catch 静默失败、同样的"仅超越才写"逻辑。
- headless 测试环境无 localStorage → try/catch 原样兜底，全套门禁通过。

**Implications:** 新持久化键一律进 settings.ts；World 不再直接触碰 localStorage。

## 252. §3.1 双渲染器 fallback 移除 — 否决（前提不成立） (STATUS: 否决)

**Decision:** 不移除 SpriteArtistTerrain/Tanks/Effects 中的程序化 Canvas2D 绘制路径。
plan/refactor.agy.md §3.1 称其约 500 行为"死重"（dead-weight）——经核实前提错误。

**Rationale:**
- `GameRendererCore.ts:224`: `artist.skipSvg = themeKey !== 'modern'`——Classic 与 Neon
  主题**故意**走程序化路径，用 ThemeColors 实时着色，而非 Modern-Retro 调色的 SVG
  （SpriteArtistCore.ts:355-358 注释明确记载该设计意图）。
- 这正是 MANIFEST themability 原则的执行："sprite 颜色随主题变化来自 ThemeColors 在绘制时
  应用，不烘焙进 SVG"。SVG 管线只服务 modern 主题。
- 删除后果：Classic/Neon 下坦克/地形直接消失。渲染无深度单测覆盖，此类回归测试抓不住。
- plan 自身的前提条款"Once SVG asset coverage is verified as complete"已满足，但覆盖完成
  ≠ fallback 死亡——它们是主题分支，不是遗留物。

**Implications:** 若未来要让全主题走 SVG，须先为 Classic/Neon 生成主题化 sprite 变体并
扩展 SpriteCache 键空间——那是新特性工作，不是清理。§3.1 关闭。

## 253. §2.6 types.ts 重组织 — presentation-only 类型迁出 (STATUS: 部分实施)

**Decision:** 四个纯 presentation 消费的类型（VisualComponent/Particle/EmitterConfig/
CameraState）定义迁至 `src/presentation/types.ts`，src/types.ts 兼容再导出。
ThemeColors/ThemeDefinition **有意保留**在根 types.ts。

**Rationale:**
- plan §2.6：624 行厨房水槽类型文件。实际考察消费方后收窄范围：只有上述四个类型的全部
  使用点都在 presentation 层（AnimationSystem/Camera/ParticleSystem/PresentationLayer）。
- ThemeColors 是 config 层契约（config/theme.ts 定义 THEMES 数据）——迁去 presentation 会
  制造 config→presentation 的反向依赖，违反分层。Tank 拆分（PlayerState/EnemyAIState 组合）
  plan 自评"影响序列化，谨慎评估"——收益不抵快照契约风险，不做。
- 再导出保持零消费方改动；新 presentation 代码应从 ./types 导入。

**Implications:** 根 types.ts 现只含 sim/config/UI 契约；presentation 视觉类型有独立家。

## 254. §1.1 Mixin→组合：Simulation（21 stubs 归零） (STATUS: 已实施, plan Phase 3)

**Decision:** 六条 mixin 链（SimulationSpawn/Player/Enemies/Combat/PowerUps/Effects）转换为
六个显式子系统类，经共享注册表 `SimulationSystems`（src/game/systems.ts）互联；
`Simulation` 本体持有注册表 + 延迟 coop/spectate 切换 + tick/updatePlaying 编排 +
togglePause。SimulationCore 与 21 个 throw stub 删除。测试白盒入口改为
`sim.systems.<subsystem>.<method>`（新增公共 getter `systems`）。

**Rationale:**
- plan §1.1：stub 只为类型检查存在、跨 mixin 调用对静态分析不可见、改一个 mixin 有运行时
  throw 风险——组合后依赖显式（d.effects.createExplosion），新系统=新类+注册表一行+编排器
  一行。
- 环依赖（Player↔PowerUps、Enemies↔Effects）用"构造后填充的注册表"解决：方法只在运行期解引用。
- 行为不变实证：语句原位搬移（codemod 按行切片+映射重写），全套 1411 tests 含 God-AI 确定性
  门禁（数千 headless sims）字节级通过；TacticalIntelligence 实例移入注册表（enemies 使用）。
- 测试侧 codemod 重写 44 处 `(sim as unknown as {...}).m` cast 为 systems 访问；少数多行
  变体手工修复；applyPowerUp/rollPowerUpType/bulletHitsTank/guardAIById/activateFrenzy 等
  白盒方法转公开。

**Implications:** Simulation 公共 API 不变（tick/requestX/togglePause/input/input2/systems）；
给 World 加系统逻辑时新建 System 类而非 mixin。Game 链（26 stubs）同法待办。

## 255. §1.1 Mixin→组合：Game（27 stubs 归零） (STATUS: 已实施, plan Phase 3)

**Decision:** 四条 Game mixin 链转换为控制器类：LoopController（rAF 循环+事件处理）、
MenuController（菜单/暂停输入+主题动作）、SnapshotController（快照框架+恢复流）、
ReplayController（录制/回放/浏览器/导出）。`Game` = 原 GameCore 字段与方法 + 控制器
back-reference（`g: Game`）+ 27 个一行委托器取代 throw stubs。GameCore.ts 删除。

**Rationale:**
- 与 Simulation 同法（§254）；差异点：Game 的 mixin 是"同一控制器的功能切片"而非独立系统
  ——共享几十个字段，故用 back-ref 而非依赖注册表；protected 成员转公开供 g.* 访问。
- codemod 按文件 own-member 集合区分 this.x（本切片）与 this.g.x（跨切片）；发现并修复
  前缀碰撞漏改一处（this.startRecovery 因 own 含 start 被保护）。自建"无定义引用"检查器
  扫四个文件确认归零。
- 验证：tsc + oxlint + vite build 通过；bun test 全套绿。注意 bun test 不构造 Game——
  按 AGENTS 规范以构建门禁为 UI/编排层的验收（运行时行为由人工 playtest 兜底，结构由
  类型系统锁定）。
- 构造顺序安全：控制器仅存 back-ref，方法体不在构造期执行。

**Implications:** 新增跨切片入口=在对应控制器加方法+在 Game 加委托器；main/harness 的
公共 API（start/stop/world/fps/requestFrame）不变。

## 256. §1.1 Mixin→组合：GameRenderer + SpriteArtist（41 stubs 归零） (STATUS: 已实施)

**Decision:** 六条渲染 mixin 链转换为切片类：TerrainRenderSlice / EntityRenderSlice /
EffectsRenderSlice（挂 GameRendererCore）、TerrainSpriteSlice / TankSpriteSlice /
EffectSpriteSlice（挂 SpriteArtistCore）。Core 在自身构造器中创建切片（back-ref 仅存储），
原 40 个 throw stub 变为真实委托器；GameRenderer/SpriteArtist 退化为继承薄壳 + 模块助手
再导出。至此全仓 mixin 归零：Simulation(21) + Game(27) + Renderer/Artist(41) = 89 stubs → 0。

**Rationale:**
- 与 §254/§255 同法。切片经 r.<member> 访问宿主，宿主经委托器进入切片——双向显式。
- Core 的 protected 成员转公开（切片横切访问）；mixin 级字段（如 Terrain 的 _nmask 缓冲）
  随切片迁移保持私有归属。
- codemod 三次迭代教训入档：(a) 正则跨方法匹配会产生垃圾签名——改为"逐 throw 行回溯方法头"
  的行状态机；(b) 方法名含数字（drawPlayer2Tank）时 [a-zA-Z_]+ 不匹配——字符类需含 0-9；
  (c) 重建委托器前必须截断已推送的原签名行。自建"无定义引用"检查器扫全部切片确认零漏改。
- 验证：tsc/oxlint/oxfmt/vite build 全绿 + 1411 tests 全过；渲染运行时行为由 vite build +
  结构类型锁定，视觉回归由人工 playtest 兜底（AGENTS 规范：渲染层以构建门禁验收）。

**Implications:** plan/refactor.agy.md §1.1 全部完成。新增渲染子系统=新切片类+Core 委托器；
metrics 表目标"Mixin stub methods: 0"达成。

## 257. §2.6 types.ts 重组完成 + Tank 拆分否决 (STATUS: 已实施/部分否决)

**Decision:** (a) 敌方大脑类型 `IntelligenceLevel` / `GoalType` / `CommanderDirective` /
`AIState` 迁至 `src/ai/types.ts`（该文件原仅持有 Perception/Situation 等框架类型，现成为
AI 数据契约的唯一归所）；(b) config 层数据契约 `DifficultyConfig` / `StageData` /
`ThemeColors` / `ThemeDefinition` 新建 `src/config/types.ts` 归位（延续 §253"ThemeColors
是 config 契约非 presentation 状态"的判断，只是换到更准确的层）；(c) 根 types.ts 保留
兼容 re-export，全部调用点零改动。**Tank 拆分为 PlayerState/EnemyAIState/WeaponState
经评估否决**。

**Rationale:**
- 迁移后根 types.ts 562→378 行，只剩核心实体（Tank/Bullet/PowerUp/…）、事件、设置契约
  ——厨房水槽问题按计划三条整改线全部落地。
- Tank 拆分否决理由：(1) 类型级拆分（intersection）纯化妆，不减少任何理解成本；
  (2) 字段级嵌套则 WorldSerializer 平铺克隆、snapshot 框架测试、~100 处字面量构造点
  全部要动——高回归面、零玩家价值，违背三门第 1/2 条；(3) 计划原文即标注
  "affects serialization and snapshot — evaluate carefully"，评估结论为负。
- GameSettings/KeyBindings 留守根文件：它们是被 game 与 presentation 双层消费的
  应用级契约，拆出无摩擦收益。

**Implications:** 新增 AI 数据结构 → ai/types.ts；新增配置数据契约 → config/types.ts；
根 types.ts 只进核心实体与事件。plan/refactor.agy.md §2.6 关闭（Tank 部分以否决关闭）。

## 258. §3.3 Browser 去重 — 最小提取（formatCreated/formatBytes） (STATUS: 已实施, 范围修正)

**Decision:** 新建 `src/presentation/ui/helpers.ts`，仅收编两处**逐字节相同**的助手：
`formatCreated`（MM-DD HH:mm 时间戳）与 `formatBytes`（B/KB/MB）。其余计划声称的重复
经核实不成立或不宜合并，明确不做。

**Rationale:**
- `formatPlayTime` 两处语义不同：Snapshot 只显示整分钟（`05m`），Replay 按时长自适应
  （`05m` / `42s`）。合并即改变可见行为——不是重复是分歧。
- 拖拽导入仅 ReplayBrowser 实现，SnapshotBrowser 无此功能，无可去重。
- 过滤页签/条目卡片结构相似但绑定不同数据模型（SnapshotType vs ReplayType+favorite），
  抽 BrowserBase 的耦合代价大于 ~40 行节省（三门：复杂度不划算）。
- 计划的审计数字基于行数估算；本次按"逐字节相同才提取"的标准执行。

**Implications:** 浏览器类新增共享格式化助手 → ui/helpers.ts；两浏览器的显示契约
保持各自独立。

## 259. §3.7 diag 脚本清理 — 归档 5 个零引用一次性脚本 (STATUS: 已实施, 范围修正)

**Decision:** 新建 `tools/diag/archive/`，归档经全仓核实**零引用**的 5 个一次性取证脚本：
`ab-score-dims` / `diag-godai` / `diag-ice-deaths` / `diag-suicide-cond` /
`diag-suicide-events`（归档内 README 说明甄别标准）。其余 29 个全部保留。

**Rationale:**
- 引用核查口径：basename 在 src/tests/tools 的 import + 全部 *.md（AGENTS/DECISIONS/
  docs/plan）中的出现。base-loss-run/worker 虽 md:0 但被 AGENTS 点名的
  base-loss-forensics 导入——保留；m4-diagnose 被 DECISIONS §212 明确"保留为诊断
  流程"——保留；t2a-audit/toolate-audit/dodge-audit 是 §218–§227 协议工具——保留。
- 计划的另一条"裸循环脚本改用 simulation-runner"经核实大多不成立：这些脚本的价值恰在
  循环体内的逐 tick 自定义取证钩子（trace/idle/AoE 剖析），runner 不暴露等价钩子，
  强迁会破坏取证能力（三门第 1 条不成立）。
- tools/ 在 tsconfig include 内，归档文件同步修正相对导入层级并保持编译通过。

**Implications:** diag 目录 35→30 个活动脚本；归档区不计入新会话的扫描面；
复活归档脚本前须先对齐当前 World/Simulation API。

## 260. §3.5 tests 目录重组 — 否决（代价/价值失衡） (STATUS: 否决)

**Decision:** 不执行 tests/ 子目录化与 god-ai-*→godai-* 重命名，维持 132 文件扁平结构。

**Rationale:**
- 机制核查：tools/test-silent.ts 的 walk() 本就递归子目录、basename 映射不依赖路径，
  子目录化不会破坏 scoped runner——技术可行性不是否决理由。
- 否决理由是代价：(1) 全部测试的 `../src/...` 相对导入需逐文件重写（~500+ 处）；
  (2) god-ai-* 重命名牵连 tools/test-silent.ts HEAVY_TESTS 精确名单、gate-core 的
  part-file 清单、AGENTS.md 行内文档与历史 DECISIONS 的测试名引用；(3) 巨量 git mv
  噪音污染 blame/archaeology；(4) 玩家价值为零，摩擦收益（目录浏览）被 grep/basename
  定位习惯完全覆盖。
- 计划原文即标注 "low priority / Caution ... Evaluate before executing"，本条为该
  评估的结论记录。

**Implications:** 新测试继续平铺于 tests/；命名沿用现有约定（域前缀一致即可）。
若未来测试数 >300 再重新评估。

## 261. §2.4 UIManager 拆分 — 四子控制器组合 (STATUS: 已实施)

**Decision:** 1560 行的 UIManager 按 §256 切片模式拆为四个子控制器，UIManager 收缩为
448 行编排器（组装 + update 编排 + showScreen + 主题/i18n 桥接 + toast）：
- `HudView`（438 行）：HUD 条 + 逐帧 world 同步（分数动画/生命/星星/buff 倒计时/
  超级道具计数/督战与回放徽章/Take Over 按钮）
- `MenuScreen`（~420 行）：开始菜单布局、配置行选项、关卡下拉、RESUME 展示、
  光标高亮同步
- `ControlsPanel`（268 行）：键位绑定模态框（点击重绑/冲突检测/恢复默认）
- `OverlayManager`（240 行）：暂停/游戏结束/过关/胜利/恢复五块覆盖层

**Rationale:**
- 公共 API 零变化：initMenuActions/initControls/showScreen/update/setReplayMode/
  notify 等全部保留为委托，Game/GameLoop/GameMenu/PresentationLayer 等调用点零改动。
- 方法体逐字搬移；跨切片桥接仅两处：Take Over 点击路由（HudView 构造时注入回调）
  与超级道具键位标签（ControlsPanel.onSuperLabelsChanged → UIManager → HudView）。
- formatCode 提取为 HudView 导出函数供两切片共用（原为 UIManager 私有方法，
  ControlsPanel 与 HUD 标签刷新各需一份——共享消重复而非复制）。
- 验证：tsc 零错误、vite build 成功、全量 1411 tests 通过。DOM 运行时行为按 AGENTS
  规范以构建门禁验收。

**Implications:** plan/refactor.agy.md §2.4 关闭，全计划条目清偿完毕。新增 UI 面 =
对应切片内加成员；UIManager 不再直接持有 [data-hud]/菜单 DOM 引用。

## 262. 废除 God AI 禁区（AGENTS §5.1 幽灵规则消歧） (STATUS: 已实施, plan/refactor.trae.md §0.1)

**Decision:** 废除 `src/ai/god/think.ts` / `ActionCandidates.ts` 的"禁区"保护
（§239 / §242 记载的 "AGENTS §5.1 God AI 禁区" 豁免条款全部失效）。两文件回归
正常工程流程：§7 bug-fix 工作流、§14 热路径纪律、以及触碰后必须过 determinism
签名门（§254 流程）。同时移除为禁区存活的兼容层：`utils/helpers.ts` 的方向
re-export 与 `utils/pathfind.ts` shim（唯一消费方 think.ts 已改为直连
`utils/direction` / `utils/grid-search`）。

**Rationale:**
- 规则已成幽灵：当前 AGENTS.md 无 §5.1、无任何禁区文本。遵循 DECISIONS 的 agent
  认为两文件不可编辑（直接阻塞一切复杂度削减工作）；遵循 AGENTS 的 agent 会误删
  compat shim 击穿导入路径。每次 session 都重新推导一遍二义。
- 原保护目标（防止无护栏手改破坏 God-AI 调参成果）已由测试结构达成：godai-*
  行为 gate 家族 + `bun test --parallel --timeout=50000` 全量门 + 批模拟
  determinism byte-identical 签名流程。
- 人工批准（2026-08-23）：在 plan/refactor.trae.md §0.1 的"废除/重述"二选一中
  用户选择废除。

**Implications:** plan/refactor.trae.md Phase 3（params 拆分、候选提取等）放行。
行为调参本身仍走 §6.3b Phase III 评估框架，禁区废除只解除"不许编辑"的工程约束。

## 263. 第二轮重构落地汇总（plan/refactor.trae.md B1–B3） (STATUS: 已实施, 2026-08-23)

**Decision:** 按 plan/refactor.trae.md 完成 Phase 1–3 全部条目，每小项独立
commit（§编号即 commit 粒度），全部通过 `bun run check` + 批模拟 determinism
签名 byte-identical 门（8 组合 × 全 tick 签名，`tools/probe-det-baseline.sh`）：

- **§1.1** tests/helpers.ts 增补复合 fixture：`setupGodGame` /
  `makeBullet(over)` / `makePowerUp(col,row,type,over)` / `makeCoopWorld()` /
  `makeEmptyStage()` / `makeBoxedArena()` + 头部口径差异表；金丝雀迁移两个逐字节
  相同的本地 setupWorld（base-clear-shot-threat、godai-threat-sticky）。
- **§1.2** 新建 `tools/lib/cli.ts`（arg/flag/parseSeeds/parseStages 唯一 CLI 层，
  断言式报错）；6 个绕过 stage-spec 的 diag 工具改严格解析（§213 静默丢 token 面
  收窄），高频工具去重本地 arg/parseSeeds（ab-diff 家族找回丢失的 count-only 分支）。
- **§1.3** 口径单源：STAGE_COUNT 派生自 STAGES.length；新增
  `EVAL_DIFFICULTY_KEYS`（classic/hard/chaos 三元组唯一来源，relax 不进 sweep）；
  gateCoreCount/splitRoundRobin 下沉 worker-pool（GATE_CORES/SIM_POOL_WORKERS
  双 env 别名保留，行为不变）。
- **§1.4** test-silent 加固：fallback-to-all 输出 ⚠ 提示；HEAVY_TESTS 名单失效
  （重命名残留）时输出 ⚠ 提示。
- **§2.1** 删除 GameRendererCore 的 20 个零值委托（render() 直调 slice）；
  SpriteArtistCore 保留 draw* 门面（slice 跨切路由需要），mixin 幽灵注释清除、
  参数正名。GameRendererCore 468→403 行。
- **§2.2** sprite-key 单源化：SPRITE_URLS 派生 TANK_KEY_MAP/ITEM_KEY_MAP 与
  tank/item 预栅格化清单（新增叶子模块 SpriteKeyMaps 防循环初始化）；顺带修复
  item.fence/boat/frenzy/sacrifice/guard 与 tank.player2 静默走每帧 SVG 慢路径。
- **§2.3** 渲染收敛：dirRotation() 单源替换 5 处方向旋转三元；
  paintPowerUpGlow()/POWERUP_GLOW_FREQ 单源（bake 与 direct 路径像素恒等由构造保证）；
  爆炸 grow/fade 数学共享（explosionSizeAt/AlphaAt）；地形 redraw/rebuild 孪生
  switch 合一 paintTerrainCellArt()。
- **§2.4** computeSceneSig 契约显式化：删除 `_sigTanks` 时序耦合缓存（render 自取
  allTanks，正确性不再依赖"shouldRender 与 render 之间无 sim tick"注释约定）；
  4 个 renderer slice 文件头部加 World→pixel 反向指针注释。
- **§2.5** ControlCenter 五个复制 setter 合一 setToggleState；EmitterConfig 工厂
  外移 src/config/effects-config.ts；fullscreen CSS-fallback 三连提方法；
  MenuScreen 行序 off+N 算术改为 ROW_ORDER+rowIndexFor 单源；TileMap.dirty 消费
  协议（presentation 只读不变量的登记例外）双向文档化。
- **§3.1** params.ts 机械三拆：params.interface.ts(2378) / params.tables.ts(967) /
  stage-adapt.ts(299)，params.ts 变 22 行 re-export 门面（消费方 import 路径零变动）。
- **§3.5** 几何魔数归位：LANE_OUT_OF_BOUNDS 哨兵、26→GRID、map-center 12→
  MAP_CENTER、中央突破扫描几何命名常量（BREACH_*）。
- **§3.2** _scanResult 顺序地雷斩断：scanAheadImpl 增显式 out 缓冲（绕过 memo、
  不触碰 cache 状态）；aimSurvivesTurnImpl 写专属 `_turnSnapScan` 复用缓冲——
  §80"guard 必须先于 scanAhead 求值"约束结构性消除，调用顺序自由且 byte-identical。
- **§3.3** 缓存失效集中化：invalidatePerTickCaches()/invalidateStageCaches()
  两个注册表方法成为唯一字段清单，reset()/endFrame() 改为调用；**实现偏离说明**：
  计划原文建议字段搬入 navCaches/scanCaches 分组对象，实际采用分组失效方法——
  达成同一目标（"新增缓存必须动哪里"从 3 处隐式变 1 处显式）而避免 ~202 个
  self.x 引用点的机械搬移风险；失效集合逐一保持与原两处完全一致。
- **§3.4** think.ts 20 个候选闭包 → candidates/*.ts 具名导出函数
  （eval<Id>(self, ctx): boolean，逐字移动），共享 helper 下沉 candidates/shared.ts
  （打断环）；think.ts 3318→635 行 = 薄注册表 + 壳。权重值仍单源
  DecisionCore.ACTION_WEIGHTS，注册表顺序 ↔ 权重一致性由 tests/decision-core.test.ts
  护栏锁定（计划设想的第三处重复记账实为测试断言，非人工负担）。
- **§3.6 明确不动**：self 整体切片 / 微助手去重（manhattan×81、terrain 字符串比较）
  / selectTargetUncached 714 行分解 —— 维持原判（热路径纪律 + 成本收益比）。

**Rationale:**
- 目标对齐 AGENTS §0：降低未来 agent 开发与维护摩擦（规则消歧、样板消除、
  单一真相源、隐性耦合显式化、可导航性），零玩家可感知行为变化（Gate 1 由
  determinism 门 + 全量测试保证），不引入新依赖/新模式（Gate 3）。
- §3.3/§3.4 的两处实现选择（失效方法 vs 分组对象；权重单源维持现状 vs 重构派生）
  均按 Three Gates 取更简单方案，偏离点已如上记录。

**Implications:** 未来 agent 触碰 God AI 的标准护栏流程 =
`bun run check` + `tools/probe-det-baseline.sh` 前后 sha256 比对；新 diag 工具必须
import tools/lib/cli；新测试 fixture 优先 tests/helpers.ts（读口径差异表再动手）；
新增渲染通道必须同步 computeSceneSig（grep 锚点已布好）。

## 264. selectTargetUncached 分解落地（§263 遗留 #3） (STATUS: 已实施, 2026-08-23)

**Decision:** StrategyPlanner 的 714 行 `selectTargetUncached` 分解为
159 行编排器 + 10 个具名私有函数（4 个独立 commit，每批过 check +
determinism byte-identical）：applyTargetBlacklist / emergencyBaseDefenseGate /
noBaseNearestTarget / freezeChaseTarget / chokepointHoldGate /
anchorApproachHoldGate / huntModeTarget / normalSelectionTarget /
defenseThreatTarget / guardAnchorHoldGate。

**Rationale:**
- 门级段落统一 `Cell | null` 返回形状，早退语义由调用点 `if (x) return x`
  表达——决策级联第一次在阅读层完全可见（威胁评估 → 撤退门 → 追击 → 驻守 → 选择器）。
- defenseThreatTarget→guardAnchorHoldGate 的 anyClearShot/anyBreacher 传递采用
  模块级 `_defenseScanFlags` 复用缓冲（§14.2 惯例，同 `_scanResult` 先例），
  不引入每 tick 对象分配。
- 纯派生量（defenseRow/anchorModeOn）在被提取的函数内重算而非传参——参数表更小，
  值恒等（纯 params/常量推导），零行为差异。
- intentWrite/§170 commit/_lastSelectTargetId 等副作用原位保留在选择器函数体内，
  不做"评分与应用分离"（M1 定理约束仍适用：二次求值会破坏 parity）。

**Implications:** 后续对目标选择的调参/调试可按函数名直接定位；
新增"撤退门/驻守门"类机制照 `*Gate` 形状接入编排器即可。

## 265. determinism 语料 v2（8→21 组合） (STATUS: 已实施, 2026-08-23, 遗留 #12)

**Decision:** `tools/probe-det-baseline.sh` 语料从 8 组合（33k 签名行/~35s）扩充到
21 组合（109k 行/~100s）。新增 13 行全部锚定历史事故关并注明出处：
Lattice idx11（§152-W1 seed 934391936 + classic 对照臂）、Frozen Field idx18
seed37、Eagle Nest idx30 seeds14/71、Diamond idx32 seed83、Battlement idx33
seed2（§178）、Star Fort idx31 seed23（chokepoint A/B r3）、Twin Towers idx8、
Steel Web idx13（central-breach 负例）、Ice Palace idx26（ice glide）、
Brick Maze idx27（brick-dense，classic+chaos 双臂）。legacy 8 行原样保留。

**Rationale:**
- 新行选择标准 = "回归实际发生过的地方"：每行对应 DECISIONS/代码注释中记载的
  具体事故（种子级），而非均匀撒网——同预算下召回率更高。
- 验证三重：①门自洽（连续两跑 byte-identical）；②legacy 8 行用 git 中 v1 脚本
  重跑逐行比对 = 零漂移（证明 §264 全程干净）；③修正了 v1 的"S 编号"误导性标注
  （per-seed-diff 实际消费 raw STAGES index），v2 标注 `idx=N` 与代码行为一致。
- 已知盲区记录在脚本头：单机 only（无 spectateDual/coop 接线），双玩家路径仍由
  godai-* gates 覆盖。
- 运行时成本 ~35s→~100s：接受——该门按"每批一次"运行，不进 per-edit 循环。

**Implications:** 旧基线 sha（1764257587…）作废；当前基线 =
`b81e240a8c2980bbf805215319be5aa2f483a312235bd35d758a6e522870ec32`
（tmp/det-batch.baseline.txt）。后续 AI 触碰的标准流程不变：改前改后各跑一次比对。

## 266. manhattan 单源化落地（遗留 #2） (STATUS: 已实施, 2026-08-23)

**Decision:** 权威 `manhattan(ax, ay, bx, by)` 落 `src/utils/helpers.ts`；
`ai/perception.ts` 改 re-export（TacticalIntelligence 导入路径零变动）；
god 层 ~100 处内联 `Math.abs(dx) + Math.abs(dy)` 拼写收编为调用
（含 `Math.round(manhattan(...)/CELL)` 包裹式、运动预测偏移点距、三元臂）；
ThreatBudget 的模块级本地复制箭头函数删除；BaseLaneSentry 撞名局部变量
`manhattan` 更名 `sentryDist`。**有意保留原样**的三类：①delta 形站点且
|dx|/|dy| 被下游复用（Hunt 轴向测试、DefenseIntercept 方向选择）——转换会
丢弃现成的寄存器值；②非点距和（Navigator glideSpeed=|vx|+|vy|）；③
A* 内循环启发式保持直写（pathfind pfPush 参数位，读性优先）。

**Rationale:**
- §263/§3.6 原"维持原判"的解除条件是"determinism 门 + perf 基准同时通过"。
  perf 实测：21 组合 determinism 语料，HEAD worktree vs 统一后各 n=5，
  user CPU B=22.66±0.22s vs A=22.92±0.50s，Δ+1.2%，不显著（同构建环境噪声
  即达 ±4%）；V8 对单态微函数的内联符合预期。前一会话声称的 A/B 产物未落盘，
  本次以 n=5×2 worktree 对照法重测补证（stash 法有吞工作前科，弃用）。
- determinism 门：全量 109,516 签名行 byte-identical（基线 b81e240a… 未变）。

**Implications:** 新增距离计算一律 import `manhattan`；热路径写裸 abs 和仅限
上述两类豁免形状（下游复用 delta / 非点距），否则视为欠账。helpers.ts 注释
即 grep 锚点（遗留 #2 / DECISIONS §266）。

## 267. 遗留 #1 self-hub 处置：结构性护栏替代整体切片 (STATUS: 已实施, 2026-08-23)

**Decision:** `self: GodAIInput` 整体切片**维持不做**（§263/§3.6 原判），改以
结构性护栏收口其残余风险——新增 `tests/godai-hub-fields.test.ts`：
文本解析 GodAIInput 类体，强制「每个实例字段必须在
constructor / invalidatePerTickCaches() / invalidateStageCaches() / reset()
之一被赋值/变异，或在带分类与理由的 ALLOWLIST 中」。豁免分五类并逐条给指针：
A 诊断计数（注释自证"never feeds back"）×8；B §14.2 复用缓冲 ×6；
C 键控缓存伴生（payload 惰性，守护 flag 本身受注册表覆盖）×32；
D 写一次接线/常量（含外部写入点 isGuardAI←SimulationEnemies §187）×5；
E 单调节拍器 _thinkCounter ×1。测试双向查新鲜度（新字段缺登记 → 红；
ALLOWLIST 残留改名死键 → 红），另锁两注册表在 endFrame/reset 的接线存在。
金丝雀注入验证过牙齿（_hubGuardCanary → 精确报错）。

**Rationale:**
- 切片反对证据经本轮复核仍然成立且更精确：god 层 `self.*` 引用实测
  205 个唯一成员 / ~1,670 处、33 文件（params×292 / world×121 / 方法调用
  跨切严重）——切片必然漏切核心面，收益不抵 ~1,700 处机械搬移风险；
- 审计所称"15 文件双向依赖"实为**纯类型级**（god 层 32 处 import 全部
  type-only，运行时零环）——导航摩擦已被 §3.4 候选具名化大幅消化，
  剩余真实风险是「新增字段忘写 reset」这一类，恰好可被 CI 结构性封堵；
- 普查即发现 51 字段游离于生命周期清单外，逐一定性后均为既有文档记载的
  豁免类——把口头惯例变成机器检查的契约，正是本计划"隐性耦合显式化"
  的同款手法（对齐 §2.4/§3.2/§3.3 先例）。

**Implications:** 新增 GodAIInput 字段的标准动作 = 在对应生命周期方法加
一行清场，或 ALLOWLIST 加一条带理由的豁免；两者都会被此测试强制面对。
解析器以 sanity floor 自保（字段数 <160 即红），格式化漂移不会静默放水。

## 268. 第三轮重构 Phase 1 落地汇总（plan/refactor.trae.md §1） (STATUS: 已实施, 2026-08-24)

**Decision:** §1.1–§1.4 全部落地，共 6 个 commit（41094b7 docs / 84a9b7c 死指针 /
bb96340+2a18531+d116cb0+f5e4ed4 死代码 / ccd85ef 吞错）。条目清单与偏离：

- **1.1 活文档**：README / architecture / features / AGENTS 四份按表逐行修订
  （端口、测试规模与 check 口径、快照 30s/20/100、15 道具、39 SVG、IndexedDB、
  回放已建成、God AI/躺赢段落、config 表去 tanks.ts、types 四拆、仓库地图补全）；
  presentation-audit.md 加历史基线横幅。
- **1.2 死指针**：experimental.ts ×22 处改"已退役（文件已删，git 史可考）"；
  pickSentryStandImpl / line 721 / see aggressive branch / single source: think.ts:483 /
  SimulationCore / GameCore / gate-core / god-ai-gate.test 等全部如实化；
  isBaseUnderThreat 截断文档补全为 6 规则；pickClassicDir 文档归位；Navigator 重复注释去重。
- **1.3 死代码**：randInt（Math.random 脚枪）/ALIGN/SPAWN_PROTECTION_MS/turnCW/turnCCW/
  NEUTRAL_BIAS/REPLAY_THUMBNAIL_*/DEFAULT_SNAPSHOT_KEY/bulletPathSteelBlocked 包装/
  chokepointHoldCheckTicks/GameplayRules 三死旋钮/i18n 死键 ×13×2 全部删除。
  **部分否决**：`byId` payload——审计称"全仓零消费"不成立，tools/sim/simulation-runner.ts:866
  取证消费 e.byId 做击杀者归因（§252 先例：前提不成立即记录跳过）；仅删零 push 点的
  `'self'` union 分支。**幽灵参数 dodgeCounterFireRangeCells**：注释改指 Dodge.ts 硬编码
  5*CELL 实况（参数化留给 3.11 决策）。
- **1.4 吞错**：SnapshotManager×2 + ReplayManager×2 的 `.catch(() => {})` → console.warn；
  AudioManager 构造失败补 warn；captureThumbnail 按计划保持不动。

**Rationale:** 文档是 agent 第一入口，错误声明是最大摩擦源（§0）；死导出中 randInt
内嵌 Math.random 是确定性契约的脚枪，优先级最高；其余删除项均经执行时 grep 复验。

**Implications:** 度量基线更新——活文档错误声明 ~40→0（四份）；src 内不存在文件的
注释指针→0；死导出/死旋钮/死 i18n 键清零。`byId` 保留后 types.ts 注释已标注消费方，
未来再审计不会再误判。

## 269. 第三轮重构 Phase 2 落地汇总（plan/refactor.trae.md §2） (STATUS: 已实施, 2026-08-24)

**Decision:** §2.1–§2.8 全部落地，8 个 commit。条目清单与偏离：

- **2.1** helpers.makeTank 补齐（语义冻结为五份字节级副本的默认值），5 文件本地
  副本删除改导入，调用点零改动；顺带清理由此空置的 TANK/Tank 导入。
- **2.2** 差异表补录 emptyArena/addEnemy(1-based)/placeEnemy 三方言与 positionPlayer
  三方言（~18 本地副本）。**可选增量否决**：~7 处本地 placeEnemy 与 helpers 版参数序
  相反（第 3 参 dir vs kind），非 byte-identical——盲收编会静默翻转 4 参调用点语义，
  陷阱记入差异表（§260 教训的直接应用）。
- **2.3** batch-sim/sweep-winrate 的 parseSeeds → parseSeedSpec 正名（单颗种子方言）；
  方言保留不并（§213）。tests/calibration 同步。
- **2.4** lib/cli 新增 parseParamSets（--set 共享收集器）；parseSeedSpec 提升进 lib/cli
  （batch-sim 改 re-export）。freeze-thrash-audit/decision-probe/per-seed-diff/
  curriculum/regression-check 五处手搓 argv 迁移。**行为修正两处**（commit 注明）：
  freeze-thrash 与 regression-check 的 --stages 从静默过滤越界 token 变为抛错
  （§213 类坑消除，烟测 StageSpecError 生效）；per-seed-diff 的 --max-ticks 非法值
  从静默忽略变抛错。cli.ts 头部登记 perf/* 等号语法并存。
- **2.5** 8 个零引用工具归档（replay/archive 新建 + diag/archive 二批），相对导入层级
  修正，两侧 archive README 记甄别标准与复活条件。执行前逐个复验零引用成立。
- **2.6** eval-suite main() 413 行拆 runCompare/runCalibrate/runScorecard + SuiteContext；
  头部 v6→v7 更正；eval-refs 加载器下沉 tools/lib/eval-refs.ts；取证语料默认路径 ×4
  收敛为 DEFAULT_FORENSICS_CORPUS。
- **2.7** sweep-winrate 报告生成器（HTML+内嵌 JS ~460 行）逐字搬至 report-html.ts；
  ranAt/seedsCount/modeSuffix 参数化（ReportMeta），报告模块零 argv 零 IO。
- **2.8** 四个测试文件改名归一 godai-* 前缀（含 guard-god-ai→godai-guard），
  describe 名/交叉引用同步；子目录化维持不做。

**Rationale:** tests/tools 是 agent 开发摩擦主战场（§0）；同名反义函数、静默吞 token、
字节级 fixture 副本都是"agent 改一处坏三处"的放大器。方言本身承载行为语义，处置原则
是**显式化而非消灭**（差异表/DIALECT NOTE/参数化），与 §260 一脉相承。

**Implications:** 度量基线更新——makeTank 副本 5→1；parseSeeds 3 份 2 义→具名双义；
god-AI 测试命名分裂 4→0；零引用工具 8 个 ~1500 行归档；lib/cli 成为唯一 argv 层
（perf/* 等号语法除外，已登记）。

## 270. 第三轮重构 Phase 3 落地汇总（plan/refactor.trae.md §3） (STATUS: 已实施, 2026-08-24)

**Decision:** §3.1–3.9、§3.11、§3.12、§3.14 全部落地；**§3.13 整批关闭**。每项独立
commit，全部通过 determinism 门（tools/probe-det-baseline.sh，21 组合 ×109,516
签名行，sha256 与基线 b81e240a… 逐字节一致）+ 全量 127 文件测试。

条目清单：
- **3.1** 弹道对齐谓词单源化：utils/helpers 新增 bulletLaneDist（行进方向语义）/
  bulletInFrontDist（静态朝向半平面，T2a aimError 门专用），替换内联链 ×10。
  **审计漂移**：TA×8 实为 ×7——第 8 处 hasBulletInAimLane 是"子弹逆 aimDir"反向
  语义变体，不可合并。落点选 utils/helpers 而非 ai/god（沿用 §266 manhattan 先例，
  避免 perception→god 反向依赖）。中途 det 门红一次：FireControl 站点极性反相
  （113377≠109516），worktree 逐 tick 对照定位 tick124 后修正——门有效性的实证。
- **3.2** 基地环几何单源化：ThreatBudget 导出 isBaseRingCell + countRingBrickCells；
  FireControl/PathCarve/candidates-shared/StrategyPlanner×2/SmartThreatModel 的
  手展开环遍历全部改走 RING_CELLS/共享谓词。
- **3.3** 自射基地守卫簇 ×4 → candidates/shared.selfFireBaseGuardBlocks；
  ActionCandidates 的 laneCorridorBlocked 内联复实现改真调用（1.2 注释谎言兑现）。
- **3.4** 区域卡死跟踪器 ×4 → god/stuck-track.ts（StuckTrack + updateStuckTrack），
  GodAIInput 四组标量四件套收敛为 4 个 tracker 字段。**行为边界发现**：Hunt 的击杀
  基线与 Engage 的 _campKillsAtStart 存在历史跨候选耦合——拆分使 8/21 组合 det 红，
  插桩定位到 f764 逃逸时机分歧后按纪律保留该耦合（Hunt 保持内联形态并注明）。
- **3.5** pickup commit 尾巴 ×6 → shared.commitPowerupTail（Aggro 变体保留）。
- **3.6** 钢穿深字面量 ≥3 ×7 → STEEL_PIERCE_PLAYER_LEVEL import（审计称 8 处，
  实测第 8 处已随 SuicideReturn 32→TANK 在 3.1 完成）。
- **3.7** evalHunt 597→127 行编排器 + 8 具名函数（副作用原位保留，M1 parity）。
- **3.8** dodgeDirectionImpl 330→112 行：4 策略函数 + 编排器，策略通信走 §14.2
  模块级 _dodgeOut 缓冲；pinned 兜底与平局裁决留编排器（回退契约可见）。
- **3.9** isBaseUnderThreat 95 行迁 ThreatAssessor.isBaseUnderThreatImpl，hub 一行
  委托；缓存字段不动。
- **3.10** coop 调整块 ×7 → 文件内 coopAdjustDist。**第二部分否决**：min-manhattan
  扫描 ×8 各异（tankCell vs 像素中心、过滤谓词、targetValue 加权、方向约束），
  非 byte-identical 不合并（§260 先例）；findCloseEnemyImpl/countAlignedEnemiesImpl
  近孪生同判——argmin vs count 合并需 mode flag，比重复更差（MANIFEST §10）。
- **3.11** 命名常量批：BULLET_ALIGN_NEXT_CELL/HIT_HALF_SPAN/BASE_CENTER_X|Y_PX/
  COUNTER_FIRE_RANGE_CELLS(=5，维持硬编码设计)/SCAN_AABB_HALF_SPAN(=TANK+1)；
  PickupLow <=5 改读 pickupPriorityMinEnemyDist（默认下 byte-identical，耦合显式化）。
- **3.12** ACTION_WEIGHTS 标注权威契约地位 + think.ts 指针 + GodAIInput 头部
  坐标习惯速查（审计所称五处逐字重复经查已在前轮局部化）。
- **3.13 关闭**：按计划判断标准（"3.7/3.8 实际成本与顺利度"）——两项虽最终过门，
  但拼接手术各返工多次（Hunt 结构行误伤两次、dodge 五轮），对 evaluateUnifiedCandidates
  等 ~1200 行同类手术预期成本更高而收益同为可导航性；scanAheadImpl 另有 memo/out
  缓冲契约风险（§263 已固化）。整批关闭不算欠账。
- **3.14** hub 单调用方纯委托包装删除 ×11，调用点 Impl 直连；约定写入 hub 头部。

**Rationale:** 单源化的价值在"语义改动只改一处"，但前提是合并对象 byte-identical
或差异被显式参数化——本轮两处否决（3.10 扫描合并、3.4 Hunt 解耦尝试）与一次回退
均源于此纪律。

**Implications:** god 层 >200 行函数 7→2（evalUnifiedCandidates/scanAheadImpl 按
3.13 关闭保留）；弹道谓词/基地环几何/守卫簇/卡死跟踪器/pickup 尾巴副本清零；
hub 包装 -11。determinism 语料 v2 在本轮三次拦截行为漂移（1 次 3.1 极性、1 次
3.4 耦合、若干次拼接损坏由 tsc 拦截），验证 §266 门的价值。

## 271. 第三轮重构 Phase 4 落地汇总（plan/refactor.trae.md §4） (STATUS: 已实施, 2026-08-24)

**Decision:** §4.1/§4.2/§4.3 落地；**§4.4 按三道门判断后不做**。

- **4.1 One-Author 残余写路由**：Simulation 新增 `applyTakeover(coop)` /
  `clearRewindPending()` / `refundRewind()` 公共入口；Game.takeOverFromSpectate
  （两处 w.coop 直写）、GameReplay.takeOverFromReplay、GameLoop 时光宝盒
  rewindPending 消费与 rewindStock++ 退款全部改走入口。豁免注记写入
  AGENTS §2.1 与 MANIFEST §3：`world.state = …` / `world.ui.*` 为转移写非实体
  变更，属既有灰色地带的显式化（本条不动其行为）。度量：Simulation 外
  gameplay 直写 3→0。
- **4.2 parseReplayFile 分解**：107 行一体式拆为 validateEnvelope →
  validateStructure → decodeFrames → buildReplay + reconcileSnapshotStage。
  全仓最差类型逃逸 `env as unknown as FileEnvelope` 移除——envelope 改为逐字段
  显式构造。**兼容性纪律**：第一版补的 source/sim/finalState 强校验改变了错误
  优先级（replay-file 测试 4 红），按"零新增拒绝分支"原则回退为纯构造式守卫，
  错误消息与历史解析器 byte-identical（测试钉死）。
- **4.3 genId() 豁免注记**：World.ts nextId 旁加交叉引用注释（权威记录在
  types.ts 快照 id 字段文档），横幅矛盾消除，行为不动。
- **4.4 DOM 构建长函数（ControlCenter/ReplayController/MenuScreen 构造器）**：
  **关闭不做**。三道门判定：无 DOM 单测网（验收仅 tsc+vite build）、收益仅
  可导航性、且本轮 3.7/3.8 的拼接返工率表明无测试网的机械搬移风险真实存在。
  不做不算欠账（计划原文授权）。

**Rationale:** Phase 4 是选择性收尾；每处行为面都有测试或 determinism 门兜底，
4.2 的类型收紧以测试契约优先于审计理想。

**Implications:** 度量基线收口——Simulation 外 gameplay 直写 0；replay/file.ts
解析器可导航性提升且类型逃逸清零；genId 豁免从口头惯例变为文档注记。
plan/refactor.trae.md 全部条目处置完毕（执行/否决/关闭各有记录）。


---

# Part C. God AI 冻结纪元运维（§272–§276）

> §272（v1 封版冻结）的基线表 / golden / 重启协议已完整归档于
> `docs/god-ai-tuning.progress.md` Part 0，此处不再重复其正文；重启协议另见
> `plan/God-AI-Organization.md` §8。以下为 §273–§276 全文。

## 273. 留档实验资产不删决策（OFF 旋钮 / OFF 候选 / 锁存测试全保留）(STATUS: 已实施, 2026-08-26)

**Decision:** God AI v1 封版（§272）语境下，~40 个 default-OFF 留档旋钮、6 个 OFF 候选、
以及对应的 OFF 特征锁存测试**全部保留，不删除、不移出数组**。

**Rationale:**
- refactor.trae §1.2-2 四条件核查（2026-08-25）：所有 OFF 候选均被 A/B 测试以非零态调用
  或被 standing 工具引用，不满足删除条件；
- 重启成本对称性：删掉省下的复杂度 < 未来重开实验时的重建成本（这些是已付学费的实验基建）；
- L1/L2 双守卫（tests/godai-archived-knobs.test.ts + tools/diag/archived-reach-audit.ts）
  已把「以为它在跑」的风险锁死，保留不再有歧义代价。

**Implications:** 此后任何删除提议须先推翻本条（新 DECISIONS 条目论证四条件已满足或资产
已无重启价值）。数据化注册表：`ARCHIVED_KNOB_GROUPS`（params.interface.ts 底部）+
`CANDIDATE_SURVIVAL`（think.ts）。

## 274. sweep-winrate `--difficulties` 字符迭代 bug 修复 —— 列表参数走 assertive 解析器 (STATUS: 已实施, 2026-08-26)

**Decision:** `tools/lib/cli.ts` 新增 `parseDifficulties(spec, fallback, validKeys)`（逗号切分 +
trim + 空 token 拒绝 + validKeys 域校验，缺省回落 fallback），`sweep-winrate.ts` 的
`--difficulties` 改为经它解析。测试：`tests/cli-parse.test.ts`。

**Rationale:**
- 原实现对 arg 结果**不做 split**，仅默认路径 split——`--difficulties hard` 被
  `for…of` 按字符迭代成 h/a/r/d 四个"难度"，各 2100 局共 8400 局白跑。
- 更危险的是 simulation-runner.ts:554 的 `?? DIFFICULTIES['classic']` 静默回落：
  四个非法键全部跑成 **classic 却以 h/a/r/d 标签出报告**（实测四行结果逐字节相同）。
  这是 §213「35 关扫描静默跑 S1」同一失败类：静默降级。M0 §3.1 协议（坏 token 抛错）
  是既有先例，cli.ts 头注亦明言"Parsers are assertive: a bad token throws"。
- 修复后 hard×35×60 实测 75.9% 胜率，与 Phase III 基线（~73%）吻合，反证此前
  66.4% 系 classic 冒名。

**Implications:** run-forensics / threat-ledger 等已自行 split 的工具不受影响；
后续可逐步迁移到本解析器（本次不顺手重构）。simulation-runner 的静默回落保留
（改动面大），防线前移到 CLI 解析层。

## 275. God AI code-review 批量 bug 修复（冻结路径零行为变更）(STATUS: 已实施, 2026-08-26)

**Decision:** 根据 `docs/god-ai-code-review.md`（未追踪文件，未入 git）审计结果，
实施一批冻结路径（`intentMode=0, candidateMode=0`, 单人）上零行为变更的修复。
剩余行为变更项（§273 留档）不触动，留待下一纪元走三件套。

**已修复（frozen-path 零行为变更）：**

| 编号 | 文件 | 修复 |
|---|---|---|
| M-1 | StrategyPlanner.ts:279 | `intentWrite` 恒等式：`w.player!` → `self.controlledTank(w)!`（coop 写 P2 意图） |
| M-2 | StrategyPlanner.ts:109 | 意图 stall 判断：`p.fireCooldown <= 0`（永假）→ `now - lastFire < nextFireInterval` |
| #5 | ThreatAssessor.ts:376 | 行边界：`> FIELD` → `>= FIELD` + `?.` 安全访问 |
| #7 | FireControl.ts:722,851 | 魔术字面 `< 3` → `< STEEL_PIERCE_PLAYER_LEVEL` |
| #8-9 | FireControl.ts:385-386,704 | 注释与公式/值不匹配，修正 |
| #10 | FireControl.ts:180-232 | 死分支 `t.isPlayer`：World.tanks 已无玩家 |
| #11-12 | ThreatAssessor.ts:21,426 | docstring 不匹配实际常量/公式 |
| #15 | ThreatBudget.ts:27 | msToTicks 浮点含义注释 |
| #16 | ThreatBudget.ts:71 | ring-intact 谓词三分支注释 |
| #17 | Navigator.ts:247 | "byte-identical" → "deterministic" |
| #18 | Navigator.ts:324 | _navCache 无 tileMap.revision 注释 |
| #19 | Navigator.ts:507-508 | `mult > 0` → `mult >= 1`（负倍率穿透砖块 guard） |
| #20-21 | PathCarve.ts:244,614 | doc 不匹配 cols 范围 + memo 别名警告 |
| #22 | Navigator.ts:390 | firecontrol gate 耦合注释 |
| #23 | FiringLane.ts:60 | `self._fire = false` before recordBranch |
| #24 | PickupHigh.ts:20 | 行格约定：`Math.floor(partner.x/CELL)` → `Math.round(partner.x/CELL)` |
| #25 | SuicideReturn.ts:276-279 | `Math.round(pcx/CELL)` → `self.tankCell(p)` |

**未修复（留档，下一纪元走三件套）：**
- Medium-3: `buildCarveCosts` — 更薄 brick 意图 = 行为变更
- Medium-4: `BaseConnectClear` `maxKillsGate=0` 死 config — 行为变更
- #6: ring steel 后退逻辑 — 行为变更
- #13-14: EnemyModel 信息泄漏 — 行为变更
- #15 msToTicks 统一 — 跨层重构
- #26-27: clear-lane OFF 候选 — 行为变更

**Rationale:**
- §272 冻结协议要求冻结路径零行为变更；M-1/M-2 在 `intentMode=0` 路径不触达；
  所有注释/死分支/保守 guard 修复亦不影响 frozen det 签名。
- `freeze:check` 通过：签名 `b81e240a`（109516 行）与 golden 字节一致。
- 新增两个 intent stall 测试（`tests/godai-intent.test.ts`）验证 M-2 onCooldown 逻辑。

**Implications:** 行为变更项（M-3/M-4/#6/#13-14/#15/#26-27）留待下一轮纪元，
届时需走三件套（新 DECISIONS + 60-seed 三难度基线 + 更新 det-golden）。

## 276. code-review 遗留项全清 —— 新纪元三件套执行（§275 遗留 → 全部落地）(STATUS: 已实施, 2026-08-26)

**Decision:** 用户拍板"不要推迟，所有问题都要处理掉"。将 §275 留档的 8 个遗留项
全部修复，并完整执行 §272 新纪元三件套（本条目 + 60-seed×3 难度基线重跑 + golden
替换为 `20784637…`）。

**修复内容：**

| 编号 | 文件 | 修复 | 行为影响 |
|---|---|---|---|
| M-3 | PathCarve.ts buildCarveCosts | 足迹感知：代价写在 2×2 坦克位置（任一足迹格命中环/基柱砖即付价），对齐 buildDigCosts/buildBaseRingCosts 语义；环砖压过基柱 | A* 导航路径变化 |
| M-4 | BaseConnectClear.ts | 实现 baseConnectClearMaxKills 门控（killCount ≥ maxKills 即退出并复位 travel flag），兑现参数文档"开局阶段"语义 | frozen 默认 OFF（baseConnectClearMode=0） |
| #6 | FireControl.ts shouldFireInDirImpl | `if (result.baseSteel) return false`——环钢全等级禁射（<3 打不动浪费冷却；≥3 T6 不许拆自家保护） | 射击决策变化 |
| #13 | EnemyModel.ts updateEnemyModel | 窗口边界重置 `_enemyModelLastHp`，跨窗口 HP 跳变（含死亡）不再虚增 winHits→fireAccuracy | 敌方模型精度估计变化 |
| #14 | EnemyModel.ts staticPriorLevel | 归一化分母 count → MAX_ENEMIES_ALIVE，与三个动态特征口径一致（单个指挥官不再读满权重） | estimatedLevel 变化 |
| #15 | constants.ts + ThreatBudget/CoveragePlanner | msToTicks 统一为共享 msToTicksFloat/msToTicksInt（语义不变，消除双定义漂移风险） | 无行为变化（当前输入整数 ms） |
| #26 | ActionCandidates.ts fireRayBlocked | 扫描全射线任意障碍（brick/steel/base）；clear-lane follow-up 检查跳过正被清除的首砖（skipCol/skipRow 参数） | frozen 默认 OFF（candidateMode=0） |
| #27 | ActionCandidates.ts clear-lane | cadenceTicks 改用 fireCooldown 基础节奏惯用式（msToTicksInt），与 ThreatBudget/think/CoveragePlanner 一致 | frozen 默认 OFF（candidateMode=0） |

**60-seed × 3 难度基线对比（sweep-winrate，seeds 1-60）：**

| 难度 | 修复前 | 修复后 | Δ |
|---|---|---|---|
| classic | 89.52% | 89.52% | ±0 |
| **hard（主）** | 75.90% | 75.29% | −0.62pp（n=2100 SE≈0.94pp，<1 SE 噪声内） |
| chaos | 70.14% | 70.57% | +0.43pp |

逐阶段再分配主要来自 M-3（导航路径改变）：hard S17 −10pp / S34 −6.7pp 与
chaos S14 +11.7pp / S25 +8.3pp 同源。hard 总胜率仍高于 Phase III 基线 ~73%。

**测试更新：**
- battlement-carve-path：M-3 后 A* 正确避开全部环重叠足迹位，pocket→post 找到
  carve-safe 路径（原断言返回 null 已过时）
- navbreak-carve-dig：navBreakStuck=0 回归对照翻转——nb=0 现在 stageclear
  （足迹感知代价让 A* 找到更优路线，正向改进）
- score-gate truth 全量重采（1050 局，三难度并行采集），gate 绿：
  classic 0.870/0.830 · hard 0.766/0.726 · chaos 0.756/0.716

**Rationale:**
- M-3 是索引语义缺陷（构建按子块、消费按坦克左上角），修复后 R5/R6 代价转向
  与 §178 中央破口覆盖真正生效；pathCarveSafeImpl 足迹门控保证安全性不破。
- #6/#13/#14 为正确性修复：浪费冷却、winHits>winShots clamp 到 1.0 的虚假
  "完美"、先验幅值独立于场敌数，均属冻结帧内的实现缺陷。
- 用户明确指令全清（2026-08-26 会话），三件套已完整执行。

**Implications:** det golden 进入 §276 新基线（21 组合 111176 行，
`20784637c67ecd72e0c297d77bf3415b6621120475e3dc0cec6ee63a5caeadaf`）。后续任何
God-AI 行为改动再次触发 freeze:check 红 = 下一纪元。

> **合并编号迁移注（2026-08-26，窗口 0 合并 intent-ai ← origin/main）。** 以下 12 条原编号 §239–§250（intent-ai 线：God-AI 实验 / RL 工程 / BC 三线），与 main 线重构条目撞号；合并时统一重编号为 §277–§288。映射：§239→§277 · §240→§278 · §241→§279 · §242→§280 · §243→§281 · §244→§282 · §245→§283 · §246→§284 · §247→§285 · §248→§286 · §249→§287 · §250→§288。历史文档中的旧编号引用以本表为准。


---

# Part D. 回放与 RL 桥（§279–§280）

> §279 实现计划与评审 → `plan/Replay-TickHash-Chain.md` + `plan/tickhash.review.md`；
> §280 评审全文 → `plan/RL-WASM-Bridge.review.md`，执行计划 → `plan/RL-Bun-Bridge.md`。
> 以下为决策正文（含各评审驱动修订附录）。

## 279. Replay Tick-Hash Chain 实现定案 — 每 100 tick 世界哈希锚点 (STATUS: 完成)

**Decision:** 按 `plan/Replay-TickHash-Chain.md` 实现回放 desync 定位链：录制侧
`InputRecorder` 自 `startNew()` 持有 World 只读引用，`recordFrame()` 内当
`frames.length % REPLAY_HASH_INTERVAL === 0`（`REPLAY_HASH_INTERVAL = 100`，config.ts）
采样 `worldTickHash()`（新文件 `src/replay/tickHash.ts`）；哈希链随 .replay envelope
序列化（`replay.tickHashes` + `replay.hashInterval`，旧文件缺失时兼容）；校验器
`verify-replay.ts` 以同一相位表达式在 sim.tick() 后、终态 break 前比对，失配即
`hashVerified=false` + `firstHashMismatch{checkpoint, recorded, computed, tickWindow}`。
verdict 决策抽纯函数 `decideVerdict(hashVerified, terminalMatch)` 按 §1.4 决策表
（唯一 OK：true×true / null×true）。验收：T1–T7 单测（tests/replay-tickhash.test.ts，
8 pass）+ T3② 语料基线 `tmp/demos-baseline.txt` 既有列逐字节不变、新增 hash=n/a 列。

**Rationale:**
- 哈希世界态而非输入帧：免疫录制时输入采样竞态（recordFrame 在 sim.tick() 之后采样，
  键位恰在两者间变化会录到未被消费的帧——哈希测世界后果，不受此竞态影响）。
- 相位基准统一为「tickCount = 已完成 sim.tick() 次数 = frames.length（push 后）」，
  两侧同一表达式防 off-by-one（tickHash.ts 头注释 + T1 相位钉测试）。
- 实体 id 首见重映射（id/ownerId/powerUp.id）：genId() 是进程级计数器，录制/校验两侧
  绝对 id 必不同；不重映射则每局必失配（P8）。
- 字段选「tick 敏感优先」：±1 帧相位偏移先在计时器（spawnTimer/冷却/shield）上露头，
  字段集过简会把 firstHashMismatch 推迟到更晚窗口（P1）。
- 测试难度选 'hard'（cooldown 开火模型）：classic 的 bulletCap 会让 t=250 的 fire 位
  翻转被弹道中的旧弹挡住而无效果（probe 实测 playerBullets@250=1），T2a 会假失败。
- 基线 diff 实测：唯一差异是基线文件自身的 UTF-8 BOM（PowerShell 重定向产物）+ 新增
  hash 列；裁决/分数/动作分布全部逐字节一致。

**Implications:** 新录制 .replay 文件均携带哈希链（5.9KB 级单文件增量可忽略）；旧文件
校验走「null×终态」回退，逐字节输出不变。后继：coop（frames2）链、阶段切换跨链校验、
`--explain-hash` 差异字段级反查工具可在此基础上扩展。

## 280. 否决 RL-WASM-Bridge（B3），改走 A'（bun 持久进程桥）(STATUS: 已决议)

**Decision:** 不执行 `plan/RL-WASM-Bridge.md` 的 WASM-into-Python 方案（B3）。RL 训练环境
改用 **A'：复用现有 `tools/sim/sim-worker.ts` 持久 bun worker 池，Python↔bun 走 stdio JSON
一步一 step 接口**，零引擎改动，确定性直接继承现有测试。WASM 仅在 A' 实测成为瓶颈时再议。

**Rationale:**
- **subprocess 被做成稻草人，WASM 解决非瓶颈。** 仓库已有持久 bun 进程池（God-AI 全扫
  8,400 局 4 分钟 = ~15 万 ticks/s）。RL 采样瓶颈是 torch 前向（52K–999K 参数），非 env；
  真实引擎单核 ~2 万 ticks/s、obs 编码 ~30μs，env 速度远非约束，「≥50 sps」不构成选型依据。
- **移植范围低估 ~10×。** 移植真实引擎需 `src/ai`(20,785) + `src/game`(7,734) +
  `src/nn`(1,026) + `src/config`(2,491) ≈ **32K 行**，计划只列 2 个文件。且 obs 编码器
  （`obs-encoder.ts:39` → `god/ThreatBudget` → `config/combat`）把 god AI 派生链拉进移植范围；
  `SimulationCore.ts:9` 的 `new (...args: any[]) => T` 构造类型 + 6 层 mixin 链 **AS 不支持**
  → 是跨语言移植非 "minor adaptations"，改完不再是"同一份源码"，零漂移优势自毁，每次引擎
  改动须重跑 WASM 对比。
- **Emscripten 回退自相矛盾。** "100% 兼容" 的 B 路径 = WASM 内跑 QuickJS，比 V8 慢 5-10×，
  击穿 50 sps；而 60% 失败概率（自估）的 AS 主路径失败后总工期翻倍，timeline 无此预算。
- **env 契约 + 零拷贝坑。** `env_is_done` 漏 stageclear / outcome 四态 / timeout；reward 增量
  需 prev 跟踪未设计；`:113` 直接返回 `np.frombuffer().reshape` 致 PPO rollout 跨数千步持有
  时被 WASM 内存覆盖 → 训练数据静默污染。
- **整个 RL 栈从未跑过真实游戏。** `rl_env.py` 的 `_start_simulation`/`_execute_step` 返回
  `np.random` 假数据（:183-227），计划时间表建立在虚数据上。

**Implications:** B3 标记为否决（保留文件作反面案例，不删除）。A' 落地须先补齐真实 rl_env 契约
（outcome 四态 + prev 值跟踪 + timeout），并**同步修模型架构**（增大容量 / 扩大感受野）——
后者是 `docs/nn.progress.md` §1 指明的真正瓶颈，与选型无关。NN 训练方向由 BC 转 RL 本身正确
（val_loss 是差的游戏性能代理，分布偏移 + 7×7 感受野是天花板）。
> 评审全文 → plan/RL-WASM-Bridge.review.md
> 执行计划 → plan/RL-Bun-Bridge.md（A'：bun 持久进程桥，~2 天桥接 + 确定性验证；v2 已落实评审全部 P0/P1/P2 修订）
> 全文 → plan/Replay-TickHash-Chain.md + plan/tickhash.review.md（4 轮评审闭环）

**附录（2026-08-20 评审驱动修订）：动作空间取舍 + 权重定稿**
- **砍掉 rewind（item=3）→ 动作空间 30（move5×fire2×item3）。** 依据：`SimulationPlayer.ts:172`
  仅置 `w.rewindPending=true`，恢复逻辑在浏览器 `Game.ts`+`RecoveryController`，headless 桥里 rewind
  是死动作（吞 rewindStock 零效果）。砍掉后与 `rl_model.py:25 ITEM_DIM=3` + `train_rl.py:85` 解码
  `move+fire*5+item*10`（最大 29）**完全对齐，零模型改动**（原 40 动作 + ITEM_DIM=4 的模型改动需求自动消解）。
- **RewardShaper 忠实移植 v7×10 权重（修正 rl_env.py 原符号 bug）：** KILL 4.77、DEATH 2.56、
  BASE_WALL 1.70、GROWTH 0.60、FIRST_KILL 0.18、CLEAR 100、BASE_DESTROYED -100、LIVES_EXHAUSTED -50；
  原 rl_env `lives_delta<0 → *REWARD_DEATH(负)` 得正奖励（死命反而得正奖励），TS 版修正为
  **正权重配负 delta**（死命/掉墙 → 负奖励）。BASE_PRESSURE(-0.44) 在原 `_compute_reward` 未使用，省略；
  TIMEOUT -1.0 为计划新增（v7 未定义）。权重首轮训练后定稿（沿用 B3 §8 约定）。
- **P0 阻断 bug 全部修：** `world.gameOver`→`world.state==='gameover'`；`world.totalKills`→`world.killCount`；
  `step()` 补 `input.endFrame()`；`done` 返回同构消息非 `null`；`reset` 回传初始观测；`train_rl.py`
  `reset()` 调用点（evaluate:69 / main:185）补参，`rl_ppo.py` 内部 `self.reset()` 不调 env 无需改。
  P3 确定性验收改为桥自洽双跑（同种子+同脚本→逐位一致），跨引擎 A/B 待 `simulation-runner` 支持脚本输入后再议。
- **修订全文 → plan/RL-Bun-Bridge.md（含 §10 评审驱动修订清单，无遗留阻断项）。**

**附录（2026-08-20 二次审查 v3 修订）：RL 网络选型算力账校准**
- **BC 基准修正（model.py 头部注释过时）**：实测 BC `model.py` = conv_ch(32,48,64) → **52.0K 参数 / 30.8M MAdds**（7×7 RF 不变）；原计划误用 112K/65M（错引头部注释 + 误用 (32,64,128) 通道）。
- **吞吐基准一致性修正（审查最重要发现）**：原 §2.3 两行差 30–100× MAC/s 基准（教师行隐含 150–300M、学生行隐含 9–28G，后者是 WASM-SIMD 量级）。统一朴素 TS ~150–300M MAC/s（佐证：当前 BC 30.8M 已在浏览器每 10 tick 运行，反推 infer.ts ≳200M）。修正后：**纯 TS 下无任何模型满足 K=1**；无注意力学生 37M 桌面 K=10 仅边缘可行（123–247ms，需 Worker 卸载）。
- **甜点降级**：CoordConv-ConvMixer-Lite **无注意力 37M/69K** 为首选；+注意力 95.5M（注意力 58.5M，原 92M 漏计 ~3.5M）降为兜底档；补空洞 3×3(dilation=13, +0.39M) 中间档。
- **K=1 否决**：纯 TS 单步 ≥123ms ≫ 16.6ms 且对 hold 语义动作无游戏价值 → 锁 K=10。
- **Worker 部署为硬要求**：`think()` 同步阻塞（policy-input.ts:146）→ 推理移 Web Worker + 双缓冲，否则掉帧。
- **O3 表述修正**：学生 69K > BC 52K，故「学生小于 BC」不成立；O3 优势是 vs 教师 950K（1/13）。
- **蒸馏假设标注**：教师高胜率 + 90% 保留率均为**假设非实证**（BC 教训 val_loss↓8.4% 胜率不变 → 分布拟合≠轨迹胜率）；改推 **DAgger 在线蒸馏**直击分布迁移根因；保留率须 P1–P3 实证。
- **编码器成本口径**：ObsEncoder.encode 每决策 tick 运行（逐敌 killAssessment/enemyDeadline，God-AI 级），真实 ~数万 ops 占前向 <1%（审查称「同量级」夸大），但所有模型共有、须计入绝对能耗与 K=1 预算，建议稀疏更新。
- **全文 v3 → plan/RL-Net-Selection.md；RL-Bun-Bridge.md §6.1 同步修正。**

**附录（2026-08-20 三次审查 v4 修订）：论证闭合——删除虚构佐证 + 绝对锚 + 下探/量化补位**
- **删除 §2.3 虚构佐证（审查 P0-1，已亲验）**：「BC 已在浏览器每 10 tick 运行反推 infer.ts ≳200M MAC/s」为假——`NNInput`/`infer` 仅 `src/nn/` 内部引用（weights/policy-input/obs-encoder），浏览器零接线；`policy-input.ts:22-23` 引 `fs`/`path` 不可打包；BC 前向只跑 Bun 无头（JSC，无墙钟）。全部可行性结论改为悬于**未实测常数 0.15–2 G MAC/s**，P0 基准前架构仅「膝点候选」未锁定。
- **验收改绝对锚（审查 P0-2）**：原「学生≥教师 90%」相对阈值与项目硬约束「hard>90%」联立 ⇒ 教师≥100% 不可能。改**学生 hard 胜率绝对锚（起评≥85%/定稿≥90%）**，保留率降诊断指标。
- **补下探 + 量化两维（审查 P1-3/P1-4）**：P5 ablation 增 **h=48 下探档（~46K/25M）** 探 O3 下界；增 **int8 量化**（46–69K 参数→46–69KB 下载，4× 杠杆，权重 int8+累加 f32 确定性，与 byte-for-byte 不冲突，须以 int8 为 canonical 参考并做保留率回归）。
- **God-AI 先行教师 P1.5（审查 P0-2 去风险）**：仓库现成 God-AI（hard ~0.73–0.77，DECISIONS 门禁 baseline / AGENTS §6.3b Phase III）`GodAIInput` 即 `InputLike`，确定性标号与学生学习空间一致（30 离散）→ RL 教师落地前即可端到端验证「学生架构+DAgger+保留率测量」整条管线。
- **措辞修正（审查 P1-1 / P2）**：Pareto 单点→**膝点候选/ε-constraint**（O1 未实测前沿是集合）；§6.2 注意力「676 tokens 极便宜」删改（仅参数便宜，MAdds 不便宜）；**attention MAdds 补投影项 → 含注意力总计 ~107M**（原 95.5M 漏计 QKV/O 投影 11.1M，已修订）；**学生 RF 33→35×35**（stem 3 + 8×4）；教师参数全文档统一 950K（实测 949,835）；**BN 措辞软化**（折叠数学等价但改浮点序，真阻断工程理由是 603M MAdds）；**≤120ms 与 O2 自相矛盾**→改锚定「单核占用≤30%≈推理≤50ms」或实测 jank；**K=10↔20 列为 O2 旋钮**（默认 K=10，移动端可上探 K=20）。
- **全文 v4 → plan/RL-Net-Selection.md。**

**附录（2026-08-20 四审 v5 修订）：候选集补全——下探档 + 直接RL 对照 + 移动端范围**
- **补最低档 h=32/d=4 + 空洞 depthwise（审查 P1-1，已亲验算术）**：实算 **~20K 参数 / 7.85M MAdds / RF=45×45**（stem 14→32 + 4×dw5×5 + 空洞 3×3 dil=13 groups=32）。若蒸馏保留率兑现，它是严格更优 O3 点；「69K 甜点」降级为「已试甜点、非消融甜点」，结论待 P5 容量消融闭合。
- **空洞补齐层必须显式 depthwise（审查 P1-4 算术硬伤，已亲验）**：空洞 3×3 64ch **depthwise(groups=64) = 0.39M MAdds**，但**全卷积 64→64 = 24.9M MAdds**——若不显式标 `groups=64`，「+0.39M」表述误导实现者做出超预算全卷积。v5 在 §4.3 架构图、§4.3 档位、§6.2(c) 三处均显式标注 depthwise。
- **补「小模型直接 RL」对照臂（审查 P1-2）**：BC 0% 是 7×7 RF 问题，非「小模型欠拟合」证据。69K 全 RF 模型直接 PPO 训若达同等 O1，则同参数同能耗、零保留率风险、严格 Pareto 点 → P5 设对照臂；若成立则蒸馏从「主线路」降级为「可选增强」。蒸馏「必需性」现为待排除假设。
- **移动端范围声明（审查 P1-3）**：「web 端」默认＝桌面 web。纯 TS 路径下 37M(h=64) 仅桌面边缘（且 depthwise 真实偏低端），**移动端超预算、属 out-of-scope**，除非用最低档 h=32+空洞(~8M) + int8 + K=20 且 P0 基准门控。若项目要求移动端达标须显式立项。
- **depthwise 5×5 算术强度警示（审查 P0-3）**：学生骨干是 depthwise 串联（memory-bound），真实 MAC/s 可能**低于**标准 3×3 密集卷积基准 → 桌面边缘比标称更薄，低端机取 50–150M 而非 150M 下限；P0 基准必须用**真实 depthwise 学生权重**跑，不能 BC 外推。
- **绝对 O1 门槛锚定 M1 硬 ≥60% 先例（审查结构化）**：§4.6 主验收改为**起评 ≥60%（M1 硬门先例）/ 阶段 ≥85% / 定稿 ≥90%（项目硬约束）**，拒绝用「相对教师 90%」虚高过关；「胜率更佳」获绝对含义。
- **h=48 内部口径修正**：GAP+FC 原误复用 h=64 的 88→128（应为 72→128=9,344），v5 修正后 ~46K/~25M（量级不变）。
- **全文 v5 → plan/RL-Net-Selection.md。**
- **v5.1（P0 实测，2026-08-20）**：`tools/bench-nn-infer.ts` 跑现有 BC 权重（52K/30.8M）`NNModel.forward`，**Bun 1.3.14 (JSC) 桌面两次可复现 = 27.3ms@1.13G / 27.7ms@1.11G → 实测常数 ≈1.1 G MAC/s**。结论升级：① §2.3 删「假设基准」改实测+depthwise 折扣(~0.6×≈0.66G)；② 学生 h=64 @~56ms **桌面舒适**（3× 余量）由「边缘」升级「舒适」；③ K=1 否决 / 教师不部署 / 移动端需下探档 维持；④ 文档「未实测」标注降级为「BC 已实测 / 学生 dw 待训后实测」。学生 depthwise 精确延迟待 P0 学生权重产出后 `bench-nn-infer.ts` 实测定稿（届时 infer.ts 需先扩 depthwise/5×5/残差支持）。


---

# Part E. NN 语料与意图纪元（§289–§290）

> §289 的基线数据（pinned run）同时入档 `docs/god-ai-tuning.progress.md` Part 0；
> §290 的规格原文另见 `plan/Intent-Policy-NN-Plan.md` §3。以下为决策正文。

## 289. 窗口 0 稳定化 gate — super-item 退役默认 OFF + 基线重钉三件套（2026-08-26，plan/Intent-Policy-NN-Plan.md §5.4）
**Decision:** window-0 合并（f956d3f）落地后，按 AI-No-Items-Warmstart M0 决策把
`superItemMode/superItemGuardThreat` 退役为默认 **OFF**（NN 训练语料必须无主动道具；
原 §167 的 ON 默认仅保留为可重开旋钮），并完成新纪元三件套：① 本条目；② eval-suite
35×60 三难度 pinned run 重跑；③ det golden 更新（aa395e1f…）。

**Rationale:**
- 意图策略计划的全案前提是"摘道具后 ~75%"基线；训练分布与部署分布必须一致。
- 成本已量化并接受（plan §0.2 R4）：M0 60-seed 配对 A/B hard ≈ −1pt（p=0.0002）。
- 稳定化 gate 五项全过：check 绿 / verify-demos 产出率 93.3%（104→97，≥90% 照旧可用分支）/
  决策面 diff = ACTION_WEIGHTS 逐值一致 + 共享旋钮默认零变更（chokepointHoldCheckTicks 随封版移除）/
  机械 tagger 单局冒烟通过。

**Implications:** 新 pinned run = **2026-08-26 @ post-merge+super-item-OFF，seeds 1–60，v7**：
classic SUITE 0.7286 (lcb 0.7238±0.0048, WIN 89.5%) / **hard 0.5290 (0.5228±0.0063, WIN 74.3%)** /
chaos 0.4862 (0.4797±0.0066, WIN 69.0%)。74/76/78 锚点与全部 A/B 对比自此相对本 run 定义
（预注册 #4）；score-gate TRUTH_SCORES 第三次重钉（hard agg −0.89pt、chaos −1.9pt、classic 不变，
`tools/diag/recapture-score-truth.ts` 常备化）。


## 290. M0a 词表契约定稿 — spec-in-code 单一实现（2026-08-26，plan/Intent-Policy-NN-Plan.md §3）
**Decision:** M0a 全部规格落在 `src/ai/intent/vocab.ts` 一处（tagger/executor/探针共享，禁第二份）：
① 8 类词表 `INTERCEPT/RETURN_DEFENSE/HUNT/HOLD_LANE/CLEAR/PICKUP/CRUISE/ESCAPE`；
② 正向映射以**细分支标签**为主键（19 候选全量挂靠，两层口径 P1-7④），战斗族四分支
（t2a/navigate/aggressive/firingLane/candidateKill）走预注册战斗链谓词：
回防(基地受威胁∧距基地>maxPlayerDistFromBase) > CRUISE(≤endgameEnemyThreshold) > HUNT；
③ 反向白名单三层标注（window/overlay/reflex），压制 dodge 标记唯一挂 suicideReturn（默认 OFF 反例）；
④ 分段四件套 N=4 / reflex 透明 {dodge,survive} / 短段归前段（局首归后段）/ 探针同模块；
⑤ 激活头矩阵：enemy⊆{INTERCEPT,HUNT,CRUISE}、anchor⊆{RETURN_DEFENSE,HOLD_LANE,CRUISE}、
CRUISE 双激活、PICKUP/CLEAR/ESCAPE 双不激活；
⑥ 目标敌槽序 = 距基地 Manhattan 升序 + 行主扫描 tie-break（单一数值秩 dist×1024+row×32+col）；
⑦ 锚点 16 role 实例级槽位 + 35 关解析报表（0 miss；ok 34.5% 经现役 impl，其余确定性几何回退）。

**Rationale:**
- 计划明文要求映射表/槽序/分段为 tagger 与 executor 的共享实现（禁两份）→ 规格即代码 + 测试锁定，
  比 prose 规格更抗漂移；反向完备性断言（P0-1）由 tests/intent-vocab.test.ts 静态执行。
- INTERCEPT vs HUNT 区分判据用状态谓词预注册（敌是否威胁基地/是否在回防距离外），
  复用 §159 同款阈值 maxPlayerDistFromBase=26，不新增旋钮。
- CRUISE 切分复用 StrategyPlanner 现役 ENDGAME 门（enemiesRemaining ≤ endgameEnemyThreshold）。

**Implications:** M1 正式 tagger 只需在 think 层加日志支路消费本模块；M0b 探针已用同一实现
冒烟通过（单局 38 窗口、±5 tick 翻转率 6.8%；stages 30-32×seeds1-3 分布合理）。锚点排名解析器
`rankBaseGuardAnchorsImpl` 为 additive 提取（k=1 与原 computeBaseGuardAnchorImpl 逐字节等价）。


---

# 已归档索引（正文已在主题文档，DECISIONS.md 仅留指针）

| 条目 | 落点 |
|---|---|
| §21–§26 渲染/仿真性能四轮压榨 | `docs/perf-optimization.progress.md`（Round 1–13 各节） |
| §31 超级道具背包累积制 | `docs/features.md` §1.3 |
| §70–§234 God AI 调参 / 回放 / 督战 / 双玩家 | `docs/god-ai-tuning.progress.md`（§编号章节） |
| §122–§129 仿真性能 Round 10–13 | `docs/perf-optimization.progress.md` |
| §235–§238 渲染优化 R6/P1-C/R7/R8 | `docs/render-optimization.progress.md`（同名节） |
| §277–§278 all-on 全策略实验 | `docs/god-ai-tuning.progress.md` §239 / §240 |
| §281–§284 RL 断点续跑 / 跳轮修复 / 干净评估 / BCV2 | `docs/nn.progress.md`（§4 / §5 / §7 / 分布式节） |
| §285–§288 M1 分歧探针 / 语料纪元 / M3 双臂 / 回环整改 | `docs/nn.progress.md`（§13.2 / §14 / §15.1 / §15.4） |
| §291–§302 意图策略 M0b–M8 | `docs/nn.progress.intent.md`（§16–§27） |
| §293-intent M4 完成 | `docs/nn.progress.intent.md` §19 |
| §293-God AI 解冻纪元 | `docs/god-ai-tuning.progress.md` Part 0.1 |
