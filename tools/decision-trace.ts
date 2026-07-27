/**
 * decision-trace.ts — Detailed per-tick decision trace recorder.
 *
 * Runs a simulation step-by-step and records what the God AI decided at
 * every tick: movement, firing, which branch was taken, enemy positions,
 * threats, and events. The trace is used to analyze decision mistakes
 * after each optimization round.
 *
 * Pure tools-layer — does not modify src/ simulation code.
 */

import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, type GodAIParams, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { CELL, BASE_POS, TANK } from '../src/constants'
import type { StageData, GameEvent, TankKind, Direction } from '../src/types'

// ============================================================
// Types
// ============================================================

export interface TickDecision {
  tick: number
  /** Player cell position. */
  pc: { col: number; row: number }
  /** Player facing direction. */
  pdir: Direction
  /** AI-decided move direction (null = stop). */
  mv: Direction | null
  /** AI-decided fire. */
  fr: boolean
  /** Which branch was taken (inferred from branchCounts delta). */
  branch: string
  /** Enemies on field: kind + cell + hp. */
  enemies: Array<{ k: TankKind; c: number; r: number; hp: number; bonus: boolean }>
  /** Total bullets on field. */
  bullets: number
  /** Enemy bullets on field. */
  eBullets: number
  /** Incoming threats (bullets aligned with player). */
  threats: number
  /** Enemy bullets heading toward base. */
  baseThreats: number
  /** Base protection bricks remaining (approximate). */
  baseBricks: number
  /** Events this tick (kill, base_destroyed, etc). */
  ev: string[]
}

export interface DecisionTrace {
  /** Configuration used. */
  config: {
    seed: number
    difficulty: string
    stageName: string
    params: GodAIParams
  }
  /** Per-tick decisions. */
  ticks: TickDecision[]
  /** Final outcome. */
  outcome: string
  /** Total ticks. */
  totalTicks: number
  /** Failure taxonomy. */
  failure?: {
    cause: string
    tick: number
    playerDistToBase?: number
    firstKillTick?: number
  }
  /** Final state. */
  finalState: {
    score: number
    lives: number
    killCount: number
    baseAlive: boolean
    playerLevel: number
  }
  /** Branch usage summary. */
  branchSummary: Record<string, number>
}

// ============================================================
// Recorder
// ============================================================

export interface TraceOptions {
  seed: number
  stage: StageData
  difficulty: string
  params?: GodAIParams
  maxTicks?: number
  /** Sample every N ticks (1 = every tick, 6 = every 100ms). */
  sampleInterval?: number
}

/**
 * Run a simulation with full per-tick decision tracing.
 * Returns a DecisionTrace that can be analyzed for decision mistakes.
 */
export function traceSimulation(opts: TraceOptions): DecisionTrace {
  const { seed, stage, difficulty, params = DEFAULT_GOD_AI_PARAMS } = opts
  const maxTicks = opts.maxTicks ?? 18000
  const sampleInterval = opts.sampleInterval ?? 1

  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']

  const input = new GodAIInput(world, params)
  const sim = new Simulation(world, input)
  world.loadStageData(stage, 0)
  input.reset()

  const ticks: TickDecision[] = []
  const branchSummary: Record<string, number> = {}
  let prevBranchCounts = { ...input.branchCounts }
  let outcome = 'max_ticks'
  let failure: DecisionTrace['failure']
  let firstKillTick: number | undefined

  for (let tick = 1; tick <= maxTicks; tick++) {
    sim.tick()
    // IMPORTANT: read AI decisions BEFORE endFrame(), while _thought is still
    // true. This returns the cached _moveDir / _fire without re-calling think(),
    // which would consume RNG values and perturb the simulation.
    const aiMoveDir = input.getMoveDirection()
    const aiFire = input.isFiring()
    input.endFrame()

    // Collect events.
    const events = world.consumeEvents()
    const eventTypes: string[] = []
    for (const e of events) {
      eventTypes.push(e.type)
      if (firstKillTick === undefined && e.type === 'tank_destroyed' && e.by === 'player') {
        firstKillTick = tick
      }
    }

    // Check terminal states.
    if (world.state === 'stageclear') {
      outcome = 'stage_clear'
      break
    }
    if (world.state === 'gameover') {
      outcome = 'gameover'
      const baseDestroyed = world.tileMap.isBaseDestroyed()
      failure = {
        cause: baseDestroyed ? 'base_destroyed' : 'lives_exhausted',
        tick,
        firstKillTick,
      }
      if (world.player) {
        const pcx = world.player.x + world.player.w / 2
        const pcy = world.player.y + world.player.h / 2
        const bcx = BASE_POS.col * CELL + CELL
        const bcy = BASE_POS.row * CELL + CELL
        failure.playerDistToBase = Math.round((Math.abs(pcx - bcx) + Math.abs(pcy - bcy)) / CELL)
      }
      break
    }
    if (world.state === 'victory') {
      outcome = 'stage_clear'
      break
    }

    // Sample this tick.
    if (tick % sampleInterval === 0) {
      // Determine which branch was taken by diffing branchCounts.
      const curCounts = input.branchCounts
      let branch = 'none'
      for (const key of Object.keys(curCounts) as (keyof typeof curCounts)[]) {
        const delta = curCounts[key] - prevBranchCounts[key]
        if (delta > 0) {
          branch = key
          branchSummary[key] = (branchSummary[key] ?? 0) + delta
          break
        }
      }
      prevBranchCounts = { ...curCounts }

      // Record player state.
      const p = world.player
      if (p && p.alive) {
        const moveDir = aiMoveDir
        const fire = aiFire

        // Collect enemy info.
        const enemies = world.tanks
          .filter((t) => t.alive && t.spawnTimer <= 0)
          .map((t) => ({
            k: t.kind,
            c: Math.round(t.x / CELL),
            r: Math.round(t.y / CELL),
            hp: t.hp,
            bonus: !!t.bonus,
          }))

        // Count bullets.
        const allBullets = world.bullets.filter((b) => b.alive)
        const enemyBullets = allBullets.filter((b) => !b.isPlayer)

        // Count incoming threats.
        const threats = countIncomingThreats(world)

        // Count base-bound bullets.
        const baseThreats = countBaseThreats(world)

        // Count base protection bricks.
        const baseBricks = countBaseBricks(world)

        ticks.push({
          tick,
          pc: { col: Math.round(p.x / CELL), row: Math.round(p.y / CELL) },
          pdir: p.dir,
          mv: moveDir,
          fr: fire,
          branch,
          enemies,
          bullets: allBullets.length,
          eBullets: enemyBullets.length,
          threats,
          baseThreats,
          baseBricks,
          ev: eventTypes,
        })
      }
    }
  }

  if (outcome === 'max_ticks' && !failure) {
    failure = { cause: 'timeout', tick: maxTicks, firstKillTick }
  }

  return {
    config: {
      seed,
      difficulty,
      stageName: stage.name,
      params,
    },
    ticks,
    outcome,
    totalTicks: ticks.length > 0 ? ticks[ticks.length - 1].tick : 0,
    failure,
    finalState: {
      score: world.score,
      lives: world.lives,
      killCount: world.killCount,
      baseAlive: !world.tileMap.isBaseDestroyed(),
      playerLevel: world.playerLevel,
    },
    branchSummary,
  }
}

// ============================================================
// Helpers
// ============================================================

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

function countBaseThreats(world: World): number {
  const baseCx = BASE_POS.col * CELL + CELL
  const baseCy = BASE_POS.row * CELL + CELL
  let count = 0
  for (const b of world.bullets) {
    if (!b.alive || b.isPlayer) continue
    const bcx = b.x + b.w / 2
    const bcy = b.y + b.h / 2
    const dx = b.dir === 'right' ? 1 : b.dir === 'left' ? -1 : 0
    const dy = b.dir === 'down' ? 1 : b.dir === 'up' ? -1 : 0
    // Project forward and check if heading toward base.
    for (let d = CELL; d <= 26 * CELL; d += CELL) {
      const fx = bcx + dx * d
      const fy = bcy + dy * d
      if (Math.abs(fx - baseCx) < CELL * 2 && Math.abs(fy - baseCy) < CELL * 2) {
        count++
        break
      }
      if (fx < 0 || fx > 416 || fy < 0 || fy > 416) break
    }
  }
  return count
}

function countBaseBricks(world: World): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let count = 0
  for (let dr = -2; dr <= 1; dr++) {
    for (let dc = -2; dc <= 1; dc++) {
      const c = bc + dc
      const r = br + dr
      if (c < 0 || c >= 26 || r < 0 || r >= 26) continue
      if (world.tileMap.get(c, r) === 'brick') count++
    }
  }
  return count
}

// ============================================================
// Analysis
// ============================================================

/**
 * Analyze a decision trace for decision mistakes.
 * Returns a human-readable analysis string.
 */
export function analyzeTrace(trace: DecisionTrace): string {
  const lines: string[] = []
  lines.push(
    `=== Decision Trace Analysis: ${trace.config.stageName} seed=${trace.config.seed} ${trace.config.difficulty} ===`,
  )
  lines.push(`Outcome: ${trace.outcome} (ticks: ${trace.totalTicks})`)
  lines.push(
    `Final: kills=${trace.finalState.killCount} lives=${trace.finalState.lives} baseAlive=${trace.finalState.baseAlive} level=${trace.finalState.playerLevel}`,
  )

  if (trace.failure) {
    lines.push(`Failure: ${trace.failure.cause} at tick ${trace.failure.tick}`)
    if (trace.failure.playerDistToBase !== undefined) {
      lines.push(`  Player dist to base at death: ${trace.failure.playerDistToBase} cells`)
    }
    if (trace.failure.firstKillTick !== undefined) {
      lines.push(`  First kill at tick: ${trace.failure.firstKillTick}`)
    }
  }

  // Branch usage.
  lines.push(`\nBranch usage:`)
  for (const [branch, count] of Object.entries(trace.branchSummary).sort((a, b) => b[1] - a[1])) {
    lines.push(`  ${branch}: ${count}`)
  }

  // Find critical moments: ticks where base threats appeared.
  const criticalTicks = trace.ticks.filter((t) => t.baseThreats > 0)
  if (criticalTicks.length > 0) {
    lines.push(`\nBase threat moments (${criticalTicks.length} ticks):`)
    // Show first 5 and last 5.
    const shown =
      criticalTicks.length <= 10
        ? criticalTicks
        : [...criticalTicks.slice(0, 5), ...criticalTicks.slice(-5)]
    for (const t of shown) {
      lines.push(
        `  tick ${t.tick}: baseThreats=${t.baseThreats} branch=${t.branch} pos=(${t.pc.col},${t.pc.row}) mv=${t.mv} fr=${t.fr}`,
      )
    }
  }

  // Find idle ticks (player not moving and not firing).
  const idleTicks = trace.ticks.filter((t) => t.mv === null && !t.fr && t.enemies.length > 0)
  if (idleTicks.length > 0) {
    lines.push(`\nIdle ticks with enemies present: ${idleTicks.length}`)
    // Show first 5.
    for (const t of idleTicks.slice(0, 5)) {
      lines.push(
        `  tick ${t.tick}: pos=(${t.pc.col},${t.pc.row}) enemies=${t.enemies.length} dir=${t.pdir}`,
      )
    }
  }

  // Find firing-while-moving ticks (potential wasted shots).
  const movingFire = trace.ticks.filter((t) => t.mv !== null && t.fr)
  if (movingFire.length > 0) {
    lines.push(`\nFiring while moving: ${movingFire.length} ticks`)
  }

  // Player position heatmap (where the player spent the most time).
  const posCounts = new Map<string, number>()
  for (const t of trace.ticks) {
    const key = `${t.pc.col},${t.pc.row}`
    posCounts.set(key, (posCounts.get(key) ?? 0) + 1)
  }
  const topPositions = [...posCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)
  lines.push(`\nTop positions (cell: ticks):`)
  for (const [pos, count] of topPositions) {
    lines.push(`  (${pos}): ${count} ticks`)
  }

  return lines.join('\n')
}
