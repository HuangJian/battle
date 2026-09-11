# AGENTS Rules — Details & Rationale (docs/agents.details.md)

> Companion to the slim `AGENTS.md`. Same section numbering (§0–§14) and rule IDs;
> every rule's 缘由 (why it exists), operational detail, examples, and incident history
> live here. When this doc and `AGENTS.md` disagree, `AGENTS.md` wins (it is the contract);
> file a fix rather than drifting.

---

## §0 Mission

**Rule (AGENTS §0):** judge every change against "open the browser, play for five minutes, leave with a smile" (MANIFEST §1).

- This is the tie-breaker for everything the plans don't answer: if a change does not serve that
  moment, it does not belong here, however clever.
- MANIFEST §13 "The Three Gates" (enjoyable · simple · faithful) is the operational form of this
  sentence; AGENTS §2.7 repeats it because it is the final arbiter for ambiguity.

---

## §1 What to read before writing code

**Rule (AGENTS §1):** read MANIFEST → DECISIONS → active plans → this file, every session; MANIFEST wins over plans.

- `MANIFEST.md` — the creed; §13 settles most doubts. Non-negotiable.
- `DECISIONS.md` — the decision **index**: one line per decision (§ + one-sentence status + pointer).
  You extend it, not contradict it. Full bodies live in `docs/decisions.details.md` (foundational §1–§10,
  refactor/engineering §239–§271, God-AI freeze ops §273–§276, replay/RL-bridge §279–§280, NN epoch §289–§290)
  and the topic progress docs (`docs/god-ai-tuning.progress.md`, `docs/perf-optimization.progress.md`,
  `docs/render-optimization.progress.md`, `docs/nn.progress.md`, `docs/nn.progress.intent.md`).
- `plan/mvp.md` — what the product is; its §10 MVP DoD applies to every change.
- `plan/Snapshot-Management-Framework.md` and `plan/presentation-upgrade.md` — active feature plans;
  their "Definition of Done" sections are acceptance criteria.
- `docs/presentation-audit.md` — **historical baseline** (2026-07-20, taken *before* the presentation
  upgrade). Its "current state" sections are superseded (SVG pipeline, HTML/CSS UI, DPR scaling,
  particles/camera are all built). Read for method/structure, not for facts.
- Plan-vs-MANIFEST conflict ⇒ MANIFEST wins; stop and record the conflict in `DECISIONS.md` before
  executing (§6). Recording first prevents re-litigating the same conflict next session.

---

## §2 Architecture invariants

Violating any of these is a bug **even if the tests pass** — they are enforced by codebase structure,
not by lint.

### R2.1 One Author
Only `Simulation` may mutate the `World`; everything else observes read-only.

```
Input → Simulation → World → Renderer / Audio / UI / Stats
```

- `src/game/Simulation.ts` is the only gameplay writer. `Input`, `PresentationLayer`, `AudioManager`,
  `UIManager` observe. The `RecoveryController` (+ `WorldSerializer`) is the single exception: it
  restores the World from a snapshot by overwriting state atomically, never by participating in
  gameplay rules (plan/Snapshot-Management-Framework.md).
- **Documented gray-zone exemptions** (refactor.trae.md §4.1/§4.3):
  - controller-driven state TRANSITIONS (`world.state = …`) and menu/UI-state writes (`world.ui.*`)
    are not entity mutations and are allowed from Game controllers;
  - `genId()`'s module-level `nextId` counter (World.ts) is a deliberate hidden-state exemption
    (cross-snapshot id uniqueness — see types.ts).
- Gameplay **entity** writes outside Simulation must route through Simulation entry points
  (e.g. `sim.applyTakeover()`, `sim.refundRewind()`), never direct.

### R2.2 No Hidden State
All gameplay state lives on the `World` (`src/game/World.ts`) — not in a singleton, module variable,
or closure. If it affects the game, it lives in the World; if you are tempted to add a module-level
mutable variable for gameplay, you are wrong (the one sanctioned exception is R2.1's `genId` counter).

### R2.3 Determinism Is a Promise
Fixed timestep (`TICK_MS = 1000/60`), all randomness through `world.rng` (`src/utils/RNG.ts`, seeded
mulberry32), input recordable. Same inputs + same RNG state + same World ⇒ identical replay.

- **Never call `Math.random()` inside the Simulation.** `Math.random()` is acceptable only in pure
  presentation code (particles, visual jitter) that never feeds back into the World.
- The forensics/A-B/freeze tooling assumes this: re-running a `(difficulty, stage, seed)` combo
  reproduces the run byte-for-byte (§4.7 subset re-runs, §6.3b signature gate).

### R2.4 Data Over Code
Tanks are config rows (`src/config/combat.ts` CombatProfile — "add a tank = add a row"), score values
in `src/config/score.ts` + `score-constants.ts`, colors in `src/config/theme.ts`, drops in
`src/config/powerups.ts`, stages in `src/config/stages.ts` ← `stageData.ts`, difficulty in
`src/config/difficulty.ts`. If a feature requires hardcoding entity behavior into a system,
reconsider — the engine executes, it does not hardcode.

### R2.5 Presentation Is Disposable
Particles, camera shake, animation state, screen flashes: none live in the World, none survive a
reset. On rewind (RecoveryController) or return-to-menu, `PresentationLayer.reset()` is called and
visual state is rebuilt from the World. "Same World state. Better presentation." (MANIFEST §8)

### R2.6 Zero-Asset Discipline (SVG)
Sprites are hand-authored SVG (`src/assets/sprites/*.svg`) pre-rasterized into a `SpriteCache` at
load time — not PNGs, not a raw `drawImage` path. Audio is synthesized via Web Audio API
(`src/audio/AudioManager.ts`). Future bitmap assets extend the sprite registry; they do not replace it.

### R2.7 The Three Gates (MANIFEST §13)
Every improvement must pass all three: more enjoyable · keeps the architecture simple · respects the
spirit of the original. Two of three is not enough.

---

## §3 Repository map & conventions

The tree lives **here** rather than in `AGENTS.md`, deliberately. The copy of `AGENTS.md`
that gets injected into an agent's context is truncated at ≈8.1K chars — 26% of the file,
the cut landing inside §4 — and a 5.1K tree sitting at §3 was eating **63% of the only
text an agent ever reads**, pushing §5–§17 (`never git stash`, long-run logging discipline,
Windows editing rules) below the fold. Reference data does not belong in that budget; the
rules do. Measured 2026-09-10, after two agents (human and otherwise) burned an hour on
rules that were "already written down" but never visible.

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
  githook/                 # git hooks + quality gates: pre-commit, nn-python-gate.sh
                           #   (ruff+mypy+pytest, §16 note on the sandbox delete guard),
                           #   run-logged.sh (16.7 capture-once logs), nn-clean-tmp.py
```

The three conventions under it (these stay in `AGENTS.md` — they are rules):

- **Canvas is playfield-only:** 416×416 logical, DPR-scaled via an offscreen buffer (`SpriteCache`,
  `GameRenderer`). HUD/menu/overlays are HTML/CSS in `UIManager` — moving UI back onto the canvas was
  tried and rejected (HTML/CSS is more maintainable and keeps the render loop cheap).
- **Tank sprites face UP** in the SVG; the renderer rotates per direction. Preserve when authoring.
- **`genId()`** (World.ts) is the single source of entity IDs — never mint ids elsewhere.

---

## §4 Development workflow

**Rule (AGENTS §4):** decode → audit → implement → verify against acceptance criteria → record → hand off green; debug re-runs use the failure subset only.

### 4.1–4.3 Decode / audit / implement
- **Decode** = restate the deliverable (plan's "Deliverable"/"Acceptance"), the invariants touched
  (§2), and the `DECISIONS.md` entries that constrain the design. Vague tasks ("polish the sprites",
  "make it feel better") are specced from MANIFEST's Three Gates + "Readable at a Glance" (§12).
- **Audit before build:** read the existing audit doc for the area (docs/presentation-audit.md is the
  structural template). For non-trivial refactor-grade work with no audit, write a short note in
  `docs/` (current state + target state) — presentation-upgrade.md §2 requires it.
- **Implement** keeping Simulation pure, Presentation read-only, data in `config/`; run the quality
  gates (§9) continuously, not just at the end.

### 4.4 Verify against acceptance criteria
Check each "Definition of Done" item literally. The MVP DoD (plan/mvp.md §10) applies to *every*
change: works correctly · no TS errors · no runtime errors · 60 FPS · integrates with existing
systems · restartable safely · no hidden state.

### 4.5 Record (§6) and 4.6 hand off green
Append the decision to `DECISIONS.md` + a line to the workspace memory log (§11); leave the tree
green (`bun run check`) and present the result per the agent loop's result-presentation rules.

### 4.7 Iterative debug re-runs: failure subset only (DECISIONS §120)
When a forensics/collector script change invalidates previously collected data (off-by-one fix, new
field, new 口径), do **not** re-run the full stage×seed sweep — simulations are deterministic (R2.3),
so the same combos reproduce the same failures. Re-run only the failure subset:

```
bun tools/diag/run-forensics.ts --from-json tmp/fx-120.json \
    --kinds base_destroyed,lives_exhausted,timeout \
    --selfkill --json tmp/fx-subset.json
```

- `--from-json <corpus>` derives the re-run set from the old corpus's failed runs (filter with
  `--kinds` failure causes and/or `--selfkill` = player self-inflicted base kills only). The report
  header labels the subset as such — never compare a subset corpus's absolute numbers against a full
  sweep.
- Cost: full sweep 35 stages × 120 seeds × 2 difficulties ≈ 8,400 runs (~4 min); the failure subset
  is typically <2,000 runs (seconds). §120 validated on 32 self-kill runs: 2.2s vs ~4 min,
  byte-identical failure list.
- **Full sweep is required only when the corpus itself changed** (stages, seeds, difficulty set,
  `--set` params). First sweep of a new experiment always collects with `--json` so later iterations
  have a subset to draw from.

---

## §5 Code conventions

### 5.1 Language & tooling
TypeScript `strict: true` with `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`,
`noFallthroughCasesInSwitch` (`tsconfig.json`) — the compiler is a reviewer; do not silence it with
`any` or `@ts-ignore`. Bun is the all-in-one tool (runtime, `bun test`, package manager). Vite is
dev server + build (target `es2020`). oxlint + oxfmt only — do not introduce ESLint/Prettier.

### 5.2 Commands (canonical)

```
bun run dev          # vite dev server on :8956
bun run build        # oxlint && tsc && vite build  (the gate before merge)
bun run test         # SCOPED: only tests tied to local git changes, prints only failures
bun test --parallel --timeout=50000   # full suite — ALWAYS with both flags
bun run typecheck    # tsc --noEmit --incremental
bun run lint         # oxlint
bun run format       # oxfmt
bun run check        # full gate: tsc --noEmit --incremental && bun test --parallel --timeout=50000
bun run setup        # git config core.hooksPath tools/githook  (enables pre-commit hook)
bun run freeze:check # det 21-combo signature vs tools/det-golden.v1.sha256 (~100s) — red ⇒ new-era triple
bun run freeze:l2    # archived-candidate reachability audit over the same corpus (~100s)
```

`bun run check` is the definition of "green" — run it before declaring a task done.

### 5.3 Scoped vs full test runs
`bun run test` invokes `tools/test-silent.ts`, a token-saving runner: it finds changed/untracked
files via git, maps each to relevant `tests/*.test.ts` files by basename (incl. `base`/`base-*`/
`*-base` patterns), runs **only** those, and prints **only failing-test logs** (a passing scoped run
prints one summary line; `--strict` skips entirely when nothing maps). The pre-commit hook uses the
same scoped runner and falls back to the full suite when a change maps to no test file — so it never
silently skips. `tools/runner.ts` holds the shared `spawnCapture`/`gitChangedFiles`/printing helpers.

**Heavy gate/integration tests are excluded by default:** the fast runner skips files that run
hundreds–thousands of full-game simulations (`godai-score-gate` — the worker-pool score gate, and
`calibration`). Exercise them with `bun run test --heavy` or the full suite. Keep the `HEAVY_TESTS`
list in `tools/test-silent.ts` in sync with measured wall-time (add any file whose standalone run
exceeds a few seconds). Because these gates are standalone files (not basename-matched to source
changes), they are essentially only exercised by the full suite — **if a God-AI change is landing,
run `bun test --parallel --timeout=50000` before committing** to validate the floors.

### 5.4 `bun test` flags are mandatory
- **`--parallel`**: bun does not parallelize files by default; per-FILE parallelism across the suite
  is what keeps the full run fast (the heavy gates spawn their own workers internally, but that does
  not help across files). `bun run check` already includes the flag.
- **`--timeout=50000`**: the God-AI gates run hundreds–thousands of full-game sims per file; bun's
  default 5s per-test timeout kills them. (Gate files raise their own per-`it` timeouts, but the
  runner default must also be lifted so the harness doesn't abort.)
- `test.concurrent` does **not** help — it only cooperatively schedules on one thread and will not
  parallelize synchronous CPU-bound sim work. Bare `bun test` (no flags) is a regression: slow
  and/or timing out.

### 5.5 NEVER start the dev server to validate changes
The dev server (`bun run dev`) is for the **human to playtest** — an agent must never start it (or
spin up a browser) to validate its own changes.

- Validation is the automated gates only: `bun run check` (typecheck + full suite), `bun run build`
  (adds oxlint). For presentation/UI work unit tests can't assert, rely on `tsc --noEmit`, oxlint,
  and a successful `vite build`.
- The pre-commit hook additionally runs oxfmt **in place** (it reformats files and re-stages, rather
  than failing on `--check`) plus oxlint.
- If a visual check is wanted, the human opens it themselves. Never leave a dev server running as
  "proof" of work, and never present a localhost URL as a validation step.

### 5.6 NEVER launch NN training by running `python` directly
Two sanctioned entries (DECISIONS §349 — the former unified CLI launcher `tools/training/start.ts`
was folded into them; it had already replaced `nn-training/start-training.{sh,ps1}` +
`tools/hub-start.ts` in DECISIONS §346):

- **Training console** (daily management of all training components):
  `bun run train` → http://127.0.0.1:8900 (LAN read-only + loopback control, §2026-09-09-goalnn-console-lan-readonly):
  binds 0.0.0.0 so the LAN can view any course/logs/node stats (`?course=` read-only override, never
  writes console-state), while every POST action (start/stop/smoke, pull/push/local presets,
  `rl.stream`/`rl.double_buffer`/`rl.precollect_early` toggles, rollout-node enable/concurrency,
  writes back rl-config.json) is gated to loopback callers only (403 otherwise; LAN responses strip
  the cloudflared tunnel auth key). Per-iteration metrics + sparklines, per-component log viewer,
  and change-detection supervision (sentinel mtime → auto-restart) are all read paths, open to LAN.
- **Headless one-shot runner**: `bun tools/training/train.ts --script <name>.py [args]`
  (venv setup, single-instance locking, smoke gates, `--force`, `--kill-previous`, `--detach`,
  `--torch-threads`, `--check`, `--echo`).

- Raw `python train_loop.py` / `python train_bc.py` bypasses pre-flight checks and can spawn
  duplicate training processes competing for the same lock file and weights.
- If training is already running, the runner detects it and exits cleanly; a stale lock (crashed
  process) is auto-cleaned; force-restart after a crash with `--force`.
- The lock file (`.train_loop.lock`) is managed exclusively by `train_loop.py` — the runner
  never writes to it (eliminates the shell-PID/Python-PID mismatch that caused double-spawn on Windows).
- **torch lives only in `nn-training/.venv`** (per-platform venv) — the system `python` has no torch
  (`ModuleNotFoundError: torch`). Do NOT probe with `python -c "import torch"`; use the runner's
  idempotent self-check:
  - `bun tools/training/train.ts --check` — verifies venv+torch and prints the absolute
    torch interpreter path; exit 0 = usable.
  - Print the exact command without running it: `bun tools/training/train.ts --echo --script <name>.py [args]`.
- The runner is not just `train_loop.py`: `--script <path>.py [args]` (path relative to
  `nn-training/`) runs root runners
  (`run_rl.py`, `train_loop.py`, `smoke_test.py`) or subpackage entries (`train/bc.py --arch student`,
  `train/goal_bc.py`, `train/intent_probe.py`, `scripts/eval_bridge.py`, `scripts/validate_export.py`, …)
  through the same venv, so all torch work shares one entry and agents never hit "no torch".
  Legacy flat names auto-alias to their package home (`train_bc.py` → `train/bc.py`,
  `gen_self_inj.py` → `scripts/gen_self_inj.py`, `train_rl.py` → `run_rl.py`; DECISIONS §324).

### 5.7 pwsh git commit — the reliable recipe (Windows agents)
The shell is **PowerShell 7 (pwsh)**, not bash; redirect-and-heredoc tricks that work in bash silently break
here. Each pitfall below was hit and debugged (2026-08-27) — the "reproduce" of §7 applied to git.

1. **No multi-line messages via heredoc / `$(cat <<'EOF' …)`.** PowerShell parses `<<` as redirection
   and throws before git runs. Same for `-m "line1" -m "line2"` with non-ASCII (Chinese, `§`, `→`,
   `±`): PowerShell→git arg mangling makes `git commit` exit 1 with **empty output** — no verbosity,
   no reason, just silent.
2. **Working recipe:** write the message to a temp file (e.g. `tmp/commit-msg.txt`), then
   `git commit ... -F <file>`; `--amend` lands the same way (`git commit --amend -F tmp/…`). Keep the
   temp file ASCII, under `tmp/`, and delete it after. Pathological fallback that also proved
   reliable: commit an ASCII one-liner (`git commit -m "commit-test"`), then
   `git commit --amend -F tmp/…`.
3. **pre-commit hook output is invisible when the hook fails.** The hook
   (`tools/githook/pre-commit`: typecheck, scoped tests, oxfmt in place, oxlint, freeze:check)
   prints to its own stdout, which the command wrapper swallows on failing exit — exit 1 with empty
   output is indistinguishable from a real failure. Diagnose by running it directly:
   ```
   bash tools/githook/pre-commit > tmp/hook.txt 2>&1; echo "EXIT=$LASTEXITCODE"
   ```
   The hook reformats with oxfmt **in place and re-stages** — re-check `git status` / re-stage after
   it runs (a stale index is a common downstream confusion).
4. **Verify, never assume.** After any commit run `git log -1 --pretty=fuller` + `git log --oneline -3`
   to confirm the intended message (and hash) landed — the silent exit-1 can leave a `commit-test`
   placeholder while the tree looks committed.

### 5.8 NEVER add an untracked `*.md` file to git tracking
- Already-tracked markdown (`AGENTS.md`, `DECISIONS.md`, `MANIFEST.md`, `README.md`, …) is fair game:
  edit, stage, commit like any source file.
- Untracked markdown must never be added by the agent: no `git add <untracked>.md`, and no blanket
  `git add -A` / `git add .` that could sweep one in. Commit with explicit paths; before committing,
  confirm `git status` shows no untracked `*.md` staged — if one appears, unstage
  (`git restore --staged <file>`) and leave it for the human.
- Exception: if the human explicitly asks to create/add a specific markdown file, that is allowed —
  but never auto-discover and track markdown the human did not request. (Rationale: working notes,
  plans, and scratch docs are the human's curation domain; sweeping them into history creates churn
  the human then has to undo.)

### 5.9 NN training progress must be recorded in `docs/nn.progress.md`
Every architecture change, eval result, and lesson from NN training goes there — new entries
appended at the top (reverse chronological) under numbered sections (§1, §2, …), each recording what
changed, eval results (val_loss + sim win rate), root-cause analysis, and concrete lessons. It is the
single source of truth for NN training history — not chat, not commit messages. Check it first
before architectural changes (`model.py`, `infer.ts`, `obs-encoder.ts`).

### 5.10 Style
- No classes where a function suffices; no singletons for gameplay state.
- Prefer pure functions in `utils/`; Simulation methods may mutate the World they own — that is
  their job.
- Seeded `RNG` (`world.rng`) for gameplay-affecting randomness; `Math.random()` only in pure
  presentation code that never feeds back into the World (R2.3).
- Keep the bundle small; a new dependency ⇒ justify in `DECISIONS.md` (the project is deliberately
  tiny, MANIFEST §14).

### 5.11 File placement
- New gameplay system → `src/game/`, called from `Simulation.updatePlaying()` (or the relevant state
  branch).
- New visual system → `src/presentation/`; it must not import from `src/game/` except to read types.
- New content (tank/stage/theme/difficulty) → `src/config/`; engine code must not change.
- New sprite → `src/assets/sprites/*.svg` + register in `src/assets/sprites/index.ts` (`SPRITE_URLS`);
  96×96 viewBox, tanks face UP.
- New test → `tests/`, mirroring the concern (e.g. `tests/stages.test.ts` ↔ `src/config/stages.ts`).

### 5.12 NEVER `git stash` — sandbox object-store destruction (2026-08-28 + 2026-09-06 incidents)

This environment's sandbox silently intercepts some git writes to `.git`. The **first** incident
(2026-08-28): `git stash -u` (during an A/B baseline check) deleted the entire object store — all 4
pack files vanished, `.git/refs` was removed, `git status` reported "not a git repository". The
**second** incident (2026-09-06): `git stash push <pathspec>` repeated the same destruction — the
three `objects/pack/*.pack` files, the whole `refs/` tree, and `logs/refs/heads|remotes/*` reflogs
were deleted; only `logs/HEAD` survived. Working tree and `index` were untouched both times. The
trigger mechanism is not fully understood (gc.auto=0 rules out auto-gc; the write is intercepted
mid-transaction) — **do not try to reproduce it.**

**Why "just this once" is never acceptable (the actual failure mode, 2026-09-06):**
the operator knew an A/B comparison was needed and reached for the most convenient command instead
of reading the rulebook first. Two compounding gaps: the auto-injected AGENTS.md preview is truncated
before §5 (the git-discipline section), and §5.12 lived only in `docs/agents.details.md`. Any agent
about to run a git write that is not `add`/`commit`/`push`/`fetch`/`pull` **must Read this section
first** — the cost is seconds; the cost of not doing it was a full repo restore.

**Rules that follow:**
- **Never run any `git stash` subcommand** (`push`/`pop`/`apply`/`drop`/`clear`, with or without
  `-u`/`--keep-index`/pathspec). For before/after A/B comparisons use
  `git worktree add --detach <path> HEAD` (verified safe in this sandbox; remove with
  `git worktree remove <path>` when done) or a scratch clone. Never reach for stash.
- Normal git usage (`add`/`commit`/`fetch`/`pull`; `push` is the human's) writes `.git` all the time and is safe —
  **no backup needed**. Back up the object store only before a genuinely destructive command
  (`reset --hard`, `filter-branch`, `gc`, `repack`, `prune`): `cp -r .git <tmp>/<name>-git-backup`.
- **Push after every commit — but that is the *human's* step, not the agent's** (2026-09-11 用户指令：
  「我自己来push」). Agents stop at `commit` and report the hash; never run `git push` yourself — a push
  is outward-facing and irreversible. Both incidents were fully recoverable ONLY because all local
  commits already existed on `origin`; local-only commits are the only real loss vector, so keep
  telling the human when a commit is waiting ("committing is yours, pushing is theirs").
- Remote access in this sandbox is **HTTPS-only** (SSH port is blocked: "Connection closed by
  UNKNOWN port 65535"). `origin` is already switched to `https://github.com/HuangJian/battle.git`.
  Do not switch it back to SSH.

**Recovery path (runbook verified working 2026-09-06 — two rounds of lessons folded in):**
1. **Stop, assess, back up**: copy the surviving `.git` metadata first
   (`HEAD`, `packed-refs`, `index`, `logs/`, `config`) into `tmp/<name>-git-backup/`; full
   `cp -r .git tmp/<name>-git-backup/git-full` if it is small. Confirm the working tree still holds
   your uncommitted edits (`git grep` a known-new string) before touching anything.
2. **Recreate the ref dirs** (git refuses to recognize the repo without them):
   `mkdir -p .git/refs/{heads,tags,remotes,remotes/origin} .git/logs/refs/{heads,remotes,stash}`.
3. **Restore objects**:
   - Loose object dirs (whatever the interrupted command wrote) are still there — keep them.
   - A `git fetch` that got partway leaves `*.pack` files **without** `.idx` — index them with
     `git index-pack <pack>.pack`; they become usable.
   - Older packs may exist in a local scratch clone (`/d/github/battle2-git-backup/objects/pack/`) —
     `cp` the `.pack`/`.idx`/`.rev` files in; extra packs are harmless (objects are content-addressed).
   - **Remove orphaned `.idx` files that have no `.pack`** (move them aside) — they poison
     negotiation.
4. **Clear dead refs**: any ref whose object is gone (e.g. stale `refs/cline/checkpoints/*` — IDE
   checkpoints, list them with a `git cat-file -e` loop first and save the list) blocks every
   `fetch`. Remove them with `git update-ref -d` (works on packed refs) — they were unrecoverable
   anyway.
5. **Fetch the full history**: `git fetch origin` now succeeds. Compare every
   `refs/heads/*` tip against `refs/remotes/origin/*` and the last `logs/HEAD` entries; restore the
   branch to the newest (it is always a fast-forward). **Gotcha:** `git update-ref` silently does NOT
   persist updates to refs that live in `packed-refs` (rc=0 but nothing written; loose-ref writes to
   `refs/heads/` DO persist) — edit `packed-refs` directly with python (`str.replace` on the two
   `goal-nn` lines) after backing it up. `git show-ref <branch>` is the verification.
6. **Re-sync the index without touching the working tree**: `git reset` (mixed, never `--hard`) so
   `git status` shows exactly the real uncommitted edits.
7. **Verify**: `git fsck --connectivity-only` — ignore "HEAD: invalid reflog entry" (pre-rewrite
   leftovers from the filter-repo history rewrite) and dangling objects; any `missing`/`broken`
   output means the store is still damaged. `git status -sb` must show the branch even with
   `origin`, and `git log --oneline -3 HEAD` must show today's commits.
8. Keep `tmp/<name>-git-backup/` until a successful `git push` of all branches, then delete.

Deviations seen in the field: `git fetch` may print a ref update that does not stick (same
packed-refs write gotcha) — verify with `git show-ref`, not the fetch banner.

---

## §6 When in doubt — derive, then execute; recording is the exception

The autonomy contract: make judgment calls instead of stalling. **A doubt is answered by deciding, not by filing an entry** — admission gates live in `docs/decisions/HOW-TO-ADD.md` and the default verdict is "don't write".

### 6.1 Identify the doubt
A doubt is any of: the plan silent on a design point · two reasonable implementations and the plan
picks neither · the plan appears to conflict with MANIFEST or an existing decision · an unspecified
visual/tunable value (color, timing, count, threshold).

### 6.2 Derive the optimal solution (priority order)
1. **MANIFEST** — the creed; §10 "Simple Beats Clever" and §13 "Three Gates" settle most doubts.
2. **DECISIONS.md** — closest precedent.
3. **Existing code** — consistency is a form of correctness.
4. **The classic original** — for gameplay feel, Battle City (Famicom) behavior is the reference;
   authenticity beats novelty unless the MANIFEST says otherwise.
5. **The plan's stated rationale** — if the plan gives a "why", honor it even when the "what" is
   ambiguous.

### 6.3 Record the decision BEFORE executing — but only if it survives admission

**权威清单 = `docs/decisions/HOW-TO-ADD.md`；默认结论是「不写」。** 三问闸门（有一问答 No 就不写）：

| # | 闸门 | 答 No 意味着 |
|---|---|---|
| ① | 有没有**被否决的备选方案**？ | 那是执行，不是决策（bugfix / UI 调整天然全灭）。 |
| ② | 未来 agent 会不会**重犯或重查**？ | 不会 → 不记。会 → 记，但优先考虑「勿重提清单」。 |
| ③ | **能不能就近表达**？ | 能 —— 代码注释 / 测试断言 / 配置字段名 / docstring 说清的，**一律不进中央文件**。 |

**四类归属**：真决策 / 禁令 / 铁律（不可逆、有被否决备选、防重犯）→ `DECISIONS.md`;
实验记录 / 参数调优 / 探针结论 → 对应 `docs/*.progress.md`（**禁止双写**）;
bugfix / UI 调整 / 运维清理 → **commit message only**（要防重犯处落成测试断言）;
临时上下文 → `.workbuddy/memory/YYYY-MM-DD.md`。

**三个真实踩过的坑（2026-09-11，远程 worker 热替换护栏那次）**：
1. **把「记录」当默认动作** —— 代码注释 + 5 个测试断言已经把事情说全了，仍然开了条目；
   根因是本节旧标题写着 "…Record, Then Execute"，把闸门放在了标题之后。
2. **顺手 `--write-baseline`** —— 加条目**不需要**重写基线（校验只查"编号丢失 / 新撞号 /
   新日期 ID 格式"，基线里没有的新 ID 会被正常识别为 added）。重写基线 = 一个纯机械 diff，
   且会把**已有的撞号合法化**（基线写入 count=2 后就再也不报）。
3. **自创字段 / 超长** —— 模板是固定的 4 段（背景 / 备选与否决 / 决定 / 违反后果）且 **≤12 行**；
   单条正文 >40 行会触发 `check-decisions` 告警（该搬 progress 文档了）。

**真写才走的流程（只有过闸门才执行）**：
- **New-entry ID (自治理生效起)**: `§YYYY-MM-DD-<branch>-<slug>`（branch 去连字符，如
  `god-ai` → `godai`；同日同分支第 N 条加 `-N`）. Old §1–§384 are a **frozen external-reference
  contract: never renumber, never delete**; superseded entries are marked `_(superseded by §ID)_`.
- **Entry template (≤12 lines, 备选与否决必填)**:
  背景（2 行）→ 备选与否决（A —— 否，因为…；B —— 否，因为…）→ 决定（1–3 行）→ 违反后果（1 行）.
- **Append-only + union**: new entries go at file **end only**（禁止中部插入；`.gitattributes`
  已设 `DECISIONS.md merge=union`，并行 append 零冲突）. 写前只需读
  `docs/decisions/HOW-TO-ADD.md` + `tail -200`，不要全文扫描几千行。
- **Automated guard**: `bun run check` runs `tools/check-decisions.ts`（撞号 / 丢号 /
  新条目格式，基线见 `tools/decisions-baseline.json`）.
- **Historical format（§1–§384 期间使用，仅供解读旧条目）**: foundational / architecture /
  gameplay-mechanic decisions previously used this shape — keep numbering sequential; revising an
  older decision marks it `_(superseded by §N)_` — history matters, never delete:

```markdown
## N. <Short Title>

**Decision:** <What you chose, concretely.>

**Rationale:**
- <Why, anchored in MANIFEST / precedent / the plan's intent.>
- <What you rejected and why.>

**Implications:** <Optional — what this enables or forecloses.>
```

- **God AI / performance / render tuning experiments** → verbose detail (A/B tables, forensics,
  per-seed breakdowns) goes to the matching `docs/*.progress.md`; `DECISIONS.md` gets only a
  compressed index row (`## N. Title (STATUS)` + one pointer line `> 全文 → docs/<file>.progress.md`).
  This keeps DECISIONS a scannable index, not a 300 KB wall.

### 6.3b God-AI tuning — operative rules
- **Any God-AI behavior change = a new era** requiring the triple: new `DECISIONS.md` entry +
  60-seed three-difficulty baseline (`eval-suite v7`, hard = primary, classic/chaos = reference) +
  frozen-signature golden update (`tools/det-golden.v1.sha256`; `bun run freeze:check` goes red by
  design — that is the forced explicit judgment, not an error). Official current baseline:
  `docs/god-ai-tuning.progress.md` Part 0.1 (DECISIONS §293). Restart protocol:
  `plan/God-AI-Organization.md` §8; un-archive gate: `tests/godai-archived-knobs.test.ts` header.
- **Tune on `hard`** (smallest noise, closest to the "reasonable behavior" boundary); **conclusions
  need ≥60 seeds** (CRN-paired A/B; 20 seeds only for direction screening); per-seed tick-diff
  (`docs/god-ai-tuning.progress.md` §I.5.1) locates the first diverging tick.
- classic/chaos are regression guardrails only — keep no *large* regression, don't optimize for them.
- **Historical note:** the Phase III framework (2026-08-12, hard-focused tuning + godai-score v7
  weight bands + Phase III baseline table) is **closed out** — v1 was frozen (§272, 2026-08-26) and
  then unfrozen with super-items restored (§293, 2026-08-28). The full framework text was removed
  from AGENTS.md as no-longer-operative; it lives in DECISIONS §6.3b history and
  `docs/god-ai-tuning.progress.md` §0.C.

### 6.4 Execute
Implement the recorded decision; if it proves wrong mid-implementation, update the `DECISIONS.md`
entry with a dated note and proceed — never silently deviate.

### 6.5 What NOT to decide alone (escalate, don't guess)
- Breaking the One-Author invariant (§2.1) in a way the MANIFEST forbids.
- A new runtime dependency or a new build tool.
- Altering public game feel in a way the plan did not contemplate (tank speed defaults, new game mode).
- Deleting or rewriting a system that has no test coverage and no audit doc.
- Everything else: decide, record, execute.

---

## §7 Bug-fix workflow — reproduce with a test before you fix

Mandatory, no exceptions: **a bug is not fixed until a failing test proves it existed and then
passes after your change.**

- **7.1 Reproduce first:** write the failing test in `tests/` BEFORE touching production code and
  confirm it fails on the unmodified codebase (if it passes, you have not reproduced the bug — keep
  going). Minimal: isolate the failing behavior (drive `World`+`Simulation` directly for Simulation
  bugs; test `TileMap` in isolation; for config decoders mirror the codec independently like
  `tests/stages.test.ts` — an independent re-implementation is the strongest proof). Deterministic:
  seed `world.rng` with a fixed value (`new RNG(12345)`); never depend on `Math.random()` or
  wall-clock time.
- **7.2 Then fix minimally:** the smallest change that turns the test green. Do not refactor
  opportunistically during a bug fix — that is how regressions are born. Spot a cleanup? Note it as a
  follow-up task; do not bundle it.
- **7.3 Then verify:** the new test passes; `bun run check` green (no type/lint/format regressions).

---

## §8 Testing conventions

- Runner: `bun:test` (`import { describe, it, expect } from 'bun:test'`); tests live in `tests/`.
- **Mirror the concern:** `tests/stages.test.ts` ↔ `src/config/stages.ts`; name test files after the
  module/system they cover.
- **Prefer independent re-implementations for codecs/data** (`tests/stages.test.ts` re-decodes level
  data locally and asserts equality with the production decoder) — catches decoder regressions a
  golden-file test would miss.
- **No DOM in unit tests** unless the system under test requires it — `Simulation`/`World`/`TileMap`/
  `SnapshotManager` are pure logic, test headlessly.
- **Snapshot/restoration tests must assert the full field list** (plan/Snapshot-Management-Framework.md
  §7, `tests/snapshot-framework.test.ts`): player/enemy positions, bullets, terrain destruction,
  items, score, lives, timers, enemy queue, RNG state.
- **Determinism tests:** any RNG-consuming logic gets a same-seed-twice test asserting identical
  World state.

---

## §9 Quality gates — Definition of Done

A task is done when **all** hold:

- [ ] `bun run check` green (test + typecheck + lint + format)
- [ ] `bun run build` succeeds (this is what ships)
- [ ] No new `Math.random()` in Simulation paths (§2.3)
- [ ] No new module-level mutable gameplay state (§2.2)
- [ ] No new UI drawn on the game canvas (§2.5 — UI is HTML/CSS)
- [ ] The relevant plan's "Definition of Done" checklist satisfied
- [ ] New decisions recorded in `DECISIONS.md` (§6)
- [ ] Bug fixes have a reproducing test (§7)
- [ ] 60 FPS maintained on a typical machine (MANIFEST §14, plan/mvp.md §10); profile expensive changes
- [ ] Memory bounded — snapshot history bounded by per-type retention (circular 20 auto/pause/
      stage-start, 100 manual never-overwritten); no unbounded growth
      (plan/Snapshot-Management-Framework.md)

---

## §10 Asset pipeline

- **Author** sprites as SVG in `src/assets/sprites/`, 96×96 viewBox, tanks face UP.
- **Register** each sprite in `src/assets/sprites/index.ts` → `SPRITE_URLS` (keys like `tank.<kind>`,
  `terrain.<type>`, `fx.<name>`, `item.<type>`).
- **Regenerate** the library with `node tools/gen-sprites.mjs` when adjusting the generator (rather
  than hand-editing SVGs).
- **Consume** via `SpriteLibrary` (preloads) → `SpriteCache` (pre-rasterizes to DPR-scaled bitmaps) →
  `SpriteArtist`/`GameRenderer` (draws). Never bypass the cache by loading images inline in the
  render loop.
- **Terrain tiles must be seamless**: 96×96 full-frame working memory, texture period must divide 96
  — no inset borders, no centered shrink, no mosaic seams (Modern Retro design conventions for
  palette and per-tile rules).
- **Themability:** theme-varying sprite colors come from `ThemeColors` (`src/config/types.ts`,
  re-exported by root `src/types.ts`) applied at draw time, never baked into the SVG — gameplay-
  neutral color stays in the SVG, theme-reactive color in config.

---

## §11 Memory & continuity

After substantive work, append a brief note to the workspace daily log:
`.workbuddy/memory/YYYY-MM-DD.md` (repo-relative, append-only, create if missing); curated long-term
notes go to `.workbuddy/memory/MEMORY.md`. Record: what was built/changed, the decision rationale
(link the `DECISIONS.md` entry), pitfalls discovered, next sensible step — not transient search
results or tool errors. Supplemental only: never replaces the deliverable or the reply to the user.

> Path note: the historical absolute path `/Users/hj/dev/github/battle/...` referred to the previous
> machine; the workspace now lives on Windows and the log is repo-relative.

---

## §12 Quick reference

The constants block in `AGENTS.md` mirrors `src/constants.ts` (verified 2026-08-28). If code and this
file ever disagree, the code wins — fix this file.

---

## §13 The rule behind all the rules

> Simple beats clever. Readable in six months is worth more than elegant today. (MANIFEST §10)

When this file and your instincts disagree, this file wins. When this file and the MANIFEST disagree,
the MANIFEST wins. When the MANIFEST is silent, choose the option that keeps the game small, the
architecture clean, and the player smiling — then write it down in `DECISIONS.md` so the next agent
does not have to re-derive it.

---

## §14 Performance anti-patterns — hot-path rules

Context: the God-AI tuning loop runs thousands of headless simulations; any per-tick allocation or
redundant scan is amplified ×millions. Discovered via `bun --cpu-prof` + determinism-signature
verification (`docs/perf-optimization.progress.md`). Violating these in hot paths is a bug even if
the tests pass.

- **14.1 No array allocations in per-tick functions.** V8 does not eliminate short-lived arrays —
  each `[]`+`push` is a heap object surviving to the next minor GC, and GC pauses eat the
  60 FPS / 0.02ms-per-tick budget. Fixes: hoist constant arrays to module scope (`const
  PERCEIVE_DIRS = [...]` — a 4-element array inside `perceive()` allocated ~550K times per 30-game
  batch); replace `.filter()` with an inline `if` guard while iterating the source array; replace
  tiny result arrays (`const open: Direction[] = []` + push in a 2-iteration loop) with local
  booleans.
- **14.2 No per-tick object returns in hot paths.** `return { enemy, wall, steel, … }` allocates per
  call. If the caller consumes it immediately and stores nothing: use a reusable result object on the
  owning class (`self._scanResult`) or return a primitive when only one field is used (e.g.
  `scanAhead()` in perception.ts returns a `ScanHit` string; the caller only checks the category).
- **14.3 Guard `.sort()` against empty arrays.** A 0-element sort still pays setup:
  `if (threats.length > 1) threats.sort(...)`.
- **14.4 V8 string interning is fast — don't fight it.** Terrain types are interned strings, so
  `type === 'brick'` is a pointer compare. The numeric-encoding "optimization" (codes + Uint8Array
  lookup + `TERRAIN_CODES.xxx`) measured a **28% regression**: exported mutable objects defeat
  constant-folding, the reverse-lookup in `get()` outweighs cache locality, and `getRaw()` call
  overhead + bounds checks lose to inlined `blocksTank()`. Rule: keep terrain strings in TileMap; if
  a specific hot path needs numbers, use inline literals (`=== 1`) — never `TERRAIN_CODES.xxx` — and
  benchmark against the string baseline with the determinism-signature gate.
- **14.5 Avoid `.filter()`+`.sort()` chains in per-tick paths.** `evaluateGoals()` builds a 7-element
  scores array and sorts it each tactical-think cycle — throttled (~5s, not per-tick) but the pattern
  in any per-tick path is a red flag; prefer inline scoring with a running best.
- **14.6 Reuse the `allTanks` buffer — don't rebuild.** `World.allTanks` rebuilds `_allTanksBuf` per
  call; consumers needing the list multiple times in one tick call the getter once and pass the
  reference (perceive() takes an optional `all?` param and passes it to `canStep()` 4×).

### 5.13 Never sleep-wait on long tasks — notification + marker-grep, never fixed `sleep N`

> 提炼自 2026-08-29 goal-nn 会话教训：`sleep 420 && tail log` 这类定长傻等会把回合阻塞满
> 全时长（任务早完成也干等）、还可能被用户取消；而任务完成后的推进应该零延迟。

**三条模式（按优先级）**：

1. **默认：后台任务 + 完成通知，零轮询。** 长任务（训练 / 2100 局评估 / 基线仿真）一律
   `run_in_background: true` 启动——完成时运行器自动投递通知，收到即跑下一步。
   不要在前台 `sleep` 等它。
2. **子步触发：后台 `tail -F | grep -m1`。** 需要在长日志里等**中间里程碑**（如
   `iteration 3/6`、`epoch 10/15`）时，把等待本身也放后台：
   `tail -F tmp/train.log | grep -m1 -E "iteration 3/|ALL DONE"` —— 模式一命中 grep 即退
   （SIGPIPE 带掉 tail），后台任务通知立刻到达，马上继续。用 `-F`（大写）而非 `-f`：
   训练器每轮可能重建/截断日志，`-F` 能跟随重建。
3. **短等待（<10 min）：有界标记循环。** 确实要在前台等一个很快的标记时，用单次调用内
   的有界循环，命中即出：
   ```bash
   for i in $(seq 1 20); do grep -q "ALL DONE" tmp/train.log && break; sleep 15; done; tail -5 tmp/train.log
   ```
   上限 20×15s=300s < 工具超时；关键是**退出条件是标记不是时长**。

**反模式**：`sleep N && tail log`（N 猜大了干等、猜小了没等到）；`tail -F` 裸跟（永远不退，
占满工具超时）；对已完成的后台任务再开监控（通知已经在路上）。

**纪律**：收到完成通知后立即推进下一步（读日志 → 判定 → 启动下一阶段），不要让已完成
的任务排队等下一次交互。

---

## §15 Closed-loop corpus discipline — why & recipes (AGENTS §15)

### The case study (goal-nn s1-cap, 2026-08-30)

A capped S1 RL continuation ran with a **locked corpus**: explicit `--stages
1000-1002 --seeds 0-3` = the same 12 games every iteration. Symptoms, in the
order an operator should learn to recognize them:

1. **Training win curve rises while capability does not** — 23→44% sampled win
   rate over 10 iterations, but greedy (deployment-mode) eval sat at 26.7% with
   the policy visiting ~10 cells and firing 22.7 shots/game for **zero kills**
   in losing games (a fixed "push up + spray the midline" routine that never
   tracks the enemy).
2. **Different weights, identical games** — the A4-warm and A5-scratch policies
   (provably different logits, verified via an `EVAL_DEBUG` first-frame dump)
   produced **60/60 identical** greedy outcome/tick vectors. Argmax had converged
   to the same routine on every visited state. This is the mode-collapse
   fingerprint; its eval-side enabler was reports without a weights fingerprint
   (fixed: `_eval_report.json` now carries `weightsSha`).
3. **Inverted greedy-vs-sampled gap** — sampling escapes the routine ~15-25% of
   the time and wins more than argmax. Greedy should never be *worse* than
   sampling; when it is, suspect deterministic mode collapse before blaming
   training budget.

### The KL explosion gallery (all small-batch + miscalibrated scale)

| kl (one update) | context | root cause |
|---|---|---|
| 69.9 | 12-game iter, BC warm start | random-init value head on a BC-scale trunk (V(s)≈±700) → value grads wreck the shared trunk → policy scramble |
| 119 | scratch init, kaiming | ConvMixer trunk activations ~1000 (inputs 0..255, no norm) amplify even clip-1.0 grads into 100-nat logp swings |
| 32905 | scratch init after partial fix | real-obs activations were 13× the synthetic probe's estimate; 1/α-rescaled heads hypersensitive to trunk drift |

Fixes that worked, in `run_rl.py build_model` (!resume path) and
`nn-training/init_scratch_weights.py`:

- **warm_start_normalize**: sample REAL obs (multi-shard max-union + synthetic
  extremes — a single degenerate shard once read feat_max=1 and α blew up 14×),
  scale trunk to hidden≈15, scale move/fire heads to a logit range of ~3
  (argmax-preserving soft prior, entropy≈1), zero the value head (BC checkpoints
  have none). The trunk/head rescaling is exactly function-preserving
  (Conv/Linear+ReLU are positively homogeneous).
- **Batch size as the KL stabilizer**: kl per update ≈ f(gradient SNR). At 12
  games (1.4K samples) the global advantage normalization is dominated by single-
  episode luck; at 150 games (17K samples) it is stable. Measured: first big-batch
  iter kl=0.0135 with entropy 1.77 — lower than any healthy micro iteration.

### Recipes

- **Corpus rotation**: explicit-mode `--seed-rotate N` draws N fresh seeds per
  stage per iteration, keyed `(rotateSeed, it)` (deterministic, resume-safe);
  `N=0` keeps the legacy fixed-seed behavior byte-identical. Rotate-mode
  (`--rotate-stages`) already rotates but is hard-wired to real stages.
- **Corpus sizing**: saturate the pool. Rule of thumb: games/round ≈ workers ×
  (a few minutes of wall per round). 2026-08-30 cluster (~60 workers): 150
  games/round ≈ 5-6 min rollout + ~7 min PPO. Re-estimate when the cluster
  changes; the AGENTS rule deliberately states magnitudes, not constants.
- **Free OOD validation**: keep `--eval-games-per-stage > 0` — the clean eval
  runs on real stages during the PPO window and appends `eval_log.jsonl`; with
  seed rotation the training win rate itself is also OOD.
- **Checkpoint discipline**: pre-register a mid-run greedy checkpoint with a
  stop bar (s1-cap: "greedy <50% at midpoint → stop") so a doomed run dies on a
  number, not on willpower.
- **Report fingerprints**: eval reports carry `weightsSha`; rollout manifests
  carry rewardScheme + arena layoutHash. A metric that cannot answer "which
  weights produced this" is unauditable.
- **New experiment = new directories**: switching corpus/curriculum/reward
  semantics changes the shard accounting contract; never resume `--out/--traj`
  across it (DECISIONS §296).
- **Stream mode is the default (AGENTS 15.6)**: serial runs idle the whole
  collection cluster during every PPO window (measured: ~8 min idle per
  150-game iteration ≈ half of wall time). Two rot hazards to keep in check:
  ① `rl/stream.py` requires the backend module to expose `update(...)` — the
  plain `ppo` backend lacked the alias for months (built for intent only, never
  exercised by run_rl); any new trainer backend must implement the full stream
  contract (`update` / `_ppo_load` / `load_episodes` / `chunk_episodes`).
  ② the `"stream": 1` key lives in rl-config's `intent_rl` block and is read by
  `run_rl_intent` only — `run_rl` ignores it; the launch must pass `--stream 1`
  (now the code default). Serial remains available via `--stream 0` for
  debugging (deterministic, easier attribution).

## §16 Long-run task discipline — budget & observability (AGENTS §16)

**Case study (2026-09-04, BC distillation — the 8× miss).** A 60-epoch BC run
over a 165K-frame corpus was budgeted from a 723-frame smoke: 3 epochs in 12.5 s
→ ~1.5 s/epoch "pure training" → ~85 s/epoch extrapolated → "~60 min" for 60
epochs. Reality: ~11 min/epoch → ~11 h. The extrapolation ignored fixed
per-epoch overhead (validation forward on 16.5K frames, mirrorX augmentation,
per-epoch stats + best-state clone) that is invisible at 723 frames and
dominates at 165K. The launch also piped stdout through `| tail -N`, which
buffered every epoch line until process exit — so the 8× miss stayed invisible
for 5.5 h and progress could only be guessed from CPU sampling. Two lessons,
both now rules (AGENTS 16.1-16.4).

- **Measure on the real corpus, then scale (16.1)**. For any run >5 min: launch
  1-2 epochs/batches on the actual data with output to a file, read the
  timestamps, multiply out, THEN set the full `--epochs`. Kilo-sample smokes are
  pipeline checks, not speed benchmarks. Reference point (2026-09-04, CPU 8
  cores, batch 512, student model ~67.7K): 165K frames ≈ 11 min/epoch incl. val
  + mirrorX; pure training ≈ 322 iters × ~2 s.
- **Logs to files, never pipes (16.2)**. Long tasks launch as
  `python -u ... > run.log 2>&1` (or the runner's log file), then
  `tail -f run.log` / read tail on demand. `| tail`/pipes buffer until the
  process exits — a backgrounded run with buffered output is unverifiable, and
  "unverifiable progress" is one step from "restart from scratch".
- **Mid-run artifacts (16.3)**. Emit one line per epoch/step (loss, acc, value
  loss, lr, elapsed). For weight-producing runs, checkpoint periodically:
  `train/bc.py --ckpt-every N` writes `{out}.ckpt.{epoch}` (meta carries
  epoch/best_val_loss) resumable via `--resume` — any moment is a valid stopping
  point, so an over-budget run keeps its best work.
- **Kill-vs-wait on data (16.4)**. When a run overruns: sample process CPU time
  twice, ~20 s apart (Windows: pwsh `(Get-Process -Id N).CPU`; Linux:
  `ps -o time= -p N`). CPU-seconds rising ≈ full-speed compute — let it run.
  Flat CPU with climbing RSS = hang/leak — investigate or kill. Memory alone is
  ambiguous (torch caches fluctuate); CPU delta is the signal.
- **Parallel is the default (16.5/16.6)**. Shardable long tasks run sharded.
  By tool class:
  - *Data/corpus collection*: split (stage, seed) across processes. Tools with a
    built-in pool use it; otherwise shell-level seed sharding — 8 processes each
    own a disjoint seed range writing uniquely-named shards (e.g.
    `export-godai-labels.ts` probe: 2000 games ≈ 4 min wall on 8 shards, and the
    per-worker logs land in separate files so no interleaving). Shard counts
    follow the machine: ~1 process per physical core, headroom for the reader.
  - *Batch sims / evals*: `tools/lib/worker-pool.ts` (N persistent Workers,
    results re-ordered by task id — identical to serial, so float aggregation
    stays stable) or `sim-pool.ts`; its determinism contract is *parallel ==
    serial* **only when every task is pure** — verify once per tool by
    byte-comparing a parallel run against a serial run on identical inputs
    (16.6). The God-AI exporter has no pool today; a shard driver script is the
    accepted pattern until it grows one.
  - *RL/PPO*: stream mode overlaps collection with updates (15.6); distributed
    dispatch fans out to nodes by `concurrency` + local `local_slots`. A local
    run that ignores these and trains single-worker wastes the machine.
  - *Tests*: `bun test --parallel` (AGENTS §5) and `pytest -n` (xdist — §318
    measured 27.2s → 14.8s on the full suite).
  Serial is the exception, always with a stated reason: order-sensitive
  debugging/attribution, memory-bound single huge job, or a workload so small
  that spawn cost dominates.

**Capture-once logs, self-cleaning (16.7).**

*Case study (2026-09-10, gate/PPO debugging loop).* Two red gate runs cost ~15
full-suite executions, because each run's output was only visible in that one
tool call's scrollback — so every new hypothesis needed a fresh run to get a
fresh log. Two bills came due: (a) wall time, and (b) the repeated runs blew
through this sandbox's per-turn bulk-delete budget (`[safe-delete]
{"count":56,"threshold":50,"scope":"turn"}`), after which every delete was
refused and the delete-dependent tests went red — in a state where a
single-file run proved them green. An environmental artifact that looks exactly
like a regression, and it consumed an hour of forensic work. All of it
disappears once the first run's output is on disk: run once, then `grep`/`tail`
that file as many times as needed.

The rule in three lines:

1. **Every** long run (gate, sim, probe, batch eval, training smoke) redirects
   its full output to a file **in the same tool call that starts it**.
2. Diagnosis happens against that file — `grep -nE "FAILED|error:" log`,
   `tail -n 40 log`. Re-running *just to see the output again* is the
   anti-pattern.
3. On success the log is deleted in the same step, unless it is the evidence
   (benchmark numbers, a result you must cite). Red logs are always kept.

Wrapper — `tools/githook/run-logged.sh` (verified: green deletes, red keeps and
echoes the tail, exit code passes through):

```sh
sh tools/githook/run-logged.sh tmp/logs/gate.log -- bash tools/githook/nn-python-gate.sh
KEEP_LOG=1 sh tools/githook/run-logged.sh tmp/logs/bench.log -- <cmd>   # 留档
```

Minimal inline form when a wrapper is overkill:

```sh
python -u -m pytest tests/ -n 4 -q > tmp/logs/pytest.log 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then rm -f tmp/logs/pytest.log; else tail -n 40 tmp/logs/pytest.log; fi
exit "$rc"
```

Same shape from python, for probes/sims that drive their own subprocesses:

```python
import subprocess
import sys
import time
from pathlib import Path


def run_logged(cmd: list[str], log: Path, *, keep: bool = False) -> int:
    """长任务执行器：全量落盘；失败留档并回显尾部，成功且 keep=False 时删除。"""
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT).returncode
    dt = time.time() - t0
    if rc == 0:
        if keep:
            print(f"✓ {dt:.0f}s — log kept: {log}")
        else:
            log.unlink(missing_ok=True)
            print(f"✓ {dt:.0f}s — log clean (keep=True 可留档)")
    else:
        print(f"✗ exit={rc} after {dt:.0f}s — log kept: {log}", file=sys.stderr)
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        print("\n".join(lines), file=sys.stderr)
    return rc
```

Rule of thumb that saves an hour: **single-file green + full-suite red, with
`[safe-delete]` lines in the log = environment, not a regression.**


## §17 Editing files on Windows — text-splicing discipline (AGENTS §17)

**Why this section exists.** 2026-09-04, one working session hit four separate
Windows text-splicing failures, all of which a scripted-replacement discipline
would have prevented or made instantly diagnosable:

1. A multi-line `python - <<'PYEOF'` heredoc whose replacement text contained
   nested double quotes (`help="... (continue, not retrain)"` right before the
   closing `"""`) produced `SyntaxError: unterminated string literal` — the
   heredoc/quote interaction ate the rest of the script.
2. The interactive file-edit tool reported "Successfully edited" for three
   hunks that were later found **not on disk** (file had been rolled back to
   HEAD between edits). Trust-but-verify applies to our own write path: a
   "success" is a hypothesis until grep/ast confirms it.
3. Git Bash `/tmp/foo.py` was written by `cat >`, but native Python (a Windows
   binary) resolved `/tmp` as `D:/tmp` → `can't open file`. Throwaway scripts
   must live inside the repo with cwd-relative paths.
4. `cd /d/github/battle2` then `./.venv/Scripts/python.exe` failed because the
   venv lives under `nn-training/.venv` — an environment fact, not a text bug,
   but the same class of "assumed path silently wrong" error.

**The pattern that worked (scripted all-or-nothing replacement):**

```python
# _patch_xxx.py — temp patch script, written via the file tools, run, deleted.
import io
p = 'path/to/target'            # repo-relative or explicit drive path
s = io.open(p, encoding='utf-8').read()

def rep(old, new):
    global s
    c = s.count(old)
    assert c == 1, f'count={c}: {old[:70]!r}'   # wrong-count guard fails loudly
    s = s.replace(old, new)                     #   BEFORE any write

rep("""<exact old hunk>""", """<exact new hunk>""")   # per hunk
# ...
io.open(p, 'w', encoding='utf-8', newline='\n').write(s)   # one atomic write
```

Properties that make it reliable:
- **Assertions before write**: every hunk must match exactly the expected
  number of times (usually 1). count=0 → text drifted, stop; count>1 →
  ambiguous anchor, stop. Either way the file on disk is untouched and the fix
  is a retry with a corrected hunk — no partial state, no cleanup.
- **Single atomic write**: all replacements run in memory; the file is written
  once at the end. A crash/exception mid-script = zero side effects.
- **No shell layer**: Python reads/writes bytes directly — no bash expansion,
  no quote re-parsing, no CRLF conversion surprises (write with
  `newline='\n'`).
- **Encoding**: always `encoding='utf-8'` on both read and write; the repo is
  full of CJK comments and a default codepage read/write on Windows will garble
  them.

Escalation ladder for edit jobs (biggest hammer only when needed):
1. Dedicated edit tool for single, uniquely-anchored hunks — then verify with
   grep of the anchor line (17.4).
2. Temp python patch script for multi-hunk / repeated-text edits (17.1/17.2).
3. Full-file rewrite only for wholesale regenerations (e.g. codegen), never for
   surgical changes.

Rules of thumb:
- Never construct file content by `echo`/`printf` piping into a file.
- Never inline long replacement text in `bash -c` / `python -c` strings.
- After every patch run, immediately do a cheap existence check (`ast.parse`
  for python targets, `bun build <file> --outfile ...` for TS, or grep the
  anchor) — if the file rolled back again, reapply and verify before moving on.
