import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { allEnemiesCleared } from '../../src/game/SimulationEffects'
import { GodAIInput, type GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { NNInput } from '../../src/nn/policy-input'
import { GoalExecutor } from '../../src/nn/goal-executor'
import { IntentPlayer } from '../../src/nn/intent-player'
import { IntentExecutor } from '../../src/nn/intent-executor'
import { IntentOracleProbe } from '../../src/nn/intent-oracle-probe'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { CELL, GRID, BASE_POS, ENEMIES_PER_STAGE, START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { readFileSync } from 'fs'
import { computePlayer2SpawnCol } from '../../src/utils/helpers'
import { paramsHash } from '../lib/stage-spec'
import { InputRecorder } from '../../src/replay/InputRecorder'
import { enemyCanShootBase, enemyCanBreachRing } from '../../src/ai/god/SmartThreatModel'
import type { Direction } from '../../src/constants'
import type { StageData, GameEvent, TankKind, IntelligenceLevel, Tank } from '../../src/types'

// ============================================================
// Types
// ============================================================

export type SimOutcome = 'stage_clear' | 'gameover' | 'max_ticks'

/**
 * Failure taxonomy (plan/God-AI-Tuning §2 Phase 0).
 * Answer "why did the AI lose?" without re-reading the event stream.
 * Pure tools-layer addition — does not touch src/.
 */
export interface FailureTaxonomy {
  /** What ended the run. */
  cause: 'base_destroyed' | 'lives_exhausted' | 'timeout'
  /** Tick at which the run ended. */
  tick: number
  /** Who destroyed the base (kind of the bullet's owner). undefined for non-base deaths. */
  killerKind?: TankKind
  /** Player's Manhattan distance to base (in cells) at the moment of death/base-loss. */
  playerDistToBase?: number
  /** Tick of the first player kill (output efficiency indicator). undefined if no kills. */
  firstKillTick?: number
}

/**
 * One player-death event (M0 death attribution, plan/God-AI-Redesign-v2 §6).
 * Collected when `telemetry: true` — read-only observation of the World,
 * never feeds back into gameplay or the RNG stream.
 */
export interface PlayerDeath {
  /** Tick at which the player was destroyed. */
  tick: number
  /** Player tank center (px) at death. */
  x: number
  y: number
  /** Player's Manhattan distance to the base (cells) at death. */
  distToBase: number
  /** Kind of the tank that fired the killing bullet. */
  killerKind?: TankKind
  /** AI tier of the killer tank (aiState.level), if enemy. */
  killerTier?: IntelligenceLevel
  /** The think() branch the God AI was in on the death tick. */
  branch: string
  /** Player star level at death. */
  playerLevel: number
  /** Player HP at the death tick (pool model — how many hits it absorbed). */
  hp: number
  /** Live, fully-spawned enemy count at the death tick (surround context). */
  liveEnemies: number
}

/**
 * Per-run telemetry for the v6 evaluation model
 * (plan/God-AI-Evaluation-Redesign.md §3).
 *
 * Opt-in via `RunOptions.telemetry`. When the flag is off, none of the
 * collection code runs and the simulation path is byte-identical to before —
 * `validate-p4` / the regression gate / the optimizer's v5 fitness are
 * unaffected.
 *
 * Everything here is a read-only observation of the World (AGENTS §2.1): the
 * collector never mutates state and never consumes `world.rng`, so a run with
 * telemetry on produces the same outcome as the same run with it off.
 */
export interface RunTelemetry {
  /** Enemies the stage requires (stage.enemyCount ?? ENEMIES_PER_STAGE). */
  enemyTotal: number
  /** Lives the player started with (difficulty.startLives). */
  startLives: number
  /** Times the player tank was destroyed. */
  playerDeaths: number
  /** Bullets fired by the player. */
  playerShots: number
  /** Power-ups that appeared on the field during the run. */
  powerUpsSpawned: number
  /** Power-ups the player picked up. */
  powerUpsCollected: number
  /** Star power-ups specifically (the firepower-growth currency). */
  starsCollected: number
  /** Player star level at the end of the run. */
  finalPlayerLevel: number
  /** Base protection-ring cells still solid (brick/steel) at the end. */
  baseWallIntact: number
  /** Base protection-ring cells solid at stage load — the denominator. */
  baseWallTotal: number
  /**
   * Mean base pressure over the run, in [0,1]. Each sample takes the closest
   * alive enemy and maps its Manhattan cell distance to the base through
   * `clamp(1 - dist / BASE_PRESSURE_RADIUS)`. 0 = no enemy ever came near the
   * base; 1 = an enemy sat on the base the whole run.
   *
   * This is the dense proxy for `base_destroyed` (a rare binary event): it
   * gives the optimizer gradient in the "not losing yet but dangerous" region.
   */
  basePressureMean: number
  /** Number of pressure samples taken (for reproducibility auditing). */
  basePressureSamples: number
  /** Distinct grid cells the player visited — the anti-oscillation signal. */
  cellsVisited: number
  /** Per-death events (empty when the player never died). */
  deaths: PlayerDeath[]
}

export interface SimResult {
  /** What ended the simulation. */
  outcome: SimOutcome
  /**
   * 全灭（歼灭率口径）：敌人队列已空 + 场上无存活非 extra 敌人。
   * 与 `outcome === 'stage_clear'` **不等价** —— 后者还要求地上没有存活道具，
   * 否则要等 BONUS TIME 窗口（≈600 tick）结束；若 max-ticks 先到，`outcome` 记为
   * `max_ticks` 但敌人其实已全灭。方案 §2.1 的「全歼率」门以此字段判定。
   */
  cleared: boolean
  /** Total ticks simulated. */
  ticks: number
  /** Wall-clock simulation time in ms (for perf reporting). */
  wallMs: number
  /** Final game state. */
  finalState: {
    score: number
    lives: number
    killCount: number
    playTimeMs: number
    stageIndex: number
    baseAlive: boolean
    playerAlive: boolean
    playerLevel: number
    /** Lie-Back-Win-Mode: God AI score (coop only). */
    score2?: number
    /** Lie-Back-Win-Mode: God AI lives (coop only). */
    lives2?: number
    /** Lie-Back-Win-Mode: God AI alive (coop only). */
    player2Alive?: boolean
  }
  /** All events collected during the run. */
  events: GameEvent[]
  /** Per-frame metric samples. */
  metrics: FrameMetrics[]
  /** The seed used. */
  seed: number
  /** The difficulty key. */
  difficulty: string
  /** Tick of the first player kill (output efficiency indicator). undefined if no kills. */
  firstKillTick?: number
  /** Failure attribution (plan/God-AI-Tuning §2). undefined on stage_clear. */
  failure?: FailureTaxonomy
  /** paramsHash of the God AI params actually used (FNV-1a over the stable
   *  encoding, tools/lib/stage-spec.ts). Live-probe identity tag: proves a
   *  profile/override reached the Simulation and wasn't silently defaulted. */
  paramsHash: string
  /** v6 evaluation telemetry (only when `telemetry: true`). */
  telemetry?: RunTelemetry
  /** Suicide-trade commit ticks (only when `commitCounts: true`). */
  suicideReturnCommits?: number
  /** §121 self-fire base-guard block ticks (only when `commitCounts: true`).
   *  A/B trigger-rate proxy: 0 = the arm never suppressed a base-line shot. */
  selfFireGuardBlocks?: number
  /** Full branch-counter totals snapshot (only when `branchTotals: true`).
   *  §272 L2 archived-candidate reachability audit: every key must be 0 for
   *  candidates whose gate is OFF (see tools/diag/archived-reach-audit.ts). */
  branchTotals?: Record<string, number>
  /** Per-run forensics (only when `forensics: true`). */
  forensics?: RunForensics
  /** Event-driven threat ledger (only when `threatLedger: true`). */
  ledger?: ThreatLedgerRun
  /** intent-exec 策略每 replan 的原始 argmax 意图 + 边际（only when `recordIntentTrace`）。
   *  intent 用作 rollout 意图分布探针（意图分布熵 / HUNT 占比）。 */
  replanIntentTrace?: Array<{ tick: number; intent: number; margin: number }>
  /** intent-exec 策略实际承诺（切换生效）的意图序列（only when `recordIntentTrace`）。 */
  committedIntentTrace?: number[]
  /** M4 star census (only when `powerupCensus: true`). */
  powerupCensus?: {
    spawned: number
    picked: number
    /** Per-star lifecycle: spawn tick, min player dist (px), despawn tick
     *  (-1 = still alive at run end), picked. */
    stars: Array<{
      spawnTick: number
      picked: boolean
      minDist: number
      despawnTick: number
    }>
  }
  /** Replay data (only when record=true). */
  replay?: {
    initialSnapshot: import('../../src/snapshot/types').WorldSnapshot
    frames: Uint8Array
    tickCount: number
  }
}

export interface FrameMetrics {
  tick: number
  bullets: number
  enemyCount: number
  playerX: number
  playerY: number
  enemyPositions: Array<{ x: number; y: number }>
  /** Count of enemy bullets on a collision course with the player. */
  incomingThreats: number
}

// ============================================================
// Run forensics (DECISIONS §119) — per-run structured autopsy data
// ============================================================

/**
 * One tick of the player's action log: the move/fire the God AI issued and
 * the decision-chain rule (candidate branch) that took effect, plus the world
 * state that tick. Read-only observation of the World + the AI's cached
 * decision state (`input._lastBranch` etc.) — never feeds back.
 */
export interface ForensicsAction {
  tick: number
  /** The decision-chain branch that took effect (生效规则): e.g. 't2a', 'dodge', 'navigate'. */
  branch: string
  moveDir: Direction | null
  fire: boolean
  playerX: number
  playerY: number
  playerHp: number
  playerLives: number
  playerDistToBase: number
  baseHp: number
}

/** A live enemy (or its in-flight bullet) at the terminal tick. */
export interface ForensicsTank {
  kind: TankKind
  hp: number
  maxHp: number
  /** AI tier (aiState.level) — only set for enemies, 'none' if unknown. */
  level: string
  x: number
  y: number
  /** Manhattan distance (cells) to the player center; -1 if no player. */
  distToPlayer: number
  /** Manhattan distance (cells) to the base center. */
  distToBase: number
}

export interface ForensicsBullet {
  x: number
  y: number
  dir: Direction
  damage: number
  distToPlayer: number
  distToBase: number
  /** Ticks to reach the player if aligned + approaching, else -1. */
  etaToPlayer: number
  /** Hits this bullet would need to kill the player (ceil(hp / damage)). */
  hitsToDiePlayer: number
  /** Hits this bullet would need to kill the base (ceil(baseHp / damage)). */
  hitsToDieBase: number
}

/** Full state at the terminal tick (gameover / stage-clear / timeout). */
export interface ForensicsSnapshot {
  tick: number
  playerAlive: boolean
  playerLives: number
  playerHp: number
  /** ceil(playerHp / 100) — hits the player can still take vs basic firepower. */
  playerHitsToDie: number
  playerDistToBase: number
  playerLevel: number
  baseAlive: boolean
  baseHp: number
  baseMaxHp: number
  /** ceil(baseHp / 100) — hits the base can still take vs basic firepower. */
  baseHitsToDie: number
  /** Base protection-ring cells still solid (brick/steel). */
  baseWallIntact: number
  /** Live, fully-spawned enemies, each with type/HP/distances. */
  enemies: ForensicsTank[]
  /** Every in-flight enemy bullet, with position/direction/distances. */
  enemyBullets: ForensicsBullet[]
}

// ============================================================
// Threat ledger (plan/God-AI-Hard-Breakthrough-Implementation.md §4.1)
// ============================================================

/** One live enemy's diagnostic snapshot inside a ledger sample. */
export interface ThreatLedgerEnemy {
  id: number
  kind: TankKind
  hp: number
  cell: { col: number; row: number }
  dir: Direction
  /** Enemy currently has a CLEAR shot at the base (canShootBaseFrom). */
  canShootBase: boolean
  /** Enemy currently has a clear shot at an intact ring brick (canBreachRingFrom). */
  canBreachRing: boolean
  /** Conservative movement ETA (ticks) to reach the base ring; 0 when already
   *  aligned on a shoot line (csb/cbr). Geometric lower bound — no turn cost,
   *  no path A* (M0; the M1 ThreatBudget replaces this with legal-turn-aware
   *  ETAs). */
  enemyToRingEta: number
  /** Conservative player ETA (ticks) to reach a kill position for this enemy
   *  (Manhattan cell distance / player speed). M0 geometric estimate. */
  playerKillEta: number
  /** Conservative ticks until this enemy's bullet would damage the base/ring
   *  if it fired now (flight time along its aligned line; 0 when csb — the
   *  danger is immediate). */
  shootEta: number
}

/** One event-driven ledger sample (plan §4.1). Collected only when the run
 *  opts in via `RunOptions.threatLedger`; when off the run is byte-identical.
 *  Everything here is a read-only observation of the World + the AI's cached
 *  decision state — never feeds back, never consumes world.rng. */
export interface ThreatLedgerSample {
  tick: number
  baseHp: number
  intactRing: number
  playerCell: { col: number; row: number }
  playerDir: Direction
  playerLives: number
  /** The decision-chain branch that took effect this tick. */
  branch: string
  /** Player fire cooldown mirror (same semantics as thinkImpl's gate). */
  onCooldown: boolean
  liveEnemies: number
  /** The AI's own base-threat predicate (isBaseUnderThreat) — the "detection"
   *  signal: what the player could know. */
  baseThreatNow: boolean
  /** Min over live enemies of shootEta — the danger deadline (ticks). */
  nearestThreatEta: number
  /** Player ETA to the best intercept (min playerKillEta over threat enemies). */
  playerEtaToBestIntercept: number
  /** nearestThreatEta − playerEtaToBestIntercept (positive = can make it). */
  threatSlack: number
  /** Branch name when this tick's commit produced NO movement and NO fire —
   *  a standing no-output commit. null otherwise. */
  noOpReason: string | null
  /** Per-enemy diagnostics (live, fully-spawned enemies only). */
  enemies: ThreatLedgerEnemy[]
}

/** Per-run ledger payload (only when `threatLedger: true`). */
export interface ThreatLedgerRun {
  outcome: SimOutcome
  failureCause?: 'base_destroyed' | 'lives_exhausted' | 'timeout'
  tick: number
  baseMaxHp: number
  samples: ThreatLedgerSample[]
}

/** One historical player-side event (death / kill / power-up pickup / shot). */
export interface ForensicsEventLog {
  tick: number
  type: 'death' | 'kill' | 'pickup' | 'shot'
  /** Event position (px, tank/bullet center). */
  x: number
  y: number
  /** Killer kind (death) / killed kind (kill) / power-up type (pickup)
   *  / decision-chain branch that issued the shot (shot). */
  detail: string
  /** Player facing at the shot tick (shot only). */
  dir?: Direction
  /** True when the shot's forward ray crosses the base 2×2 rectangle — the
   *  self-inflicted-base-kill candidate flag (shot only). */
  towardBase?: boolean
}

/**
 * Per-run forensics payload (DECISIONS §119). Collected only when the run
 * opted in via `RunOptions.forensics`; when off, the run is byte-identical.
 */
export interface RunForensics {
  outcome: SimOutcome
  failureCause?: 'base_destroyed' | 'lives_exhausted' | 'timeout'
  /** World state at the terminal tick. */
  terminal: ForensicsSnapshot
  /** The last ≤10 ticks of player actions + the rules that took effect. */
  lastActions: ForensicsAction[]
  /** Death / kill / pickup history (tick + position). Single-player oriented:
   *  in coop, P2 pickups also emit `powerup_collected` (by: 'player') and would
   *  be attributed to P1 — do not rely on this for coop sweeps. */
  events: ForensicsEventLog[]
  /** Lifetime power-up pickup totals per type — consumables (bomb/freeze/
   *  fence) are consumed on use, only super power-ups (guard/decoy/rewind/
   *  mine/sacrifice) actually accumulate; this is a pickup census, not the
   *  literal remaining stock. */
  inventory: Record<string, number>
  kills: number
  playerDeaths: number
}

// ============================================================
// SimulationRunner
// ============================================================

/** Maximum ticks before forcing a stop (10 minutes at 60fps = 36000). */
export const MAX_TICKS = 36000

export interface RunOptions {
  seed: number
  stage: StageData
  difficulty: string
  /**
   * 0-based index of the stage being simulated. Recorded into the World via
   * loadStageData so the resulting snapshot (and any replay) carries the real
   * stage number. Without this the World defaulted to stage 0, which makes
   * imported replays display "STAGE 01" even though they were recorded on a
   * later stage (bug: import 的 S32 replay 播放时显示 STAGE 01).
   */
  stageIndex?: number
  /** God AI parameters (defaults to DEFAULT_GOD_AI_PARAMS). */
  godAIParams?: GodAIParams
  /** Player policy for the headless run: 'god' (default), 'nn', 'intent',
   *  'intent-exec', 'intent-oracle' or 'goal' (T8.5 goal-space 执行器). */
  policy?: 'god' | 'nn' | 'intent' | 'intent-exec' | 'intent-oracle' | 'goal' | 'goal-god'
  /** Weights directory for the 'nn' policy (auto-discovers latest). */
  nnWeightsDir?: string
  /** Weights JSON file for the 'intent' policy (M4 stub / M5 trained). */
  intentWeightsDir?: string
  /** Weights JSON file for the 'goal' policy (T8.5). */
  goalWeightsDir?: string
  /** Goal 承诺期 T ticks（0/缺省 = 执行器默认 240）。 */
  promiseTicks?: number
  /** M7① cadence 扫描：意图 replan 周期覆盖（0/缺省 = 策略默认）。 */
  replanEvery?: number
  /** M7① risk-gated（Q7）：危险窗口 cadence 动态压缩。 */
  riskGated?: boolean
  baseCadence?: number
  dangerCadence?: number
  /** 返回 intent-exec 策略每 replan 的原始 argmax 意图 trace（只读，零 RNG，默认关）。
   *  intent 用作 rollout 意图分布探针（意图分布熵 / HUNT 占比）。 */
  recordIntentTrace?: boolean
  /** 返回 goal 策略的重选 trace（T0-goal 遥测；只读，默认关）。 */
  recordGoalTrace?: boolean
  /** Max ticks before stopping (default: MAX_TICKS). */
  maxTicks?: number
  /** Sample metrics every N ticks (default: 1 = every frame). */
  sampleInterval?: number
  /**
   * Collect the per-frame `metrics` array (one FrameMetrics object per sample,
   * each allocating an enemyPositions array — the default `sampleInterval: 1`
   * therefore allocates every tick). Default true. Set false when the caller
   * never reads `result.metrics` (e.g. the worker-pool score gate): the
   * sampling is pure read-only observation, so the run outcome and telemetry
   * are byte-identical either way (verified — score-gate scores identical).
   */
  collectMetrics?: boolean
  /**
   * Retain the full event log in `result.events`. Default true. Set false when
   * the caller only consumes aggregate counters (telemetry / failure / score):
   * the per-tick event processing still runs, only the retention-array push is
   * skipped, so the run outcome is byte-identical (the failure killerKind is
   * still captured via the in-loop base_destroyed tracker).
   */
  collectEvents?: boolean
  /** Record input frames for replay playback (plan/God-AI-Replay-Visualization §4.1). */
  record?: boolean
  /** Lie-Back-Win-Mode: enable coop (God AI controls player2, human idle). */
  coop?: boolean
  /** 督战双玩家: supervise mode with a SECOND God AI driving player2 (mirrors
   *  Game.requestSpectateToggle(true)). Distinct from `coop`, which is the
   *  Lie-Back-Win (human P1 + God AI P2) mode. */
  spectateDual?: boolean
  /**
   * 一命覆写（plan/dodge-item-curriculum.md §1b F1）：强制玩家命数替代
   * difficulty.startLives。S-Dodge 锚定探针传 1。缺省 = difficulty.startLives。
   */
  livesOverride?: number
  /**
   * Collect v6 evaluation telemetry (plan/God-AI-Evaluation-Redesign.md §3).
   * Default false — when off, the run path is byte-identical to before.
   */
  telemetry?: boolean
  /**
   * Return `input.branchCounts.suicideReturn` — the total suicide-trade
   * commit ticks for the run (§116/§117 A/B trigger-rate probe). Read-only
   * observation, no RNG, no gameplay effect. Default false (off → no extra
   * work, byte-identical run).
   */
  commitCounts?: boolean
  /** Snapshot the full `input.branchCounts` object into the result (default
   *  false). Read-only observation — never touches the World or RNG streams,
   *  so outcomes stay byte-identical. Consumers: §272 L2 reachability audit. */
  branchTotals?: boolean
  /**
   * Collect per-run forensics (DECISIONS §119): terminal snapshot (player /
   * base / per-enemy / per-bullet), last-10-ticks action+rule log, and the
   * death/kill/pickup event history. Read-only observation — the run outcome
   * is byte-identical whether on or off. Default false.
   *
   * NOTE: opt-in tooling — the action trace allocates one object per tick, so
   * never enable this inside the optimizer's fitness loop (use the
   * run-forensics CLI instead, which is a separate sweep).
   */
  forensics?: boolean
  /**
   * Collect the event-driven threat ledger (plan/God-AI-Hard-Breakthrough §4.1):
   * samples pushed when the base HP / ring / threat predicate / branch /
   * player cell / slack sign changes. Read-only observation — the run outcome
   * is byte-identical whether on or off. Default false. Opt-in tooling: the
   * per-sample enemy scan allocates, so never enable inside the optimizer.
   */
  threatLedger?: boolean
  /**
   * Collect a star power-up census (M4 safe-powerup diagnosis): for each
   * 'star' power-up that spawns during the run, record spawn tick, whether
   * the player picked it up, the player's minimum distance (px) to it while
   * it was alive, and its despawn tick. Read-only observation — outcome
   * byte-identical whether on or off. Default false; per-tick distance scan
   * allocates, so never enable inside the optimizer.
   */
  powerupCensus?: boolean
}

// ============================================================
// Telemetry constants (plan/God-AI-Evaluation-Redesign.md §3.4)
// ============================================================

/** Base-pressure sampling cadence in ticks (10 Hz at 60 fps). */
const TELEMETRY_SAMPLE_TICKS = 6
/** Enemies closer than this (Manhattan cells) contribute base pressure. */
const BASE_PRESSURE_RADIUS = 12

/**
 * The 8 cells that form the classic base protection ring, mirroring
 * SimulationCombat.isBaseProtectionCell verbatim:
 *   row br−1 over cols bc−1..bc+2, plus cols bc−1 and bc+2 at rows br..br+1.
 * Computed once — the base position is a fixed constant (`BASE_POS`).
 */
const BASE_RING_CELLS: Array<{ col: number; row: number }> = (() => {
  const cells: Array<{ col: number; row: number }> = []
  const bc = BASE_POS.col
  const br = BASE_POS.row
  for (let col = bc - 1; col <= bc + 2; col++) {
    cells.push({ col, row: br - 1 })
  }
  for (let row = br; row <= br + 1; row++) {
    cells.push({ col: bc - 1, row })
    cells.push({ col: bc + 2, row })
  }
  return cells
})()

/** Count protection-ring cells that are still solid (brick or steel). */
function countBaseWall(world: World): number {
  let n = 0
  for (const { col, row } of BASE_RING_CELLS) {
    const t = world.tileMap.get(col, row)
    if (t === 'brick' || t === 'steel') n++
  }
  return n
}

/**
 * Run a single headless simulation.
 *
 * Creates a fresh World + Simulation + GodAIInput, loads the given stage,
 * and ticks until the stage is cleared, the game is over, or maxTicks is
 * reached. Collects events and per-frame metrics.
 *
 * Deterministic: same seed + same stage + same difficulty ⇒ identical result.
 */
export function runSimulation(opts: RunOptions): SimResult {
  const { seed, stage, difficulty } = opts
  const maxTicks = opts.maxTicks ?? MAX_TICKS
  const sampleInterval = opts.sampleInterval ?? 1
  const collectMetrics = opts.collectMetrics !== false
  const collectEvents = opts.collectEvents !== false
  // Stage-level adaptation happens inside GodAIInput.reset() via
  // computeStageAdaptedParams() — a unified, data-driven filter on stage
  // characteristics (armor ratio, brick/steel/forest/water density).
  // Per-stage (stage-name-keyed) overrides are forbidden (DECISIONS §81).
  const godAIParams = opts.godAIParams ?? DEFAULT_GOD_AI_PARAMS

  // Create a fresh World (avoids any state leakage between runs).
  const world = new World()
  world.rng.reseed(seed)

  // Set difficulty BEFORE loading the stage (loadStageData reads difficultyKey).
  // CRITICAL: must also set world.rules — startGame() does this but the
  // simulation runner calls loadStageData directly, so without this line every
  // simulation runs with DEFAULT_RULES (modern) regardless of the difficulty
  // key. Classic mode's bulletCap/instant/wander rules never took effect.
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  // §105 (M7, 2026-08-03): mirror startGame()'s P1 init. startGame sets
  // `playerLevel = difficulty.playerStartLevel` and `lives = difficulty.startLives`
  // BEFORE spawning; the runner calls loadStageData directly, which reads
  // NEITHER — so the simulated first life ran at level 0 (and 3 default lives)
  // even for difficulties that ship playerStartLevel=1 / startLives=2
  // (hard/chaos since §104/M6). The browser (startGame) and the sim
  // (loadStageData) therefore diverged: gates/A/Bs measured a "first life 0★,
  // 3 lives" baseline that understates the shipped config. Sync both here so
  // the simulation matches the real game on the first life.
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = opts.livesOverride ?? world.difficulty?.startLives ?? START_LIVES

  // Create the God AI input with an independent RNG (DECISIONS #47).
  // This decouples God AI decisions from the world RNG stream, enabling
  // faithful replay playback where the God AI is absent.
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input: GodAIInput =
    opts.policy === 'nn'
      ? (new NNInput(world, { weightsDir: opts.nnWeightsDir }) as unknown as GodAIInput)
      : opts.policy === 'intent'
        ? (new IntentPlayer(world, {
            weightsText: readFileSync(opts.intentWeightsDir ?? '', 'utf8'),
            replanEvery: opts.replanEvery,
          }) as unknown as GodAIInput)
        : opts.policy === 'intent-exec'
          ? (new IntentExecutor(world, {
              weightsText: readFileSync(opts.intentWeightsDir ?? '', 'utf8'),
              rng: godRng, // §47：执行器内部 God-AI 独立 RNG，与 world 解耦
              replanEvery: opts.replanEvery,
              riskGated: opts.riskGated,
              baseCadence: opts.baseCadence,
              dangerCadence: opts.dangerCadence,
              recordReplanTrace: opts.recordIntentTrace,
            }) as unknown as GodAIInput)
          : opts.policy === 'intent-oracle'
            ? (new IntentOracleProbe(world, {
                seed, // 内部派生 oracle/exec 两个独立 RNG（§47）
                replanEvery: opts.replanEvery,
              }) as unknown as GodAIInput)
            : opts.policy === 'goal' || opts.policy === 'goal-god'
              ? (new GoalExecutor(world, {
                  weightsText:
                    opts.policy === 'goal-god'
                      ? ''
                      : readFileSync(opts.goalWeightsDir ?? '', 'utf8'),
                  rng: godRng, // §47：执行器内部 God-AI 独立 RNG（兜底/物理层）
                  promiseTicks: opts.promiseTicks || undefined,
                  // 诊断模式（§T9a 归因）：goal-god = 不跑网络，追逐 God-AI 导航目标，
                  // 测执行器上限。不构建模型 ⇒ weights 可缺。
                  followGodNav: opts.policy === 'goal-god',
                }) as unknown as GodAIInput)
              : new GodAIInput(world, godAIParams, godRng)
  const sim = new Simulation(world, input)

  // Lie-Back-Win-Mode: when --coop, set up player2 with God AI.
  const coop = opts.coop ?? false
  const spectateDual = opts.spectateDual ?? false
  let coopInput: GodAIInput | null = null

  // Load the stage (this also spawns the player and sets state to 'playing').
  // Pass the real stage index so the World's stageIndex (and therefore the
  // recorded snapshot / replay) reflects the actual stage being simulated.
  world.loadStageData(stage, opts.stageIndex ?? 0)

  // Dual central breach: set mode flags BEFORE input.reset() so P1's
  // computeStageAdaptedParams sees spectateDual=true and applies the
  // dual central breach knob overrides (plan/dual-central-breach-strategy.md).
  if (spectateDual) {
    world.spectate = true
    world.spectateDual = true
  }

  // Reset the input to pick up the new World state.
  input.reset()

  // Lie-Back-Win-Mode: set up coop (God AI as player2, human idle).
  if (coop && !spectateDual) {
    world.coop = true
    const d = world.difficulty
    world.lives2 = d?.startLives ?? 3
    world.playerLevel2 = d?.playerStartLevel ?? 0
    const p1Col = world.playerSpawnPoint?.col ?? 8
    world.player2SpawnPoint = { col: computePlayer2SpawnCol(p1Col), row: 24 }
    world.spawnPlayer2()
    // God AI controls player2 with an independent RNG.
    const coopRng = new RNG((seed ^ 0x9e3779b9 ^ 0xdeadbeef) >>> 0)
    coopInput = new GodAIInput(world, godAIParams, coopRng, (w) => w.player2)
    coopInput.reset()
    sim.input2 = coopInput
  }

  // 督战双玩家 (dual supervise): mirror Game.requestSpectateToggle(true).
  // God AI already drives P1 (the `input` above); here we also spawn player2
  // and attach a SECOND God AI so both tanks are machine-controlled.
  // Mode flags (world.spectate / world.spectateDual) were set BEFORE
  // input.reset() above so P1's stage adaptation sees them.
  if (spectateDual) {
    const d = world.difficulty
    world.lives2 = d?.startLives ?? 3
    world.playerLevel2 = d?.playerStartLevel ?? 0
    const p1Col = world.playerSpawnPoint?.col ?? 8
    world.player2SpawnPoint = { col: computePlayer2SpawnCol(p1Col), row: 24 }
    world.spawnPlayer2()
    // Second God AI drives player2 with an independent RNG.
    const dualRng = new RNG((seed ^ 0x9e3779b9 ^ 0xdeadbeef) >>> 0)
    const god2 = new GodAIInput(world, godAIParams, dualRng, (w) => w.player2)
    god2.reset()
    sim.input2 = god2
  }

  // Set up recording if requested (plan/God-AI-Replay-Visualization §4.1)
  // Note: InputRecorder.startNew() calls cloneWorld() internally to capture
  // the initial snapshot. The order is safe: loadStageData → input.reset()
  // → recorder.startNew(world) because reset() doesn't mutate world state.
  const recorder = opts.record ? new InputRecorder() : null
  if (recorder) recorder.startNew(world)

  const allEvents: GameEvent[] = []
  const metrics: FrameMetrics[] = []
  let tick = 0
  let outcome: SimOutcome = 'max_ticks'
  let firstKillTick: number | undefined
  let failure: FailureTaxonomy | undefined
  // In-loop base_destroyed tracker: when `collectEvents` is off the event log
  // is not retained, so failure.killerKind cannot do its backward scan — the
  // last base_destroyed event's owner is captured here instead (same value).
  let lastBaseDestroyedBy: TankKind | undefined

  // ---- v6 telemetry accumulators (only touched when opts.telemetry) ----
  const wantTelemetry = opts.telemetry === true
  const baseWallTotal = wantTelemetry ? countBaseWall(world) : 0
  const seenPowerUpIds = wantTelemetry ? new Set<number>() : null
  // Ping-pong live-id buffers (AGENTS §14.1): the power-up census allocates a
  // fresh Set EVERY tick in the original code (a per-tick heap churn that the
  // score gate multiplies by ~9M ticks). Two preallocated sets alternate: the
  // current tick clears the one NOT referenced as `prevLivePowerUpIds` and
  // becomes the next tick's prev. Membership semantics are identical — values
  // are byte-identical to the allocating version.
  const liveIdSetA = wantTelemetry ? new Set<number>() : null
  const liveIdSetB = wantTelemetry ? new Set<number>() : null
  let prevLivePowerUpIds: Set<number> | null = wantTelemetry ? liveIdSetB : null
  const visitedCells = wantTelemetry ? new Set<number>() : null
  let playerDeaths = 0
  let playerShots = 0
  let powerUpsSpawned = 0
  let powerUpsCollected = 0
  let starsCollected = 0
  let basePressureSum = 0
  let basePressureSamples = 0
  // ---- M0 death-attribution accumulators (only touched when telemetry) ----
  const deaths: PlayerDeath[] = []
  // Enemy tank id → AI tier, refreshed each tick (the killer is usually still
  // alive when the player dies, so it cannot be learned from death events).
  const tankTierById = wantTelemetry ? new Map<number, IntelligenceLevel>() : null
  // ---- §119 run-forensics accumulators (only touched when opts.forensics) ----
  const wantForensics = opts.forensics === true
  const ACTION_TRACE_LEN = 10
  const actionTrace: ForensicsAction[] = []
  const fxEvents: ForensicsEventLog[] = []
  const fxInventory = new Map<string, number>()
  let fxKills = 0
  let fxPlayerDeaths = 0
  let fxTerminal: ForensicsSnapshot | null = null
  // ---- Threat-ledger accumulators (only touched when opts.threatLedger) ----
  const wantLedger = opts.threatLedger === true
  const ledgerSamples: ThreatLedgerSample[] = []
  let ledgerPrevSig = ''
  // ---- M4 star census (only touched when opts.powerupCensus) ----
  const wantCensus = opts.powerupCensus === true
  const censusStars = new Map<
    number,
    { spawnTick: number; picked: boolean; minDist: number; despawnTick: number }
  >()
  let censusStarSpawned = 0
  let censusStarPicked = 0

  const t0 = performance.now()

  while (tick < maxTicks) {
    sim.tick()
    // §120: snapshot THIS tick's AI decision state BEFORE endFrame clears it.
    // Event consumption happens after endFrame, and bullet_fired / death
    // events must be tagged with the state of the tick that PRODUCED them —
    // reading input._lastBranch / player.dir at consume time reads the stale
    // post-endFrame state (off-by-one: S6 s43's fatal down-shot was recorded
    // as the next tick's left-facing). Pure read, used only by forensics.
    const fxTick = wantForensics
      ? {
          branch: input._lastBranch,
          dir: world.player?.dir,
          px: world.player ? world.player.x + world.player.w / 2 : -1,
          py: world.player ? world.player.y + world.player.h / 2 : -1,
        }
      : null
    // Threat ledger: event-driven sampling — push a sample when the base HP /
    // ring integrity / threat predicate / branch / player cell / slack sign
    // changed (pre-endFrame: _lastBranch is THIS tick's decision). Read-only.
    if (wantLedger) {
      const s = computeLedgerSample(world, input, tick)
      const sig = ledgerSignature(s)
      if (sig !== ledgerPrevSig) {
        ledgerSamples.push(s)
        ledgerPrevSig = sig
      }
    }
    // Record this tick's input BEFORE endFrame clears the cached state.
    // In dual supervise the second God AI (god2) is the P2 input; coopInput is
    // null there, so fall back to whichever P2 input is actually wired.
    const p2Input = coopInput ?? (spectateDual ? sim.input2 : null)
    if (recorder) recorder.recordFrame(input, p2Input)
    // §119: append this tick's action + the decision rule that took effect
    // (same sampling point as the recorder — before endFrame clears the
    // cached decision state). Rolling window of the last 10 ticks.
    if (wantForensics) {
      const pf = world.player
      const pfX = pf ? pf.x + pf.w / 2 : -1
      const pfY = pf ? pf.y + pf.h / 2 : -1
      const bcx = BASE_POS.col * CELL + CELL
      const bcy = BASE_POS.row * CELL + CELL
      actionTrace.push({
        tick,
        branch: input._lastBranch,
        moveDir: input._moveDir,
        fire: input._fire,
        playerX: Math.round(pfX),
        playerY: Math.round(pfY),
        playerHp: pf?.hp ?? -1,
        playerLives: world.lives,
        playerDistToBase:
          pfX >= 0 ? Math.round((Math.abs(pfX - bcx) + Math.abs(pfY - bcy)) / CELL) : -1,
        baseHp: world.baseHp,
      })
      if (actionTrace.length > ACTION_TRACE_LEN) actionTrace.shift()
    }
    // Game.ts calls input.endFrame() after each tick; the headless runner
    // must do the same so GodAIInput's _thought flag resets and the AI
    // re-evaluates every tick (not just the first one).
    input.endFrame()
    // End the frame on the P2 God AI — in coop this is `coopInput`, in
    // 督战双玩家 (dual) it is `god2` (wired as sim.input2). endFrame() resets
    // _thought so the AI re-evaluates every tick; omitting it (only calling
    // coopInput?.endFrame()) left the dual P2 AI frozen after tick 0, collapsing
    // dual win-rate far below single-player. sim.input2 is null in single mode.
    sim.input2?.endFrame()
    tick++

    // Collect events.
    let collectedThisTick = 0
    const events = world.consumeEvents()
    // M4 star census: track every star power-up's lifecycle. Read-only —
    // observes world.powerUps (post-simulation state), no RNG, no feedback.
    if (wantCensus) {
      const pus = world.powerUps
      const seen = new Set<number>()
      for (let pi = 0; pi < pus.length; pi++) {
        const pu = pus[pi]
        if (!pu.alive || pu.type !== 'star') continue
        seen.add(pu.id)
        let rec = censusStars.get(pu.id)
        if (!rec) {
          rec = { spawnTick: tick, picked: false, minDist: Infinity, despawnTick: -1 }
          censusStars.set(pu.id, rec)
          censusStarSpawned++
        }
        const p = world.player
        if (p && p.alive) {
          const d = Math.abs(p.x - pu.x) + Math.abs(p.y - pu.y)
          if (d < rec.minDist) rec.minDist = d
        }
      }
      for (const [id, rec] of censusStars) {
        if (rec.despawnTick === -1 && !seen.has(id)) {
          rec.despawnTick = tick
          if (!rec.picked) {
            // The star left the field this tick: did the player collect it
            // (powerup_collected event) or did it time out? Same-tick multi-
            // star edge cases are acceptable for a diagnostic census.
            for (const e of events) {
              if (e.type === 'powerup_collected' && e.powerUp === 'star') {
                rec.picked = true
                censusStarPicked++
                break
              }
            }
          }
        }
      }
    }
    for (const e of events) {
      if (collectEvents) allEvents.push(e)
      if (e.type === 'base_destroyed') lastBaseDestroyedBy = e.by
      // Track first kill for failure taxonomy.
      if (firstKillTick === undefined && e.type === 'tank_destroyed' && e.by === 'player') {
        firstKillTick = tick
      }
      if (wantTelemetry) {
        // isPlayer (not kind === 'player'): the decoy ally tank also carries
        // kind='player' for its visual (SimulationPlayer.activateDecoy) but is
        // NOT the player — counting it here inflates playerDeaths and skews
        // death attribution with 0★ decoy deaths (probe-503, DECISIONS §105).
        if (e.type === 'tank_destroyed' && e.tank.isPlayer) {
          playerDeaths++
          const t = e.tank
          // Killer attribution: the event carries the killer tank id (additive
          // byId metadata from SimulationCombat.bulletHitsTank).
          let killerKind: TankKind | undefined
          let killerTier: IntelligenceLevel | undefined
          if (e.byId !== undefined) {
            const killer = world.tanks.find((k) => k.id === e.byId)
            killerKind = killer?.kind
            killerTier = tankTierById!.get(e.byId)
          }
          const bcx = BASE_POS.col * CELL + CELL
          const bcy = BASE_POS.row * CELL + CELL
          // Live-enemy surround context (M13 probe): count alive, fully-spawned
          // enemies at the death tick. Indexed loop — per-death, not per-tick;
          // telemetry-only, never feeds back (determinism invariant preserved).
          let liveEnemies = 0
          const tanksArr = world.tanks
          for (let ti = 0; ti < tanksArr.length; ti++) {
            const o = tanksArr[ti]
            if (!o.isPlayer && o.alive && o.spawnTimer <= 0) liveEnemies++
          }
          deaths.push({
            tick,
            x: t.x + t.w / 2,
            y: t.y + t.h / 2,
            distToBase: Math.round(
              (Math.abs(t.x + t.w / 2 - bcx) + Math.abs(t.y + t.h / 2 - bcy)) / CELL,
            ),
            killerKind,
            killerTier,
            branch: input._lastBranch,
            playerLevel: t.level ?? 0,
            hp: t.hp,
            liveEnemies,
          })
        } else if (e.type === 'bullet_fired' && e.bullet.isPlayer) playerShots++
        else if (e.type === 'powerup_collected') {
          collectedThisTick++
          powerUpsCollected++
          if (e.powerUp === 'star') starsCollected++
        }
      }
      if (wantForensics) {
        // §119 history log: player deaths (tick+pos+killer kind), kills
        // (tick+pos+killed kind), power-up pickups (tick+pos+type).
        if (e.type === 'tank_destroyed') {
          const cx = Math.round(e.tank.x + e.tank.w / 2)
          const cy = Math.round(e.tank.y + e.tank.h / 2)
          if (e.tank.isPlayer) {
            fxPlayerDeaths++
            let killerKind = 'unknown'
            if (e.byId !== undefined) {
              for (const k of world.tanks) if (k.id === e.byId) killerKind = k.kind
            }
            fxEvents.push({ tick, type: 'death', x: cx, y: cy, detail: killerKind })
          }
          if (e.by === 'player') {
            fxKills++
            fxEvents.push({ tick, type: 'kill', x: cx, y: cy, detail: e.tank.kind })
          }
        } else if (e.type === 'powerup_collected') {
          const pf = world.player
          fxInventory.set(e.powerUp, (fxInventory.get(e.powerUp) ?? 0) + 1)
          fxEvents.push({
            tick,
            type: 'pickup',
            x: pf ? Math.round(pf.x + pf.w / 2) : -1,
            y: pf ? Math.round(pf.y + pf.h / 2) : -1,
            detail: e.powerUp,
          })
        } else if (e.type === 'bullet_fired' && e.bullet.isPlayer) {
          // §120: every player shot, tagged with the decision-chain branch that
          // issued it (fxTick — the pre-endFrame snapshot), the player position
          // at fire, and whether the shot's ray crosses the base 2×2 rectangle.
          // The ray uses the BULLET's own dir (ground-truth trajectory), NOT
          // the tank's facing — a turn on the same tick leaves tank.dir off
          // the bullet's axis (S33 s81: fatal left shot recorded as facing up).
          //
          // LATENCY CAVEAT: the branch tag is fxTick — THIS tick's post-tick
          // decision state — but the fire itself was decided from the PREVIOUS
          // tick's input (one tick of input latency). In fast branch
          // transitions the tag can be one tick off; fine for aggregate branch
          // mixes, do not use it to pin an exact fire tick.
          const px = fxTick!.px
          const py = fxTick!.py
          const dir = e.bullet.dir
          // Base rect: cols 12-13 / rows 24-25. The bullet corridor is the
          // bullet's forward ray (≈ ±16px = tank half-width band).
          const baseL = 12 * CELL
          const baseR = 14 * CELL
          const baseT = 24 * CELL
          const baseB = 26 * CELL
          let towardBase = false
          if (dir && px >= 0 && py >= 0) {
            if (dir === 'down') towardBase = py < baseT && px > baseL - 16 && px < baseR + 16
            else if (dir === 'up') towardBase = py > baseB && px > baseL - 16 && px < baseR + 16
            else if (dir === 'right') towardBase = px < baseL && py > baseT - 16 && py < baseB + 16
            else if (dir === 'left') towardBase = px > baseR && py > baseT - 16 && py < baseB + 16
          }
          fxEvents.push({
            tick,
            type: 'shot',
            x: Math.round(px),
            y: Math.round(py),
            detail: fxTick!.branch,
            dir,
            towardBase,
          })
        }
      }
    }

    // Death attribution: refresh the enemy id → AI-tier map (read-only scan,
    // O(live tanks) per tick — telemetry-only, never affects gameplay).
    if (wantTelemetry) {
      const tanksNow = world.tanks
      for (let ti = 0; ti < tanksNow.length; ti++) {
        const t = tanksNow[ti]
        if (t.aiState) tankTierById!.set(t.id, t.aiState.level)
      }
    }

    // Telemetry sampling — read-only, never consumes world.rng.
    if (wantTelemetry) {
      // Power-up spawn census. `updateBullets` (which drops power-ups on a
      // kill) runs BEFORE `updatePowerUps` in the same tick, so a drop that
      // lands on the player is collected before we ever observe it in
      // `world.powerUps`. Counting only observed ids therefore undercounts.
      //
      // We reconcile per tick: ids newly present are fresh spawns; collections
      // that cannot be explained by an id vanishing from the live set must be
      // same-tick pickups, so they are fresh spawns too. (A pickup and a
      // timeout-despawn colliding in one tick can still undercount by one —
      // rare, and only ever biases `powerUpsSpawned` downward, which makes the
      // loot-capture dimension conservative rather than inflated.)
      const liveIds = prevLivePowerUpIds === liveIdSetA ? liveIdSetB! : liveIdSetA!
      liveIds.clear()
      for (const pu of world.powerUps) {
        liveIds.add(pu.id)
        if (!seenPowerUpIds!.has(pu.id)) {
          seenPowerUpIds!.add(pu.id)
          powerUpsSpawned++
        }
      }
      let vanished = 0
      for (const id of prevLivePowerUpIds!) if (!liveIds.has(id)) vanished++
      powerUpsSpawned += Math.max(0, collectedThisTick - vanished)
      prevLivePowerUpIds = liveIds

      if (tick % TELEMETRY_SAMPLE_TICKS === 0) {
        basePressureSamples++
        basePressureSum += sampleBasePressure(world)
        if (world.player?.alive) {
          const col = Math.floor((world.player.x + world.player.w / 2) / CELL)
          const row = Math.floor((world.player.y + world.player.h / 2) / CELL)
          visitedCells!.add(row * GRID + col)
        }
      }
    }

    // Check for terminal states.
    if (world.state === 'stageclear') {
      outcome = 'stage_clear'
      if (wantForensics) fxTerminal = snapshotForensics(world, tick)
      break
    }
    if (world.state === 'gameover') {
      outcome = 'gameover'
      if (wantForensics) fxTerminal = snapshotForensics(world, tick)
      // Determine failure cause: base destroyed or lives exhausted.
      const baseDestroyed = world.tileMap.isBaseDestroyed()
      failure = {
        cause: baseDestroyed ? 'base_destroyed' : 'lives_exhausted',
        tick,
        firstKillTick,
      }
      // Populate killerKind from the base_destroyed event. The Simulation
      // records the actual bullet owner when the base collision resolves;
      // using the last bullet_fired event would misattribute an unrelated
      // shot fired before the killing bullet arrived.
      if (baseDestroyed) {
        if (collectEvents) {
          for (let i = allEvents.length - 1; i >= 0; i--) {
            const e = allEvents[i]
            if (e.type === 'base_destroyed') {
              failure.killerKind = e.by
              break
            }
          }
        } else {
          failure.killerKind = lastBaseDestroyedBy
        }
      }
      // Record player distance to base at death moment.
      if (world.player) {
        const pcx = world.player.x + world.player.w / 2
        const pcy = world.player.y + world.player.h / 2
        const bcx = BASE_POS.col * CELL + CELL
        const bcy = BASE_POS.row * CELL + CELL
        failure.playerDistToBase = Math.round((Math.abs(pcx - bcx) + Math.abs(pcy - bcy)) / CELL)
      }
      break
    }
    // If the game transitioned to 'victory' (ran out of stages).
    if (world.state === 'victory') {
      outcome = 'stage_clear'
      if (wantForensics) fxTerminal = snapshotForensics(world, tick)
      break
    }

    // Sample metrics.
    if (collectMetrics && tick % sampleInterval === 0) {
      metrics.push(sampleFrame(world, tick))
    }
  }

  // If we hit max_ticks without a terminal state, record timeout.
  if (outcome === 'max_ticks' && !failure) {
    failure = { cause: 'timeout', tick, firstKillTick }
    if (wantForensics) fxTerminal = snapshotForensics(world, tick)
  }

  const wallMs = performance.now() - t0
  const runParamsHash = paramsHash(opts.godAIParams ?? DEFAULT_GOD_AI_PARAMS)

  const result: SimResult = {
    outcome,
    // 全灭口径：与 stage_clear 解耦（BONUS TIME 窗口内被 max-ticks 截断的局仍算全灭）。
    cleared: allEnemiesCleared(world),
    ticks: tick,
    wallMs,
    finalState: {
      score: world.score,
      lives: world.lives,
      killCount: world.killCount,
      playTimeMs: world.playTimeMs,
      stageIndex: world.stageIndex,
      baseAlive: !world.tileMap.isBaseDestroyed(),
      playerAlive: !!world.player?.alive,
      playerLevel: world.playerLevel,
      ...(coop
        ? { score2: world.score2, lives2: world.lives2, player2Alive: !!world.player2?.alive }
        : {}),
    },
    events: allEvents,
    metrics,
    seed,
    difficulty,
    firstKillTick,
    failure,
    paramsHash: runParamsHash,
  }

  if (opts.commitCounts === true) {
    result.suicideReturnCommits = input.branchCounts.suicideReturn
    result.selfFireGuardBlocks = input._selfFireGuardBlocks
  }

  if (opts.branchTotals === true) {
    result.branchTotals = { ...input.branchCounts }
  }

  if (opts.recordGoalTrace === true && opts.policy === 'goal') {
    const gx = input as unknown as {
      reselectTrace?: Array<{ tick: number; cell: number; clause: string; outcome: string }>
    }
    if (gx.reselectTrace && gx.reselectTrace.length) {
      ;(result as { goalReselectTrace?: unknown }).goalReselectTrace = gx.reselectTrace
    }
  }

  if (
    opts.recordIntentTrace === true &&
    (opts.policy === 'intent-exec' || opts.policy === 'intent')
  ) {
    const ex = input as unknown as {
      replanTrace?: Array<{ tick: number; intent: number; margin: number }>
      intentTrace?: number[]
    }
    if (ex.replanTrace && ex.replanTrace.length) result.replanIntentTrace = ex.replanTrace
    if (ex.intentTrace && ex.intentTrace.length) result.committedIntentTrace = ex.intentTrace
  }

  if (wantForensics) {
    result.forensics = {
      outcome,
      failureCause: failure?.cause,
      terminal: fxTerminal ?? snapshotForensics(world, tick),
      lastActions: actionTrace,
      events: fxEvents,
      inventory: Object.fromEntries(fxInventory),
      kills: fxKills,
      playerDeaths: fxPlayerDeaths,
    }
  }

  if (wantLedger) {
    result.ledger = {
      outcome,
      failureCause: failure?.cause,
      tick,
      baseMaxHp: world.baseMaxHp,
      samples: ledgerSamples,
    }
  }

  if (wantCensus) {
    result.powerupCensus = {
      spawned: censusStarSpawned,
      picked: censusStarPicked,
      stars: [...censusStars.values()].map((r) => ({
        spawnTick: r.spawnTick,
        picked: r.picked,
        minDist: r.minDist === Infinity ? -1 : r.minDist,
        despawnTick: r.despawnTick,
      })),
    }
  }

  if (wantTelemetry) {
    result.telemetry = {
      enemyTotal: stage.enemyCount ?? ENEMIES_PER_STAGE,
      startLives: world.difficulty.startLives,
      playerDeaths,
      playerShots,
      powerUpsSpawned,
      powerUpsCollected,
      starsCollected,
      finalPlayerLevel: world.playerLevel,
      baseWallIntact: countBaseWall(world),
      baseWallTotal,
      basePressureMean: basePressureSamples > 0 ? basePressureSum / basePressureSamples : 0,
      basePressureSamples,
      cellsVisited: visitedCells!.size,
      deaths,
    }
  }

  // Finalize recording if active
  if (recorder) {
    const rec = recorder.finalize()
    if (rec) {
      result.replay = {
        initialSnapshot: rec.snapshot,
        frames: rec.frames,
        tickCount: rec.tickCount,
      }
    }
  }

  return result
}

/**
 * §119: capture the full structured state at the terminal tick — player,
 * base, every live enemy (type/HP/distances) and every in-flight enemy
 * bullet (position/direction/distances/hit economics). Pure World read.
 */
function snapshotForensics(world: World, tick: number): ForensicsSnapshot {
  const p = world.player
  const pcx = p ? p.x + p.w / 2 : -1
  const pcy = p ? p.y + p.h / 2 : -1
  const bcx = BASE_POS.col * CELL + CELL
  const bcy = BASE_POS.row * CELL + CELL
  const distCells = (x: number, y: number): number =>
    Math.round((Math.abs(x - bcx) + Math.abs(y - bcy)) / CELL)

  const enemies: ForensicsTank[] = []
  const tanks = world.tanks
  for (let ti = 0; ti < tanks.length; ti++) {
    const t = tanks[ti]
    if (t.isPlayer || !t.alive || t.spawnTimer > 0) continue
    const cx = t.x + t.w / 2
    const cy = t.y + t.h / 2
    enemies.push({
      kind: t.kind,
      hp: t.hp,
      maxHp: t.maxHp,
      level: t.aiState?.level ?? 'none',
      x: Math.round(cx),
      y: Math.round(cy),
      distToPlayer: pcx >= 0 ? Math.round((Math.abs(cx - pcx) + Math.abs(cy - pcy)) / CELL) : -1,
      distToBase: distCells(cx, cy),
    })
  }

  const enemyBullets: ForensicsBullet[] = []
  const bullets = world.bullets
  for (let bi = 0; bi < bullets.length; bi++) {
    const b = bullets[bi]
    if (!b.alive || b.isPlayer) continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    // ETA to the player: aligned (same row/col, within the tank-width band)
    // AND approaching — same 口径 as countIncomingThreats.
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned =
      pcx >= 0 && (vertical ? Math.abs(bx - pcx) < CELL * 0.75 : Math.abs(by - pcy) < CELL * 0.75)
    const approaching =
      pcx >= 0 &&
      ((b.dir === 'down' && by < pcy) ||
        (b.dir === 'up' && by > pcy) ||
        (b.dir === 'right' && bx < pcx) ||
        (b.dir === 'left' && bx > pcx))
    const eta =
      aligned && approaching
        ? (vertical ? Math.abs(by - pcy) : Math.abs(bx - pcx)) / Math.max(1, b.speed)
        : -1
    enemyBullets.push({
      x: Math.round(bx),
      y: Math.round(by),
      dir: b.dir,
      damage: b.damage,
      distToPlayer: pcx >= 0 ? Math.round((Math.abs(bx - pcx) + Math.abs(by - pcy)) / CELL) : -1,
      distToBase: distCells(bx, by),
      etaToPlayer: Math.round(eta * 10) / 10,
      hitsToDiePlayer: p && p.hp > 0 ? Math.ceil(p.hp / Math.max(1, b.damage)) : 0,
      hitsToDieBase: world.baseHp > 0 ? Math.ceil(world.baseHp / Math.max(1, b.damage)) : 0,
    })
  }

  return {
    tick,
    playerAlive: !!p?.alive,
    playerLives: world.lives,
    playerHp: p?.hp ?? 0,
    playerHitsToDie: p && p.hp > 0 ? Math.ceil(p.hp / 100) : 0,
    playerDistToBase: pcx >= 0 ? distCells(pcx, pcy) : -1,
    playerLevel: world.playerLevel,
    baseAlive: !world.tileMap.isBaseDestroyed(),
    baseHp: world.baseHp,
    baseMaxHp: world.baseMaxHp,
    baseHitsToDie: world.baseHp > 0 ? Math.ceil(world.baseHp / 100) : 0,
    baseWallIntact: countBaseWall(world),
    enemies,
    enemyBullets,
  }
}

/** Sample per-frame metrics from the World. */
function sampleFrame(world: World, tick: number): FrameMetrics {
  const enemyPositions = world.tanks
    .filter((t) => t.alive && t.spawnTimer <= 0)
    .map((t) => ({ x: t.x + t.w / 2, y: t.y + t.h / 2 }))

  return {
    tick,
    bullets: world.bullets.filter((b) => b.alive).length,
    enemyCount: world.enemyCount,
    playerX: world.player ? world.player.x + world.player.w / 2 : 0,
    playerY: world.player ? world.player.y + world.player.h / 2 : 0,
    enemyPositions,
    incomingThreats: countIncomingThreats(world),
  }
}

/**
 * Instantaneous base pressure in [0,1] — how close the nearest live enemy is
 * to the base, on a linear ramp over `BASE_PRESSURE_RADIUS` cells.
 *
 * Stages without a base (curriculum arenas) report 0: there is nothing to
 * pressure, and the scorer drops the dimension rather than crediting safety
 * the AI did not earn.
 */
function sampleBasePressure(world: World): number {
  if (!world.tileMap.hasBase()) return 0
  const baseCol = BASE_POS.col
  const baseRow = BASE_POS.row
  let worst = 0
  for (const t of world.tanks) {
    if (!t.alive || t.spawnTimer > 0) continue
    const col = Math.floor((t.x + t.w / 2) / CELL)
    const row = Math.floor((t.y + t.h / 2) / CELL)
    const dist = Math.abs(col - baseCol) + Math.abs(row - baseRow)
    const p = 1 - dist / BASE_PRESSURE_RADIUS
    if (p > worst) worst = p
  }
  return worst > 0 ? Math.min(1, worst) : 0
}

/**
 * Count enemy bullets on a collision course with the player.
 * A bullet is "incoming" if it is aligned with the player (same row or
 * column, within tank width) and approaching.
 */
function countIncomingThreats(world: World): number {
  const p = world.player
  if (!p) return 0
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  let count = 0
  for (const b of world.bullets) {
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bcx - pcx) < CELL * 0.75 : Math.abs(bcy - pcy) < CELL * 0.75
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && bcy < pcy) ||
      (b.dir === 'up' && bcy > pcy) ||
      (b.dir === 'right' && bcx < pcx) ||
      (b.dir === 'left' && bcx > pcx)
    if (approaching) count++
  }
  return count
}

// ============================================================
// Threat-ledger sampling (plan/God-AI-Hard-Breakthrough-Implementation §4.1)
//
// M0 geometric ETA model — deliberately conservative and explainable, NOT a
// simulation of enemy RNG or turn costs. The M1 ThreatBudget pure module
// (src/ai/god/ThreatBudget.ts) replaces these estimates with the legal-turn-
// aware model; the sample shape stays stable so M0 corpora remain valid.
// ============================================================

/** Manhattan cell distance between two cells. */
function manhattanCells(a: { col: number; row: number }, b: { col: number; row: number }): number {
  return Math.abs(a.col - b.col) + Math.abs(a.row - b.row)
}

/** Nearest ring cell to (col,row) — Manhattan metric over BASE_RING_CELLS. */
function nearestRingCell(col: number, row: number): { col: number; row: number } {
  let best = BASE_RING_CELLS[0]
  let bestD = Infinity
  for (let i = 0; i < BASE_RING_CELLS.length; i++) {
    const c = BASE_RING_CELLS[i]
    const d = Math.abs(c.col - col) + Math.abs(c.row - row)
    if (d < bestD) {
      bestD = d
      best = c
    }
  }
  return best
}

/**
 * Build one ledger sample from the current World + the AI's cached decision
 * state. Pure observation: writes nothing, consumes no RNG, and snapshots
 * every value immediately (the tankCell shared-buffer contract — never hold
 * a reference across another tankCell call).
 */
function computeLedgerSample(world: World, input: GodAIInput, tick: number): ThreatLedgerSample {
  const p = world.player
  const pc = p ? playerCellOf(p) : { col: -1, row: -1 }
  const playerSpeed = p && p.speed > 0 ? p.speed : 1

  // Player fire-cooldown mirror (same semantics as thinkImpl's M6 gate).
  let onCooldown = false
  if (p) {
    if (world.rules.fireModel === 'bulletCap') {
      const cap =
        (world.rules.maxBullets['player'] ?? 1) +
        ((p.level ?? 0) >= world.rules.playerDoubleShotLevel ? 1 : 0)
      let inFlight = 0
      const bullets = world.bullets
      for (let bi = 0; bi < bullets.length; bi++) {
        const b = bullets[bi]
        if (b.alive && b.ownerId === p.id && ++inFlight >= cap) break
      }
      onCooldown = inFlight >= cap
    } else {
      onCooldown = world.frame * (1000 / 60) - p.lastFire < p.nextFireInterval
    }
  }

  // Per-enemy diagnostics. tankCell writes the shared buffer — snapshot into
  // our own objects immediately.
  const enemies: ThreatLedgerEnemy[] = []
  let liveEnemies = 0
  let nearestThreatEta = Infinity
  let bestInterceptEta = Infinity
  const tanks = world.tanks
  for (let ti = 0; ti < tanks.length; ti++) {
    const t = tanks[ti]
    if (t.isPlayer || !t.alive || t.spawnTimer > 0) continue
    liveEnemies++
    const c = input.tankCell(t)
    const cell = { col: c.col, row: c.row }
    const csb = enemyCanShootBase(input, t)
    const cbr = !csb && enemyCanBreachRing(input, t)
    const ring = nearestRingCell(cell.col, cell.row)
    // M0 conservative ETAs (ticks).
    const moveEta = csb || cbr ? 0 : (manhattanCells(cell, ring) * CELL) / Math.max(1, t.speed)
    const flightEta = (manhattanCells(cell, ring) * CELL) / Math.max(1, t.bulletSpeed)
    const shootEta = csb ? 0 : moveEta + flightEta
    const killEta = (manhattanCells(cell, pc) * CELL) / playerSpeed
    if (csb || cbr) {
      if (shootEta < nearestThreatEta) nearestThreatEta = shootEta
      if (killEta < bestInterceptEta) bestInterceptEta = killEta
    }
    enemies.push({
      id: t.id,
      kind: t.kind,
      hp: t.hp,
      cell,
      dir: t.dir,
      canShootBase: csb,
      canBreachRing: cbr,
      enemyToRingEta: Math.round(moveEta * 10) / 10,
      playerKillEta: Math.round(killEta * 10) / 10,
      shootEta: Math.round(shootEta * 10) / 10,
    })
  }

  const nearestEta = Number.isFinite(nearestThreatEta) ? Math.round(nearestThreatEta * 10) / 10 : -1
  const interceptEta = Number.isFinite(bestInterceptEta)
    ? Math.round(bestInterceptEta * 10) / 10
    : -1
  const slack =
    Number.isFinite(nearestThreatEta) && Number.isFinite(bestInterceptEta)
      ? Math.round((nearestThreatEta - bestInterceptEta) * 10) / 10
      : -999

  return {
    tick,
    baseHp: world.baseHp,
    intactRing: countBaseWall(world),
    playerCell: { col: pc.col, row: pc.row },
    playerDir: p?.dir ?? 'up',
    playerLives: world.lives,
    branch: input._lastBranch,
    onCooldown,
    liveEnemies,
    baseThreatNow: input.isBaseUnderThreat(),
    nearestThreatEta: nearestEta,
    playerEtaToBestIntercept: interceptEta,
    threatSlack: slack,
    noOpReason: input._moveDir === null && !input._fire ? input._lastBranch : null,
    enemies,
  }
}

/**
 * Event-detection signature: two samples with the same signature are
 * observationally identical for the ledger's triggers (base HP, ring intact,
 * threat predicate, branch, player cell, slack sign, no-op status). The
 * player-dist-to-base is folded in because the P4 race predicate consumes it.
 */
function ledgerSignature(s: ThreatLedgerSample): string {
  const threatFlags = s.enemies
    .map((e) => `${e.id}:${e.canShootBase ? 1 : 0}${e.canBreachRing ? 1 : 0}`)
    .join(',')
  return [
    s.baseHp,
    s.intactRing,
    s.branch,
    s.playerCell.col,
    s.playerCell.row,
    s.playerLives,
    s.liveEnemies,
    s.baseThreatNow ? 1 : 0,
    s.threatSlack >= 0 ? 1 : 0,
    s.noOpReason ?? '',
    threatFlags,
  ].join('|')
}

/**
 * Player tank cell — the ledger runs outside GodAIInput's private access, so
 * it re-derives the cell from world.player (identical to playerCellImpl).
 */
function playerCellOf(p: Tank): { col: number; row: number } {
  return { col: Math.floor((p.x + p.w / 2) / CELL), row: Math.floor((p.y + p.h / 2) / CELL) }
}
