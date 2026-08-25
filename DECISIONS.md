# Design Decisions

> Key decisions. Full details in linked documents.
> 编号体系：§1–§9 为基石决策，其余为分类索引。
> **God AI 调校索引**：Classic 纪元（§27–§95，2026-07-27 → 08-01）与 v2 重设计纪元（§96–§110，2026-08-03，
> M0–M11）的完整进展、数据与方法论教训统一归档于 **`docs/god-ai-tuning.progress.md`**（Part I / Part II）；
> §96–§110 在本文件为压缩索引，正文全文见该文档。v2 设计文档（plan/God-AI-Redesign-v2.md 等）已删除，
> 核心设计归档于 progress.md §II.0。

---


> **本文为决策索引**：§1–§10 为基础架构决策（全文保留）；God AI / 性能 / 渲染调参条目已按主题将全文迁入 `docs/*.progress.md`，本文件仅保留编号、标题、状态与指针（`→` 链接）。

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

## 167. B4 超级道具战略激活（superItemMode）— SHIPPED guard-only → RETIRED by default（2026-08-07 → 修订 2026-08-25 M0）
> 修订：plan/AI-No-Items-Warmstart.md M0 将 superItemMode/GuardThreat 默认归零（NN AI 全链路不使用主动道具）。
> 配对复测（A=显式 ON, B=新默认 OFF, hard 60 seeds）：胜率 76→75%、Δscore −0.0093±0.0025、t=−3.77、p=0.0002
> （Lattice 显著变差 0.519→0.485）——与新预检 B 臂完全一致；−1pt 缺口（R4）转列 RL 守家目标。
> 全文 → docs/god-ai-tuning.progress.md §M0

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

**Decision:** 新增决策链分支 `baseLaneSentry`（权重 850，位于 interceptBase 900 之下、pickupHigh 800 之上），仅限基地车道场景：当带威胁的敌人与玩家同列/同行（±1 格内对齐、其间无挡住子弹的砖）且曼哈顿距离 ≤ baseLaneSentryRange(6) 时，哨兵把炮口立定向目标并开火击杀；仅在本 tick 能开火（非冷却期且弹池未顶）时接管，冷却期放弃 claim 交还 midLane/navigate 正常流动，避免长期轴锁（v4→v5 关键改进）。环先破（baseRingBreached）后兜底：对齐任意附近带威胁敌人（anyBreacher || csb || cbr）也开火。

**Rationale:**
- 目标关 S34 Battlement hard 基线 13/60（21.7%）→ 发货后 20/60（33.3%），60-seed flip-scan 净 +7（8 胜 1 负）；全 35 关 × 60 seeds A/B 净 +17（67 to-win / 50 to-lose），无任何关崩盘（最差 S22 Oasis hard -4、chaos -1）。
- 根因取证（Battlement seed 14 弹道级还原）：拆环 fast 洞口横走被打到 24hp（一枪线），玩家与它同列 40 ticks 却一枪未中——双偏线扫描（±8px 两条偏线 OR）看见敌人（enemy=true）的同时中线被墙挡（wall=true），子弹从 (16,23) 砖格内射出被墙吃掉，随后 800ms 冷却（nextFireInterval≈48 ticks）内敌人转身逃离，midLaneDefense 又把玩家拖走。46/60 败局同因。评分类（§141 D2 defenseBreachBonus）、驻守类（§142 D1 anchor、§137）已穷尽 — 哨兵是第 5 杠杆：决策链抑制 + 原位持位打一枪线。
- A/B 演化：v1 长行军站位 + nav 开火 → 6/60 崩盘（旧胜局翻 11 个）；v2 仅站位 + nav 禁射 → 18/60；v3 nav 加回开火 → 12/60（nav 开火净亏 6 胜）；v4 纯持位打砖死锁修复 → 17/60；v5 冷却期不 claim → 20/60。实证三条：(a) 哨兵绝不能接管导航（navigate 的开火是杀伤主力）；(b) 冷却期站位移交流动（死锁修复：blocked==1 不能打砖时必须 return false 让位）；(c) 站位只用 ⚡immediate 一枪线，不做长驻。
- chaos gate 20-seed 曾两关采样性触底（S22/S26），用 60-seed + S26 120-seed 重测校准真值后稳定通过（chaos 全关 60-seed 均值 68.71%，S34 chaos 5%→21.7%）。

**Implications:** hard/chaos 默认参数 `baseLaneSentryMode=1`、`baseLaneSentryRange=6`；classic restore 区保持 0（instant 1-HP 未 A/B，classic gate 629/700 字节不变）。hard 全关 60-seed 均值 72.86%、chaos 68.71%，aggregate floor 随之重校准（484/455）。

## §193-A. 中线火力门（centerLineFireGate）— 标注重评（被 §193-C 取代）
> 原文保留：首次 A/B 结论（full 版 S34 净 -4 / 全关 +20）在 §193-C 重测中被推翻 —— 该数字来自实验期旧树（站台/边缘实验混杂）。_(superseded by §193-C)_

**Decision:** `centerLineFireGate` 旋钮实现并 A/B 后**回退为默认 0**（代码保留为实验旋钮）。双偏线扫描同时报 enemy+wall 时，用真实 6px 弹道中线做炮口→目标格砖检查，墙挡则不开火。

**Rationale:**
- full 版：全关 60-seed 净 +20（122 to-win / 102 to-lose）——但 **S34 净 -4**（520-650 权干掉了高权 firingLane 击杀，7 个 flip 全在 waist/ring 区，玩家冷却锁死窗口被敌弹打穿）。
- 迷你版（仅 counter-fire 路径）：S34 干净（0 flip）但全关净 +8 不达「全局 & S34 均净胜」门槛。
- 用户判定标准（s34.attempt.md）：两方向均须 全局 & S34 净胜；方向 2 两版均不满足 → 回退。

**Implications:** 2026-08-13 复测（HEAD=§193-B 四门槛版树，三工具交叉验证）：full 版 hard 全关净 +41（147 to-win / 106 to-lose）、S34 净 +2（9/7）——数字与 §193-A 原记录不符（旧数字混入了实验期瞬时代码状态）；豁免版复测 +2（S17:29、S29:12，与历史字节级一致）。据此 §193-A 结论作废，发货决议见 §193-C。

## §193-C. 中线火力门 — SHIPPED（hard/chaos 默认 1；classic restore 0 字节不变）

**Decision:** `centerLineFireGate` 发货，hard/chaos 池默认 `1`；classic 经 CLASSIC_MODEL_PARAMS 保持 restore 0（instant 未 A/B）。实现为 **full 版（无 march-dig 豁免）**：双偏线扫描同时报 enemy+wall 时，用真实 6px 弹道中线（`centerPathBlockedImpl`，语义与 `bulletHitsTerrain` 逐格一致）检查炮口→目标格，墙挡则抑制该次开火。

**Rationale:**
- 官方口径（60-seed，ab-gate / ab-gate2 / ab-final 三套独立 A/B 实现交叉验证一致）：hard 全关净 +41（147/106）、S34 净 +2（17→19/60，to-win 6,11,16,17,19,24,39,44,45 / to-lose 20,25,29,42,46,51,54）；chaos 全关净 +6（127/121，S34 −5：26→21/60）；classic FULL 净 −12 → classic 保持 restore 0 即可规避。豁免版（仅抑制 OFF 方向 pokes）在 hard 仅 +2、chaos 0——march-dig 的 6px 中线大多数是实心砖，不是未来路线，越豁免越弱（§193-A 的「S34 需要豁免」推论被推翻：现在 S34 +2，豁免版反而归零）。
- gate 全量复测（改默认后）：hard 524/700（74.9%，floor 484）、chaos 494/700（70.6%，floor 455）——从校准基线 72.86%/68.71% **双双上升**；classic 629/700（89.9%）字节不变。
- 用户确认：hard + chaos 开、classic 保持 0。

**Implications:** `bun run check` 全绿（1247 pass / 1 skip）。§161 Battlement carve 集成测试的 minDistPost 断言 6→8（§193-C 默认 1 改变 RNG 流，seed 1 轨迹偏移至 7；carve 仍 engage，行为保真）。`_centerLineFireBlocks` 保留为观察计数（GodAIInput +7），不 feed back。

## §193-D. 预测前移门（predictiveFireGate）— SHIPPED（三难度默认 1；classic restore 0）

**Decision:** `predictiveFireGate` 发货（hard/chaos 默认 `1`；classic 经 CLASSIC_MODEL_PARAMS restore 0，字节不变）。enemy 分支加时间窗门：目标以垂直方向横穿弹道线、且子弹到达（`enemyDist×CELL / bulletSpeed` ticks）时其身体已滑出 ±(TANK+BULLET)/2 命中窗 → 抑制必 miss 开火，时机窗口交给 P2.4 `predictEnemyCrossingImpl`（管「将上线」）——本门管「在线但将滑出」，互补。

**Rationale:**
- 60-seed A/B（ab-predict 全关扫描）：hard 全关净 **+7**（16 to-win / 9 to-lose，无崩盘关，最差 S10 -2）；classic +1（4/3）；chaos +1（12/11，**S34 +1**）。三难度净正、S34 非负 → 满足 s34.attempt.md「全关净正 → 考虑发货」。
- S34 触发面小（60 runs 仅 6 blocks）：fast 移动 1.2px/tick 太慢，2-3 格内子弹（~4.2px/tick）总先到达；门主要命中 waist/ring 区远距横走目标（604 blocks/60 runs 全关）。
- gate 全量复测（发货默认后）：hard 528/700（75.4%，floor 484，较 §193-C 又 +0.5pp）、chaos 494/700（70.6%）、classic 629/700（89.9%）——全绿。
- 负 flip 取证（S10 hard seed 28/39、chaos S20 3 局）：抑制边缘命中（目标恰在窗内边缘滑出判定过严）——net 为正说明收益（避免冷却浪费 → 更多有效弹）大于损失。

**Implications:** `_predictiveFireBlocks` 保留为观察计数（不 feed back）。与 §193-C 中线门正交（前者地形挡、后者时间滑出），同处 enemy 分支、均默认 1。

## §193-B. 卫位导航（baseLaneSentryStation）— 达标保留（默认 0 = OFF，待发货）
> 迭代取证全文 → s34.attempt.md（v1→v4 六败局逐案）

**Decision:** 新增 `baseLaneSentryStation` 旋钮（默认 0 = OFF，v5 字节不变）：哨兵对准 csb/cbr 目标且玩家非对齐/被挡时，低权分支走向**目标列 ±1** 的清晰站台列（垂直廊道无砖、站台格可站），到位后由对齐开火接管。**S34 hard 60-seed 净 +1（21/60，1 to-win 0 to-lose），全关净 +6（11 to-win / 5 to-lose），classic 0、chaos +2** —— 满足「全局 & S34 均净胜」。

**Rationale:**
- 迭代：v1 追踪敌列 dc±1 → 净 -3（seed 32 横向追逐振荡）；v2 固定列 base±6 扫描 → 净 -1；v3 固定列 + 玩家列清晰即跳过 → S34 归零、全局 +5（误杀 seed 17/42 有效拦截）；v4 目标列±1 就近 + 四门槛 → 达标。
- 四门槛（由 25/32/47/51/53/56 六败局取证）：(1) 敌已入带（row ≥ 23）禁止站台——须下行堵口；(2) 玩家须已在带内（row ≥ 21）——带外下行回防不得被拽横移；(3) 玩家列与目标列差 ≤ 1 跳过——已在拦截列；(4) 目标横移且与玩家同行跳过——目标将横穿本行，守株待兔。
- 附带修复：`laneCorridorBlocked` 同格退化（r===tr 时 `rr !== tr` 循环永不命中 → 无限下走越界崩溃），加 `if (r === tr) return 0`。

**Implications:** 默认 OFF 保持 v5 字节不变、gate 全绿（1247 pass）。发货开关 = `baseLaneSentryStation: 1`（hard/chaos 池模型），需用户确认后并入 §192 哨兵配置。

## §193-E. 环破回防（ringFallback）— 阴性归档（S34 -2，hard 全关 -54）
> 取证与机制分析 → s34.attempt.md（方向 4）；实验代码已全量回退，树保持字节不变。

**Decision:** 尝试「哨兵第二触发器」：基地危局成立（csb/cbr/inBand）+ 玩家远离基地（dist > 16）时，以 850 权重导航回防（§137 守位格可站目标）。**hard 全关 60-seed 净 -54（35 关仅 2 关 +2、20+ 关为负）、S34 净 -2（4 to-win / 6 to-lose）**——不满足「S34 ≥ +3 或 全关净正」任何一条，归档并全量回退。

**Rationale:**
- 机制学（两处实现缺陷，先后修复）：(1) 最初用 `getDefaultDefensePositionImpl`（(12,23) 环砖格，全 35 关不可达）→ `navigateTowards` 恒 null、分支空转每 tick 刷共享 nav 缓存/重算计时器 → **幻影翻局**（claims=0 仍改变结果，seed 11 首分歧 t3582 距首调 t3002+）；(2) 换 `computeBaseGuardAnchorImpl`（可站可达，每游戏一次缓存）后分支真实生效。
- 生效后仍负：to-lose 机制 = 远距玩家靠场内压制赢的局被跨图拉回拖垮（seed 11：拉回 27s/1660 ticks 未到基地已丢；seed 16：到家仍被 overwhelming；seed 44：半路丢）。to-win 4 局（30/38/42/54）拉回耗时跨度大（30~836 claims）——与 to-lose 无干净分离特征。
- csb-only 变体（仅「下一发毁基地」拉人）更差：S34 净 -3（0 to-win——to-win 恰恰依赖非 csb 拉回）、to-lose 保留。两变体均否决。
- P4 race + S6 leash（maxPlayerDistFromBase 26）仍为既有回防机制；本方向证伪「远距拉回能赢」假设——回防步行机会成本大于基地救援收益。

**Implications:** 实验痕迹全清（params/think/GodAIInput 回退至 §193-D 状态，git 树字节不变）。若未来重试，须先解决「拉回途中阵亡/错失场内压制」——需要更强的触发门（如敌方队列 < N / 玩家 HP 阈值），而非距离门。

## 194. 像素卡死 directMove 兜底 (§190)

**Decision:** 当玩家像素卡死（`_digBlockTicks >= pixelStuckDirectMoveTicks`，默认 **0（已关闭，2026-08-13）**）且无活跃 carve-dig 时，HUNT 分支绕过 A* 寻路，改用 `directMove` 选向。新增参数 `pixelStuckDirectMoveTicks`（**默认 0 = byte-identical（关闭）**，classic 也=0 保持 byte-identical；曾短暂设为 480 但已回退）。

**Rationale:**
- 根因：`replanInterval=1`（hard 默认）导致 A* 每 tick 重规划。敌方移动使 replan 缓存失效（target cell 变化），A* 重算后第一歩方向在 left↔right 间振荡。turn cooldown (50ms) 将这种振荡转化为"来回走但净位移为零"的卡死模式。
- 症状：S35@seed10 玩家在 (1,25) 卡死 30.6s，S2@seed13 卡死 28s，S17@seed12 卡死 30.9s — 全程 `fire=false`，`branch=navigate`，`pathLen` 在 30-32 间反复变化。
- 修复：`directMove` 基于目标相对位置选向（优先垂直），不依赖 A* 路径，方向稳定。阈值 480 ticks (8s) 高于 nav-stuck escape (180 ticks = 3s)，让该机制先生效；低于 10s 告警阈值。
- 300 ticks (5s) 在 chaos S5/S8 引入回归（score 下降 0.07）；480 ticks 消除了回归。
- Classic 设为 0（`replanInterval=50`，路径稳定，无此问题）。

**Implications:**
- 消除 5/17 个 10s+ 静止告警（S2×2, S17, S31, S35）。
- 门禁全绿：hard 0.742 (floor 0.713), chaos 0.716 (floor 0.684), classic 0.875 (floor 0.845)。
- SUITE score 0.5140 (基线 0.5132，在噪声范围内)。
- 剩余 14 个告警的根因不同（`canMoveDir` 的 snap 精度问题、密封口袋、dead-end 走廊），需要不同的修复策略。

**Status (2026-08-13) — DISABLED by default.** Paired A/B on `--difficulty hard` (§190 ON=merged default 480 vs OFF=0, 20 seeds, CRN) proved it is net-negative: suite 0.5308 → **0.5363** (paired Δ +0.0053, **p=0.0185**, B better/worse 12/10). It failed to help its own target seeds — S31 Eagle Nest was actually 80%→85% better with it OFF. The 480 value still regressed hard; the earlier 300 value had regressed chaos S5/S8. Default reverted to 0 so hard/chaos are byte-identical to pre-§190. Gate truth (hard/chaos) re-baselined to §190-OFF. Code path retained, gated by `pixelStuckDirectMoveTicks > 0` for a future, properly-tuned re-introduction.

## 195. 中路钻探粘性驻守 midLaneStickyTicks=90 — S8 Riverbed 钻探败链修复 SHIPPED (2026-08-14)

**Decision:** 新参数 `midLaneStickyTicks`（§164 机制：`laneThreatImpl` 触发时置 `_midLaneStickyHold`，endFrame 递减，`MID_LANE_DEFENSE` 在 hold>0 时跨钻墙间隙保持驻守）。**hard/chaos 默认 90**（classic restore 0 via CLASSIC_MODEL_PARAMS）。60-seed 配对 A/B：hard SUITE 0.5333→0.5380，S8 Riverbed 37%→45%（seeds 1-30 配对 16/30 vs 11/30，L→W×5、W→L×0）；classic/chaos 无回归。门禁全绿（classic 0.875/hard 0.770/chaos 0.733）。

**Rationale:**
- 根因（S8 hard 取证）：中央口袋敌人反复向下凿穿 col 12-13，每发子弹 10-60 ticks 后死于砖墙，间隙 70-130 ticks → §165 midLaneDefense 触发闪烁，玩家走向锚点途中被释放，环砖凿穿后下一发 71 ticks 无障碍弹道 vs 玩家 1px/tick 必输。
- 只加时间粘性、不改弹道触发（§163 敌情触发教训：29/35 关更差）。距离 leash（midLaneMaxDist=8）保留 — 远距败局属另一杠杆（§173 已 closed）。
- 粘性时长双刃剑：90 为扫掠峰值（60→38%、90→45%、120→38%、150→38%、180→33%、240→30%）— 必须 > 间隙桥接，但 ≥120 开始锚定反噬（玩家失去场内压制/捡星节奏）。

**Implications:** 硬关 5/35 关 L→W 转换集中于 S8 式钻探关（1/10/15/18/26）。`midLaneStickyTicks=0` 仍字节恒等关闭。
> 全文 → docs/god-ai-tuning.progress.md §195

## 196. 钻探预警列完整性触发器（drill alarm）— 方向 1 证伪（2026-08-14）

**Decision:** 方向 1（S8 远距群：base column 砖墙破坏事件触发、长间隙钉锚点）**已证伪，回滚，不发货**。hold=1500 A/B 12 W→L / 2 L→W；hold=300 6 W→L / 1 L→W — 均为净负。

**Rationale:**
- 铁证（seed 1 hold=1500）：t901 钻墙破坏 → 玩家被钉 (12,21) 面朝上 866 ticks，期间横向侧射（row 25）经被凿的 (11,24)/(11,25) 环砖打进基地（t1595/t1762/t1767），玩家全程无动于衷。
- 任何「钻探进行中 → 长时间钉锚点」都是 §163 statue 变体：玩家被钉时环周威胁（侧射、拆环司机）失控，远距群 8 局的收益被近基侧射群 12 局损失淹没。
- 长间隙真实解不是钉锚点，而是「别游荡太远」+「拆环司机被处理」— 后者是 §192 baseLaneSentry 的职责（850 权重），但玩家未对齐且距敌 >6 格时不接管（station 导航 baseLaneSentryStation=0 未 A/B）。

**Implications:** 方向 2 升为最高优先：近基错位群 probe（seed 21 侧射铁证已取：t1343 (11,24)、t1376 (11,25) 被凿、t1417 横向弹杀基地）+ baseLaneSentryStation=1 A/B（§193-B 遗留候选）。
> 全文 → docs/god-ai-tuning.progress.md §196

## 197. 带内拆环导航 + 远距开火（baseLaneSentryInBandNav/FarRange）— 方向 2 证伪（2026-08-15）

**Decision:** 方向 2（S8 侧射拆环：sentry 带内导航 + 对齐持位 + 远距开火三件套）**已证伪，回滚，不发货**。S8 60-seed nav-only 32/60（L→W 5, W→L 0）、nav+far 33/60（+8/-2），但全 35 关 paired A/B **净负约 -35 / -65**（s24 34→22/20、s32 49→37/38、s7 48→36…）。

**Rationale:**
- 铁证（s24 s1）：带内持位把玩家钉 (11,21) 25+ ticks（sentry 0→48 claims），首发散点仅 1px，RNG 分流后玩家错过下行拦截，base t2738 死（基线 t4061 clear）。持位 = §196 statue 变体的短时版本 — 任何「冷却期钉玩家」的 claim 都中断场内流动。
- 持位是 S8 修复的必要件（v2 去持位变体 seed 23/29 不绿）也是全关危害件 — 无法拆分；§7 先失败测试曾 3/3 绿（seed 23 t6070 / seed 29 t5139 stageclear）。
- S8 真实败因三分：环侧被凿（侧射）、顶部环被凿（钻探 col 12）、玩家被拽离列位 — 持位只治第三种且副作用全局。

**Implications:** 方向 2 关闭，处置同方向 1（回滚 + 记录）。S8 hard 27/60 保留为已知开放项。后续杠杆优先级：§193-B baseLaneSentryStation=1 A/B（导航无钉住语义）→ 调 midLane claim 条件（降低拽离频率，替代持位）→ 方向 4 沉睡参数筛选。
> 全文 → docs/god-ai-tuning.progress.md §197

## 198. 卫位导航发货（baseLaneSentryStation=1）— SHIPPED（2026-08-15）

**Decision:** `baseLaneSentryStation` 默认 0→1 发货（hard/chaos 池，classic 保持 0）。修复后 A/B：hard 净 +2（6 L→W / 4 W→L）、chaos 净 +7（13/6）、classic 0（byte-identical）；gate 全过（hard 0.779 / chaos 0.733 / classic 0.875）。

**Rationale:**
- 当前基线（§195 sticky=90）确认 §193-B 候选仍净正；三轮证据链（§193-B 旧基线 +6/+2、修复前 +3/+10、修复后 +2/+7）方向一致。
- 发货前修两处 gate 级问题：(1) 门槛 (5) 带内威胁让位 + 门槛 (6) 上行接近让位 — chaos S34 seed 7 score 0.960→0.173（0.401→0.321 跌破 floor 0.351）的元凶是 station 把玩家从带内击杀/上行路径拽去站台，RNG 蝴蝶翻局；(2) **共享 buffer 契约 bug**：`tankCellImpl` 写入 `_tankCellBuf`，门槛循环内再次调用覆盖外层 `tc` 引用（t=34633 实测 tc 值从 (11,21) 变 (11,2)）— 循环前必须快照。此坑与 §193-B 的 laneCorridorBlocked 同格退化同源。
- 拒绝把 station 拆成 per-stage：gate 硬门（chaos S34 恢复）说明修复已够，无需 §81 违背的 per-stage 特例。

**Implications:** §192 哨兵配置完全体（mode=1 + station=1）发货。S8 环侧拆环（§197 方向 2）仍为已知开放项；chaos S34 seed 58 1 局噪声记录在案。
> 全文 → docs/god-ai-tuning.progress.md §198

## 199. S34 站桩取证 + 工具口径 bug（方向 3 证伪 + ab-param 修复）（2026-08-15）

**Decision:** (1) S34 Battlement 站桩修复方向证伪 — `cooldownBlockDrop` 三种变体（fall-through / 换线横移 / armor+2层砖窄化）S34 30-seed 全净负（net -2 / -4 / -1），代码回滚，默认 0 byte-identical。(2) 修复 `tools/diag/ab-param.ts` 口径 bug：`stageIndex` 由真实索引改为 0（官方口径）。

**Rationale:**
- S34 取证（hard 60-seed，stageIndex=0 口径）：43 败局 = 40 base_destroyed；死亡瞬间最后分支 navigate 18（17 局 baseHp=0 — 远处游走时 base 被端）> midLaneDefense 11 > dodge 6 > 站桩类 7（defenseIntercept 4 + t2a 3）> powerup 1。s1 死链：armor@(10,25) 几乎对齐但中心线 (11,24)(11,25) 2 层砖挡 → t2a 开 1 发破砖 → cooldown 模型 74-tick 冷却站桩 → armor 破 1 层打爆 base。**根因是 aimDir 主方向量化 + 冷却站桩 + 破砖竞赛**，任何「离开站桩」的替代（navigate/换线）均被 A/B 证伪 — 与 §133（早回防）/§138（守位格）家族结论一致：S34 防守位行为不可修。
- **口径 bug（重要）**：ab-param 传真实 stageIndex → killScore=1000×1.05^idx（S34 时 ≈5.2×）→ `dropOnScoreMilestone=5000` 道具掉落频率在 hard/chaos 下大幅分叉 → 与 eval/gate/forensics 的 stageIndex=0 口径是**两条不同轨迹**。§193-B/§198/方向 4 的历史 A/B 数字均在分叉口径下测得（发货正确性不受影响 — gate 用 0 口径验证过 station=1）。`stageIndex=0` 对 classic 无影响（`dropOnScoreMilestone=0`）。修复为传 0，未来 A/B 与 gate 严格同口径。

**Implications:** S34 弱关（28%）暂挂 — 修复应在漏斗口上方进攻性拦截/击杀节奏（无现成参数）。历史 A/B 数字（§193-B +6/+2、§198 +2/+7、方向 4 全无信号）均为分叉口径 — 结论方向性仍有效但数字不可与官方口径直接比较；后续 A/B 一律 stageIndex=0。
> 全文 → docs/god-ai-tuning.progress.md §199

## 200. dodgeEscapeDepth 逃逸深度闪避证伪（方向 5 D1）（2026-08-15）

**Decision:** `dodgeEscapeDepth`（双侧垂直候选逃逸深度 < 阈值时改选子弹轴向长逃逸）**全关净负，不发货**，默认 0 保留代码。

**Rationale:**
- 方向 5 取证（tmp/fx-weak-hard.json，240 runs）：S14（Bastion 水带关）23 败全 lives_exhausted（100% 玩家死亡）；seed 60 铁证 — 玩家在 (5,12)↔(5,13) 垂直抖动 93 ticks（向上撞砖 (5,10)、向下撞水 (5,14)，两侧都是 1 格死胡同），fast/armor 持续压制 hp 315→0。根因：dodgeDirection 的 isSafeDir/canMoveDir 只看**一步** — 候选格可走就进，下 tick 侧向堵塞 → 弹回，无限往返。
- 设计：垂直逃逸深度 < `dodgeEscapeDepth`（cells）→ 探测子弹轴向（left/right）长逃逸（深度 ≥ 阈值 + isSafeDir 门控 + §83 不沿子弹行进方向逃）。
- A/B（stageIndex=0 官方口径）：S14 30-seed 阈值扫描 depth=2→-1 / 4→+2 / 6→+2 / **8→+4** / 10→+1；depth=8 的 S14 60-seed **+5**（37→42，L→W 11 / W→L 6）— 局部正信号成立。
- **全关 hard 60-seed 配对（2100 局 ×2，决定性测试）：net=-30**（L→W 172 / W→L 202）。W→L 201 局中 **172 局（86%）base 受损/被端**，仅 29 局玩家死亡 → **弃守假设证实**：逃逸把玩家带离防守位，base 被端。重灾关 s6（-8）/s20（-5）/s15（-5）/s4、s24（-4）/s10、s13、s17、s21（-3）等；正关仅 s14（+5）/s29（+5）/s5（+5）/s2、s18（+4）等。
- **第七个逃跑类参数证伪**（M9 horizon / M10 margin / §86 counter-fire / trapAvoidance / crossfirePathCost / cooldownBlockDrop / 本参）— 模式完全一致：局部救玩家，全局弃守。**「玩家离开位置」在任何版本里几乎从不值得。**

**Implications:** S14 生存类候选穷尽 — 抖动是真问题但修法必须「不弃守」（如逃逸距离上限+立即回防、或 base 威胁门控），鉴于七连负，不继续探索。方向 5 剩余三关（S24/S20/S12，killer 以 basic/power 为主，死亡热点 base 附近 + navigate 期）同属「打猎期生存」类，无现成参数 → **方向 5 暂挂**，与 S34 相同命运。下一候选方向：击杀节奏/清关效率（方向 6 clearSpeed 0.146）或 base 防御的进攻性拦截（无现成参数）。
> 全文 → docs/god-ai-tuning.progress.md §200

## 201. 方向 6（clearSpeed 0.151 慢/拖沓）— 分析性证伪：无 AI 拖沓可修（2026-08-15）

**Decision:** clearSpeed 维度低是 **fireModel 射速设计差异**的物理结果，不是玩家 AI 行为问题 — 无参数可修，方向 6 关闭。

**Rationale:**
- 维度真相：hard clearSpeed 0.151 vs classic 0.817（60-seed 官方口径）— 5 倍差距。
- **射击已达物理上限**：cooldown 模型下玩家 fire interval = 1300ms/1.05 ≈ 1238ms ≈ **74 ticks/发**。实测清关局 fire 占比 1.4% ≈ 理论上限 1/74 = 1.35%（含星加成已超理论）。玩家**无浪费射击窗口**（aggressive 就绪必开火 `!onCooldown && rng.next() >= aimError`，aimError=0；navigate 移动中应开火已含 shouldFireInDir；t2a 站桩 = 冷却物理）。
- **清关局零死亡**：Outpost 18/20 清关局 death=0 — 无重生损耗。shield 731 ticks/run = 出生盾物理。
- **敌人 tier 对照实验（30-seed，同 1 星同 rules）**：Outpost hard tier 6123 vs relax tier 6086（±0.6%）；Twin Spires 7087 vs 7225（±2%）— **敌人 AI 聪明程度对清关时间无影响**。
- 逐项排查 readyNoFire（冷却就绪不开火）33.6%：navigate 31262（走路中无同线敌人 — 几何，接敌路径由 directMove 垂直优先已优化）；powerup/dodge（拿道具/闪避中不开火 — 合理）；aggressive 1575 中 82% 在冷却。
- **根因**：hard/relax/chaos 用 cooldown fireModel（74 ticks/发）+ pool 模型；classic 用 bulletCap（子弹落地立即再发 ≈ 10-15 ticks/发）+ instant。**射速差 5-7 倍 = clearSpeed 5 倍差的全部来源** — 这是难度设计（cooldown 是 modern 统一节奏），非 AI 可调。
- 相关已试候选（均无信号/已证伪）：§170 huntCommit、§171 pathTargetMode（方向 4 35×20 筛选）、§200 dodgeEscapeDepth（全关 -30）。

**Implications:** 「慢/拖沓」是 Phase III 维度信号的误读 — 该维度在 cooldown 难度上结构性偏低，不应作为行为调优目标。God-AI 清关效率（tempo 0.558）的真实杠杆 = 接敌路径效率（§170/§171 已穷尽）与击杀精准（accuracy 0.838 已高）。**建议**：如追求维度公平，应重新校准 hard 的 eval-refs（现用 classic refs 评分）而非调 AI — 属评估层修复，另议。
> 全文 → docs/god-ai-tuning.progress.md §201

## 202. M0 威胁台账取证上线（threat-ledger + failure-classifier）— 硬关 2100 局基线（2026-08-15）

**Decision:** 上线 plan/God-AI-Hard-Breakthrough-Implementation.md 的 M0 取证工具链，零行为变更：
- `simulation-runner` 威胁台账（`threatLedger` 开关，只读采样，签名变更触发，pre-endFrame）— 记录 baseHp/环/分支/玩家格/威胁 ETA/slack/无产出原因/每敌 csb/cbr。
- `failure-classifier`（7 族：late_detection / no_output_commit / multi_threat_overload / turn_locked / travel_late / wrong_target / player_survival + unknown）。
- `threat-ledger` CLI（35 关 × 60 seeds × hard，2100 局，JSON 语料 + 逐 tick 报告 + 族分布 + 最差关）。
- `tests/threat-ledger.test.ts`：ledger 开/关字节一致（parity）+ 每族合成台账分类单测。

**Rationale:**
- 基线复现 plan §1.1 完全吻合：清关 1582/2100（75.3%）；失败 448 base_destroyed / 69 lives_exhausted / 1 timeout vs plan 447/70/1 — ±1 局差异源于 maxTicks 口径（本工具 36000 vs 旧基线 18000），非行为回归。
- 失败族分布：**no_output_commit 311（60.0%）** > multi_threat_overload 99（19.1%）> player_survival 69（13.3%）> travel_late 24（4.6%）> turn_locked 14（2.7%）> unknown 1（0.2%）。base 失败中 no_output 69.4% + multi_threat 22.1%。
- M0 门达成：每族有可复现证据 — 逐 tick 抽查 10 局（含 S34:1、S1:5/13/26/50/58、S8:2、S7:54）分类与台账流完全一致（如 travel_late 铁证：玩家 (8,8) 打猎中，ETA 458 > 威胁 ETA 40，base 行被啃 120→0）。
- 顺带修复：台账 ring 口径 bug — 最初按 4×4 边框 12 格数环，与游戏真环（SimulationCombat.isBaseProtectionCell 8 格）不符，已镜像修齐；JSON 语料压缩（466MB → 85MB，弃每敌 ETA + 紧凑键）。
- parity 测试证明台账零副作用（同 seed 同局字节一致，5 组 stage×seed 验证）。

**Implications:** M0 门通过 → M1 `ThreatBudget`（纯模型，默认 OFF）可开工。头号失败族 no_output_commit（站桩无输出）与 travel_late 共享同一信号：**slack 常态为负**（威胁 ETA < 玩家拦截 ETA）— 玩家移动速度 < 敌人推进速度是设计常数，M1 的威胁预算必须显式处理「赶不上」而不是仅靠 slack 符号。multi_threat_overload（≥2 同时 csb）是第二族 — M2 动作契约的优先级仲裁目标。
> 全文 → docs/god-ai-tuning.progress.md §202

## 203. M1 ThreatBudget 纯模型上线（Phase 1 §5，默认未接线）（2026-08-15）

**Decision:** 新增 `src/ai/god/ThreatBudget.ts` — 只读、确定性、零 RNG 的威胁预算层（plan Phase 1 §5.1-5.3 全部落地），**未接线到任何决策路径**（Phase 2 才接线）。`GodAIInput.ts`/`think.ts` 零改动。

**Rationale:**
- actionEta = nextLegalTurnEta + movementEta + aimAlignmentEta + fireCooldownEta + requiredShotsEta（§5.1）；`turnCooldownMs` 从 `world.rules` 读取、永不被 AI 改写 — 200/500ms 都只改变 ETA 不改变公平规则（测试锁死单调性）。
- 敌人期限（§5.2）：enemyToRingEta / enemyToShootEta（csb→0；cbr→按同轴砖块数×节奏+飞行；否则走到环+固定开销）/ enemyDamageWindow（baseHp ÷ 敌 firepower 的射击次数×节奏）/ enemyUrgency（baseHp + 环完整度 + 射击次数三加权）。
- 玩家期限（§5.3）：killSlack = damageDeadline − playerKillEta；interceptSlack = enemyToRingEta − 到达且瞄准 ETA；missesSecondThreat（其他敌期限早于击杀落地）。
- 镜像纪律：环格集 + 清线谓词是 SimulationCombat.isBaseProtectionCell / SmartThreatModel 几何的逐字镜像，35 关 parity 测试锁死与 AI 谓词一致（§6.2 接线时不会出现「模型说安全、AI 说不安全」的分裂）。
- 数据源全部来自 config（firepower/damage/maxHp 走 resolveProfile、节奏走 tank 冻结的 nextFireInterval/base fireCooldown）— 不硬编码数值。
- 测试（§5.4 最小集全过）：turnCd 200 vs 500 单调、需要转向/更多射击/更远目标 → ETA 不减、baseHp 更低/环砖更少 → urgency 不降 deadline 不长、同 World 同调用字节一致、不消费 world.rng、World 只读。

**Implications:** M0 证据（§202）与 M1 模型闭环：no_output_commit/travel_late 共享的「slack 常态为负」现在有了可解释的逐分量计算（killSlack / interceptSlack / missesSecondThreat），Phase 2 ActionContract 可用「slack 为正且有产出」作为防守/进攻分支的闸门。下一阶段：Phase 2 接线（§6.1 防守分支 slack 门控，默认 OFF + A/B）。
> 全文 → docs/god-ai-tuning.progress.md §203

## 204. M2 ActionContract 防守站桩门控（Phase 2 §6.1，默认 OFF + A/B）（2026-08-15）

**Decision:** 新增 `src/ai/god/ActionContract.ts` — 对防守分支（defenseIntercept 直射/dig、baseLaneSentry dig）的「站桩 + 冷却中」提交做统一行动有效性门控，`actionContractMode` 参数默认 0（OFF，字节一致），1 时启用。站桩仅当下列之一成立才保留：敌人子弹在射击射线（拦截在即）/ 己方子弹在威胁线上（击杀已落地中）/ 站桩击杀 killSlack > 0（射击比威胁期限快）。

**Rationale:**
- plan §6.1 原样执行：不得因「检测到敌人」就提交 `moveDir=null, fire=false` 的静止分支；同时遵守 §199 教训 — 不能盲目 fall-through（所以只挡站桩+冷却，不挡正开火/正移动）。
- 选中位点（think.ts）：defenseIntercept 直射 commit、defenseIntercept dig commit（`continue`）、baseLaneSentry dig commit（`return false`）。midLaneDefense 站桩有 laneThreatImpl||sticky 壳门（按定义有效）不碰；baseLaneSentry 对齐+清线 commit 已有 `if (onCooldown) return false` 天然无站桩。
- 门控只消耗只读 World + ThreatBudget 模型，`mode>0` 拒绝路径跳过 aimError 的 `rng.next()` — 与默认 OFF 的字节一致性兼容（mode 0 短路径零改动）。
- 模型事实（数据源）：基础子弹伤害 = resolveProfile(kind,0).firepower（basic 50/fast 36/power 64/armor 43，baseHp 120 → 2-4 发）；杀敌池伤害 = round(firepower×1.05×2)，敌 maxHp = round(armor×5)；节奏 = 冻结 nextFireInterval / base fireCooldown。

**A/B 证据（hard 35×60，stageIndex=0 官方口径，`bun tools/diag/ab-param.ts --param actionContractMode=1`）：**
- baseW 1582 → candW 1590，**L→W 34 / W→L 26，净 +8**（±~10 局噪声内，方向正确但未达决定性）。
- 机制命中目标族：34 个翻胜中 **26 个是 no_output_commit（占该族 311 局的 8.4%）** + multi_threat 4 + turn_locked 2 + player_survival 1。
- 剩余失败 484/518 局失败 tick 变化 ±200 以内（中性）；no_output 族尾部 11 局 +600（拖后失败）vs 9 局 -600 — 无系统性加速/延迟。
- 最差关 S34（17/60）零翻转 — 其站桩机制不在「站桩+冷却」形态（多为移动中/非冷却态），留给 Phase 3 覆盖点。
- 翻负 26 局无单关集中（最差 S24/S25 各 -2）。

**Implications:** 机制验证通过、信号为正但不足以改变默认 — 按 plan §9/§10 纪律保持 `actionContractMode=0` 默认 OFF，最终 ship 决定留给 Phase 5 全参数 CMA-ES。§6.1 的「冷却期也 fall-through」风险被 §199 教训约束，本版本刻意窄（只 3 个位点）。下一阶段：Phase 2 §6.2 进攻分支 targetValue 排序键（engage/hunt 目标价值随期限/距离/射击成本动态化），同样默认 OFF + A/B。
> 全文 → docs/god-ai-tuning.progress.md §204

## 205. §6.2 targetValue 排序键 — A/B 证伪，保持默认 OFF

**Decision:** `targetValueMode=1`（engage/hunt 目标选择改用 targetValue = 预期基础伤害预防 / 行动时限）在 hard 35×60 A/B 中 **净 −125（baseW 1582 → candW 1457，−6.0pp）**，被证伪。按 plan §9/§10 纪律保持 `targetValueMode=0` 默认 OFF；代码与测试保留（模式 0 字节等价，测试固化语义），记录失败机制供后续阶段参考。

**Rationale:**
- A/B 数据（`tmp/ab-target-value.json`，与 `tmp/threat-baseline-2100.json` 同口径）：
  - L→W 301（恢复的 base 失败：no_output_commit 179 / multi_threat_overload 59 / player_survival 43 / travel_late 12 / turn_locked 5）vs W→L 426（新增失败）→ 净 −125。全 35 关中 31 关负翻转（最差 S18 −14 / S33 −13 / S19 −11 / S24 −11 / S16 −10），S34 崩塌 17/60 → 4/60。
  - 35% 的局（727/2100）行为翻转 — 该键在真实关卡上远非惰性，时刻重排目标。
- 失败机制（结构分析）：
  1. **伤害上限反直觉**：damagePrevented = min(baseHp, fp×interim)，对 horizon−e2s ≥ 3 周期的敌人恒等于 120 → 价值 ≈ 120/horizon，几乎等价于「按杀敌耗时取最近」；而 interim 1-2 的将死敌人只得 50-100/horizon → **被降权**，玩家不补刀（clear 局平均 ticks 全线上升：S12 6543→7875、S22 6569→7634…）。
  2. **e2s > horizon 的敌人 v=0 被剔除** → 玩家被吸引向 cbr 带（ring 砖可及列），反复横跳而非打完整局（S34 崩塌即此）。
  3. horizon 分子分母共用 1/(reach+eta.total) 无归一化，跨分支（canHunt/normal）语义不一。

**Implications:** §6.2 的字面公式作为主排序键被实证否决；「将死目标降权」教训对 Phase 2 §6.3 ActionIntent（意图提交稳定性 + 威胁事件重验）有直接约束 — 补刀/收割应是价值函数的保底项而非惩罚项。下一阶段：Phase 2 §6.3 ActionIntent（默认 OFF + A/B）。
> 全文 → docs/god-ai-tuning.progress.md §205

## 206. §6.3 短期 intent（lease+重验）— A/B 中性偏负，保持默认 OFF

**Decision:** `intentMode=1`（hunt/engage 目标以 ActionIntent 锁定：intentLeaseTicks=12 租约 + 到期/死亡/停滞/期限收紧/新威胁/逃逸 6 重释放 + 10-tick 重验）在 hard 35×60 A/B 中 **净 −14（baseW 1582 → candW 1568，−0.7pp），噪声内**；S34 17→12（−5）偏负。按 plan §9/§10 纪律保持 `intentMode=0` 默认 OFF。

**Rationale:**
- A/B（`tmp/ab-intent.json`，同 §205 口径）：L→W 206 / W→L 220；20% 局（426/2100）翻转 — 释放→重选→重承诺循环在真实对局中大量发生，租约锁只在小窗口内生效。
- 机制诚实评价：§6.3 的 6 重释放条件全部落地且测试固化（8 测试：字节等价/租约内持住/过期重选/目标死亡/新威胁释放/逃逸释放/确定性）；但「释放后落回最近目标并重新承诺」使大多数释放行为学上不可观测（重选=同一目标），仅当场况变化时产生差异 — 这正是 §205 教训的镜像：分支级机制不构成 hard 突破的杠杆。
- 与 §170 huntCommitTicks 的关系：intentMode>0 时自动取代 plain commit（默认均 0，互不干扰）；intent 的差异点（期限/进展/威胁约束）已实现。
- 组合实验（intent+targetValue）留给 Phase 5 CMA-ES，不单轮探索。

**Implications:** Phase 2（§6.1 行动契约 +8 / §6.2 目标价值 −125 / §6.3 intent −14）三机制全部保持 OFF — 分支级微调不是 hard 突破的主杠杆。下一阶段：Phase 3 动态攻击覆盖点（§7：S34/S8 类「回基地驻守反而失去全场压制」，coverageValue 评分 + 6-15 tick lease + 多威胁护栏）。
> 全文 → docs/god-ai-tuning.progress.md §206

## 207. Phase 3 §7 动态攻击覆盖点 — A/B 证伪（S34 崩塌），保持默认 OFF

**Decision:** `coverageMode=1`（无基地威胁但有重大威胁时，coverageValue 评分几何候选点——喉道/列道/射击交点，12-tick lease + 低频重规划 + 3 重护栏）在 hard 35×60 A/B 中 **净 −35（baseW 1582 → candW 1547，−1.7pp），且目标关 S34 崩塌 17/60 → 7/60（−10pp）**。按 plan §7.4/§9 M3 决策门（S34/S8/S24/S20 至少两关改善且无新 ≥5pp 回退）**判定失败，保持 `coverageMode=0` 默认 OFF**；实现与 9 测试保留。

**Rationale:**
- A/B（`tmp/ab-coverage.json`，同口径）：L→W 154 / W→L 189，15.2% 局翻转。S34 −10（17→7）、S12 −5、S30 −5、S08 −4、S25 −4、S14 −5；仅 S15 +7 / S21 +5 / S04 +3 / S11 +3 / S27 +3 小正 — 无一关达到决策门。
- 失败机制（结构分析）：
  1. **ring 未破时防守是纯浪费时间**：cbr 威胁（deadline ≈ 360 < 450 视界）尚需 1-2 发破砖才摸得到基地 — coverage 分支把玩家钉在喉道点，等于放弃清怪节奏（W→L 的 clear 局变慢 + 后期波次叠加即 S34 崩塌的机制）。
  2. **护栏几何条件在真实关卡几乎不触发**：(a) 需 3+ 敌且第二威胁 deadline < killEta（池模型杀敌 killEta ≈ 88 ticks，cbr 威胁 deadline ≥ 180 — 结构上不成立，测试需冻结玩家 6000ms 节奏才可触发）；(c) 需 baseDist > 12 且 returnEta > deadline（race 谓词已强制玩家 ≤ ~6 格，同样结构上罕见）。护栏纪律成立，但正因此「覆盖点只在安全时刻接管」，恰好把最需要压制力的场景排除。
  3. 与 §205/§206 同源教训：分支级接管目标选择不改主循环形态（hunt 节奏），hard 突破仍需结构性杠杆。

**Implications:** Phase 3 首机制证伪 — 「驻守喉道」对 S34 不成立（S34 需要的是清怪节奏本身更快，不是防御姿态）。§7 的候选几何/评分仍可复用于 Phase 5 CMA-ES 参数（若未来有全参数搜索空间），但不再单独开轮。下一里程碑：plan §8 M4 安全吃星（playerSurvival 族已有改善史）或 Phase 5 全参数 CMA-ES 决策。
> 全文 → docs/god-ai-tuning.progress.md §207

## 208. §207 覆盖点实现缺陷审计 — 5 缺陷已修复，机制仍保持 OFF

**Decision:** 复查 §207 A/B 失败时发现 CoveragePlanner 实现存在 5 个缺陷（候选点方向、坦克阻挡、伤害上限、基地邻域、玩家距离护栏），全部修复并用 3 个新测试锁定（先写失败测试复现，再修）。修复后全量 A/B：**net −35 → −7，S34 17→7 → 17→11**——缺陷真实存在且修复有效，但机制仍净负，按 §207 结论保持 `coverageMode=0` 默认 OFF。

**Rationale:**
- **A. per-threat 候选点方向反了**（`t.row − 1` → `t.row + 1`）：原代码把"敌人与 ring 之间"的格子放在敌人**远离基地**一侧，玩家被引离基地（S34 取证：基地被毁时玩家 26 格外的局 8+ 局）。
- **B. clearLane 不检查坦克**：同列双敌时 `prevent()` 对视线被挡的威胁照样计满伤害；候选点可落在坦克占位格上（玩家无法站立）。
- **C. prevent 求和可超 baseHp**（单枪上限缺失）→ 多威胁价值虚高、过度承诺。
- **D. 候选点无基地邻域约束**：20+ 格外"蹲守点"也能赢基线 → 玩家被调去远处，基地失守。
- **E. guardrail (c) 需 `returnEta > deadline` 才挡**：20 格 returnEta≈255 < deadline 360 时不挡 → 远距玩家照样被改道。修复为 baseDist > 12 直接拒绝。
- 修复后 S34 dist>12@loss 比例与 base 相同（~37-41%）——远距失败是 S34 固有模式（4 敌围殴），非 coverage 独有。

**Implications:** 机制层面的 A/B 判定（§207：覆盖点对 S34 是负杠杆）不受修复影响；实现缺陷的修复保留（未来 Phase 5 若把 coverage 参数纳入 CMA-ES，语义已正确）。mode 0 字节等价保持（修复全部在 coverageMode>0 路径）。
> 全文 → docs/god-ai-tuning.progress.md §208

## 209. 覆盖点实现审计第二轮 — 坐标系根因 + (b)/BUG-2 修复，正确实现仍净负（OFF 维持）

**Decision:** 第二轮审计发现 §208 修复未触及根因：CoveragePlanner 混用两套坐标系——候选点/playerCell/BASE_POS/tileMap 是 corner 空间（round(x/CELL)），威胁坐标却用 ThreatBudget.tankCell 的 center 空间（floor((x+w/2)/CELL)），错位半格；坦克是 2×2 footprint，对齐判定必须是子弹带与 footprint 带相交（|Δcol| ≤ 1 或 |Δrow| ≤ 1）。修复：全模块统一 corner 空间 + footprint 带相交 laneAligned + clearLane 扫带并跳过目标坦克 footprint + guardrail (b) 改为 lane 带分离判定（原版 ray 到 BASE_POS 被 ring 砖挡，(b) 在 ring 完好时从不触发）+ 快路径威胁清空必须释放（BUG-2：cur.length===0 时旧代码 return held 死守空点）+ horizon 450→425（实测 cbr 带 300-421、walk 带 ≥435，450 落在 walk 带上抖动）。修复后 A/B hard 35×60：**net −27（§208 是 −7）**，S34 11→13/60——正确实现使 coverage 更常触发、充分暴露机制净负。

**Rationale:**
- 坐标系是根因：单威胁 null（玩家明明对齐却 prevent=0）、DEFECT-B 假阳性、§208 测试"因错误原因通过"（(11,17) 是 walk 带 512 非威胁）都源于此。bullet 物理：子弹从坦克前缘中心出发（bx=x+16−BULLET/2），带宽 BULLET 跨 2 个子块 → 玩家站 corner (c,r) 的子弹带是列 c..c+1，威胁 footprint 是 (tc..tc+1, tr..tr+1)。
- (b) 原实现 `clearLane(威胁→BASE_POS.row)` 在 ring 完好时被 ring 砖永远挡 → independent 恒 false → S34 场景（两列同时逼近）玩家只守一列——这就是 (b) 存在的意义，却从未生效。
- BUG-2：快路径 `cur.length===0 → return held`，威胁全部走远后玩家死守空点（同帧重入即可复现）。
- A/B 三版对比：§207（缺陷实现）−35、§208（半修复，coverage 几乎不触发）−7、§209（正确实现，充分触发）−27。**正确实现的负值比"几乎不触发"更差** = 机制本身负杠杆的实证，非实现缺陷。

**Implications:** 机制判定不变且更强：覆盖点在 hard 全量上净负 → `coverageMode=0` 维持 OFF。§209 修复保留（语义正确，Phase 5 若纳入 CMA-ES 无坐标系陷阱）。测试 14 全绿，新增 DEFECT-F（双 cbr lane ring 完好 → (b) 拒绝）与 BUG-2（同帧重入释放）为真实覆盖。
> 全文 → docs/god-ai-tuning.progress.md §209

## 210. 覆盖点格坐标 round → floor（消除格中点决策振荡）

**Decision:** CoveragePlanner 全部格坐标（cornerCell、laneAligned、tankBlocksCell、clearLane skip 端点、push 占位）从 `Math.round(x/CELL)` 改为 `Math.floor(x/CELL)`；`coveragePlanImpl` 入口重算 pc（调用方传的 playerCell() 是 round 语义，内部统一 floor）。`msToTicks` 的 round 是时间→tick 换算，不属于位置判定，保留。

**Rationale:**
- round 的跳变点在格中点（x = 16k+8）：坦克左上角还在格 c 内时（x ∈ [16c, 16c+16)）round 可能已报 c+1——footprint 判定错位一格（几何错误）；且导航 jitter/碰撞回弹在 16k+8 附近 ±1px 抖动时，判定每 tick 翻转 → laneAligned/clearLane/占位/候选集振荡 → 决策 churn。
- floor 的跳变点恰在真正跨格处（x = 16c），与"左上角所在格"的物理语义同步：格内微动永不翻转判定。注释原文"sub-block containing the tank's top-left corner"的定义本就要求 floor——round 是实现与定义不符。
- 实测（seed 2, hard, stage 0）：威胁 x 在 183.5↔184.5（corner 11 中点）抖动时，round 版计划在 (12,21)↔null 间翻转；floor 版恒定 (12,21)。

**Implications:** A/B hard 35×60：round 版 net −27（S34 13/60）→ floor 版 net −31（S34 13/60）。stage 级散布对称（better 25 vs worse 29，最大单关 ±7）——噪声级，无系统性回归；机制净负判定（§209）不变。新增 §210 防回归测试：威胁格中点抖动 4 帧断言计划恒定（round 版必失败，已验证）。
> 全文 → docs/god-ai-tuning.progress.md §210

## 211. 覆盖点负翻转 per-seed 取证 — 蝴蝶效应根因，csb/cbr 过滤修复证伪（OFF 维持）

**Decision:** 用 per-seed tick-diff 诊断 coverageMode=1 的 120 个 W→L 翻转。结论：翻转根因是 **coverage 在玩家已处于有效拦截轨迹时改道造成的相位蝴蝶**（非机制级缺陷）；"威胁集只保留 csb/cbr"的修复尝试 A/B 证伪（22/4200 runs 变化，净 −3，S34 一例负向），已回滚。coverageMode 维持默认 0（OFF）。

**Rationale:**
- 120 个 W→L 分布：stage idx 5 与 30 各 13（最多）、19 (9)、8/33 (7)；L→W 89 个。对 3 个代表种子逐 tick 取证：
  - **S6-11（真 W→L）**：首个玩家行为分歧 tick 2380——base 版玩家 (8,22) 北上 col 8 拦截 basic@(10,12)（selectTarget (10,12)，mv up）；coverage 版被拉去 (10,22) 守点。2550 两版轨迹几乎汇合（相位差 ≈0.3 格），但 tick 2595 A 版在 (10,12) 停 17 tick 击杀、B 版停 2 tick；2666 A 版 (12,10.07) 转 down、B 版 (11.34,10) 继续 right；2669 敌人 roster 首次分叉（basic (14,10) vs (13,10)）；2751 两版世界已不同（base 有 basic@(14,15) 逼近、isBaseUnderThreat true；cand 无此敌，baseLaneSentry 劫持转向）。base 版 1500 后基地不再掉血、stageclear 5842；cand 版 3282 掉 70→34、4519 掉 34→0 gameover。玩家 HP 事件两版完全相同（283/670/1332）——**差异全在敌人链，纯蝴蝶**。
  - **S20-5 / S31-1**：初判为同类改道，进一步取证发现 S20-5 双 W、S31-1 是 L→W（cand 反而赢）——coverage 介入同样产生相位差，但方向随机。
- 修复尝试："deadline < 425 且 csb/cbr 才入威胁集"。想法：walk 带敌人（deadline 纯几何、无视地形，如 S31-1 armor@(10,0) d346）不该触发守点。实测：A/B v4 net −33（vs v3 −31），仅 22/4200 runs 结果变化、净 −3，S34 唯一变化负向（cand|33|38: W→L）。**原因：deadline < 425 的威胁绝大多数已是 cbr/csb（S6-11 的 basic@(10,12) 在 2383 已变 cbr）——过滤几乎不改变行为**。回滚。
- 结论：四轮 A/B（−7 / −27 / −31 / −33）全部净负且每次"正确性修复"后仍是噪声级负值——机制本身与 hunt 的路线选择差异被确定性 RNG 放大成胜负翻转，不存在可修的系统性缺陷。S34（idx 33）是唯一稳定正信号关（13/60 vs base 7/60）。

**Implications:** coverageMode=0 维持默认。机制保留为实验（S34 场景有局部正信号）；任何未来启用尝试必须 A/B hard 35×60 全绿。§211 取证方法（per-seed-diff dump + decision-probe 分支对比 + 敌人 roster 签名）沉淀为负翻转诊断的标准流程。
> 全文 → docs/god-ai-tuning.progress.md §211

## 212. M4 安全吃星 — 诊断先行，收益空间不足（不提高 pickup 权重）

**Decision:** 按 plan/God-AI-Hard-Breakthrough-Implementation.md §8 纪律（"先做诊断，不要直接提高 pickup 权重"），对 M4 安全吃星执行全量诊断（hard 35×60 + 518 失败局 star census）。结论：**星级与胜负的相关是"输 → 击杀少 → 星少"的结果链，不是"星少 → 输"的因果；失败局中不存在可观的 safe-opportunity 遗漏空间**。不提高 pickup 权重、不扩 starRush 范围；机制维持现状（pickupPriority 族 SHIPPED 默认、starRushMode=0）。

**Rationale:**
- **星级与胜负**：胜局 avgFinalLevel=1.43 / avgStarsPicked=0.61 vs 败局 1.19 / 0.31。但弱关反证：S34（28% 最弱）通关局 avgLevel=1.53（全场第 4 高）、S8 avgLevel=1.48——**弱关失败不是缺星**。
- **失败局 star 供给**：518 失败局中 **355 局（69%）整局无 star 掉落**（击杀少 → 掉落机会少：败局 avgKills 9.95 vs 胜局 17.94）；掉落的 194 个 star 中玩家已捡 127（65%）。
- **遗漏分析**：67 个未捡 star 中，玩家曾进入 4 格内未捡仅 4 例、6 格内未捡 27 例且其中仅 6 例发生在早段（<50% 局长）；其余 star 掉在玩家活动区外（minDist>6 格 40 例）。"该捡没捡"的干预样本量不足以支撑 A/B 显著性。
- **M4 前置条件**：breakthrough plan §10 写明 "M4 只有 M2/M3 通过后才执行"；M2/M3 均证伪（§205/§206/§207-§211）——本诊断进一步显示 M4 直接干预同样没有杠杆。
- 新增 `powerupCensus`（simulation-runner + sim-worker，flag-gated 纯观察）：逐 star 记录 spawnTick/picked/minDist/despawnTick，行为字节等价（默认关，check 全绿）。

**Implications:** M4 以诊断收口（§212），不进入参数扫描。Phase 4 结论：吃星不是 hard 突破的主杠杆；瓶颈仍在击杀节奏（§207 S34 结论一致）与基地防守（§198 已 SHIPPED）。诊断工具 `tools/diag/m4-diagnose.ts` + `powerupCensus` 沉淀为道具类机制的标准诊断流程（掉落供给 → 拾取率 → 遗漏分布 → 时间竞争）。
> 全文 → docs/god-ai-tuning.progress.md §212

## 213. Phase 5 CMA-ES — 启动决策与搜索空间重定义（§9.1 条件未满足，用户指示启动，按协议执行）

**Decision:** 按用户指示启动 Phase 5 CMA-ES（hard 主目标）。记录 plan §9.1 条件偏差：Phase 0-2（§6.1/§6.2/§6.3）与 M3/M4 均证伪/收口，无"新结构改善某类失败"的证明——完整 CMA-ES 的 plan 前置未满足。因此本纪元按 §9.3 协议**保守执行**：20 seeds 方向筛选 → 60 seeds CRN 配对 → holdout；遵守 §9.4 采纳标准（不满足即保留 DEFAULT）+ §9.3.5 停止条件（3-5 批候选仍 ±1-2pp 噪声即确认参数面无 ROI 并收口）。

**Rationale:**
- 搜索空间重定义（§9.2）：剔除 `aimError`、`suboptimalPathProb`（game-feel，plan 明确排除）；保留 19 个当前行为结构实际使用的参数（reactionDelay / defenseRowOffset / defenseColSpread / threatRangeCells / maxPlayerDistFromBase / t8MaxInterceptDistCells / baseWallScanRadius / replanInterval / powerupMaxDivertDistance / endgameEnemyThreshold / huntAllyCount / baseRaceRange* / outnumbered* / campTimeoutTicks / t2aHighHpMaxRange）。**init 全部同步当前 DEFAULT**（旧空间 21 个 init 中 19 个与 DEFAULT 失同步——v2 纪元遗留，CMA-ES 从错误起点出发）。
- M1-M4 新机制参数（threat slack 余量、防守 lease、释放 ticks、覆盖点最小收益、安全拾星绕行成本）不纳入：§9.2 要求"只包含当前行为结构中确实使用的参数"，上述机制全部默认 OFF；`powerupMaxDivertDistance` 是 §87 SHIPPED 的拾星绕行实现，保留。
- 排除 `turnCooldownMs`（§9.2 明确排除，不变）。

**Implications:** 若 3-5 批候选仍噪声（§9.3.5），Phase 5 以"参数面无 ROI"收口，DEFAULT 不变——与 classic 纪元 §67 调参冻结、v2 纪元 M11 的结论一致。任何候选必须通过 §9.4 全部六项才 ship（base_destroyed 率下降、最弱关无 ≥5pp 回归、classic 无显著回归等）。
> 全文 → docs/god-ai-tuning.progress.md §213

## 214. Phase 5 CMA-ES — 参数面无 ROI，三批候选全部噪声，维持 DEFAULT（停止条件 §9.3.5 触发）

**Decision:** Phase 5 收口。两轮 CMA-ES 方向筛选（20 seeds，batch 1 与 warm-start batch 2 均收敛到同一方向：defenseRowOffset 1→3、baseRaceMarginCells 2→6、threatRangeCells 下调、powerupMaxDivertDistance 下调、outnumbered 族下调）在 60 seeds CRN 配对下**全部落到 ±1pp 噪声内**：batch 1 net +17/2100（+0.8pp，303 L→W vs 286 W→L）、batch 2 net +9/2100（+0.4pp）、batch 3（仅两个核心参数）net −16/2100（−0.8pp）。§9.3.5 停止条件（3-5 批候选仍 ±1-2pp 噪声）触发：**参数面没有足够 ROI，DEFAULT_GOD_AI_PARAMS 不变，不发货任何候选**。

**Rationale:**
- **筛选与确认的落差**：两轮 20-seed 筛选都显示 +37/+43 fitness、+3pp 胜率、floor penalty 大幅下降——但 60-seed 确认全部消失。根因：20 seeds 下 floor penalty（minStageWin 在 20-45% 间剧烈摆动）和胜率噪声（±2pp）主导了 fitness，筛选指标系统性高估真实收益。
- **§9.4 采纳标准逐项未满足**：无候选 hard 胜率显著提升；batch 1 最弱关 S34 17→16、batch 2 17→15（未达 ≥5pp 回归门槛但方向负面）；改善关数 ≈ 回归关数（L→W ≈ W→L）。无候选可 ship。
- **三批一致性证明**：收敛方向在 60 seeds 下无信号（batch 3 净负），说明该方向是 20-seed 噪声的产物，不是真实参数面梯度。
- **与历史结论同构**：classic 纪元 §67（多轮 CMA-ES 探针 ±1pp 噪声内，正式停止调参）、v2 纪元 M11 结论一致——**参数面在 hard 下同样已穷尽**。God AI 的剩余提升需要结构性机制（behavior/architecture），不是阈值微调；而 M2-M4 结构性机制均证伪（§205/§206/§207-§211/§212）。
- 新增 `tools/diag/ab-multi-param.ts`：多参数候选 CRN 配对 A/B（ab-param.ts 的泛化，Phase 5 验证工具）。

**Implications:** Phase 5 正式关闭。搜索空间重定义（§213）与三批 A/B 数据保留在 `.workbuddy/optimization-phase5*`；后续若引入新行为结构（如 §211 遗留的 S34 coverage 局部正信号、§198 之后的防守机制），可从此搜索空间 warm-start 重启。God-AI 主战场的结论链完整：**M1-M4 结构性机制 + 参数微调均无 ROI，当前 hard 基准（SUITE 0.5132 / 胜率 73%）即参数面与行为面的局部最优**。
> 全文 → docs/god-ai-tuning.progress.md §214

## 215. Hard 开放测试第 1 轮: M0–M3 通过, idle 因果证伪(STATUS: 完成)

**Decision:** God-AI-Hard 开放测试协议第 1 轮(M0–M3)执行完毕。M0 共享口径(`tools/lib/stage-spec.ts`,6 工具接入)+ **2100 局 byte-identical 实证**(HEAD vs 工作树,outcome mix 与 518 条失败 forensics 逐字段相等,默认行为不变);M1 ThreatBudget 语义分层、M2 ActionContract 拦截段 + intent 修复(`<0` 释放仅对 directThreat 生效,`EnemyDeadline.directThreat` 新字段)全部默认 OFF;M3 反事实工具 `tools/diag/counterfactual-idle.ts`(四分支 replay: continue/turn-and-fire/move-to-intercept/clear-or-advance)在 40 局 base_destroyed 抽样上 14 事件人工抽查 14/14 一致——**idle_causal 仅 14.3%,协议 §6.3 条款生效: 不得再以消灭 idle alert 为主线**;主导机理是"干预窗口在静止发生前已关闭"(8/14 事件 commit 时 slack −81..−573,根因在更早的选靶/路线/转向),且 26/40 局首次受伤前 600t 内无静止段(no_output_commit 69.4% 的表观占比严重高估 idle 因果地位)。另: §213/§214 的 CMA-ES 筛选因旧 parser `--stages 1-35` 实际只跑 S1,"35 关参数面无 ROI"表述作废(60-seed 确认口径正确,"S1 局部方向无 ROI"与 DEFAULT 不变的决定仍成立)。

**Rationale:**
- byte-identical 用 git worktree @HEAD 双跑对比;重建的 `tmp/open-test-forensics-baseline.json` 与上轮记载聚合完全一致(75.3%/447/70)。
- replay 分支必须匹配 corpus 的 stageIndex(=0):loadStageData 的 N 影响 score-milestone 掉落 → RNG 流 → 整局发散,已用 `--stage-index` 钉死。
- M3 的"开火 ≠ 更优"反例(S30s27 t&f dmg@190 vs cont 无伤): 朝威胁开火会打掉自家环砖——M4 候选评估必须包含此类反例。

**Implications:** M1–M3 通过 → M4(统一行动候选最小原型)允许启动;候选集设计以 M3 结论为输入(决策点前移 + 开火反例约束),而非消除静止。§214 的"参数面已穷尽"范围收窄为"S1 局部方向已穷尽",重开参数搜索必须走新 parser 的口径行。
> 全文 → docs/god-ai-tuning.progress.md §215

## 216. M4 统一行动候选 — paired A/B 净 −116, 方向否决 (STATUS: 完成)

**Decision:** M4 (candidateMode=1) 在 hard 35×60 CRN paired A/B 中 baseW 1582/2100 (75.3%) → candW 1466/2100 (69.8%), net −116 (L→W 222 / W→L 338, 翻转率 26.7%)。按协议 §8.3 判定 **reject implementation**: 主筛选即明确负信号,不进入 holdout;DoD "充分证据否决该方向" 成立。candidateMode 保持默认 0 (OFF), 出货行为 byte-identical。

**Rationale:**
- 30/35 关净负、15 关 −9..−19 级负翻转 vs 仅 S18/S24 +3 噪声正信号;§8.3 禁止把筛选噪声写成行为改善。
- 翻转子集取证 (n=338): 91.1% base_destroyed;末 10 tick candidate 分支仅 ~6% —— 多数翻转是站桩/拦截的**机会成本**,而非直接开错火;实锤 M3 结论 2 (窗口在静止前已关闭,提前占位 ≠ 提前得手)。
- 2 局玩家自毁基地: S30s27 类风险 (站桩开火被闪避后命中自家基地) 在 candidateMode 下真实存在,且 fireRayBlocked 门 (scan 线) 无法覆盖子弹 6px 弹道差异 —— 排除"收紧门控重试"的简单路径。
- 不动 200ms 公平规则、不引入依赖、无新模块级可变状态;所有门控修复 (standingShot/fireRayBlocked/blockedRay) 留在树内,候选层默认 OFF。

**Implications:** 静止段干预方向整体证伪 (M3 14.3% + M4 净 −116 双重证据);若继续开放测试,下一候选必须以 M3 结论 1/4 的"决策点前移" (travel/turn 段) 为输入,而非消除静止。协议 §9 CMA-ES 重启条件中 "小型结构候选有可重复正向信号" 仍未满足。

> 全文 → docs/god-ai-tuning.progress.md §216 (含协议 §8.3 完整报告模板)。

## 217. M5 travel 段火力偏离 — 诊断先行, 机会空间 33% (STATUS: 实现中, 未 A/B)

**Decision:** 按 §216 否决与 M3 结论 2 (窗口在 travel/turn 段, 非静止段), 新候选
`fireLineDetourMode` (默认 0): HUNT 旅行中对齐 + 射线全清 + killSlack>13 的目标
(csb/cbr/基地逼近带) 一次转弯开火, 不打断导航超过一个转弯窗。纯几何谓词
`travelFireDetourDir` (ActionCandidates.ts), 无 RNG 扰动, byte-identical 保持。

**Rationale:**
- 诊断 (travel-fire-probe.ts, 60 局 baseline 败局抽样): 33.3% 败局有 ≥1 机会 (mean 10.3 tick),
  75% 先于首次基地受伤, 100% 在 navigate — 空间是 idle 段 (14.3%) 的 2.4×, 与 M3 机理一致。
- 目标覆盖规则用 csb/cbr/逼近带 (row ≥ 20, |col−12| ≤ 6) — csb/cbr 几何上全部落入该带,
  带规则是其超集; 探针显示机会 94% 是带内 fb (S3s46 类游走威胁), 6% cbr。
- S30s27 安全: 走廊检查 (任何非空地形含 base 格) + fireRayBlocked (环+基地) 双重门;
  该 detour 不改变 dodge/t8/aggro 等上层分支 (位于 HUNT 内部, 权重序在其下)。

**Implications:** 若 hard 35×60 CRN paired A/B 正信号 (net ≥ +20 且无最弱关崩塌),
进入 holdout + guardrail; 否则按 §8.3 否决并归档。§9 CMA-ES 重启条件待其确认。

> 全文 → docs/god-ai-tuning.progress.md §217 (含探针方法学与机会空间明细)。

## 218. M5 travel 段火力偏离 (fireLineDetourMode) — 三批全正向, 候选通过初步 A/B → gated rollout (STATUS: 完成)

**Decision:** 按 §217 实现 + 三批独立语料 paired A/B (hard 35×60 CRN, stageIndex=0):
primary +12 / holdout +16 / b3 +14, 合计 **+42/6300 (+0.67pp)**, 翻转 183:141 (~2.3σ);
最弱关 S34 净 +1 无崩塌, S24/S31/S22 反复受益, S19/S13 稳定小亏无崩塌。
**verdict: 候选通过初步 A/B, 允许 gated rollout (NOT shipped)** — 3/3 语料正向
+ holdout 复现 + classic guardrail 通过; 协议 §10 DoD "候选在 hard 35×60 paired A/B
有明确正向信号" 由此满足。按评审要求 (2026-08-17): "ship candidate" 措辞降级为
gated rollout — 仅在 candidate-on 完整验证确认 base_destroyed / 最弱关 / lives /
clear speed 无恶化后才考虑默认打开 (届时再升格为 ship)。
`fireLineDetourMode` 保持默认 0 (独立 flag, 协议 §11), 出货行为 byte-identical。

**Rationale:**
- 97 局 W→L 翻转取证: killer mix 正常; 2 局玩家自毁的致命弹均为 t2a 分支 (§216 老类,
  baseline 同概率), 非 detour 直发 — detour 走廊+射线门 6300 局 0 漏。
- classic guardrail 0 flips 系结构性 no-op (探针 gateFail 归因: classic 敌人不入基地带),
  属预期非缺陷; chaos guardrail (35×20) 在修正实验工具后补跑, 记录于 §219。
- turn+fire 公平性语义 (评审 P1): detour commit 与人类同帧 "转向+开火" 完全同构 —
  引擎先设 dir 再开火 (子弹沿新方向立即生成), 200ms 转向冷却只推迟视觉转向;
  "13t 转弯窗" 是未来移动成本, 开火立即生效, 无公平性违例 (tests §218 3 用例钉住)。
- §9 条款 5 (连续 3–5 批 ±1–2pp 且 holdout 无信号才停止) 不适用 — holdout +16 有信号。

**Implications:** 协议 §9 CMA-ES 重启条件满足 (小型结构候选有可重复正向信号)。
后继候选按证据排序: ① defenseIntercept 开火窗口 (反事实 6/14 最大未动类, 与 M5 同构)
→ ② CMA-ES 结构参数面 (slack 边际/intent 租约/coverage 最小收益/多威胁 opportunity cost/
道具绕行) → ③ dodge 分支 idle 取证 → ④ "太迟"防御结构分析 → ⑤ t2a 自毁守卫重论证。

> 全文 → docs/god-ai-tuning.progress.md §218 (含三批明细表、翻转取证、后继计划)。

## 219. 评审 P1 修复轮 (实验工具可信度 + 4 处 AI 缺陷) (STATUS: 完成)

**Decision:** 2026-08-17 open-test round-1 评审的 6 项 P1 全部落地:

1. **实验 hash/seed 错标** (`tools/lib/stage-spec.ts` + `ab-param.ts`/`ab-multi-param.ts`
   + `eval-suite.ts`): `RunHeaderInfo` 新增 `paramsB`, 有候选时打印
   `paramsA=<hash> paramsB=<hash>` (原只打 baseline hash 并写死 `/60`);
   表格行用 `seeds.length` 而非硬编码 60。实测:
   `paramsA=f5cc0288 paramsB=493a5081`。
2. **enemyBulletOnRay 几何误判** (`ActionContract.ts`): 原代码 (评审指出的 P1) 不保证
   子弹与玩家在基地同侧, 也不要求交汇点落在玩家—基地区间内 — 玩家在基地右方朝左、
   敌弹从基地左方横穿, 仍被判可拦截。新增同侧门 + 交汇点门 (baseInFront 与
   playerEdge 判定, 两道 `continue`)。3 个回归用例 (东侧横穿→false / 西侧镜像→false /
   同侧追尾→true), 旧代码下 2 个 repro 失败 (tests/godai-action-contract.test.ts, 21 pass)。
3. **counterfactual-idle RNG 终态** (`tools/diag/counterfactual-idle.ts`): `rngStateEnd`
   原在分支循环前读取, 改为循环后 (break 路径也在 break 后)。
4. **M4 standing 缺 second-threat 门** (`ActionCandidates.ts`): standing kill-current /
   standing intercept 补 `missesSecondThreat` 门 (`killValid`/`interceptValid` 各含
   `!secondThreat`), 4 处选择点 `secondThreatRisk` 由硬编码 false 改为派生值;
   拒绝测试 (tests/godai-candidates.test.ts, 11 pass, 旧代码下失败)。
5. **M5 200ms 转向/开火语义澄清** (非缺陷, 记录在案): 引擎同帧先设 `p.dir` 再
   `tryFire` — 子弹沿新方向立即生成; 200ms 冷却仅推迟视觉转向 (回退 dir + halt)。
   人类同帧 "转向+开火" 走同一路径 → AI 无额外能力, 无公平性违例; "13t 转弯窗"
   是未来移动成本, 开火立即生效。3 用例钉住 (谓词 active-cooldown 提交 / think
   真实接线 / 人类输入字节级同构, tests/godai-travel-fire.test.ts, 13 pass)。
6. **门禁重跑 (修正打印后)**: hard 35×60 net **+12** (1582→1594, 66:54 — 与
   primary 语料逐局一致, 证明打印 bug 不影响运行内容); classic 35×20 net **0**
   (0 flips, 结构性 no-op 复确认); **chaos 35×20 首次护栏**: net **+8** (494→502,
   25:17), 最差单关 S12 −2, 无 ≥3 崩塌 — 通过。artifact: tmp/m5-rerun-{hard,classic,chaos}.json。

**Rationale:**
- 评审核心关切: 实验记录的可信度优先于任何新实验 — 错标 hash/seed 会让所有 A/B
  结论不可审计; 修复优先于 CMA-ES (§9 暂缓)。
- chaos 护栏通过 (净正) 且无崩塌 — §218 "gated rollout" 的候补条件补全; 默认
  `fireLineDetourMode=0` 不变, 仅在 candidate-on 完整验证 (base_destroyed/最弱关/
  lives/clear speed) 无恶化后才考虑默认打开。
- 措辞修正: "ship candidate" → "候选通过初步 A/B, 允许 gated rollout"; chaos
  "无行为差异可测" 的错误断言由真实 artifact 替换。

**Implications:** 实验工具链恢复可审计; 4 处 AI 缺陷修复全部有回归测试;
M5 候选状态明确为 gated rollout, 后继按 §218 Implications 顺序推进。

> 全文 → docs/god-ai-tuning.progress.md §219 (含 chaos 分关明细)。

## 220. defenseIntercept 开火窗口 (actionContractMode 独立 A/B) — 三线微负, 方向否决 (STATUS: 完成)

**Decision:** `actionContractMode` (M2 站桩契约, 5 个防守站桩点) 独立 A/B 后 reject —
hard 35×60 net −7 (120:127), chaos 35×20 net −2 (45:47), classic 结构性 no-op;
W→L 翻转 127 局中 92% 是 base_destroyed (基线 ~73–80%) — 放弃站桩让玩家移开防线,
代价是送基地。参数维持默认 0, 不修改代码。

**Rationale:**
- 与既有证据链闭合: §215 M3 结论 1 (idle_causal 仅 14.3%, 不得以消灭 idle 为主线 —
  本 A/B 高翻转≈无关噪声, 不产净益, 独立复证) + §216 M4 整包 −116 (含同语义) +
  §218 高危标注 ("改错直接送基地")。
- 反事实 6/14 事件中仅 2 例干预有效 (S17s38/S28s26), 2 例太晚, 2 例无需 — 干预
  窗口真实存在但占比低, 且"放弃站桩"不是正确的干预形态。

**Implications:** ① 正式关闭。defenseIntercept 窗口问题不再以"放弃站桩"形态追;
若重启, 应从"开火窗口"本义 (冷却恢复前最后 ~13t 的 killSlack 判定, M5 同构) 入手。
后继顺序: ② CMA-ES (工具链已可信) → ③ dodge idle 取证 → ④ "太迟"防御结构 → ⑤
t2a 自毁守卫。

> 全文 → docs/god-ai-tuning.progress.md §220 (含三线明细表与翻转取证)。

## 221. 评审 P2 修复 + M5 candidate-on 完整验证 (STATUS: 完成, M5 升格待拍板)

**Decision:** 评审 P2 两项缺陷已修: ① fireRayBlocked/firstBrickOnRay 同格死循环
(零长度射线提前返回, 回归测试 + 旧代码挂起验证); ② ActionCandidates/ThreatBudget
热路径 scratch 化 (caller-owned out 参数 + 模块级缓冲, 零 per-tick 分配; M5 cand
350 局逐局 0 差异 = 字节级等价)。M5 candidate-on 完整验证 (hard 35×60): base_destroyed
447→433 (−14 改善), lives/clear speed 全关中性, 最弱关 S34 wins +1 无崩塌 →
**§218 gated 四项条款全部满足**, 升格 ship (默认打开) 的权限已解锁, 但默认打开
改变所有玩家体验, 留待 human 拍板; 未拍板前 fireLineDetourMode 默认 0 不变。

**Rationale:**
- 死循环: 同格时 step=−1, 循环永不终止 (§14 纪律 + 正确性); 修复 = 零长度射线
  无中间格, 提前返回语义正确。
- scratch 化: 候选层开启即每 tick 进入热路径, 每调用一次对象分配违反 §14.1/14.2;
  保持 API 兼容 (不传 out 行为不变), 等价性用逐局比对实证而非假设。
- M5 验证: base_destroyed 改善 + lives/clear speed 中性 = gated 条款"无恶化"满足;
  S34 lives 2.88→2.67 为 18 局小样本, 幅度 ~M4 否决理由的 1/13, 不构成否决。

**Implications:** fireLineDetourMode 升格 ship 待拍板; 顺手修 ab-param lives 记录 bug
(需 task telemetry: true); handoff 文档重写为单一可信版本。后继: ② CMA-ES →
③ dodge idle 取证 → ④ "太迟"防御结构 → ⑤ t2a 自毁守卫。

> 全文 → docs/god-ai-tuning.progress.md §221 (含四项验证表与 scratch 化明细)。

## 222. CMA-ES 重启 — 全 stage 口径 (STATUS: 收口, 参数面无 ROI 确认)

**Decision:** 重启 §218 Implications ② CMA-ES 标量参数搜索, 但口径修正为
**真 35 关** (旧 §214 三批筛选因 stage parser bug 实际只在 S1 单关跑, "参数面
穷尽"结论作废重验)。搜索空间沿用 §213 的 19 参数 (init 已同步 DEFAULT, 排除
game-feel 参数与 turnCooldownMs; M1-M5 机制参数默认 OFF 不纳入)。协议 §9.3 保守
执行: 筛选 (20 seeds × 35 关 × λ13 × 8 代) → bestParams 用 ab-multi-param 做
**60-seed CRN 确认** (真 35 关 × 60 seeds × 2 臂); 确认 ±1-2pp 噪声即停 (§9.3.5),
不得仅凭筛选 fitness 发货。

**Rationale:**
- §215 勘误: §214 停止条件在单关过拟合口径下触发, 证据无效, 需全 stage 重验。
- 工具链已可信 (M0.1 统一 parseStageSpec + §219 hash/seed 口径行)。
- 筛选高估问题 (20 seeds 噪声) 已知, 以 60-seed CRN 确认为唯一发货依据。

**Implications:** 若 60-seed 确认无信号 → 正式确认 hard 标量参数面已穷尽, 收口
并转向结构性机制; 若有信号 → 按 §218 verdict 流程评估。

> 全文 → docs/god-ai-tuning.progress.md §222 (执行中)。

## 223. ③ dodge idle 取证收口 (STATUS: 完成, 候选方向待拍板)

**Decision:** dodge 分支 0% idle (518 失败局 5180 ticks 全在移动闪避) — "dodge
idle" 字面不存在, idle 修复对 dodge 无意义 (§215 M3 证伪终审)。但死亡窗口反事实
(315 死亡局, clone@T−60, 4 分支) 显示 **85.6% dodge 死亡局在死亡前 60 ticks 有
可干预窗口**, hard-away (远离弹群质心) 存活 75.3% vs 当前单弹闪避 0% — 闪避
路径选择有 ~12pp 改善空间。不可救 14.4% 归入 travel 段决策问题 (④)。

**Rationale:**
- 取证纪律 (§218 ③): 先确认窗口再设计 — 窗口已确认存在, 且方向明确 (质心远离)。
- M3/M9/M10/M12 的 hard+/chaos− 签名: dodge 增强候选必须以 hard 为主口径 +
  chaos 护栏 + S26 确定性回归复查。
- 顺带发现 t2a 末段 72% idle (442/611) — 记录为新嫌疑面, 不并入本候选。

**Implications:** 候选空间 = dodge 闪避方向升级 (多弹时质心远离, 单弹保持现状)
或 dodge+对枪 (hard pool combat 一发击杀使对枪与 chaos 结论不同, 须独立 A/B)。
由 human 拍板是否进入候选设计, 或按 §218 顺序先进 ④ "太迟"防御结构。

> 全文 → docs/god-ai-tuning.progress.md §223。

## 224. 候选 A: dodgeCentroidMode 否决 (STATUS: 完成)

**Decision:** §223 反事实 hard-away 方向设计为 dodgeCentroidMode (多弹质心远离),
hard 35×60 net 0 / 0 翻转 / 8645 dodge ticks 方向 0 差异 → 否决, 默认 0 不变。

**Rationale:**
- 基地门 (slack=0, 防 S10s6 逃逸) 使质心远离与 legacy base-closer 选择完全等价:
  弹群在上方 (常态) → away=朝基地=legacy; 弹群在下方 → away 被基地门拒绝。
- 无门版 = M9 horizon 持续逃离, 已被 chaos −3.5pp 否决 (生存↑基地/效率↓)。
- dodge 增强族 (M3 对枪 / M9 horizon / M10 门控 / 本次) 四度确认 hard 无杠杆。

**Implications:** 关闭 ③ 候选设计。§223 的"可干预窗口"结论修正为: 窗口存在但
短窗伪影 (逃=丢基地)。后继: ④ "太迟"防御结构 (travel 段无火力机会的 67% 败局)。
> 全文 → docs/god-ai-tuning.progress.md §224。

## 225. 后继 ④ "太迟"防御结构审计收口 (STATUS: 完成)

**Decision:** 抽样 40 局 base_destroyed + threatLedger 重放, 收口"太迟"机制:
窗口中位 271 ticks (4.5s); 玩家 0% absent/stationary (人在动); 但 sentry 仅
37.5% 局触发、中位 2 ticks, 62.5% 局窗口内 0 tick — **sentry 机制空白**: 带内
敌人 (row ≥ 23) + 玩家 lane 外 → 无哨兵路径 → navigate 盲跑。候选: A 带内
应急进 lane 导航 (推荐) / B 危局拾取抑制 (轻) / C 冷却保位 (契约冲突, 不做)。

**Rationale:**
- absent/stationary 0/40 推翻"人不在"假设 — 是"人在但防守系统未接棒"。
- sentry 空白由代码路径证明: 对齐开火 (非冷却瞬间) + 带外站台导航之外无第三
  条路; 冷却期让位 → navigate 漂出 lane → 哨兵永久失效 (中位 2 ticks 实证)。
- powerup 6.7% 窗口占比说明危局期行为未切换 (sentry 不触发 → 拾取继续)。

**Implications:** 待用户拍板候选方向 (A/B/都不做)。dodge 增强族已四度否决,
防守结构 (sentry/拦截) 是 hard 剩余失败的主导面。
> 全文 → docs/god-ai-tuning.progress.md §225。

## 226. 后继 ④ 候选 A/B 双否决 (STATUS: 完成)

**Decision:** §225 三因子的两个候选均 hard 35×60 否决: A baseLaneSentryInBandNav
（带内应急进 lane）v1 net −35 / v2 net −53; B baseAlertPickupSuppress（危局拾取
抑制）net −42。参数保持默认 0。

**Rationale:**
- A: 横移劫持（_fire=false + 打乱站位）代价 > 收益; "sentry 0 tick"是开火瞬间
  条件苛刻, 非缺路径; colGap=1 部分 +18 / colGap 2-3 净 −71 佐证。
- B: star/tank = 永久 DPS（M6 每 star ≈ +9pp）; 抑制拾取 = 破坏 star 经济,
  "不拾取"≠"去防守"（sentry 不触发时玩家依旧 navigate）→ 空洞抑制。
- ④ 收口: hard "太迟"失败面结构性（弹速×短窗口×贴脸连发）, 防守微调杠杆耗尽。

**Implications:** ④ 关闭。后继只剩 ⑤ t2a 自毁守卫重论证（t2a 末段 72% idle
新嫌疑面）与 M5 开放测试待拍板。hard 上下一轮有效杠杆更可能在火力/成长面
（star 经济、t2a 行为）而非防守微调。
> 全文 → docs/god-ai-tuning.progress.md §226。

## 227. 后继 ⑤ t2a 自毁守卫重论证收口 (STATUS: 完成)

**Decision:** §223 的"t2a 末段 72% idle 嫌疑"经 40 局 ledger 取证为**伪嫌疑**:
idle 100% onCooldown（800ms 冷却的正常形态）、94% 时基地威胁 imminent 且玩家
平均 11.5 cells 外（物理无解）→ t2a idle 无独立病因, 不产候选。

**Rationale:**
- idle 率全生命周期一致（43.3% vs 42.2%）— 非末段特有; 100% 冷却构成排除了
  "无输出站桩"行为缺陷。
- "中场缠斗 → 基地被掏"是 §225 窗口结构问题（271 ticks 中位）的结果, 不是
  t2a 病因; 唯一微调面（skipT2aForDefense 阈值 26 收紧）是 §159 已失败方向
  且 §226 泛化证据否定。

**Implications:** §218 后继列表全部完成（② CMA-ES 无 ROI / ③ dodge idle 伪
嫌疑转候选 A 否决 / ④ "太迟"结构性 / ⑤ t2a 伪嫌疑）。剩余: M5 fireLineDetourMode
开放测试待拍板。
> 全文 → docs/god-ai-tuning.progress.md §227。

## 228. M5 人工开放测试入口 (STATUS: 完成)

**Decision:** fireLineDetourMode（M5, 默认 0）开放人工 playtest：URL 参数
`?fireLineDetour=1` → main.ts 启动时 setGodAIParamsOverride({ fireLineDetourMode: 1 })
→ GodAIInput 构造时合并。测试 3 用例（默认 null / 合并只作用于新实例且不碰
DEFAULT 单例 / 清除恢复）。

**Rationale:**
- 零 UI 改动（MANIFEST 保持小）、零 gameplay 状态（启动配置, 不进快照, 不随
  tick 变化, §2.2 不违反）；构造时克隆（§98 纪律）保证不泄漏单例。
- candidate-on 已验证 +12 wins / base_destroyed −14（§217）, 默认 0 不变 —
  人工 playtest 是升格前的最后一关（用户拍板程序）。

**Implications:** 测试入口: `bun run dev` 后访问 `http://localhost:3000/?fireLineDetour=1`
（对照: 无参数）。建议 hard 难度 S3/S7/S12/S34（travel 段长、§217 机会面大的
关型），关注"travel 途中击杀变多 / 基地掉血变少"的体感。

## 229. M5 fireLineDetourMode SHIPPED — 默认 1（含 S30/S13 弱关重标定）(STATUS: 完成)

**Decision:** `fireLineDetourMode: 1`（DEFAULT 全难度生效；classic 无覆盖继承）。
用户拍板"默认开启 + commit"；gate 拦截（hard S30 / chaos S13 per-stage floor 失败）
后用户再次拍板"Ship + 重标定 S30 truth"。gate truth 更新: hard S30 0.9046→0.8388、
chaos S13 0.9138→0.8439（floor 随 truth 平移, 继续防未来退化）。新增二级参数
`fireLineDetourMinSlack: 13`（= DETOUR_TURN_WINDOW_TICKS 语义, 接口保留原值）。

**Rationale:**
- 胜率/结果口径三批 60-seed 验证全绿（§217/§221）: hard **+12 wins** /
  base_destroyed 447→433（−14）/ lives 中性 / classic 0 / chaos +8 — §218 gated
  四项条款全满足。人工 playtest 入口（§228）已开放, playtest 结论为"默认打开"。
- **已知代价（结构性弱关）**: hard S30（Concentric 迷宫）赢局质量 −7%
  （score 0.905→0.84, clearSpeed −0.077 / baseIntegrity −0.061, **60-seed 胜率
  0 翻转不变**）; chaos S13 同型（0.914→0.844）。机制: detour 打断迷宫 navigate
  计划 — 12t 站定 + 重导航 ≈ 24-36t 代价, 换 ~10t 击杀提前; 近距目标也空转
  （击杀 0 增量, kills 187→184）。
- **修复探针全穷尽（全部无效或负作用）**: killSlack 13→26（0.839→0.843, 微弱）;
  maxDist 2/3/4 cells（逐位相同, detour 目标全在 ≤2 cells 内, 近距也空转）;
  不回头判据（0.841, S30 无反向 detour）; csb/cbr 收窄（S30 0.872 过 floor 但
  全局 **0 wins** — 94% 机会 tick 是纯带内游走, 收窄 = 关掉 M5）。判据空间已
  穷尽, 接受 tradeoff: 全局 +12 wins 换 S30/S13 赢局质量 −7%（胜率不变）。
- 无效探针全部回滚（§217 原版语义, 无证据的改动违背纪律）; `fireLineDetourMinSlack`
  参数保留（接口默认 13, 未来调参入口）。
- 重标定先例: §190 禁用后 2026-08-13 重标定 truth — 行为有意变化（A/B 验证）
  后重标定是既有流程; §229 与其方向不同（接受已知弱关而非移除负面特性）, 但
  用户拍板 + 代价显式记录 + floor 继续防退化, 符合 gate 精神。

**Implications:** 默认行为变化: 玩家 travel 段会转向击杀带内威胁敌人（M5）。
S30/S13 的 floor 降为 0.81/0.81 量级, 未来若 detour 在迷宫关再退化将重新被
gate 拦截。`?fireLineDetour=1` 入口仍可用（无参数 = 默认 1, 同值）。

---

## 230. 门禁 runner 瘦身 — collectMetrics/collectEvents + telemetry Set ping-pong (STATUS: SHIPPED)

**Decision:** `runSimulation` 新增两个 opt-in 开关（默认 true = 行为不变）:
`collectMetrics: false` / `collectEvents: false`，score-gate（`tests/score-gate-core.ts`）
与 pass-rate gate（`tests/gate-core.ts`）均关闭——scorer（`scoreRun`）只读
`outcome/ticks/finalState/firstKillTick/telemetry`，`metrics`（默认 `sampleInterval: 1`
每 tick 分配 FrameMetrics + enemyPositions 数组）与 `events` 留存对门禁是纯浪费。
另有 telemetry power-up census 每 tick `new Set()`（§14.1 反模式）改为双缓冲 ping-pong
（`liveIdSetA/B` 交替 clear，成员语义逐位不变）。

**Rationale:**
- 读-only 观察：跳过采样/留存不消耗 RNG、不触碰 World → 结局与 telemetry 逐位不变。
  验证：60 sims score-sum 51.62（metrics 开/关完全相同）；full-suite 分数不变。
- `events` 关闭时 `failure.killerKind` 由向后扫描改走循环内 `lastBaseDestroyedBy`
  追踪（同值）。classic 行为契约（godai-score-gate truth）逐字节不变。
- 门禁 CPU-bound（2100 sims 占全套 ~85%），任何 runner 开销都直接放大。

**Implications:** 其它消费 `metrics`/`events` 的工具（`tools/eval/evaluator.ts`、
`tools/optimize/level-sim.ts`、diag/forensics）默认路径不受影响。门禁口径与
`tmp/capture-truth.ts` 相同（telemetry on）→ truth 无需重标定。

## 231. thinkInterval 决策链节流 A/B — 否决 (STATUS: 完成)

**Decision:** `GodAIParams.thinkInterval`（默认 1 = byte-identical）作为实验旋钮保留；
`thinkInterval=2`（决策链每 2 tick 跑一次，off-tick 保持上次 `_moveDir/_fire`）经
hard 35×60 A/B **否决**。保留参数与 `_thinkCounter`/`branchCounts.hold`（纯观察）。

**Rationale:**
- A/B（paired，60 seeds，telemetry on，v7 scoring）: thinkInterval=1 → win **75.6%**
  / mean-score 0.7693；=2 → win **72.8%** / 0.7469（**−2.8pp**，−0.022 score，
  691/2100 outcome 翻转）。SE≈1.34pp → ~2 SE 的真实回归，非噪声。
- 机制：1 tick（16.7ms）决策延迟看似远低于反应视界（子弹横场 100+ ticks、冷却
  ~13 ticks、followPath cell 门控 ~23 ticks/cell），但实测 dodge/火力窗口仍被
  打穿；且 off-tick 跳过消耗 godRng 的 aim roll → RNG 流移位级联改变决策
  （691/2100 翻转与 §68 签名移位同型）。classic 未测（instant 1-HP 零余量，
  §115 纪律），即便 hard 通过也不对 classic 默认开。
- 节流类方向到此收束：naive 节流 −2.8pp；条件节流（仅静默 tick 保持）理论收益
  ~5-6% sim CPU，不足以改变机器吞吐墙结论，违反 simple-beats-clever（§10）。

**Implications:** 决策链 CPU（chaos ~30%）在行为保真约束下不可节流。`thinkInterval`
旋钮保留供未来实验（默认 1 逐字节等价）。

## 232. 决策链小数组分配消除 + scanAhead 整数步进 (STATUS: SHIPPED, 字节等价)

**Decision:** 三处 per-call 小数组分配改局部变量（§14.1）: `Navigator.directMoveImpl`
的 `dirs[]`（每 tick 调用）、`think.ts` BASE_LANE_SENTRY 的 `cands[]`（每 tick
evaluate）、HUNT navStuck 回退的 `pref[]`。`perception.ts scanAhead` 改整数 cell
步进（`floor((sx + dx·k·CELL)/CELL) = floor(sx/CELL) + dx·k`，像素坐标按需由
cell + 中心偏移导出），`world.allies` 提出循环。

**Rationale:** 全部为纯计算/分配消除——选择顺序、比较次序、AABB 像素语义逐位不变。
验证：45 sims（3 难度 × 3 关 × 5 seeds）stash 前后签名（outcome:ticks:score）
**IDENTICAL**；godai-score-gate truth 逐字节通过。
- 收益：全套 41.4s → 37.5s（连同 §230/§231 的 runner 改动）。
- 拒绝：`getDefaultDefensePositionImpl` 的 `def` 对象（每 think 1 次分配，且多返回
  路径 + 调用方可能持有引用，共享 buffer 别名风险 > 收益）。

**Implications:** src/game + ai 的 per-tick 分配已按 §14 纪律扫净（决策链余量均为
节流/稀有路径）。后续优化需算法级或行为级杠杆。

## 233. bun test 全套 <20s 攻坚结论 — 机器吞吐墙 + 种子数决策 (STATUS: 完成, 10 种子落地)

**Decision:** `bun test --parallel --timeout=50000` 基线 54.5s → **~25.9s**。
score gate 种子 **20→10**（1050 sims，用户拍板），truth 重标定（seeds 1-10）、
margin 按 ~2-SE 加宽（`MARGIN_SCORE` 0.05→0.07、`AGG_MARGIN_SCORE` 0.03→0.04）。
<20s 未达成（机器墙 + 10 种子统计功效的折中），用户接受。

**Rationale:**
- 机器吞吐墙（决定性测量）: Ryzen 5800H 8C16T 对该负载有效并行度仅 ~2.5×（1 worker
  40.9ms/sim = 主线程同速；4 workers 65ms/sim；2/4/6/8 workers 无改善；进程级拆分
  3×2 workers 仅 34.7→31.8s）——内存带宽/功耗墙，非并行结构问题。gate（1050 sims）
  实测 19.7-20.2s 已是机器地板 ~16s 的 ~88%。
- 每关 margin 加宽依据: n=10 时每关均值 SE ≈ σ/√10（σ≈0.15 → SE≈0.047）→
  0.07 ≈ 1.5 SE（原 0.05 @ n=20 ≈ 1.5 SE，等功效）；聚合 floor（350 样本）稳健。
- 20→10 实测全套 ~25.9s 而非预估 ~21s: 机器有效并行度低于预估（2.5× vs ~4×），
  且 gate 效率随每 worker 样本数下降（262 vs 525 sims/worker）。8 种子预估 ~21-22s
  仍贴边不达标（机器方差 ±2s），6 种子（~17-18s）牺牲每关灵敏度过大——用户选择
  10 种子保住统计功效。
- 其它路径均穷尽: think 节流 A/B 否决（§231）；perceive/决策脑算法级改动违反
  §2.17 STOP 且无净收益；`--parallel=N` 4/6/8/16 全套 24.9-26.9s（噪声带）；
  worker 数 2-6 无益；worker boot 仅 20ms（无可省）。

**Implications:** 全套 `bun run check` ~25.9s（基线 54.5s，**−52%**），1385 tests 全绿。
gate 口径: 10 seeds × 35 关 × 3 难度，truth 以 seeds 1-10 为准（`tmp/capture-truth.ts`
重算路径保留）。未来若需 <20s: 再减至 8（贴边）或 6（达标但每关灵敏度降）种子，或
在更强调优/多核机器上跑。`GATE_CORES` 默认 4 保持（实测最优）。

## 234. 门禁种子 20→10 后补 — test-silent HEAVY_TESTS 修复 + 强制 --parallel (STATUS: SHIPPED)

**Decision:** `bun run test` 落地两条修复：
1. `HEAVY_TESTS` 里过期的 `god-ai-gate`（已 test.skip 的旧文件）替换为当前活跃门禁 `godai-score-gate`（10 种子 ~19.5s）。
2. `test-silent.ts` 的 `spawnCapture('bun', ['test', ...])` 补上 AGENTS.md §4 强制的 `--parallel --timeout=50000` 标志。

**Rationale:**
- 清洁树 `bun run test` 之前跑 30s 且 **12 fail**——根因是 runner 用单进程 batch（无 `--parallel`）导致跨文件模块态泄漏（order-dependent，`m4-release-restore` 收到 `instant` 而非 `pool`），且 HEAVY_TESTS 指向的是已跳过的旧 gate，活跃 19.5s gate 未被排除。
- 修复后 `bun run test` 清洁树 7.3s、0 fail；`bun run check` 26-27s、1385 pass 全绿。
- 该 12-fail 在 HEAD~1（未含 §230-§233 改动）同样存在——是既有缺陷，非本次 perf 攻坚引入；但 §233 把 gate 从 20→10 种子后仍未触达 <20s 目标，补齐 runner 侧排除/标志是自然收尾。

**Implications:** 显式文件模式与失败重跑（`-t` 单测隔离）均验证不受影响；pre-commit hook 使用的 `bun run test` 也随之变绿变快。

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

## 239. 全关策略 all-on 实验 — M0/M1 收口：all-on 灾难性否决 + LOO 定位 firingLaneMode (STATUS: 完成)

**Decision:** 「全关闭策略开启 + CMA-ES 重跑」实验（GOD-AI-all-strategies-CMA-ES.md）
在 **M1 门禁处收口否决**：all-on（default + manifest 21 项开关）hard 35×60 官方口径
**−693 wins / −33.0pp**（75.9% → 42.9%），base_destroyed 433→1186（+753）→ 按计划
§4 M1 门「灾难性负向不进入大规模 CMA-ES」，**M2/M3 不执行**。leave-one-out 定位冲突：
**firingLaneMode 主导**（all-on 中关掉 +416/2100 @60-seed，+19.8pp），dodgeHorizonScore
（+73）/ candidateMode（+37）/ dodgeCounterFire（+20）次级。单独 A/B 全部内在净负：
firingLaneMode **−30.0pp**（全语料首次测量，§139 仅 Battlement 局部测过）、candidateMode
−6.9pp、dodgeHorizonScore −5.4pp。**DEFAULT_GOD_AI_PARAMS 不变，无任何开关升格。**

**Rationale:**
- **协同复活假设证伪**：独立否决的策略（coverageMode §204–211、Phase 2 三机制 §207 等）
  组合开启后不协同，反而叠加灾难；LOO 中 coverageMode 继续净负（−2 噪声）。dominant
  冲突是此前从未全语料 A/B 的 firingLaneMode——§139 死区修复的局部 A/B 掩盖了其
  全语料灾难（W→L 132 vs L→W 27）。
- M0 三 artifact 完整落档（default 75.9% / all-on 42.9% / all-on−M5 43.0%）：M5 在
  all-on 语境贡献 ≈0（901 vs 904），且失败形态翻转（lives_exhausted 72→11、
  base_destroyed +753）——all-on 拖离基地 + 击杀效率下降（17.9→13.7 kills）。
- LOO 10-seed 筛选与 60-seed 确认逐 seed 增量一致（firingLaneMode +0.194 vs +0.198
  wins/run），确定性纪律（§2.3）保持。
- 四开关（targetValueMode/intentMode/pathThreatAvoidance/pathTargetMode）在 all-on 语境
  LOO 净正（+5..+8）但落在 10-seed 噪声带内（±2.3pp），不作后继依据。
- dodge 增强族（centroid/counterFire/horizon/clearance）延续 §224：hard 无杠杆。

**Implications:** all-on 方向关闭；`--profile all-on|all-on-m5-off` 仅作实验入口保留
（不传 = 一键回退 default）。新增工具：`run-profile.ts`（M0 artifact 跑批）、
`loo-allon.ts`（leave-one-out 筛选/确认）、`optimize-godai.ts --profile` + live probe
（`SimResult.paramsHash` 身份标签）。后继候选不在「开启被否策略」方向，转向状态表示/
多步计划机制（计划 §6 停止条件方向）。
> 全文 → docs/god-ai-tuning.progress.md §239

## 240. all-on−firingLaneMode CMA-ES 两轮收口 — 数值调参止于 −10.4pp，方向关闭 (STATUS: 完成)

**Decision:** 按用户指令（§239 后：关掉头号元凶 firingLaneMode，其余全开跑 CMA-ES），
新增 `ALL_ON_MINUS_FLM_PARAMS` profile（= all-on − firingLaneMode，hash f5ac3ad7），
在 hard 35×60 官方口径上跑 **两轮独立 CMA-ES**（opt-seed 1 / 2，20 seeds，12 gens，
15 workers，maxTicks=36000，warm-start 第 2 轮从第 1 轮 best）。结果：
- 基线 all-on−FLM = **1317/2100 (62.7%)**
- 第 1 轮 best @60 = **1363/2100 (64.9%)**（+46 wins，+2.2pp）
- 第 2 轮 best @60 = **1376/2100 (65.5%)**（+59 wins，+2.8pp）
- 第 2 轮 holdout（seeds 61–120）= **1313/2100 (62.5%)** —— 回落到基线水平，调参**过拟合**
- 对照：shipped default = 1594/2100 (75.9%)，差距仍 **−10.4pp**

**Rationale:**
- 关掉 FLM 后 all-on 从 42.9% 回升至 62.7%（+19.8pp，与 §239 LOO 结论一致）；但
  dodgeHorizonScore / candidateMode / dodgeCounterFire 等其余内在净负开关仍在，结构性
  伤害无法用数值调参填平 —— 调参 gains（+2.2~2.8pp）落在 holdout 上归零。
- 两轮独立 opt-seed 都止步 0.65 胜率带（fitness 532.5 / 548.1），未触及 default 75.9%；
  计划 §6 停止条件「两轮 --opt-seed 均不超 default → 停止」触发，**不再跑第 3 轮**。
- 失败形态未修复：base_destroyed 704（vs default 433），avgKills 16.7（vs default 17.9）。

**Implications:** 「开启被否策略」方向正式关闭（§239 门禁 + §240 数值双否决）。
`--profile all-on-minus-flm` 与 CMA-ES 工具链保留作复测入口；后继转向状态表示 /
多步规划机制（计划 §6 停止条件方向）。
> 全文 → docs/god-ai-tuning.progress.md §240

## 241. Replay Tick-Hash Chain 实现定案 — 每 100 tick 世界哈希锚点 (STATUS: 完成)

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

## 242. 否决 RL-WASM-Bridge（B3），改走 A'（bun 持久进程桥）(STATUS: 已决议)

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

## 243. RL 训练断点续跑机制（服务随时停启）(STATUS: 完成, 2026-08-23)

**Decision:** `run_rl.py` 三层断点续跑，崩溃/停启后自动从断点继续而非重跑：
1. **it 续跑**：`--start-it`（缺省自动 = `training_log.jsonl` 最后完成迭代 + 1），重启续跑后续迭代，不重跑已完成轮。
2. **rollout 任务续跑**：`completed_pairs(traj_dir, wver)` 扫描已完整落盘且 `manifest.wver==当前权重` 的 `(stage,seed)`（write_shard 先写 12 npy 后写 manifest ⇒ 有 manifest 即完整），`run_rollout_queue` 从任务集剔除 → 只跑未完成局；`resumed_manifests` 把已 done 局摘要并入聚合，报告 games/outcomes 仍覆盖完整一轮。
3. **PPO epoch 续跑**：`ppo_update(..., ckpt_path=it{n}/ppo_ckpt)` 每 epoch 落 `model.pt+opt.pt+epochs_done+numpy RNG`；重启 `_ppo_load` 恢复 model/optimizer/RNG，从断点 epoch 按**同一乱序**（numpy MT19937 状态精确重建）继续未完成批次。

**Rationale:**
- **权重逐轮原子写回 + rollout 确定性可复现** 使「整轮可重跑」原本成立，但成本是每崩溃重跑整轮 rollout（分钟级/GB 级）。三层断点把恢复粒度降到「未完成任务」与「未完成 epoch」。
- PPO 精确续跑的可行性来自：`ppo_update` 的 minibatch 乱序用**全局 numpy RNG**（`np.random.permutation`），且 `nn-training` 里它只被这一处消耗（`build_pairs` 用独立 `default_rng`、agents 用 `random.Random`）⇒ 存 numpy RNG 状态即可让续跑乱序与未中断完全一致。等价性由 `tmp/dist-resume-check.py` 验证：从 checkpoint 续跑后的权重与一次跑完**逐参数相等**。
- 模型初始对齐：`mA`/`mB` 须同初始（真实场景从同一 `weights.json` 加载），否则随机初始化不同导致不可比——这是测试脚本首跑失败根因，非机制缺陷。
- 崩溃窗口语义：若崩溃在「权重已写回但 jsonl 未写」之间（极小窗口），重启会用新权重重跑该轮——正确且 on-policy 一致（跑该轮用其应有权重），接受。
- **Implications:** 训练可随时停启无损续跑；`--start-it` 显式覆盖 / 自动续跑。续跑的 rotate 课程置换每次 relaunch 抖动（rotateSeed 含时间戳），it 换代但 140 局仍全覆盖、每关新鲜种子，训练正确性不受影响（PPO 消费全集）。

## 244. RL 队列模式静默跳轮修复 — resumed_manifests 双 schema 归一 + 失败迭代原地重试 (STATUS: SHIPPED, 2026-08-24)

**Decision:** `run_rl.py` 四处修复：
1. `resumed_manifests(traj_dir, wver, exclude=seen)`：只并入本轮**未采**局（排除本轮 results 已覆盖的 `(stage,seed)`），且把 shard manifest 双 schema（本地单局式 `outcome/nSamples/ticks/score` / 远端单局聚合式 `outcomes/totalSamples/scoreList`）归一为 `combine_reports` 可消费形态。
2. 「全部已 done」早返回改从磁盘 shard 聚合出完整报告（不再返回空报告——it1 指标全盲的根源）。
3. 主循环两个 except 分支：写 `iter_error` jsonl 事件 + `it -= 1` 原地重试同一迭代（`consec_fail>=5` 才 raise）——失败迭代不再静默前跳。
4. `start-training.ps1` detach 分支 stdout/stderr 重定向 `tmp/run_rl-<stamp>.{out,err}.log`（写日记）。

**Rationale:**
- 事故（2026-08-24 00:19–00:44）：队列模式整轮 rollout 完成后，`resumed_manifests` 把本轮刚落盘的 140 个 shard manifest（单局 schema，无 `games`/`totalSamples` 顶层键）原样并入 → `combine_reports` L168 `r["games"]` KeyError 秒崩 → 主循环吞掉后 `it+=1` → it2/it3 连续跳轮（dist-meta 时间线：it3 末局 00:34:48 → it4 首局 00:35:35，间隔 47s，PPO 从未启动、无 ppo_ckpt）。it1 因 438 前序局全 done 走早返回侥幸未崩，但报告 samples=0/outcomes={} 指标全盲。
- 失败详情只进易失 stdout（detach 无重定向）→ 零可复盘痕迹。观测必须自带牙齿（§3.14 教训）。
- 原地重试安全性由三层断点（§243）保证：resume 保留已完成 shard + PPO ckpt，不重跑已完局。
- 修复以真实事故数据验证：`combine_reports(resumed_manifests(it2))` 修复前 KeyError、修复后 games=140/outcomes 全归类（111 base_destroyed + 20 lives_exhausted + 7 stage_clear + 2 timeout）。

**Implications:** 任何迭代失败都会在 training_log 留 `iter_error` 痕迹并自动原地重试；detach 运行从此可事后取证。巡检工具 `rl-hourly-inspect.ts` 同步修复 null score_mean 渲染崩溃（队列空聚合事件触发）。

## 245. 干净评估嵌入分布式流水线 — PPO 空窗期全节点贪心局（STATUS: SHIPPED, 2026-08-24）

**Decision:** rollout 收官后、下轮权重分发前的空窗期，trainer 后台线程向全部 eval-capable 节点派发固定语料评估局（默认 35 关 × 2 固定种子 = 70 局，`EVAL_SEEDS=(860001,860002)`），结果追加 `tmp/rl-traj/eval_log.jsonl`（按 wver 前 16 位去重账本）。四处落地：
1. **新文件 `tools/sim/export-eval-game.ts`**（不在 codeHash 哈希集）：掩码 argmax 贪心单局 runner，只写 `_eval_report.json` 不产 shards；打分纯 v7（无 F3 门控），与 God-AI 全部基线可比。
2. **`sampler-agent.ts`**：`/v1/task` 增 `mode=eval` 路由；`/v1/ping|status` 增 `evalSupport:true` 能力声明；manifest 回显 `mode` 供中心硬校验；权重切换的旧文件删除改尽力而为 + retention 清扫（保留最新 4 份）——修在飞评估局持句柄导致 Windows EBUSY 的竞态。
3. **`dist_common.py`**：`fetch_task(mode=)` 参数 + `validate_eval_result()` 轻量校验（wver/mode 回显/关键字段）。
4. **`run_rl.py`**：rollout 返回后 spawn 守护线程 `dispatch_eval_bg`；节点门 = enabled ∧ ping ∧ evalSupport ∧ bun major.minor 一致；`--eval-games-per-stage`（0=关）/`--eval-window-sec`。

**Rationale:**
- 动机（用户指令）：采集成本趋零后，训练遥测 winRate 的两股噪声——探索采样（熵≈1.27 nats）与每轮 rotate 换 seed 的构成波动——让轮间比较失去意义。干净评估冻结权重 + argmax + 固定语料，同 seed 胜负成为确定事件，可做配对比较。
- **时机即流水线**：此刻节点持有的权重恰为上一轮 PPO 产物，与本轮 rollout winRate 同一策略直接对照；评估墙钟完全藏在 PPO 计算窗口里，零额外成本。
- **独立文件而非给 export-rl-rollout.ts 加 --greedy**：codeHash 是 rollout 准入硬门（§M4），动哈希集内文件会让全部远程节点在同步代码前被剔除、采集塌缩成本机。独立文件 + ping 能力声明实现逐节点灰度，旧 agent 零影响。
- **iterId 后缀 `ev` 隔离键空间**：agent 结果缓存按 `{iterId}:{stage}:{seed}` 键控，评估与采集天然不混叠；断点重启靠 eval_log.jsonl 账本去重，不重评已完局。
- 未完成局语义：下轮权重分发触发 agent 切换，在飞评估局失败（409/EBUSY 已修）→ 记 dropped 放弃，绝不阻塞 PPO 或下一轮。

**Implications:** 评估能力随 agent 逐节点同步灰度点亮（未同步节点自动跳过）；跨 checkpoint 比较仅在 eval-runner 口径不变的前提下有效（口径同步契约写入文件头注释）。后续可挂 HTML 趋势报告与 godai-score 维度面板（本轮只落 JSONL + 控制台摘要行）。

**修订（2026-08-24 晚，实跑发现）：流式模式下钩子位置错误 → 改阻塞式。** 原设计"rollout 返回后的 PPO 空窗"只在串行模式存在；流式模式的空闲窗已被 `run_rollout_stream` 内部的收尾 drain 吃掉，函数返回后距下轮权重分发仅秒级——后台派发的评估局必撞上权重切换全数作废。修正：串行模式保持后台隐藏；流式模式改为**阻塞执行**（墙钟预算 `--eval-window-sec`，默认 900s，即每轮迭代间显式增加约 5 分钟评估段）。代价诚实化：流式的"零成本隐藏"不成立，评估是显式流水线阶段。

## 246. 分布式协议 v3.6 — 结果容器 BCV2 子进程打包 + 任务获取异步化（STATUS: SHIPPED, 2026-08-25）

**Decision:** 针对生产实测瓶颈（macOS 四核节点 workers=8 时每个 rollout 子进程仅 ~50% CPU、整机 Idle ~50%），三项改造：
1. **容器 v2（BCV2）**：新文件 `tools/sim/pack-container.ts`——`gzip(magic 'BCV2' | headerLen | headerJSON | entry*)`，entry = nameLen u16 + name + dataLen u64 + **原始 npy 字节**。打包从 agent 主线程**下沉到 exporter 子进程**（rollout/eval 两脚本新增 `--pack <path>`），与仿真并行；agent 只做一次文件读。去 base64：线体体积 -25%。Python 解码端 `dist_common.unpack_container()` 按 magic 自动识别 v1/v2。
2. **任务获取异步化**：`GET /v1/task` 带 `x-async: 1` → 立即 `202 {token}` 后台执行；新增 `GET /v1/result?iterId&stage&seed` 轮询取包（200 容器 | 202 在跑 | 500 失败一次性消费 | 404 过期）。同 key 在跑幂等回同一 token。旧 trainer 同步路径原样保留；submit 用完整 timeout 以兼容旧 agent 长连接。
3. **协调器零改动接入**：`fetch_task()` 内部透明完成异步提交+轮询+瞬断重试，签名/异常语义不变，`run_rl.py` 两处调用点无需修改。

**Rationale:**
- 归因链：inactivity 不是 macOS 限核——4 workers 时单进程 98%、8 workers 时单进程 ~50% 是典型的"上游供给限速"特征；供给卡点 = v1 容器的 base64+gzip+JSON 拼装串行在 Bun.serve 单线程主线程上，多局完成体排队，仿真子进程一半时间在等打包。
- 异步化的真实收益按价值排序：①轮询期网络瞬断不丢局（结果在 agent 缓存里，恢复续拉——Cloud Shell/隧道场景刚需）；②计算槽与传输解耦；③重复提交天然幂等。
- 兼容性矩阵：新 trainer+旧 agent 可用（magic 自动识别 v1）；反向不兼容——两侧同 commit 部署是既有惯例（codeHash 门强制节点同步）。submit 不用短超时试探旧 agent（会误杀同步长连接），靠 x-async 头灰度。
- 协议变更入册 plan/distributed-rollout.md v3.6；E2E 全链路验证（本地起 agent + dist_common 驱动）：异步 rollout 校验落盘、eval 任务、并发同 key 幂等（gamesDoneTotal 恰 +1）、未知 token 404、v1 容器解码全过。

**Implications:** codeHash 集内文件 export-rl-rollout.ts 有改动 → 全部远程节点须重新 checkout 到新 commit 才能过门（标准流程）。elapsedSec 语义微变为 exporter 子进程内耗时（少算 spawn 开销，仅日志用）。agent `/v1/status` 新增 `recentFailed`。后续若要进一步压传输成本，可让 /v1/result 支持 ETag 断点续传（当前无需求，不做）。

## 247. M1 分歧探针 — 归因 ①/③ 边界（2026-08-26，plan/AI-No-Items-Warmstart.md §4）
> 工具 `tools/diag/divergence-probe.ts`（预注册：分歧=学生贪心≠教师标签且 120-tick 内
> 有可观测后果；三桶 基地高压/交战/巡航；后果代理指标从学生实际轨迹提取，不做双臂重放）。
> 结果（25 局 hard）：分歧率 70.6%，基地高压桶最高 74.6% 且特征表完整 →
> 按预注册规则判 **①/③ 边界**（标签或监督）：M3 走 wins-only + 守家帧回补（near-miss
> 3×），预留 DAgger 交互轮。全文 → docs/nn.progress.md §13.2。

