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

**Current state**: 91.9% mean (post-§70 base-ring guard), 0/35 below floor, 0 stage overrides. Default params frozen.

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

**Implications:** Default `evasionSteelOcclusionBrickRatio = 0.1` is ON (S6/S32 only). `evasionSteelOcclusion = 0` stays the explicit master switch; the gate auto-enables on qualifying stages. Tooling: `tools/diag/ab-test-steel-occlusion.ts --brickGate R`, `per-seed-diff --brickGate R`.

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
