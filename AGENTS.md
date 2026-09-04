# AGENTS.md — Battle City Web

> **Operating manual for any coding agent working in this repository.** One sentence per rule; the
> why and full detail for every rule live in **`docs/agents.details.md`** (same section numbering).
> The creed lives in `MANIFEST.md`; decisions are indexed in `DECISIONS.md`; verbose tuning logs in
> `docs/*.progress.md`; plans in `plan/`.

---

## 0. The One-Sentence Mission

> Open the browser, play for five minutes, leave with a smile. (MANIFEST §1) — when unsure, this sentence decides.

---

## 1. Read These Before Writing Any Code

1. `MANIFEST.md` (the creed; its §13 "Three Gates" is the final arbiter) → `DECISIONS.md` (extend, never contradict) → the active plans (`plan/mvp.md`, `plan/Snapshot-Management-Framework.md`, `plan/presentation-upgrade.md` — their "Definition of Done" sections are acceptance criteria) → this file.
2. `docs/presentation-audit.md` is a historical pre-upgrade baseline — read it for method/structure, not for current facts.
3. If a plan contradicts the MANIFEST, the MANIFEST wins — stop and record the conflict in `DECISIONS.md` before proceeding (§6).

---

## 2. Architecture Invariants — Non-Negotiable

Violating any of these is a bug even if the tests pass (details & gray-zone exemptions: `docs/agents.details.md` §2).

- **2.1 One Author** — only `Simulation` may modify the `World`; everything else observes read-only (RecoveryController restores atomically; controller state-transitions and `genId()` are the documented exemptions).
- **2.2 No Hidden State** — all gameplay state lives on the `World` object; never in a singleton, module variable, or closure.
- **2.3 Determinism Is a Promise** — fixed timestep; all randomness through `world.rng` (never `Math.random()` inside the Simulation); input recordable ⇒ identical replay.
- **2.4 Data Over Code** — tanks/stages/themes/difficulty are config rows in `src/config/`; "add a tank = add a row", never hardcode entity behavior into a system.
- **2.5 Presentation Is Disposable** — particles/camera/animation never live in the World; `PresentationLayer.reset()` rebuilds them on rewind/menu.
- **2.6 Zero-Asset Discipline (SVG)** — sprites are hand-authored SVG pre-rasterized via `SpriteCache`; audio is Web-Audio-synthesized; future bitmaps extend, never replace.
- **2.7 The Three Gates (MANIFEST §13)** — more enjoyable + architecture simple + spirit of the original: all three, or reject.

---

## 3. Repository Map

```
src/
  constants.ts            # CELL=16, GRID=26, FIELD=416, TANK=32, TICK_MS, direction vectors
  types.ts                # root re-export hub; Tank/Bullet/WorldSnapshot/... live in the
                          #   four-way split: types.ts (root) / config/types.ts (ThemeColors etc.)
                          #   / ai/types.ts / presentation/types.ts — all re-exported here
  main.ts                 # Entry: wires Game into #app
  i18n/                   # zh/en localization
  game/                   # SIMULATION LAYER (only layer that mutates World)
    World.ts              #   complete runtime state + entity management
    Simulation.ts         #   composition root: six subsystems via SimulationSystems registry
    Simulation*.ts        #   the six subsystems: Spawn/Player/Enemies/Combat/PowerUps/Effects
    systems.ts            #   SimulationSystems registry (tick order contract)
    EventBus.ts  KillPipeline.ts  TankFactory.ts  GridQuery.ts   # event buffer / kill resolution / entity construction / grid lookups
    UIState.ts  settings.ts  AutoFireInput.ts  battleSpeed.ts  uiFlowGates.ts
    TileMap.ts            #   26×26 sub-block grid + cached base state
    Input.ts              #   keyboard capture; never mutates World
    Game.ts               #   top-level orchestrator; delegates to controllers below
    GameLoop.ts           #   fixed-timestep loop + event wiring (LoopController)
    GameMenu.ts  GameSnapshot.ts  GameReplay.ts   # menu/snapshot/replay controllers
  ai/                     # AI LAYER (~half of src by line count)
    GodAIInput.ts         #   player God AI facade (state + Impl delegates; normal code per §262)
    god/                  #   think.ts (orchestrator), candidates/ (~20 candidate evaluators),
                          #   params.ts / params.interface.ts / params.tables.ts / stage-adapt.ts,
                          #   FireControl, ThreatAssessor, StrategyPlanner, Navigator, PathCarve,
                          #   pathfind.ts, DecisionCore, ThreatBudget, SmartThreatModel, ...
    TacticalIntelligence.ts + perception.ts      # enemy AI, invoked by Simulation
  snapshot/               # SnapshotManager, WorldSerializer (spread clone/restore),
                          #   RecoveryController, storage (IndexedDB)
  replay/                 # InputRecorder, ReplayManager, PlaybackController, file/pack, storage
  presentation/           # PRESENTATION LAYER (read-only on World)
    PresentationLayer.ts  #   orchestrator: camera + anim + particles + effects + renderer + ui
    renderer/             #   GameRenderer/SpriteArtist Core + slices, SpriteLibrary, SpriteCache
    ui/                   #   UIManager facade over HudView / MenuScreen / ControlsPanel /
                          #   OverlayManager; plus ControlCenter, PerfOverlay, ReplayBrowser,
                          #   SnapshotBrowser, ReplayController (canvas is playfield-only, 416×416)
    Camera.ts  AnimationSystem.ts  ParticleSystem.ts  EffectsSystem.ts
  audio/AudioManager.ts   # Web Audio synthesis
  config/                 # DATA: combat (tank profiles), stages+stageData, difficulty, theme,
                          #   score+score-constants, rules, powerups, fire-rate, hp-level, speed,
                          #   base, effects-config, types
  assets/sprites/         # SVG sprite library + index.ts URL registry
  utils/                  # RNG (seeded mulberry32), helpers (snap/aabb), direction, grid-search, idb-store
  perf/                   # dev-only browser perf harness
tests/                    # bun:test specs (mirrors src/ structure by concern)
plan/                     # mvp.md, Snapshot-Management-Framework.md, presentation-upgrade.md, tasks.chat.md
docs/                     # presentation-audit.md (2026-07-20 pre-upgrade baseline, historical)
tools/
  gen-sprites.mjs          # regenerates the SVG sprite library
  lib/                     # SHARED tool infra: worker-pool.ts (the only Worker() site),
                           #   stage-spec.ts (strict stage parsing — §213 guard), cli.ts (argv parsing)
  sim/                     # headless batch sims: simulation-runner, sim-worker/pool
  diag/                    # forensics + A/B tooling: run-forensics, per-seed-diff,
                           #   decision-probe, ab-*, base-loss-* (§119/§120); archive/ = quarantined one-offs
  eval/  perf/  level/  replay/  optimize/
```

Key conventions:

- **Canvas is playfield-only**: 416×416 logical, DPR-scaled via an offscreen buffer (`SpriteCache`, `GameRenderer`). HUD/menu/overlays are HTML/CSS in `UIManager`. Do not move UI back onto the canvas.
- **Tank sprites face UP** in the SVG; the renderer rotates per direction. Preserve this convention when adding sprites.
- **`genId()`** (`World.ts`) is the single source of entity IDs.

---

## 4. Development Workflow — Executing a Plan Autonomously

Handed a plan (`plan/*.md`, a `tasks.chat.md` directive, or an inline task), follow the loop; do not ask permission for steps the MANIFEST already answers (full detail: `docs/agents.details.md` §4).

1. **Decode** the task: deliverable, touched invariants (§2), constraining `DECISIONS.md` entries — vague tasks take their spec from the Three Gates (§2.7) + MANIFEST §12.
2. **Audit before build:** read the area's audit doc if one exists; for non-trivial refactor-grade work without one, write a short current-state/target-state note in `docs/`.
3. **Implement** per §5, keeping Simulation pure, Presentation read-only, data in `config/` — and run the quality gates (§9) continuously.
4. **Verify** against the relevant plan's DoD and the MVP DoD (plan/mvp.md §10): works · no TS errors · no runtime errors · 60 FPS · integrates · restartable · no hidden state.
5. **Record** non-obvious decisions in `DECISIONS.md` (§6) and a line in the workspace memory log (§11).
6. **Hand off green:** `bun run check` must pass; present the result.
7. **Debug re-runs use the failure subset only** (DECISIONS §120): when tooling invalidates previously collected forensics data, re-run only the recorded failure subset via `run-forensics.ts --from-json` — full sweep only when the corpus itself changed.

---

## 5. Code Conventions

### Language & tooling

- TypeScript `strict` (the compiler is a reviewer — never silence it with `any`/`@ts-ignore`); Bun is the all-in-one tool (runtime, `bun test`, packages); Vite dev/build with target `es2020`; oxlint + oxfmt only — no ESLint/Prettier.

### Commands (canonical)

```
bun run dev          # vite dev server on :8956
bun run build        # oxlint && tsc && vite build  (the gate before merge)
bun run test         # SCOPED: runs only tests tied to local git changes, prints only failures
bun test --parallel --timeout=50000   # full suite, all tests — ALWAYS pass these flags
bun run typecheck    # tsc --noEmit --incremental
bun run lint         # oxlint
bun run format       # oxfmt
bun run check        # full gate: tsc --noEmit --incremental && bun test --parallel --timeout=50000
bun run setup        # git config core.hooksPath tools/githook  (enables pre-commit hook)
```

God AI freeze gates (DECISIONS §272/§293; pre-commit runs the first one):

```
bun run freeze:check # det 21-combo signature vs frozen golden (~100s) — red ⇒ new-era triple
bun run freeze:l2    # archived-candidate reachability audit over the same corpus (~100s)
```

`bun run check` is the definition of "green". Run it before declaring a task done.

- `bun run test` is the scoped token-saving runner (changed-file → basename-matched tests, prints only failures; the pre-commit hook uses it with a full-suite fallback); heavy gates (`godai-score-gate`, `calibration`) are excluded from it — run the full suite before landing God-AI changes, and keep `HEAVY_TESTS` in `tools/test-silent.ts` in sync with measured wall-time (details: `docs/agents.details.md` §5.3).
- `bun test` always takes `--parallel --timeout=50000` — both flags mandatory (details: `docs/agents.details.md` §5.4).

### Hard rules (NEVER)

- **Never start the dev server** (or spin up a browser) to validate your own changes — validation is the automated gates only (`bun run check` / `bun run build`; for UI work untestable by units: `tsc --noEmit` + oxlint + a successful `vite build`).
- **Never launch NN training with raw `python`** — always via `nn-training/start-training.sh` / `.ps1` (venv setup, single-instance locking, `--check` / `--echo --script <name>.py`; details: `docs/agents.details.md` §5.6).
- **Record every NN-training architecture change/eval/lesson in `docs/nn.progress.md`** (top, numbered §) — and check it before architectural changes.

- **On PowerShell, commit via a temp message file** — `git commit -F tmp/<ascii-file>` (delete after; `--amend -F` likewise); heredocs and non-ASCII `-m` args fail silently, and the pre-commit hook's failing output is swallowed — diagnose with `bash tools/githook/pre-commit > tmp/hook.txt 2>&1; echo "EXIT=$LASTEXITCODE"`, and verify every commit with `git log -1 --pretty=fuller` (full recipe: `docs/agents.details.md` §5.7).
- **Never `git add` an untracked `*.md`** (and no blanket `git add -A`/`git add .`) — commit tracked markdown freely, and only the markdown the human explicitly requested (details: `docs/agents.details.md` §5.8).
- **Never `git stash`** — in this sandbox the stash's object writes get silently intercepted and can delete the whole object store (2026-08-28 incident: all packs vanished, 503 commits unreadable). For A/B comparisons use `git worktree add` or a scratch clone. Normal git flow (`add`/`commit`/`push`/`fetch`) writes `.git` all the time and is safe — no backup needed; back up `.git/objects` only if you are about to run a genuinely destructive command (`reset --hard`, `filter-branch`, `gc`, `repack`). Remote access is HTTPS-only here (origin is already switched; SSH is unreachable from the sandbox) (details: `docs/agents.details.md` §5.12).
- **Never sleep-wait on a long task** — launch it in the background (the harness notifies on exit) and continue other work; while waiting only peek at the log with short non-blocking `tail` reads, and when a wait is unavoidable use a bounded marker-grep loop that exits the moment the done-marker appears (never a fixed `sleep N`; recipes: `docs/agents.details.md` §5.13).

### Style

- No classes where a function suffices; no singletons for gameplay state; prefer pure functions in `utils/` — Simulation methods may mutate the World they own.
- Gameplay-affecting randomness only via seeded `world.rng`; `Math.random()` only in presentation code that never feeds back into the World.
- Keep the bundle small — a new dependency ⇒ justify in `DECISIONS.md` (MANIFEST §14).

### File placement

- Gameplay system → `src/game/` (called from `Simulation.updatePlaying()`); visual system → `src/presentation/` (no imports from `src/game/` except types); content → `src/config/`; sprite → `src/assets/sprites/*.svg` + register in `SPRITE_URLS` (96×96 viewBox, tanks face UP); test → `tests/` mirroring the concern.

---

## 6. When in Doubt — Derive From the MANIFEST, Record, Then Execute

The autonomy contract: make judgment calls instead of stalling (full detail + DECISIONS entry template: `docs/agents.details.md` §6).

- **6.1 Identify the doubt** — plan silent on a design point / two reasonable implementations unpicked / plan-vs-MANIFEST conflict / unspecified tunable value.
- **6.2 Derive the solution** in priority order: MANIFEST → DECISIONS precedent → existing-code consistency → classic Famicom authenticity → the plan's stated rationale.
- **6.3 Record BEFORE executing** — foundational decisions get full entries in `DECISIONS.md` (sequential numbering; superseded entries are marked `_(superseded by §N)_`, never deleted); tuning experiments get a compressed index row + full text in the matching `docs/*.progress.md`.

- **6.3b God-AI behavior changes = a new era** — required triple: new `DECISIONS.md` entry + 60-seed three-difficulty baseline (eval-suite v7; `hard` primary, classic/chaos reference) + frozen-signature golden update (`bun run freeze:check` going red is the forced explicit judgment, not an error); tune on `hard`, conclude only on ≥60 seeds — current official baseline: `docs/god-ai-tuning.progress.md` Part 0.1 (DECISIONS §293).

### 6.4 Execute

Implement the recorded decision; if it proves wrong mid-way, update the `DECISIONS.md` entry with a dated note and proceed — never silently deviate.

### 6.5 What NOT to decide alone

Escalate to the human (ask, don't guess) only for: One-Author breaks the MANIFEST forbids · a new runtime dependency or build tool · public game-feel changes the plan did not contemplate (tank speed defaults, new game modes) · deleting/rewriting a system with no test coverage and no audit doc — everything else is yours to decide, record, and execute.

---

## 7. Bug-Fix Workflow — Reproduce With a Test Before You Fix

Mandatory, no exceptions: **a bug is not fixed until a failing test proves it existed and then passes after your change.**

- **7.1 Reproduce first** — a minimal, deterministic failing test in `tests/` (fixed seed, no `Math.random()`/wall-clock; codecs get independent re-implementations like `tests/stages.test.ts`), confirmed failing on the unmodified codebase before touching production code.
- **7.2 Then fix minimally** — the smallest change that turns the test green; no opportunistic refactoring (note cleanups as follow-up tasks, don't bundle).
- **7.3 Then verify** — the new test passes and `bun run check` is green.

---

## 8. Testing Conventions

- **Runner**: `bun:test` (`import { describe, it, expect } from 'bun:test'`). Tests live in `tests/`.
- **Mirror the concern**: `tests/stages.test.ts` ↔ `src/config/stages.ts`. Name new test files after the module or system they cover.
- **Prefer independent re-implementations for codecs/data**: see `tests/stages.test.ts` for the pattern — it re-decodes the level data locally and asserts equality with the production decoder. This catches decoder regressions that a "golden file" test would miss.
- **No DOM in unit tests** unless the system under test requires it. `Simulation`/`World`/`TileMap`/`SnapshotManager` are pure logic — test them headlessly.
- **Snapshot/restoration tests** must assert the full field list (see plan/Snapshot-Management-Framework.md §7 and tests/snapshot-framework.test.ts): player position, enemy positions, bullets, terrain destruction, items, score, lives, timers, enemy queue, RNG state.
- **Determinism tests**: when adding RNG-consuming logic, add a test that runs the same seed twice and asserts identical World state.

---

## 9. Quality Gates — Definition of Done

A task is done when **all** of these hold:

- [ ] `bun run check` is green (test + typecheck + lint + format).
- [ ] `bun run build` succeeds (this is what ships).
- [ ] No new `Math.random()` in Simulation paths (§2.3).
- [ ] No new module-level mutable gameplay state (§2.2).
- [ ] No new UI drawn on the game canvas (§2.5 — UI is HTML/CSS).
- [ ] The relevant plan's "Definition of Done" checklist is satisfied.
- [ ] New decisions recorded in `DECISIONS.md` (§6).
- [ ] Bug fixes have a reproducing test (§7).
- [ ] 60 FPS maintained on a typical machine (MANIFEST §14, plan/mvp.md §10). If your change is expensive, profile it.
- [ ] Memory stays bounded — snapshot history is bounded by per-type retention policies (circular 20 for auto/pause/stage-start, 100 for manual never-overwritten); do not introduce unbounded growth (plan/Snapshot-Management-Framework.md).

---

## 10. Asset Pipeline

- **Author sprites as SVG** in `src/assets/sprites/`, 96×96 viewBox. Tanks face UP.
- **Register** each new sprite in `src/assets/sprites/index.ts` → `SPRITE_URLS` (key like `tank.<kind>`, `terrain.<type>`, `fx.<name>`, `item.<type>`).
- **Regenerate the library** with `node tools/gen-sprites.mjs` if you are adjusting the generator rather than hand-editing SVGs.
- **Consume** via `SpriteLibrary` (preloads) → `SpriteCache` (pre-rasterizes to canvas bitmaps at DPR) → `SpriteArtist`/`GameRenderer` (draws). Do not bypass the cache by loading images inline in the render loop.
- **Terrain tiles must be seamless** (working memory: 96×96 full-frame, texture period must divide 96 — no inset borders, no centered shrink, no mosaic seams). See the Modern Retro design conventions for the palette and per-tile rules.
- **Themability**: sprite colors that vary by theme come from `ThemeColors` (`src/config/types.ts`, re-exported by the root `src/types.ts`) applied at draw time, not baked into the SVG. Keep gameplay-neutral color in the SVG; keep theme-reactive color in config.

---

## 11. Memory & Continuity

After substantive work, append a brief note to `.workbuddy/memory/YYYY-MM-DD.md` (repo-relative, append-only; create if missing) and durable conventions to `.workbuddy/memory/MEMORY.md`: what was built/changed, the decision rationale (link the `DECISIONS.md` entry), pitfalls discovered, and the next sensible step — never transient search results or tool errors. Supplemental only: never replaces the actual deliverable or your reply to the user.

---

## 12. Quick Reference — Constants You Will Need

```
CELL = 16            // sub-block px
GRID = 26            // sub-blocks per side
FIELD = 416          // playfield px (GRID × CELL)
TANK = 32            // tank px (2 × CELL)
TICK_MS = 1000/60    // fixed timestep
MAX_ENEMIES_ALIVE = 4
ENEMIES_PER_STAGE = 20
START_LIVES = 3
PLAYER_SPAWN = { col: 8, row: 24 }
BASE_POS = { col: 12, row: 24 }   // base eagle, 2×2 at rows 24-25 / cols 12-13
ENEMY_SPAWNS = [ {0,0}, {12,0}, {6,0} ]   // tile coords
```

Tank kinds: `'player' | 'basic' | 'fast' | 'power' | 'armor'` (MANIFEST: players wear stars, enemies wear faces).
Terrain chars in stage grids: `'.' 'b' 's' 'w' 'f' 'i' 'E'` (empty/brick/steel/water/forest/ice/base).
Game states: `'menu' | 'playing' | 'paused' | 'stageclear' | 'gameover' | 'victory' | 'recovery'`.

---

## 13. The Rule Behind All the Rules

> Simple beats clever. Readable in six months is worth more than elegant today. (MANIFEST §10)

When this file and your instincts disagree, this file wins. When this file and the MANIFEST disagree, the MANIFEST wins. When the MANIFEST is silent, choose the option that keeps the game small, the architecture clean, and the player smiling — then write it down in `DECISIONS.md` so the next agent does not have to re-derive it.

---

## 14. Performance Anti-Patterns — Hot-Path Rules

> The God-AI tuning loop runs thousands of headless simulations: any per-tick allocation or redundant scan is amplified ×millions (evidence via `bun --cpu-prof` + determinism-signature verification: `docs/perf-optimization.progress.md`). Violating these in hot paths is a bug even if the tests pass; details/examples: `docs/agents.details.md` §14.

- **14.1** No array allocations in per-tick functions — hoist constant arrays to module scope, inline `if` guards instead of `.filter()`, local booleans instead of tiny result arrays.
- **14.2** No object allocations for hot-path return values — reuse a result buffer on the owning class or return a primitive.
- **14.3** Guard `.sort()` against empty arrays (`if (arr.length > 1) arr.sort(...)`).
- **14.4** Don't fight V8 string interning — keep terrain as strings in the TileMap; never "optimize" through mutable numeric lookup objects (measured 28% regression).
- **14.5** No `.filter()` + `.sort()` chains in per-tick paths — prefer inline scoring with a running best.
- **14.6** Reuse the `World.allTanks` buffer — call the getter once and pass the reference to same-tick consumers.

---

## 15. Closed-loop corpus discipline (training / CMA-ES / sweep loops)

> Any "evaluate → update → re-evaluate" loop (RL/PPO, BC-DAgger, CMA-ES, parameter
> sweeps) optimizes its evaluation set itself — without corpus rotation the score
> rises while the capability does not. Case study & recipes:
> `docs/agents.details.md` §15 (s1-cap: a locked 12-game set produced a 23->44% memorized win curve while greedy eval sat at 26.7% with 60/60 identical games across two policies).

- **15.1 Rotate the corpus every round**: the (stage, seed) pairs of round *it*
  must not repeat any earlier round (draw keyed by `(runSeed, it)`, resume-safe).
  Re-grinding a fixed set = memorization; treat those metrics as void.
- **15.2 Micro-corpus is a sentinel, not a verdict**: small fast loops (<~50
  games/round, order-of-magnitude — re-estimate with cluster size) only validate
  "pipeline works / reward arm has gradient / incident attribution"; their win
  curves are never a capability conclusion. Capability claims need a corpus that
  saturates the cluster plus an independent validation channel (e.g. clean eval).
- **15.3 Evaluate the deployment mode separately**: training sampling win rate ≠
  greedy/deployment win rate — argmax converges onto fixed routines that sampling
  would escape. The gap between the two, and "different weights produce 60/60
  identical greedy games", are mode-collapse fingerprints worth acting on.
- **15.4 Large batches are the KL stabilizer**: size closed-loop updates (PPO
  etc.) so advantage normalization is not dominated by single-episode luck;
  kl / entropy / value / gnorm are the four must-read numbers of every update.
- **15.5 Changing corpus / curriculum / reward semantics = a new experiment**:
  fresh `--out/--traj` directories + a DECISIONS entry; never resume across the
  change (the accounting contract has changed).
- **15.6 RL training defaults to stream mode** (collection and PPO waves
  overlap): serial mode idles the entire collection cluster during every PPO
  window (~half of wall time at 150 games/iter). Pass `--stream 0` only with a
  stated reason; keep the stream path exercised — it rotted once unnoticed
  (`ppo.update` alias missing for the plain backend).

## 16. Long-Run Task Discipline — Budget, Parallelism & Observability

> Any long task (training runs, batch sims, full test suites) costs hours of wall
> time, and two failure modes make it cost twice: a duration estimate off by an
> order of magnitude, and output you cannot read until the process exits. Both
> are preventable. Case study: `docs/agents.details.md` §16 (2026-09-04 BC
> distillation — 11 min/epoch measured vs ~85 s extrapolated from a 723-frame
> smoke (8× miss, "~60 min" run took ~11 h), and a `| tail` pipe hid every epoch
> line so the miss was invisible until the end).

- **16.1 Speed-budget before launch**: any run expected to take >5 min gets
  1-2 epochs/batches measured on the REAL corpus first — read the log
  timestamps, then scale. Never extrapolate per-step cost from a kilo-sample
  smoke: fixed overhead (validation, augmentation, per-epoch stats) is invisible
  there and dominates at scale.
- **16.2 Logs go to files, always**: redirect long-task output to a log file
  (`> run.log 2>&1`); never feed it into a `| tail`/pipe that buffers until
  exit. Unreadable output = unverifiable progress = one step from a wasted
  restart.
- **16.3 Progress observable mid-run**: emit a per-epoch/per-batch line (loss,
  metric, elapsed) to the log, and checkpoint weight-producing runs every N
  epochs (`train/bc.py --ckpt-every N`) so any moment is a valid stopping point —
  an over-budget or crashed run keeps its best work instead of starting over.
- **16.4 Decide kill-vs-wait from measurements**: when a run overruns budget,
  sample its CPU time twice across ~20 s (rising CPU-seconds = working, flat CPU
  + climbing memory = leak/hang). Kill or wait on data, not vibes.
- **16.5 Parallelize every shardable long task — never default to single-core**:
  corpus/data collection splits by (stage, seed) across N processes; batch sims
  run through `worker-pool.ts`/`sim-pool.ts` (its determinism contract makes
  parallel == serial byte-for-byte when tasks are pure); RL uses stream mode +
  node concurrency + local slots; tests take `--parallel` (bun) / `-n` (pytest
  xdist). Parallelism is the default, not an optimization — serial is the
  exception, stated with a reason (order-sensitive debug, memory-bound, tiny
  workload where spawn cost dominates). Shell-level seed sharding is the
  fallback when a tool has no built-in pool (e.g. `export-godai-labels.ts`:
  8-way shard split measured ~4 min for 2000 games vs unbounded serial).
- **16.6 Verify parallel == serial once per tool**: before trusting a parallel
  path, byte-compare its output against a serial run on the same inputs (the
  worker-pool determinism note assumes pure tasks — confirm the tool honors it).

## 17. Editing Files on Windows — Text-Splicing Discipline

> Text splicing through PowerShell/bash on Windows fails constantly: heredoc
> quoting, nested quotes, CRLF vs LF, `|` buffering, and path mangling (`/tmp`
> resolves to `D:\tmp` under native Python, not Git Bash's `/tmp`). In-repo
> agent runs confirmed it — most edit attempts that piped text through the
> shell mis-landed or silently no-op'd, while scripted replacements succeeded.
> Full case study: `docs/agents.details.md` §17.

- **17.1 Scripted replacements for multi-hunk edits**: read the file once in
  Python, apply each hunk behind `assert old.count(...) == expected`, write
  back only after every hunk matched — all-or-nothing, a failed run leaves the
  file untouched and is safe to retry.
- **17.2 Keep hunk text out of the shell**: quoted heredocs (`<<'EOF'`) stop
  bash expansion, but nested/triple quotes still corrupt inline `python -c`
  and heredoc scripts. Beyond a one-liner, write the patch to a temp `.py`
  with the file tools, run it, delete it.
- **17.3 Windows path discipline**: native Python resolves `/tmp` to `D:\tmp`,
  not Git Bash's `/tmp`; keep throwaway scripts repo-relative (cwd), never in
  shell temp dirs. Use the managed runtime's absolute python path or a venv
  path relative to the dir you `cd`'d into — bare `./.venv` fails from the
  repo root.
- **17.4 Verify the write landed**: after patching, run a cheap check (`python
  ast.parse`, `bun build <file>`, grep the anchor). "Reported success" is not
  "on disk" — silent rollback has happened here.
- **17.5 Anchor multi-line hunks on unique context**: replace a signature plus
  its neighbors, never a bare line that recurs (wrong-count asserts catch the
  rest).
- **17.6 Shell encoding facts on this machine (zh-CN Windows)**: the
  PowerShell tool runs pwsh 7.6.5 Core (not 5.1) as a clean session that does
  NOT load `$PROFILE`, and `[Console]::OutputEncoding` defaults to gb2312
  (cp936) — CJK text printed to the console comes back garbled under UTF-8.
  When a command must emit or capture CJK, set both
  `[Console]::OutputEncoding` and `$OutputEncoding` to UTF-8 first, or (better)
  route text through the python channel of 17.1 with explicit
  `encoding='utf-8'`. Never rely on a user profile to fix this.

- **17.7 PowerShell invocations use `pwsh` only — never bare `powershell`**
  (2026-09-04, DECISIONS §323): repo scripts, run-books, Makefiles, and examples
  invoke `pwsh` (PowerShell 7; this machine runs 7.6.5). Bare `powershell`
  resolves to inbox Windows PowerShell 5.1
  (`C:\Windows\System32\WindowsPowerShell\v1.0`), an OS component that cannot
  be uninstalled and must not be called from the repo — the two coexist
  side-by-side and only `pwsh` is used.
