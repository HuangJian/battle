# Design Decisions

> Key decisions. Full details in linked documents.
> 编号体系：§1–§9 为基石决策，其余为分类索引。

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

**Current baseline**: `wall=2531ms perTick=0.0020ms` (classic/35 stages/10 games/warmup=2).
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
