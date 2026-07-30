import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, type GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { applyStageOverrides } from '../src/ai/godai-stage-overrides'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { CELL, GRID, BASE_POS, ENEMIES_PER_STAGE } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { InputRecorder } from '../src/replay/InputRecorder'
import type { StageData, GameEvent, TankKind } from '../src/types'

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
}

export interface SimResult {
  /** What ended the simulation. */
  outcome: SimOutcome
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
  /** v6 evaluation telemetry (only when `telemetry: true`). */
  telemetry?: RunTelemetry
  /** Replay data (only when record=true). */
  replay?: {
    initialSnapshot: import('../src/snapshot/types').WorldSnapshot
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
// SimulationRunner
// ============================================================

/** Maximum ticks before forcing a stop (10 minutes at 60fps = 36000). */
export const MAX_TICKS = 36000

export interface RunOptions {
  seed: number
  stage: StageData
  difficulty: string
  /** God AI parameters (defaults to DEFAULT_GOD_AI_PARAMS). */
  godAIParams?: GodAIParams
  /** Max ticks before stopping (default: MAX_TICKS). */
  maxTicks?: number
  /** Sample metrics every N ticks (default: 1 = every frame). */
  sampleInterval?: number
  /**
   * Skip the per-stage GOD AI overrides (src/ai/godai-stage-overrides.ts)
   * and run with the given params verbatim. Used by probing tools that
   * need to measure the raw effect of a parameter set on a stage.
   * Default false: overrides apply, matching real evaluation conditions.
   */
  skipStageOverrides?: boolean
  /** Record input frames for replay playback (plan/God-AI-Replay-Visualization §4.1). */
  record?: boolean
  /**
   * Collect v6 evaluation telemetry (plan/God-AI-Evaluation-Redesign.md §3).
   * Default false — when off, the run path is byte-identical to before.
   */
  telemetry?: boolean
}

// ============================================================
// Telemetry constants (plan/God-AI-Evaluation-Redesign.md §3.4)
// ============================================================

/** Base-pressure sampling cadence in ticks (10 Hz at 60 fps). */
const TELEMETRY_SAMPLE_TICKS = 6
/** Enemies closer than this (Manhattan cells) contribute base pressure. */
const BASE_PRESSURE_RADIUS = 12

/**
 * The 8 cells that form the classic base protection ring: the border of the
 * 4×4 box centred on the 2×2 base, clipped to the grid. Computed once — the
 * base position is a fixed constant (`BASE_POS`).
 */
const BASE_RING_CELLS: Array<{ col: number; row: number }> = (() => {
  const cells: Array<{ col: number; row: number }> = []
  for (let row = BASE_POS.row - 1; row <= BASE_POS.row + 2; row++) {
    for (let col = BASE_POS.col - 1; col <= BASE_POS.col + 2; col++) {
      if (col < 0 || col >= GRID || row < 0 || row >= GRID) continue
      // Skip the 2×2 base itself — only the surrounding wall counts.
      const isBaseCell =
        col >= BASE_POS.col &&
        col <= BASE_POS.col + 1 &&
        row >= BASE_POS.row &&
        row <= BASE_POS.row + 1
      if (isBaseCell) continue
      cells.push({ col, row })
    }
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
  // P4: per-stage tactical overrides (data over code — see
  // src/ai/godai-stage-overrides.ts for rationale + validation protocol).
  const baseParams = opts.godAIParams ?? DEFAULT_GOD_AI_PARAMS
  const godAIParams = opts.skipStageOverrides
    ? baseParams
    : applyStageOverrides(stage.name, baseParams)

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

  // Create the God AI input with an independent RNG (DECISIONS #47).
  // This decouples God AI decisions from the world RNG stream, enabling
  // faithful replay playback where the God AI is absent.
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, godAIParams, godRng)
  const sim = new Simulation(world, input)

  // Load the stage (this also spawns the player and sets state to 'playing').
  world.loadStageData(stage, 0)

  // Reset the input to pick up the new World state.
  input.reset()

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

  // ---- v6 telemetry accumulators (only touched when opts.telemetry) ----
  const wantTelemetry = opts.telemetry === true
  const baseWallTotal = wantTelemetry ? countBaseWall(world) : 0
  const seenPowerUpIds = wantTelemetry ? new Set<number>() : null
  let prevLivePowerUpIds = wantTelemetry ? new Set<number>() : null
  const visitedCells = wantTelemetry ? new Set<number>() : null
  let playerDeaths = 0
  let playerShots = 0
  let powerUpsSpawned = 0
  let powerUpsCollected = 0
  let starsCollected = 0
  let basePressureSum = 0
  let basePressureSamples = 0

  const t0 = performance.now()

  while (tick < maxTicks) {
    sim.tick()
    // Record this tick's input BEFORE endFrame clears the cached state.
    if (recorder) recorder.recordFrame(input)
    // Game.ts calls input.endFrame() after each tick; the headless runner
    // must do the same so GodAIInput's _thought flag resets and the AI
    // re-evaluates every tick (not just the first one).
    input.endFrame()
    tick++

    // Collect events.
    let collectedThisTick = 0
    const events = world.consumeEvents()
    for (const e of events) {
      allEvents.push(e)
      // Track first kill for failure taxonomy.
      if (firstKillTick === undefined && e.type === 'tank_destroyed' && e.by === 'player') {
        firstKillTick = tick
      }
      if (wantTelemetry) {
        if (e.type === 'tank_destroyed' && e.tank.kind === 'player') playerDeaths++
        else if (e.type === 'bullet_fired' && e.bullet.isPlayer) playerShots++
        else if (e.type === 'powerup_collected') {
          collectedThisTick++
          powerUpsCollected++
          if (e.powerUp === 'star') starsCollected++
        }
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
      const liveIds = new Set<number>()
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
      break
    }
    if (world.state === 'gameover') {
      outcome = 'gameover'
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
        for (let i = allEvents.length - 1; i >= 0; i--) {
          const e = allEvents[i]
          if (e.type === 'base_destroyed') {
            failure.killerKind = e.by
            break
          }
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
      break
    }

    // Sample metrics.
    if (tick % sampleInterval === 0) {
      metrics.push(sampleFrame(world, tick))
    }
  }

  // If we hit max_ticks without a terminal state, record timeout.
  if (outcome === 'max_ticks' && !failure) {
    failure = { cause: 'timeout', tick, firstKillTick }
  }

  const wallMs = performance.now() - t0

  const result: SimResult = {
    outcome,
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
    },
    events: allEvents,
    metrics,
    seed,
    difficulty,
    firstKillTick,
    failure,
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
