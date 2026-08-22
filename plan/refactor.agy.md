# Refactoring Plan — Agent-Oriented Code Smell Remediation

> **Status (2026-08-23): EXECUTION COMPLETE.** All items resolved. Done:
> §1.1 (§254–256), §1.2, §1.3 A/B/C/E (§7e3b44d), §1.4 (§32cae0f), §1.5,
> §1.6, §2.1–§2.3, §2.5 (§5a9cd13), §2.6 (§257/0d3d95d), §2.7–§2.9, §3.2–
> §3.4, §3.6, §3.7 (§259), §3.3 (§258). Rejected with recorded rationale:
> §3.1 fallback removal (§252 — theme branches are live code) and §3.5 test
> reorganization (§260 — churn vs zero player value). See DECISIONS.md
> §250+ for per-item entries; determinism verified byte-identical against a
> pre-refactor batch-sim signature after every Simulation-touching change.
>
> The audit below is the historical baseline (line counts are pre-refactor).

> **Goal:** Reduce the cognitive load and failure surface for future agent sessions.
> Every item is ranked by *agent friction* — the cost an agent pays when it has to
> understand, navigate, or safely modify a tangled area.

---

## 0. Audit Summary

| Layer | Files | Lines | Top Issue |
|---|---|---|---|
| `src/game/` | 21 | ~6,300 | 47 mixin stubs, 322-line god method, One-Author leaks |
| `src/ai/` | 24 | ~20,700 | `params.ts` 3,636 lines, `think.ts` 3,275 lines |
| `src/presentation/` | 18 | ~6,400 | `UIManager.ts` 1,560 lines, dual renderer fallbacks |
| `src/config/` | 13 | ~2,500 | Hardcoded durations scattered across simulation files |
| `src/utils/` | 4 | ~960 | `pathfind.ts` 790 lines coupling AI fire-control into A* |
| `src/snapshot/` | 6 | ~1,240 | Manual 45-field clone/restore, UUID duplication |
| `src/replay/` | 10 | ~1,500 | IndexedDB wrapper duplication with snapshot |
| `tests/` | 130 | ~31,000 | Zero shared fixtures, 41× clear-arena copy-paste |
| `tools/` | 96 | ~22,700 | 4 independent worker pools, 35 ad-hoc diag scripts |

**Total production source (src/): ~39,600 lines across 96 files.**

---

## 1. Critical Smells — High Agent Friction

### 1.1 The Mixin Onion & 47 Stub Methods

**What:** Both `Simulation` and `Game` are built via TypeScript mixin chains. To let
mixins call siblings, `SimulationCore` declares 21 stub methods and `GameCore`
declares 26 stub methods, all `throw new Error('stub: …')`. The stubs exist solely
to appease the type checker.

**Why it hurts agents:**
- An agent adding a new simulation subsystem must add stubs to `SimulationCore`,
  wire the mixin into `Simulation.ts`, and understand the entire inheritance graph.
- Cross-mixin method calls are invisible to static analysis — the dependency is
  hidden behind `this.someStubMethod()`.
- Refactoring one mixin risks runtime `throw` if another mixin's ordering changes.

**Files:** `SimulationCore.ts:272–406`, `GameCore.ts:571–677`, `Simulation.ts`,
`Game.ts`, `GameRendererCore.ts:388–454`, `SpriteArtistCore.ts:448–580`.

**Proposed fix:** Replace mixin onion with **explicit composition / delegation**.

```
// Before (mixin chain):
class Simulation extends
  SimulationEffectsMixin(SimulationPowerUpsMixin(
    SimulationCombatMixin(SimulationEnemiesMixin(
      SimulationPlayerMixin(SimulationSpawnMixin(
        SimulationCore))))))

// After (composition):
class Simulation {
  private combat:   CombatSystem
  private spawn:    SpawnSystem
  private player:   PlayerSystem
  private enemies:  EnemiesSystem
  private powerUps: PowerUpSystem
  private effects:  EffectsSystem

  constructor(world: World, input: Input) {
    this.combat   = new CombatSystem(world)
    this.spawn    = new SpawnSystem(world)
    // ...
  }

  tick() { /* delegate in order */ }
}
```

Each subsystem class takes `World` (and any inter-system dependencies) as constructor
args. No stubs. No inheritance. An agent adding a new system creates a new file and
registers it in `Simulation.tick()`.

**Scope:** ~47 stubs removed across 4 files. Same pattern for `Game` (6 mixins →
composition) and `GameRenderer`/`SpriteArtist` (3+3 mixins → composition).

---

### 1.2 `GameLoop.loop` — The 322-Line God Method

**What:** `GameLoop.ts:254–575` is a single arrow function that orchestrates:
timestep accumulation, performance profiling, replay playback, live simulation
stepping, input recording, stage change detection, snapshot creation, victory/defeat
replay finalization, game-over → recovery interception, manual rewind (F7), recovery
lifecycle, auto-snapshot timer, thumbnail capture, event consumption → audio/
presentation, render throttling, UI sync, replay progress, FPS calculation, keyboard
refocusing, and next-frame scheduling.

**Why it hurts agents:**
- Any behavioral change in the game loop requires reading and understanding 322 lines
  of interleaved concerns.
- Inserting a new "phase" (e.g. a tutorial overlay, a network sync point) means
  finding the right spot in a monolith.
- Regression risk is high because unrelated features share scope variables.

**Proposed fix:** Extract named step methods:

```
loop = (now: number) => {
  const dt = this.computeDelta(now)
  this.stepSimulation(dt)      // live or replay
  this.stepRecovery()          // fade, countdown, re-arm
  this.stepSnapshots()         // auto-snapshot, thumbnails
  this.processEvents()         // audio + presentation routing
  this.stepRender()            // throttled draw
  this.stepDiagnostics()       // FPS, perf overlay
  this.scheduleFrame()
}
```

Each step method: 30–60 lines, single responsibility, independently testable.

---

### 1.3 `World.ts` God Object (970 lines, 50+ Fields)

**What:** `World` combines:
1. Runtime entity collections (tanks, bullets, mines, powerUps, explosions, popups).
2. Menu & UI navigation state (`menuCursor`, `selectedStage`, `recoveryCursor`,
   `recoveryCountdown`, `recoveryFading`).
3. Entity factory (`createTank` — 102 lines, initializing 16-field AI brains).
4. Browser persistence (`localStorage.getItem/setItem` for high scores).
5. Spatial queries (`rectHitsTerrain`, `findFreeSpawnCell`, `isInBounds`).
6. Event bus (`pushEvent`, `consumeEvents`, double-buffered arrays).

**Why it hurts agents:**
- An agent doing *any* gameplay work must read/understand 970 lines to know what
  state exists and where.
- Adding a new field risks silent snapshot desync (see §1.5).
- Menu state in the same object as gameplay state invites One-Author violations.

**Proposed fix (phased):**

| Phase | Extract | Target |
|---|---|---|
| A | Menu/UI state | `UIState` object (or separate struct on World) |
| B | `createTank()` | `TankFactory.create(world, kind, ...)` in `src/config/` or `src/game/` |
| C | `loadHighScore`/`saveHighScore` | `src/game/settings.ts` (already exists) |
| D | Spatial queries | `src/game/GridQuery.ts` or methods on `TileMap` |
| E | Event bus | `EventBus` class composed into World |

Phase A alone removes ~15 fields and decouples presentation from simulation state.

---

### 1.4 One-Author Invariant Violations

**What:** AGENTS.md §2.1 states "Only `Simulation` may modify the `World`." But
`GameCore` directly mutates `world.coop`, `world.player2`, `world.lives2`,
`world.playerLevel2`, `world.spectate`, `world.spectateDual`, `world.difficultyKey`,
`world.difficulty`, `world.themeKey`, `world.theme`, plus `GameMenu` mutates
`world.menuCursor`, `world.selectedStage`. `GameLoop` mutates world state during
recovery restore.

**Why it hurts agents:**
- An agent implementing a new feature that touches coop/spectate has no single
  source-of-truth for where those fields change.
- State corruption bugs are hard to trace because mutations are spread across 4 files.
- The AGENTS.md contract says "only Simulation", so an agent following the docs will
  not look in `GameCore` for coop setup — leading to incorrect mental models.

**Proposed fix:**
- Route all gameplay state changes through `Simulation` methods:
  `sim.requestCoopToggle(on)` already exists but `GameCore` duplicates its logic.
- For menu/UI state that legitimately lives outside simulation (cursor, selected stage):
  move to a separate `UIState` (§1.3 Phase A) so the invariant is truthful.

---

### 1.5 Manual Field Enumeration in WorldSerializer (45+ Fields)

**What:** `WorldSerializer.cloneWorld` and `restoreWorld` manually copy 45+ fields.
Every new field added to `World` or `Tank` must be added to 3 places (World class,
clone, restore) or snapshots silently lose data.

**Why it hurts agents:**
- An agent adding a gameplay field (e.g. `world.newTimer`) will correctly add it to
  `World.ts` but has a high probability of forgetting `WorldSerializer.ts`.
- The failure mode is *silent* — the game appears to work but snapshot restore drops
  the new field.

**Proposed fix (choose one):**

| Option | Approach | Tradeoff |
|---|---|---|
| A | **Schema registry**: `World` exports a `SERIALIZABLE_FIELDS` array; serializer iterates it. | Simple, but fragile if types need custom serialization. |
| B | **Reflection-based deep clone** with explicit exclusion list. | Correct by default, risk of over-serializing. |
| C | **Code-gen / test guard**: A test asserts `Object.keys(new World())` matches the serializer's field list. | Catches drift automatically; zero runtime cost. |

Option C is cheapest and catches the exact failure mode. Add a test:
```ts
test('WorldSerializer covers all World fields', () => {
  const worldKeys = Object.keys(new World())
  const clonedKeys = getClonedFieldNames()  // parse from serializer
  expect(worldKeys.sort()).toEqual(clonedKeys.sort())
})
```

---

### 1.6 Magic Numbers & Timing Inconsistencies

**What:** 21 instances of `1000 / 60` scattered across 6 simulation files instead
of using `TICK_MS` from `constants.ts`. Additionally:

| Magic Value | Occurrences | Should Be |
|---|---|---|
| `1000 / 60` | 21× in `src/game/` | `TICK_MS` |
| `-9999` (lastTurnMs sentinel) | 2× | `TURN_SENTINEL = -9999` |
| `0x9e3779b9` (golden ratio hash) | 8× in 4 files | `SEED_HASH` const |
| `0xdeadbeef` (P2 seed offset) | 2× | `P2_SEED_OFFSET` const |
| `1500` (popup duration) | 5× | `POPUP_DURATION_MS` |
| `3000` (spawn shield / game over timer) | 5× | `RESPAWN_SHIELD_MS` (exists), `GAME_OVER_TIMER_MS` |
| `{ col: 8, row: 24 }` (default P2 spawn) | 4× | `DEFAULT_P2_SPAWN` |
| `200` / `500` (explosion durations) | 3× | `SMALL_EXPLOSION_MS`, `BIG_EXPLOSION_MS` |

**Proposed fix:** Define named constants in `constants.ts` (or domain-specific config
files). Mechanically replace all literal occurrences. This is a safe, test-preserved
refactor.

---

## 2. Major Smells — Moderate Agent Friction

### 2.1 Kill/Score/AoE Pipeline Duplication (5 Locations)

**What:** The "tank dies → score += killScore → addPopup → pushEvent → addExplosion"
pipeline is copy-pasted in 5 mixin files:
- `SimulationCombat.bulletHitsTank` (lines 596–623)
- `SimulationPlayer.updateMines` (lines 360–373)
- `SimulationEnemies.triggerSacrificeAoE` (lines 497–504)
- `SimulationPowerUps.applyPowerUp('bomb')` (lines 448–465)
- `SimulationEffects.checkConditions` (partial)

Brick-wall AoE destruction is byte-identical in 2 places.

**Proposed fix:** Extract:
```ts
// src/game/KillPipeline.ts
function recordKill(w: World, victim: Tank, killer: Tank | null): void { ... }
function applyBrickAoE(w: World, cx: number, cy: number, radius: number): void { ... }
```

---

### 2.2 Player 1/2 Lifecycle Duplication (6 Locations)

**What:** Player 2 setup (lives, level, spawn point, `spawnPlayer2()`, shield timer)
and teardown (player2 = null, lives2 = 0, playerLevel2 = 0) are duplicated across:
- `SimulationCore.updatePlaying` (lines 126–140, 154–167)
- `GameCore.requestCoopToggle` (lines 219–226, 241–255)
- `GameCore.enableSpectateDual` (lines 364–379)
- `GameCore.disableSpectateDual` (lines 383–390)
- `GameLoop` recovery restore (lines 407–434)
- `World.spawnPlayer` / `spawnPlayer2` (lines 534–564)

**Proposed fix:** Centralize into `World.enablePlayer2(config)` and
`World.disablePlayer2()`, called from a single `Simulation.applyCoopToggle()`.

---

### 2.3 Free-Cell Grid Search Duplication (4 Locations)

**What:** Four independent implementations of "find a free grid cell":
- `World.findFreeSpawnCell` (lines 921–943)
- `SimulationPowerUps.findFreeDropCell` (lines 97–125)
- `SimulationPowerUps.isTankPositionClear` (lines 639–666)
- `SimulationPlayer.decoySpawnCell` (lines 223–258)

**Proposed fix:** Unify into `GridQuery.findFreeCell(world, origin, constraints)` in
a new `src/game/GridQuery.ts` or as methods on `TileMap`.

---

### 2.4 `UIManager.ts` — 1,560-Line God UI Manager

**What:** Single class managing HUD, 7 overlay screens, key-binding remapping,
dropdown menus, buff counters, theme CSS variable propagation, and score animations.

**Proposed fix:** Extract sub-controllers:
- `MenuScreen.ts` — start menu rendering & interaction
- `ControlsPanel.ts` — key binding UI
- `HudView.ts` — in-game HUD sync
- `OverlayManager.ts` — game over, stage clear, recovery overlays

---

### 2.5 God Method Hotspots (>100 Lines)

| Method | Lines | File | Fix |
|---|---|---|---|
| `GameLoop.loop` | 322 | `GameLoop.ts:254–575` | §1.2 |
| `SimulationCombat.updateMovement` | 159 | `SimulationCombat.ts:55–213` | Split: turn logic, velocity, collision |
| `GameMenu.handleStateInput` | 157 | `GameMenu.ts:19–175` | Split by game state |
| `SimulationCombat.bulletHitsTank` | 156 | `SimulationCombat.ts:538–693` | Extract kill pipeline, drop schedule |
| `SimulationPowerUps.applyPowerUp` | 148 | `SimulationPowerUps.ts:390–537` | Dispatch table per powerup type |
| `SimulationEffects.checkConditions` | 135 | `SimulationEffects.ts:94–228` | Split: death handling, win/lose check |
| `SimulationSpawn.updateSpawning` | 133 | `SimulationSpawn.ts:56–188` | Split: capacity check, tier roll, spawn |
| `SimulationCore.updatePlaying` | 126 | `SimulationCore.ts:119–244` | Delegate to subsystems |
| `SimulationCombat.tryFire` | 117 | `SimulationCombat.ts:244–360` | Extract fire-rate models |
| `GameReplay.startPlayback` | 116 | `GameReplay.ts:70–185` | Split: setup, UI binding, thumbnails |
| `SimulationPlayer.updateMines` | 110 | `SimulationPlayer.ts:291–400` | Extract AoE, use kill pipeline |
| `World.createTank` | 102 | `World.ts:572–673` | §1.3 Phase B: TankFactory |

---

### 2.6 `types.ts` Kitchen-Sink (625 Lines, 31+ Types)

**What:** Root `src/types.ts` mixes simulation types (`Tank`, `Bullet`, `AIState`),
presentation types (`Particle`, `EmitterConfig`, `CameraState`, `ThemeColors`),
config types (`DifficultyConfig`, `StageData`), and UI types (`GameSettings`,
`KeyBindings`).

The `Tank` type alone is 93 lines with 30+ fields mixing player-specific, enemy-
specific, and guard-specific properties via optional `?` markers.

**Proposed fix:**
- Move presentation types → `src/presentation/types.ts`
- Move AI types → `src/ai/types.ts` (partially done already)
- Consider splitting `Tank` into a lean core entity + composed states:
  `PlayerState`, `EnemyAIState`, `WeaponState`
  (Trade-off: this affects serialization and snapshot — evaluate carefully)

---

### 2.7 `pathfind.ts` — AI Domain Logic in a Utility (790 Lines)

**What:** `src/utils/pathfind.ts` contains A* search (correct placement) plus
`fireClearStopTicks`, `marchTicksPerCell`, and `baseRingCosts` which are God AI
domain concepts.

**Proposed fix:** Move AI-specific cost functions to `src/ai/god/` and have
`pathfind.ts` accept cost functions as parameters.

---

### 2.8 Direction/Vector Helper Duplication

**What:** Direction definitions and step helpers duplicated across:
- `src/constants.ts`: `DIR_VECTORS`, `DIR_DX`, `DIR_DY`, `dirIdx`
- `src/utils/helpers.ts`: `opposite`, `turnCW`, `turnCCW`, `moveDir`, `ALL_DIRS`
- `src/utils/pathfind.ts`: `STEP_DC`, `STEP_DR`, `EDGE_DC*`, `EDGE_DR*`

**Proposed fix:** Consolidate into a single `src/utils/direction.ts` module.

---

### 2.9 Inconsistent Time Units in Constants

**What:** `src/constants.ts` mixes milliseconds (`POWERUP_DURATION_MS = 20000`,
`EMP_DURATION_MS = 8000`) and frame counts (`DECOY_LIFESPAN_FRAMES = 1800`,
`FENCE_DURATION_FRAMES = 1200`) without a naming or documentation convention that
makes the unit obvious at a glance.

**Proposed fix:** Enforce naming convention: `*_MS` for milliseconds, `*_FRAMES` for
frame counts, `*_TICKS` for simulation ticks. Add a comment block at the top of
`constants.ts` documenting the convention.

---

## 3. Moderate Smells — Lower Agent Friction

### 3.1 Dual Renderer Fallbacks (~500 Lines)

**What:** `SpriteArtistTerrain.ts`, `SpriteArtistTanks.ts`, `SpriteArtistEffects.ts`
maintain procedural Canvas2D fallback paths alongside the SVG/cached sprite paths.

**Proposed fix:** Once SVG asset coverage is verified as complete, remove the
procedural fallbacks. This is ~500 lines of dead-weight code.

---

### 3.2 Snapshot/Replay Infrastructure Duplication

**What:**
- `src/snapshot/storage.ts` and `src/replay/storage.ts` duplicate IndexedDB wrappers.
- `src/replay/uuid.ts` and `src/snapshot/SnapshotManager.ts` duplicate `generateUUID`.

**Proposed fix:**
- Extract `src/utils/idb-store.ts` — generic `IndexedDBStore<T>` class.
- Extract `src/utils/uuid.ts` — single `generateUUID` function.

---

### 3.3 SnapshotBrowser / ReplayBrowser UI Duplication

**What:** `SnapshotBrowser.ts` (286 lines) and `ReplayBrowser.ts` (413 lines)
duplicate: filter tab implementations, entry card building, drag-and-drop file import,
and helper functions (`formatBytes`, `formatPlayTime`, `formatCreated`).

**Proposed fix:** Extract a shared `BrowserBase` component or at minimum extract
shared helpers into `src/presentation/ui/helpers.ts`.

---

### 3.4 Test Infrastructure: Zero Shared Fixtures

**What:** 130 test files with no centralized helper library. Key duplications:
- **Clear arena + place base**: 41 instances of the grid-wiping loop.
- **`placeEnemy` / `spawnEnemy`**: 22+ files with slightly varying signatures.
- **`positionPlayer`**: 19+ files.
- **`makeBullet` / `makeTank`**: 11+ files of manual mock construction.
- **`ringArena` / `makeEmptyStage`**: 6+ files with ASCII grid generators.

**Proposed fix:** Create `tests/helpers.ts`:
```ts
export function createTestWorld(opts?: { stage?, difficulty?, rngSeed? }): World
export function clearArena(world: World): void
export function placeEnemy(world: World, col: number, row: number, opts?): Tank
export function positionPlayer(world: World, col: number, row: number): void
export function makeBullet(world: World, opts: Partial<Bullet>): Bullet
export function makeTank(opts: Partial<Tank>): Tank
```

**Impact:** Eliminates ~1,000+ lines of copy-paste and makes test setup consistent.

---

### 3.5 Flat `tests/` Directory (130 Files)

**What:** All 130 test files sit in one flat directory. Unit tests, integration tests,
gate harnesses, and worker scripts are intermixed. Naming is inconsistent:
`god-ai-*` vs `godai-*`, plus milestone-numbered names (`dodge-m3`, `s12-replay-fixes`,
`m4-release-restore`).

**Proposed fix (low priority):**
- Subdirectories: `tests/ai/`, `tests/game/`, `tests/replay/`, `tests/gates/`.
- Normalize `god-ai-*` → `godai-*`.
- Rename milestone files to domain concepts (e.g. `dodge-m3` → `dodge-multibullet`).

> **Caution:** `tools/test-silent.ts` maps test files by basename to source changes.
> Moving tests into subdirs may require updating that mapping logic. Evaluate before
> executing.

---

### 3.6 Four Independent Worker Pool Implementations

**What:**
- `SimWorkerPool` in `tools/sim/sim-pool.ts`
- `ForensicPool` in `tools/diag/base-loss-forensics.ts`
- Worker pool in `tests/gate-core.ts`
- Worker pool in `tests/score-gate-core.ts`

All implement identical dispatch/error/termination loops.

**Proposed fix:** Extract `tools/lib/worker-pool.ts` — a generic `WorkerPool<TInput,
TOutput>` class. The 4 consumers become thin wrappers.

---

### 3.7 Ad-Hoc Diagnostic Scripts (35 Files in `tools/diag/`)

**What:** 35 scripts, many single-purpose (e.g. `diagnose-s32.ts` for one stage,
`m4-diagnose.ts` for one milestone). Several duplicate the headless simulation loop
instead of using `simulation-runner.ts`.

**Proposed fix:**
- Scripts that manually construct `new World(); while (tick < 18000) sim.tick()`
  should be refactored to use `runSimulation()` from `simulation-runner.ts`.
- One-off scripts tied to closed milestones can be archived to `tools/diag/archive/`
  or removed.

---

### 3.8 AI Constants Misplaced in `constants.ts`

**What:** `TACTICAL_INTERVAL_MS`, `DODGE_LOCK_MS`, `NONE_TURN_MIN_MS`,
`CORRIDOR_ESCAPE_CHANCE` (lines 184–234 of `constants.ts`) are AI-specific but live
in the shared constants file.

**Proposed fix:** Move to `src/ai/config.ts` (already exists and is the natural home).

---

## 4. Execution Priority & Phasing

> Ordered by agent-friction-reduction per unit of effort.

### Phase 1 — Quick Wins (Safe, Mechanical, High Impact)
1. **§1.6 Magic numbers** → Named constants. Pure find-replace, zero behavior change.
2. **§3.4 Test helpers** → `tests/helpers.ts`. Eliminates ~1,000 lines of duplication.
3. **§3.2 Snapshot/Replay infra dedup** → `utils/idb-store.ts`, `utils/uuid.ts`.
4. **§2.8 Direction helpers** → `utils/direction.ts`.
5. **§1.5 Option C** → Test guard for WorldSerializer field coverage.

### Phase 2 — Structural Improvements (Moderate Risk)
6. **§1.2 GameLoop.loop decomposition** → Named step methods.
7. **§2.1 Kill pipeline extraction** → `KillPipeline.ts`.
8. **§2.2 Player 1/2 lifecycle centralization**.
9. **§2.3 Grid query unification** → `GridQuery.ts`.
10. **§2.5 God method splits** — address the top 5 by line count.

### Phase 3 — Architecture Refactors (Higher Risk, Higher Reward)
11. **§1.1 Mixin → Composition** for `Simulation` (6 mixins, 21 stubs).
12. **§1.1 Mixin → Composition** for `Game` (6 mixins, 26 stubs).
13. **§1.3 World decomposition** — Phases A–E.
14. **§1.4 One-Author restoration** — route all mutations through Simulation.
15. **§1.1 Mixin → Composition** for `GameRenderer` + `SpriteArtist`.

### Phase 4 — Polish (Lower Priority)
16. **§2.4 UIManager split** → sub-controllers.
17. **§2.6 types.ts reorganization**.
18. **§2.7 pathfind.ts decoupling**.
19. **§3.1 Dual renderer fallback removal**.
20. **§3.5 Test directory reorganization**.
21. **§3.6 Worker pool unification**.
22. **§3.7 Diagnostic script cleanup**.

---

## 5. Risk & Guardrails

### 5.1 What NOT to Touch

- **`src/ai/god/params.ts`** (3,636 lines) and **`think.ts`** (3,275 lines): These
  are the God AI brain. They are *intentionally* large because they encode thousands
  of hand-tuned heuristic weights. Refactoring them risks regressions that take hours
  of A/B testing to detect. Per AGENTS.md §6.3b, God AI changes require the full
  eval-suite pipeline. **Leave them alone unless explicitly tasked with AI work.**

- **`src/config/stageData.ts`** (570 lines): Raw level data. It's large because there
  are 35 stages. This is data, not code.

- **Simulation determinism**: Any refactor that changes the *order* of operations in
  `Simulation.tick()` or its subsystems will break replay determinism. All refactors
  in §1.1 and §2.1–§2.3 must preserve call order exactly.

### 5.2 Validation Gate

Every refactoring PR must pass:
```
bun run check   # tsc --noEmit + full test suite
```

For any change touching simulation (§1.1, §1.3, §1.4, §2.1–§2.3):
```
bun test --parallel --timeout=50000  # includes heavy God-AI gates
```

### 5.3 Determinism Smoke Test

After any Simulation-layer refactor, verify determinism by replaying a known seed:
```
bun tools/sim/batch-sim.ts --stages 1,5,10,20,35 --seeds 42 --difficulty hard
```
and comparing output against a pre-refactor baseline.

---

## 6. Metrics (Pre-Refactor Baseline)

| Metric | Current | Target (Post Phase 2) |
|---|---|---|
| Mixin stub methods | 47 (Sim) + ~31 (Renderer) = 78 | 0 |
| Longest function (lines) | 322 (`GameLoop.loop`) | ≤80 |
| Functions >100 lines | 12 | ≤3 |
| `1000 / 60` literals | 21 | 0 |
| Magic seed hash literals | 8 | 0 |
| Test arena-clear copy-pastes | 41 | 1 (in helpers.ts) |
| Worker pool implementations | 4 | 1 |
| World fields in serializer (manual) | 45+ | 45+ (but with test guard) |
