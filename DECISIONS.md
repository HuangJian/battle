# Design Decisions

> Key decisions made during MVP development, as requested.

---

## 1. Sprite Rendering: Programmatic Canvas Drawing

**Product spec says:** Assets = PNG + JSON

**Decision:** Draw all sprites programmatically using Canvas 2D primitives (rectangles, arcs, paths). No PNG assets.

**Rationale:**
- Keeps the MVP self-contained — no need to source or create bitmap assets
- Pixel-art style is fully achievable with canvas drawing
- Theme support is trivial: just change colors in the theme config
- Smaller bundle size (37KB vs. potentially MBs of PNGs)
- Future: can add PNG sprite support by extending SpriteFactory without changing game logic

---

## 2. Audio: Web Audio API Synthesis

**Product spec says:** Audio = HTML Audio / Howler.js

**Decision:** Use Web Audio API to synthesize 8-bit style sound effects at runtime. No audio files, no Howler.js dependency.

**Rationale:**
- Zero audio assets to manage
- Procedurally generated sounds (beeps, sweeps, noise) fit the retro aesthetic
- No additional dependency
- Future: can add Howler.js for music streaming without changing the AudioManager interface

---

## 3. Tile System: 26×26 Sub-block Grid

**Decision:** Use a 26×26 grid of 16px sub-blocks. Tanks are 2×2 sub-blocks (32×32px). Playfield = 416×416px.

**Rationale:**
- Matches classic Battle City proportions (13×13 tiles, each subdivided into 2×2)
- Sub-block granularity allows precise brick destruction
- 416×416 is a good internal resolution; CSS scales it up with `image-rendering: pixelated`
- 16px sub-blocks are large enough for clear visuals, small enough for satisfying destruction

---

## 4. Stage Data: TypeScript Config (not JSON)

**Product spec says:** Resources in JSON format (assets/maps/stage01.json)

**Decision:** Stage data is defined in TypeScript config files (`src/config/stages.ts`) for the MVP.

**Rationale:**
- TypeScript provides type safety and IDE autocompletion for stage data
- No async loading needed — stages are bundled at build time
- The data structure is JSON-compatible and can be extracted to `.json` files later
- Architecture supports the transition: just replace the import with a `fetch()` call

---

## 5. Classic Stages: Authentic NES Layouts (35 stages)

**Reference:** `github.com/artF412/BattleCity-HD-Reforged` (core `stageData.ts` + `stages.ts`),
which itself derives the maps from `github.com/FrontHeads/tanchiki` (original Famicom layouts).

**Decision:** Ship the 35 authentic classic stages. Raw numeric level grids live in
`src/config/stageData.ts` (`LEVELS` + `ENEMY_FORCES`); `src/config/stages.ts` holds a
codec that decodes each 13×13 numeric level into the engine's native 26×26 char grid
(one char per 16px sub-block) and builds `STAGES`.

**Rationale / best-choice论证:**
- The authentic data uses a 13×13 grid of *numeric tile codes*, where each code names a
  material **plus which of the four 2×2 sub-cells it fills** (whole / half / quarter brick
  & steel). This reproduces the original's partial brick/steel pieces — the signature look.
- This engine's terrain is already a 26×26 sub-block grid (`GRID=26`, `CELL=16`) and the
  renderer draws each 16px cell independently, so decoding to a 26×26 char grid preserves
  partial fills **losslessly** with no engine changes beyond the loader.
- The base eagle (code `15`) is decoded into the 2×2 sub-blocks at rows 24–25 / cols 12–13,
  which exactly matches `BASE_POS` and the renderer's base-ruins region — so base detection,
  destruction, and rendering all work unchanged.
- Enemy spawn order uses the authentic `ENEMY_FORCES` strings (a=BASIC, b=FAST, c=POWER,
  d=ARMOR), mapped to this engine's `TankKind` values. Bonus-enemy cadence (every 4th) is
  unchanged.
- Keeping the raw numeric data separate from the codec makes the data trivially diffable
  against the reference and easy to extend (append a 13×13 grid + 20-char force string).

---

## 5. Movement Alignment: Perpendicular Axis Snapping

**Decision:** When a tank moves, the perpendicular axis is snapped to the nearest 16px cell boundary every frame.

**Rationale:**
- Ensures tanks can navigate through 1-tile-wide corridors
- Allows turning only at grid intersections (classic Battle City behavior)
- The snap distance is at most 8px, which is imperceptible at normal movement speeds
- Simpler than tracking "last turn position" and aligning on direction change

---

## 6. Enemy AI: Tactical Intelligence Framework

**Decision:** Enemy AI is now the **Tactical Intelligence Framework** (`src/ai/`),
implemented per `plan/Tactical-Intelligence-Framework.md`. It replaces the old
weighted-random direction picker entirely.

- **Single decision pipeline for every enemy:** `World → Perception → Situation
  Analysis → Goal Evaluation → Decision → Action Planner → Execution`
  (`src/ai/TacticalIntelligence.ts`).
- **Three thinking layers, distinct time scales:** strategic (~20 s, stable
  long-term objective), tactical (~5 s, dynamic goal + route target), reactive
  (every tick, bullet avoidance + committed-dodge hold).
- **Intelligence is configuration, not code.** Tiers `none / rookie /
  soldier / veteran / commander` live in `src/ai/config.ts`
  (`INTELLIGENCE_LEVELS`) with capability flags + dynamic goal weights.
  A tank's tier is **rolled at spawn time** from the per-difficulty
  distribution table `DIFFICULTY_TIER_DISTRIBUTION` (in `Simulation.
  updateSpawning`); **tank kind no longer implies a tier** (`KIND_TO_LEVEL`
  is retired). The **only** difficulty→AI lever is that distribution — difficulty
  never scales the capability numbers (`DIFFICULTY_AI` / `resolveConfig`
  retired). `none` is a separate minimal classic branch (`updateNoneTank`),
  outside the perception/goals/dodge pipeline. `teamwork` is split: issuing
  directives is exclusive to the active Commander; obeying is universal,
  gated per-tier by `compliance` (None = 0, deaf). New tiers = append one
  `INTELLIGENCE_LEVELS` entry + a distribution weight; no engine change.
- **Dynamic goal scoring** replaces fixed priority lists (`evaluateGoals`):
  each candidate goal (attackBase / attackPlayer / destroyWall / retreat /
  regroup / advance) gets a weighted score from situation factors; the highest
  wins. Weights are tier-owned, so "smarter" tanks make better judgements.
- **Bullet avoidance** scales with intelligence: prediction depth (how early a
  bullet is seen), dodge probability, and a delayed-reaction model. Lower tiers
  react late and fail to dodge more often.
- **Commander system:** a commander is the coordination role. The active
  commander is the **alive** `commander`-tier tank with the highest `spawnSeq`
  (per-World monotonic counter assigned at `createTank`, recomputed every tick
  by `Simulation.recomputeActiveCommander()` — no election). On death the
  previous-born commander regains command; a 1 s office delay (`commanderTimer`)
  is overwritten on each succession. **Every** `commander`-tier tank — including
  inactive ones — gets the +15% combat-profile boost (DECISIONS §29, D10
  carve-out); only the newest-born issues directives. If 2 commanders are
  already alive, a rolled commander is **downgraded to veteran** (no boost). A
  commander broadcasts lightweight directives (pushLeft / pushRight / defendBase /
  attackTogether / spreadOut) every ~20 s; obedience is gated per-tier by
  `compliance` and cached per broadcast. The commander never overrides an
  autonomous tank.
- **Imperfection model:** `reactionTime`, `aimError`, `routeNoise` make higher
  tiers commit fewer mistakes while never becoming flawless (dodge probability
  clamped to ≤ 0.95).
- **State lives on the World.** The per-tank brain is the `AIState` field on
  `Tank` (renamed from the old 3-field struct to a flat, serializable
  `AIBrain` — still shallow-cloned safely by `RecoverySystem`). All entropy
  flows through `world.rng` (`AGENTS.md §2.3`), so the framework is fully
  deterministic and survives snapshots/replays.

**Rationale:**
- Satisfies the plan's Definition of Done: same pipeline for all enemies,
  config-driven intelligence, separated strategic/tactical thinking, coordinating
  (non-overriding) commander, dynamic scoring, scalable bullet avoidance,
  configurable imperfection, determinism, and extensibility for future bosses /
  tower-defense / replay / mods.
- Difficulty now arises from *better decisions*, not stronger stats — exactly
  the plan's Vision.
- The previous `aiState` "can be replaced with different strategies" note
  (DECISIONS §6 old) is now realized: the strategy is the framework, and tiers
  are data.

**Testing:** `tests/tactical-ai.test.ts` guards determinism, no-stall
  navigation, strategic-goal stability, tier-roll distribution + the
  classic-zero-RNG gate, command authority (highest-spawnSeq active
  commander, succession, 1s office-delay overwrite) + directive broadcast,
  floor (attempt-based quota) + cap (≤2 alive, downgrade-to-veteran),
  the +15% commander boost carve-out, intelligence-scaled bullet avoidance,
  compliance (one roll per directive, cached, None deaf), the None classic
  branch, and snapshot round-trip of the command-authority fields.

---

## 7. Game Loop: Fixed Timestep with Accumulator

**Decision:** Use a fixed timestep (1000/60ms) with an accumulator. Max 5 simulation steps per render frame.

**Rationale:**
- Deterministic simulation (required for future replay system)
- Stable physics regardless of frame rate
- 5-step cap prevents spiral-of-death on slow devices
- Product spec requires 60 FPS — fixed timestep ensures consistent gameplay speed

---

## 8. Input: Per-frame Edge Detection

**Decision:** `endFrame()` is called once per render frame (not per simulation tick) to clear "just pressed" state.

**Rationale:**
- Menu navigation and state transitions need per-frame edge detection
- If cleared per-tick, multiple ticks per frame could miss input
- If never cleared (e.g., in menu state), keys would repeat every frame
- Calling once per frame at the end of the loop is the cleanest solution

---

## 9. Base Destruction: All Cells at Once

**Decision:** When a bullet hits any base sub-block, all base sub-blocks are destroyed simultaneously.

**Rationale:**
- In classic Battle City, any hit on the base ends the game
- The base is 2×2 sub-blocks; destroying one would leave a visual gap but the game should end immediately
- Destroying all cells ensures `isBaseDestroyed()` returns true on the next check
- The destroyed base is rendered as ruins for visual feedback

---

## Presentation Upgrade Decisions

### 10. Presentation Adapter: Event-Driven Visual State

**Decision:** The PresentationLayer observes the World (read-only) and consumes GameEvents to build its own visual state. It does not modify the World.

**Rationale:**
- Preserves the Simulation → World → Renderer architecture
- GameEvents already exist for audio; presentation reuses the same stream
- Visual state (particles, animation transitions, camera shake) is ephemeral and belongs in the presentation layer, not the World
- Game.ts passes events to both AudioManager and PresentationLayer

---

### 11. Canvas Resolution: Playfield-Only (416×416) + HTML HUD

**Decision:** Reduce canvas to 416×416 (playfield only). Move HUD and menu to HTML/CSS overlay elements positioned around the canvas.

**Rationale:**
- The simulation only uses the 416×416 playfield; the 96px HUD sidebar was purely visual
- HTML/CSS HUD enables modern typography, smooth animations, and responsive layout
- Separating UI from game canvas is explicitly required by the upgrade plan
- Canvas becomes simpler — no need to handle two rendering zones

---

### 12. DPR-Aware Rendering with Offscreen Buffer

**Decision:** Use an offscreen canvas at logical resolution (416×416), then blit to a DPR-scaled display canvas. This gives crisp pixels on retina displays.

**Rationale:**
- Current canvas is blurry on retina/HiDPI displays
- Offscreen buffer keeps drawing code simple (logical coordinates)
- Display canvas is sized to `416 * DPR` for pixel-perfect output
- CSS scales the display canvas responsively

---

### 13. Animation System: Time-Based with Visual Components

**Decision:** Introduce time-based animation using `performance.now()` elapsed time. Each entity gets a `VisualComponent` that tracks animation state (name, elapsed time, direction). The animation system computes the current frame from elapsed time and FPS config.

**Rationale:**
- Time-based animation is frame-rate independent (required by upgrade plan)
- Visual components decouple visual state from simulation state
- Frame computation: `frame = floor(elapsed / (1000 / fps)) % frameCount`
- Supports smooth transitions between animation states (idle → move → destroy)

---

### 14. Particle System: Pool-Based with Configurable Emitters

**Decision:** Implement a pool-based particle system. Particles are pre-allocated and reused. Emitters are configured via data (position, velocity, lifetime, color, size, gravity).

**Rationale:**
- Pool allocation avoids GC pressure during gameplay
- Configurable emitters allow adding new effects without code changes
- Used for: explosion debris, bullet sparks, dust, ambient effects
- Each particle: position, velocity, life, maxLife, size, color, type

---

### 15. Camera System: Shake + Offset (No Zoom for Now)

**Decision:** Implement a camera with position offset and shake. Zoom is architecture-ready but disabled (scale = 1.0) to avoid gameplay readability issues on small maps.

**Rationale:**
- Screen shake adds impact feedback for explosions and hits
- Position offset enables future camera panning and stage transitions
- Zoom on a 416×416 map would reduce visibility — kept at 1.0 for now
- Camera shake decays exponentially: `shake * pow(0.85, frames)`

---

### 16. Theme System: Extended ThemeColors + CSS Variables

**Decision:** Extend `ThemeColors` with additional fields for UI styling (panel colors, text colors, gradients, shadows). Generate CSS custom properties from the active theme at runtime.

**Rationale:**
- CSS variables allow HTML UI to react to theme changes instantly
- Extended color palette supports modern UI elements (panels, buttons, shadows)
- Themes remain pure data — no rendering code changes needed
- Two initial themes: Classic (enhanced) and Neon

---

### 17. Visual Asset Format: Programmatic Drawing + Metadata Registry

**Decision:** Keep programmatic Canvas 2D drawing but organize it through a metadata-driven sprite registry. Each sprite type has a `VisualDefinition` with animation states, frame counts, and metadata. The `SpriteArtist` reads these definitions to draw.

**Rationale:**
- Maintains the MVP's zero-asset approach (no PNG files to source)
- Metadata registry enables future PNG sprite support — just swap the artist
- Animation definitions are data, not code — easy to add new animations
- `VisualDefinition` format is compatible with the plan's `metadata.json` structure

---

### 18. State Transitions: CSS-Animated Overlays

**Decision:** Game state transitions (menu → playing, stage clear, game over) use CSS-animated HTML overlays with fade/slide effects. No canvas-based transition animations.

**Rationale:**
- CSS transitions are GPU-accelerated and smooth
- HTML overlays can use modern typography and layout
- Keeps canvas focused on game rendering
- Transitions are declarative (CSS classes) not imperative (JS animation)

---

## Determinism & Input Fixes

### 19. Simulation RNG: Migrate Math.random() to world.rng

**Decision:** All entropy inside `Simulation` flows through `world.rng` (`src/utils/RNG.ts`). Specifically, `updateEnemyAI()` now uses `w.rng.next()`, `w.rng.pick(ALL_DIRS)`, and `w.rng.next() * N` for think/fire timers; `spawnPowerUp()` now uses `w.rng.pick(types)` and `w.rng.int(12)` for placement. `Math.random()` is no longer called anywhere in `src/game/`.

**Rationale:**
- AGENTS.md §2.3 promises "Same inputs + same RNG state + same World ⇒ identical replay, always." `Math.random()`'s state is not captured by `WorldSnapshot`/`world.rng.getState()`, so any call inside `Simulation` is a determinism leak that breaks replays and `RecoverySystem` rewinds.
- `RNG` already exposes `next()`, `int(max)`, and `pick<T>(arr)` — no new RNG API was needed.
- Presentation code (`Camera`, `ParticleSystem`, `AudioManager`) still uses `Math.random()`, which AGENTS.md §5 explicitly permits because that entropy never feeds back into the World.

**Implications:**
- A rewound World (via `RecoverySystem`) now reproduces the exact same future it produced the first time, because `world.rng` state is part of the snapshot and `Math.random()` no longer contributes.
- Existing AI distributions are unchanged (same weights, same ranges) — only the source of randomness moved.
- Test coverage: `tests/simulation.test.ts` runs the same seeded simulation twice while perturbing `Math.random()` between runs; before the fix this diverged, after it matches.

---

### 20. Input Movement Priority: Last Pressed Wins

**Decision:** `Input.getMoveDirection()` resolves held movement keys by "last pressed wins" instead of the previous fixed order (up → down → left → right). A press-ordered `moveStack: string[]` is maintained — `onKeyDown` pushes a movement key when first pressed, `onKeyUp` removes it, and `getMoveDirection()` walks the stack from most-recent to oldest, returning the direction of the first still-held entry.

**Rationale:**
- The old code comment explicitly said "Priority: last pressed wins — but for simplicity, check in order", acknowledging the debt. Players who roll from one direction to another (e.g. up → right while still holding up) expect the newer direction to take precedence, matching classic Battle City feel and every modern twin-stick/top-down shooter.
- The fixed-order check made the up direction "sticky": holding up and pressing right kept the tank moving up, which felt broken.
- A stack is the minimal data structure that encodes press order with O(1) push and O(n) (n ≤ 4) remove; given the at-most-four movement keys, this is effectively O(1).
- `moveStack` is keyed off `onKeyDown`/`onKeyUp` (not `endFrame`), so it survives the per-frame `endFrame()` clear that governs edge-detection state (DECISIONS §8) — held keys persist correctly across frames.
- A defensive prune in `getMoveDirection()` handles stale entries if a keyup was missed (e.g. window blur), keeping the stack from growing unbounded.

**Implications:**
- No change to menu navigation, which uses `wasPressed()` edge detection (DECISIONS §8) rather than `getMoveDirection()`.
- No change to custom key bindings — `moveDirFor()` consults `this.keys`, so WASD layouts work identically.
- Test coverage: `tests/input.test.ts` pins down press-order, fallback-on-release, auto-repeat dedup, non-movement-key filtering, custom bindings, and cross-frame persistence.

---

## Performance Tuning (2026-07-22)

**Goal:** Sustain 60fps with no GC jank. Scope: render/GC path only — the simulation was measured at ~3µs/tick (negligible vs the 16.6ms frame budget), so it is NOT the bottleneck.

### 21. Eliminate Per-Frame Allocations (GC Pressure)

**Decision:** Remove every unconditional allocation on the per-frame hot path.

- `Camera.getOffset()` now returns a reused `_offset` object instead of `new {x,y}` every frame.
- `World.consumeEvents()` uses a double-buffered swap (`events` ↔ `eventsSpare`, clear the spare) instead of `this.events = []` — zero array allocation per frame.
- `EffectsSystem.getFlash()` returns a reused `_flashResult` object instead of `new {color,intensity}` on every flash frame.
- `AnimationSystem.cleanup()` uses a frame stamp (`VisualComponent.lastSeenFrame === world.frame`) for mark-and-sweep instead of `PresentationLayer` allocating `new Set<number>()` every frame and doing hash `.has()` lookups.

**Rationale:** GC pauses are the most likely cause of frame drops in a steady-state canvas game; removing guaranteed per-frame allocations keeps the heap stable. Verified by `tools/bench-sim.ts` (≤2 distinct event-buffer identities over 2000 frames).

### 22. Incremental Terrain Cache Rebuild

**Decision:** A single destroyed sub-block no longer triggers a full 26×26 terrain-cache rebuild. `TileMap` now records changed cells in `dirtyCells` (flat `row*GRID+col` indices); the renderer redraws only those cells in place (terrain + forest caches), leaving `tm.dirty` (full rebuild) for stage load, theme change, and base destruction (which needs ruin rendering).

**Rationale:** Previously every brick/steel hit set `tm.dirty`, forcing ~676 tile draws + grid redraw + forest redraw + a full water rescan on the next frame — even when only 1–4 cells changed. During sustained combat this was a per-frame O(676) redundant-render cost. Incremental redraw is O(changed cells) and pixel-identical to a full rebuild (empty cells redraw their grid lines; solid cells redraw tile art; the opposite cache cell is cleared). Both caches stay consistent for any transition.

**Verification:** `tools/bench-sim.ts` asserts `destroy(3,3)` → `dirty===false && dirtyCells.length===1`, and `destroyAllBaseCells()` → `dirty===true` (full rebuild preserved).

### 23. Regression Guard: FPS Sampler

**Decision:** `Game` tracks a rolling FPS (updated once/second, allocation-free) and `console.warn`s only after 3 *consecutive* sub-45fps seconds (avoids one-off GC-blip noise). `bun tools/bench-sim.ts` is the headless perf regression check (sim timing + allocation + terrain invariants).

### 24. Deeper Pass: Render Hot-Path & Water Steady-State

Second optimization pass ("think deeper"). All changes are behavior-preserving or
behavior-improving and allocation-free on the steady-state path. Verified: typecheck clean, oxlint 0 warnings, `bench-sim.ts` ALL PASS.

- **Water is a steady-state hotspot (fixed).** `drawWater` previously called
  `drawSvgCentered('terrain.water', …)`, and because `terrain.water` is registered
  in the sprite library, that path **won** — so every water sub-cell did
  `save()/translate()/drawImage()/restore()` **every frame** (40–80 cells on
  "Waterways"/"Riverbed" stages → 40–80 graphics-state allocations/frame,
  every frame). It also silently *disabled* the intended wave animation (the
  static SVG never animated). Fix: `SpriteCache.rebuildWater(theme)` pre-rasterizes
  two phase-animated water bitmaps (theme-aware, rebuilt on theme change like the
  terrain/vignette caches); `drawWater` blits the active phase. Net: 1 cheap blit
  per cell, no per-call allocation, and the wave animation is restored.

- **`drawSvgCentered` no-rotation fast path (fixed).** The common SVG draw has
  `rotationRad === 0` (water, power-ups, base tiles). It now blits directly with
  `drawImage` and skips `save()/restore()`, eliminating a per-call graphics-state
  allocation for every non-rotated sprite draw.

- **Hoist per-call object literals (fixed).** `drawEnemyTank` allocated a `keyMap`
  object literal and `drawPowerUp` a `itemKey` object literal on **every** draw
  call (once per enemy tank / power-up per frame → N allocations/frame). Both are
  now module-level constants (`TANK_KEY_MAP`, `ITEM_KEY_MAP`).

- **Debris particles: drop `save()/restore()` per particle (fixed).** The debris
  pass did `save()/translate()/rotate()/restore()` per debris particle. `save()`
  allocates a graphics-state object each call — GC pressure exactly when many
  explosions overlap (debris up to ~15 per big boom). Replaced with an explicit
  `setTransform` to the base+translate matrix and a single `setTransform` reset
  after the pass (base transform components cached on `GameRenderer`).

- **`renderPopups` early-return (fixed).** Popups are transient (briefly after a
  kill). The method forced `ctx.font = 'bold 11px …'` (a CSS-font parse) and two
  `textAlign` writes **every frame** even with zero popups. Now returns early when
  `world.popups.length === 0`.

- **`UIManager.update` stage-clear / victory DOM writes (fixed).** Previously wrote
  a template-literal string into a hidden element's `textContent` **every frame**
  during normal play (the elements exist but are `display:none`). Now guarded by
  `world.state` and cached (`lastStageClear` / `lastVictory`) so the work only
  happens on the relevant screen and only on change.

**Sweep result:** The `Game` loop orchestration (`consumeEvents` double-buffer,
number `dt`, `endFrame`) is allocation-free. Event-driven allocations
(`EmitterConfig` literals per explosion/bullet) are bounded and not steady-state;
left as-is. Net steady-state per-frame allocations on the render path: ~0.

### 25. Ultra Pass: Simulation Per-Tick Allocations

Third pass ("ultra think"). Scrutinized every per-frame system that the first two
passes hadn't read line-by-line — Simulation tick, RecoverySystem, AudioManager,
AnimationSystem, EffectsSystem, Camera, Input. Confirmed clean (allocation-free
or event-bound, not steady-state) for: RecoverySystem (snapshots are 1 Hz, not
per-frame), AudioManager (Web Audio nodes only on actual sound events),
AnimationSystem (getOrCreate allocates once per entity; getFrame/cleanup no-alloc),
EffectsSystem (getFlash reuses object), Camera (reused offset), Input (persistent
Sets cleared, not reallocated), Game loop orchestration (double-buffered events,
number dt). The remaining steady-state allocations were all in the **simulation
tick** (previously deemed "not the bottleneck" by timing, but per-tick allocations
still feed GC during active play):

- **`tryFire` player-bullet count (fixed).** Counted active player bullets with
  `w.bullets.filter(b => b.alive && b.ownerId === tank.id).length` — a **new array
  allocation on every player fire attempt** (each shot, ~2–5×/sec). Replaced with an
  allocation-free counting loop that early-breaks at the cap.
- **`spawnPowerUp` drop table (fixed).** Rebuilt the `['star','bomb','shield',
  'freeze','tank','helmet']` array literal **every bonus-enemy kill**. Hoisted to a
  module-level `POWERUP_TYPES` constant (same pattern as the earlier key-map hoists).
- **`updateSpawning` spawn rect (fixed).** Allocated `{ x, y, w, h }` on every
  spawn-retry tick (while the spawn point is blocked). Inlined the AABB call with
  literal values — zero allocation.
- **`World.removeBullet` (fixed, defensive).** Dead code (never called) but carried
  `this.bullets = this.bullets.filter(...)`, a per-removal array allocation. Switched
  to in-place swap-and-pop, matching `removeDeadEntities`.

**Verified:** typecheck clean, oxlint 0 warnings, `bun test` 25/25 pass,
`bun tools/bench-sim.ts` ALL PASS (sim timing informational; correctness invariants
hold). Net: the entire per-frame + per-tick path is now allocation-free on the
steady-state path. Only remaining allocations are gameplay/event-bound (explosions,
power-up spawns, emitted events) and are accepted as inherent.

**Considered-and-left:** `renderParticles` iterates the pool in 5 type-batched
passes (≈2500 cheap iterations/frame, no allocation). Left as-is — the per-type
batching minimizes `fillStyle`/`strokeStyle` state changes; merging into one pass
would interleave types and *increase* state churn. `world.allTanks` rewrites a
reused buffer per call (O(tanks), trivial).

---

### 26. Energy Pass: On-Demand Render Skip + Visibility Pause

Fourth pass, driven by the explicit goal "reduce power draw / don't let the fan
spin". Hitting 60 fps is necessary but not sufficient: if the loop repaints the
full 416×416 canvas **every animation frame unconditionally**, the GPU never idles
and the fan runs continuously — even at a static menu, while paused, on the
game-over screen, or during idle lulls. Energy is won by **doing less work per
second**, not just by speeding up each frame.

**DPR cap already in place** (`PresentationLayer` clamps `devicePixelRatio` to ≤2),
so overdraw was already bounded. The real gaps were (1) unconditional full-canvas
repaint and (2) no visibility pause.

**Changes:**
- **On-demand render skip (`PresentationLayer.shouldRender`)** — returns `false`
  (skip `render()` entirely) when the visible scene is unchanged. The canvas keeps
  its last painted pixels; the GPU goes idle. Input, simulation, and the HTML HUD
  still run every frame, so responsiveness is untouched. Forced-`true` when:
  time-based effects animate every frame (particles, explosions, screen flash,
  hit-pause, camera shake, score popups); structural/UI state changes (game state,
  theme, menu/recovery navigation, terrain-cache dirty). Otherwise a cheap
  **scene signature** (`computeSceneSig`) is compared to the last painted frame —
  water phase + coarse bullet/tank positions + tank state bits (spawn/shield/flash/
  bonus/moving) + player level + power-up position/type + camera offset, with the
  native spawn/shield/flash animation phase folded in so those animations still
  play at their designed rate (repaint only on phase flips, skip in-between
  frames). Coarse 8px buckets mean sub-pixel jitter never false-triggers a repaint.
- **`reset()` forces a repaint** (`_needRender = true`) so returning to menu /
  recovery-resume always paints a fresh frame. Same flag set on visibility resume.
- **Visibility pause (`Game.onVisibility`)** — on `document.hidden`, cancel the rAF
  and stop the loop completely (the single biggest energy saver for a backgrounded
  tab); on return, reset `lastTime`, mark-needs-render, and resume. Guarded by
  `running` so `stop()` still wins. Listener added in `start()`, removed in `stop()`.
- **`MAX_RENDER_FPS` knob (`constants.ts`, default 0 = uncapped)** — decouples the
  canvas repaint rate from the 60 Hz simulation. Set to e.g. `30` for a battery /
  low-power mode that halves GPU work during *active* play. Default preserves the
  60 fps feel; on-demand skip already eliminates idle repaints regardless of value.
- **HUD split out** — `render()` no longer calls `ui.update`; a separate always-on
  `updateUI()` keeps menu/pause overlays live even when the canvas repaint is gated.

**Behavior:** during menu / pause / game-over / victory / idle lulls the canvas is
repainted 0× (fans off); during active play with motion it repaints every frame as
before (smooth, 60 fps). The simulation always ticks at 60 Hz for determinism.

**Verified:** `tsc --noEmit` clean, oxlint 0 warnings, `bun test` 25/25,
`bun tools/bench-sim.ts` ALL PASS (sim 1.5–2.2 µs/tick, well under the 16.6 ms
frame budget). `vite build` itself is blocked in this sandbox only by the
bulk-delete of `dist/assets` (unrelated to code correctness).

**Considered-and-left:** a finer-grained dirty-rectangle canvas compositor was
evaluated but rejected — it would violate the "keep architecture simple" gate for
marginal extra gain over the whole-frame skip, and the terrain/forest/water/vignette
caches already make each full repaint cheap.

---

### 16. Combat Capability System (plan/Combat-Capability-System.md)

**Decision:** All tanks (player + enemies) are described by one shared six-dimension
`CombatProfile` (firepower / projectileSpeed / fireControl / mobility / armor /
special, each 0–100, 50 = baseline). Concrete stats (speed, HP, bullet speed,
bullet power, fire cooldown) are **derived** from the profile by
`profileToStats()` in `src/config/combat.ts`. Tank *types* are just budgeted
profile distributions, not code branches.

**Rationale / specifics:**
- **Budgets:** every normal enemy archetype sums to `BASELINE_BUDGET = 300`
  (balanced/fast/power/heavy in `TANK_PROFILES`); difficulty = variety, not
  inflation. Commanders (spawn-rolled `commander` tier) break the budget via a
  +15% boost to their kind-specific dimension (`ELITE_DIMENSION` / `ELITE_BONUS`),
  applied once at spawn in `Simulation.updateSpawning` (a fresh object, never
  mutating the shared base profile — safe for shallow-clone recovery). Every
  `commander`-tier tank is boosted (including inactive commanders); a
  cap-downgraded commander becomes a `veteran` and is NOT boosted. Since the
  boost is applied exactly once to a fresh profile, there is no compounding
  ("commander = +15%, never more"). See DECISIONS §29 (D10 carve-out).
- **Player progression:** universal growth — every star raises ALL six dimensions
  together (level 0→50, 1→60, 2→70, 3→80). Ceiling is `PLAYER_PROGRESSION`
  (`maximumLevel`, `maxMultiplier`) so hardcore/challenge modes can out-scale
  commanders without touching engine code.
- **Star damage regression (plan §12 → user override, 2026-07-23):** the plan
  specified "lose one star level" on death, BUT the user reported the star buff
  persisting after respawn as a bug. Changed to a FULL reset: on death the player
  reverts to `difficulty.playerStartLevel` (all earned stars discarded), classic
  Battle City behaviour (death resets the tank to its baseline form). `checkConditions`
  now sets `w.playerLevel = w.difficulty.playerStartLevel` (was `Math.max(0, -1)`),
  and `spawnPlayer` rebuilds the tank from that reset level. Relax (startLevel 1)
  keeps its 1 baseline star; classic/hard/chaos reset to 0.
- **AI integration (plan §14):** `capabilityBias(profile)` adds flank/push/attack
  weight to goal scoring, and `fireControl` scales firing aggression, so every tank
  "plays to its strengths" with no bespoke scripts.
- **Removed hardcoded stats:** `hp/speed/bulletSpeed/bulletPower` dropped from
  `TankConfig`; `Tank` now carries `profile` + `bulletPower`/`bulletSpeed` derived
  at spawn and on star upgrade. `createTank` and `tryFire`/`applyPowerUp` read these.

**Verified:** `tsc --noEmit` clean, oxlint 0 warnings, `vite build` OK,
`bun test` 58/58 (incl. new `tests/combat.test.ts` and recalibrated
bullet-avoidance test). Deterministic — no new RNG calls; elite promotion changes
only HP (not position/speed), so the determinism test is preserved.

**Refinement (2026-07-22) — speed-ratio invariant & identity-preserving hits:**
- `profileToStats` now anchors tank speed and bullet speed to **disjoint px/tick
  bands**: tank `1.5–3.5` (mobility 30→100), bullet `6.0–10.0` (projectileSpeed
  40→100). So no tank — including the fast enemy (mobility 80 → speed 2.93) — can
  outrun its own bullet (projectileSpeed 45 → 6.25); bullets are always ~1.7–4×
  faster. The player's per-star speed buff is proportional (mobility +10 → speed
  +~0.29, bullet +~0.5). Removed the dead `BULLET_SPEED` / `PLAYER_SPEED` /
  `FIRE_COOLDOWN` constants (replaced by profile-derived stats).
- Hit overlays (`fx.hit1–4`, generated by `tools/gen-sprites.mjs`) no longer fill
  the hull with a charcoal/gray rect (which recoloured every tank into a generic
  blob — the "armor looks like a basic enemy when hit" bug). They are now
  transparent crack + scorch + ember/smoke **decals**; the type-specific sprite
  always shows through, so each enemy keeps its type & appearance when hit.
- `bulletHitsTank` hardens the rule: a non-lethal hit never swaps `kind`,
  mutates `profile`, or alters any derived stat (speed/bulletSpeed/fireCooldown/
  bulletPower) — only `hp` and cosmetic `hitCount` change (issue #2: no power
  loss on hit; damage is decorative only, as requested).

**Refinement (2026-07-22, 2nd) — global speed −40% & steel-pierce gate:**
- User tuning: ALL tank speeds cut 40% and bullet speeds cut by the SAME
  proportion, so the relative race (bullet ≫ tank) still holds at the slower
  scale. Driven by two module-level constants in `src/config/combat.ts`:
  `SPEED_SCALE = 0.6` and `BULLET_SPEED_SCALE = 0.6`. New px/tick bands:
  tank `0.9–2.1` (mobility 30→100), bullet `3.6–6.0` (projectileSpeed 40→100).
  Fast enemy is now ~2.1× outrun by its own bullet (was already impossible to
  overtake). All relative orderings preserved.
- **Steel-pierce gate (user request):** default `power` tank must NOT destroy
  steel; only ELITE `power` may. Implemented via `STEEL_PIERCE_FIREPOWER = 80`
  in `profileToStats` (bulletPower 2 iff firepower ≥ 80). Default power
  firepower = 75 → bulletPower 1 (cannot pierce). Elite power's +15% firepower
  boost lifts 75 → 86 → bulletPower 2 (can pierce). Note: max-level player
  (firepower 80) also reaches the gate — this matches classic Battle City, where
  the 3-star player breaks steel; flagged to user, easy to exclude if unwanted.
- Verified: `tsc` clean, `oxlint` 0 warnings, `vite build` OK, `bun test` 60/60
  (combat.test grew by 2 cases asserting the gate + the −40% scale; the
  bullet-avoidance test in tactical-ai.test still passes against the slower
  tanks, so dodge-vs-intelligence holds).

**Refinement (2026-07-23) — fire-rate fairness invariant (duel-safe player):**
- User requirement: with no buffs on either side, the player must never LOSE a
  head-on duel (对枪) against any enemy type because of a lower fire rate. In a
  duel opposing bullets cancel 1:1 (`bulletHitsBullet`), so the shorter
  fireCooldown side always lands the surplus shell — the invariant reduces to:
  unbuffed player (level 0, fireControl 50 → 420 ms) cooldown ≤ every enemy
  archetype's cooldown.
- `power` violated it (fireControl 55 → 400 ms, strictly out-firing the
  player). Rebalanced: fireControl 55 → 50 (420 ms, tie), the 5 freed points
  moved to `special` (no stat mapping) so the 300 budget is preserved. Side
  effects: none on speed/HP/bulletSpeed/bulletPower; AI `effectiveAggression`
  shifts 0.865→0.85 (negligible). Elite promotions never boost fireControl, so
  elites keep the invariant too.
- Guarded by `tests/fire-rate-duel.test.ts` (6 cases): a config contract
  (player cd ≤ every base AND elite archetype cd) plus a real-Simulation
  head-on duel vs each kind — corridor cleared, both sides firing at their
  exact max cadence via the real `tryFire` gate — asserting the player is
  never destroyed, fires ≥ the enemy, and strictly-slower enemies actually
  die to the surplus shells. Verified the tests FAIL with the old power
  profile. `tsc --noEmit` clean, `bun test` 75/75.

**Feature (2026-07-26) — bullet-speed redesign: per-kind table anchored to
movement × 4, with per-bullet jitter (user spec):**
- Bullet speed is now a **per-kind data table** in `src/config/speed.ts`
  (`BASE_BULLET_SPEED_CPS`), mirroring the movement-speed table `BASE_SPEED_CPS`:
  - 均衡(basic) 弹速 = 均衡敌人移动速度 × 4  → 2.5 cps × 4 = 10.0 cps (2.6667 px/tick)
  - 快速(fast)   = × 1.05 → 10.5 cps (2.8 px/tick)
  - 强力(power)  = × 0.95 →  9.5 cps (2.5333 px/tick)
  - 重甲(armor)  = × 0.90 →  9.0 cps (2.4 px/tick)
  - 无星星玩家    = × 1.05 → 10.5 cps (2.8 px/tick); player scales +0.5 cps/star
    (universal growth, capped at 12.0 cps / 3.2 px/tick at 3★).
  `BULLET_SPEED_RATIO = 4` and `BULLET_SPEED_MULT` encode the spec ratios;
  `baseBulletSpeedPxPerTick(kind, level)` is the canonical lookup.
- **Why a table, not a CombatProfile dimension:** basic and the no-star player
  share `projectileSpeed` 50 yet must fire at different bullet speeds (×1.00 vs
  ×1.05), so bullet speed cannot be a pure function of one capability axis —
  exactly the same reason movement speed is per-kind data. The `projectileSpeed`
  dimension is retained on `CombatProfile` for AI/extensibility symmetry but no
  longer drives bullet speed. `profileToStats` now returns the per-kind table
  value (synthetic no-kind profiles fall back to the balanced enemy); the old
  `BULLET_SPEED_SCALE` constant is removed.
- **Per-bullet jitter: actual = base × random(0.95, 1.05).** Implemented as
  `bulletSpeedJitter(seq, frame)` — a deterministic hash of the World's
  monotonic `bulletSeq` counter + `frame` — applied in `Simulation.tryFire`
  via `spawnBulletSpeedPxPerTick`. It deliberately does NOT draw from
  `world.rng`: bullets fire far more often than tanks spawn, so an rng draw per
  shot would interleave thousands of draws into the AI's decision stream and
  silently alter enemy behaviour. `bulletSeq` lives on the World, resets per
  World, and is snapshotted by `WorldSerializer`, so jitter is reproducible
  across runs and recovery (AGENTS §2.3). It also does NOT use the module-level
  `genId()` counter, which is not reset between Worlds and would break
  cross-run determinism.
- **Race invariant preserved:** every bullet outruns every tank. Slowest bullet
  (armor, 2.4 px/tick) = exactly 3× the fastest tank (fast, 0.8 px/tick).
- **Tests:** new `tests/bullet-speed.test.ts` (anchor, per-kind multipliers,
  concrete px/tick values, no-star player == fast, player star scaling, jitter
  range/determinism/AI-independence, spawn composition, race invariant,
  `profileToStats` integration). `tests/combat.test.ts` updated (basic bullet =
  4× move; power bullet = 0.95× basic bullet, no longer "= lvl-2 player"). Full
  suite green: `bun test` 185/185, `tsc --noEmit` clean.

**Feature (2026-07-26) — fire-rate standard (user spec, replaces the
2026-07-23 fire-rate fairness invariant):**
- Fire cadence is now a SINGLE data-driven standard in `src/config/fire-rate.ts`,
  NOT the old `620 − fireControl×4` formula (and NOT the 2026-07-23 "player never
  out-fired" fairness invariant, which this spec deliberately reverses).
- **Core constraint:** a balanced (basic) enemy firing straight down from the
  top must have AT MOST 3 bullets on the vertical route — the 3rd is fired
  exactly when the 1st reaches the bottom. That pins the balanced interval to
  half the bullet's full-field travel time: `FIELD / basicBulletSpeed` → 156
  ticks → 2600 ms travel → **`BALANCED_FIRE_INTERVAL_MS = 1300`** exactly.
- **Firing-frequency multipliers (higher = fires more often = shorter interval):**
  `basic 1.00×, fast 1.05×, power 1.10×, armor 0.90×, player 1.05×` (no-star).
  Interval = `BALANCED_FIRE_INTERVAL_MS / multiplier` (power 1181.8ms, player0
  1238.1ms, basic 1300ms, armor 1444.4ms). Player gains +0.05×/star
  (`PLAYER_FIRE_FREQUENCY_PER_STAR`), so a max-level (3★) player reaches 1.20×
  and out-rates even power.
- **Per-fire random variation:** actual next interval = base × random(0.95,
  1.05). Implemented as `fireIntervalJitter(seed, frame)` — a deterministic hash
  of the tank's **per-World `fireCount`** + `frame` — frozen into `tank.nextFireInterval`
  at each shot in `Simulation.tryFire`. Like the bullet-speed jitter it does NOT
  draw from `world.rng` (keeps the AI decision stream pure) and does NOT use the
  global `genId` counter (which is NOT reset between Worlds → would break
  cross-run determinism, AGENTS §2.3). `fireCount` resets per World and is
  shallow-cloned by `WorldSerializer`, so it survives snapshots/Replay.
- **Wiring:** `profileToStats` now returns `fireCooldown = round(baseFireIntervalMs(kind, level))`;
  the gate in `tryFire` is `now − lastFire < nextFireInterval`; the AI's
  `brain.fireTimer` re-decision delay tracks `nextFireInterval`. Elite promotion
  leaves cadence unchanged (resets `nextFireInterval` to the base).
- **Ordering consequence (explicit new design):** the no-star player out-rates
  basic/fast(=tie)/armor but is OUT-RATED by power (1.10× > 1.05×) — the opposite
  of the 2026-07-23 invariant, which the user's new spec overrides.
- **Tests:** new `tests/fire-rate.test.ts` (derived baseline, 3-bullet math,
  per-kind multipliers/ordering, jitter band/determinism/variety, ±5% mean);
  `tests/fire-rate-duel.test.ts` rewritten to assert the NEW ordering (player
  not out-fired by basic/fast/armor; power out-rates no-star player; max-level
  player beats power); `tests/combat.test.ts` updated (fireCooldown now = the
  standard's base, not fireControl; power out-rates player); `tests/simulation.test.ts`
  fire-cadence tolerance widened to the ±5% band. Full suite green: `bun test`
  204/204, `tsc --noEmit` + `oxlint` + `vite build` clean.

**Feature (2026-07-23) — Snapshot Management Framework (replaces RecoverySystem):**
- The monolithic `RecoverySystem` (auto-numbered snapshot files + inline
  recovery UI) is **retired** (file deleted; only historical doc-comment
  references remain). It is replaced by a policy-declared, data-driven
  framework in `src/snapshot/` per `plan/Snapshot-Management-Framework.md`.
- **One model, four origins.** Every snapshot is the SAME shape
  (`WorldSnapshot` in `src/snapshot/types.ts`): a UUID id + `parentId`
  timeline link + 13-field first-class metadata (origin/createdAt/tick/
  stageIndex/score/lives/playerLevel/enemyCount/phase/rngState/thumbnail/
  version/notes). Origins are `stage-start`, `pause`, `auto`, `manual` — they
  differ ONLY by a declarative `RetentionPolicy`, never by branching code.
- **Retention is configuration, not `if` statements.** `SnapshotManager`
  (`src/snapshot/SnapshotManager.ts`) enforces per-origin policy from
  `src/snapshot/config.ts`: `circular` (ring of N, overwrite oldest) for
  stage-start/pause/auto (cap 20), `never` (hard cap 100, refuse overwrite) for
  manual. `create(type, world)` returns `null` when a `never` bucket is full —
  the UI then shows a toast rather than silently dropping the player's save.
- **Determinism preserved on restore.** `WorldSerializer`
  (`src/snapshot/WorldSerializer.ts`) deep-clones the World for a snapshot and
  atomically overwrites it on restore (the AGENTS §2.1 exception —
  AGENTS §2.1 — it restores state, it does not run gameplay rules). Thumbnails
  (256×256, square) are captured by `Game` AFTER the canvas repaint (pending queue) to
  avoid grabbing a stale frame.
- **Persistence.** `storage.ts` defines a backend contract; `createDefaultStorage()`
  returns an IndexedDB-backed implementation or `null` when IndexedDB is
  unavailable (in-memory fallback, no crash). `Game.start()` awaits
  `snapshots.hydrate()` before `spriteLibrary.load()`.
- **Recovery Controller (plan §11).** `RecoveryController`
  (`src/snapshot/RecoveryController.ts`) drives the 5-option failure-recovery
  flow: `continue` / `loadLatest` / `replayStage` / `restartStage` /
  `chooseSnapshot`. `loadLatest` has a 15s auto-trigger that falls back to
  `restartStage` if no snapshot exists. Option availability is computed per
  state and surfaced to the UI (disabled options are soft-denied, never crash).
- **UI (UIManager + CSS).** `SnapshotBrowser` (plan §12: list + 320×180
  thumbnails w/ hover zoom + load/delete) and `ControlCenter` (plan §13:
  left sidebar, manual-save + open-browser + collapsible, hidden <900px) live
  in `src/presentation/ui/`. Shortcut `Alt+S` = manual snapshot (rebindable in
  Controls); opening the browser auto-pauses. Toasts (`notify()`) report save
  success / capacity.
- **Determinism fix found via this work.** `Simulation.spawnPointIndex` was a
  hidden instance field (state OUTSIDE the World — AGENTS §2.2/§2.3 violation):
  two identical World snapshots replayed on different Simulation instances
  diverged because spawn counts live on the Sim. Moved to `World.spawnPointIndex`
  (init in constructor + `loadStage`), used as `w.spawnPointIndex` in
  `Simulation`, and captured/restored by `WorldSerializer`. This also fixed a
  flaky `tests/snapshot-framework.test.ts` case ("a restored world reproduces
  the exact same future (determinism §2.3)") that passed in isolation but
  failed in the full suite.
- **Verification:** `bun test` 104/104 (29 in `tests/snapshot-framework.test.ts`
  covering one-model-four-origins, 13-field metadata, circular overwrite,
  never-overwrite-full→null, per-type isolation, auto cadence, restore
  round-trip + determinism reproduction + delete, 15s fallback rule, queries,
  RecoveryController 5-option/availability/flow, storage backend contract).
  `tsc --noEmit` clean, `oxlint` 0 warnings, `vite build` OK.

---

### 27. Power Tank Firepower Rebalance (firepower 80 → 64, 2026-07-26)

**Decision:** Lower the power enemy's `firepower` capability from 80 to 64 (per-shot
damage 160 → 128) and raise its `armor` from 36 to 40 (HP 180 → 200), redistributing
the budget to `special` (34 → 46) to keep the 300-budget invariant. The hits-to-kill
matrix changes in exactly one cell: power→fast goes from 1 (one-shot) to 2.

**Rationale:**
- Power's specialty shifted to **firing frequency** (1.10×, the highest among all
  tanks — see `config/fire-rate.ts`). Having both the highest fire rate AND a
  one-shot ability (damage 160 > fast HP 150) made power overwhelmingly dominant:
  it killed every archetype faster than they could kill it, and elite power
  (firepower 80×1.15 = 92, damage 184) one-shot multiple archetypes.
- At firepower 64 (damage 128), power is **still the strongest enemy gun**
  (128 > basic 100 > armor 86 > fast 72) and still kills basic/power in 2 hits
  and armor in 3 — but no longer one-shots any enemy. Elite power (firepower 74,
  damage 148) also cannot one-shot the frailest fast (HP 150): ceil(150/148) = 2.
- Raising armor 36 → 40 (HP 180 → 200) makes "略低 HP" (slightly low HP) more
  accurate relative to basic (250): 200/250 = 80% vs the old 180/250 = 72%. No
  other matrix cell changes because all ceil divisions round the same way.
- Steel-pierce was already decoupled from firepower (player-only, level-gated via
  `STEEL_PIERCE_PLAYER_LEVEL`); this change is purely about DPS balance, not steel.

**Implications:**
- The adjusted hits-to-kill matrix (only power→fast changed: 1 → 2):

  | source\target | 均衡 | 快速 | 强力 | 重甲 |
  |---|---|---|---|---|
  | 均衡 | 3 | 2 | 2 | 4 |
  | 快速 | 4 | 3 | 3 | 5 |
  | 强力 | 2 | **2** | 2 | 3 |
  | 重甲 | 3 | 2 | 3 | 5 |

- No enemy archetype can one-shot another (minimum 2 hits in every cell). This is
  a deliberate design principle: one-shots belong to the max-level player
  (steel-pierce + high damage), not to any enemy.
- The no-star player (damage 105, HP 263) now beats power in a straight duel:
  player kills power in ceil(200/105) = 2 hits, power kills player in
  ceil(263/128) = 3 hits. Previously it was a tie (both 2) that power won via
  faster fire rate — the new balance is more player-friendly.

---

## 28. Tank HP Level Visual Aura Decoration (2026-07-26)

**Decision:** Render distinct, dynamic visual light-ring (aura) shapes under/around tanks according to their current remaining HP (Level 1~6, where Level 1 = no extra aura, and Level 2~6 feature emerald green circle, sky blue double ring, amethyst purple diamond, flame orange hexagon, crimson red solar radiation respectively).

**Rationale:**
- **Hits-to-Kill Standard**: Map remaining HP against standard base damage (100 HP = 1 hit). `getHpLevel(hp)` computes `ceil(hp / 100)`, clamped to [1, 6]. Level 6 reserves space for promoted elite heavy tanks (600 HP / 6 hits).
- **Dynamic Degradation**: As tanks take damage in real time, the HP level drops (e.g. Level 3 -> Level 2), providing intuitive visual feedback on how many hits remain to kill the target.
- **Architectural Purity**: `World` and `Simulation` remain unpolluted by visual state (AGENTS §2.1/§2.5). The calculation is a pure function in `src/config/hp-level.ts`, and rendering is handled entirely in `SpriteArtist.ts` / `GameRenderer.ts`.
- **Symmetry**: Players and enemy tanks share identical HP aura rules.

**Implications:**
- Players can immediately identify high-threat or high-HP targets at a glance without cluttered health bars.
- 0 runtime/memory footprint overhead: decorative shapes are drawn via 2D Canvas primitives with frame-based pulse animations.
- Test coverage: `tests/hp-level.test.ts` validates mapping boundaries (0~600+ HP) and color/shape configurations.

---

## 29. Spawn-Rolled 5-Tier Enemy AI (2026-07-26, revises former "born-commander" model)

**Decision:** The enemy intelligence tier is **rolled at spawn time** from a
per-difficulty distribution table (`DIFFICULTY_TIER_DISTRIBUTION` in
`src/ai/config.ts`). Tank *kind* no longer implies a tier — `KIND_TO_LEVEL`,
`DIFFICULTY_AI`, `resolveConfig`, `levelForKind`, and the per-difficulty
`eliteChance` knob are all **retired**. The five tiers are
`none / rookie / soldier / veteran / commander`.

**Difficulty no longer scales capability numbers.** The *only* difficulty→AI
lever is the tier distribution. `relax` skews low (mostly none/rookie), `hard`
shifts mass toward veteran, `chaos` adds commander weight; `classic` may be
100% `none` (consuming **zero RNG** — the roll is skipped when the
distribution is a single tier).

**The `none` tier is a separate minimal classic branch**, not a zeroed pipeline:
random wander biased down + random fire, implemented in
`TacticalIntelligence.updateNoneTank` with its own timing constants
(`NONE_TURN_MIN_MS`, `NONE_TURN_JITTER_MS`, `NONE_FIRE_JITTER_MS`). It is
deaf to directives (`compliance` is 0) and carries no rank insignia.

**Command authority (succession, no election):** the active commander is the
**alive** commander with the highest `spawnSeq` (a per-World monotonic counter
in `World`, `spawnSeqCounter`, assigned at `createTank` — `genId()` is *not*
used for this so sequencing is independent of entity reuse). On death, the
previous-born commander automatically regains command. A 1s office delay
(`commanderTimer = 1000`) is measured **from the moment a tank becomes active**
and **overwritten** (not added) on each succession. `activeCommanderId` is
recomputed every tick by `Simulation.recomputeActiveCommander()` (the AI layer is
read-only; it never writes World state).

**Boost rides the tier, not the command role (D10 carve-out, provisional):** the
+15% combat-profile boost (`applyEliteModifier` on the kind's `ELITE_DIMENSION`)
is applied to **every** spawned `commander`-tier tank — including inactive
commanders. Only the **newest-born** commander issues directives; the older
commander(s) obey like any other tier. If the live commander cap (`COMMANDER_ALIVE_CAP = 2`)
is already saturated when a commander tier is rolled, it is **downgraded to
`veteran` and receives no boost**. `veteran` itself lost `strategicThinking`
and `teamwork` (it is now a pure combat tier, D1).

**Floor / cap guarantees:** `COMMANDER_FLOOR` (`relax ≥ 1`, `hard ≥ 2`,
`chaos ≥ 4`) is expressed as a **remaining-attempts quota**
(`commanderQuotaRemaining`), initialized from the floor at `loadStage`. The quota
**decrements on every commander roll** (forced floor *and* natural roll,
including cap-downgraded ones) and **clamps at 0** — so it never goes negative
and the floor is always satisfiable within the stage's 20 spawns. When
`remainingSpawns <= commanderQuotaRemaining` the floor forces the next spawn to
be a commander with **no RNG**. The live cap is enforced at spawn: if 2
commanders are already alive, the rolled commander becomes a veteran.

**Compliance (directive obedience):** the old `teamwork: boolean` is replaced by
a per-tier `compliance` probability. On each directive broadcast,
`broadcastDirective` **bumps `world.directiveSeqCounter`** and **rolls
`directiveCompliant` once per receiver**, cached until the next broadcast
(succession-safe — keyed on the World seq, not per-tank age). `none` is always
deaf. Higher tiers have higher `compliance`, so they obey more reliably.

**Visuals:** rank insignia (gold chevrons: 1/2/3 for
rookie/soldier/veteran — `fx.insignia.*.svg`, pre-rasterized in `SpriteCache`)
render on enemy tanks when `level !== 'none' && !isCommander`; the golden crown
renders only when `isCommander` (crown XOR insignia). Snapshot metadata
`commanderPresent` is now `world.activeCommanderId !== null` (was the per-tank
`isCommander` scan).

**Testing:** `tests/elite-spawn.test.ts` was **deleted** (it assumed the retired
`eliteChance` model). `tests/tactical-ai.test.ts` now guards: tier-roll
distribution sums + determinism + classic-zero-RNG; headless determinism; no-stall
classic wander; strategic-goal stability; command authority (classic none,
highest-spawnSeq, succession + office-delay overwrite); floor (attempt-based
quota) + cap (≤2 alive, downgrade-to-veteran no boost); boost carve-out
(every commander +15%, two commanders both boosted); bullet avoidance; compliance
(none deaf, cached-per-directive, higher tiers comply more); none-branch
deterministic/no-freeze/fires; snapshot round-trip of the 4 new World fields;
`commanderPresent` metadata. **240/240 tests pass; `tsc --noEmit` clean.**

**Snapshot/Recovery safety:** the 4 new World-level fields
(`spawnSeqCounter`, `activeCommanderId`, `commanderQuotaRemaining`,
`directiveSeqCounter`) are cloned/restored by `WorldSerializer`; per-tank new
fields (`spawnSeq`, `directiveSeq`, `directiveCompliant`) ride the shallow-copied
`aiState`. All wiring is shallow-clone safe.



---

## Centralized Score Calculation (2026-07-27)

All scoring is derived from `src/config/score.ts` (no per-kind magic numbers in
the Simulation — AGENTS §2.4 data over code). Replaces the old per-kind
`TANK_CONFIGS[kind].score` (100/200/300/400) which was deleted.

**Formulas (user spec):**
- **Kill:** `round(100 * DIFFICULTY_SCORE_FACTOR[d] * 1.05^(stageIndex+1) * AI_SCORE_FACTOR[aiLevel])`.
  - Difficulty: classic 1.0, relax 1.0, hard 1.2, chaos 1.5.
  - AI tier: none/rookie 1.0, soldier 1.2, veteran 1.5, commander 2.0 (tier read
    from `tank.aiState.level` at kill time; applies to bullet kills AND the
    bomb power-up).
  - Stage index is 0-based in code; the spec's "第 N 关" is 1-based, so the
    level number is `stageIndex + 1` (single constant `STAGE_INDEX_OFFSET`).
- **Stage clear:** `round(1000 * 1.05^(stageIndex+1))`, awarded once when the
  stage transitions to `stageclear` (both immediate-clear and bonus-window
  timeout branches in `Simulation.checkConditions`).
- **Item:** `+100` (`ITEM_SCORE`) per power-up collected, added in
  `Simulation.updatePowerUps` at pickup (covers every type, including bomb).

**Tests:** `tests/score.test.ts` locks every coefficient and key sample values
(classic rookie stage1 = 105; hard commander stage1 = 252; chaos veteran
stage20 ≈ 597; stage-clear stage1 = 1050, stage20 ≈ 2653). 266/266 suite green.

---

## 21. InputLike Interface Extraction (2026-07-27)

**Decision:** Extract a minimal `InputLike` interface from the concrete `Input` class (`src/game/Input.ts`). `Simulation` now depends on `InputLike` instead of `Input`, enabling headless tools (GodAIInput, level-sim) to inject programmatic input sources without a DOM.

**Rationale:**
- The Automated Level Design plan (§3.4) requires a `GodAIInput` that implements the same contract as `Input`. Without an interface, `Simulation.input` was typed as the concrete `Input` class, making injection impossible without inheritance hacks.
- `Simulation` only calls `getMoveDirection()` and `isFiring()`; `endFrame()` and `reset()` are included so `Game.ts` can still call them on the same reference it hands to `Simulation`.
- Pure type change — no behavioral impact, no violation of One Author (§2.1). `Game.ts` still holds its own `input: Input` reference.

**Implications:** Any class implementing `InputLike` can drive the Simulation. This is the foundation for the God AI player simulator and headless simulation tooling.

---

## 22. Generic Pathfinding Module — `src/utils/pathfind.ts` (2026-07-27)

**Decision:** Implement A* pathfinding, BFS reachability, and flood-fill as pure functions in `src/utils/pathfind.ts`. All three account for the 2×2-block tank footprint (checking all four sub-blocks at each candidate position).

**Rationale:**
- The codebase had zero pathfinding (verified: `src/` full search for `aStar|pathfind|BFS|findPath` returned no matches). Both the God AI (navigation) and the level generator (connectivity validation) need it.
- Placed in `utils/` per AGENTS §5 — pure functions shared by the game layer and tools layer.
- `TileMap.blocksTank()` is reused for terrain passability; `ignoreWater` constraint supports the boat power-up scenario.
- A* uses Manhattan-distance heuristic on a 4-connected grid; the 26×26 grid (676 nodes) is small enough for a flat-array open set without a binary heap, keeping the code simple (MANIFEST §10).

**Implications:** `findPath()` returns `Direction[]` (one CELL step per direction), `isReachable()` returns boolean, `floodFill()` returns `Set<string>` of reachable cells. All are deterministic (no RNG) and don't mutate the World/TileMap.

---

## 23. `World.loadStageData(stage)` — Custom Stage Loading API (2026-07-27)

**Decision:** Add `loadStageData(stage: StageData, index = 0)` to `World`. It performs the exact same setup as `loadStage(index)` — terrain load, entity reset, spawn-queue build, player spawn, state transition — but accepts an arbitrary `StageData` instead of looking up `STAGES[index]`. `loadStage(index)` now delegates to `loadStageData`.

**Rationale:**
- The headless simulation runner needs to load generated/custom stages that aren't in the `STAGES` config array. Without this API, the only way to load a stage was by index into `STAGES`.
- The `index` parameter (default 0) is used only for scoring formulas (`killScore` / `stageClearScore`); generated stages use index 0 since the simulation runner cares about pass/fail, not score magnitude.
- Reuses all existing spawn-queue construction and state reset logic — no duplication.

**Implications:** Headless tools can now call `world.loadStageData(generatedStage)` to simulate any stage. The runtime consumption path (§3.6) can also use this to inject generated stages into the live game.

---

## 24. Level Generator — Layered Procedural Generation (2026-07-27)

**Decision:** Implement `tools/level-gen.ts` as a pure-function procedural level generator using a 7-layer pipeline:

1. **Layer 0**: 26×26 empty grid (all `.`)
2. **Layer 1**: Base placement (`E` at 12-13, 24-25)
3. **Layer 2**: Classic U-shape brick/steel defense around base (8 cells)
4. **Layer 3** (implicit): Spawn areas + defense cells added to a `RESERVED_CELLS` set — all cluster placement skips these
5. **Layer 4**: Tactical cover clusters (brick/steel, size 3-8, frontier-based organic growth)
6. **Layer 5**: Environmental terrain clusters (water/forest/ice, size 4-12)
7. **Layer 6**: Single-cell noise (~3% of remaining empty cells)
8. **Layer 7**: Enemy formation (20 tanks, fixed count distribution per difficulty, Fisher-Yates shuffled)
9. **Force overrides**: Clear all spawn 2×2 areas, ensure base is `E`
10. **Validation + carving**: `validateStage()` checks base/spawns/connectivity; if fails, carve 2-cell-wide L-shaped corridors (spawn→row 12 + player→row 12) and re-validate
11. **Retry**: Up to 10 attempts with sub-seeds (`seed * 1000 + attempt`)

Four themes (forest/ice/fortress/mixed) control terrain type fractions. Forest ≥30% forest, ice ≥25% ice, fortress ≥20% steel.

**Rationale:**
- **Frontier-based cluster growth** (`growCluster`) creates organic, connected terrain shapes — no isolated single cells (except noise layer). Average cluster size ≥ 4.
- **Reserved cells** prevent clusters from blocking spawn areas or overwriting base defense. No post-hoc cleanup needed.
- **2-cell-wide corridor carving** ensures 2×2 tank passability. The player corridor (cols 8-9, rows 12-24) connects the player at row 24 to the horizontal corridor network at row 12 that links all three spawn points.
- **Sub-seed per retry** maintains determinism (same input → same output) while allowing each attempt to explore different terrain.
- **Fixed enemy count distribution** (not probabilistic) ensures exact difficulty scaling: relax 14/4/1/1, classic 10/5/3/2, hard 7/5/4/4, chaos 4/6/5/5.

**Rejected alternatives:**
- Cellular automata (CA) for terrain generation: too unpredictable, hard to guarantee connectivity. Cluster-based growth gives more control.
- Pre-designated road network: too rigid, creates unnatural straight corridors. Post-generation carving is more flexible.
- BFS path carving (single-cell-wide): doesn't work for 2×2 tanks. Fixed with 2-cell-wide L-shaped corridors.

**Implications:** `generateStage({ seed, difficulty, theme })` produces a valid `StageData` in < 50ms. 100% of generated stages pass `validateStage()`. Generated stages can be consumed by `SimulationRunner` via `world.loadStageData()`.

## 25. God AI Fixes — endFrame, Global Vision, Move-and-Shoot

**Decision:** Four fixes to the God AI based on the auto-level-review-1 audit:

1. **`input.endFrame()` in simulation runner** (critical bug): The headless `simulation-runner.ts` never called `input.endFrame()` after `sim.tick()`. `Game.ts` calls it in the live game loop, but the headless path didn't. This meant `GodAIInput._thought` stayed `true` after the first tick, so `think()` only ran once — the AI made one decision and cached it forever. Fix: added `input.endFrame()` after each `sim.tick()` in the runner loop.

2. **Global vision for `findEnemyDirection`**: Replaced the line-of-sight scan (which stopped at walls) with a global check that reads `world.tanks` directly and finds enemies in the same row/column regardless of terrain. This matches the plan §3.1 spec ("reads world.tanks directly — no perception limits"). The AI now fires toward enemies even through walls — bullets hit walls (breaking through over repeated shots) or hit the enemy directly.

3. **Two-ray scan for `scanAhead`**: The wall/enemy scan was a single ray from the tank's center, which checked only one column of the tank's 2-cell width. A wall in the offset column was invisible. Fix: cast two parallel rays offset by ±CELL/2, covering both cells. This is used by `shouldFireInFacingDir` to detect walls to shoot through.

4. **Proactive fire**: When no specific target (enemy, wall, or bullet) is in the facing direction, the AI fires anyway (`return this.rng.next() >= this.params.aimError`). A competent player holds the fire button while moving — bullets clear walls, create openings, and catch enemies that move into the line of fire. The fire cooldown limits the rate.

**Rationale:** The original AI fired 0 bullets and got 0 kills because (a) it only thought once (endFrame bug), (b) it couldn't see through walls (line-of-sight scan), and (c) it only fired when a target was in the exact facing direction (single-ray scan missed offset targets). These four fixes make the AI functional: it fires 15-43 bullets per run and kills 0-4 enemies on stage 0 classic.

**Rejected alternatives:**
- "Stop and shoot" (stop moving when a target is visible): The AI wasted too much time standing still. "Move and shoot" (navigate via A* while firing) is more aggressive and effective.
- Defensive positioning (stay near base, don't chase): Enemies overwhelmed the base faster than the AI could break through walls to see them. Chasing enemies (the original `selectTarget`) is more effective because the AI gets closer to enemies and has more shooting opportunities.

**Implications:** The AI is functional but not optimal — it can play the game (fire, kill, survive 20-54 seconds) but cannot yet clear stage 0. Further tuning (target leading, smarter navigation, fire discipline) is a follow-up task. The calibration gates (God AI Hard ≥70%) will likely fail until the AI is improved further.

## 26. Evaluator Fixes — threatRate, Weights, Pass Gate

**Decision:** Three fixes to the evaluator based on the auto-level-review-1 audit:

1. **`threatRate` formula**: Replaced the proxy metric (`bullets > 0 && enemyCount > 0` divided by `metrics.length / 60`) with actual bullet trajectory intersection. Added `incomingThreats: number` to `FrameMetrics` (counted at sample time by checking if enemy bullets are aligned with and approaching the player). The formula now uses `threatSamples * sampleInterval / (totalTicks / 60)`, which correctly accounts for the sampling interval and produces threats/second instead of the inflated proxy value.

2. **Soft-metric weights**: Normalized from 1.30 (kpm .3 + bulletDensity .25 + threatRate .25 + formationVar .2 + killDiversity .1 + terrainUtil .1 + noDeadZones .1) to exactly 1.0 (kpm .25 + bulletDensity .2 + threatRate .2 + formationVar .15 + killDiversity .08 + terrainUtil .07 + noDeadZones .05). Now `softScore` ranges 0–100 as documented.

3. **Pass gate**: Changed `totalScore = hardPass ? 1000 + softScore : 0` (where the 700 threshold was dead code since 1000+anything ≥ 700) to `totalScore = hardPass ? softScore : 0` with `passThreshold = 70`. The gate is now meaningful: a stage must score ≥70/100 on soft metrics to pass.

**Rationale:** The original threatRate was inflated by 10× (sampleInterval mismatch) and used a crude proxy instead of actual bullet trajectories. The weight sum of 1.30 meant `softScore` could exceed 100, and the 1000-point hard-pass bonus made the 700 threshold unreachable — `pass` was always `true` when `hardPass` was `true`, making the soft-score gate meaningless.
