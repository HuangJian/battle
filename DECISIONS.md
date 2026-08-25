# Design Decisions

> Key decisions. Full details in linked documents.
> 编号体系：§1–§9 为基石决策，其余为分类索引。
> **God AI 调校索引**：Classic 纪元（§27–§95，2026-07-27 → 08-01）与 v2 重设计纪元（§96–§110，2026-08-03，
> M0–M11）的完整进展、数据与方法论教训统一归档于 **`docs/god-ai-tuning.progress.md`**（Part I / Part II）；
> §96–§110 在本文件为压缩索引，正文全文见该文档。v2 设计文档（plan/God-AI-Redesign-v2.md 等）已删除，
> 核心设计归档于 progress.md §II.0。

---


> **本文为决策索引**：§1–§10 为基础架构决策（全文保留）；God AI / 性能 / 渲染调参条目已按主题将全文迁入 `docs/*.progress.md`，本文件仅保留编号、标题、状态与指针（`→` 链接）。

---

## 勿重提清单 (Do-Not-Re-Investigate)

> 以下结论已在重构审计中反复确认，未来 agent 不要再重查或推翻。完整依据见 `plan/refactor.trae.md` §0.5。

| 项 | 结论 | 依据 |
|---|---|---|
| `Tank` 实体字段拆分 | 不拆。42 字段逐字段 grep 读写方，零死字段；spread 克隆零漂移。 | refactor.trae.md §0.5 / zcode §257 |
| `WorldSerializer` 改实体级 spread 为字段级手写 | 维持现状（实体已走 spread 克隆，护栏只需守 World 顶层且已完备）。 | refactor.trae.md §0.5 / zcode §257 |
| 主题 `skipSvg` 分支删除 | 保留。`skipSvg = (themeKey !== 'modern')` 是活代码。 | agy §3.1 / §252 |
| 测试子目录化 | 不做（churn 高、零玩家价值）。仅廉价改名已做。 | agy §3.5 / zcode §269 |
| `byId` 事件字段删除 | 保留。`simulation-runner` 取证工具在消费它。 | zcode P1 |
| `applyPowerUp` switch → 派发表 | 维持。switch 本身就是派发表，形态合理。 | zcode 复核 |
| 大文件机械搬移（无测试网领域） | 先补特征测试再动手（高返工率）。 | zcode 教训 3 |

> **重构计划文件保留约定**：重构计划文件（如 `plan/refactor.agy.md` / `plan/refactor.trae.md`）除非其内容已并入本文件，否则保留在仓库内，不得删除；其否决结论以本文件 § 编号或上方「勿重提清单」作持久落点。（`refactor.zcode.md` 已删、仅留历史于本文件与 refactor.trae.md 顶部，未来引用一律改指 `refactor.trae.md`。）

---

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

## Architecture Decisions

| Decision | Detail |
|----------|--------|
| Presentation layer (event-driven, read-only, canvas 416×416 + HTML HUD) | `docs/architecture.md` §4 |
| DPR-aware rendering (offscreen buffer + DPR-scaled display canvas) | `docs/architecture.md` §4 |
| Animation system (time-based, VisualComponent) | `docs/architecture.md` §4 |
| Particle system (pool-based, pre-allocated) | `docs/architecture.md` §4 |
| Camera system (shake + offset) | `docs/architecture.md` §4 |
| Theme system (ThemeColors + CSS variables) | `docs/architecture.md` §4 |
| State transitions (CSS-animated HTML overlays) | `docs/architecture.md` §4 |
| Determinism (seeded RNG, `Math.random()` banned in Simulation) | `docs/architecture.md` §7 |
| InputLike interface (Simulation depends on interface, not concrete Input) | `docs/architecture.md` §3 |
| Generic pathfinding (`utils/pathfind.ts`, A* + BFS + flood-fill) | `docs/architecture.md` §3 |
| Stage loading API (`World.loadStageData`) | `docs/architecture.md` §8 |
| Level generator (7-layer procedural pipeline) | `docs/architecture.md` §11 |

## Gameplay Feature Decisions

| Decision | Detail |
|----------|--------|
| Combat capability system (6-dim `CombatProfile`, 300-point budget, derived stats) | `docs/features.md` §3 |
| Fire-rate standard (per-kind table, 3-bullet math anchor, per-fire jitter) | `docs/features.md` §3 |
| HP level visual aura (6-tier light rings, dynamic degradation) | `docs/features.md` §3 |
| Spawn-rolled 5-tier AI (commander succession, compliance, floor/cap) | `docs/features.md` §4 |
| Centralized scoring (kill/clear/item formulas, per-difficulty/stage/tier) | `docs/features.md` §1 |
| Item drop rules (elite kills + every-10-kills, super power-ups 10%) | `docs/features.md` §1.3 |
| Gameplay rules (per-difficulty rule profiles, classic faithful feel) | `docs/features.md` §1.2 |
| Timed power-ups stack duration on re-pickup | `docs/features.md` §1.3 |
| Enemy dead-end shaft recovery (tunnel out of 1-wide channel) | `docs/features.md` §4 |
| Snapshot management framework (one model, four origins, policy-driven retention) | `docs/architecture.md` §7 |
| Recovery-screen UI state guards extracted to pure predicates (`uiFlowGates.ts`) | The MISSION FAILED (recovery) screen buttons (Replay Browser / Lie-Back Win / Key Bindings) were dead / erroring because their *state guards* in Game.ts forgot `'recovery'`. The guards were extracted into a DOM-free module so the fix is regression-tested headlessly (`tests/recovery-screen-flow.test.ts`). Game.ts consults the same predicates — one source of truth, no behavior change. |

## God AI Tuning

Full history in `docs/god-ai-tuning.progress.md`. Key milestones:

| Phase | Outcome |
|-------|---------|
| Infrastructure (CMA-ES, decision tracing, simulation pool) | `docs/god-ai-tuning.progress.md` §3.1 |
| P0–P3 deadlock fixes (anti-camp, wider dodge, A* dig-through-brick) | `docs/god-ai-tuning.progress.md` §3.2 |
| P4 all-35 floor-aware tuning (81.9%→87.7%, 0/35 below floor) | `docs/god-ai-tuning.progress.md` §3.3 |
| Round 5 S33 close-combat (t2aMaxRange=2, 72.5%→85.0%) | `docs/god-ai-tuning.progress.md` §3.4 |
| Phase A SmartThreatModel (rejected, 8+ variants all negative) | `docs/god-ai-tuning.progress.md` §3.5 |
| §47 base protection ring collision fix (real S33 breakthrough) | `docs/god-ai-tuning.progress.md` §4 |
| §48 terrain-occlusion evasion (rejected, terrain-blind is load-bearing) | `docs/god-ai-tuning.progress.md` §4 |
| §49/§52 muzzle-to-muzzle (v1 rejected, v2 counter-fire neutral) | `docs/god-ai-tuning.progress.md` §9 |
| §67 stop tuning at 88.5% (flat optimum confirmed) | `docs/god-ai-tuning.progress.md` §4 |
| §68 crossfire awareness v2 (negative -1.1pp, default OFF, infra preserved) | `docs/god-ai-tuning.progress.md` §10 |
| §69 crossfire terrain-gate + A* threat cost (both negative, infra preserved) | `docs/god-ai-tuning.progress.md` §10 |
| §70 base-ring fire guard (T2b/aggressive break-through + T6 steel ring + post-loop baseSteel) | see below |
| §48-revisit steel-only evasion occlusion (terrain-gated: brickWallRatio < 0.10 → steel mazes only) | see below §71 |
| §49-revisit counter-fire parameterized + re-validated (clean positive: net +3 flips, zero ON→OFF losses) | see below §72 |
| §68-revisit crossfire re-tuned with per-seed tick-diff (rejected: 4 variants all net-negative, stays OFF) | see below §73 |
| §74 distance-aware base-wall fire guard (T2a/aggressive suicide fix, +0.2pp mean, killer=player 4→1) | see below §74 |
| §79 coop God AI drove P1 not P2 (replay stall + base-wall break, single-player no-op) | see below §79 |

**Current state**: 92.1% mean (post-§74 distance-aware base-wall guard), 0/35 below floor, 0 stage overrides. Default params frozen.

> **§2.3 压缩状态（refactor.trae.md）**：§71–§169 条目已在先前轮次统一压缩为
> `docs/*.progress.md` 指针（全文 → 共 165 处），阴性 / REJECTED 细节均已下沉，
> 本段无冗余正文，原 §2.3 范围已满足。本文件当前的 2223 行主要来自 §192–§271
> 较新 verbose 条目（重构落地汇总 + M 系列 A/B），超出 §2.3 既定范围；其中含活跃
> 结论（SHIPPED 默认 / 进行中），按 AGENTS §5.1 不擅自压缩。

## 71. §48-Revisit: Steel-Only Evasion Occlusion, Terrain-Gated (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 72. §49-Revisit: 炮口相向对枪抵消 Parameterized + Re-Validated (SHIPPED, default unchanged)
> 全文 → docs/god-ai-tuning.progress.md

## 73. §68-Revisit: Crossfire Awareness v2 Re-Tuned with per-seed tick-diff (REJECTED, stays OFF)
> 全文 → docs/god-ai-tuning.progress.md

## 74. Steel-Fire Gate: Never Fire at Unpierceable Steel to Break Through (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 75. §75: Distance-Aware Base-Wall Fire Guard (T2a/Aggressive Suicide Fix)
> 全文 → docs/god-ai-tuning.progress.md

## Performance Optimization
> 全文 → docs/perf-optimization.progress.md

## Lie-Back-Win-Mode (Coop God AI)
> 全文 → docs/god-ai-tuning.progress.md

## Render Optimization
> 全文 → docs/render-optimization.progress.md

## 70. Base-Ring Fire Guard (Never Destroy Own Base)
> 全文 → docs/god-ai-tuning.progress.md

## 75. Replay Recording Must Tap the Decorated Input (Lie-Back-Win-Mode desync) (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 76. The Packed Blob Is the Only Authority on Frame Schema (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 77. Playback seek must advance the input (drag-the-bar desync) (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 78. Seek catch-up must drain (discard) world events — no audio burst (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 79. Coop God AI drove P1 instead of P2 (replay stall + base-wall break)
> 全文 → docs/god-ai-tuning.progress.md

## 80. §80: Turn-Snap Aim Guard — Don't Commit to a Stop-and-Aim Turn Whose Grid-Snap Breaks the Firing Line (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 82. 督战模式（Supervise）— God AI 作为 player1 全程无人类输入 + 战斗速率快捷键 (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 81. 移除 godai-stage-overrides.ts 机制 — 禁止按关卡名特殊化（防过拟合）
> 全文 → docs/god-ai-tuning.progress.md

## 83. §83: dodgeDirection 回退分支不再沿炮弹飞行方向逃跑 — 受困走廊时回头对枪抵消 (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 84. BONUS TIME: God AI Collects the Remaining Power-ups in the Pickup Window (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 85. §84-Revisit: BONUS TIME Pickup Is Reachability-Aware — Never Chase an Unreachable Item (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 86. Snapshot Must Preserve the Bonus Pickup Window — Mid-Window Restore Never Re-Opens BONUS TIME (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 87. Replay Needs No Pickup-Window Changes — It Inherits §86 via the Shared Serializer (VERIFIED + GUARDED)
> 全文 → docs/god-ai-tuning.progress.md

## 88. §88: Aggressive branch stall detection — freeze window no longer wasted firing at nothing (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 89. §89: Close-range enemy exposure check — don't flee from point-blank enemies (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 90. Dodge Direction Persistence + Threat Hysteresis (Bug Fix)
> 全文 → docs/god-ai-tuning.progress.md

## 90b. §90 A/B Test Results — Oscillation Counter-Fire Shipped (Negative Results Recorded)
> 全文 → docs/god-ai-tuning.progress.md

## 91. Turn Cooldown (§90c) — Simulation-Layer Oscillation Prevention
> 全文 → docs/god-ai-tuning.progress.md

## 92. §87: Urgent Power-Up Pickup Priority — Close + Safe-Path Pickups Outrank Defense/Kill (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 93. §88: 据守咽喉要地 (Chokepoint Holding) — Rule-1/2/3/4 Base-Defense Strategy (CANDIDATE, A/B-Tuned) _(superseded by §94 — SHIPPED default ON)_
> 全文 → docs/god-ai-tuning.progress.md

## 94. §88 据守咽喉要地 (Chokepoint Holding) — SHIPPED (default ON, supersedes §93 candidate)
> 全文 → docs/god-ai-tuning.progress.md

## 95. Turn Cooldown 50ms → 100ms + Halt-During-Cooldown (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 96. M0 基线测量 + M0.5 僵尸参数退役（SHIPPED，2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 97. §M3 Dodge 质量：dodge 分支近距离对枪抵消（SHIPPED 后回退） _(superseded by §98)_
> 全文 → docs/god-ai-tuning.progress.md

## 98. §M3 Dodge 对枪抵消：回退 OFF + Gate 确定性根因修复（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 99. M1 决策链评分制外壳：落地 + Parity 三重验证通过（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 100. M2 权重数据化：actionWeights 基础设施 + classic 重排 A/B 诚实阴性 + M2b 推迟（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 101. M3 dodgeCounterFire 三轮门控全部官方口径阴性 + stageIndex 口径伪影完整机制（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 102. M3 敌情感知 EnemyModel + survive 候选 + 命数感知 + M4 紧急对枪：机制落地，默认 OFF（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 103. M5 站位提前规避（pathThreatAvoidance）：机制落地，A/B 阴性，默认 OFF + 口径事故根因（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 104. M6 出生即一星（playerStartLevel 0→1）：首个强信号发布，hard/chaos +8~9pp（SHIPPED，2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 105. M7 追猎死亡探针：真追猎仅 ~3-7% + 模拟口径三重修复（playerLevel / lives / telemetry）（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 106. M8 survivalRetreat 官方口径 60-seed 确认：持平偏负，不发布（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 107. M9 dodgeHorizonScore 多弹道生存视界承诺闪避：机制成立但 60-seed 阴性，不发布（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 108. M10 dodgeHorizon 门控变体（时间余量 + 距离）：chaos 确凿阴性，不发布（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 109. M11 星经济下一档：playerStartLevel 1→2（SHIPPED 后用户否决） _(superseded by §110: 用户否决，回退 1★，2026-08-03)_
> 全文 → docs/god-ai-tuning.progress.md

## 110. 用户否决 §109：hard/chaos 起始二星回退为一星（2026-08-03）
> 全文 → docs/god-ai-tuning.progress.md

## 111. 星盾扩展到所有难度（引擎改动）+ HP 模型探针选靶（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 112. M12 玩家 HP 缓冲感知：诚实阴性（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 113. M13 全场压力撤退（outnumberedFieldRetreat）：SHIPPED，hard +2.3pp / chaos +0.6pp（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 114. M4 标量参数 CMA-ES 首轮：子集过拟合阴性 + M13 阈值双重复证（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 114.1 M4 round-2 建议配置（评审决议，未执行）
> 全文 → docs/god-ai-tuning.progress.md

## 115. M4 round-2 全语料 CMA-ES：SHIPPED，pool 模型 +5.0/+8.3pp（classic 还原表保 91%）（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 116. 自杀秒回（suicide quick-return）：实现 + 诚实阴性（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 117. 自杀秒回条件①变体（mode 2 STAND / mode 3 CHARGE）：诚实阴性（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 118. §117 守卫升级（baseHp 阈值 + 防守位失守）A/B — 仍为诚实阴性，机制性证伪（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 119. 固化策略调试方法论：run-forensics 分层取证（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 120. 自毁基地 32 局取证 + 采集脚本迭代（off-by-one / bullet-dir / --from-json）（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 121. t2a/aggressive 停射自毁守卫 selfFireBaseGuard SHIPPED（2026-08-04）
> 全文 → docs/god-ai-tuning.progress.md

## 122. 仿真性能 Round 10：computeThreatPoints 对齐枚举（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 123. 仿真性能 Round 10：scanAheadImpl per-tick memo（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 124. 仿真性能 Round 10（REJECTED）：rectHitsTerrain 比较链重排 / terrain 短路
> 全文 → docs/perf-optimization.progress.md

## 125. 仿真性能 Round 10：selectTarget within-tick memo（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 126. 仿真性能 Round 10（REJECTED）：canStepLat 手内联 rectHitsTerrain
> 全文 → docs/perf-optimization.progress.md

## 127. 仿真性能 Round 11：followPath→replanImpl 跨 tick 缓存（SHIPPED，2026-08-05，含引用别名修复）
> 全文 → docs/perf-optimization.progress.md

## 128. 性能基线标准场景改为 classic/hard/chaos 各 1/3（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 129. pickup 可达性 A*：dig-only + 跨 tick 纯 memo（SHIPPED，2026-08-05）
> 全文 → docs/perf-optimization.progress.md

## 130. 全难度命数统一为 3 + GOD AI 基线重测（SHIPPED，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 131. T8 拦截射程 pool 2→8/12：60-seed 诚实阴性（不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 132. 方向 B：selectTarget 威胁评分按 kind 速度 × 距基地距离加权（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 133. 方向 C：brick-heavy 关防守距离再校准——诚实阴性（不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 134. 方向 D：防守位停射拦截基地车道敌人（SHIPPED，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 135. 方向 D 预测版：提前拦截基地车道逼近者（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 138. 基地守位格 v2：受威胁时驻守守位格（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 137. 基地守位格（Base Guard Anchor）—— 诚实阴性，不发布（2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 136. 方向 D 破砖版：预测命中时打场景砖开路（诚实阴性，不发布，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 139. 方向 A：火力死区解除（firing-lane re-engage）—— 灾难性阴性，不发布（2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 140. 方向 D4：baseWall 精确环判定（破砖开火假阳性修复，SHIPPED，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 141. D2 拆环威胁评分 —— 诚实阴性（旋钮默认 0，byte-identical）
> 全文 → docs/god-ai-tuning.progress.md

## 142. D1 防守落点解盲 —— 诚实阴性（baseGuardAnchorMode 保持 0）
> 全文 → docs/god-ai-tuning.progress.md

## 143. D5 基地火力解锁 + 星经济 —— 诚实阴性（firingLaneBoxRow / pickupStarBoxRow 保持 0）
> 全文 → docs/god-ai-tuning.progress.md

## 144. E1 道具经济（危急道具拾取）—— 诚实阴性（direItemMode 保持 0，反证判据收束）
> 全文 → docs/god-ai-tuning.progress.md

## 145. S24 冰面机制深潜 + iceGlideControl —— 诚实阴性（旋钮保持 0，S24 = 难度地板关）
> 全文 → docs/god-ai-tuning.progress.md

## 146. S8 Riverbed 取证深潜 + defensePosStandable —— SHIPPED（集合点可达性修复，hard 45%→52%）
> 全文 → docs/god-ai-tuning.progress.md

## 147. S8 三杠杆 B/C/A 逐一 A/B —— B SHIPPED（§146 已记），C/A 诚实阴性（§146 C 范围限制 + A 全局崩盘）
> 全文 → docs/god-ai-tuning.progress.md

## 148. fieldRetreatPickupGate 扩展到 MID/LOW —— 实测证伪后回退（HIGH-only 定稿，§147 范围锁定）
> 全文 → docs/god-ai-tuning.progress.md

## 149. defensePosStandable 全面启用（minDist 解除）全关验证 —— 边际 ≈ 0，不发货（收窄版 §146 保持最优）
> 全文 → docs/god-ai-tuning.progress.md

## 150. 关卡序号统一为 1-based（工具 CLI + 文档 S# 全量修正，2026-08-05）
> 全文 → docs/god-ai-tuning.progress.md

## 152. hard S12 Lattice 回放四联 bug 修复（§152-W1..W4）+ 全关 A/B 验证（SHIPPED）
> 全文 → docs/god-ai-tuning.progress.md

## 153. hard S12 Lattice seed 3214953618 回放两行为（bullet-crash + close-combat trade）诊断与修复（实现 + 单测锁定；A/B 发现两者全局非正 → 实验旋钮不发货）
> 全文 → docs/god-ai-tuning.progress.md

## 154. bulletLaneWait W1 重设计（§153 后记）：18 个净负种子根因定位 + predictive next-body 最终版（实测 35×60 hard 净 +15；仍为实验旋钮默认 0）
> 全文 → docs/god-ai-tuning.progress.md

## 155. bulletLaneWait W1 全局发货（§154 最终版，用户决策：忽略 chaos）
> 全文 → docs/god-ai-tuning.progress.md

## 156. Freeze-Window Power-Up Pickup（冰冻期道具拾取，无限距离）
> 全文 → docs/god-ai-tuning.progress.md

## 157. Base Clear-Shot Threat Detection（基地车道对齐远距离威胁检测）
> 全文 → docs/god-ai-tuning.progress.md

## 158. Non-Freeze Close-Range Power-Up Pickup（非冰冻期近距离道具拾取）
> 全文 → docs/god-ai-tuning.progress.md

## 159. 天降神兵守卫改用 GOD AI + §避让防堵车（用户需求 2026-08-06）
> 全文 → docs/god-ai-tuning.progress.md

## 160. 避让中扫射压制——避让开火优先沿腾挪轴（用户需求 2026-08-06）
> 全文 → docs/god-ai-tuning.progress.md

## 161. §161 开路策略（carve path）——实现完整、hard 全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，Stage 33 Battlement 过关思路）
> 全文 → docs/god-ai-tuning.progress.md

## 162. §162 nav 卡死破局（navBreakStuck carve-dig escape）——SHIPPED 默认 1，hard 全 35 关显著胜率提升 p=0.019（用户需求 2026-08-06，回放 hard-s34-base-l2-t69-seed2050197249 Problem 1：出生点被砖墙围堵，player 不开墙出击，0:00~0:20 在出生点附近振荡）
> 全文 → docs/god-ai-tuning.progress.md

## 163. §163 中路防守（midLaneDefense）——子弹触发版全 35 关与 Battlement 均实测净零 → 诚实阴性归档，旋钮默认 OFF（用户需求 2026-08-06，回放 Problem 2：基地列无钢铁防护，player 坐视敌人凿穿中路砖墙）
> 全文 → docs/god-ai-tuning.progress.md

## 164. §164 中路列旁主动驻守（midLaneHold）——诚实阴性归档（用户需求 2026-08-06：让 §162 出袋后的玩家优先走中路走廊而非左侧，在列旁持枪对消）
> 全文 → docs/god-ai-tuning.progress.md

## 165. T2a Defense Override — 近敌停射允许（修复 S20 Bastion 振荡死锁，origin 侧原 §159）
> 全文 → docs/god-ai-tuning.progress.md

## §165. 中路防守启用 + 水阻弹 bug 修复 + 近战对枪火力评估
> 全文 → docs/god-ai-tuning.progress.md

## §165-round2. 深度调优：pathThreatAvoidance 假阳性 + closeCombatDuel 多敌计数 + midLaneHold 主动防守
> 全文 → docs/god-ai-tuning.progress.md

## 166. B1 starRush 星经济冲刺 — 诚实阴性归档（旋钮默认 0，2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 167. B4 超级道具战略激活（superItemMode）— SHIPPED guard-only（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 168. navStuck 计数器抖动重置 bug（navStuckZone）— 实验阴性，旋钮留档默认 0（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 169. 基地威胁信号闪烁（threatStickyTicks）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 170. 追击承诺（huntCommitTicks）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 171. 路径长度感知目标选择（pathTargetMode）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 172. bonus 敌人追猎权重（bonusHuntBias）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 173. 基地损伤召回（baseDamageRecall）— 立项（2026-08-07）
> 全文 → docs/god-ai-tuning.progress.md

## 174. 双玩家仿真系统 — 双 God AI 协作 + 防堵车 + 督战双玩家 (SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 175. Dual 中路无钢关配合策略 — 立项（2026-08-08）
> 全文 → docs/god-ai-tuning.progress.md

## 176. Dual Central Breach §6 实测缺陷修复 — P2 角色落地 + P1 dig-fire
> 全文 → docs/god-ai-tuning.progress.md

## 177. Dual Central Breach P2 导航落地 — directMove/patrol 实测回退，de-conflict 生效
> 全文 → docs/god-ai-tuning.progress.md

## 178. Dual Central Breach autopsy (hard-s34 seed2) — carve 穿墙 + 中驻守 + sticky hold
> 全文 → docs/god-ai-tuning.progress.md

## 179. Dual Central Breach autopsy (hard-s34 seed6) — P1 凿盾 + 危基不回防 + 冰冻浪费
> 全文 → docs/god-ai-tuning.progress.md

## 180. Dual Central Breach autopsy (hard-s34 seed34) — 右路盲区 + fence 独占 + defenseSecond 近端覆盖
> 全文 → docs/god-ai-tuning.progress.md

## 181. Dual Central Breach autopsy (hard-s34 seed115) — P1 spawn 振荡：A* 路由穿透基地保护砖
> 全文 → docs/god-ai-tuning.progress.md

## 182. 重放暂停后切换应用再回来点播放，画面不动（visibilitychange 污染 world.state）
> 全文 → docs/god-ai-tuning.progress.md

## §182. Face-Nearest-Enemy Fallback for Immobile-Stuck Player
> 全文 → docs/god-ai-tuning.progress.md

## §183. GOD AI Idle Calibration — Analysis Complete
> 全文 → docs/god-ai-tuning.progress.md

## §184. Freeze Powerup — Allied Guard Freeze Bug + Pickup Stuck Bug
> 全文 → docs/god-ai-tuning.progress.md

## §185. navStuckZone=1 — Sub-Pixel Jitter Defeats Nav-Stuck Counter
> 全文 → docs/god-ai-tuning.progress.md

## §186. powerupStuckTicks — Powerup Navigation Stuck Detection
> 全文 → docs/god-ai-tuning.progress.md

## §187. Guard/P2 A* Player-Obstacle + Target Blacklist + Fire Post-Turn + Powerup-Enemy Overlap
> 全文 → docs/god-ai-tuning.progress.md

## §188. Fence Power-Up Must Not Trap Tanks Inside Steel
> 全文 → docs/god-ai-tuning.progress.md

## §189. 开局联通清墙 — Base Connectivity Clear
> 全文 → docs/god-ai-tuning.progress.md

## §190. A* 寻路代价模型升级 — 砖墙=空地 + 基地环倍率 + 开火停车代价
> 全文 → docs/god-ai-tuning.progress.md

## 191. 批量仿真共享态硬化 — findPath 重入守卫 + level-sim 子进程隔离
> 全文 → docs/god-ai-tuning.progress.md

## §192. 基地车道哨兵（baseLaneSentry）—— SHIPPED（hard/chaos 默认 1；classic 0 保持字节不变）
> 全文 → docs/god-ai-tuning.progress.md

## §193-A. 中线火力门（centerLineFireGate）— 标注重评（被 §193-C 取代）
> 全文 → docs/god-ai-tuning.progress.md

## §193-C. 中线火力门 — SHIPPED（hard/chaos 默认 1；classic restore 0 字节不变）
> 全文 → docs/god-ai-tuning.progress.md

## §193-D. 预测前移门（predictiveFireGate）— SHIPPED（三难度默认 1；classic restore 0）
> 全文 → docs/god-ai-tuning.progress.md

## §193-B. 卫位导航（baseLaneSentryStation）— 达标保留（默认 0 = OFF，待发货）
> 全文 → docs/god-ai-tuning.progress.md

## §193-E. 环破回防（ringFallback）— 阴性归档（S34 -2，hard 全关 -54）
> 全文 → docs/god-ai-tuning.progress.md

## 194. 像素卡死 directMove 兜底 (§190)
> 全文 → docs/god-ai-tuning.progress.md

## 195. 中路钻探粘性驻守 midLaneStickyTicks=90 — S8 Riverbed 钻探败链修复 SHIPPED (2026-08-14)
> 全文 → docs/god-ai-tuning.progress.md

## 196. 钻探预警列完整性触发器（drill alarm）— 方向 1 证伪（2026-08-14）
> 全文 → docs/god-ai-tuning.progress.md

## 197. 带内拆环导航 + 远距开火（baseLaneSentryInBandNav/FarRange）— 方向 2 证伪（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 198. 卫位导航发货（baseLaneSentryStation=1）— SHIPPED（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 199. S34 站桩取证 + 工具口径 bug（方向 3 证伪 + ab-param 修复）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 200. dodgeEscapeDepth 逃逸深度闪避证伪（方向 5 D1）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 201. 方向 6（clearSpeed 0.151 慢/拖沓）— 分析性证伪：无 AI 拖沓可修（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 202. M0 威胁台账取证上线（threat-ledger + failure-classifier）— 硬关 2100 局基线（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 203. M1 ThreatBudget 纯模型上线（Phase 1 §5，默认未接线）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 204. M2 ActionContract 防守站桩门控（Phase 2 §6.1，默认 OFF + A/B）（2026-08-15）
> 全文 → docs/god-ai-tuning.progress.md

## 205. §6.2 targetValue 排序键 — A/B 证伪，保持默认 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 206. §6.3 短期 intent（lease+重验）— A/B 中性偏负，保持默认 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 207. Phase 3 §7 动态攻击覆盖点 — A/B 证伪（S34 崩塌），保持默认 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 208. §207 覆盖点实现缺陷审计 — 5 缺陷已修复，机制仍保持 OFF
> 全文 → docs/god-ai-tuning.progress.md

## 209. 覆盖点实现审计第二轮 — 坐标系根因 + (b)/BUG-2 修复，正确实现仍净负（OFF 维持）
> 全文 → docs/god-ai-tuning.progress.md

## 210. 覆盖点格坐标 round → floor（消除格中点决策振荡）
> 全文 → docs/god-ai-tuning.progress.md

## 211. 覆盖点负翻转 per-seed 取证 — 蝴蝶效应根因，csb/cbr 过滤修复证伪（OFF 维持）
> 全文 → docs/god-ai-tuning.progress.md

## 212. M4 安全吃星 — 诊断先行，收益空间不足（不提高 pickup 权重）
> 全文 → docs/god-ai-tuning.progress.md

## 213. Phase 5 CMA-ES — 启动决策与搜索空间重定义（§9.1 条件未满足，用户指示启动，按协议执行）
> 全文 → docs/god-ai-tuning.progress.md

## 214. Phase 5 CMA-ES — 参数面无 ROI，三批候选全部噪声，维持 DEFAULT（停止条件 §9.3.5 触发）
> 全文 → docs/god-ai-tuning.progress.md

## 215. Hard 开放测试第 1 轮: M0–M3 通过, idle 因果证伪(STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 216. M4 统一行动候选 — paired A/B 净 −116, 方向否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 217. M5 travel 段火力偏离 — 诊断先行, 机会空间 33% (STATUS: 实现中, 未 A/B)
> 全文 → docs/god-ai-tuning.progress.md

## 218. M5 travel 段火力偏离 (fireLineDetourMode) — 三批全正向, 候选通过初步 A/B → gated rollout (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 219. 评审 P1 修复轮 (实验工具可信度 + 4 处 AI 缺陷) (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 220. defenseIntercept 开火窗口 (actionContractMode 独立 A/B) — 三线微负, 方向否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 221. 评审 P2 修复 + M5 candidate-on 完整验证 (STATUS: 完成, M5 升格待拍板)
> 全文 → docs/god-ai-tuning.progress.md

## 222. CMA-ES 重启 — 全 stage 口径 (STATUS: 收口, 参数面无 ROI 确认)
> 全文 → docs/god-ai-tuning.progress.md

## 223. ③ dodge idle 取证收口 (STATUS: 完成, 候选方向待拍板)
> 全文 → docs/god-ai-tuning.progress.md

## 224. 候选 A: dodgeCentroidMode 否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 225. 后继 ④ "太迟"防御结构审计收口 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 226. 后继 ④ 候选 A/B 双否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 227. 后继 ⑤ t2a 自毁守卫重论证收口 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 228. M5 人工开放测试入口 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 229. M5 fireLineDetourMode SHIPPED — 默认 1（含 S30/S13 弱关重标定）(STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 230. 门禁 runner 瘦身 — collectMetrics/collectEvents + telemetry Set ping-pong (STATUS: SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 231. thinkInterval 决策链节流 A/B — 否决 (STATUS: 完成)
> 全文 → docs/god-ai-tuning.progress.md

## 232. 决策链小数组分配消除 + scanAhead 整数步进 (STATUS: SHIPPED, 字节等价)
> 全文 → docs/god-ai-tuning.progress.md

## 233. bun test 全套 <20s 攻坚结论 — 机器吞吐墙 + 种子数决策 (STATUS: 完成, 10 种子落地)
> 全文 → docs/god-ai-tuning.progress.md

## 234. 门禁种子 20→10 后补 — test-silent HEAVY_TESTS 修复 + 强制 --parallel (STATUS: SHIPPED)
> 全文 → docs/god-ai-tuning.progress.md

## 235. R6 vignette 缓存 1× 化 — 全屏 alpha blit 面积 4× 缩减 (STATUS: SHIPPED, 有损项已论证)

**Decision:** `GameRendererCore` 的 vignette 离屏缓存从 `FIELD*dpr × FIELD*dpr`（DPR 分辨率）改为
`FIELD × FIELD`（1× 逻辑分辨率），blit 时仍画到 `FIELD×FIELD` 目标（主 ctx 已带 DPR transform，
物理像素仍为 832×832）——即源 416 上采样到目标 832。

**Rationale:**
- vignette 是平滑 radial gradient（中心 `rgba(0,0,0,0)` 全透明 → 边缘 `vignetteColor`），上采样
  无纹理细节可模糊，视觉无损。
- 全屏 alpha blit 是软件光栅化器上最贵的单操作（R0 关键发现：API churn 占 DPR=2 成本一半；
  progress 文档明示 vignette 在 Skia ~1.4ms/frame 估算）。源面积 4× 缩减直接砍 blit 像素工作。
- 实测（frames=300 warmup=30 repeat=2 dpr=2）：idle 1.3260→1.0060 (−24%)、combat 1.5862→1.2911
  (−19%)、burst 2.0068→1.7642 (−12%)、pan 1.7223→1.4678 (−15%)。draw/f 计数完全不变
  （21/46/109/47——确定性信号零回退）。
- 与 R3+ aura / R4-glow 预渲染同属"位图预渲染 + 量化"族，但本次是有损项中**差异最小**的一类。

**有损项论证（像素 diff ≠ 0，按 plan §6 流程）：**
- 微型基准（`vignetteScaleBench` 探针）：1× 上采样 vs DPR 直绘，差异**全部集中在 alpha 通道**，
  maxDelta = 1（1/255），中心透明区（<0.35R）差异 = 0%，仅渐变边缘带 9-15% 像素的 alpha 1/255 波动。
- RGB 通道差异 = 0（纯 alpha-only）。
- 结论：视觉不可辨（alpha 1/255 ≈ 0.4% 不透明度差，远低于人类可辨阈值 ~1/50）。属"数学上有损、
  视觉无损"级别，与 R4-glow 的 pulse 量化（alpha 6.25% 步进，最大 delta ≈ 1.1/255）同级或更小。
- pixref 已按既有流程重捕获（`tools/perf/pixdiff.ts --write`），后续回归以此为新基线。

**Implications:** 未来若浏览器端实测 vignette 上采样在目标硬件产生可见伪影（预期不会——渐变
无高频内容），可回退为 DPR 分辨率或按 R 半径分环绘制。Performance Mode（lowQuality）仍整体跳过
vignette，不受影响。

## 236. P1-C 粒子 per-type 分桶 — 精确测量后放弃 (STATUS: 否决, 实测数据入档)

**Decision:** 不做 `ParticleSystem` per-type 紧凑索引数组（5N→N 迭代）。R2 仅实现 `count===0`
早退后的剩余项，经精确测量确认收益不证成复杂度。

**Rationale（实测数据，burst 场景 60 粒子峰值）：**
- `renderParticles` wall-time（非 counting ctx）median = 0.140ms，占帧 8.3%。其中原生 draw 调用
  （fillRect/arc/stroke/setTransform）占绝对大头——每个活跃粒子必须画一次，分桶无法削减。
- 全渲染体 A/B（2000 重复）：5-pass 42.5µs/frame vs per-type 35.0µs/frame → **上限 7.5µs/帧
  （0.45% 帧预算）**，且未计入 emit 入桶 / update 压缩 / 死亡出桶的维护成本。
- 老机器投影（JS 5-10× 放大）：~40-75µs/帧，仍 < 0.5% 帧预算。
- 大多数帧 0 成本：非爆炸期 `count===0` 早退（R2 已实现），分桶收益只在峰值帧存在。

**判定：** Gate 1 不可感知（0.45%）；Gate 2 净负（新增三处桶维护 + 紧凑数组，复杂度显著上升）。
与 R2 既有结论一致，本次以精确测量取代估算。

**Implications:** P1-C 从 plan 未完成项转正式否决项。若未来目标硬件实测粒子 JS 开销成为瓶颈
（预期不会——原生调用占 93%），可重审。

## 237. R7 坦克 tight-viewport blit — 实测否决 (STATUS: 否决, 9-arg 调用开销抵消面积节省)

**Decision:** 不做坦克 body blit 的 tight-viewport 化（58² → 内容 ~30² 窗口）。R6 后最大的
"理论可削减项"（坦克 sprite 烘焙在 58² 画布，内容只占 ~30²，blit 面积浪费 73%）经完整实现
+ 实测证明收益不成立。

**Rationale（实测数据）：**
- 前置验证：内容 bbox 确实只有 ~30×30（4 方向全部验证），58² blit 面积 73% 是透明浪费。
- 纯 5-arg 面积 A/B（合成测试）：58² → 30² blit 省 84%（196.8→31.7µs/frame）——但这是
  不同源画布（30² 真实画布），非 9-arg 窗口。
- **关键反证**：真实管线用 9-arg drawImage 源子矩形（纯 viewport 裁剪，像素级等价已验证
  IDENTICAL）后，idle 无改善（1.03 vs 1.02）、combat 反而 +7%、burst −6%、pan −7%——
  面积节省被 9-arg 调用固定开销抵消。合成测试 9-arg vs 5-arg 同面积仅差 1% 证实：58² 尺度下
  blit 是**调用开销主导，非像素面积主导**。
- pan 场景引入有损项（0.022% 像素通道 diff，maxDelta=12——相机亚像素位移下 9-arg 与 5-arg
  插值系数不同）。

**判定：** Gate 2 ✗（SpriteCache 双缓存 + bbox 计算 + 6 处调用点改造，复杂度显著）；收益 ✗
（实测非正）；pan 有损项 ✗。三项全不通过。

**Implications:** 真正能省的是**减少 blit 次数**（R5-B composite 已做）或**缩小源画布**
（重烘焙 30²，但会位移坦克 ≤2px + 需重捕获 pixref——收益预期也被调用开销主导，不证成）。
坦克 body blit 关闭此方向。

## 238. 粒子烘焙位图 blit — 实测 4× 慢，彻底证伪 (STATUS: 否决)

**Decision:** 不采用任何基于 `drawImage` 的粒子渲染方案。粒子原生绘制调用已到地板，不再优化。

**Rationale:**
- 微基准（416×416 @DPR2 主 ctx，12 粒子/帧 × 2000 次，与 renderParticles 同模式）：
  - arc+fill（现状 smoke/flash）：43.15µs/frame（3.6µs/粒子）
  - **烘焙 32px sprite blit 1:1：171.87µs/frame（14.3µs/粒子）——4× 慢**
  - blit 缩放（3-arg）：163.97µs/frame（13.7µs/粒子）
  - arc+stroke（现状 ring）：42.23µs/frame
- `@napi-rs/canvas` 的 drawImage 固定调用开销巨大（32px ≈ 14µs/call，58² ≈ 33µs/call 与 §237 一致）——**小图 blit 是该后端最贵原语，arc/fillRect 才是最便宜的**。
- 这反转了 R3-aura 的手法假设：aura 是 58² 大图（blit 相对划算），粒子是 10-30px 小图（blit 绝对亏损）。
- 现有实现已是近最优：spark=fillRect（最便宜原语）、smoke/flash=arc+fill、debris=setTransform+rotate+fillRect（rotation 逐粒子不可省）、globalAlpha 逐粒子 fade 不可批量。
- 唯一理论节省（arc→fillRect）需要圆形改方形，视觉错误。

**Implications:** 粒子渲染优化方向关闭。所有后续粒子相关改动只允许走 arc/fillRect 路径，禁止 drawImage。§236（分桶）与 §238（位图）合璧：粒子侧无优化空间。

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

## 272. God AI v1 封版冻结 —— D0 拍板 + 冻结基线 + 签名 golden (STATUS: 已实施, 2026-08-26)

**Decision:** owner 拍板（plan/God-AI-Organization.md §3，选项 A，不含敌人 AI 转向）：
**player 侧 God AI v1 就此定版**。无新证据不再开调优轮；此后任何 God-AI 行为改动 = 新纪元，
必须走「三件套」——新 DECISIONS 条目 + 重跑 60-seed 三难度基线 + 更新冻结 golden，缺一不可。

**冻结基线（v1 官方口径）**（2026-08-26，eval-suite v7 · 35 关 × 60 seeds · params=351325f1，
命令与 seed 来源见 progress.md Part 0；语料 tmp/freeze/baseline-<难度>.json 不作长期凭证）：

| 难度 | SUITE (lcb ±se) | 平均胜率 | fitness v6 | 最弱关 |
|---|---|---|---|---|
| classic | 0.7258（0.7211 ±0.0048） | 90% | 721.1 | Ice Palace 68% |
| **hard（主）** | **0.5450**（0.5388 ±0.0062） | **76%** | 538.8 | Battlement 30% |
| chaos | 0.4943（0.4878 ±0.0065） | 70% | 487.8 | Battlement 17% |

较 Phase III 基线（§0.C.5，08-12）：classic 逐位持平（0.7259→0.7258）；hard **+3.18pp SUITE /
+3pp 胜率**（§195/§198/§229 发货杠杆的累计收益）；chaos +0.17pp。hard 维度均值详见 progress.md Part 0。

**冻结签名 golden**：`tools/det-golden.v1.sha256` =
`b81e240a8c2980bbf805215319be5aa2f483a312235bd35d758a6e522870ec32`
（probe-det-baseline.sh 全量 21 组合 · 109,516 签名行 · @dc18e6a pristine 行为采集）。
`--golden` 校验模式并入 pre-commit（M6 落地）；门红 ≠ 出错，是强制显式判定（走上述三件套或回滚）。
已知盲区照录脚本头注：det 语料单玩家 only，dual/coop 由 godai-* 门禁覆盖。

**Rationale:**
- §226 已收口「hard 行为微调杠杆耗尽」，剩余失败面是结构性物理（弹速 × 中位 271 tick 拆基窗口）；
- score-gate（10 seeds 统计 floor）+ det 语料（相对自洽）都拦不住「字节自洽但语义漂移」——
  封版必须以冻结签名门强制（评审 god-ai-org.review.md P1）;
- MANIFEST Three Gates：继续调参无玩家可感知收益，只消耗维护面。

**Implications:** 重启协议（必读顺序 / un-archive 四步闸门）见 plan/God-AI-Organization.md §8 / §6 C1；
L2 可达性审计证据于 M6 落地时补录至本条。B（敌人 AI）/C（规则层）/D（人类体验）出口均需新立项拍板。
