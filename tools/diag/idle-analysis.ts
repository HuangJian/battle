#!/usr/bin/env bun
/**
 * idle-analysis.ts — Player stationary period analyzer for GOD AI calibration.
 *
 * 督战+单人+hard: runs headless God-AI simulations and detects periods where
 * the player tank remains effectively stationary for >10 seconds (600 ticks at
 * 60 FPS). "Effectively stationary" means the player's displacement from the
 * idle-start position never exceeds 1 cell (16px) in either axis — this
 * catches standing still, turning-in-place, and oscillating within 1 cell.
 *
 * Combat exemption: if the player was firing AND hit enemies or destroyed
 * brick walls during the stationary period, it is considered a correct
 * decision (not a bug). Only non-combat-exempt stationary periods are
 * reported as alerts.
 *
 * Determinism: same seed + same stage + same difficulty ⇒ identical alerts.
 * The idle tracker is a read-only observer — it never feeds back into the
 * World or the AI, so the simulation outcome is byte-identical to runSimulation.
 *
 * Usage:
 *   bun tools/diag/idle-analysis.ts --seeds 120 --difficulty hard
 *   bun tools/diag/idle-analysis.ts --seeds 120 --stages 1-10 --json tmp/idle.json
 *   bun tools/diag/idle-analysis.ts --from-json tmp/idle.json  # re-run only alerts
 *   bun tools/diag/idle-analysis.ts --all  # show combat-exempt alerts too
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { CELL, BASE_POS, START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import type { Direction } from '../../src/constants'
import type { StageData } from '../../src/types'
import { arg, parseSeeds, parseStages } from '../lib/cli'

// ============================================================
// Constants
// ============================================================

/** 10 seconds at 60 FPS = 600 ticks. */
const IDLE_THRESHOLD_TICKS = 600
/** Max ticks per simulation (10 minutes at 60fps). */
const MAX_TICKS = 36000
/** Displacement threshold: player must not move more than 1 cell (16px) from idle start. */
const CELL_THRESHOLD = CELL // 16px

// ============================================================
// Types
// ============================================================

/** One stationary alert — a period where the player didn't move for >2s. */
export interface IdleAlert {
  stageIndex: number
  stageName: string
  seed: number
  /** Tick when the stationary period started (first tick of no movement). */
  startTick: number
  /** Tick when the stationary period ended (last tick of no movement). */
  endTick: number
  /** Total ticks the player was stationary. */
  durationTicks: number
  /** Duration in milliseconds. */
  durationMs: number
  /** Player center X (px) during the idle period. */
  playerX: number
  /** Player center Y (px) during the idle period. */
  playerY: number
  /** Player cell (col, row) during the idle period. */
  playerCol: number
  playerRow: number
  /** Player facing direction at the end of the idle period. */
  playerDir: Direction
  /** All unique AI decision branches active during the idle period. */
  branches: string[]
  /** Number of ticks the player fired during the idle period. */
  fireTicks: number
  /** Whether _moveDir was null for the entire period (AI chose not to move). */
  moveDirAlwaysNull: boolean
  /** Number of live, fully-spawned enemies at the midpoint tick. */
  enemyCountMid: number
  /** Player's Manhattan distance to base (cells) at the midpoint. */
  distToBaseMid: number
  /** AI branch at the end of the idle period. */
  endBranch: string
  /** Simulation outcome. */
  outcome: string
  /** Combat exemption: player was firing AND hitting enemies or destroying terrain. */
  combatExempt: boolean
  /** Enemies killed by the player during the idle period. */
  killsDuringIdle: number
  /** Whether terrain was destroyed during the idle period. */
  terrainChangedDuringIdle: boolean
}

/** Per-run result with idle alerts. */
export interface IdleRunResult {
  stageIndex: number
  stageName: string
  seed: number
  outcome: string
  ticks: number
  alerts: IdleAlert[]
}

// ============================================================
// Idle-tracking simulation
// ============================================================

/**
 * Run a single headless simulation with idle tracking.
 * Mirrors `runSimulation` setup exactly, but adds per-tick player position
 * tracking to detect stationary periods > IDLE_THRESHOLD_TICKS.
 *
 * "Stationary" = player's displacement from the idle-start position never
 * exceeds 1 cell (CELL_THRESHOLD = 16px) in either X or Y axis. This catches:
 *   - Standing completely still
 *   - Turning in place without moving
 *   - Oscillating back and forth within 1 cell
 */
export function runSimulationWithIdleTracking(
  seed: number,
  stage: StageData,
  stageIndex: number,
  difficulty: string,
  params: GodAIParams = DEFAULT_GOD_AI_PARAMS,
): IdleRunResult {
  const maxTicks = MAX_TICKS
  const world = new World()
  world.rng.reseed(seed)

  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  // 督战 (supervise) mode: God AI drives player1, no human input.
  world.spectate = true

  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)

  // Load the stage (spawns player, sets state to 'playing').
  world.loadStageData(stage, stageIndex)

  // Reset the input to pick up the new World state.
  input.reset()

  // ---- Idle tracking state ----
  const alerts: IdleAlert[] = []
  let idleStart = -1 // tick when current idle period started (-1 = not idle)
  let idleStartX = 0 // player X at the START of the idle period
  let idleStartY = 0 // player Y at the START of the idle period
  // Accumulators for the current idle period:
  let idleBranchSet = new Set<string>()
  let idleFireTicks = 0
  let idleMoveDirAlwaysNull = true
  let idleKillsAtStart = 0 // world.killCount at idle start
  let idleRevisionAtStart = 0 // tileMap.revision at idle start

  const bcx = BASE_POS.col * CELL + CELL
  const bcy = BASE_POS.row * CELL + CELL

  let tick = 0
  let outcome = 'max_ticks'

  while (tick < maxTicks) {
    sim.tick()

    // Read player state BEFORE endFrame (to get this tick's decision branch).
    const p = world.player
    const playerAlive = !!p && p.alive && p.spawnTimer <= 0
    const px = p ? Math.round(p.x + p.w / 2) : -1
    const py = p ? Math.round(p.y + p.h / 2) : -1
    const branch = input._lastBranch
    const moveDir = input._moveDir
    const fire = input._fire

    input.endFrame()
    tick++

    // ---- Idle detection ----
    // "Effectively stationary" = player's displacement from idle-start
    // position does not exceed 1 cell (CELL_THRESHOLD = 16px) in either axis.
    // This catches standing still, turning in place, and oscillating within
    // 1 cell — all scenarios where the player isn't making meaningful progress.
    if (playerAlive) {
      if (idleStart < 0) {
        // Start tracking a potential idle period.
        idleStart = tick - 1
        idleStartX = px
        idleStartY = py
        idleBranchSet = new Set<string>([branch])
        idleFireTicks = fire ? 1 : 0
        idleMoveDirAlwaysNull = moveDir === null
        idleKillsAtStart = world.killCount
        idleRevisionAtStart = world.tileMap.revision
      } else if (
        Math.abs(px - idleStartX) <= CELL_THRESHOLD &&
        Math.abs(py - idleStartY) <= CELL_THRESHOLD
      ) {
        // Still within 1 cell of start — extend the idle period.
        idleBranchSet.add(branch)
        if (fire) idleFireTicks++
        if (moveDir !== null) idleMoveDirAlwaysNull = false
      } else {
        // Player moved more than 1 cell — check if the just-ended idle period
        // was long enough and not combat-exempt.
        const duration = tick - 1 - idleStart
        if (duration >= IDLE_THRESHOLD_TICKS) {
          const killsDuringIdle = world.killCount - idleKillsAtStart
          const terrainChanged = world.tileMap.revision !== idleRevisionAtStart
          const combatExempt = idleFireTicks > 0 && (killsDuringIdle > 0 || terrainChanged)
          if (!combatExempt) {
            alerts.push(
              makeAlert(
                stageIndex,
                STAGES[stageIndex]?.name ?? `Stage ${stageIndex + 1}`,
                seed,
                idleStart,
                tick - 2, // last stationary tick
                idleStartX,
                idleStartY,
                p!,
                idleBranchSet,
                idleFireTicks,
                idleMoveDirAlwaysNull,
                world,
                bcx,
                bcy,
                branch,
                combatExempt,
                killsDuringIdle,
                terrainChanged,
              ),
            )
          }
        }
        // Reset — start a new potential idle period at this tick.
        idleStart = tick - 1
        idleStartX = px
        idleStartY = py
        idleBranchSet = new Set<string>([branch])
        idleFireTicks = fire ? 1 : 0
        idleMoveDirAlwaysNull = moveDir === null
        idleKillsAtStart = world.killCount
        idleRevisionAtStart = world.tileMap.revision
      }
    } else {
      // Player dead or spawning — flush any ongoing idle period.
      if (idleStart >= 0) {
        const duration = tick - 1 - idleStart
        if (duration >= IDLE_THRESHOLD_TICKS) {
          const killsDuringIdle = world.killCount - idleKillsAtStart
          const terrainChanged = world.tileMap.revision !== idleRevisionAtStart
          const combatExempt = idleFireTicks > 0 && (killsDuringIdle > 0 || terrainChanged)
          if (!combatExempt) {
            alerts.push(
              makeAlert(
                stageIndex,
                STAGES[stageIndex]?.name ?? `Stage ${stageIndex + 1}`,
                seed,
                idleStart,
                tick - 2,
                idleStartX,
                idleStartY,
                p,
                idleBranchSet,
                idleFireTicks,
                idleMoveDirAlwaysNull,
                world,
                bcx,
                bcy,
                branch,
                combatExempt,
                killsDuringIdle,
                terrainChanged,
              ),
            )
          }
        }
      }
      idleStart = -1
    }

    // Check for terminal states.
    if (world.state === 'stageclear' || world.state === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (world.state === 'gameover') {
      outcome = 'gameover'
      break
    }
  }

  // Flush any ongoing idle period at simulation end.
  if (idleStart >= 0) {
    const duration = tick - 1 - idleStart
    if (duration >= IDLE_THRESHOLD_TICKS) {
      const killsDuringIdle = world.killCount - idleKillsAtStart
      const terrainChanged = world.tileMap.revision !== idleRevisionAtStart
      const combatExempt = idleFireTicks > 0 && (killsDuringIdle > 0 || terrainChanged)
      if (!combatExempt) {
        const p = world.player
        alerts.push(
          makeAlert(
            stageIndex,
            STAGES[stageIndex]?.name ?? `Stage ${stageIndex + 1}`,
            seed,
            idleStart,
            tick - 2,
            idleStartX,
            idleStartY,
            p,
            idleBranchSet,
            idleFireTicks,
            idleMoveDirAlwaysNull,
            world,
            bcx,
            bcy,
            input._lastBranch,
            combatExempt,
            killsDuringIdle,
            terrainChanged,
          ),
        )
      }
    }
  }

  return {
    stageIndex,
    stageName: STAGES[stageIndex]?.name ?? `Stage ${stageIndex + 1}`,
    seed,
    outcome,
    ticks: tick,
    alerts,
  }
}

/** Build an IdleAlert from accumulated tracking state. */
function makeAlert(
  stageIndex: number,
  stageName: string,
  seed: number,
  startTick: number,
  endTick: number,
  playerX: number,
  playerY: number,
  p: { dir: Direction } | null,
  branches: Set<string>,
  fireTicks: number,
  moveDirAlwaysNull: boolean,
  world: World,
  bcx: number,
  bcy: number,
  endBranch: string,
  combatExempt: boolean,
  killsDuringIdle: number,
  terrainChangedDuringIdle: boolean,
): IdleAlert {
  const durationTicks = endTick - startTick + 1
  const col = Math.floor(playerX / CELL)
  const row = Math.floor(playerY / CELL)
  // Sample enemy count at the current tick (close to the end of the period).
  let enemyCount = 0
  const tanks = world.tanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.isPlayer && t.alive && t.spawnTimer <= 0) enemyCount++
  }
  const distToBase = Math.round((Math.abs(playerX - bcx) + Math.abs(playerY - bcy)) / CELL)

  return {
    stageIndex,
    stageName,
    seed,
    startTick,
    endTick,
    durationTicks,
    durationMs: Math.round(durationTicks * (1000 / 60)),
    playerX,
    playerY,
    playerCol: col,
    playerRow: row,
    playerDir: p?.dir ?? 'up',
    branches: [...branches].sort(),
    fireTicks,
    moveDirAlwaysNull,
    enemyCountMid: enemyCount,
    distToBaseMid: distToBase,
    endBranch,
    outcome: '',
    combatExempt,
    killsDuringIdle,
    terrainChangedDuringIdle,
  }
}

// ============================================================
// CLI
// ============================================================

async function main() {
  const difficulty = arg('difficulty', 'hard')!
  const seeds = parseSeeds(arg('seeds'))
  const stageIdxs = parseStages(arg('stages'))
  const jsonOut = arg('json')
  const fromJson = arg('from-json')
  const verbose = process.argv.includes('--verbose')

  // If --from-json, re-run only the stage@seed combos that had alerts.
  let runSet: Array<{ stageIdx: number; seed: number }>
  if (fromJson) {
    const file = Bun.file(fromJson)
    const data = (await file.json()) as { results: IdleRunResult[] }
    runSet = []
    for (const r of data.results) {
      if (r.alerts.length > 0) {
        for (const seed of r.alerts.map((a) => a.seed)) {
          runSet.push({ stageIdx: r.stageIndex, seed })
        }
      }
    }
    // Deduplicate.
    const seen = new Set<string>()
    runSet = runSet.filter(({ stageIdx, seed }) => {
      const k = `${stageIdx}:${seed}`
      if (seen.has(k)) return false
      seen.add(k)
      return true
    })
    console.log(`[from-json] Re-running ${runSet.length} stage@seed combos with alerts`)
  } else {
    runSet = []
    for (const si of stageIdxs) for (const seed of seeds) runSet.push({ stageIdx: si, seed })
  }

  const total = runSet.length
  console.log(
    `[idle-analysis] ${total} runs | difficulty=${difficulty} | threshold=${IDLE_THRESHOLD_TICKS} ticks (${(
      IDLE_THRESHOLD_TICKS / 60
    ).toFixed(1)}s) | spectate=single`,
  )

  const t0 = performance.now()
  const results: IdleRunResult[] = []
  let done = 0
  let totalAlerts = 0

  for (const { stageIdx, seed } of runSet) {
    const stage = STAGES[stageIdx]
    if (!stage) continue

    const result = runSimulationWithIdleTracking(seed, stage, stageIdx, difficulty)
    results.push(result)

    if (result.alerts.length > 0) {
      totalAlerts += result.alerts.length
      for (const a of result.alerts) {
        const tag = `S${a.stageIndex + 1}@seed${a.seed}`
        const dur = (a.durationMs / 1000).toFixed(1)
        const fire = a.fireTicks > 0 ? ` 🔥${a.fireTicks}` : ''
        const kills = a.killsDuringIdle > 0 ? ` 💀${a.killsDuringIdle}` : ''
        const terrain = a.terrainChangedDuringIdle ? ' 🧱' : ''
        const br = a.branches.join(',')
        console.log(
          `  ⚠ ${tag} tick ${a.startTick}-${a.endTick} (${dur}s) ` +
            `pos=(${a.playerCol},${a.playerRow}) ${br}${fire}${kills}${terrain} ` +
            `enemies=${a.enemyCountMid} distBase=${a.distToBaseMid}`,
        )
      }
    }

    done++
    if (done % 500 === 0 || done === total) {
      const elapsed = ((performance.now() - t0) / 1000).toFixed(1)
      console.log(`[progress] ${done}/${total} runs | ${totalAlerts} alerts | ${elapsed}s`)
    }
  }

  const elapsed = ((performance.now() - t0) / 1000).toFixed(1)
  console.log(`\n[idle-analysis] Complete in ${elapsed}s`)
  console.log(`  Total runs: ${total}`)
  console.log(`  Total alerts: ${totalAlerts}`)
  console.log(`  Runs with alerts: ${results.filter((r) => r.alerts.length > 0).length}`)

  // Print summary by stage.
  const byStage = new Map<number, number>()
  for (const r of results) {
    if (r.alerts.length > 0) {
      byStage.set(r.stageIndex, (byStage.get(r.stageIndex) ?? 0) + r.alerts.length)
    }
  }
  if (byStage.size > 0) {
    console.log(`\n  Alerts by stage:`)
    const sorted = [...byStage.entries()].sort((a, b) => a[0] - b[0])
    for (const [si, count] of sorted) {
      console.log(`    S${si + 1} ${STAGES[si]?.name}: ${count} alerts`)
    }
  }

  // Write JSON output.
  if (jsonOut) {
    const output = {
      difficulty,
      thresholdTicks: IDLE_THRESHOLD_TICKS,
      thresholdMs: IDLE_THRESHOLD_TICKS * (1000 / 60),
      totalRuns: total,
      totalAlerts,
      results: results.filter((r) => r.alerts.length > 0 || verbose),
    }
    await Bun.write(jsonOut, JSON.stringify(output, null, 2))
    console.log(`\n  Written to ${jsonOut}`)
  }
}

main()
