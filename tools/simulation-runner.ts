import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, type GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { CELL, BASE_POS } from '../src/constants'
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
  /** Failure attribution (plan/God-AI-Tuning §2). undefined on stage_clear. */
  failure?: FailureTaxonomy
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
  const { seed, stage, difficulty, godAIParams = DEFAULT_GOD_AI_PARAMS } = opts
  const maxTicks = opts.maxTicks ?? MAX_TICKS
  const sampleInterval = opts.sampleInterval ?? 1

  // Create a fresh World (avoids any state leakage between runs).
  const world = new World()
  world.rng.reseed(seed)

  // Set difficulty BEFORE loading the stage (loadStageData reads difficultyKey).
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']

  // Create the God AI input and Simulation.
  const input = new GodAIInput(world, godAIParams)
  const sim = new Simulation(world, input)

  // Load the stage (this also spawns the player and sets state to 'playing').
  world.loadStageData(stage, 0)

  // Reset the input to pick up the new World state.
  input.reset()

  const allEvents: GameEvent[] = []
  const metrics: FrameMetrics[] = []
  let tick = 0
  let outcome: SimOutcome = 'max_ticks'
  let firstKillTick: number | undefined
  let failure: FailureTaxonomy | undefined

  const t0 = performance.now()

  while (tick < maxTicks) {
    sim.tick()
    // Game.ts calls input.endFrame() after each tick; the headless runner
    // must do the same so GodAIInput's _thought flag resets and the AI
    // re-evaluates every tick (not just the first one).
    input.endFrame()
    tick++

    // Collect events.
    const events = world.consumeEvents()
    for (const e of events) {
      allEvents.push(e)
      // Track first kill for failure taxonomy.
      if (firstKillTick === undefined && e.type === 'tank_destroyed' && e.by === 'player') {
        firstKillTick = tick
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
      // Fix Bug 5: Populate killerKind — walk back through events to find
      // the last enemy bullet_fired before the base_destroyed event. That
      // bullet's ownerKind is the tank kind that destroyed the base.
      if (baseDestroyed) {
        for (let i = allEvents.length - 1; i >= 0; i--) {
          const e = allEvents[i]
          if (e.type === 'bullet_fired' && !e.bullet.isPlayer) {
            failure.killerKind = e.bullet.ownerKind
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

  return {
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
    failure,
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
