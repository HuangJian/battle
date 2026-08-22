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

## 239. §1.6 魔法数字 → 命名常量 (STATUS: 已实施, plan/refactor.agy.md Phase 1)

**Decision:** 在 `constants.ts` 新增 `SEED_HASH` (0x9e3779b9)、`P2_SEED_OFFSET` (0xdeadbeef)、
`TURN_SENTINEL_MS` (-9999)、`POPUP_DURATION_MS` (1500)、`GAME_OVER_TIMER_MS` (3000)、
`SMALL_EXPLOSION_MS` (200)、`BIG_EXPLOSION_MS` (500)；将 `src/game/` 全部 21 处 `1000 / 60`
字面量替换为既有 `TICK_MS`。纯机械替换，数值逐一相等。

**Rationale:**
- plan/refactor.agy.md §1.6：散落的魔法值迫使 agent 逐处确认语义；命名后可检索、可审计。
- 保护文件豁免：`src/ai/god/think.ts` / `ActionCandidates.ts`（AGENTS §5.1 God AI 禁区）内的
  同字面量保持原样；`ThreatBudget.ts` 不在禁区，已一并替换。
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
