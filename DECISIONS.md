# Design Decisions

> Key decisions. Full details in linked documents.
> 编号体系：§1–§9 为基石决策，其余为分类索引。
> **God AI 调校索引**：Classic 纪元（§27–§95，2026-07-27 → 08-01）与 v2 重设计纪元（§96–§110，2026-08-03，
> M0–M11）的完整进展、数据与方法论教训统一归档于 **`docs/god-ai-tuning.progress.md`**（Part I / Part II）；
> §96–§110 在本文件为压缩索引，正文全文见该文档。v2 设计文档（plan/God-AI-Redesign-v2.md 等）已删除，
> 核心设计归档于 progress.md §II.0。

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
| Round 5 S32 close-combat (t2aMaxRange=2, 72.5%→85.0%) | `docs/god-ai-tuning.progress.md` §3.4 |
| Phase A SmartThreatModel (rejected, 8+ variants all negative) | `docs/god-ai-tuning.progress.md` §3.5 |
| §47 base protection ring collision fix (real S32 breakthrough) | `docs/god-ai-tuning.progress.md` §4 |
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

**Decision:** The original §48 terrain-occlusion evasion was rejected (-10pp S32, brick+steel both occluded). The revisit ships a **steel-only** occlusion **gated to steel-maze stages** (`evasionSteelOcclusionBrickRatio: 0.1`, auto-enabled in `computeStageAdaptedParams` when `brickWallRatio < 0.10`):

1. `findMostDangerousBulletImpl` skips enemy bullets whose path to the player is blocked by STEEL — but only when the player is NOT pinned (≤2 open directions). Brick is never occluded (dodging brick-blocked bullets is load-bearing anticipatory dodge — the original §48 lesson).
2. The terrain gate is the key discriminator: brickWallRatio, NOT steel ratio, predicts the mechanism's value. S26 Brick Maze has MORE steel (26%) than S32 Diamond (18%) yet regresses while S32 gains.
3. A re-ranking guard (`nearestBlocked < bestDist → null`) was prototyped and **removed** — its motivating case (S26) is gated OFF, and it cost ~0.8pp on S32 (+3.3 → +2.5 @120 on same seeds).
4. Trap avoidance (user idea 2 — don't walk into surround positions) was implemented (`trapAvoidance` in Navigator) but stays OFF: full-corpus A/B near-neutral (net +2 @60), no big regressions, but no clear win either — rejected per the "neutral structural change" discipline.

**Rationale:**
- Steel is a permanent barrier for enemy bullets (STEEL_PIERCE_PLAYER_LEVEL is player-only), so a steel-blocked dodge is genuinely wasteful in open guard bands / steel corridors.
- But on brick-heavy stages, ANY dodge (even of a steel-blocked bullet) is load-bearing repositioning through breakable cover; suppressing it re-ranks the scan to a farther bullet (S26 seed-7: player dodged down one tick early and lost).
- Terrain data (2026-08-01 probe): S32 0.063 / S6 0.04 gain; S14 0.915 / S26 0.254 lose.

**Results (2026-08-01):**
- 35×60 full A/B with brickGate 0.10: **net +1 flip, ZERO per-stage regressions** (S14/S26 byte-identical, all other 33 stages 0pp). S32 +3pp @60, +3.3pp @120 (68.3→71.7); S6 +0.8pp @120 (80.0→80.8, the -2pp @60 was seed noise).
- Regression gate passes with the shipped default (644/700, 92.0% vs 581 floor) — S6/S32 now play occlusion-ON in the gate.
- S32 base_destroyed 11→18 but lives_exhausted 27→16: the trade is base-risk for survival — net positive.

**Implications:** Default `evasionSteelOcclusionBrickRatio = 0.1` is ON (S6/S32 only). `evasionSteelOcclusion = 0` stays the explicit master switch; the gate auto-enables on qualifying stages. Tooling: `tools/diag/ab-test-steel-occlusion.ts --brickGate R`（本地不入库，§0.C）, `per-seed-diff --set evasionSteelOcclusionBrickRatio=R`（通用 --set 标志；与旧 --brickGate R 等价，由地形门控按关自动启用）.

## 72. §49-Revisit: 炮口相向对枪抵消 Parameterized + Re-Validated (SHIPPED, default unchanged)

**Decision:** The retained §49-family behavior (§52 v2 对枪抵消 — facing-enemy counter-fire + keep-alignment, inline in T2a) was parameterized as `counterFire` (default **1** = current shipped behavior, byte-identical) + `counterFireMaxRange` (default 5 = the original hardcoded 5-cell range), then re-validated on the current tree (post-§47/§58/§48-revisit) with the same per-seed methodology as §48-revisit:

1. `counterFire: 0` → plain pre-§52 T2a (turn to face + fire, no facing-enemy special-casing) — the A/B OFF arm.
2. Default stays **ON** (1): the A/B shows counter-fire is a clean positive on the current tree, so flipping it OFF would lose S26/S20 wins. `SKILLED_HUMAN_PARAMS` inherits it automatically (derived from `DEFAULT_GOD_AI_PARAMS`).
3. Per-seed byte-identity (the §70 JIT-sensitivity check): the parameterization's ternary + `counterFireMaxRange * CELL` hot-path shape change is byte-identical to the committed hardcoded baseline — S26 seed-41 and S20 seed-60 dumps (committed vs param-default) both **IDENTICAL**.
4. `AIM_RANGE_CELLS` = 15 (FireControl constant) vs `counterFireMaxRange` = 5: the param is the binding gate, not shadowed by the primitive's own scan range.

**Rationale:**
- §49 v1 (post-fire dodge, top-level branch) was rejected (-2.6pp); §52 v2 (T2a-inline counter-fire) was retained with +5 wins @35×120 on the pre-§47 tree. Re-processing §49 per the user's directive required re-validating the retained form on the CURRENT tree.
- Result: **zero negative results** — 35×60 full A/B net **+3 flips with 0 ON→OFF losses** (S26 +3.3pp @60 / +2.5pp @120 seeds 41/44/61; S20 +1.7pp @60 / +0.8pp @120 seed 60; all other 33 stages 0pp). No terrain gate needed — unlike §48, counter-fire's value does not divide by terrain class.
- The §52 v2 mechanism (fire to cancel an in-line enemy bullet — bullet elimination is safer than trading hits) holds on the current tree; 120-seed confirmations on both gain stages rule out seed noise.

**Results (2026-08-01):**
- 35×60: net +3 flips, 0 per-stage regressions, 33 stages 0pp. Mean 88.9% → 89.0%.
- S26 @120: +2.5pp (seeds 41/44/61). S20 @120: +0.8pp (seed 60).
- Regression gate passes with production default (644/700, 92.0% vs 581 floor) — the parameterized default plays identically to the hardcoded shipped behavior.
- New unit tests (`tests/counter-fire.test.ts`, 10 tests) lock the detection primitives + shipped default.

**Implications:** Default `counterFire = 1` / `counterFireMaxRange = 5` unchanged. Tooling: `tools/diag/ab-test-counter-fire.ts --all --seeds N`（本地不入库，§0.C）, `per-seed-diff --set counterFire=0`（通用 --set 标志）.

## 73. §68-Revisit: Crossfire Awareness v2 Re-Tuned with per-seed tick-diff (REJECTED, stays OFF)

**Decision:** The user directive re-processed §68-v2 (crossfire awareness, default OFF since its original -1.1pp) with the per-seed tick-diff method. The re-tune confirmed the negative result at mechanism level and **shipped nothing** — all four fix variants were net-negative, and the experiment code was reverted (src/ byte-identical; crossfire stays OFF per "基础设施保留默认 OFF" policy):

1. **A/B reproduction on the current tree**: 35×60 OFF 89.0% vs ON 88.1% (-0.9pp, 138→156 paired flips, net -18) — matches the original -1.1pp.
2. **Per-seed mechanism (cf-trace, GodAIInput subclass)**: bad flips (S26/S6/S14) fire on threats 12.6-23.1 ticks out (premature perpendicular commitment off the A* path into death); good flips (S28/S27) fire at 8.3-8.4 ticks (imminent escape). The reactive dodge handles 12-23t threats fine — the crossfire diversion is redundant early and deadly when it commits the wrong way.
3. **Variant 1 — lead-time cap** (`crossfireThreatTicks=10`, only flag bullets arriving within 10t of NOW): net -25. Helped mazes (S26 -12→-7, S6 -10→-7, S31 -8→-2, S30 -7→-5) but destroyed open-stage gains (S28 +7→-3, S32 +5→-2, S1 +5→+2). Chain-breakage: S28-15's escape needed a SECOND 31.7t-lead diversion at tick 3700 that the cap suppressed → the whole win chain collapsed.
4. **Variant 2 — destination openness gate** (`crossfireMinExits=3`, only divert into cells with ≥3 passable exits): net -14. The bad maze lanes are locally OPEN (≥3 exits) — S26-5/S6-3/S14-8 ran byte-identical to raw ON, so the exit-count heuristic cannot separate them.
5. **Variant 3 — combined**: net -25 (inherits the cap's open-stage damage).
6. **Stage-metric correlation (all 35 stages)**: density / avgPass / open-cell% / brick% / steel% — NO metric separates good stages (S28/S27/S32/S8/S1/S10) from bad (S26/S6/S14/S31/S30/S2/S5); every metric overlaps (e.g. S2 23% density bad vs S8 23% good; S33 2.93 avgPass good vs S30 2.94 bad). Extends the §69-A finding: the entanglement is dynamic (enemy/bullet/cascade context), not static terrain.

**Rationale:** The diversion gains and losses share the same trigger — no lead-time, destination-quality, or terrain discriminator exists. This is the definitive confirmation of the §68/§69 conclusion ("any perturbation of dodge → T8 → T2a → navigate is net-negative") with mechanism-level evidence. Per §0.C rule 2, only generalizable changes are kept: the generic `per-seed-diff --set key=value` override (used for all diagnostics) stays; the experiment params/gates were reverted.

**Implications:** `crossfireAwareness` stays 0 (OFF). No new production params shipped. Local diagnostics (gitignored, never committed): `tools/diag/ab-test-crossfire.ts` (A/B, `--fix key=value`) persists locally; the ephemeral `tmp/cf-trace.ts` (diversion tracer) and `tmp/stage-metrics.ts` (metric correlation) were deleted before commit per §0.C rule 3.

## 74. Steel-Fire Gate: Never Fire at Unpierceable Steel to Break Through (SHIPPED)

**Decision:** New param `steelFireGate` (default **1** = ON; 0 = OFF = byte-identical pre-§74). When ON, the two navigate **break-through** fire sites in `think()` (aggressive navigate + T2b navigate) — which fire WITHOUT calling `shouldFireInDirImpl` — apply the same T11 steel gate that `shouldFireInDirImpl` already enforces: steel blocks fire while `p.level < STEEL_PIERCE_PLAYER_LEVEL` (3). Implemented as `steelFireBlockedImpl` (the T11 predicate) + `shouldFireBreakThroughImpl` (steel gate + §70 base-ring guard) in `FireControl.ts`, used at both sites.

**Rationale:**
- User report (2026-08-01): "player 不具备破钢能力时，不要射击钢铁障碍物来试图开路" — the AI fired at indestructible steel to open a path, wasted the bullet cap, then camped at the wall for the full camp timeout, cutting combat efficiency.
- Root cause: T11 lives in `shouldFireInDirImpl`, but the break-through sites bypass it entirely, firing at whatever blocks the move direction — including steel.
- Scope (per-seed A/B, 2026-08-01): deliberately applied ONLY to the break-through sites. The T2a/aggressive stop-and-aim sites fire on `scan.enemy` and are left ungated — the dual-offset case (steel on one scan line, enemy on the other) means the enemy is genuinely reachable by the center-line bullet, and a distance-blind gate there costs kills (arena A/B: 20 kills → 7 kills, gameover @1634 vs clear @4592).
- Replay confirmation: `classic-s05-clear-l3-t67-seed1785579063833.replay` (coop, player2 = God AI) — player2 parked at (20,20) facing right and held fire at a steel wall for 80+ consecutive ticks (t≈450–530, "8秒"); 2051 of 2461 recorded fire ticks had steel in the line of fire with no enemy. Live coop re-sim from the same snapshot with the gate ON drops steel-fires 270 → 47 (−83%).
- MANIFEST §13 Three Gates: (1) not wasting bullets on indestructible walls is more enjoyable; (2) one param + one predicate is simple; (3) the AI should not fight terrain it cannot affect.

**Results (2026-08-01):**
- 35×60 A/B (single-player corpus): suite 0.7685 → 0.7594, win rate 92% → 91%, mean Δ −0.0040 ± 0.0041 (t=−0.99, p=0.32) — **no stage moved significantly** (all p ≥ 0.05), so no per-seed tick-diff was triggered per §0.B. The gate is near-inert on the single-player corpus because A*/directMove never point `_moveDir` *into* steel; its measurable win is in coop (see replay evidence above).
- Arena A/B (steel-ring curriculum): byte-identical (4592 ticks / 20 kills both arms) — zero kill loss from the scoped gate.
- Regression gates pass: `godai-split-parity` / `god-ai-regression-gate` / `god-ai-curriculum` 22/22; full `bun run check` 686 tests green.
- New unit tests (`tests/steel-fire-gate.test.ts`, 21 tests) lock the predicate, the break-through decision, the ungated-T2a scope decision, and the shipped default.

**Implications:** Known non-goal — the gate does NOT suppress T2a/aggressive stop-and-aim fire in the dual-offset case (that fire is load-bearing). Known theoretical edge — `shouldFireBreakThroughImpl` blocks on `bs.steel` from either offset line, so a brick wall with steel closer on one offset line could suppress a legitimate brick-break; not observed on the 35×60 corpus (p=0.32), revisit only if a larger-seed run flips a stage. Tooling: A/B via `eval-suite --compare` with `{"steelFireGate":0}` vs default; per-seed via `per-seed-diff --set steelFireGate=0`.

---

## 75. §75: Distance-Aware Base-Wall Fire Guard (T2a/Aggressive Suicide Fix)

**Decision:** The §70 base-ring fire guard protected `shouldFireInDirImpl` and the two break-through fire paths, but the T2a (stop-and-aim) and aggressive-mode fire paths bypassed `shouldFireInDirImpl` entirely — firing directly when `scan.enemy` was true, without checking `scan.baseWall`. Because `scanAheadImpl` uses two independent offset scan lines, one offset can find a base-protection brick (`baseWall=true`) while the other finds an enemy (`enemy=true`). The T2a path fired whenever `scan.enemy` was true, destroying the player's own base. This caused 4 `killer=player` base-destruction failures in S32 Diamond (120 seeds: 26, 34, 78, 82).

The fix has three parts:

1. **`scanAheadImpl` (FireControl.ts)**: New `baseWallDist` field — stores the step count when a base-protection brick or 'base' (eagle) terrain is found. Initialized to `Infinity`. Set alongside `baseWall=true` for both 'brick' and 'base' terrain cases.

2. **T2a and aggressive-mode entry guards (GodAIInput.ts)**: Changed `if (scan.enemy)` to `if (scan.enemy && !(scan.baseWall && scan.baseWallDist <= scan.enemyDist) && !(scan.baseSteel && (p.level ?? 0) >= 3))`. This prevents firing only when the base wall is **closer than or at the same distance as** the enemy — the 6px bullet spans both offset columns and WILL hit a closer base wall before reaching the enemy. If the enemy is closer, the bullet hits the enemy first, so firing is safe. This distance-aware check avoids the over-conservative regression of a blanket `!scan.baseWall` check (which prevented valid shots at enemies behind the base wall and caused +12 lives_exhausted on S32).

3. **Break-through fire paths (GodAIInput.ts)**: The two break-through paths (aggressive T2b and navigate) had `bs.enemy || (!bs.baseWall && ...)` — the `bs.enemy ||` short-circuited and bypassed the base protection. Fixed in `shouldFireBreakThroughImpl` (shared by both sites) to `!bs.baseWall && !(bs.baseSteel && lvl >= 3)` (conservative, no distance comparison — break-through is for breaking walls, so never break a base wall). The §74 steel-fire gate is layered on top in the same function, so a break-through never fires at unpierceable steel either.

**Rationale:**
- The `baseWallDist <= enemyDist` comparison is correct because `bulletHitsTerrain` runs BEFORE `bulletHitsTank` per tick. If the base wall is at distance 3 and the enemy at distance 5, the bullet reaches the base wall first (step 3) and is stopped — the enemy at step 5 is never reached. If the enemy is at distance 3 and the base wall at distance 5, the bullet hits the enemy first (step 3) — the base wall is never reached.
- A blanket `!scan.baseWall` check (conservative) was tested first: it eliminated all 4 suicides but caused -1 net win on S32 (86→85 @120) due to +12 lives_exhausted from suppressed valid shots. The distance-aware check recovers those shots: S32 86→87 @120, mean 91.9%→92.1%.
- 1 residual `killer=player` suicide remains (seed 34) — likely an edge case where the enemy and base wall distances are very close but the scan ordering or movement timing allows the bullet to reach the base wall. This is a 75% reduction (4→1) in player suicides, with a net +0.2pp mean improvement.

**Results (2026-08-01):**
- S32 Diamond @120: 86→87 wins (+1), base_destroyed 18→8 (-10), killer=player 4→1 (-3), lives_exhausted 16→25 (+9).
- 35×20 validation: mean 92.1% (was 91.9% pre-fix). S18 +10pp, S25 +5pp, S28 +10pp.
- Regression gate: 645/700 (92.1%) — all 35 stages meet floors. S32 15/20 (75%), floor 11.
- Unit tests: 11/11 pass (`tests/fire-control-steel-block.test.ts`), including new `baseWallDist` and dual-offset baseWall+enemy tests.
- `bun run check` green (test + typecheck + lint + format).

**Implications:** `baseWallDist` is now a permanent field on the `scanAheadImpl` result. The distance-aware check is applied only to the T2a and aggressive paths — `shouldFireInDirImpl` and the break-through paths keep the conservative blanket check (no distance comparison), as they have since §70.

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

## Lie-Back-Win-Mode (Coop God AI)

| Decision | Detail |
|----------|--------|
| Q1–Q10 sign-off + hidden-state compliance | `plan/Lie-Back-Win-Mode.md` |
| Kill-score routing (score for human, score2 for God) | `plan/Lie-Back-Win-Mode.md` |
| AutoFireInput wiring | `plan/Lie-Back-Win-Mode.md` |
| One-Author correction (requestCoopToggle routing) | `plan/Lie-Back-Win-Mode.md` |
| Parity baseline drift attribution | `plan/Lie-Back-Win-Mode.md` |
| Stage override table updates (S26, S32, S6, S18, S25, S32 close-combat) | `docs/god-ai-tuning.progress.md` §6 |
| God AI evaluation standard v7 (wide-band gap + harmonic mean fitness) | `docs/god-ai-tuning.progress.md` §9 |
| RNG split for replay fidelity | `docs/god-ai-tuning.progress.md` §9 |
| Fire-through-steel fix | `docs/god-ai-tuning.progress.md` §9 |

## Render Optimization

Full baseline + per-milestone deltas in `docs/render-optimization.progress.md`. Upstream plan: `plan/render-performance.plan.md`.

| Decision | Detail |
|----------|--------|
| `@napi-rs/canvas` (Skia) as render-bench devDependency | R0 headless baseline; not in `bun run build` output. Skia shares Chromium's rasterizer, so relative rankings are trustworthy even though absolute wall-time does not extrapolate to browsers (no GPU compositing / `desynchronized`). mock canvas rejected — cannot measure `drawImage` blit / gradient raster / save-restore real cost. |
| `SpriteLibrary.loadFromSources` injection point | Only production-code change in R0. Lets the harness feed disk-decoded SVGs bypassing Vite `?url` imports (Bun can't resolve them). Idempotent, ~4 lines, parallel to existing `loadFromUrls`. |
| Render regression gate keyed on draw-call / save-restore counts | Draw/saveRestore tallies are backend-agnostic (counted by a Proxy on the main 2D ctx) and byte-identical run-to-run — the deterministic CI signal. Wall-time is noise-prone under Skia software raster (±10% on Windows), so it is reported but not gated. |
| R1 frame orchestration (P1-A sig threading + P1-B allTanks threading) | `shouldRender` passes the already-computed scene signature to `recordRendered(world, sig?)`, eliminating a second full walk of tanks+bullets+power-ups on the sig-changed path. `PresentationLayer.render` rebuilds `world.allTanks` once and threads it to both `updateVisualState` and `renderer.render`. Net: allTanks calls 4→2 (sig-changed) / 3→2 (forceRender). |
| `hasForest` gate (skip forest blit on forest-free stages) | Default stage has no forest; the unconditional full-field `drawImage(forestCache)` was a pure no-op costing a full-screen blit. `recomputeHasForest` scans 676 cells only on terrain-cache rebuild, not per frame. Net: -1 draw call/frame all scenes. |
| Particle early-exit + `drewDebris` flag | `renderParticles` returns immediately when `activeCount === 0` (skips 5 loop set-ups + an unconditional `setTransform`). `drewDebris` skips the base-transform restore when pass 2 drew nothing — `setTransform` is a real napi/Skia call (~300ns). Common-frame (no debris) savings. |
| Shadow `fillStyle` save/restore instead of `save()`/`restore()` | `drawTankShadow` only mutates `fillStyle`, so a cheap read/write of that one property replaces a full graphics-state push. Net: -12 save/restore pairs/frame (6 tanks × 1 pair). |
| Insignia 180° rotation baked into SpriteCache | `drawInsignia` always rotates the badge by PI about its center, so baking that rotation into the sprite (`renderRotated(img, TANK_RENDER_SIZE, Math.PI)`) lets the render path use a plain `drawImage` instead of `save/translate/rotate/restore`. Eliminates 1 save/restore pair per non-commander tank. |
| Aura manual property save (drawAllyAura / drawHpLevelAura / drawCommanderAura) | Each aura method only mutates `fillStyle`/`strokeStyle`/`lineWidth`/`globalAlpha`, so manual read/write of those properties replaces `save()`/`restore()`. Combined with shadow + insignia bake: main render path now has ZERO save/restore calls (saveRest/f = 0 across all bench scenes). All remaining save/restore live in fallback paths (SVG fallback, terrain cache rebuild, procedural tank fallback) that don't run when SpriteCache is built. |
| `measureText` width cache for power-up countdown | Font is deterministic in `fontSize`, so key on `${fontSize}:${text}`. Avoids a ~6µs `measureText` per power-up per frame. R4 plan's gradient cache not implemented — power-ups are 0–1 per frame, gradient allocation is not a measured hotspot. |
| Vignette sub-rect blit rejected | A 9-arg `drawImage` skipping the transparent center was prototyped; Skia's `extractSubset` overhead made it ~2-3× slower than the whole-image fast path. Full blit is both correct and fastest. |
| R5-A static layer merge (bg baked into terrainCache) | The per-frame `fillRect`(bg) + alpha-blended `drawImage`(terrain) pair is replaced by a single opaque `drawImage` of a terrain cache that has the background baked in. The cache is opaque (bg + tiles), so the blit is a fast source-copy rather than a per-pixel alpha blend — meaningful on software rasterizers (old machines w/o GPU). When the camera shifts (shake/pan), the overscroll border is filled with bg first to prevent stale pixels. `paintCacheBg` bakes the bg gradient into the cache; `rebuildTerrainCache` and `redrawTerrainCell` use bg fill instead of `clearRect`. |
| Low-quality render mode (Performance Mode) | Gated by `GameRenderer.lowQuality` (set by Performance Mode). Skips: (1) vignette full-screen blit (~1.4ms/frame on Skia software — the single most expensive op); (2) tank contact shadow (6 fillRect-equivalents/frame); (3) decorative particle passes (debris/smoke/ring/flash — keeps sparks for hit-direction feedback). Gameplay-relevant visuals (auras, insignia, hit overlays, shields) are NEVER skipped. On the burst scenario: draw/f 120→65, perFrame 1.62→1.04ms (-36%). |
| R3 aura pre-rendering (16 pulse buckets, lossy) | Auras (ally / hp-level 2–6 / commander) are pre-rasterized into `AURA_BUCKETS` (=16) offscreen bitmaps per variant at SpriteCache build time. At runtime, `draw*Aura` quantizes the frame-derived sine pulse to a bucket index and `drawImage`s the bitmap — 1 blit replaces 2–7 path ops + manual property save/restore + the commander's per-frame `createRadialGradient`. Net: combat draw/f 58→48 (-10), burst 121→111 (-10). **Lossy** (plan §6): (1) pulse alpha quantized to 6.25% steps — visually indistinguishable at the slow pulse frequencies used (period ~50–80 frames); (2) commander's two out-of-phase pulses (0.12 + 0.08) collapsed to one (pulse2 := pulse1) — the inner ring now pulses in sync with the outer instead of slightly offset, a subtle change. Anti-aliasing is preserved because the bitmap is rasterized at full alpha and blitted with the bucket's alpha baked in (drawImage multiplies per-pixel alpha). Fallback to direct path drawing when SpriteCache is not built. |
| R5-B composite tank bitmap (body + overlay, lossless) | When a tank has an overlay (player starbuf1–3 / enemy hit1–4), the body sprite + overlay sprite are composited into a single bitmap on first access (lazy LRU, 128-entry cap per plan §7 B). Render path issues 1 `drawImage` instead of 2. Lookup is zero-allocation: outer `Map<tankKey, (CanvasImageSource\|undefined)[]>` keyed by the caller-provided tankKey (no construction), inner numeric index `dirIndex*20 + overlayNum*10 + stage` into a sparse 80-slot array (P0 — replaces an earlier `${tankKey}:${dirIdx}:${overlayKind}:${stage}` template-string key that allocated 1 short-lived string per tank-with-overlay per frame, up to 12/frame in combat). **Lossless**: both sprites are opaque, drawn at the same position, in the same order (body→overlay) — pixel-identical to the 2-draw path. Net: idle 22→21 (-1), combat 48→46 (-2), burst 111→109 (-2). Memory ≤ 6.9 MB worst-case (128 × 54 KB @ DPR=2), typically ≤ 1.1 MB (10–20 lazy entries). Ally tanks (no overlay) and stage-0 tanks (no overlay) bypass the composite entirely. |
| R4-glow power-up glow pre-rendering (16 pulse buckets, lossy) | The power-up golden radial gradient (`createRadialGradient` + 3× `addColorStop` + `arc`+`fill`) is pre-rasterized into `AURA_BUCKETS` (=16) offscreen bitmaps at SpriteCache build time. At runtime, `drawPowerUp` quantizes the frame-derived `sin(frame * 0.11)` pulse to a bucket index and `drawImage`s the bitmap. **Draw-call count unchanged** (the glow was already 1 `arc`+`fill` = 1 draw; the bitmap is also 1 `drawImage` = 1 draw). The optimization eliminates per-frame `createRadialGradient` object allocation + 3× `addColorStop` string parsing + gradient rasterization setup — API-churn cost that R0 showed accounts for ~50% of DPR=2 perFrame cost (the meaningful axis for old-machine software rasterizers). **Lossy** (plan §6, same class as R3 aura): (1) pulse alpha quantized to 6.25% steps — max alpha delta ≈ 2.8% on a ~40% alpha pixel (≈1.1/255 absolute), visually indistinguishable at pulse frequency 0.11 (period ~57 frames ~ 0.95s); (2) anti-aliasing preserved (bitmap rasterized at full alpha, blitted with bucket alpha baked in via drawImage per-pixel alpha multiplication — mathematically equivalent to direct path). A/B pixel verification (2026-07-31): `idle` (no power-ups) 11/11 checkpoints identical — confirms R4-glow's blast radius is strictly the power-up glow halo; `combat`/`burst` 9/11 mismatch (frames 60/84 land on bucket boundaries, quantization error = 0); `pan` 11/11 mismatch. Memory: 16 × (24 × dpr)² × 4 bytes ≈ 147 KB @ DPR=2. Fallback to `drawPowerUpGlowDirect` when SpriteCache is not built (procedural / no-cache themes). |
| P0 hot-path allocation elimination (SpriteCache / SpriteArtist) | Pre-rendering pipelines (R3 aura / R5-B composite / R4-glow) originally keyed their caches with template strings (`fx.starbuf${stage}`, `${tankKey}:${dirIdx}:${overlay}:${stage}`, `${fontSize}:${text}`) — each constructing 1 short-lived string per tank / per power-up / per frame. Up to ~12 strings/frame in combat. On a 20-year-old machine these short-lived allocations drive minor GC pauses that read as frame hitches (AGENTS §14.1). Replaced with zero-allocation lookups: module-level `STARBUF_KEYS` / `HIT_KEYS` / `INSIGNIA_KEYS` arrays for SVG keys, numeric-indexed arrays for composite-tank lookup, nested `digitWidthCache[fontSize][secStr]` + `fontStringCache[fontSize]` for the power-up countdown. `drawPowerUpCountdown` also caches `ctx.font` strings per fontSize (power-ups are always CELL-sized → 1 entry) and reuses the `String(seconds)` result instead of calling it twice. **Lossless**, pixel-identical (pixdiff 4/4 scenes identical 2026-07-31). draw/saveRestore counts unchanged (21/46/109/47, 0/0/0/0). perFrame unchanged on dev machine (allocation savings realize on the target hardware, not on this machine — V8's generational GC absorbs them invisibly here). |
| P1 zero-allocation incremental terrain rebuild | `updateTerrainCache`'s incremental path (invoked whenever a bullet damages terrain — every bullet-impact frame in combat/burst) previously allocated a `new Set<number>(tm.dirtyCells)` plus a 4-element tuple array `[c-1,r], [c+1,r], [c, r-1], [c, r+1]` per call — ~7 short-lived heap objects per terrain-damage frame. Replaced with two reusable buffers on `GameRenderer`: `_dirtyMark: Uint8Array(GRID*GRID)` (676 bytes, lifetime) marks cells needing repaint; `_dirtyList: number[]` collects the unique cell indices. Phase 1 marks dirty cells + inline 4-neighbour scan (no tuple array, no destructuring); phase 2 repaints each marked cell and clears the mark in the same iteration. Both buffers reset to empty after each call. **Lossless**, pixel-identical (pixdiff 4/4 scenes identical 2026-07-31). draw/saveRestore/perFrame unchanged on dev machine — same rationale as P0; the win is GC-stability on old hardware, not visible in wall-time here. |
| P2 render-path allocation cleanup (batched micro-fixes) | Four small allocation hotspots surfaced by a focused audit of the per-frame path. All **lossless**, pixel-identical (pixdiff 4/4 scenes identical 2026-07-31), draw/saveRestore counts unchanged. (a) **`computeSceneSig` allTanks cache**: `world.allTanks` was fetched twice per repainted frame — once in `computeSceneSig` (called from `shouldRender`), once in `render`. The `_allTanksBuf` is mutated only by the getter itself and no sim tick runs between `shouldRender` and `render`, so the buffer fetched in `computeSceneSig` is cached on `_sigTanks` and consumed by `render`. Skips the second ~6-10 entry array write per repainted frame. (b) **`AnimationSystem.cleanup` Map iteration**: `for...of` over a Map allocates an iterator-result + a fresh `[id, vc]` tuple per entry per call — ~12 short-lived objects/frame with 6 tanks. Switched to `components.forEach((vc, id) => ...)` — `forEach` does not use the iterator protocol and is spec-safe for `delete` during iteration. (c) **`neighborMask` closure + tuple**: 4 call sites in `redrawTerrainCell`/`rebuildTerrainCache` previously allocated a fresh 4-tuple AND an `at` closure per call (~12 short-lived objects per brick-destroy burst). Refactored to fill a reusable `_nmask: boolean[4]` field on `GameRenderer`; bounds checks inlined; callers read `_nmask[0..3]` after the call. (d) **`renderBullets`/`renderPowerUps`/`renderExplosions`/`renderPopups`**: `for...of` → `for (let i = 0; ...)` index loops, matching `renderTanks`. V8 optimizes both for dense arrays, but `for...of` may allocate an iterator on holey arrays (post-compaction); the index loop is worst-case safe and consistent with the rest of the render path. P5 (Camera.getOffset `Math.random()` skip when `shake===0`) was **rejected** — would shift the `Math.random` sequence and break pixdiff determinism vs the captured reference, for negligible wall-time savings (~2 native calls/frame at rest). |

---

## 70. Base-Ring Fire Guard (Never Destroy Own Base)

**Decision:** The God AI must never fire at base-protection bricks or base-ring steel in any navigation or fire path. Concretely:
1. `FireControl.ts scanAheadImpl`: new `baseSteel` flag computed **post-loop** (outside the hot per-cell scan) to avoid changing V8's JIT optimization of the scan loop. The steel cell coordinates (`steelCol`, `steelRow`) are stored with two assignments in the steel branch (minimal hot-loop overhead), then the base-protection band check runs once after the for-loop completes. OOB cells (which default to `'steel'`) are naturally excluded because `steelCol`/`steelRow` are only set for in-bounds cells.
2. `FireControl.ts shouldFireInDirImpl` T6: `if (result.baseWall) return false` and `if (result.baseSteel && level >= 3) return false` — the steel ring is never broken, even when the player can pierce steel (level ≥ 3). Below level 3, steel is indestructible so no guard is needed.
3. `GodAIInput.ts` T2b navigate break-through and aggressive break-through: re-scan ahead before firing. Only fire to break through when the obstacle is NOT a base ring (`!bs.baseWall && !(bs.baseSteel && lvl >= 3)`). The "enemy in line of fire" exception is honored: if `bs.enemy` is true, fire anyway.

**Rationale:**
- Root cause of the suicide: the old T2b break-through path used `!canMoveDir` to trigger fire, bypassing T6's base-protection check. In coop mode (player2 born on the opposite side of the base), this caused the God AI to shoot through the base ring and destroy its own base.
- Root cause of the initial S32 regression (OOB false positive): placing the baseSteel band check **inside** the hot scan loop's steel branch caused it to run on ALL `'steel'` terrain including OOB cells at the field edge (which default to `'steel'`). On S32, the bottom edge at `row=GRID` (dr=|26-24|=2) was falsely flagged as `baseSteel`, causing the T6 non-base-steel guard to skip and the AI to waste bullets at the field edge.
- Root cause of the residual 1-seed regression (V8 JIT sensitivity): even with an OOB bounds check, the extra variable declarations and comparisons inside the steel branch changed V8's JIT optimization of `scanAheadImpl`, causing subtle behavioral differences in the `shouldFireInDir` code path. Fix: move ALL baseSteel computation to a **post-loop** block (runs once per scan call, not per cell), keeping the hot steel branch to just two coordinate assignments.
- The fix is mandatory under MANIFEST §13 (Three Gates): (1) not destroying your own base is more enjoyable; (2) a minimal scanAhead guard is simple; (3) Battle City tanks never shoot their own base.

**Results:**
- S32 Diamond: 15/20 (pre-fix) → 10/20 (OOB bug) → 14/20 (OOB bounds check, -1 from V8 JIT) → **15/20** (post-loop baseSteel, zero regression).
- Aggregate: 643/700 (91.9%) — identical to pre-fix baseline. Zero net regression.
- Coop base suicide: eliminated (confirmed via `repro-base-suicide.ts`).
- **No floor adjustment needed**: the regression gate passes with original truth values.
- **Lesson**: adding computation to a V8 JIT-hot loop (called thousands of times per simulation) can change optimization decisions that cascade into behavioral differences, even when the computation is functionally a no-op. Hot-loop changes must be validated with per-seed comparison, not just aggregate win rates.

---

## 75. Replay Recording Must Tap the Decorated Input (Lie-Back-Win-Mode desync) (SHIPPED)

**Symptom:** A 躺赢模式 (coop) stage-clear replay played back as a defeat — the
tank drove into and shot its own base. Reproduced headlessly on the reported
artifact `classic-s15-clear-l2-t105-seed1785585133360.replay`:
recorded `clear` @6312 ticks (score 11300 / 40 kills) → played back as
`gameover` @3205 ticks with the base destroyed.

**Root cause:** `Game.loop()` recorded the *raw* keyboard object, not the object
the Simulation actually consumed:

    this.simulation.tick()                                // consumed simulation.input === AutoFireInput
    this.recorder.recordFrame(this.input, this.godInput)  // recorded the bare Input   [WRONG]

In coop, `requestCoopToggle()` sets `simulation.input = new AutoFireInput(this.input)`.
`AutoFireInput.isFiring()` returns `true` on every tick while armed, so the live
run fires from tick 0, while the raw keyboard reports "not firing" until the
human's first real press (tick 118 in the reported file). Every auto-fired shot
was therefore missing from the P1 stream: playback diverged at tick 0, the enemy
population and `world.rng` draw sequence drifted, and P2's literally-correct God
AI frames were then applied to a world that no longer matched — steering the God
tank into the base. This contradicted `AutoFireInput`'s own documented contract
("the decorated (auto-fired) input is what the replay records").

**Decision:** The recorder always taps `simulation.input` / `simulation.input2` —
the exact objects the preceding `tick()` consumed — never the raw `Input` /
`godInput` fields. This is decoration-agnostic: any future input decorator is
recorded correctly by construction.

**Blast radius:** Browser (`source: 'browser'`) coop recordings only.

- Single-player is unaffected: `simulation.input === this.input`, so the tap is identical.
- Sim-generated replays are unaffected: `tools/sim/simulation-runner.ts:329` already
  recorded the same object the sim consumed. Verified empirically.
- P2 is unaffected: `simulation.input2` and `this.godInput` are always assigned together.
- Every coop replay *type* (`clear` / `base` / `died`) was affected, not just `clear`.

**Not the cause (ruled out):** God AI RNG (deliberately independent per #47);
`endFrame()` running once per rAF rather than per tick (stale `_thought` caching
is shared by sim *and* recorder, so it stays self-consistent through playback).

**Salvage:** the loss is exactly reconstructible, because AutoFireInput is
deterministic — armed from stage start (one recording session == one stage),
firing every tick until the human's first real press, pass-through after. So
`fire = true` for ticks `0..T-1` where `T` is the first recorded fire bit.
`tools/replay/repair-coop-replay.ts` applies this; the reported file then replays
back to `stageclear` @6312 with score 11300 / 40 kills — an exact match to its
recorded metadata.

**Tooling added:**

- `tools/replay/verify-replay.ts` — headless `.replay` verifier (mirrors
  `PlaybackController` wiring; exit 1 on desync). No such tool existed before.
- `tools/replay/repair-coop-replay.ts` — salvages pre-fix coop replays.
- `tests/replay-coop-autofire.test.ts` — records through the real `AutoFireInput`
  + `GodAIInput` wiring across 3 stages, asserts P1 fires from tick 0, covers the
  non-coop path, and pins the old buggy tap as a desync.

**Known gap (unrelated, pre-existing):** `parseReplayFile` hard-rejects
`frameSchemaVersion: 1` even though `unpackFrames`/`ReplayInput` still handle v1,
so the seven v1 files in `replays/` cannot be imported or verified.
→ Closed by #76.

---

## 76. The Packed Blob Is the Only Authority on Frame Schema (SHIPPED)

Two loose ends left by #75, both instances of "a declaration drifted from the
thing it declares".

### 76a — v1 replays were unreadable for no reason

**Symptom:** the seven `.replay` files in `replays/` could not be imported or
verified: `parseReplayFile` returned `Unsupported frame schema version: 1`.

**Root cause:** three layers disagreed about what "schema version" means.

| Layer | What it did |
| --- | --- |
| `packFrames` / `InputRecorder` | Emits **v1** bytes for a single-stream recording (deliberate downgrade), **v2** only for coop. |
| `serializeReplayFile` | Always stamped the envelope `frameSchemaVersion: 0x02` — a lie for every single-player file. |
| `parseReplayFile` / `canPlay` | Demanded `=== FRAME_SCHEMA_VERSION` (0x02). |

`unpackFrames` and `ReplayInput` had always auto-detected from the blob's
leading byte and handled both. Only the gatekeepers were strict, and they were
strict against a field the writer filled in wrong.

**Decision:** the packed blob's **leading byte** is the single authority on
layout. Everything else is descriptive and must agree with it.

- `SUPPORTED_FRAME_SCHEMA_VERSIONS` + `isSupportedFrameSchema()` (`config.ts`) —
  readers accept every schema this build can decode, not just the newest.
- `frameSchemaVersionOf(blob)` (`pack.ts`) — the one way to ask the question.
- `serializeReplayFile` stamps the envelope with the blob's actual version, so a
  single-stream file declares v1 and stays readable by v1-era readers.
- `parseReplayFile` gates on the envelope *and* re-checks the decoded blob;
  `Replay.schemaVersion` comes from the blob instead of being hardcoded. A file
  that declares v2 but carries v1 bytes (every pre-fix browser recording) parses
  as the v1 replay it actually is.
- `ReplayManager.canPlay()` gates on the blob too, precisely so those
  historically mis-stamped records stay playable.
- `ReplayManager.create()` records the blob's version instead of hardcoding 0x02.
- `parseReplayFile` now also rebuilds `Replay.frames2` for imported coop replays;
  import previously dropped it while recording kept it.
- Adjacent fix: `InputRecorder` stamped the standalone `frames2` stream with the
  **v2** header despite it being single-stream. Dormant (nothing decoded it) but
  the same bug class — now `FRAME_SCHEMA_V1`.

**Result:** all seven legacy files import *and* reproduce their recorded outcomes
exactly under `tools/replay/verify-replay.ts`.

### 76b — leaving a replay mid-coop dropped both live inputs

**Root cause:** `PlaybackController.exit()` took only the raw input and
hard-nulled `input2`, with the comment *"caller re-wires if coop is active"*.
Neither `stopPlayback()` nor `finishPlayback()` re-wired. Resuming a coop game
after a replay would therefore hand the Simulation the **bare keyboard**
(auto-fire decoration gone — a mute P1) and **no player-2 input** (a frozen God
tank). Latent today only because every exit path happens to funnel through
`resetToMenu()`, which clears coop anyway — a comment-shaped landmine.

**Decision:** an exit restores exactly what was live; it never guesses.

- `exit(simulation, realInput, realInput2 = null)` — both streams explicit.
- `Game` grows one source of truth: `liveInput` (`autoFireInput ?? input`),
  `liveInput2` (`godInput`), and `wireLiveInputs()`. All five wiring sites
  (coop on, coop off, recovery restore with and without coop, `resetToMenu`) go
  through it, and both playback exits pass `liveInput` / `liveInput2`.

The rule is the same one as #75, one layer up: **hand over the object the
Simulation actually consumes, never the undecorated field behind it.**

**Tests:** `tests/replay-v1-compat.test.ts` (blob-is-authority, v1/v2 parse, the
historical envelope lie, both rejection paths, plus a data-driven guard that
imports and starts playback on every file in `replays/`) and three exit-rewire
cases in `tests/replay-coop-autofire.test.ts`. Gate: 713 pass / 0 fail; tsc,
oxlint and oxfmt clean.

## 77. Playback seek must advance the input (drag-the-bar desync) (SHIPPED)

Follow-up to #75/#76: the repaired coop replay played correctly start-to-finish,
but **dragging the progress bar then resuming desynced** ("重放又乱了").

**Root cause:** `PlaybackController.seekTo()` (and the matching restore loop in
`buildKeyframes()`) restored the initial snapshot, re-created the `ReplayInput`,
called `input.seekTo(targetFrame)` to pin the cursor at the target, then ran a
fast-forward loop — but **never called `input.advance()`**:

```ts
this.input.seekTo(targetFrame)          // cursor pinned at target
for (let i = 0; i < targetFrame; i++) {
  simulation.tick()                     // every tick reads frame[targetFrame]
}                                        // cursor never moves → advance() missing
```

So every one of the `targetFrame` fast-forward ticks re-consumed the **same**
frame, and frames `0..targetFrame-1` were never applied. The world landed in a
state that had nothing to do with the real timeline at `targetFrame`, so the
resumed playback diverged immediately (reproduced on the coop replay as
`gameover @13`, base destroyed, instead of `stageclear @6311`).

Normal playback was unaffected because `update()` calls `input.advance()` once
per tick.

**Decision:** the fast-forward loop must replay frames `0..targetFrame-1` exactly
like `update()` does — `simulation.tick()` then `this.input.advance()` — so the
world lands on the true timeline at the seek target and resume is seamless.

- `seekTo()`: dropped the pre-seek `input.seekTo(targetFrame)` (the fresh
  `ReplayInput` already starts at cursor 0) and added `this.input.advance()`
  inside the loop. `input2` (the coop God-AI slice, which shares the parent
  `tick` counter) advances in lockstep.
- `buildKeyframes()` restore loop: same fix — `restoredInput.advance()` per tick,
  so the world handed back after thumbnail capture matches the cursor.

**Reproduction / proof:** `tools/replay/repro-seek.ts` drives the REAL
`PlaybackController` (full playback vs seek-then-resume). On the pre-fix code it
prints `[DESYNC]` for the coop replay; after the fix `[OK]` at 0.1/0.33/0.5/0.66/0.9,
and a v1 single-stream replay also seeks cleanly.

**Tests:** `tests/replay-seek.test.ts` — coop (stages 15 & 1) and single-player
(stages 3 & 8) each seek at five fractions and must reproduce the clean full
playback outcome; plus a direct guard that the cursor sits at the expected
offset after `seekTo(0.5)`. Gate: 713 pass / 0 fail; tsc, oxlint clean.

## 78. Seek catch-up must drain (discard) world events — no audio burst (SHIPPED)

Follow-up to #77: after the seek desync was fixed, **dragging the progress bar
emitted a harsh burst of sound** — "把一个阶段的游戏音效一下子播放出来了". Same
catch-up loops, a second side effect.

**Root cause:** the render loop (`Game.ts`) calls `world.consumeEvents()` once per
*rendered* frame, draining one frame's worth of events into audio + presentation.
The seek fast-forward (`seekTo`) and thumbnail pre-pass (`buildKeyframes`) run
`targetFrame` / `currentFrame` sim ticks with **nobody draining** in between. Each
`tick()` pushes its sound effects into `world.events`; `restoreWorld` clears the
buffer up front, then it silently accumulates across the whole catch-up. When the
next rendered frame finally runs `consumeEvents()`, it plays the *entire*
backlog at once — hundreds of shots/explosions detonating simultaneously.

(`Game.ts:1624`'s "fast-forward to final frame for a thumbnail" loop is NOT
affected: it ends with `restoreWorld(savedSnap)`, which clears `world.events`.)

**Decision:** a silent catch-up must drain events every tick — mirrors what the
render loop does, we just don't play them. The world state is already updated by
`tick()`, so discarding the observation events is safe and keeps both audio AND
presentation backlogs empty after seek (no stale particle/flash burst either).

- `seekTo()`: `world.consumeEvents()` inside the fast-forward loop, per tick.
- `buildKeyframes()`: `world.consumeEvents()` in BOTH the full replay loop and the
  restore loop, per tick.

**Reproduction / proof:** `tools/replay/repro-seek-audio.ts` measures the pending
queue after a real `seekTo`. Pre-fix (non-draining equivalent) would queue **424**
events on the coop replay and **142** on a v1 single-stream replay; post-fix the
pending queue is **0** at every seek point.

**Tests:** `tests/replay-seek.test.ts` — "PlaybackController seek leaves no audio
backlog (#78)": coop seek leaves `world.events.length === 0` at 0.1/0.33/0.5/0.66/0.9,
single-player seek also empty, and `buildKeyframes` leaves the queue empty.
Gate: 716 pass / 0 fail (+3 vs #77); tsc, oxlint clean.

---

## 79. Coop God AI drove P1 instead of P2 (replay stall + base-wall break)

**Symptom:** In 躺赢模式 (coop), after P2 respawns it sits at spawn 00:56–01:41 (no
move) and mid-way shells the base protection wall. Reported on
`classic-s15-clear-l2-t105-seed1785585133360.repaired.replay`.

**Root cause:** 7 call sites in `src/ai/god/{Navigator,FireControl,StrategyPlanner}.ts`
read `const p = w.player` (P1) instead of `const p = self.controlledTank(w)` (P2 in
coop). The plan START used the correct P2 cell via `playerCell()`, but every
passability / wall-break / fire-gate / target-select validation read P1's surroundings,
so co-op P2 emitted `left` into the base wall forever (open for P1, a base brick for P2)
and cleared base bricks via the position-relative `canMoveOrBreak` guard.

**Fix:** the 7 sites → `self.controlledTank(w)`. The default `controlledTank` is
`(w) => w.player`, so single-player behaviour is byte-identical (a no-op). Do NOT
reintroduce `w.player` in these sub-modules — co-op P2 must always be addressed through
`self.controlledTank(w)`.

**Tests / proof:** `tests/godai-coop-controlled-tank.test.ts` (5 pass) — stall repro
(fails pre-fix, passes post-fix), P2 never fires at the base wall, movement evaluated
vs P2, `canMoveOrBreak` refuses the base wall for P2 while P1 is clear, single-player
parity. `tools/sim/regression-check.ts` A/B (classic, stages 0–15, seeds 1–3):
single-player clear rate parity 93.75% = 93.75%; co-op clear rate 100% = 100%; co-op
P2 avg-lives buggy −7.63 (death-spiral) vs fixed +2.90.

## 80. §80: Turn-Snap Aim Guard — Don't Commit to a Stop-and-Aim Turn Whose Grid-Snap Breaks the Firing Line (SHIPPED)

**Symptom:** `classic-s11-clear-l1-t51-seed1785622102123.replay` (0:31–0:47): during a freeze window, player 2 (God AI) stood in one spot firing at nothing instead of roaming to kill the helpless enemies.

**Root cause:** turning is not free. `Simulation.updateMovement` axis-locks movement and snaps the PERPENDICULAR coordinate to the grid on every direction change (`axis === 'x' ? tank.y = snap(tank.y, CELL) : tank.x = snap(...)`). A tank parked at a non-grid-aligned sub-cell offset teleports up to CELL/2 px sideways the instant it turns — dragging the target off `scanAhead`'s ±CELL/2 offset lines. The `aggressive` (freeze) branch has NO anti-stall guard of its own (T2a has `_campTicks`, navigate has `_navStuckTicks`), so once the snap broke the line it stayed broken: the whole freeze window — the highest-value window in the game, when enemies are helpless — burned firing at nothing.

**Decision:** New param `aimTurnSnapGuard` (default **1** = ON; 0 = OFF = byte-identical pre-§80). When ON, the aggressive branch re-runs `scanAheadImpl` from the POST-snap position before committing to a stop-and-aim TURN; if the enemy is no longer on that line, the aim is a lie → fall through to navigate (which has real stall detection). Implemented as `aimSurvivesTurnImpl` (`FireControl.ts`), evaluated BEFORE the branch's own scan — both write the shared `self._scanResult`, so the `&&` short-circuit ordering is load-bearing.

**Rationale:**
- Turning's axis-snap is cheap and invisible when the tank is grid-aligned (the overwhelmingly common case), so the guard early-returns and is byte-identical there — the fix only engages in the pathological geometry that causes the deadlock (post-snap scan misses a target the pre-snap scan saw).
- MANIFEST §13 Three Gates: (1) the freeze window actually producing kills is more enjoyable; (2) one param + one predicate is simple; (3) not aim-jittering at a target one's own turn moved out of reach respects the original.

**Results (2026-08-01, classic 35 stages × 60 seeds × 2 modes — truth scale):**
- **Freeze-window kills: coop 2415 → 5688 (+136%), single 1363 → 3503 (+157%)** — the reported bug (freeze window fired at nothing) is fixed.
- Win rate: coop 98.8% → 99.0% (+0.2pp, net +3 flips); single 88.8% → 89.7% (**+0.9pp, net +20 flips**). Per-stage @60: **0 regressions ≥5pp** (largest negative Diamond −1.7pp coop / Ramparts −1.7pp single = 1-seed binomial noise); improvements Lattice +8.3pp (net +5) / Riverbed +5pp (net +3) single.
- **S32 Star Fort regression was seed noise — confirmed at 60 seeds: exactly even (Δ0.0pp, net 0; coop 60/60 both, single 55/60 both).** The −10pp @10 / −3.3pp @30 (seed-7 single flip) cancelled out across the full seed set. Per-seed mechanism (still a real failure mode, but net-neutral at truth scale): t1226 the guard rejected a stop-and-aim turn (A `up|.|up` vs B `left|F|left`), the ON tank marched into a top-wall dead-end (base fell t2524) while OFF's accidental stop-and-hold cleared (t3673) — a pre-existing navigate dead-end weakness (the tank changes cells along the wall, so `_navStuckTicks` never fires), not a guard defect.
- Naive period-2 thrash detector: at 10-seed screening the worst streak (Brick Maze s27/seed1 = 1166t) was byte-identical in both states → the guard is inert on that run (different mechanism). At 60 seeds the OFF baseline's aggregate worst streak EXCEEDS ON's in both modes (coop 1199t vs 1166t; single 1186t vs 1159t) — mildly supportive of the fix. Some single stages show HIGHER detector counts with the guard ON but byte-identical outcomes — the detector now counts productive hunting motion; `freezeKills` is the meaningful metric.
- Regression gates pass; `bun run check` green (746 tests).

**Tests:** `tests/godai-turn-snap-guard.test.ts` (8 tests) — guard geometry (gate OFF → always true, already-facing → true, grid-aligned → true, lie-aim rejected, real aim accepted, default ON) + integration arms on a crafted lie-aim arena: guard ON kills the frozen enemy within 900 ticks; guard OFF wastes the window (0 kills, sub-cell displacement). Seed-independence verified via a 2-seed × 2-guard matrix.

**Implications:** Default ON (the fix ships). A/B baseline: `per-seed-diff --set aimTurnSnapGuard=0`; freeze-window quantification: `tools/sim/freeze-thrash-audit.ts --set aimTurnSnapGuard=0` (added a generic `--set` override + per-stage JSON). Known residual: aggressive-branch anti-stall remains absent for grid-aligned oscillation (s27/seed1) — a candidate follow-up.

---

## 82. 督战模式（Supervise）— God AI 作为 player1 全程无人类输入 + 战斗速率快捷键 (SHIPPED)

**Decision:** 新增「督战模式」：与躺赢模式（coop）同源但反其道而行——God AI 控制 **player1**（`GodAIInput` 默认 `controlledTank = w.player`），人类键盘完全脱离游戏输入（`simulation.input = godInput`，`input2 = null`）。`world.spectate` 标记（World 字段 + 快照序列化 + 回放 metadata）。督战与躺赢互斥（`requestSpectateToggle` 开启时先退出 coop，反之亦然）。督战局不存最高分（延续 Q4：无人类参与的成绩不入榜）。

**Rationale:**
- MANIFEST §2.1 One-Author：与 coop 完全同构——`requestSpectateToggle` 走 `simulation.requestSpectateToggle` 延迟到 `updatePlaying()` 首 tick 应用；Game 在 menu/paused 时立即应用（与 `pendingCoopToggle` 同款）。
- AGENTS §2.2 No Hidden State：`spectate` 是 World 字段，快照/回放可复原；恢复（recovery restore）时若快照带 spectate 而 `godInput` 已清，重建 God AI（镜像 coop 的 §3.8 路径）。
- AGENTS §2.3 Determinism：战斗速率（`battleSpeed`）只缩放 accumulator 的毫秒沉积（`accumulator += dt * speed`），tick 本身不变——是 cadence 不是模拟状态，故是 Game 字段（如 renderFpsCap），不入 World、不入快照。
- 速率阶梯 `[1, 1.5, 2, 4]`（`src/game/battleSpeed.ts` 纯函数，可单测）；Alt+>（Shift+Period）/ Alt+<（Shift+Comma）事件驱动监听（同 onPerfKey 模式），回放时路由到 `setPlaybackSpeed`。

**Implications:**
- 督战 HUD 徽章（金色 SPECTATE）+ 速率芯片（×1 隐藏）+ footer 提示 + Control Center 按钮 + 回放 SPECTATE 徽章；i18n en/zh。
- 高分局 gating：`checkConditions` 两处 `saveHighScore` 改为 `!coop && !spectate`。
- 返回菜单时 `battleSpeed` 复位 ×1（会话级观战辅助，不跨会话）。

---

## 81. 移除 godai-stage-overrides.ts 机制 — 禁止按关卡名特殊化（防过拟合）

**Decision:** 彻底删除 `src/ai/godai-stage-overrides.ts`（文件、`GOD_AI_STAGE_OVERRIDES` 空表、`applyStageOverrides()`）以及全部 17 处调用点，包括 `simulation-runner.ts` 的 `skipStageOverrides` 选项。今后不允许对关卡做按名特殊化（stage-name-keyed overrides），防止过拟合。如果经过充分验证确需特殊化处理，只能写统一的过滤逻辑：基于关卡特征的阈值（如钢/砖比、森林/水域密度、敌人队列装甲比等），即现有的 `computeStageAdaptedParams()` 模式（§58/§60/§61/§62/§64/§66/§48-revisit terrain gate）。

**Rationale:**
- 覆盖表在 §58（2026-07-31）已清空，`applyStageOverrides` 成为恒等 no-op；保留空表 + 17 处调用点只是残留复杂度，`skipStageOverrides` 标志已完全失去意义。
- §54-§56 教训：覆盖是数据，数据会过时。RNG split、§47 碰撞修复、v7 评估之后，全局默认大幅提升，历史上 5 条覆盖被逐一移除或泛化。
- 按关卡名写覆盖 = 对固定关卡集的过拟合：当关卡数据（stageData）变化或新增关卡时无法泛化。统一过滤逻辑（特征 → 参数）对新关卡自动生效，且每个阈值本身就是可调参数（CMA-ES 可直接优化）。
- MANIFEST §13 Three Gates：删除残留机制让架构更简单（2）；统一过滤让所有关卡公平竞技（3）；防过拟合提升长期可维护性。

**Implications:**
- 行为无变化：覆盖表早已为空，删除前后仿真结果逐位一致；`computeStageAdaptedParams()` 继续在 `GodAIInput.reset()` 内负责数据驱动适配。
- `simulation-runner.ts` 移除 `skipStageOverrides` 选项；`per-seed-diff.ts` / `freeze-thrash-audit.ts` 改为先复制 `DEFAULT_GOD_AI_PARAMS` 再应用 `--set`（顺带修复了空表时代码直接变更共享默认对象引用的隐患）。
- 文档同步：`plan/tasks.chat.md` 勾选、`docs/god-ai-tuning.progress.md` 现状改写、`plan/Lie-Back-Win-Mode.md` / `plan/God-AI-Next-Round.md` 残留引用更新。

---

## 83. §83: dodgeDirection 回退分支不再沿炮弹飞行方向逃跑 — 受困走廊时回头对枪抵消 (SHIPPED)

**Symptom:** `classic-s02-clear-l1-t62-seed1785636440494.replay` (0:27, tick 1641)：player 在垂直走廊（col 4，左右被封死）里，一颗敌方炮弹在同一列从上方追下来，player `dir=down` 一路往下逃（`rec=[down.]` 持续 39 tick），炮弹更快，追上致死。期望行为是「回头开火，对消敌人炮弹」。

**Root cause:** `dodgeDirectionImpl`（ThreatAssessor.ts）对垂直炮弹的垂直候选是 left/right。当 player 被夹在走廊里（left/right 均不可走）时，落入回退分支：原逻辑选「离基地最近的开方向」。炮弹往下飞（朝向基地），"离基地最近"恰好就是 **炮弹自身的飞行方向（down）** —— player 沿走廊在炮弹的尾流里逃跑，但炮弹更快，必然被追上。（忠实回放 + fresh GodAIInput 无状态诊断逐 tick 确认：`threat=down@d…` 检测到了，`dodge=down` 是问题所在，`aim=up face=Y bInLine=true` 说明 turn-up + T5 本可抵消。）

**Decision:** 回退分支排除炮弹飞行方向（逃跑徒劳且面对反方向、冷却结束的子弹也打反方向 = 必死）；优先选**朝向炮弹的方向**（炮弹飞行的反方向），使 player 转身面对来袭炮弹、`shouldFireInDir` 的 T5 在 128px 内开火抵消（对枪抵消）。仅当朝向方向也被堵死时才退回炮弹方向（最后手段，能动能比站着强）。

**Rationale:**
- 证明"朝向炮弹"在两种情况下都支配"逃跑"：未冷却时转身即开火抵消、存活；冷却中至少 **面对** 炮弹 —— 其子弹一解析立即开火抵消（逃跑则面对反方向，冷却结束那发也打空 → 必死）。
- 只在 `!safeA && !safeB`（垂直候选不可行的受困几何）时触发；普通垂直闪避路径逐字节不变，无回归风险。
- MANIFEST §13 Three Gates：(1) 消除"炮弹尾流里逃跑致死"令人沮丧的行为，更愉悦；(2) 一个局部回退改动，简单；(3) 触发对枪抵消符合 FC 精神。

**Results (2026-08-01, classic 35 关真值 A/B):**
- **过关率：持平（byte-identical）。** 35×60：修复前 90.05%（1891/2100）＝ 修复后 90.05%（逐关逐 seed 完全一致）；35×20：修复前 92.57% ＝ 修复后 92.57%（干净重跑逐位一致）。
- **确定性已证**：同一代码同一进程类型重复跑 bulk sweep，输出逐字节一致（`probe` 两跑 IDENTICAL）。
- **方法论教训（预 seed 重叠验证）**：20-seed（1–20）⊂ 60-seed（1–60）。诊断初期 35×20「+3 进步 / S2/S30/S33 回退」是**污染产物** —— 在我 stash 往复期间，基线那次 bulk run 的 `src/ai/god/ThreatAssessor.ts` 处于修复进行中的残留态（该跑 S2=20 与孤立 per-seed S2=19 矛盾即铁证）。干净重跑后 fixed == baseline。**任何代码改动 A/B 必须在干净 git 态下、且用 per-seed 对比校验，不能只看一次 bulk 总胜率。**
- 为何"回退分支大量触发（35×20 约 1815 次）却净持平"：sim-runner 用独立 `godRng = (seed ^ 0x9e3779b9)`（浏览器 spectate 用不同接线，replay 场景由浏览器产生），其 seed 分布把致命受困走廊场景稀释到不翻转任何最终结果；修复正确但在此评估框架上不改变 aggregate。

**Tests:** `tests/dodge-corridor-flee.test.ts`（5 tests）——垂直走廊被夹不逃 `down`、回头选 `up`（朝炮弹）；水平走廊不逃 `right`、选 `left`；有垂直候选时仍 `left`（无回归）；端到端 `findMostDangerousBullet` 检测 + dodge 朝炮弹。先红后绿：修复前 `dodge=down`（Fail），修复后 `dodge=up`（Pass）。

**Implications:** 默认随代码 SHIPPED（无参数开关，逻辑内联）。已修复真实 replay 致命 bug 且无评估框架回归。推进方向：若想让该修复在 God AI 调优 framework 上显现价值，需给 sim-runner 的受困走廊场景构造/加权重，或用浏览器 spectate 同源 RNG 接线复现——但那属于评估基建，非本 bug 修复范围。


## 84. BONUS TIME: God AI Collects the Remaining Power-ups in the Pickup Window (SHIPPED)

**Decision:** `findPowerUpTarget` (StrategyPlanner) now lifts the normal-play divert-distance / route-danger caps during the post-clear pickup window (`world.pickupWindowEntered && world.pickupWindowTimer > 0`): the whole field is fair game, and items are scored by despawn urgency first (an item with <5s of life left gets a boost up to 2000), then nearest-first (`-dist*10`) with a small priority tie-break (`(6-priority)*10`). The normal-combat path is byte-identical (same caps, same score formula).

**Rationale:**
- 督战 spectate task「bonus time, GOD AI player 需要去捡道具」: after the last enemy dies, remaining power-ups can sit anywhere on the 26×26 field, but `powerupMaxDivertDistance` (16 cells; 8 for bomb/star) silently capped the loot radius. A distant item was never targeted — the AI fell through to `selectTarget` → no enemies → defense position and stood there while the 10s window (`POWERUP_PICKUP_WINDOW_MS`) expired.
- With the stage already cleared there is nothing to fight or defend, so the divert caps (built for combat opportunity cost) are meaningless; TIME is the only scarce resource. Urgency-first + nearest-first maximizes the number of items collected inside the window.
- Scope: applies to every God AI user (督战 P1, 躺赢 P2, headless sims). Win rate is unaffected (the stage is already cleared when the window runs); score/clear-time improve slightly.

**Results:**
- New tests `tests/godai-bonus-time.test.ts` (4 tests): far item targeted only in the window, divert cap still respected in combat, expiring item outranks a fresh far item, and a full-sim run where the AI physically collects a 24-cell-away star before the window expires.
- `bun run check` green.

**Implications:** No new params — the pickup window itself is the gate. If future balance wants the God player to skip low-value loot even in the window, add a param; not needed today.

## 85. §84-Revisit: BONUS TIME Pickup Is Reachability-Aware — Never Chase an Unreachable Item (SHIPPED)

**Decision:** `findPowerUpTarget` now resolves every BONUS TIME candidate to a **collect cell** (`powerUpCollectCell` in StrategyPlanner): the item's own cell when reachable, or the nearest overlapping passable neighbour when the item sits on blocking terrain. Reachability uses the exact same A* the navigator drives (corridor paths, then dig-through-brick — `findPath` with/without `breakBrick`). Genuinely unreachable items (steel/water-enclosed pockets) are skipped; the collect cell is what `navigateTowards` receives, so the AI never targets an impassable cell. The normal-combat path is untouched.

**Rationale:**
- Follow-up to §84: the whole-field scoring made the AI chase the BEST-scored item even when it sat behind a steel wall or in a water pocket. `navigateTowards` always answers null for such a target, so the S5 branch returned `_moveDir = null` every tick — the AI stood still and burned the entire 10s window on one unreachable item instead of collecting the reachable ones.
- Reachability uses `findPath` directly (NOT `navigateTowards`), so it is a pure function of World state: no RNG draws (the navigator's suboptimal-path gate draws from the AI RNG stream on cache misses), no cross-tick cache mutation. Determinism preserved; the extra RNG stream is untouched.
- Scope is BONUS TIME only (the pickup window): during real combat the pre-existing caps remain authoritative. The tank collects by OVERLAPPING the TANK-sized item rect, so every cell in the item's 3×3 neighbourhood overlaps it — when the item cell itself is impassable (a deferred drop materialized on another stage's layout — `flushPendingDrops` never re-validates position — or fence steel placed over a drop), the tank parks on the nearest overlapping passable neighbour and THAT cell is the navigation target. Natural drops never straddle the base (`buildDrop` rejects blocked footprints), so the neighbourhood path is for the deferred/fence edge cases.
- Water is NOT ignored even for boat-holders: the navigator's own `navigateTowards` never passes `ignoreWater`, so flagging a water-island item reachable would re-create the stuck state. Reachability matches what the tank can actually drive (consistency by construction).

**Results:**
- New tests in `tests/godai-bonus-time.test.ts` (10 total): a steel-boxed bomb (higher score, first in iteration) is skipped for a reachable star; a field of only-unreachable items yields no target (AI not stuck); a base-straddling item resolves to the adjacent collect cell (10,24) and is physically collected by parking beside it; and a full-sim run collects the reachable star while the boxed bomb stays on the field. Red-green verified (tests fail on the committed baseline).
- `bun run check` green.

**Implications:** No new params. Perf: `findPath` is called only inside the window (1-2 A* per item; impassable neighbors quick-reject before searching). Known non-goal: items enclosed by steel/water stay uncollected by design — nothing the tank can do, and skipping them frees the window for collectible loot.

## 86. Snapshot Must Preserve the Bonus Pickup Window — Mid-Window Restore Never Re-Opens BONUS TIME (SHIPPED)

**Decision:** `WorldSnapshot` gains `pickupWindowEntered?: boolean` and `pickupWindowTimer?: number` (Timers section). `cloneWorld` serializes both; `restoreWorld` restores them with legacy fallbacks (`?? false` / `?? 0` — the pre-window state). Snapshot type is unchanged for old saves (optional fields).

**Rationale:**
- Bug (AGENTS §7 repro): `restoreWorld` never touched the window fields. A fresh-session restore (app reload → World constructed with `false`/`0`) left `pickupWindowEntered === false` while alive power-ups sat on the field, so `checkConditions` re-opened the window at the full `POWERUP_PICKUP_WINDOW_MS` (10 s) — a mid-window save silently extended BONUS TIME. `tests/snapshot-framework.test.ts` proves the bug: 3 new tests failed on the unpatched serializer (restore kept diverged values, cloneWorld dropped the fields, legacy path kept stale values) and pass after.
- The window is gameplay state, so it must travel with the snapshot (Constitution §6: a snapshot is a complete World description). It is also BONUS-TIME pickup logic from §84/§85 — the same spectate feature whose timer this guards.
- Legacy snapshots (pre-§86) fall back to the pre-window state, which re-opens the window on next tick — the same behavior those saves always had; acceptable.

**Implications:** Old stored snapshots restore as before; new snapshots are fully faithful mid-window.

## 87. Replay Needs No Pickup-Window Changes — It Inherits §86 via the Shared Serializer (VERIFIED + GUARDED)

**Decision:** No changes to `replay/file.ts` or `replay/pack.ts`. The replay format carries world state ONLY in `initialSnapshot` (a `WorldSnapshot` produced by `cloneWorld` in `InputRecorder.startNew` — the same for browser, spectate, coop, and sim-generated replays via `tools/sim/simulation-runner.ts`); the packed frame stream is pure input (direction/fire/guard/frenzy). Playback (`PlaybackController.start`/`seekTo`/`buildKeyframes`) restores via `restoreWorld`, so §86's `pickupWindowEntered`/`pickupWindowTimer` fields travel with the snapshot automatically and the window then evolves deterministically (fixed-timestep decrement + `checkConditions`; no RNG, no wall-clock). Guard: `tests/replay-roundtrip.test.ts` — a recording that STARTS mid-window must restore the window on playback and tick it down identically, not reset it; red without §86 (`pickupWindowEntered` undefined), green with it.

**Rationale:**
- User asked whether replay serialization needed to carry the window state. Answer: it already does — through the shared `WorldSnapshot` + `cloneWorld`/`restoreWorld` serializer that §86 fixed. Duplicating the state into the frame stream would fight the input-recording design (re-simulation derives world state from inputs + snapshot).
- Legacy replays recorded mid-window before §86 restore with the pre-window fallback (window re-opens) — same pre-existing behavior as legacy snapshots (§86); recordings only ever start mid-window via exotic paths (manual start mid-session), so the exposure is minimal.

**Implications:** The single serializer remains the one source of truth for both snapshot and replay world state — no fork to maintain.

## 88. §88: Aggressive branch stall detection — freeze window no longer wasted firing at nothing (SHIPPED)

**Symptom:** `classic-s03-clear-l3-t79-seed1785643123096.replay` (0:20–0:36): during a freeze window the player sat at cell (16,5) facing left, firing at a fast enemy 3 cells away but slightly offset in Y. The 6px bullet passed above/below the 32px enemy body, so every shot missed. The aggressive branch has NO anti-stall guard (unlike T2a's `_campTicks` and navigate's `_navStuckTicks`), so the player wasted the ENTIRE freeze window (1080+ ticks / 18 seconds) in one spot.

**Root cause:** the aggressive (freeze/shield) branch's stop-and-aim code has no escape mechanism. When `scanAhead` finds an enemy (via the ±8px offset lines) but the actual bullet (6px, centered) misses, the player stops and fires indefinitely. T2a has `_campTicks` + `_antiCampSuppress` to break deadlocks; navigate has `_navStuckTicks`. The aggressive branch had neither — and during a freeze window it is the ONLY branch that runs.

**Decision:** New param `aggCampTimeoutTicks` (default **120** = 2 seconds; 0 = OFF = byte-identical pre-§88). When ON, the aggressive stop-and-aim tracks how long the player has been at the same cell zone (±1 cell, same as T2a's zone tracking). When camping exceeds `aggCampTimeoutTicks` with no kills, the AI sets `_aggCampSuppress = antiCampSuppressTicks` (reusing the existing T2a suppress param, 60 ticks) and falls through to navigate. The suppress timer prevents re-entry into stop-and-aim for 60 ticks, giving the player enough consecutive navigate ticks to actually reposition.

**Rationale:**
- The freeze window is the highest-value window in the game (enemies are helpless). Wasting it firing at nothing is the worst possible outcome — worse than navigating suboptimally.
- The suppress mechanism reuses `antiCampSuppressTicks` (not a new param) because the semantics are identical: "suppress stop-and-aim for N ticks to let the player navigate." AGENTS §10: Simple beats clever.
- MANIFEST §13 Three Gates: (1) freeze window kills are more enjoyable; (2) one param + one suppress field is simple; (3) breaking deadlocks respects the original.

**Results (2026-08-02, classic 35 stages × 20 seeds, 18000 ticks):**
- §88 ON alone (§89 OFF): **92.9%** vs baseline 92.6% = **+0.3pp**, 0 stages below 80%. No regressions.
- §88 + §89 ON (final shipped): **93.0%**, 0 stages below 80%.

**Tests:** `tests/godai-stall-exposure.test.ts` — 4 tests: default param check, stall OFF (player stays stuck 300+ ticks), stall ON (player breaks free <200 ticks), kill resets timer (productive camping unaffected).

**Implications:** Default ON (ships). The §80 turn-snap guard test was updated to disable §88 (`aggCampTimeoutTicks=0`) when isolating the guard-OFF behavior, since §88 now provides a safety net that breaks the same deadlock the guard prevents.

---

## 89. §89: Close-range enemy exposure check — don't flee from point-blank enemies (SHIPPED)

**Symptom:** `classic-s03-clear-l3-t79-seed1785643123096.replay` (1:03): the player was in close combat with an enemy, turned away (moveDir = away from enemy), and was killed by the enemy's bullet before it could dodge. The navigate branch only checks for BULLET threats (`findPathThreat`), not for enemy tanks that could fire.

**Root cause:** the navigate branch determines `_moveDir` via A*/directMove, then checks for bullet threats. But it doesn't check for enemy tanks that are aligned, close, and have a clear shot. If the player's moveDir takes it AWAY from such an enemy (fleeing), the enemy fires, the bullet is faster, and the player gets hit in the back.

**Decision:** New params `closeCombatDangerCheck` (default **1** = ON; 0 = OFF) and `closeCombatDangerRange` (default **2** = point-blank, 32px). When ON, after the navigate branch determines `_moveDir`, `closeCombatExposureImpl` checks: is there an enemy within `range` cells, aligned (same row/col, within TANK px), with no wall between (scanAhead finds enemy), AND the player's moveDir is the OPPOSITE of the enemy's direction (fleeing)? If so, cancel the move — face the enemy and fire instead.

**Critical design choices (discovered via A/B testing):**
1. **Perpendicular moves are safe** — the initial implementation caught ALL non-toward moves (including perpendicular dodges), causing -1.7pp regression. Fixed: only `moveDir === opposite(enemyDir)` (fleeing) triggers the check. Perpendicular moves are dodges and are always safe.
2. **Range 2, not 4** — at range 4, the check fired too often, cancelling legitimate navigation (retreating to defend, repositioning). A/B at 35×20: range 4 = 91.0% (-1.6pp), range 2 = 93.0% (+0.4pp), range 1 = 92.9% (+0.3pp). Range 2 is the sweet spot — the enemy is truly adjacent (32px), where fleeing is almost certainly death.

**Rationale:**
- At point-blank range (2 cells = 32px), the enemy's bullet travel time is ~8 ticks. The player's dodge reaction delay is 0 ticks (God AI), but the player must TURN to face the bullet first (1+ tick for axis-snap), then the bullet-cancellation fire must not be on cooldown. Fleeing gives the enemy a free shot at the player's back — almost certainly lethal.
- At range 4+ (64px+), the bullet travel time is ~16 ticks — enough time for the existing dodge system (findMostDangerousBullet) to handle the threat. The §89 check is unnecessary and harmful at that range.
- MANIFEST §13 Three Gates: (1) eliminating "flee and die" at point-blank is more enjoyable; (2) one param + one range param is simple; (3) facing the enemy to trade shots respects the original.

**Results (2026-08-02, classic 35 stages × 20 seeds, 18000 ticks):**
- §89 ON, range=4: **91.0%** (-1.6pp) — REJECTED (too aggressive).
- §89 ON, range=2: **93.0%** (+0.4pp) — SHIPPED. 0 stages below 80%.
- A/B: baseline (both OFF) 92.6% → final (both ON, range=2) 93.0% = **+0.4pp, 0 regressions**.

**Tests:** `tests/godai-stall-exposure.test.ts` — 8 tests: default params, perpendicular=safe, fleeing=exposed, toward=safe, wall-blocks=safe, beyond-range=safe, disabled=safe.

**Implications:** Default ON with range=2 (ships). The check is deliberately conservative — only point-blank fleeing triggers it. This ensures it catches the reported bug (player flees from adjacent enemy) without disrupting normal navigation.


## 90. Dodge Direction Persistence + Threat Hysteresis (Bug Fix)

**Decision:** Two fixes for player evasion failures found in `classic-s12-died-l0-t43-seed1322088985.replay`:

1. **Dodge direction persistence** (`dodgeDirectionImpl`): when the same threat bullet persists across ticks, return the last dodge direction if it's still `canMoveDir` + `isSafeDir`. Prevents the 1px oscillation where `canMoveDir` or `isSafeDir` flips at the sub-cell boundary, causing the dodge direction to reverse every tick (e.g., up→down→up→down, making the player effectively stationary at y=55↔56 while the bullet approaches and hits).

2. **Threat hysteresis** (`findMostDangerousBulletImpl`): for the recently-dodged threat bullet (`b.id === _lastDodgeThreatId`), widen the alignment threshold from `< TANK` (32) to `< TANK + 2` (34). Prevents the threat from flickering between detected/not-detected at the exact boundary (|bcy-pcy| = 31 vs 32), which caused the player to alternate between dodge and navigate branches every tick. New threats still use the standard `< TANK` threshold.

**Rationale:**
- Bug 1 (0:21 replay): player IS in the dodge branch, IS detecting the threat, but dodgeDirection oscillates up↔down every tick → player stationary → bullet hits. Root cause: 1px movement changes canMoveDir/isSafeDir results → direction flips → player moves back → conditions flip again → infinite cycle.
- Bug 2 (0:42 replay): player oscillates at y=95↔96, alignment boundary |bcy-pcy| = 31/32. `< TANK` detects at 31 but not 32 → threat flickers → player alternates dodge/navigate → fast bullet (8.3px/tick) hits stationary player.
- **Rejected: global `<= TANK`** — widened the threshold for ALL bullets. Caused S32 Diamond -37pp (72.5%→35%) by detecting bullets in adjacent steel corridors at exactly 32px (corridor spacing = 2 cells = 32px). The hysteresis approach only widens for the ALREADY-dodged bullet, not new threats.
- MANIFEST §13 Three Gates: (1) fixing "dodge but stand still" is more enjoyable; (2) persistence + hysteresis are simple, targeted mechanisms; (3) proper evasion respects the original's combat feel.

**Results (2026-08-02, classic 35 stages × 20 seeds, 18000 ticks):**
- Global `<= TANK`: 577/700 = 82.4% — REJECTED (S32 35%, S33 65%).
- Hysteresis (TANK+2 for recent threat only): **631/700 = 90.1%** — all 35 stages above floor. 0 stages below floor. S32 Diamond 80%, S33 Battlement 80%.

**Tests:** `tests/dodge-oscillation.test.ts` — 8 tests: persistence same-direction, persistence threat-change, persistence blocked-fallback, hysteresis new-threat-standard, hysteresis new-threat-TANK-not-detected, hysteresis recent-threat-TANK-detected, 0:42 scenario simulation, hysteresis beyond-TANK+2-not-detected.

**Implications:** Both fixes ship by default (no params — structural code changes). The persistence is scoped to the dodge branch only (reset when no threat). The hysteresis is scoped to the specific bullet being dodged (no effect on new threat detection). Together they eliminate the two reported death patterns without any per-stage regression.


## 90b. §90 A/B Test Results — Oscillation Counter-Fire Shipped (Negative Results Recorded)

**Decision:** After 35×60 A/B testing, only the **oscillation counter-fire** (threshold=3) ships ON by default. Hysteresis, persistence, and floorSnap are all OFF — each caused net regressions.

**A/B Results (35 stages × 60 seeds, classic, 18000 ticks, all params=0 for baseline):**

| Approach | Net Delta | Worst Stage | Shipped |
|---|---|---|---|
| Persistence + Hysteresis (both ON) | -1.7pp | S6 Iron Curtain -10pp | ❌ OFF |
| Hysteresis only (TANK+2 for recent threat) | -1.1pp | S14 Citadel -8.3pp | ❌ OFF |
| Oscillation counter-fire (threshold=3) | **-0.8pp** | S11/S16/S25/S26/S28 -3.3pp | ✅ ON |
| Oscillation counter-fire (threshold=2) | -0.9pp | similar | ❌ |
| Oscillation counter-fire (threshold=3, dist gate TANK*4) | -1.4pp | worse — dist gate prevents early counter-fire | ❌ |
| canMoveDirFloorSnap (Math.floor in canMoveDirRaw) | -2.6pp | S6 Iron Curtain -21.7pp | ❌ |

**Root cause of all regressions:** The `snap()` function uses `Math.round(v / CELL) * CELL`, which has a discontinuity at cell midpoints (y=56 → snap=64, y=55 → snap=48). This 16px jump flips `canMoveDir` results, causing the dodge direction to oscillate. All fix approaches that change the dodge behavior (persistence, hysteresis, counter-fire) cause cascading effects through the deterministic simulation, leading to net regressions.

**Rationale for shipping counter-fire despite -0.8pp:**
- It's the least aggressive fix (only activates after 3 consecutive direction flips — rare, only during actual oscillation).
- It addresses the user-reported bugs (0:21 oscillation → counter-fire faces bullet and fires to cancel).
- The -0.8pp is within the noise range of 60-seed testing (~1.7pp = 1 seed per stage).
- The alternative (shipping nothing) doesn't fix the reported bugs.

**Rejected approaches:**
- `canMoveDirFloorSnap` (Math.floor): breaks ALL navigation predictions, not just dodging. S6 -21.7pp.
- Global `<= TANK`: detects bullets in adjacent steel corridors at exactly 32px. S32 -37pp.
- Hysteresis alone: causes player to dodge more (stay in dodge branch longer). -1.1pp.
- Persistence alone: overrides legitimate direction switches. -0.6pp additional.

**Per-seed tick-diff diagnosis (S6 Iron Curtain seed 5):** Divergence at tick 3025. Player A (persistence OFF) recomputed dodge to 'up'; Player B (persistence ON) persisted 'down'. The 2px difference cascaded into B failing. Root cause: persistence overrides legitimate direction switches, not just oscillation.

**Params added for A/B testing:**
- `dodgeHysteresis: 0` — TANK+2 threshold for recently-dodged threat.
- `dodgeDirPersistence: 0` — return last dodge direction if same threat.
- `dodgeOscillationCounterFire: 1` — face bullet after 3 direction flips (SHIPPED).
- `canMoveDirFloorSnap: 0` — Math.floor in canMoveDirRaw (REJECTED).


## 91. Turn Cooldown (§90c) — Simulation-Layer Oscillation Prevention

**Decision:** Added `turnCooldownMs` (default 50ms ≈ 3 ticks at 60fps) to `GameplayRules` — enforced in `SimulationCombat.updateMovement()`. After a tank turns (dir changes), it must wait `turnCooldownMs` before turning again. During the cooldown, `tank.dir` is reverted to `tank.prevMoveDir`. This blocks per-tick direction oscillation at the simulation layer (the source), rather than patching it in the AI layer (§90).

**Implementation:**
- `rules.ts`: `turnCooldownMs: 50` in both `DEFAULT_RULES` and `RULES.classic`.
- `types.ts`: `Tank.prevMoveDir?: Direction` and `Tank.lastTurnFrame?: number` fields.
- `World.ts`: initializes `prevMoveDir = dir` and `lastTurnFrame = -9999` at tank creation.
- `SimulationCombat.ts`: in `updateMovement()`, before velocity integration, checks `tank.dir !== tank.prevMoveDir`; if cooldown not elapsed, reverts `tank.dir = tank.prevMoveDir`.
- `tests/turn-cooldown.test.ts`: 4 tests (cooldown blocks, same-dir allowed, OFF=no block, oscillation prevented).

**A/B/C Results (2026-08-02, classic 35 stages × 60 seeds, 18000 ticks):**

| Configuration | Win Rate | Delta vs A |
|---|---|---|
| A: no cooldown + no counter-fire | 91.7% (1926/2100) | — |
| B: cooldown 50ms + no counter-fire | 90.3% (1896/2100) | **-1.4pp** |
| C: cooldown 50ms + counter-fire | 90.4% (1899/2100) | -1.3pp |

**Key finding — counter-fire is redundant with turn cooldown (C - B = +0.1pp):**
The oscillation counter-fire (§90, `dodgeOscillationCounterFire: 1`) was designed to detect per-tick direction flips and face the bullet to cancel it. With the turn cooldown active, the per-tick oscillation cannot happen — the simulation refuses to turn faster than 50ms, so the AI's dodge direction is stable for ~3 ticks at a time. The counter-fire rarely activates (27/35 stages are byte-identical B=C). The +0.1pp net is within noise (1 seed).

**Per-stage impact of turn cooldown (B - A):**
- **Biggest regressions:** S6 Iron Curtain -16.7pp, S20 Checkers -10pp, S25 Ice Palace -10pp, S30 Eagle Nest -10pp, S4 Maze -6.7pp, S32 Diamond -6.7pp. Steel maze and ice stages are hurt most — the AI relies on rapid turns to navigate tight corridors and dodge in confined spaces.
- **Improvements:** S10 Fortress +5pp, S15 Crossroads +5pp, S26 Brick Maze +5pp, S33 Battlement +5pp. Open stages benefit — the cooldown prevents jittery micro-adjustments that caused oscillation deaths.
- **Counter-fire helped most:** S11 Lattice +5pp (C vs B) — even with the cooldown, some oscillation occurs at the 3-tick boundary, and the counter-fire catches it.

**Rationale:**
- MANIFEST §13 Three Gates: (1) preventing per-tick turning is more enjoyable (tanks feel like physical objects, not vibrating particles); (2) one rule field is simpler than AI-layer patches; (3) the original FC game had inherent turn latency (animation frames), so this respects the spirit.
- The -1.4pp regression is the cost of blocking the God AI's per-tick turning "cheat." The AI must now plan turns ahead, making it a more honest benchmark.
- The counter-fire (§90) is kept ON by default since it's net neutral (+0.1pp) and still catches the rare 3-tick-boundary oscillation. It becomes a no-op in the common case, which is the desired outcome — the simulation-layer fix is the real solution.

**Implications:** The turn cooldown is the canonical fix for oscillation. The §90 AI-layer counter-fire is a defense-in-depth fallback that activates only when the simulation-layer cooldown is insufficient (e.g., oscillation at the cooldown boundary). Future oscillation-related work should focus on the simulation layer, not the AI layer.

## 92. §87: Urgent Power-Up Pickup Priority — Close + Safe-Path Pickups Outrank Defense/Kill (SHIPPED)

> Progress-doc numbering: **§87**. Code comments use §87.

**Decision:** User directive (2026-08-02): "炸弹/冰冻/护栏 8 格内、星星/加命/护盾 4 格内、船 2 格内且路径安全时，拾取优先级 > 回防/杀敌；然后全 35 关仿真验证，下降严重的用 per-seed tick-diff 分析处理。"

New `think()` branch placed AFTER dodge (survive) and T8 (in-flight bullet aimed at the base — an immediate loss) but BEFORE aggressive/T2a/S5: a power-up within its category range AND with a safe path diverts the player immediately, overriding stop-and-aim kills and base-defense repositioning. Normal mode only (during freeze, the aggressive branch already grabs pickups when no enemy is aligned, and an aligned frozen enemy is a free kill not to interrupt).

**Params (SHIPPED defaults):** `pickupPriorityMode=1`, `pickupPriorityHighRange=8` (bomb/freeze/fence + modern emp/guard), `pickupPriorityMidRange=4` (star/tank/shield + remaining modern items), `pickupPriorityLowRange=2` (boat), plus three safety gates discovered by the tuning loop:

1. **`pickupPriorityMaxDanger=0`** — route danger (enemies strictly BETWEEN player and item, `calculateRouteDanger`) must be 0.
2. **`pickupPriorityMinEnemyDist=5`** — no fully-spawned enemy within 5 cells of the player (same radius as S5 P3.2). Added after Lattice s2 / Battlement s3 per-seed diffs: an enemy 5 cells away (or an active firefight) was abandoned while the player walked to the item, then the player stalled/stopped firing and died.
3. **`pickupPrioritySpawnRowMax=3`** — items in the classic enemy spawn band (rows ≤ 3; spawns at row 0) are never urgent errands. Added after Lattice s2/s32 diffs: diving for a "safe" pickup in the top band put the player inside the spawn corridor (s32 walked up to a fence at (1,0) while an enemy spawned at (0,0) beside it).

All params are 0-able for A/B; OFF (mode=0) is byte-identical to pre-§87 (verified via per-seed tick-diff, S6 s5 / S32 s11 IDENTICAL).

**Tuning loop (35×60 classic, paired CRN, eval-suite --compare):**

| Config | Net wins vs baseline | Notable |
|---|---|---|
| 8/4/2, danger=0 only | **-10** | Lattice -8, Star Fort -5, Battlement -4 (real); Frozen Field +6, Diamond +5 (real) |
| + nearby-enemy gate (5) | 0 | Lattice -8 persists; Battlement -4→-2 |
| + spawn-zone gate (rows≤3) | **+9** | **Lattice -8→-2, Star Fort -5→+1, Diamond +5, Frozen Field +4, Final Redoubt +3, Ice Palace +2**; no significant regression |

**Final 35×60 A/B (SHIPPED defaults):** 1899/2100 → 1908/2100 (**+9 wins**), win rate 90%→91%, suite 0.7439→0.7551, mean Δscore +0.0038 ± 0.0033 (p=0.245), net flips +54/−45. No stage moved significantly negative (the only p<0.05 move was Steel Web -0.0053 score with 0 win change = noise).

**Per-seed mechanisms found & fixed (per-seed tick-diff method, §0.B):**
- Lattice s2: diverted 2 cells to a star (enemy 5 cells away, path "clean") → stalled ~80 ticks at (8,2) → died. → nearby-enemy gate.
- Lattice s32: at lives=2, dove up to a fence at (1,0); enemy spawned at (0,0) beside it → died. → spawn-zone gate.
- Battlement s3: stopped firing mid-engagement (3 enemies) to divert up → lost the fight. → nearby-enemy gate.
- Star Fort s10: fence pickup (2 cells) + downstream cascade chaos — not surgically fixable; resolved by the gates + 60-seed averaging (Star Fort ends +1).

**Gate truths regenerated** (35×60, mean 90.9%): `TRUTH_WIN_PCT` in `tests/god-ai-regression-gate.test.ts`, aggregate floor raised 581→610 per "随收益上调" discipline. **S28 Spider floor kept at pre-§87 level** — the gate's full-suite context is order-dependent (module-level `genId()` counter, World.ts documented caveat; pre-existing, proven by stashing src: standalone 631 vs full-suite 625 WITHOUT §87) and Spider swings 13-20/20 between contexts; the 60-seed eval shows Spider 91.7% with §87 (+1).

**Implications:** SHIPPED ON. New tests `tests/pickup-priority.test.ts` (15 tests) lock the category gates, all three safety gates, tie-breaks, think() integration, the freeze-window exclusion, and shipped defaults. 120-seed confirmation of Diamond/Frozen Field is a recommended follow-up.

## 93. §88: 据守咽喉要地 (Chokepoint Holding) — Rule-1/2/3/4 Base-Defense Strategy (CANDIDATE, A/B-Tuned) _(superseded by §94 — SHIPPED default ON)_

> Progress-doc numbering: **§88**. Code comments use §88. Shipped default: mode OFF (byte-identical to pre-§88); the A/B candidate set below is ready to flip `chokepointMode=1`.

**Decision:** User directive (2026-08-02): implement 据守咽喉要地 — threat points (cells from which an enemy can directly shoot the base), threat paths (A* corridor to the nearest threat point, gated by turret facing), and the lower-half cell that can shoot the most threat paths (steel cover >> brick cover). Strategy: (1) enemy enters a threat point (or margin outside) → base threatened → kill those enemies; (2) base safe + enemies > holdThreshold → hold the chokepoint, ≤ holdThreshold → chase the enemy nearest a threat point; (3) fire at enemies encountered; (4) HIGH pickup > 回防 > MID pickup > 据守.

**Architecture:** `src/ai/god/Chokepoint.ts` (pure World-state reads: threat points via `canShootBaseFrom` + passability, facing-gated threat paths, coverage-stamped chokepoint selection, threat-state + chase-target impls) and a rule-4 branch in `src/ai/god/StrategyPlanner.ts`. Throttled plan cache (chokepointReplanTicks) like the navigateTowards cache. All gated by `chokepointMode` (0 = OFF, byte-identical).

**A/B candidate set (tuned, in DEFAULT_GOD_AI_PARAMS):** `chokepointMode=0`, `threatPointMargin=1`, `chokepointHoldThreshold=2`, `chokepointMinRow=13`, `chokepointSteelWeight=10`, `chokepointBrickWeight=1`, `chokepointFacingGate=1`, `chokepointPathsPerEnemy=4`, `chokepointMaxThreatDist=14`, `chokepointReplanTicks=30`, `chokepointChaseMaxDist=3`, `chokepointHoldMaxDist=6`, `chokepointChaseMaxPlayerDist=10`.

**Tuning loop (35×60 classic, paired CRN, per-seed tick-diff method §0.B):** the feature went through 3 A/B rounds; each round's regressions were traced to a distinct mechanism and fixed:

1. **MID-pickup branch placement (S19 seed 14):** placing the §88 MID branch AFTER T2a demoted a 4-cell shield to "keep killing" — moved before T2a (S19 -0.042 → -0.021).
2. **Chase distance gate (S15 seed 24 / S32 seed 22):** chase dragged the player across the map after an enemy 10 cells from any threat point → `chokepointChaseMaxDist=3` (enemy-to-threat-point).
3. **MID pickup must not defer to 回防 (S32 seed 17):** gating MID on `isBaseUnderThreat()` made the player abandon a 3-cell star to "defend" — removed the gate; §87's own safety gates (nearby-enemy 5 格, route danger, reachability) already make close pickups safe.
4. **Hold-arm idling (S19 seed 23):** player marched to the (30-tick cached) chokepoint, found enemies had turned away, idled → hold requires a live imminent threat (`threatChaseTarget` non-null) + `chokepointHoldMaxDist=6` march cap.
5. **Facing gate on threat-state/chase (S26 seed 12):** an armor at (12,12) facing RIGHT (away from the base below) tripped the margin check and dragged the player 14 cells to "intercept" a non-threat → `facingTowardBase` gate applied to `isThreatState` and `threatChaseTarget` (rule 3). S26 -0.019 → 0.000.
6. **Rule-1 outranks hold via chokepoint coverage (S32 seed 23):** with enemies>2 the hold arm marched to chokepoint (15,18) while a fast at (24,22) headed for the base through a lane the chokepoint could NOT shoot → when the chokepoint can't cover the imminent enemy's approach (same row/col + clear LOS to the enemy or its nearest threat point), chase wins over hold.
7. **Speed-scaled chase player-distance cap (S32 seed 10 / 48):** a 27-cell chase of a POWER tank is a lost race (player can't arrive in time) while a 25-cell chase of a slow ARMOR is winnable → `chokepointChaseMaxPlayerDist=10` scaled ×3 armor / ×2 basic / ×1.5 power / ×1 fast.

**Final 35×60 A/B (candidate set, mode ON):** suite 0.7551 → 0.7561 (+0.0010), win rate 91%→91% (unchanged), mean Δscore +0.0010 ± 0.0010 (p=0.30, no significant difference), B better/worse/tied 9/6/2085. Per-stage: S16 +0.022, S6 +0.007, S18 +0.004 (score), S26 0.000 (fixed), S32 within noise (single seed-29 regression — a spec-correct rule-1 interception of an enemy ON a threat point that loses tempo — offset by a seed-48 gain). No stage moved significantly negative.

**Per-seed verification:** all previously-fixed flip seeds (S15 s24, S19 s14/s23, S26 s12, S32 s5/s17/s22/s23/s10, S20 s1, S31 s1) are IDENTICAL to OFF when the mechanism is gated correctly; OFF (mode=0) is byte-identical by construction.

**Implications:** CANDIDATE — default OFF (shipped game unchanged), candidate set ready in `DEFAULT_GOD_AI_PARAMS`. New tests `tests/chokepoint.test.ts` (24 tests) lock: threat-point computation (LOS, steel occlusion), chokepoint selection + cover tie-break, facing gate, threat state + facing gate, chase (imminence + player-distance + speed-scaling), hold vs chase + coverage gate, rule-4 think() integration, and OFF inertness. Flip `chokepointMode=1` and regenerate the regression-gate truths to ship. A 120-seed confirmation of S32/S16/S6 is the recommended follow-up.

---

## 94. §88 据守咽喉要地 (Chokepoint Holding) — SHIPPED (default ON, supersedes §93 candidate)

> DECISIONS §93 的后续：120-seed 确认满足用户的「过关率全面提升或持平 → 计入调优文档」标准后，用户拍板**启用（默认 ON）**。Progress-doc 编号保持 §88；本条目记录发货决定与重生成的门禁真值。§93 的 (CANDIDATE) 状态被本条目取代。

**Decision:** `chokepointMode` 默认值 **0 → 1**（`src/ai/god/params.ts`）。§93 的全部调优参数（margin 1、holdThreshold 2、minRow 13、steel 10/brick 1、facingGate 1、pathsPerEnemy 4、maxThreatDist 14、replan 30、chaseMaxDist 3、holdMaxDist 6、chaseMaxPlayerDist 10 速度缩放）随默认 ON 一并生效，不再需要 A/B JSON 单独翻开关。

**120-seed 确认（S6/S16/S32，paired CRN，360 对）：** suite 0.6712 → **0.6880**（mean Δscore **+0.0091 ± 0.0057**，p=0.106 未达 0.05 显著，但方向一致为正），过关率 **83% → 85%**，B better/worse/tied **13/8/339**（无系统性负向）。分关：**S16 +0.018（93%→95%）、S32 +0.011（78%→79%）、S6 0.000（持平）**——三关全部 ≥ 持平，符合用户验收标准「全面提升或持平」，无任何关卡下降。

**发货后 35×60 全量回归（shipped default）：** mean **90.9%**（与 §87 的 1908/2100 持平，新真值 3183/35 = 90.9%），**S6 73.3→75.0（+1.7pp）、S16 95.0→96.7（+1.7pp）、S28 86.7→91.7（+5.0pp）**，**其余 32 关零变化，无任何关卡低于其 §87 真值**。

**门禁真值重生成（`tests/god-ai-regression-gate.test.ts`）：** TRUTH_WIN_PCT 更新 S6/S16 两行（S28 保持 86.7 保守值——门禁上下文下 Spider 在 13-20/20 摆动，floor 14 会因上下文噪声失败，与 §87 相同的处理）。聚合均值 90.9% 不变 → AGGREGATE_FLOOR 610/700 不变。门禁实测：**639/700（91.3%），35 关全过 floor，S28 Spider 20/20（100%）**。

**Behavior-lock 验证：** `tests/godai-split-parity.test.ts`（S0 8 种子，relaxed 后只锁 outcome）全部 outcome 不变（stage_clear ×7 + gameover ×1），无需重锁；`tests/chokepoint.test.ts` 的默认值断言改为 `chokepointMode=1`，OFF-inert 测试改为显式 `offParams()`（默认 ON 后 OFF 惰性仍保证 byte-identical 回退路径）。

**Rationale（MANIFEST §13 三门）：**
- 更有趣：敌人压境时据守咽喉要地而非无脑追杀，减少「追杀过远回防不及」的败因（§88 的 S16 +2pp / S28 +5pp 佐证）。
- 架构简单：全走 `chokepointMode` 门控，ON/OFF 一刀切，无新增系统。
- 尊重原作：据守关键通道、保护基地是 Battle City 的防守本质。

**Results (2026-08-03):** `bun run check` 844 测试全绿（含重生成后的门禁 639/700 与更新后的 chokepoint 测试），`bun run build` 成功。工具链回归：`godai-split-parity` / `god-ai-regression-gate` / `god-ai-curriculum` 全过。

**Implications:** 发货默认 = §88 全开。OFF（`chokepointMode=0`）仍可作为 A/B 对照臂（byte-identical to pre-§88）。`SKILLED_HUMAN_PARAMS`（coop/躺赢模式的 God AI）由 `DEFAULT_GOD_AI_PARAMS` 派生，因此 co-op 玩家 2 也随本次启用执行据守咽喉要地（feature 本来就是 God AI 全局策略，coop 行为因此与单机一致）。后续如需再调，直接改默认参数并以门禁真值重测。

---

## 95. Turn Cooldown 50ms → 100ms + Halt-During-Cooldown (SHIPPED)

> 用户指令：player/enemy 转弯周期限制改为 160ms（≈360 APM 超级人类水平），classic 全 35 关仿真验证——全面提升/持平则计入调优文档，下降严重则 per-seed tick-diff 逐一处理，仍难解决则报告取舍。实测 160ms 是净回归，A/B 至 100ms 后反而为全局最优，用户拍板**采用 100ms**（2026-08-03）。Progress-doc 编号 **§95**。

**Decision:** `turnCooldownMs` 默认 **50 → 100**（`src/config/rules.ts`，`DEFAULT_RULES` + `RULES.classic` 两处）。同时修复 `src/game/SimulationCombat.ts`：冷却期间被拒绝的转向请求不再沿旧方向继续滑行，而是**原地等待**（`moving=false`，`prevMoveDir` 保持）直到冷却落地——「转弯周期限制」语义上正确的实现（按键后先停再转），也是人类手感。

**Tuning loop（35×60 classic single-player corpus, paired CRN, eval-suite）：**

| 方案 | win rate | suite | net flips vs 50ms 基线 |
|---|---|---|---|
| 基线 turnCooldownMs=50（未改） | 91.0% | 0.7561 | — |
| 160ms 原始（冷却期间漂移） | 87.4% | 0.6999 | **−75** |
| 160ms + 原地等待修复 | 89.8% | 0.7393 | −24 |
| 160ms + 等待 + AI 转向承诺锁（已回退） | 89.3% | 0.7345 | −34 |
| **100ms + 原地等待修复（SHIPPED）** | **91.2%** | **0.7741** | **+5** |
| 50ms + 原地等待修复（只加等待不加值） | 88.8% | 0.7225 | −17 |

**per-seed tick-diff 定位的两个根源机制：**

1. **漂移致死（已修复）**：160ms 冷却期间坦克沿旧方向滑行 ~10 ticks——S26 s1 想转 `down` 却一直冲 `left` 过弯撞死、S12 s6 想转 `left` 却一直 `up` 卡墙。修复为冷却期间原地等待后，此类死亡消除。
2. **AI 每 tick 决策模型假设瞬时转弯（残留 −24 flips @160ms）**：160ms 下每次转弯等 ~10 ticks，dodge / T2a 瞄准 / 转身开火节奏全部被拖慢。尝试的 AI 层「转向承诺锁」（提交方向后跨冷却窗口保持）帮助振荡型迷宫关（Checkers +9、Brick Maze +6）但更伤开阔关（Quarry −6、Star Fort −6）——开阔关每 tick 重新瞄准是合法行为，净负已回退。

**为何 100ms 是全局最优：** 50ms+halt 反而比 50ms 基线更差（−17 flips）——原地等待打断了原 50ms 时 3-tick 漂移已被 AI 隐式利用的滑行补偿；160ms 超出 AI 决策模型承受阈值；100ms 恰好落在「AI 可容忍的转向延迟」与「振荡抑制收益」的交汇点，净 +5 flips（Frozen Field −2 / Battlement −2 与 Eagle Nest +4 / Thicket +3 等互抵后为正）。

**门禁与行为锁重生成：**
- `tests/god-ai-regression-gate.test.ts`：TRUTH_WIN_PCT 按 `gate-truth.ts` 新真值（mean **91.19%**，floor **612/700**）重生成。
- `tests/godai-split-parity.test.ts`：seed 55555 翻转 gameover→stage_clear（160ms/100ms 体系下该局获胜），重锁为 stage_clear；其余 7 种子 outcome 不变。
- `tests/turn-cooldown.test.ts` 机制测试传显式值，不受默认值变更影响。
- `tests/classic-ai-jam.test.ts`：shaft-recovery 测试窗口 1500 → 2400 ticks——100ms 下每次被延迟的转向等待更久，veteran 级 tunnel-out 落地从 ~656 ticks 推迟到 655–1736 ticks（实测 seeds 999/100/42/7），原窗口漏掉 seed 999 的 1736 逃出；2400 覆盖最坏情况有余量。功能本身未退化（tank 仍会凿穿侧砖逃出）。

**Rationale（MANIFEST §13 三门）：**
- 更有趣：更真实的最小转弯周期（超级人类 APM 仍被尊重——100ms ≈ 10 转/秒）同时抑制 §86c 的振荡。
- 架构简单：一个 config 值 + SimulationCombat 一处 wait 语义，无新系统。
- 尊重原作：Battle City 坦克转弯需要时间，原地转身符合原作手感。

**Results (2026-08-03):** 35×60 net **+5 flips**（1908/2100 → 1915/2100，91.2%），suite 0.7561 → 0.7741，无关卡严重负向。`bun run check` 全绿。

**细粒度确认扫描（2026-08-03，用户指令：测 110/125/140ms）：** 100ms 确认为该邻域局部最优——110ms net **−27**（suite 0.7389）、125ms net **−20**（0.7504）、140ms net **−15**（0.7473），全部劣于 100ms（且劣于 50ms 基线 0.7561）。曲线形状 100 峰值 → 110 急降 → 125/140 部分回升 → 160 再降。负翻转集中在快速重新瞄准关（Bunker Hill −13@110 / −9@140、Battlement、Twin Spires、Checkers、Citadel），无任何 ≥+5 的补偿性正面关。**结论：维持 100ms 不变，配置零改动。**

**Implications:** `turnCooldownMs=100` 为发货默认，同时适用于 player 与 enemy（同一 `GameplayRules`）。AI 层无需为转弯延迟做承诺锁——100ms 在 AI 容忍阈值内。后续若再调转弯周期，直接改该值并重跑门禁真值。

## 96. M0 基线测量 + M0.5 僵尸参数退役（SHIPPED，2026-08-03）

**Summary:** God AI 重设计 v2（plan/God-AI-Redesign-v2，已归档至 docs/god-ai-tuning.progress.md Part II.0）M0/M0.5 一次性落地：① chaos 命数 1→3；② 逐死亡事件 telemetry（`deaths[]`，含 `_lastBranch` 行为分支）；③ 死亡归因工具 `tools/diag/death-attribution.ts`；④ 三难度门禁 `tests/god-ai-hard-chaos-gate.test.ts`；⑤ 22 个僵尸/否决参数退役（interface 95→~73，归档 experimental.ts，结构化 ArchivedSelf 规避模块增强陷阱）。基线：classic 91.0% / hard 38.6% / chaos 34.6%。**关键发现：chaos 命数 1→3 几乎无提升 → 失败是 AI 反复死亡；hard/chaos 83% 死亡发生在 dodge 分支。** 详见 progress.md §II.1。

## 97. §M3 Dodge 质量：dodge 分支近距离对枪抵消（SHIPPED 后回退） _(superseded by §98)_

**Summary:** `dodgeCounterFire`/`dodgeClearanceScore` 初版 35×20 测 "chaos +3.8pp"，但 A/B 脚本传了 stageIndex 与官方口径不一致，为**口径伪影**。官方口径重测 chaos 持平偏负 → 回退 OFF。对枪抵消在任何门控下对 chaos 无发布级杠杆。详见 progress.md §II.4。

## 98. §M3 Dodge 对枪抵消：回退 OFF + Gate 确定性根因修复（2026-08-03）

**Summary:** §97 被本条取代：`dodgeCounterFire` 回退 OFF。**stageIndex 口径伪影完整机制**（killScore levelFactor → 掉宝时机 → RNG 分歧）。**Gate 确定性根因**：bun test 跨文件共享模块状态，测试突变 DEFAULT 单例污染全局 → `GodAIInput` 构造器克隆 `_baseParams` + 门禁传克隆 + 测试显式克隆，gate 在任何测试上下文下确定。方法论：所有 A/B 必须与 eval-suite/gate 同口径；20-seed 只配 screening。详见 progress.md §II.4/§II.9。

## 99. M1 决策链评分制外壳：落地 + Parity 三重验证通过（2026-08-03）

**Summary:** 新建 `src/ai/god/DecisionCore.ts`（ActionId/ACTION_WEIGHTS/DecisionContext/Candidate/runChain），think.ts 顶层链重构为「公共前缀外壳 + 8 候选体权重序循环」（dodge 1000 > interceptBase 900 > pickupHigh 800 > aggro 700 > pickupMid 600 > engage 500 > pickupLow 400 > hunt 200），候选体原样转录，evaluate() 提交即执行。**三重验收**：18 份 per-seed-diff IDENTICAL + split-parity 9/9 + 三 gate 字节持平 M0。性能 +2.6~3.3%（预算内）。M1 窗口（重构不改行为）已关闭。详见 progress.md §II.2。

## 100. M2 权重数据化：actionWeights 基础设施 + classic 重排 A/B 诚实阴性 + M2b 推迟（2026-08-03）

**Summary:** M2a SHIPPED：`actionWeights?: Partial<Record<ActionId, number>>` + `orderedCandidates`（reset() 预构建，禁止每 tick 排序）。M2c 诚实阴性：4 个权重重排实验全部持平/劣化 → **M1 链序是局部最优**，91→93% 需行为改动而非重排。M2b（selectTarget mini-scoring）推迟至 M4（零行为收益 + 高 parity 风险）。详见 progress.md §II.3。

## 101. M3 dodgeCounterFire 三轮门控全部官方口径阴性 + stageIndex 口径伪影完整机制（2026-08-03）

**Summary:** pinned 对枪三轮门控（distance / timing-aware / terrain-only）官方口径全部阴性（classic 0.0 / hard +0.3 / chaos -0.2pp）。**机制级解释**：走廊关 terrain-pinned 保命（+15~25pp）但开阔关站定送死（-10~20pp），净值为零偏负。口径纪律升级为最高优先级纪律。详见 progress.md §II.4。

## 102. M3 敌情感知 EnemyModel + survive 候选 + 命数感知 + M4 紧急对枪：机制落地，默认 OFF（2026-08-03）

**Summary:** 两个里程碑合并记录（原文编号重号）：① M3 机制层一次性落地（EnemyModel 四特征 EMA → estimatedEnemyLevel、survive 候选默认权重 0、survivalModeLives/survivalRiskWeight 命数感知、tierWeightScale/dodgeRateShrinksT2a），全开 A/B 无回退但无净增益 → 默认全部 OFF；② M4 带安全门控的紧急对枪（`hasCrossFireBulletImpl` 排除交叉火力），修正 `godaiParams` 大小写口径事故后 +0.7pp 噪声内 → 不发布。详见 progress.md §II.4/§II.10。

## 103. M5 站位提前规避（pathThreatAvoidance）：机制落地，A/B 阴性，默认 OFF + 口径事故根因（2026-08-03）

**Summary:** HUNT 候选接入 `findPathThreat` + `findSafeMoveDir` 换 cell-1 单步（与 §73 否决的 diversion 区别：不承诺 A* 路径）。触发率 ~1%，classic 0.0 / hard +0.4 / chaos -1.1pp 全噪声 → 不发布。**口径事故**：`godaiParams` vs `godAIParams` 字段名错误让 M4/M5 测了 DEFAULT vs DEFAULT；纪律：A/B 传参后必须 live probe 验证参数真实到达。详见 progress.md §II.4/§II.9。

## 104. M6 出生即一星（playerStartLevel 0→1）：首个强信号发布，hard/chaos +8~9pp（SHIPPED，2026-08-03）

**Summary:** hard/chaos `playerStartLevel` 0→1（评审决议 4 备用档）。**60-seed 确认：hard +9.0pp / chaos +7.9pp**，31/29 关变好——M0 以来唯一 >3σ 发布。靶点：玩家 93% 存活时间 0★（单发慢弹）才是根本瓶颈，非 dodge 分支。方法论固化：**先查星经济/数值配置，再动 AI 行为**。门禁真值重生成。详见 progress.md §II.5。

## 105. M7 追猎死亡探针：真追猎仅 ~3-7% + 模拟口径三重修复（playerLevel / lives / telemetry）（2026-08-03）

**Summary:** ① **模拟口径三重修复**（runSimulation 补 playerLevel 同步、lives 同步、telemetry isPlayer 过滤——诱饵坦克此前被误计，chaos 误捕 29%）：修复后 hard 48.0%（2 命真实难度）/ chaos 48.9%，门禁真值重生成；② **M7 靶点证伪**：§96 的「追猎途中死亡 39%」实为「回防途中」（85-93% 净朝向基地）；SURVIVE（死角包围）触发率 0.4-2% 低杠杆；survivalRetreat 因 hard 死亡 82.7-84% 集中在最后一命而重估为 high-value。口径纪律：直驱探针必须手动同步 playerLevel/lives。详见 progress.md §II.5。

## 106. M8 survivalRetreat 官方口径 60-seed 确认：持平偏负，不发布（2026-08-03）

**Summary:** `survivalRiskWeight`/`survivalModeLives` 60-seed：OFF 46.3% vs ON 46.1%（Δ-3）。机制低覆盖：死亡在 dodge 分支，survivalRetreat 只挂 hunt 分支（权重最低）不改 dodge 死亡本身。**教训固化：凡不改变 dodge 分支本身的候选体杠杆都趋零。** 详见 progress.md §II.6。

## 107. M9 dodgeHorizonScore 多弹道生存视界承诺闪避：机制成立但 60-seed 阴性，不发布（2026-08-03）

**Summary:** 探针证伪「多弹道评分替代二元 isSafeDir」原假设（交叉火力方向误选 ~0%）；真杠杆 = **承诺不足**（起点可闪避 + 从未清带 = hard 31.8% / chaos 35.0%）。`dodgeHorizonScore` 机制上修复 S0 seed2（OFF tick2158 死 vs ON 零死亡）——**证明 dodge 分支行为改动有杠杆，是修法问题**——但 60-seed chaos **-3.5pp**（承诺闪避牺牲防守/杀敌效率，基地被拆）。**方法论升级：双目标评估（生存 + 效率）**。详见 progress.md §II.7。

## 108. M10 dodgeHorizon 门控变体（时间余量 + 距离）：chaos 确凿阴性，不发布（2026-08-03）

**Summary:** 时间余量门控正确把 M9 的 chaos 损失 -3.5pp 减到 -2.4pp（承诺质量问题被识别）但无法转正；MARGIN6 60-seed hard +1.6pp / chaos -2.4pp——**hard/chaos 方向相反是常态，参数全局无法发布**。真实成本是 dist +25px（dodge fireRate 恒 1-2%，非火力是位置）。可复用信号：S13 走廊关双难度大正。dodge 分支第三次同构证伪。详见 progress.md §II.7/§II.11。

## 109. M11 星经济下一档：playerStartLevel 1→2（SHIPPED 后用户否决） _(superseded by §110: 用户否决，回退 1★，2026-08-03)_

**Summary:** hard/chaos `playerStartLevel` 1→2。60-seed 确凿强信号：**hard +9.4pp（46.3→55.7%）、chaos +7.5pp（47.7→55.1%）**，双双破 50% 目标（6-7σ）。实现 + 门禁真值重生成（双 54.7%，floor 357）均完成。**但用户评审否决**（§110）：「起始两星有点儿欺负敌人」——difficulty 配置影响人类体验，非仅 God AI。数据与实现保留在历史中供参考，不再生效。详见 progress.md §II.8。

## 110. 用户否决 §109：hard/chaos 起始二星回退为一星（2026-08-03）

**Summary:** §109 的 `playerStartLevel` 1→2 **回退为 1**（hard/chaos）。门禁真值回退 §105（hard 48.0% / chaos 48.9%，floor 310/316）。**星经济杠杆边界明确：0★→1★ 可（M6，+7.9~9.0pp），1★→2★ 不可（M11，+7.5~9.4pp）——出生星级合理上限 1★。** 剩余路径全部是非星经济杠杆（M4 标量参数 CMA-ES / 生存站位 / 泛化语料）。已证伪方向汇总见 progress.md §II.11。详见 progress.md §II.8。

> **God AI v2 纪元（M0–M11）全文归档说明**：本段 §96–§110 为压缩索引；每个决策的完整正文
> （Rationale/Implications/方法论教训）见 **docs/god-ai-tuning.progress.md Part II**。
> v2 设计文档（plan/God-AI-Redesign-Review.md、plan/God-AI-Redesign-v2.md）已于 2026-08-03 删除，
> 核心内容归档于 progress.md §II.0。

## 111. 星盾扩展到所有难度（引擎改动）+ HP 模型探针选靶（2026-08-04）

**Decision:** ① **引擎改动（用户拍板）**：3★ 星盾从 classic-only 扩展到**所有难度**（`SimulationCombat.bulletHitsTank` 移除 `difficultyKey==='classic'` 守卫；hard/chaos/relax 三星玩家被致命击中掉回 2★ 不死，HP 回满 + 短暂无敌）。② **HP 模型量化探针**（`tmp/probe-hp-model.ts`，35×20 官方口径）产出死亡生态数据。③ **AI HP 规则选靶**：星盾交换伤害**否决**（覆盖率 <2%），玩家 HP 缓冲感知（低血保守）为唯一靶点，敌人剩余命中数补刀推迟。

**Rationale（探针数据，全链路）：**
- **星盾扩展 win 影响在噪声内**：hard 336→341（48.0→48.7%）、chaos 342→341（48.9→48.7%）。根因：**玩家几乎到不了 3★**——lvl3+ 存活时间仅 hard 1.3% / chaos 1.6%，700 局仅触发 10/15 次（星是稀缺资源，M7 结论复现）。星盾扩展机械正确但当前星经济下覆盖率极低。
- **磨血是主导死亡模式**：hard 70.1% / chaos 67.5% 的死亡吸收了 ≥3 发子弹（死前平均吸收 2.06/1.95 个 100-damage 单位）；危险区时间（hp≤130，一发即死）占存活时间 hard 19.8% / chaos 19.0%——**玩家约 1/5 时间在死亡边缘仍按满血节奏打**。死亡星级 99% 是 1★。
- **③ 星盾交换伤害否决**：触发率 0.9-1.1% 生命、覆盖 <2% 存活时间——与 M5（触发率 1% 无统计力）、M2c（覆盖不足）同构。**① 低血保守**直接命中 19-20% 危险区时间；**② 敌人补刀**有 M0.5 damagedArmorBonus（-8.4pp）历史负信号，先不做。
- **经典零漂移**：守卫移除对 classic 是 no-op（原已生效）——classic 门禁 637/700 不变，split-parity 不受影响。

**Implications:**
- **门禁真值重生成**：hard/chaos 均 341/700（48.7%），聚合 floor 310/316→**315/315**（-3.7pp 公式）；per-stage 数组更新。
- **星盾在经典仍有实际意义**（classic 0★ 起步、升级节奏不同），hard/chaos 需先解决星经济或 AI 拾取才见星盾价值。
- M12 靶点：**玩家 HP 缓冲感知**——低血（≤1-2 发）时 dodge 分支提高承诺度/回防，高血时允许以血换进度（绝境保基地），默认 OFF + 官方口径 60-seed 双目标评估（§107/§108 纪律）。
- 探针 `tmp/probe-hp-model.ts` 保留为可复用工具；星盾扩展已单测覆盖（classic-star-shield.test.ts 6 用例：全难度 3★ 触发、2★ 仍死、一次性）。

## 112. M12 玩家 HP 缓冲感知：诚实阴性（2026-08-04）

**Decision:** M12（§111 探针选靶的「玩家 HP 缓冲感知」）实现并评估后**不发布**——5 个新参数全部默认 OFF（`playerHpAwareness` / `hpDangerHits` / `hpDangerCommitMargin` / `hpTradeHits` / `hpTradeCommitPenalty`，`src/ai/god/params.ts` + `ThreatAssessor.dodgeDirectionImpl` 的 HP 自适应 commit 余量，pool 模型专属）。实现 + 5 个单元测试（danger 放宽 / trade 加严 / 阈值门控 / pool-only / 默认惰性）作为实验旋钮保留。

**Rationale（20-seed screen + 60-seed 确认全链路）：**
- **机制设计**（§111 探针驱动）：危险模式（hits-to-die ≤ hpDangerHits=2，覆盖 70% 磨血死亡的时段）把 horizon commit 余量放宽到 hpDangerCommitMargin=1（低血拼命逃脱、不再振荡）；交换模式（hits-to-die ≥ hpTradeHits=4，满血缓冲）加严余量（接受部分闪避保持火力）。引擎事实：1★ pool 玩家 315HP / 敌弹 100 伤害 = 4 hits-to-die。
- **20-seed screen（700 局/臂）**：hard OFF 48.7 / H 50.6 / H+danger 50.4 / H+both 50.6；chaos OFF 48.7 / H 47.7 / H+danger 47.1 / H+both 47.1。**M12 delta 全部 0.0~-0.6pp（噪声内）**——danger/trade 相对 horizon 基底无增益。
- **60-seed 确认（2100 局/臂）**：hard H+both +2.3pp（46.7→49.0）/ chaos -2.2pp（47.7→45.5）——**反向抵消净零**；且该信号是 horizon 基底 H 的签名（20-seed H 单独 +1.9pp，M12 delta ≤0.6pp），不是 HP 感知本身。
- **第三次证伪 dodge 分支**：M3 对枪、M9/M10 horizon 承诺、M12 HP 门控 horizon——同一签名（hard +~2pp / chaos -~2pp，§108 MARGIN6 一模一样）。机制级解释：磨血死亡（70%）分布在整个战场（追猎/回防途中），dodge 分支只在「有子弹飞来」的瞬间介入，其逃脱质量改变不了总量；而 chaos 开阔关的站定/过度承诺闪避仍损失清关效率。**HP 知识本身正确（危险区 19-20% 存活时间、99% 死在 1★），但它在 dodge 分支上没有可兑现的行为杠杆。**

**Implications:**
- 已证伪方向再 +1：dodge 分支行为族（对枪/clearance/horizon/HP 门控）全部关闭，剩余 knobs 保留供 M4 CMA-ES 标量搜索复用。
- hard/chaos 的杠杆不在 dodge：M7 归因（回防途中 85-93% 死亡在 chase/engage 分支）与 M4 标量参数搜索是下两个方向。
- 方法论：本次 20-seed 阴性（≤0.6pp）与 60-seed 一致（±2.2pp 反向）——20-seed 判断「不发布」正确，60-seed 额外确认了硬/混沌反向签名；阴性结果的 60-seed 价值在于排除「隐藏的强信号」。

## 113. M13 全场压力撤退（outnumberedFieldRetreat）：SHIPPED，hard +2.3pp / chaos +0.6pp（2026-08-04）

**Decision:** 新增并**发布** `outnumberedFieldRetreat`（1）/ `outnumberedFieldEnemies`（3）/ `outnumberedFieldDistCells`（15），**pool 模型专属**（`w.rules.combatModel==='pool'` 门控——classic instant 无磨血死亡，91% 门禁字节不变）。机制：`selectTarget` 中，场上存活敌人（全场 Cluster C，非 P4.2 的 9 格附近计数）≥3 且玩家距基地 >15 格时返回防守位，停止过度深入。这是**第一个无 chaos 负向的 God AI 行为机制**（此前 M3/M4/M9/M10/M12 全部 hard+/chaos- 反向签名）。

**Rationale（全链路数据驱动）：**
- **M13 死亡场景探针**（telemetry 扩展 hp/liveEnemies 字段，35×20）：hard/chaos 一致——死亡分支 dodge 84-86%（复现 §96）、**距基地 >20 格 39%**、**场上敌人数 >3（满编）70-73%**、1★ 死亡 80-85%、死亡时 HP 100% ≤100（磨血最后一击，复现 §111）。最大单格 = 「>20格 × dodge」32-33%：玩家在敌满编时过度深入，1★ 单发慢弹火力无法对抗 3-4 只敌人的持续输出 → 磨血死亡。
- **机制演化**：P4.2（已发布）只在 3 只**聚集到 9 格内**才撤——探针证明 70% 死亡时全场 4 只活着但未必聚集，玩家仍深入送死。M13 把撤退条件升级为全场计数。
- **A/B 官方口径**：20-seed ON3@15 hard +2.7pp / chaos +2.6pp；**60-seed hard +2.3pp（~2.1σ）/ chaos +0.6pp**——双难度方向一致、死亡↓（0.68→0.65 / 0.71→0.68）、基地失守↓（hard -33 / chaos -16）、无任何关卡系统性回退。**ON4@10 反向探明有害**（等满编 4 只才撤 + 10 格阈值 → 过于被动：hard -5.3pp / chaos -3.3pp，基地失守 +35/+11）——磨血从 3 只就开始了，不是 4。
- **M5 重测（口径事故纠正）**：§103 的 A/B 因字段名错误测了 DEFAULT vs DEFAULT，本次修正后首测：hard +0.4pp / chaos -0.8pp，死亡数降但 win% 不升（换路更被动）——确认 M5 中性偏负，不发布。

**Implications:**
- 门禁真值重生成（20-seed）：hard 341→**360/700（51.4%）**、chaos 341→**359/700（51.3%）**，aggregate floor 315/315→**333/333**。classic 门禁不动（pool-only）。
- 机制上首次绕过「dodge 分支无杠杆」死结：改的是**站位纪律**（不深入满编战场）而非闪避质量——死亡 39% 发生在 >20 格，从源头减少「深入 → 被围 → 磨死」。
- 下个候选：M4 标量参数 CMA-ES（把 outnumberedField* 加入 SEARCH_SPACE）；或探索 ON3@15 的边界（enemies=3 已验证，enemies=2 过度保守，需谨慎）。

## 114. M4 标量参数 CMA-ES 首轮：子集过拟合阴性 + M13 阈值双重复证（2026-08-04）

**Decision:** M4 首轮 CMA-ES 搜索**无发布**。基础设施落地：`outnumberedFieldEnemies` / `outnumberedFieldDistCells` 加入 `optimize-godai.ts` 的 SEARCH_SPACE（20 参数）。搜索（hard，6 代表关 × 6 seeds，v5 fitness，floor 0.4，25 代）最优解 = 12 参数变动（aimError→0、campTimeoutTicks 90→120、replanInterval 50→36、outnumberedFieldDistCells 15→9 等，子集 win 72→89%）；**60-seed 全量官方口径验证：hard 45.8%（-0.9pp）/ chaos 44.7%（-3.0pp）双难度均劣化——子集过拟合**。聚焦隔离 `outnumberedFieldDistCells=9`：hard -1.8pp / chaos -2.5pp，基地失守 +92/+70——**shipped 的 15 被复证为最优**（更早撤退 = 更被动 = 基地失守，ON4@10 有害模式复现）。`outnumberedFieldEnemies=3` 搜索保持原值（复证）。

**Rationale（方法论教训，最重要）：**
- **screening 代理失真**：6 关 × 6 seeds 的 v5 fitness 代理与全 35 关 × 60 seeds 官方口径**方向相反**（子集 89% → 全量 45.8%）。aimError=0（完美瞄准）等改动在子集上讨好，在全量上伤害（其他关的瞄准价值不同）。教训：**标量搜索的 fitness 代理必须足够接近官方口径**——子集必须更大/更有代表性，或每 N 代用全 35 关 × 10 seeds 做早期验证剪枝。
- **M13 阈值边界闭合**：3（enemies）× 15（dist）是局部最优——搜索方向（9）与既有 ON4@10 实验都证明收紧有害；放宽方向无信号。M13 参数从 SEARCH_SPACE 移除价值低（已确认边界），保留供未来全量口径搜索复用。
- **game-feel 安全检查**：`SKILLED_HUMAN_PARAMS.aimError = max(0.15, god+0.15)` 有绝对下限——即使 God aimError=0 人类代理仍是 0.15（不会变完美），代理派生安全。
- **未发布的 12 参数集无害**：全量验证已证伪，不进入默认；classic 门禁不受影响（无代码行为变更，仅 tools 文件改动）。

**Implications:**
- M4 round-2 的正确配置：全 35 关（或 ≥20 关代表性子集）× 8-10 seeds，v5 或 v7（需先校准 hard/chaos 的 eval-refs），generations 15-20，配 mid-search 全量验证剪枝。计算成本 ~1-2h/难度，需用户确认是否投入。
- M13 阈值边界闭合（补 dist=20 验证，review 补强）：60-seed 全谱 dist=9 44.9/45.2 → **dist=15（shipped）46.7/47.7 → dist=20 46.7/47.4**——15 是明确峰值（9 有害 -1.8/-2.5pp，20 持平无信号），收紧/放宽两个方向均已实证。
- 口径注明：M4 验证为**非配对比较**（OPT/dist9/dist20 为独立运行，对照 = M13 轮的 OFF 基线 46.7/47.7，同 seeds 同形状）——n=2100 下 1.8-3.0pp 差距足以定论，但严格口径应同 run 配对（后续轮次注意）。

## 114.1 M4 round-2 建议配置（评审决议，未执行）

**Decision:** 不启动（需用户确认计算投入 ~1-2h/难度）。若启动：全 35 关 × 8-10 seeds × 15-20 代，每 5 代全量剪枝验证，fitness 用 win%（或先校准 hard/chaos eval-refs 再 v7）。关键前提：**screening 代理必须接近官方口径**（§114 教训——6 关子集 89% vs 全量 45.8% 方向相反）。

## 115. M4 round-2 全语料 CMA-ES：SHIPPED，pool 模型 +5.0/+8.3pp（classic 还原表保 91%）（2026-08-04）

**Decision:** M4 round-2 搜索**发布**（14 个参数写入 `DEFAULT_GOD_AI_PARAMS`，pool 模型专属——新增 `CLASSIC_MODEL_PARAMS` 还原表，`GodAIInput.reset()` 在 `combatModel==='instant'`（classic）时把仍是 M4 默认值的参数还原为 M4 前值）。60-seed 官方口径发布验证：hard **53.0%**（+4.0pp vs §113 基线 49.0）/ chaos **56.6%**（+8.3pp vs 48.3）/ **classic 91.2%**（还原表生效，门禁字节持平）。hard/chaos 门禁真值重生成 381/407（54.4%/58.1%），floor 333→354/380。

**Rationale（§114 round-1 过拟合教训的反面教材）：**
- **搜索本身跑全 35 关**（round-1 是 6 关子集 → 60-seed 全量双难度劣化 -0.9/-3.0pp）。round-2 每代 fitness 即全量口径 → 60-seed 交叉验证保持强信号（hard 53.2/chaos 56.9；CHAOS_BEST 50.3/56.8 略逊 → 选 HARD_BEST 统一发布）。
- **game-feel 剥离验证**（用户约束：不改变 game feel）：aimError 0.03→0.12 与 suboptimalPathProb 0→0.14 是搜索噪声——剥离后 54.0/56.5（增益保持甚至略高），且这两个是 `SKILLED_HUMAN_PARAMS` 派生参数（human 代理 = max(0.15, god+0.15)），保留默认即 human 不受影响。**发布集 = HARD_BEST − aimError − suboptimalPathProb**。
- **性能安全检查**：`replanInterval 50→1`（每 tick A* 重规划）是 hard 增益引擎（剥离后 hard 48.8% 塌回基线），实测 S23 迷宫关 +52% 模拟耗时（29.3→44.7ms/局 ≈ +0.003ms/tick，远在 6ms sim 预算内）——可接受。
- **classic 回退根因**：classic instant 无磨血死亡，搜索调优的激进进攻（replan=1/更宽 threatRange/更短 camp）在 instant 下净负 -2.4pp（91.0→88.6）。还原表只在参数仍为 M4 默认时还原（显式 A/B 覆盖优先），stage 适配照常叠在还原之上（S0 open-defense baseRace 11→14 属 §60 正常适配）。
- **逐关回退检查**（60-seed 同 seed 集）：最大单关回退 S10 -11、S4 -9、S18 -8（均 < 12 = 20-seed 门禁 4 wins 的 60-seed 换算 margin），改进 S3 +20/S23 +14/S1 +11/S9 +11/S21 +11；净 +89 wins。
- **M13 参数被搜索调离**：outnumberedFieldEnemies 3→4、outnumberedFieldDistCells 15→26、outnumberedEnemyCount 3→5（P4.2 关闭）——replan=1 + 更宽 threatRange 下防御更动态，附近撤退/过密撤退反成负担；60-seed 组合验证净正。

**Implications:**
- **方法论闭环**：screening fitness 代理必须等于官方口径（全 35 关）；搜索最优解必须过「剥离 game-feel 参数」+「60-seed 全量 + 逐关 margin」+「性能微基准」三重闸门才可发布。
- pool/instant 双默认机制确立：新增 pool 行为参数 = 写 DEFAULT + 还原表 + 门禁真值重生成（classic 靠还原表保字节）。
- 发布路径（DEFAULT 默认参数，浏览器真实行为）= 60-seed 验证路径，无口径偏差。
## 116. 自杀秒回（suicide quick-return）：实现 + 诚实阴性（2026-08-04）

**Decision:** 新增 God AI 决策候选 `suicideReturn`（DecisionCore `ActionId` 权重 1100，高于 dodge 1000），默认 **OFF**（`suicideReturnMode=0`，字节持平）。当 5 个前置条件同时满足时，player 站立饮弹、无闪避、立即在出生点重生以处理基地威胁。新文件 `src/ai/god/SuicideReturn.ts`；新参 `suicideReturnMode / BulletTimeTicks(60) / EnemyDistTicks(300) / MinLives(2) / SpawnDistCells(6)`。

**5 前置条件（对应任务）：** ①敌人处威胁点（可直击基地）；②出生点能处理该敌（0-1 转直击 或 出生点比 player 当前位更近）；③player 库存命数充足；④player 全速亦需 >5s 才够到该敌；⑤1s 内被致命弹命中（扫描所有敌方子弹，非仅最近 ctx.threat）。

**验证（60-seed A/B，hard 全 35 关逐档）：**
- 无 base 威胁守卫版本（仅按任务 5 条）：hard 子集 **net -1 flips**（S23 seed14 回归）——player 在基地其实不会沦陷时盲目自杀换命属浪费。per-seed tick-diff 定位：OFF 臂（不自杀）dodge+存活并胜出，ON 臂自杀丢命后仍保不住基地。
- **根因**：God AI 自身防御（基地护墙 + T8 拦截）已能处理基地威胁，故「主动换命换位置」绝大多数时候是负资产。
- **修复**：加 base「活跃子弹威胁」守卫（`findBulletThreatToBaseImpl` 非空才触发）+ `_suicideStanding` 站立状态机（防 S30 每 tick 冻结，284→5 次/run）。修复后 hard 全 35 关 25-seed A/B **净 +0 flips（0 to-win / 0 to-lose，全程 tied）**——安全无回归，但触发率降至 0.3%，不提升过关率。

**Rationale:**
- 任务期望「全面提升过关率」未达成：现有防御已兜底基地危局，强制自杀无法净增益；守卫版本保安全（不劣化基线）。
- 保留开关默认 OFF = 保持 91.2/53.0/56.6 基线字节不变（AGENTS §2.3 确定性承诺）。
- 接受诚实阴性：这符合本项目「诚实阴性优先发布」惯例（§39/§96/§112 等）。

**Implications:**
- 策略完整实现、单测 30 项覆盖（辅助函数 + 候选端到端 9 场景）、typecheck/lint/format 绿。
- 待真正提升基地危机处理，应优先改进 base 防守行为本身（如已 SHIPPED 的 M13 站位），而非强制自杀换命。
- 诊断工具保留：`tools/diag/ab-diff.ts`（跨难度 A/B）、`tools/diag/diag-suicide*.ts`（频率/条件瓶颈/事件日志）。
## 117. 自杀秒回条件①变体（mode 2 STAND / mode 3 CHARGE）：诚实阴性（2026-08-04）

**Decision:** 按取证建议重启 §116——把触发条件从条件⑤（濒死）改挂到条件①（敌人进入威胁点），新增两个变体：`suicideReturnMode=2`（STAND：站立等弹，超时 `suicideReturnStandMaxTicks=300` 兜底 + 超时后 `_suicideStandSuppress` 防重提交）与 `=3`（CHARGE：不闪避、直线冲锋威胁敌）。均**保留**基地活跃子弹守卫（S23 修复）。默认仍 OFF（mode=0，字节持平）。新增参数仅 `suicideReturnStandMaxTicks`。

**实现要点：**
- 健康 player 无法靠「站立饮弹」快死（pool 229HP 需 2-3 发），故 mode 2 用超时兜底、mode 3 主动赴死——两者都避免 §116 S30 站立冻结病理（单元测试抓到 mode 2 超时后立即重提交的二次冻结，用 suppress 修复）。
- 执行中交易用弱检查 `anyThreatPointEnemyImpl`（仅条件①）而非全量前置——冲锋/站立中途不会因 player 已拉近距离而中止。

**验证（A/B：35 关 × 120 seeds × {hard, chaos}，36000 ticks，官方口径 no-stageIndex，3 臂并行，~8 min/15 workers）：**
- 触发率：mode 2/3 均 **378/4200 runs（hard 9.0%）**、**539/4200（chaos 12.8%）**——比 §116 mode 1 的 0.3% 高约 **30-40 倍**，交易确实发生（非「从未触发」的假阴性）；平均每次交易仅 ~36-39 ticks（0.6s），多数以死亡/威胁解除快速收场。
- 翻转：**B hard net +1（7胜/6负）、B chaos net +0（11/11）、C hard net -2（3/5）、C chaos net +3（12/9）**——8400 runs 上净翻转 ±3，全部在二项噪声带内（±3 ≈ 0.1σ，可忽略）。
- 基地防守：base_destroyed Δ vs A = **-1 / +0 / +2 / -3**（4200 runs 的 0.05-0.07pp）——无结构性改善。

**Rationale:**
- 即使触发率放大 30-40 倍、让交易真实发生，过关率与基地失守率依旧纹丝不动——确认 §116 阴性是**机制性**的，不是「触发太少」：击毁弹出膛→命中基地中位 14-15 ticks（0.24s），交易（饮弹→死亡→重生→护盾→转向→开火）物理上追不上；且健康 player 换命是净成本而非免费交易。
- 按 §88 方法论：净翻转在噪声带内 = 不发布。维持默认 OFF。

**Implications:**
- 自杀换命路线（⑤或①触发）至此双路证伪；基地防守的正确方向仍是站位/覆盖类改进（M13 站位、§88 据守、收掉 27 局 player 自毁）。
- 工具沉淀：`tools/diag/ab-suicide-v2.ts`（三臂并行 A/B，含触发率 + 结果分类修复）、sim-worker/runner 新增 `commitCounts` 可选遥测（默认关，字节持平）。
- 实现与 10 项新单测（mode 2/3 触发、超时、防重提交、威胁解除清除、shielded 清除、mode1 对照）均绿。

**追加取证（FLIP-TO-LOSE 种子 per-seed-diff + decision-probe，2026-08-04）：** chaos S10 seed 17/46（B、C 均输）与 hard S35 seed 8（B、C 均输），6 个翻转全部是 base_destroyed。三个种子、六个臂对的首次分歧 tick 全部锚定在自杀交易的 commit tick，机制可归纳为三条、同根：
1. **冻结劫持（STAND）**——hard S35 s8 t2361 / chaos S10 s17 t2083：基地**满血 120/120** 时第一发基地弹 + 威胁点敌人即触发；commit 把 `_moveDir=null`+`_fire=false`，player 在远离基地处原地冻结。冻结期间基地被打（S35：120→84/39 ticks），player 失去位置（滞留 1-5 行、hp 243→29），基地 t3103 沦陷；A 臂（不触发）继续回防清关（t8370）。
2. **开火抑制级联（两 mode 通用）**——chaos S10 s46 t2375/2874、s17 t4995：每个 commit tick 都压掉一枪（`_fire=false`），整局累计 30-89 个 commit tick（A 臂 t2a 计数 287/307 vs B/C 272/292），每次延迟一 tick 的击杀顺序级联（diff 可见 e3/e4、pb0/pb1 的单 tick 漂移），把必赢局拖成基地失守。
3. **冲锋劫持（CHARGE）**——hard S35 s8 t2361：不冻结而是朝（错误的）威胁敌横切，同样抛弃回防路径。

**根因（与 §116 S23 同源、放大 30-40 倍）：** 条件①+单弹守卫在「基地仍可防守」（满血 + 防守计划在跑）时即触发——**一发在飞基地弹不是基地沦陷的证据**（120 HP 缓冲 + A 臂自身的回防/据守能化解同一威胁）。§116 mode 1 安全只因条件⑤让命近乎免费且触发率 0.3%；mode 2/3 去掉成本保护却没加任何「基地必死」证据，于是以健康一条命 + 抛弃正在生效的防守去赌博 ~40 次，净效果为零（A/B 翻转散乱、净 ±0-3 正是两条机制对冲的写照）。

## 118. §117 守卫升级（baseHp 阈值 + 防守位失守）A/B — 仍为诚实阴性，机制性证伪（2026-08-04）

**Decision:** 按根因修复方向（守卫只验证了「有一发弹在飞」，未验证基地真会沦陷），为 mode 2/3 增加两个严格死局守卫参数（默认 0，字节持平）：`suicideReturnBaseHpFrac`（基地 HP ≤ 该比例 × baseMaxHp 才触发）与 `suicideReturnDefendDistCells`（player 距基地超过该格数=防守位失守才触发）。A/B 工具新增 `--strict` 臂 D（mode2+strict）/ E（mode3+strict），参数可调（默认 0.5 / 8 格）。

**验证（120 seeds × 35 关 × {hard, chaos}，5 臂 42000 sims，36000 ticks）：**
- 触发率降约 38%：hard 378→236 runs、chaos 539→350 runs——满血基地 + 有防守的假阳性被过滤（§117 hard S35 s8 的 FLIP-TO-LOSE 已消失）。
- 但净翻转**未转正**：hard D +1（1胜/0负）/ E +0（0/0）；chaos D −1（0/1）/ E −2（0/2）。跨难度净 = D 0、E −2。B/C 复现上一轮数字逐位一致（确定性）。
- 关键证据：chaos S15 seed 114 在所有四臂（B/C/D/E）都 base_destroyed——严格臂在该 seed 也 commit（6 ticks），即在「基地濒死 + player 远」的最理想时点执行交易，基地仍在交易延迟内沦陷。chaos S2 seed 73：D（站立）8 commits 存活，E（冲锋）8 commits 反而新增一个 B/C 没有的 base_destroyed 翻转。

**Rationale:**
- 「加死局证据」假设被否：即便只在基地真会沦陷时交易（低 HP + 无法回防），**0.24s 击毁弹窗 vs 0.5-1s 饮弹-重生延迟**的机制鸿沟不变——交易执行的恰恰是最需要防守的窗口，基地在交易延迟内照常沦陷。第三条独立证据链（⑤触发 §116、①触发 §117、①+死局证据 §118）全部归于同一条物理结论：换命回城在时间上不可能救基地。
- 保留默认 OFF（mode=0 字节持平）；mode 1/2/3 及其默认参数行为逐字节未变（守卫 param>0 才激活）。

**Implications:**
- 自杀换命路线三路证伪收官。基地防守的正确抓手仍是**位置/火力类改进**（M13 全场压力撤退、§88 据守咽喉、清除残留的 27 局 player 自毁基地——基地护墙开火守卫，性价比最高）。
- 新参数作为可调 A/B 旋钮保留（`--base-hp-frac` / `--defend-dist`），若未来敌人模型/时间窗改动（如子弹减速）使换命窗口可行，可复用。
- 单测 46 项（新增 6 项严格守卫：满血拒绝/低血触发/近基地拒绝/远基地触发/mode3 对照/默认惰性）全绿；`bun run check` 全绿。

## 119. 固化策略调试方法论：run-forensics 分层取证（2026-08-04）

**Decision:** 把本次自杀秒回调试沉淀为可复用取证工具链：跑 20/60/120-seed 仿真时，除胜率外必须能产出分层细节数据，用于理解详情/找规律/定位瓶颈。新增：
- `tools/sim/simulation-runner.ts` 增加 `forensics: true` 选项（默认关 → 逐字节不变），每次运行返回 `RunForensics`：
  1. 终局快照 `terminal`：player 命数/HP（可承受打击数，vs 100 基准）/距基地/星级/存活、base HP（可承受打击数）/护墙完好数、**每个存活敌人**（type/HP/距 player/距 base/AI tier）、**每发在飞敌弹**（位置/方向/距 player/距 base/ETA/命中数经济学 hitsToDie）；
  2. 失败前 10 ticks 行动+生效规则日志 `lastActions`（每 tick：branch 候选/_moveDir/_fire/位置/HP/命数/基地 HP）；
  3. 全程事件史 `events`：player 送命（tick+位置+击杀者 kind）、杀敌（tick+位置+被杀 kind）、拾取道具（tick+位置+类型）；
  4. 最终库存 `inventory`（按道具类型的拾取计数）。
- `tools/sim/sim-worker.ts` 透传 `forensics` 标志 + 通用 `failureCause`/`failureKillerKind`（自毁基地判定）。
- 新 CLI `tools/diag/run-forensics.ts`（`--seeds/--difficulty/--stages/--max-ticks/--set k=v` 参数覆盖，可 A/B 取证）：聚合报告 [0] 结果构成（base vs lives 比例 + 自毁）/ [1] 基地被毁上下文 / [2] 没命上下文 / [3] 历史（送命/杀敌/拾取的时刻百分位 + 格子热点 + 库存 + 终局星级）/ [4] 失败学（击杀者 mix、最差关、失败局最后 10 ticks 的规则分布）；`--json` 输出每失败局完整 forensics（`--trace-wins` 含胜局）。

**Rationale:**
- 本次调试（§116-§118）暴露的痛点：只看胜率无法区分「从未触发」「触发了但有害」「机制性无效」——§116 mode 1 靠触发率、§117 靠 per-seed-diff + decision-probe 逐 tick 才能定位「满血基地假阳性」。把这三类证据（终局上下文/历史事件/失败窗口动作）固定进每次 sweep 的默认工具箱，下次不再从零搭取证。
- 只读观察（无 RNG 消耗、不改 World），默认关 ⇒ 既有 42k sims 的 A/B 基准逐字节不变；`--set` 复用 §115 还原表外的参数面，无需新基建。

**Implications:**
- 用法：`bun tools/diag/run-forensics.ts --seeds 120 --difficulty hard,chaos --json tmp/fx.json`（失败局 JSON 可直接喂 per-seed-diff/decision-probe 定位分歧 tick）。
- 口径约定：hitsToDie 基准伤害 100（与 §116 取证一致）；敌弹 ETA/对齐口径与 countIncomingThreats 一致；距离为曼哈顿格（基地中心 (208,400)）。
- 后续 A/B 发布默认附一份 forensics 摘要（结果构成 + 两类失败上下文 + 最后 10 ticks 规则分布），作为 §88 门禁的补充证据。

## 120. 自毁基地 32 局取证 + 采集脚本迭代（off-by-one / bullet-dir / --from-json）（2026-08-04）

**Decision:** 用 §119 的 run-forensics 采集 hard/chaos 120 seeds 全部自毁基地局（hard 14 / chaos 18 = 32 局，0.33%/0.43%），并按调试过程迭代采集脚本三轮：
1. **shot 事件 off-by-one 修复**：bullet_fired 事件在 `input.endFrame()` 之后才被消费，此前读到的 branch/dir 是**下一 tick** 的状态（S6 s43 的致命下射被记成左射）。修复：在 `sim.tick()` 后立即快照本 tick 决策态（fxTick），事件处理用快照。
2. **朝向改取子弹真实弹道**：tank 转弯当帧的 `tank.dir` 会偏离子弹轴向（S33 s81 致命左射记成朝上）——shot 事件的 dir/towardBase 改用 `e.bullet.dir`（地面真值）。修复后**致命一枪指向基地区比例 29/32 → 32/32（100%）**。
3. **--from-json 子集重跑**（本次用户方法论要求）：迭代调试重跑失败局时，**只跑前期已识别失败的 (difficulty, stage, seed) 组合**，不再全量 stage×seeds（本次验证：32 局 2.1s vs 全量 8400 局 ~4min；确定性 ⇒ 复现同一失败清单）。

**自毁取证结论（32 局）：** 全部为**朝基地区内直射**（wall intact 0-6/8，护墙已破缺口）；开火路径 branch：**t2a 26（81%）**（正是 §74 范围注故意不放门控的停射站点）、navigate 4、aggressive 1、powerup 1；开火时 player 距基地 4-17 格（中位 ~6-9）；朝向 down 18 / right 9 / left 5。

**Rationale:**
- 只重跑失败组合：全量重跑 8400 局 ~4 分钟，而 120-seed 的失败局通常 <2000 局；脚本迭代（取证口径修复）不应付出全量成本。`--from-json` + `--kinds`/`--selfkill` 过滤即此流程的固化（同一机制可用于任何「重取失败局数据」场景）。
- 事件 off-by-one/bullet-dir 属于采集口径 bug：错口径会导向错误根因（曾把 4/32 误判为「非瞄准射击」）。

**Implications:**
- 自毁根因明确：**t2a/aggressive 停射在护墙已破时沿缺口朝基地区开火**——修复方向 = 给停射站点加「弹道穿越基地区」守卫（§74 范围注的剩余缺口），而非 §116-§118 的换命路线。
- 流程固化：`bun tools/diag/run-forensics.ts --from-json <旧JSON> --kinds base_destroyed --selfkill --json out.json`（失败组合子集重跑）；完整取证 JSON：`tmp/fx-120-final.json`（32 局自毁明细 `tmp/fx-selfkill-v2.json`）。
- 该子集重跑流程已固化为**迭代调试标准流程**：AGENTS.md §4 Step 7（failure subset only）——任何取证/采集脚本迭代后重跑失败局只准用 `--from-json` 子集，不再全量 stage×seeds（语料本身变更时才允许全量）。
## 121. t2a/aggressive 停射自毁守卫 selfFireBaseGuard SHIPPED（2026-08-04）

**Decision:** §120 取证根因（t2a 81% 直射基地区、护墙已破缺口）的修复：新增 `selfFireBaseGuard`（0=OFF / 1=strict / 2=lenient），默认 **2**（lenient，120-seed A/B 胜出），classic 经 CLASSIC_MODEL_PARAMS 还原 0（§115 纪律，字节持平）。

**机制：**
- `shotReachesBaseImpl`（FireControl.ts）：沿子弹**真实中心线**（6px 弹道，非 scan 的 ±8px 偏移线）做地形行走——环砖/环钢 STOP（安全）、非环钢 level<3 停、非环砖犁穿、base 格或 2×2 基地区矩形重叠（含 3px 边缘擦碰，hard S16 s82）→ true。坦克**故意不算遮挡**（敌人可闪避，正是 §120 机制）。
- 守卫挂在三处：ENGAGE(T2a) 停射、AGGRO 冻结窗停射、`shouldFireInDirImpl`（aggressive navigate fall-through 开火入口）。strict(1) 一律抑制；lenient(2) 仅当无敌人身体重叠 6px 走廊（±19px 带）时抑制——保住贴脸重叠击杀。

**A/B（35 关 × 120 seeds × hard+chaos × 3 arms）：**
- A(0) 基线 50.6%/50.3% → B(strict1) **hard −29 / chaos −24 flips**（82K guardBlocks，过度抑制合法击杀）→ C(lenient2) **hard +12 / chaos +8 flips，Δbase_destroyed −7/−12**（guardBlocks 仅 16K）。strict 在全量口径下是净负资产：堵掉的自毁少于丢失的击杀。
- 子集验证（32 局自毁语料）：strict 0 残局（10 局转胜）；lenient 14 残局但全量口径净正——子集只看自毁，不能代表全量。

**Rationale:**
- 守卫只解决「弹道穿越基地区」一个几何缺口，不动 §74 的双偏移线防御逻辑——三闸门（好玩/简单/守正）全过。
- 坦克非遮挡是有意的：若把坦克当遮挡，lenient 变 strict，重复测量到的 −24~−29 回归即此。
- classic 不启用：instant 1-HP 战斗无缓冲余量，从未 A/B 过，§115 纪律要求字节持平。

**Implications:**
- 新观测 `_selfFireGuardBlocks`（AI 内部计数，不序列化）经 sim-worker → ab-fire-guard.ts 作触发率代理。
- 新工具 `tools/diag/ab-fire-guard.ts`（arm A/B/C 显式覆盖，不依赖 DEFAULT）；测试 27 项（helpers + T2a/AGGRO 端到端 + shipped-default 不变量）。
- 剩余缺口：lenient 残局自毁（enemy dodge-between-fire-and-impact）属机制性残余，未全消除；若后续要归零需在「开火后子弹飞行期」再校验，成本高于收益，暂不做。

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
- **零陈旧**：memo 生命周期严格在单 tick 内，不跨 tick——这正是 §68 否决的 cross-tick 缓存（0.5s 陈旧致 S6 胜率 72%→40%）缺失的粒度保证。
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
- §68 否决的是 **cross-tick** 缓存（0.5s 陈旧崩 S6）；within-tick memo **零陈旧**（endFrame 每 tick 清），是 §68 响应性要求粒度下的安全形态。`selectTargetUncached` 只读 World 状态与 params、不耗 RNG。
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
- 首个实现（无 revision、60-tick 兜底）A/B 冒烟 score 700/700 tied，但 **bench 揭穿**：ticks=1172649 vs 基线 1169769、wins 316 vs 317，13/350 单元分歧（含 S32 seed1021 outcome 翻转）。eval-suite 的 paired 比较是 score 粒度，抓不住 tick 级分歧——**per-seed tick-diff 才是诚实判据**。

**Results（同机 A/B，经典/35 10 games/warmup=2 与 chaos/35）：**
- 确定性：修复后 classic bench `ticks=1169769 wins=317/350` 与关闭版逐字节一致；全量 350 单元扫描 **0/350 分歧**；per-seed-diff S15 seed1007 IDENTICAL；eval-suite hard/chaos 各 4200/4200 tied。
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
- 确定性：350 单元扫描 0/350 分歧（outcome/ticks/killCount）；per-seed-diff 4/4 敏感种子 IDENTICAL（S15 seed1007 / S32 seed1021 / S6 seed41 / S26 seed12）；eval-suite hard/chaos 各 4200/4200 tied；三难度基线签名逐字节不变（classic 1169769/317、hard 1639097/177、chaos 1777415/195）。
- Wall（同机 A/B，带热身轮 + order=ab/ba 双向）：**chaos −27.7% / −8.3%**（ab 序 B 臂 13401ms 疑似系统负载离群；perTick 两轮均 favor A）；classic ±14% 噪声带内（单场 ~3ms，收益被机器负载淹没）。chaos 收益超过 §127 profile 预估（dig A* 单次成本高于 corridor，corridor-fail 是全网格探索）。

**Implications:**
- 测量纪律（§127 教训延续）：wall A/B 必须先热身并双向跑（order=ab/ba）——首轮 JIT/页面缓存 ~700-1000ms 会被误记到被测开关头上（无热身首测时双臂差异完全被顺序支配）。
- pickup 可达性已达纯 memo 上限；再压需算法级变更（空间索引等），违反 simple-beats-clever（MANIFEST §10）。

## 130. 全难度命数统一为 3 + GOD AI 基线重测（SHIPPED，2026-08-05）

**Decision:** `src/config/difficulty.ts` 四难度 `startLives` 全部统一为 **3**：relax 5→3、hard 2→3
（classic/chaos 已为 3）。随后按 gate-context 官方口径（35 关 × 20 seeds，18000 ticks）重测
hard/chaos 门禁真值，更新 `tests/god-ai-hard-chaos-gate.test.ts` 的逐关 truth + aggregate floor，
并刷新 `docs/god-ai-tuning.progress.md` 基线表。

**Rationale:**
- 用户指令（plan/tasks.chat.md）：所有难度 player 初始 3 命。
- §105 曾将 hard 真实难度定为 2 命（此前被 3 命伪口径高估 ~6pp），但命数差异使 hard/chaos
  的差距掺杂了玩家资源因素；统一 3 命后，四难度差距只剩星位（relax/hard/chaos 1★ vs classic
  0★）与敌人 AI 层级分布（DIFFICULTY_TIER_DISTRIBUTION）——难度=更聪明的敌人，而非更少资源。
- 测量纪律：门禁真值以**门禁自身上下文**为准——`genId` 跨进程模块计数器引入上下文噪声
  （chaos 实测 7 关各 ±1 wins、总量 407→408），独立进程的 eval-suite 与门禁进程实测逐字节一致，
  但不同测量日的记录允许 ±1 级漂移（§127/§129 的字节级确定性声明不受影响）。

**Results（2026-08-05）:**
- **hard**（2→3 命）：35×20 **54.4%→61.6%**（431/700，+7.2pp，35 关无回退）；60-seed **60.6%**。
  aggregate floor 354→**405**（truth −3.7pp 惯例）。
- **chaos**（命数未变）：35×20 **58.1%→58.3%**（408/700，7 关 ±1 上下文噪声）；60-seed 56.5%。
  aggregate floor 380→**382**。
- classic（配置未动）：门禁不变。`eval-refs.json` 为 classic 口径（30 seeds），不随本变更漂移，未重生成。

**Implications:**
- hard 距 v2 目标（80%）仍有 ~18pp；命数与 §99 的 chaos 结论一致——调命数本身不是达标路径
  （M0 实测 chaos 1→3 命几乎无提升），后续达标仍需行为杠杆（M13 方向）。
- 门禁 floor 禁止静默下调；本次 hard floor 上调 354→405 属「收益随真值上调」，符合 §I.5 规则 6。
- 浏览器侧 hard/chaos/relax 初始命数均随 difficulty 配置生效（World.ts 运行时读取），无其他硬编码。

## 131. T8 拦截射程 pool 2→8/12：60-seed 诚实阴性（不发布，2026-08-05）

**Decision:** `t8MaxInterceptDistCells`（pool 默认 2，classic 8）放宽到 8/12 的 A/B 被否决，维持 2。
不发布 = 不改默认值。

**背景:** hard 35×120 扫描发现失败 91% 是 base_destroyed（31/35 关），Battlement 1.7% 过关、117/120
基地被毁，凶手 59% fast 坦克——方向 A 假设「拦下飞向基地的子弹」能保基地。

**Results（官方口径，无 stageIndex）:**
- 20-seed 筛选：t8=8 → 421/700（−10）、t8=12 → 418/700（−13）vs 基线 431/700。
- 60-seed paired（baseline vs 8，CRN 2100 对）：mean Δscore **−0.0069 ± 0.0042**（t=−1.65, p=0.0995），
  B 好/坏/平 220/254/1626，suite 0.4410→0.4352，win 61%→60%；无单关显著（均 p≥0.05）。
- 机制确认：direct-drive 探针显示 t8=2 时 T8 分支几乎不触发（0-6 ticks/20 局，Battlement 124），
  t8=12 时触发量 6-60×（225-750 ticks/20 局）——机制真实激活但净负。

**Rationale（为何负）:** T8 权重 900，只低于 dodge(1000)——放宽后玩家反复离开当前战斗
去追拦截点，放弃击杀节奏（Labyrinth 9→6、Oasis 12→9、Crossfire 13→12）。且敌人是
多弹连射，拦下一发挡不住下一发；玩家 42% 时刻离基地 9+ 格，轨迹拦截点近基地，远处根本
够不着。这是 P3 漫游约束 / §68 diversion / M8 survivalRetreat 的同一教训：**「离开战斗去
防守」的机制用杀敌效率换防守，净值为负**。

**Implications:** 基地防守瓶颈应从「敌人到达基地前」解决，而不是「子弹已离膛后」：
方向 B（快车逼近基地的威胁权重）与方向 C（无钢墙关防守参数再校准）优先。T8 维持
2 格作为「玩家恰好在基地旁」的兜底。classic 不受影响（CLASSIC_MODEL_PARAMS t8=8，
恢复逻辑按值判断，本次未改任何默认值）。

## 132. 方向 B：selectTarget 威胁评分按 kind 速度 × 距基地距离加权（诚实阴性，不发布，2026-08-05）

**Decision:** 新增旋钮 `fastBaseApproachWeight` / `fastBaseApproachRangeCells` 并 A/B，三臂全非正，
默认保持 0（OFF，byte-identical）。不发布 = 不改默认值。

**背景:** Battlement 深度取证（§131）——凶手 59% fast 坦克、玩家 42% 时刻离基地 9+ 格。
方向 B 假设：基地威胁评分里按「kind 速度 × 距基地远近」加权，让玩家优先回头打逼近基地的
快车（threatRange=23 下静态 kindThreatWeight fast=2 vs basic=1 无法表达「快车 6 格比
basic 3 格更急」）。

**实现:** 基地受威胁块的评分加一项
  term = weight × speedRatio(kind) × clamp01((range − distToBase) / range)
speedRatio = BASE_SPEED_CPS[kind] / BALANCED_ENEMY_CPS（fast 1.2、basic 1.0、power 0.95、
armor 0.85），逼近因子在基地环上为 1、线性衰减到 range 处为 0。weight=0 短路为 0。

**Results（官方口径 35×20，paired CRN）:**
- w500/r10 → 423/700（−8）；w1000/r12 → 411/700（−20）；w800/r8 → 430/700（−1）。
- Battlement 全臂纹丝不动：1/20 → 1/20/1/1；direct-drive 30 seeds 1/30 → 1/30 逐字一致。
- 机制确认激活：direct-drive 探针显示 Battlement 基地威胁模式激活 26.6% 的 tick
  （≈ Ramparts 25.6%），其中 ~45% 的时刻有 fast 在基地 10 格内；全局行为确实变化（S26 +5）。

**Rationale（为何负）:** ① 作用域错位——项只在「基地已受威胁」块内重排「追谁」，不改变
「何时回防」；Battlement 基地中位死亡 tick ≈ 3244，威胁模式激活时快车已在基地环开火，
为时已晚。② fast（4.5 cps）比 1★ 玩家（4.19 cps）还快——追快车数学上徒劳，把目标从
可击杀威胁换到追不上的快车 = 净负（w500 下 S2 −3、S23 −4；w1000 下 S12 −5、S31 −4）。
这是 §131/T8 的同一教训：**基地防守瓶颈不能靠「威胁块内重排目标」解决**，方向 A/B 都是
「敌人已到位后」的补救，杠杆在「敌人到达前」。

**Implications:** 方向 C（无钢墙关 baseRace/maxPlayerDist/M13 距离再校准——让玩家在快车
到环前更早回防）是下一步。旋钮保留默认 0（同 `dodgeHorizonMaxDistCells` 先例）。classic
不受影响（继承默认 0）。

## 133. 方向 C：brick-heavy 关防守距离再校准——诚实阴性（不发布，2026-08-05）

**Decision:** 新增 §133 适配块（`brickHeavyDefenseWallRatio`=0.9 阈值 + 3 个适配距离值），
A/B 三臂全负，默认保持阈值 0（OFF，byte-identical）。不发布 = 不改默认值。

**实现:** `computeStageAdaptedParams` 在 §60 open-defense 之后加一块：当
brickWallRatio ≥ 0.9（恰好 6 关：S0/S3/S14/S30/S33/S34，纯砖无钢墙）时覆盖三个距离——
baseRaceRangeCells↑（更早 race 触发）、maxPlayerDistFromBase↓（受威胁更早回防）、
outnumberedFieldDistCells↓（M13 更早回防）。注：§60 在这 6 关把 race 从 18 压到 14，
方向与直觉相反，§133 的初衷是把它们改回 20-24。

**Results（官方口径 35×20）:**
- mild（race20/maxDist20/field16）→ 424/700（−7）；brick-heavy 6 关 62→55/120（−7）。
- balance（22/18/12）→ 411/700（−20）；6 关 42/120（−20）。
- tight（24/14/8）→ 402/700（−29）；6 关 33/120（−29）。
- 重灾区正是目标关：S3 Crossfire 13→2/4/0（−11~−13 毁灭性）、S34 Final Redoubt 16→15/11/4、
  S14 Citadel 10→9/5/6。仅 S0 +4（mild）、S30 +1（mild）微升。Battlement 1→2/0/0 噪声级。

**Rationale（为何负）:** race 范围 20-24 使「玩家必须比敌人显著近才能继续打」几乎常驻触发，
maxDist 14-20 使基地一受威胁玩家就离开中场战斗回防——玩家整局往返奔跑，击杀节奏清零，
敌人聚集后基地照样失守（只是从「被打爆」变「被围困」）。这是 §113 M13「ON4@10 太被动，
hard −5.3pp」的放大版。**brick-heavy 关的失败不是「回防太晚」——收紧回防系统性有害。**

**Implications:** 与 §131（拦子弹）、§132（追快车）合流，Battlement 三方向全部证伪：基地被毁
是症状，根因是 1★ 火力/机动追不上 4.5cps 快车。剩余杠杆：方向 D（在防守位拦截基地车道，
不出防位追）、或接受 Battlement 为 hard 的 Boss 关。旋钮保留默认 0。classic 不受影响。

## 134. 方向 D：防守位停射拦截基地车道敌人（SHIPPED，2026-08-05）

**Decision:** 新增候选 `defenseIntercept`（weight 550，插于 pickupMid 与 engage 之间）并
SHIPPED：`defenseInterceptMode=1`、`defenseInterceptMaxDist=12`、`defenseInterceptRangeCells=15`
（pool 默认）。classic 经 CLASSIC_MODEL_PARAMS restore 0（未 A/B，instant 1-HP 无 HP 缓冲）。

**机制:** 玩家距基地 ≤ 12 格（防守位附近）时，若某存活敌人已与基地对齐且无遮挡
（enemyCanShootBase——下一发就能毁基地）、且与玩家同排/同列（拦截弹道可命中），则
停射拦截（turn to face + fire，复用 T2a 的 self-fire base guard——绝不穿基地开火）。
与 §132（威胁重排追快车）的本质区别：**不离开防守位**。

**Results:**
- 20-seed 筛选三臂全正：m8/r15 +8、m12/r15 +11、m8/r20 +8（vs 基线 431/700）；弱关全线上涨
  （Ice Palace 10→15、Thicket 8→11、Bastion 7→8、Battlement 1→3）。
- 60-seed paired（m12/r15）：hard mean Δscore **+0.0076 ± 0.0056**（p=0.17，suite 0.4410→
  0.4474；S32 Diamond +15pp、S34 +10pp、Battlement 0.175→0.200）；chaos **+0.0144 ± 0.0055
  （p=0.0087 显著，suite 0.4069→0.4284，Oasis +8.4pp p=0.042）**。双难度方向一致净正，无任何
  难度回退 → 符合 M13 发布先例。
- 门禁真值重测（gate-context 35×20）：hard 431→**442/700（63.1%）**、chaos 408→**420/700（60.0%）**；
  Battlement hard 1→3（首次离开地板）。classic 门禁 637/700 字节不变。

**Rationale（为何这次成立）:** 前三个方向全部「离开当前战斗」（拦子弹、追快车、回防）→
用击杀节奏换防守，净负（§131-§133）。方向 D 是第一个**留在防守位开火**的机制：敌人与
base 对齐的瞬间（破砖进入 row 23-25 / base 列走廊）正是它最脆弱也最危险的时刻，玩家在
base 列上方与它同列的概率最高，一枪命中即解除威胁——零机动成本，且天然不破坏击杀节奏
（拦截本身就是击杀）。S32 Diamond 意外大赚（钢墙关玩家在防守位的机会窗口长）。

**Implications:** Battlement hard 1/20→3/20 但仍是最弱关（目标 >50%）。下一杠杆：把
enemyCanShootBase 静态判定升级为「预测敌人将进入基地车道」（提前 1-2 格拦截）；或接受
其为 Boss 关。三门禁 + split-parity 全绿。
