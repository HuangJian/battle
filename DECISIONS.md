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
- **Intelligence is configuration, not code.** Tiers `rookie / soldier /
  veteran / commander` live in `src/ai/config.ts` (`INTELLIGENCE_LEVELS`) with
  capability flags + dynamic goal weights. Enemy kind → base tier via
  `KIND_TO_LEVEL` (basic→rookie, fast→soldier, power/armor→veteran). Difficulty
  scales capabilities (dodge, prediction depth, reaction, aggression,
  commander-election chance) through `DIFFICULTY_AI` — never the tank stats. New
  tiers are added by appending one registry entry; no engine change.
- **Dynamic goal scoring** replaces fixed priority lists (`evaluateGoals`):
  each candidate goal (attackBase / attackPlayer / destroyWall / retreat /
  regroup / advance) gets a weighted score from situation factors; the highest
  wins. Weights are tier-owned, so "smarter" tanks make better judgements.
- **Bullet avoidance** scales with intelligence: prediction depth (how early a
  bullet is seen), dodge probability, and a delayed-reaction model. Lower tiers
  react late and fail to dodge more often.
- **Commander system:** on difficulties with `commanderChance > 0`, one alive
  enemy is elected commander (highest tier wins) and broadcasts lightweight
  directives (pushLeft / pushRight / defendBase / attackTogether / spreadOut)
  every ~20 s. Directives *influence* — teamwork tiers heed them (goal/route
  bias); non-teamwork tiers ignore them. The commander never overrides an
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
  navigation, strategic-goal stability, commander election/broadcast,
  intelligence-scaled bullet avoidance, and config-driven tiers.

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
  inflation. Elite commanders break the budget via a +15% boost to their
  kind-specific dimension (`ELITE_DIMENSION` / `ELITE_BONUS`), applied at
  commander election in `TacticalIntelligence` (a fresh object, never mutating the
  shared base profile — safe for shallow-clone recovery).
- **Player progression:** universal growth — every star raises ALL six dimensions
  together (level 0→50, 1→60, 2→70, 3→80). Ceiling is `PLAYER_PROGRESSION`
  (`maximumLevel`, `maxMultiplier`) so hardcore/challenge modes can out-scale
  commanders without touching engine code.
- **Star damage regression (plan §12):** implemented as classic behaviour — losing
  the tank costs one star level, downgrading all dimensions together (player now has
  >1 HP at higher levels via the armor→maxHp mapping, so the downgrade is real).
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
