# Battle City Web — Architecture

> **A record of the decisions, not a restatement of the creed.**
>
> `MANIFEST.md` states *why* this project exists. `DECISIONS.md` logs individual choices as they were made. This file documents the *as-built architecture* — the boundaries, the technology choices behind them, and the seams left open for what is not built yet.
>
> Read this to understand how the pieces fit. Read `MANIFEST.md` to understand why the fit matters.

---

## 1. Layered Topology

The system is one directional flow with a hard wall at its center:

```
Input → Simulation → World → Renderer / Audio / UI
```

Four boundaries, in one line:

1. **`Input` → `Simulation`** — keyboard edges only. `Input` never touches the World.
2. **`Simulation` → `World`** — the only writer. Enforced by convention and code review, not a type system trick.
3. **`World` → readers** — `Renderer`, `Audio`, `UI` consume read-only.
4. **`Simulation` ⇢ `readers`** — one-time-per-tick `GameEvent` stream, the only channel across the wall.

This is an architectural decision, recorded here so future changes know which side of the line they belong on.

---

## 2. Technology Choices

| Concern | Choice | Rationale |
|---|---|---|
| Language | **TypeScript** (`strict`, `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`) | The compiler is treated as a reviewer; `any` and `@ts-ignore` are banned. |
| Runtime / toolchain | **Bun** | One tool for runtime, `bun test`, and package management. No npm/ts-node split. |
| Build / dev server | **Vite** (target `es2020`) | Fast HMR for the loop-on-`:3000` dev experience; `vite build` ships the bundle. |
| Rendering | **Canvas 2D** (playfield only) | HUD/menu/overlays are HTML/CSS; the canvas stays a 416×416 battle surface. |
| Styling | **CSS Custom Properties** | Themes are injected as variables at runtime; UI reacts without a re-render of the game. |
| Lint / format | **oxlint + oxfmt** | Deliberately *not* ESLint/Prettier — one faster pass, one fewer dependency. |
| Sprites | **Hand-authored SVG → pre-rasterized bitmap cache** | Zero binary assets in the repo; theme color is applied at draw time, not baked. |
| Audio | **Web Audio API synthesis** | Zero audio files; 8-bit-style effects generated per event. |
| Persistence | **localStorage** | Settings (volume/difficulty/theme/keys/scale) and high score only. No server. |

Every one of these keeps the bundle small and the project self-contained. A new dependency requires a `DECISIONS.md` entry (AGENTS §5) — the bar is intentional.

---

## 3. The Simulation Layer (`src/game/`)

The single source of truth and the only writer of gameplay state.

* **`World.ts`** — the complete runtime state object plus entity management helpers (`createTank`, `addBullet`, `removeDeadEntities`). All gameplay state lives here; there is no gameplay state elsewhere.
* **`Simulation.ts`** — runs every system per tick in a fixed order: spawn timers → spawning → player → enemy AI → movement → bullets → power-ups → explosions → win/lose conditions → dead-entity compaction. Ordering is part of the contract.
* **`TileMap.ts`** — the 26×26 sub-block grid. Brick/steel store four `quadrants` so destruction is resolved per 16px quarter-cell. `dirtyCells` records changed cells for incremental redraw; `dirty` triggers a full rebuild.
* **`Input.ts`** — keyboard capture with per-frame edge detection (`wasPressed`) and a `moveStack` that resolves "last pressed wins." Persists held keys across the per-frame clear.
* **`Game.ts`** — the conductor: fixed-timestep accumulator loop, top-level state machine, settings load/save, wiring of every subsystem, and the recovery intercept.
* **`RecoverySystem.ts`** — snapshot manager and history recorder (see §7).

The layer is *allowed* to mutate the World. Nothing outside it is.

---

## 4. The Presentation Layer (`src/presentation/`)

Reads the World and the event stream. Never imports simulation behavior; imports types only.

* **`PresentationLayer.ts`** — orchestrates the visual subsystems and decides whether to repaint (`shouldRender` + a coarse scene signature). `reset()` rebuilds all visual state from the World — this is how rewind and menu-return discard ephemera.
* **`renderer/`** — `SpriteLibrary` (loads SVGs) → `SpriteCache` (rasterizes to DPR-scaled bitmaps once) → `SpriteArtist` / `GameRenderer` (blits). The render loop blits cached bitmaps; it never decodes images per frame.
* **`ui/UIManager.ts`** — HTML/CSS HUD and overlays (menu, pause, stage-clear, game-over, recovery, controls panel). Kept strictly out of the canvas.
* **`Camera` / `AnimationSystem` / `ParticleSystem` / `EffectsSystem`** — shake, time-based animation, pooled particles, screen flashes. All hold-state-in-the-presentation, none in the World.

The separation is mechanical, not ceremonial: because Presentation cannot write the World, a rendering bug can never change a game outcome.

---

## 5. Communication Across the Wall: `GameEvent`

The Simulation emits a typed `GameEvent` union each tick (`tank_destroyed`, `bullet_fired`, `powerup_collected`, `base_destroyed`, `stage_clear`, `player_hit`, `explosion`). `Game.ts` drains the buffer once per frame and fans it out to both `AudioManager.handleEvents` and `PresentationLayer.handleEvents`.

This is the *only* path from rules to experience. It means:

* Adding a sound or a visual reaction never touches simulation code.
* A new consumer (future: a statistics module) subscribes to the same stream.
* The World stays free of presentation concerns.

---

## 6. The Combat Capability System (`src/config/combat.ts`)

**Decision:** every tank is described by one six-dimension `CombatProfile` (firepower / projectileSpeed / fireControl / mobility / armor / special, 0–100). Concrete stats are *derived* by `profileToStats()`; the engine never holds per-tank hardcoded stats.

**Why:** a new tank becomes a data row, not a code branch. Balance becomes arithmetic on a 300-point budget.

**Invariants encoded in the mapping** (each guarded by a test):

* Bullets strictly outrun tanks — disjoint speed bands (`0.9–2.1` vs `3.6–6.0` px/tick) so the relative race can never invert.
* Unbuffed player never loses a head-on duel on cadence (`fireCooldown` ≥ every enemy archetype's).
* Steel is destroyed only at `firepower ≥ 80` — default `power` (75) cannot pierce; elite `power` (+15%) and max-level player can.

Player progression raises all six dimensions together per star; elite commanders get a +15% boost on one dimension via a *new* profile object (never mutating the shared archetype), so shallow-clone recovery stays safe.

---

## 7. Determinism & Recovery (`src/utils/RNG.ts`, `RecoverySystem.ts`)

**Decision:** all gameplay entropy flows through one seeded `RNG` (mulberry32), whose entire state is a single number. `Math.random()` is forbidden inside `src/game/`; permitted only in Presentation, where it never feeds back.

**Why:** same seed + same input ⇒ identical World every run. This single property is what makes the following three features possible from one design:

* **Recovery (built):** `RecoverySystem` keeps a fixed 60-entry circular buffer of `WorldSnapshot` (one per second). A snapshot deep-clones terrain, entities, stage, score, lives, timers, the enemy queue, and the RNG state — and shares no references with the live World. On game-over, the player chooses rewind 30s / 60s / restart; the chosen snapshot overwrites the World atomically and Presentation rebuilds. Because RNG state is captured, the restored future reproduces exactly.
* **Replay (prepared, not built):** the same snapshot + seeded RNG + recordable input stream is everything a replay system needs. No architectural change required — only a recorder/player around the existing loop.
* **Networked experiment (prepared, not built):** deterministic lockstep is the foundation. Left as a research seam.

Memory is bounded by construction (circular buffer). The system owns no mutable growth.

---

## 8. Configuration-Driven Content (`src/config/`)

Content is data, separated from engine code:

| File | Holds | Change cost |
|---|---|---|
| `combat.ts` | tank capability profiles + stat mapping | new tank = new profile |
| `stages.ts` → `stageData.ts` | 35 classic levels (13×13 numeric → 26×26 decoded grid) + enemy forces | new stage = new grid |
| `difficulty.ts` | 4 presets: speed/fire/HP mults, lives, start level | new preset = new row |
| `theme.ts` | `ThemeColors` palettes (Canvas + HTML UI) | new theme = new color set |
| `tanks.ts` | score / color / drops metadata | cosmetic only |
| `ai/config.ts` | intelligence tiers + difficulty AI scaling | new tier = new entry |

The level decoder (`stages.ts`) deliberately keeps raw numeric data separate from the codec, so the Famicom-derived maps stay diffable against their reference. Stage JSON can be externalized later by swapping one `import` for a `fetch` — the architecture already assumes it.

---

## 9. The Tactical Intelligence Framework (`src/ai/`)

**Decision:** every enemy runs one pipeline — `Perception → Situation → Goal Evaluation → Decision → Action → Execution` — over three time scales (strategic ~20s, tactical ~5s, reactive per-tick).

**Why config over code:** the four tiers (`rookie`/`soldier`/`veteran`/`commander`) differ only in a registry of dodge probability, prediction depth, reaction time, aim error, route noise, and goal weights (`ai/config.ts`). `KIND_TO_LEVEL` maps enemy kind → base tier; `DIFFICULTY_AI` scales capabilities per preset without touching the pipeline. A commander is elected to broadcast *influencing* (non-controlling) directives; autonomous tanks stay autonomous.

The per-tank brain (`AIState`, flat and serializable) lives on the tank inside the World, so it snapshots and recovers with everything else. All entropy goes through `world.rng` — the framework is fully deterministic.

---

## 10. Performance Posture

Four optimization passes converted the steady-state path to allocation-free (DECISIONS §21–§26). The relevant architecture:

* **Double-buffered event swap** — `consumeEvents()` exchanges two arrays; no per-frame allocation.
* **In-place compaction** — dead entities removed by swap-and-pop; reused buffers for `allTanks`.
* **Incremental terrain redraw** — `TileMap.dirtyCells` repaints only changed cells; a full rebuild is reserved for stage load, theme change, base destruction.
* **On-demand render skip** — `PresentationLayer.shouldRender` compares a cheap scene signature and skips the full-canvas repaint when nothing visible moved; a hidden tab stops the loop entirely. The simulation still ticks at 60Hz for determinism — only painting rests.
* **DPR cap** at ≤2 prevents overdraw on retina displays.

A `tools/bench-sim.ts` headless harness and a rolling FPS sampler (warn only after 3 consecutive sub-45fps seconds) guard regressions.

---

## 11. Future Expansion Seams

The architecture anticipates these without implementing them. Each is a composition of pieces already present — no engine fork required.

| Future capability | What already exists to support it |
|---|---|
| **Replay** | seeded RNG + `WorldSnapshot` + recordable input edges |
| **Game modes** (endless / tower-defense / boss rush) | data-driven stages, difficulty, victory conditions; rules compose |
| **New tank / stage / theme / AI tier** | pure `config/` additions, no system change |
| **Statistics dashboard** | subscribe to the existing `GameEvent` stream |
| **Community / procedural maps** | level codec already decouples data from loader; swap `import` for `fetch` |
| **Bitmap asset packs** | `SpriteCache`/sprite registry extends, does not replace, the SVG pipeline |
| **Modifiers (double bullets, ricochet, fog)** | adjust existing rules; the engine executes data |
| **Networked play** | deterministic lockstep foundation |

The seats are intentionally empty. Filling them means adding data and small systems — never rewriting the core.

---

## 12. Boundaries That Must Not Move

These are load-bearing. They are recorded here so any future change knows the cost of crossing them (a `DECISIONS.md` entry is mandatory before any of these is reconsidered):

1. Only the Simulation writes the World. (`RecoverySystem` is the sole exception — atomic restore, not play.)
2. No gameplay state outside the World object.
3. No `Math.random()` inside `src/game/`.
4. No UI drawn on the game canvas — the canvas is playfield-only.
5. Recovery history stays a fixed circular buffer — no unbounded growth.

Everything else is negotiable. These are not.
