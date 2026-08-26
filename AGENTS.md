# AGENTS.md — Battle City Web

> **Operating manual for any coding agent working in this repository.**
>
> Read this file first. Read it fully. It is the contract between you and the project.
>
> The creed lives in `MANIFEST.md`. The decisions live in `DECISIONS.md` (the **index**: §1–§10 foundational decisions in full + compressed pointers). The verbose God AI / performance / render tuning logs live in `docs/*.progress.md`. The plans live in `plan/`. This file tells you how to *execute* all three without breaking what makes the game worth building.

---

## 0. The One-Sentence Mission

> Open the browser, play for five minutes, leave with a smile. (MANIFEST §1)

Every change is judged against that moment. If a change does not serve it, the change does not belong here. When you are unsure, this sentence decides.

---

## 1. Read These Before Writing Any Code

In this order, every session:

1. **`MANIFEST.md`** — the creed. Non-negotiable. Section 13 ("The Three Gates") is the final arbiter for every ambiguity.
2. **`DECISIONS.md`** — the decision **index**: §1–§10 foundational decisions (full text) + compressed pointers to the verbose tuning logs. You extend it, not contradict it. Full God AI / performance / render tuning detail lives in `docs/god-ai-tuning.progress.md`, `docs/perf-optimization.progress.md`, `docs/render-optimization.progress.md`.
3. **`plan/mvp.md`** — what the product is and the milestone structure.
4. **`plan/Snapshot-Management-Framework.md`** and **`plan/presentation-upgrade.md`** — the active feature plans. Their "Definition of Done" sections are acceptance criteria.
5. **`docs/presentation-audit.md`** — **historical baseline**: the 2026-07-20 audit taken *before* the presentation upgrade; its "current state" sections are superseded (SVG pipeline, HTML/CSS UI, DPR scaling, particles/camera are all built). Read it for method/structure, not for current facts.
6. **This file** — your operating contract.

If a plan you are asked to execute contradicts the MANIFEST, the MANIFEST wins. Stop and record the conflict in `DECISIONS.md` before proceeding (see §6).

---

## 2. Architecture Invariants — Non-Negotiable

These come from MANIFEST §3–§8 and are enforced by the codebase structure. Violating any of them is a bug, even if the tests pass.

### 2.1 One Author

```
Input → Simulation → World → Renderer / Audio / UI / Stats
```

- **Only `Simulation` may modify the `World`.** (`src/game/Simulation.ts`)
- Everything else — `Input`, `PresentationLayer`, `AudioManager`, `UIManager`, `RecoveryController` — **observes** the World read-only.
- The `RecoveryController` (with `WorldSerializer`) is the single exception: it restores the World from a snapshot, but it does so by overwriting state atomically, never by participating in gameplay rules (see plan/Snapshot-Management-Framework.md).
- **Explicit exemptions (gray zone, documented — refactor.trae.md §4.1/§4.3):** controller-driven state TRANSITIONS (`world.state = …`) and menu/UI-state writes (`world.ui.*`) are not entity mutations and are allowed from Game controllers; `genId()`'s module-level `nextId` counter in World.ts is a deliberate hidden-state exemption (cross-snapshot id uniqueness — see types.ts). Gameplay ENTITY writes outside Simulation must route through Simulation entry points (e.g. `sim.applyTakeover()`, `sim.refundRewind()`), never direct.

### 2.2 No Hidden State

There is no gameplay state outside the `World` object (`src/game/World.ts`). Not in a singleton, not in a module variable, not in a closure. If it affects the game, it lives in the World. If you are tempted to add a module-level mutable variable for gameplay, you are wrong — put it on the World.

### 2.3 Determinism Is a Promise

- Fixed timestep (`TICK_MS = 1000/60`, `src/constants.ts`).
- All randomness flows through `world.rng` (`src/utils/RNG.ts`). **Never call `Math.random()` inside the Simulation.** `Math.random()` is only acceptable in pure presentation code (particles, visual jitter) that never feeds back into the World.
- Input is recordable. Same inputs + same RNG state + same World ⇒ identical replay, always.

### 2.4 Data Over Code

Tanks are config: combat power lives in `src/config/combat.ts` (CombatProfile rows — "add a tank = add a row"), score values in `src/config/score.ts` + `score-constants.ts`, colors in `src/config/theme.ts`, drops/power-ups in `src/config/powerups.ts`. Stages are config (`src/config/stages.ts` ← `stageData.ts`). Difficulty is config (`src/config/difficulty.ts`).

> Adding a new tank = adding a row, not editing a system. Adding a stage = appending a grid. Adding a theme = swapping presentation data.

If your feature requires hardcoding a new entity behavior into a system, reconsider. The engine executes; it does not hardcode.

### 2.5 Presentation Is Disposable

Particles, camera shake, animation state, screen flashes — **none of it lives in the World.** None of it survives a reset. When the game rewinds (RecoveryController) or returns to menu, `PresentationLayer.reset()` is called and visual state is rebuilt from the World.

> Same World state. Better presentation. (MANIFEST §8)

### 2.6 Zero-Asset Discipline (Now: SVG)

Sprites are drawn, not shipped as bitmaps. The current pipeline is **SVG assets** (`src/assets/sprites/*.svg`) pre-rasterized into a `SpriteCache` at load time — not PNGs, not a raw `drawImage` path. Audio is synthesized via Web Audio API (`src/audio/AudioManager.ts`). When bitmap assets eventually arrive, they extend the sprite registry; they do not replace it.

### 2.7 The Three Gates (MANIFEST §13)

Every improvement must pass **all three**:

1. It makes the game more enjoyable.
2. It keeps the architecture simple.
3. It respects the spirit of the original.

Two out of three is not enough. If a feature adds complexity without noticeably improving the player's five minutes, reject it.

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

When you are handed a plan (a `plan/*.md` milestone, a `tasks.chat.md` directive, or an inline task), follow this loop. Do not ask for permission on steps the MANIFEST already answers.

### Step 1 — Decode the task

Restate, in your own working notes, three things:

- **What** the deliverable is (from the plan's "Deliverable" / "Acceptance" sections).
- **Which invariants** it touches (re-read §2 of this file).
- **Which `DECISIONS.md` entries** already constrain the design.

If the task is vague ("polish the sprites", "make it feel better"), the MANIFEST's Three Gates (§2.7) and "Readable at a Glance" (MANIFEST §12) are your spec. Derive concrete acceptance criteria from them before coding.

### Step 2 — Audit before you build

If the task touches rendering, audio, or any system with an existing audit doc, read it first (`docs/presentation-audit.md` is the structural template — note it is a *pre-upgrade historical baseline*, so its "current state" facts are superseded). If no audit exists for the area you are changing and the change is non-trivial, write a short audit note in `docs/` describing current state + target state. This is not optional for refactor-grade work (presentation-upgrade.md §2 requires it).

### Step 3 — Implement

- Follow §5 (code conventions) and §7 (where things go).
- Keep the Simulation pure. Keep Presentation read-only. Keep data in `config/`.
- Run the quality gates (§9) continuously, not just at the end.

### Step 4 — Verify against acceptance criteria

Check each "Definition of Done" item from the relevant plan literally. The MVP DoD (plan/mvp.md §10) applies to *every* change:

> A feature is complete only if: works correctly · no TS errors · no runtime errors · maintains 60 FPS · integrates with existing systems · can be restarted safely · introduces no hidden state.

### Step 5 — Record (see §6)

Append a decision to `DECISIONS.md` for anything non-obvious, and append a line to the workspace memory log (§11).

### Step 6 — Hand off

Leave the tree green: `bun run check` must pass. Present the result per the agent loop's result-presentation rules.

### Step 7 — Iterative debug re-runs: failure subset only

> Origin: DECISIONS §120 (自毁基地 32 局取证). Tooling: `tools/diag/run-forensics.ts` + `tools/sim/` (§119).

When a forensics/collector script change invalidates previously collected data (off-by-one fix, new field, new 口径), **do NOT re-run the full stage×seed sweep**. Simulations are deterministic (§2.3), so re-running the same `(difficulty, stage, seed)` combos reproduces the same failures — re-run **only the previously identified failure subset** and re-collect with the updated forensics:

```
bun tools/diag/run-forensics.ts --from-json tmp/fx-120.json \
    --kinds base_destroyed,lives_exhausted,timeout \
    --selfkill --json tmp/fx-subset.json
```

- `--from-json <corpus>` derives the re-run set from the old corpus's failed runs (optionally filtered by `--kinds` failure causes and/or `--selfkill` = player self-inflicted base kills only). The report header labels the subset as such — never compare a subset corpus's absolute numbers against a full sweep.
- Cost: full sweep 35 stages × 120 seeds × 2 difficulties ≈ 8,400 runs (~4 min); the failure subset is typically <2,000 runs (seconds). §120 validated this on the 32 self-kill runs: 2.2s vs ~4 min, byte-identical failure list.
- **Full sweep is required only when the corpus itself changed** — stages, seeds, difficulty set, or `--set` params. If the corpus is unchanged, a subset re-run is the honest, sufficient validation.
- First sweep of a new experiment always collects with `--json` (per-run forensics persisted) so later iterations have a subset to draw from.

---

## 5. Code Conventions

### Language & tooling

- **TypeScript**, `strict: true`, `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch` (`tsconfig.json`). The compiler is a reviewer; do not silence it with `any` or `@ts-ignore`.
- **Bun** is the all-in-one tool: runtime, test runner (`bun test`), package manager.
- **Vite** is the dev server and build tool. Build target: `es2020`.
- **oxlint** + **oxfmt** for lint and format. Do not introduce ESLint/Prettier.

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

God AI v1 freeze gates (DECISIONS §272; pre-commit runs the first one):

```
bun run freeze:check # det 21-combo signature vs frozen golden (~100s) — red ⇒ new-era triple
bun run freeze:l2    # archived-candidate reachability audit over the same corpus (~100s)
```

`bun run check` is the definition of "green". Run it before declaring a task done.

> **Scoped vs full test runs.** `bun run test` invokes `tools/test-silent.ts`, a
> token-saving runner: it finds your changed/untracked files via git, maps each to
> the relevant `tests/*.test.ts` files by basename (incl. `base`/`base-*`/`*-base`
> patterns this repo uses), runs **only** those, and prints **only the failing-test
> logs** — a passing scoped run prints a single summary line. With `--strict` it
> skips entirely when nothing maps. The bare `bun test` command always runs the
> entire suite. The pre-commit hook (`tools/githook/pre-commit`, enabled via
> `bun run setup`) also uses the scoped `bun run test` for a fast gate; it falls
> back to the full suite when a change does not map to any test file, so it will
> not silently skip tests. For exhaustive runs use `bun test` or `bun run check`
> (both always run the full suite). `tools/runner.ts` holds the shared
> `spawnCapture`/`gitChangedFiles`/result-printing helpers used by that runner.
>
> **Heavy gate/integration tests are excluded by default.** The fast runner skips
> tests that run hundreds–thousands of full-game simulations (`godai-score-gate` —
> the 1050-sim worker-pool score gate, and `calibration`) because they take
> minutes and defeat the token/time-saving purpose. On a clean tree
> `bun run test` therefore runs the ~fast suite in a few seconds rather than ~1
> minute. To exercise them, pass `--heavy` (`bun run test --heavy`) or run the full
> suite with `bun test --parallel --timeout=50000`. Keep the `HEAVY_TESTS` list
> in `tools/test-silent.ts` in sync with measured wall-time — add any test file
> whose standalone run exceeds a few seconds. Note: because these gates are
> standalone files (not basename-matched to source changes), they are essentially
> only exercised by the full suite; if a God-AI change is landing, run
> `bun test --parallel --timeout=50000` before committing to validate the floors.
>
> **`bun test` MUST be run with `--parallel --timeout=50000`.** These are not
> optional:
> - **`--parallel` is mandatory.** `bun test` does **not** parallelize files by
>   default — without it the heavy God-AI gates (each spawns its own Bun web
>   workers internally, but per-FILE parallelism across the whole 127-file suite
>   is what keeps the full run near ~20s) degrade and the full suite slows down
>   markedly. `bun run check` already includes this flag.
> - **`--timeout=50000` is mandatory.** The God-AI gates run hundreds–thousands
>   of full-game simulations per file; bun's default per-test timeout (5s) kills
>   them. The gate files raise their own per-`it` timeout (e.g. 300000ms in the
>   score gate), but the runner default must also be lifted so the harness
>   itself doesn't abort.
> - Note: `test.concurrent` does **not** help here — it only does cooperative
>   (single-thread) scheduling and will NOT parallelize synchronous CPU-bound
>   simulation work across cores. Real parallelism requires `--parallel`.
> Running the bare `bun test` (no flags) is a regression and will be slow and/or
> timeout.

### NEVER start the dev server to validate changes

- The dev server (`bun run dev`) is for the **human to playtest** — an agent must **never** start it (or otherwise spin up a browser) to validate its own changes.
- Validation is the automated gates only. `bun run check` runs `tsc --noEmit --incremental && bun test` (typecheck + full test suite); `bun run build` adds `oxlint` first. For presentation/UI work that unit tests can't assert, rely on `tsc --noEmit`, `oxlint`, and a successful `vite build` — not a running server. (The pre-commit hook additionally runs `oxfmt` in place — it reformats files directly and re-stages them, rather than failing on `--check` — plus `oxlint`.)
- If a visual check is wanted, the human will open it themselves. Do not leave a dev server running as "proof" of work, and do not present a localhost URL as a validation step.

### NEVER launch NN training by running `python` directly

- Training **must** be started via the launch scripts: `nn-training/start-training.sh` for bash/Git-Bash (or `bash nn-training/start-training.sh` from the repo root), or `nn-training/start-training.ps1` for PowerShell (`powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1`). Both are fully equivalent and handle venv setup, single-instance locking, and signal cleanup.
- **Do not** run `python train_loop.py`, `python train_bc.py`, or any raw `python`/`python3` command to start training. Doing so bypasses the launch scripts' pre-flight checks and can spawn duplicate training processes that compete for the same lock file and weights.
- If training is already running, the launch script detects it and exits cleanly. If the lock is stale (crashed process), it is auto-cleaned. To force-restart after a crash, use `--force`: `./start-training.sh --force`.
- The lock file (`.train_loop.lock`) is managed exclusively by `train_loop.py`. The shell scripts never write to it — this eliminates the shell-PID / Python-PID mismatch that caused double-spawn on Windows.
- **torch 装在本机也不等于系统 python 能用它**：torch 只装在 `nn-training/.venv`（逐平台 venv），系统裸 `python` / `python3` **没有** torch（会报 `ModuleNotFoundError: torch`）。判定本机 torch 是否可用，**不要**跑 `python -c "import torch"`，用启动器幂等自检（bash 与 PowerShell 两个入口等价，任选其一）：
  - bash：`bash nn-training/start-training.sh --check` —— 校验 venv+torch 就绪并打印唯一 torch 解释器绝对路径，返回 0 即证明本机可用。
  - PowerShell：`powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Check` —— 同上。
  - 打印将执行的准确命令（含 venv 解释器），不实际运行、可直接复制：bash `... --echo --script <name>.py [args]`；PowerShell `... -Echo -Script <name>.py [args]`。
- 启动器不只是 `train_loop.py`：`--script <name>.py [args]`（PowerShell 用 `-Script`）可经过同一 venv 跑任意 `nn-training/*.py`（`train_bc.py --arch student`、`train_rl.py`、`smoke_test.py`、`eval_bridge.py` 等），从而让所有 torch 训练都走同一入口，避免 agent 绕过入口裸跑系统 python 撞"找不到 torch"。

### NN training progress must be recorded in `docs/nn.progress.md`

- Every architecture change, eval result, and lesson learned from NN training goes into `docs/nn.progress.md`.
- New entries are appended at the top (reverse chronological) under numbered sections (§1, §2, ...).
- Each section records: what changed, eval results (val_loss + sim win rate), root cause analysis, and concrete lessons.
- This is the single source of truth for NN training history — not scattered in chat or commit messages.
- When making architectural changes (model.py, infer.ts, obs-encoder.ts), check this file first for prior decisions.

### NEVER add an untracked `*.md` file to git tracking

- Markdown that is **already in git tracking** (e.g. `AGENTS.md`, `DECISIONS.md`, `MANIFEST.md`, `README.md`, any `*.md` already committed) is fair game: an agent may edit, stage, and commit it like any other source file.
- Markdown that is **not yet tracked** (an untracked `*.md` the agent did not find already staged) must **never** be added to tracking by the agent. Do not `git add` an untracked `*.md`, and do not use a blanket `git add -A` / `git add .` that would sweep one in.
- When committing, prefer explicit paths (`git add <specific files>`) over `git add -A`. Before committing, confirm `git status` shows no untracked `*.md` being staged; if one appears, unstage it (`git restore --staged <file>` / `git reset <file>`) and leave it for the human.
- If the human explicitly asks you to create or add a specific markdown file, that is allowed — but never auto-discover and track markdown the human did not request.

### Style

- No classes where a function suffices; no singletons for gameplay state.
- Prefer pure functions in `utils/`. Simulation methods may mutate the World they own — that is their job.
- Use the seeded `RNG` (`world.rng`) for any randomness that affects gameplay. `Math.random()` is only acceptable in pure presentation code (particles, visual jitter) that never feeds back into the World.
- Keep the bundle small. New dependency ⇒ justify in `DECISIONS.md`. The project is deliberately tiny (MANIFEST §14).

### File placement

- New gameplay system → `src/game/` and called from `Simulation.updatePlaying()` (or the relevant state branch).
- New visual system → `src/presentation/`. It must not import from `src/game/` except to read types.
- New content (tank/stage/theme/difficulty) → `src/config/`. Engine code must not change.
- New sprite → `src/assets/sprites/*.svg` + register in `src/assets/sprites/index.ts` (`SPRITE_URLS`). 96×96 viewBox, faces UP for tanks.
- New test → `tests/`, mirroring the concern (e.g. `tests/stages.test.ts` mirrors `src/config/stages.ts`).

---

## 6. When in Doubt — Derive From the MANIFEST, Record, Then Execute

This is the core autonomy contract. You are expected to make judgment calls instead of stalling. The process is:

### 6.1 Identify the doubt

A doubt is any of:

- The plan is silent on a design point.
- Two reasonable implementations exist and the plan does not pick one.
- The plan appears to conflict with the MANIFEST or an existing decision.
- A visual/tunable value (color, timing, count, threshold) is unspecified.

### 6.2 Derive the optimal solution

Resolve the doubt by consulting, in priority order:

1. **MANIFEST** — the creed. Does one of the 14 sections answer this? (§10 "Simple Beats Clever" and §13 "Three Gates" settle most doubts.)
2. **DECISIONS.md** — has this already been decided? Look for the closest precedent.
3. **Existing code** — what does the codebase already do in analogous situations? Consistency is a form of correctness.
4. **The classic original** — for gameplay feel, Battle City (Famicom) behavior is the reference. Authenticity beats novelty unless the MANIFEST says otherwise.
5. **The plan's stated rationale** — if the plan gives a "why", honor it even when the "what" is ambiguous.

### 6.3 Record the decision

**Before** executing, record the decision. Where it goes depends on the topic:

- **Foundational / architecture / gameplay-mechanic decisions** (the §1–§10 lineage) → append a full entry to `DECISIONS.md` using the format below.
- **God AI / performance / render *tuning* experiments** → the verbose detail (A/B tables, forensics, per-seed breakdowns) goes to the matching log: `docs/god-ai-tuning.progress.md`, `docs/perf-optimization.progress.md`, or `docs/render-optimization.progress.md`. In `DECISIONS.md`, add only a **compressed index row** — the `## N. Title (STATUS)` header plus one pointer line `> 全文 → docs/<file>.progress.md`. This keeps `DECISIONS.md` a scannable index, not a 300 KB wall.

Full-entry format (for `DECISIONS.md` foundational decisions):

```markdown
## N. <Short Title>

**Decision:** <What you chose, concretely.>

**Rationale:**
- <Why, anchored in MANIFEST / precedent / the plan's intent.>
- <What you rejected and why.>

**Implications:** <Optional — what this enables or forecloses.>
```

Keep numbering sequential. If your decision revises an earlier one, mark the old one `_(superseded by §N)_` rather than deleting it — history matters.

### 6.3b God-AI tuning evaluation framework (Phase III, 2026-08-12)

> **⛔ 状态注记（2026-08-26，DECISIONS §272）：player 侧 God AI v1 已封版冻结。**
> Phase III 已收官——本节框架保留供重启参考。任何 God-AI 行为改动 = 新纪元，
> 必须走「三件套」：新 DECISIONS 条目 + 重跑 60-seed 三难度基线 + 更新冻结签名 golden
> （`tools/det-golden.v1.sha256`，`bun run freeze:check` 会红）。重启协议：
> `plan/God-AI-Organization.md` §8（执行后移 docs/god-ai-organization.md）；
> 封盘方向清单：该 plan §1.1 + `plan/refactor.trae.md` §0.5。

God-AI tuning has entered **Phase III: Hard-focused behavior tuning**. When judging a God-AI change, apply:

- **Drive on `hard` difficulty.** Mine and fix *unreasonable behavior patterns* there (smaller noise than classic, closer to the "reasonable behavior" boundary than chaos).
- **Win rate is the *primary* metric, not the *overwhelming* one.** Evaluate alongside the `godai-score` v7 dimensions below — kills (`progress`) / lives-remaining (`lives`) / fire-hit-rate (`accuracy`) / clear-time (`clearSpeed`) and the rest. Prioritize "high win-rate but anomalous dimension" combos as the fix targets. **hits-taken is intentionally excluded** (no telemetry field, per decision).
- **classic / chaos pass rates are reference only** — keep no *large* regression; do not optimize for them. chaos will get stronger enemy AI later, so its expected pass-rate drop is *not* a regression.
- **Scoring standard (`tools/eval/godai-score.ts`, v7 band):** 11 dimensions, two weight bands (sum = 1.0 each).
  - *clears band (pass):* `lives` 0.34 · `clearSpeed` 0.22 · `baseIntegrity` 0.16 · `baseSafety` 0.10 · `loot` 0.08 · `growth` 0.06 · `accuracy` 0.04
  - *losses band (fail):* `progress`(kills) 0.477 · `lives` 0.256 · `baseIntegrity` 0.17 · `baseSafety` 0.044 · `tempo` 0.026 · `openingTempo` 0.018 · `loot` 0.009
  - Win-rate is enforced **structurally, not by weight**: any clear (score ≥ 0.70) > any loss (score ≤ 0.40) — a 0.30 gap. In the clears band `progress`/`tempo`/`openingTempo` weight **0** (a clear is a clear); in the losses band `clearSpeed`/`accuracy`/`growth` weight 0 (clears-only metrics).
  - Other dimensions (not in the two bands above): `mobility` (visited-cell anti-oscillation).
  - **Phase III baseline (eval-suite v6, 35×60 seeds; hard = primary, classic/chaos = reference):**
    | 难度 | SUITE (lcb±se) | 平均胜率 | fitness v6 |
    |---|---|---|---|
    | **hard (主)** | **0.5132** (0.5068±0.0064) | **73%** | 506.8 |
    | classic (参) | 0.7259 (0.7211±0.0047) | 90% | 721.1 |
    | chaos (参) | 0.4926 (0.4859±0.0067) | 69% | 485.9 |
  - On **hard**, watch these dimension readings as the behavior signal: `clearSpeed` 0.146 (slow/拖沓) and `baseIntegrity` in losses 0.102 (most failures = lost base). A high win-rate paired with these *degrading* is exactly the pattern Phase III targets. Full per-dimension table: `docs/god-ai-tuning.progress.md` §0.C.5.
- **Discipline unchanged:** per-seed tick-diff (see `docs/god-ai-tuning.progress.md` §I.5.1) still locates the first diverging tick; decisive conclusions still need ≥60 seeds; the three regression lines (no SP leak / no frozen-failure-seed as hard gate / byte-identical determinism) still hold. Full framework: `docs/god-ai-tuning.progress.md` §0.C.

### 6.4 Execute

Now implement the recorded decision. If mid-implementation you discover the decision was wrong, update the `DECISIONS.md` entry (with a dated note) and proceed. Do not silently deviate.

### 6.5 What NOT to decide alone

Escalate to the human (ask, do not guess) only when:

- The change would break the **One Author** invariant (§2.1) in a way the MANIFEST forbids.
- The change requires a **new runtime dependency** or a **new build tool**.
- The change alters the **public game feel** in a way the plan did not contemplate (e.g., changing tank speed defaults, adding a new game mode).
- You are about to **delete or rewrite** a system that has no test coverage and no audit doc.

Everything else is yours to decide, record, and execute.

---

## 7. Bug-Fix Workflow — Reproduce With a Test Before You Fix

This is mandatory. No exceptions.

> A bug is not fixed until a failing test proves it existed and then passes after your change.

### 7.1 Reproduce first

Before touching production code, write a failing test in `tests/` that reproduces the bug.

- The test must **fail** on the unmodified codebase. Run `bun test` and confirm the failure. If it passes, you have not reproduced the bug — keep going.
- The test must be **minimal**: isolate the failing behavior. If the bug is in `Simulation`, drive the `World` + `Simulation` directly without the render loop. If it is in `TileMap`, test `TileMap` in isolation. If it is in a config decoder (like `stages.ts`), mirror the codec in the test the way `tests/stages.test.ts` does — an independent re-implementation is the strongest proof.
- The test must be **deterministic**. Seed `world.rng` with a fixed value (`new RNG(12345)`) if randomness is involved. Never write a bug-repro test that depends on `Math.random()` or wall-clock time.

### 7.2 Then fix

Make the minimal change that turns the test green. Do not refactor opportunistically during a bug fix — that is how regressions are born. If you spot a cleanup opportunity, note it as a follow-up task; do not bundle it.

### 7.3 Then verify

- `bun test` — your new test passes.
- `bun run check` — the full gate is green (no type/lint/format regressions).
- Manually confirm the fix at `bun run dev` if the bug had a visible component.

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

After substantive work, append a brief note to the workspace daily log:

- `/Users/hj/dev/github/battle/.workbuddy/memory/YYYY-MM-DD.md` — append-only daily log (create if missing).
- `/Users/hj/dev/github/battle/.workbuddy/memory/MEMORY.md` — curated long-term project notes, for durable conventions/preferences.

Record: what was built/changed, the decision rationale (link to the `DECISIONS.md` entry), any pitfall discovered, and the next sensible step. Do not record transient search results or tool errors.

This is supplemental — it never replaces the actual deliverable or your reply to the user.

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

> The God-AI tuning loop runs thousands of headless simulations. Any per-tick allocation or redundant scan is amplified ×millions. The rules below were discovered via `bun --cpu-prof` profiling + determinism-signature verification (see `docs/perf-optimization.progress.md`). Violating them in hot paths is a bug, even if the tests pass.

### 14.1 No array allocations in per-tick functions

Functions called once per enemy per tick (or once per tick for the God AI) must not allocate arrays. This includes:

- **Constant arrays**: `const dirs = ['up', 'down', 'left', 'right']` inside `perceive()` allocated a 4-element array ~550K times per 30-game batch. **Fix**: hoist to module scope (`const PERCEIVE_DIRS = [...]`).
- **Filtered arrays**: `w.tanks.filter(t => t.alive && t.spawnTimer <= 0)` builds a new array every call. **Fix**: iterate the source array directly with an inline `if` guard (the guard is the same filter, just without the allocation).
- **Result arrays**: `const open: Direction[] = []` followed by `open.push(d)` in a 2-iteration loop. **Fix**: replace with local boolean variables (`let safeA = false; let safeB = false`).

> V8 does not eliminate short-lived arrays. Each `[]` + `push` creates a heap object that survives until the next minor GC. In a tight 60 FPS / 0.02ms-per-tick budget, GC pauses eat the frame.

### 14.2 No object allocations for return values in hot paths

Functions called per-tick that return an object (`return { enemy, wall, steel, baseWall, enemyDist }`) allocate a heap object each call. If the caller consumes the result immediately and never stores the reference:

- **Fix**: use a reusable result object stored on the owning class (`self._scanResult`), or return a primitive (string/number) when only one field is used.

Example: `scanAhead()` in `perception.ts` was changed to return a `ScanHit` string instead of `{ hit, dist }` — the caller (`analyze`) only checks the hit category, never the distance.

### 14.3 Guard `.sort()` against empty arrays

`threats.sort((a, b) => a.distance - b.distance)` on a 0-element array still pays the sort-setup cost. **Fix**: `if (threats.length > 1) threats.sort(...)`.

### 14.4 V8 string interning is fast — don't fight it

Terrain types are `TerrainType` strings (`'brick'`, `'steel'`, etc.). V8 interns string literals, so `type === 'brick'` is a pointer comparison — **very fast**. Attempting to "optimize" by converting to numeric codes + a `Uint8Array` lookup table + `TERRAIN_CODES.xxx` property lookups was measured as a **28% regression** because:

1. `TERRAIN_CODES` is an exported mutable object — V8 cannot constant-fold `.brick` / `.steel` property lookups.
2. The `TERRAIN_NAMES[code]` reverse-lookup in `get()` adds overhead that outweighs the flat-array cache locality benefit.
3. `getRaw()` method-call overhead + bounds checking is not cheaper than the original `get()` + inlined `blocksTank()`.

**Rule**: keep terrain as strings in the TileMap. If you want numeric encoding for a specific hot path, use inline numeric literals (`=== 1`, `=== 2`) — never `TERRAIN_CODES.xxx` — and benchmark against the string baseline with the determinism-signature gate.

### 14.5 Avoid `.filter()` + `.sort()` chains in per-tick paths

`evaluateGoals()` in `TacticalIntelligence` builds a 7-element `scores` array and sorts it every tactical-think cycle. While this is throttled (~5s interval, not per-tick), the pattern of `array.filter().sort()` in any per-tick path is a red flag. Prefer inline scoring with a running best.

### 14.6 Reuse `allTanks` buffer — don't rebuild

`World.allTanks` is a getter that rebuilds `_allTanksBuf` on each call. If multiple consumers in the same tick need the tank list, call the getter once and pass the reference. The `perceive()` function does this: callers (TacticalIntelligence.update / updateTank) pass the already-cached `allTanks` buffer in via the optional `all?` parameter, then perceive passes it to `canStep()` 4× instead of having `canStep` call the getter 4×.
