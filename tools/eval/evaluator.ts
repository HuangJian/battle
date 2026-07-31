import type { SimResult, FrameMetrics } from '../sim/simulation-runner'
import { GRID } from '../../src/constants'
import type { StageData } from '../../src/types'
import { TileMap } from '../../src/game/TileMap'
import { floodFill } from '../../src/utils/pathfind'

// ============================================================
// Types
// ============================================================

export interface MetricDetail {
  value: number
  target: [number, number] | number
  normalized: number // 0..1
  weight: number
}

export interface EvaluationReport {
  hardPass: boolean
  softScore: number // 0..100
  totalScore: number // hardPass ? softScore : 0
  pass: boolean // hardPass && totalScore >= passThreshold
  details: Record<string, MetricDetail>
  hardMetrics: {
    stageClear: boolean
    baseAlive: boolean
    playerAlive: boolean
    livesRemaining: number
    playTimeMs: number
    playTimeMin: number
  }
  outcome: SimResult['outcome']
}

// ============================================================
// Baseline (initial values from plan §3.2, to be calibrated)
// ============================================================

export interface BaselineConfig {
  hard: {
    minPlayTimeMs: number
    maxPlayTimeMs: number
    requireBaseAlive: boolean
    minLivesRemaining: number
  }
  soft: {
    kpm: { target: [number, number]; weight: number }
    bulletDensity: { target: [number, number]; weight: number }
    threatRate: { target: [number, number]; weight: number }
    formationVar: { target: [number, number]; weight: number }
    killDiversity: { target: [number, number]; weight: number }
    terrainUtil: { target: [number, number]; weight: number }
    noDeadZones: { target: number; weight: number }
  }
  passThreshold: number
}

export const DEFAULT_BASELINE: BaselineConfig = {
  hard: {
    minPlayTimeMs: 30_000, // 30s minimum
    maxPlayTimeMs: 300_000, // 5 minutes max
    requireBaseAlive: true,
    minLivesRemaining: 1,
  },
  soft: {
    // Weights sum to exactly 1.0 so softScore ranges 0–100.
    kpm: { target: [8, 14], weight: 0.25 },
    bulletDensity: { target: [15, 35], weight: 0.2 },
    threatRate: { target: [0.8, 2.0], weight: 0.2 },
    formationVar: { target: [50, 300], weight: 0.15 },
    killDiversity: { target: [3, 4], weight: 0.08 },
    terrainUtil: { target: [0.35, 0.55], weight: 0.07 },
    noDeadZones: { target: 1, weight: 0.05 },
  },
  passThreshold: 70, // softScore must reach 70/100 when hardPass
}

// ============================================================
// Evaluator
// ============================================================

/**
 * Evaluate a simulation result against the baseline.
 *
 * Hard metrics are pass/fail gates. Soft metrics are scored on a 0..1 scale
 * (1 = inside target range, linear decay outside) and weighted.
 */
export function evaluate(
  result: SimResult,
  stage: StageData,
  baseline: BaselineConfig = DEFAULT_BASELINE,
): EvaluationReport {
  // ---- Hard metrics ----
  const stageClear = result.outcome === 'stage_clear'
  const baseAlive = result.finalState.baseAlive
  const playerAlive = result.finalState.playerAlive
  const livesRemaining = result.finalState.lives
  const playTimeMs = result.finalState.playTimeMs

  const hardPass =
    stageClear &&
    (!baseline.hard.requireBaseAlive || baseAlive) &&
    livesRemaining >= baseline.hard.minLivesRemaining &&
    playTimeMs >= baseline.hard.minPlayTimeMs &&
    playTimeMs <= baseline.hard.maxPlayTimeMs

  // ---- Soft metrics ----
  const details: Record<string, MetricDetail> = {}

  // KPM (kills per minute)
  const playTimeMin = playTimeMs / 60000
  const kpm = playTimeMin > 0 ? result.finalState.killCount / playTimeMin : 0
  details.kpm = scoreMetric(kpm, baseline.soft.kpm.target, baseline.soft.kpm.weight)

  // Bullet density (avg bullets per frame)
  const bulletDensity = avgBullets(result.metrics)
  details.bulletDensity = scoreMetric(
    bulletDensity,
    baseline.soft.bulletDensity.target,
    baseline.soft.bulletDensity.weight,
  )

  // Threat rate (fraction of time under incoming bullet threat, per second)
  const threatRate = computeThreatRate(result.metrics, result.ticks)
  details.threatRate = scoreMetric(
    threatRate,
    baseline.soft.threatRate.target,
    baseline.soft.threatRate.weight,
  )

  // Formation variation (avg pairwise Manhattan distance between enemies)
  const formationVar = computeFormationVariation(result.metrics)
  details.formationVar = scoreMetric(
    formationVar,
    baseline.soft.formationVar.target,
    baseline.soft.formationVar.weight,
  )

  // Kill diversity (number of distinct enemy kinds killed)
  const killDiversity = computeKillDiversity(result.events)
  details.killDiversity = scoreMetric(
    killDiversity,
    baseline.soft.killDiversity.target,
    baseline.soft.killDiversity.weight,
  )

  // Terrain utilization (fraction of non-empty cells)
  const terrainUtil = computeTerrainUtilization(stage)
  details.terrainUtil = scoreMetric(
    terrainUtil,
    baseline.soft.terrainUtil.target,
    baseline.soft.terrainUtil.weight,
  )

  // No dead zones (1 = all reachable, 0 = has dead zones)
  const noDeadZones = computeNoDeadZones(stage)
  details.noDeadZones = {
    value: noDeadZones,
    target: baseline.soft.noDeadZones.target,
    normalized: noDeadZones,
    weight: baseline.soft.noDeadZones.weight,
  }

  // ---- Total score ----
  const softScore = Object.values(details).reduce(
    (sum, d) => sum + d.normalized * d.weight * 100,
    0,
  )
  const totalScore = hardPass ? softScore : 0
  const pass = hardPass && totalScore >= baseline.passThreshold

  return {
    hardPass,
    softScore,
    totalScore,
    pass,
    details,
    hardMetrics: {
      stageClear,
      baseAlive,
      playerAlive,
      livesRemaining,
      playTimeMs,
      playTimeMin,
    },
    outcome: result.outcome,
  }
}

// ============================================================
// Metric computations
// ============================================================

/** Score a metric on 0..1 scale: 1 inside target range, linear decay outside. */
function scoreMetric(
  value: number,
  target: [number, number] | number,
  weight: number,
): MetricDetail {
  const [lo, hi] = Array.isArray(target) ? target : [target, target]
  let normalized: number
  if (value >= lo && value <= hi) {
    normalized = 1
  } else if (value < lo) {
    // Decay below the target range.
    normalized = lo > 0 ? Math.max(0, value / lo) : 0
  } else {
    // Decay above the target range.
    normalized = hi > 0 ? Math.max(0, hi / value) : 0
  }
  return { value, target, normalized, weight }
}

/** Average bullet count across all sampled frames. */
function avgBullets(metrics: FrameMetrics[]): number {
  if (metrics.length === 0) return 0
  let sum = 0
  for (const m of metrics) sum += m.bullets
  return sum / metrics.length
}

/**
 * Threat rate: fraction of time the player is under incoming bullet threat,
 * per second. Uses `incomingThreats` from FrameMetrics (actual bullet
 * trajectory intersection, not a proxy) and the true tick count for time.
 *
 * Each sample represents `sampleInterval` ticks. We count samples where
 * incomingThreats > 0, multiply by the sample interval to get threat-ticks,
 * then divide by total seconds (totalTicks / 60).
 */
function computeThreatRate(metrics: FrameMetrics[], totalTicks: number): number {
  if (metrics.length === 0 || totalTicks === 0) return 0
  let threatSamples = 0
  for (const m of metrics) {
    if (m.incomingThreats > 0) threatSamples++
  }
  // Determine the sample interval from consecutive tick values.
  const sampleInterval = metrics.length > 1 ? metrics[1].tick - metrics[0].tick : 1
  const threatTicks = threatSamples * sampleInterval
  const totalSeconds = totalTicks / 60
  return totalSeconds > 0 ? threatTicks / totalSeconds : 0
}

/**
 * Formation variation: average pairwise Manhattan distance between alive enemies,
 * averaged over all sampled frames. Higher = more spread out (less clumping).
 */
function computeFormationVariation(metrics: FrameMetrics[]): number {
  let totalDist = 0
  let count = 0
  for (const m of metrics) {
    const positions = m.enemyPositions
    if (positions.length < 2) continue
    let frameDist = 0
    let pairs = 0
    for (let i = 0; i < positions.length; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        frameDist +=
          Math.abs(positions[i].x - positions[j].x) + Math.abs(positions[i].y - positions[j].y)
        pairs++
      }
    }
    if (pairs > 0) {
      totalDist += frameDist / pairs
      count++
    }
  }
  return count > 0 ? totalDist / count : 0
}

/** Count distinct enemy kinds that were destroyed by the player. */
function computeKillDiversity(events: import('../../src/types').GameEvent[]): number {
  const kinds = new Set<string>()
  for (const e of events) {
    if (e.type === 'tank_destroyed' && e.by === 'player') {
      kinds.add(e.tank.kind)
    }
  }
  return kinds.size
}

/** Fraction of non-empty terrain cells in the stage. */
function computeTerrainUtilization(stage: StageData): number {
  let nonEmpty = 0
  for (let r = 0; r < GRID; r++) {
    const line = stage.tiles[r] || ''
    for (let c = 0; c < GRID; c++) {
      const ch = line[c] || '.'
      if (ch !== '.') nonEmpty++
    }
  }
  return nonEmpty / (GRID * GRID)
}

/**
 * Check that all enemy spawn points and the player spawn are in the same
 * connected component (no dead zones). Returns 1 if fully connected, 0 otherwise.
 */
function computeNoDeadZones(stage: StageData): number {
  const tm = new TileMap()
  tm.loadStage(stage)

  // Player spawn at (8,24). Flood-fill from there.
  const reachable = floodFill(tm, { col: 8, row: 24 })

  // Check that all enemy spawn points are reachable.
  // Enemy spawns: (0,0), (12,0), (6,0) — but these are tile coords (2×2),
  // so the sub-block is (col*2, row*2).
  const spawns = [
    { col: 0, row: 0 },
    { col: 12, row: 0 },
    { col: 6, row: 0 },
  ]

  for (const spawn of spawns) {
    // The spawn cell may be blocked by terrain (authentic stages do this).
    // Check if any cell in the 2×2 spawn area is reachable.
    let found = false
    for (let dr = 0; dr <= 1; dr++) {
      for (let dc = 0; dc <= 1; dc++) {
        if (reachable.has(`${spawn.col + dc},${spawn.row + dr}`)) {
          found = true
          break
        }
      }
      if (found) break
    }
    if (!found) return 0
  }

  return 1
}
