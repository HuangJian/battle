import type { GodAIInput } from '../GodAIInput'
import { BASE_POS, CELL, TANK, MAX_ENEMIES_ALIVE } from '../../constants'
import { manhattan } from '../../utils/helpers'
import type { Direction } from '../../constants'

/**
 * Pillar B core (plan/God-AI-Redesign-v2 §4.2b): EnemyModel — 敌情感知模型。
 *
 * God AI does NOT read the difficulty label or `tank.aiState.level` to decide
 * how to play (评审决议 3: L1 降级为先验, EnemyModel 是主要运行时自适应).
 * Instead it ESTIMATES enemy strength from observable behavior, purely from
 * World state — no RNG, no reads of difficultyKey, no reads of aiState
 * (except as an OPTIONAL static prior, §4.2b 混合模式, default OFF).
 *
 * Features (all [0,1] EMA over `enemyModelWindowTicks`):
 *
 *   enemyFireAccuracy — player hits / enemy shots. Strong enemies aim well.
 *   attackTendency    — enemies moving toward the base (ΣΔdistToBase).
 *   coordination      — enemies aligned with the player (same row/col band).
 *   discipline        — enemy turn frequency (tactical turning vs wall-hit
 *                       wandering). HIGH when enemies dodge/redirect a lot.
 *
 * Output: `estimatedEnemyLevel ∈ [0,1]` — a weighted blend of the features
 * (optionally mixed with a static `aiState.level` prior when
 * `enemyTierWeightCommander/Veteran` > 0, 混合模式 default OFF).
 *
 * Consumers (all new params, default OFF — see GodAIParams docs):
 *   tierWeightScale            → target selection (FireControl)
 *   dodgeRateShrinksT2a        → engage range shrink by discipline
 *   coordinationRiskWeight     → survival-pressure activation
 *   enemyAccuracyRaisesSurvival → survival-pressure activation
 *   survivalModeLives          → survival-pressure base activation
 *   survivalRiskWeight         → survival effect magnitude
 *
 * Determinism: pure function of World history + GodAIInput instance state.
 * `world.lives`/`world.player` are read-only observations (AGENTS §2.1).
 * Same snapshot semantics as `_campTicks` — not serialized, re-converges
 * after a rewind. Update cadence: once per think tick, O(alive enemies).
 */

/** Per-tank tracking row — the only cross-tick state the model needs. */
interface TankTrack {
  fireCount: number
  distToBase: number
  dir: Direction
}

/** The full EnemyModel state, owned by GodAIInput (`_enemyModel`). */
export interface EnemyModelState {
  /** EMA outputs [0,1]. */
  fireAccuracy: number
  attackTendency: number
  coordination: number
  discipline: number
  /** Estimated enemy intelligence [0,1] (the consumer-facing output). */
  estimatedLevel: number
  /** Windowed raw accumulators (reset every `windowTicks` ticks). */
  winShots: number
  winHits: number
  winApproach: number // Σ cells moved toward base (per enemy)
  winAligned: number // Σ ticks-enemies aligned with player
  winTurns: number // Σ enemy direction changes
  winTicks: number // ticks accumulated in this window
  /** Per-live-tank cross-tick tracking (Map — bounded by MAX_ENEMIES_ALIVE). */
  tracked: Map<number, TankTrack>
  /** Whether the model is active (enemyModelMode > 0 && window > 0). */
  active: boolean
}

/** Fresh model state — called from GodAIInput.reset() per stage. */
export function initEnemyModel(active: boolean): EnemyModelState {
  return {
    fireAccuracy: 0,
    attackTendency: 0,
    coordination: 0,
    discipline: 0,
    estimatedLevel: 0,
    winShots: 0,
    winHits: 0,
    winApproach: 0,
    winAligned: 0,
    winTurns: 0,
    winTicks: 0,
    tracked: new Map(),
    active,
  }
}

/** Exponential moving-average alpha for a window of N ticks. */
function emaAlpha(windowTicks: number): number {
  return 2 / (windowTicks + 1)
}

/**
 * Per-tick update — O(alive enemies), pure observation. Called from thinkImpl
 * ONLY when the model is active (params.enemyModelMode > 0 &&
 * enemyModelWindowTicks > 0). Must not consume RNG and must not mutate the
 * World. Feature raw values accumulate over a window; at window boundaries
 * they are folded into the EMAs and the window resets.
 */
export function updateEnemyModel(self: GodAIInput): void {
  const m = self._enemyModel
  if (!m || !m.active) return
  const w = self.world
  const windowTicks = self.params.enemyModelWindowTicks
  const alpha = emaAlpha(windowTicks)

  // Cluster C: reuse the per-tick live-enemy snapshot (same array think()
  // built this tick — no re-filter, no allocation).
  const enemies = self._enemies.length > 0 ? self._enemies : w.tanks
  const p = self.controlledTank(w)

  const bc = BASE_POS.col
  const br = BASE_POS.row
  let shotDelta = 0
  let approachDelta = 0
  let alignedCount = 0
  let turnCount = 0

  for (let i = 0; i < enemies.length; i++) {
    const t = enemies[i]
    if (!t.alive || t.spawnTimer > 0) continue

    // Shots: fireCount is a monotonic per-tank counter (SimulationCombat).
    const prev = m.tracked.get(t.id)
    if (prev) {
      const d = t.fireCount - prev.fireCount
      if (d > 0) shotDelta += d
    }
    // Distance (cell-level, pixel-rounded like tankCell).
    const tc = Math.floor(t.x / CELL)
    const tr = Math.floor(t.y / CELL)
    const distToBase = manhattan(tc, tr, bc, br)

    // Turns: dir changed since last tick.
    if (prev && prev.dir !== t.dir) turnCount++

    // Alignment with the player (same row/col band — matches
    // findEnemyDirectionImpl's halfT=TANK trigger).
    if (p && p.alive) {
      const pcx = p.x + p.w / 2
      const pcy = p.y + p.h / 2
      const tcx = t.x + t.w / 2
      const tcy = t.y + t.h / 2
      if (Math.abs(tcx - pcx) < TANK || Math.abs(tcy - pcy) < TANK) alignedCount++
    }

    if (prev) {
      const approach = prev.distToBase - distToBase
      if (approach > 0) approachDelta += approach
    }
    m.tracked.set(t.id, { fireCount: t.fireCount, distToBase, dir: t.dir })
  }

  // Player hits: hp drop or alive→dead transition.
  if (p) {
    const hp = p.alive ? p.hp : 0
    if (hp < self._enemyModelLastHp) m.winHits++
    self._enemyModelLastHp = hp
  }

  m.winShots += shotDelta
  m.winApproach += approachDelta
  m.winAligned += alignedCount
  m.winTurns += turnCount
  m.winTicks++

  // Window boundary — fold raws into EMAs.
  if (m.winTicks >= windowTicks) {
    const ticks = m.winTicks || 1
    // Accuracy: hits / shots (shots==0 → no signal, keep the prior EMA).
    if (m.winShots > 0) {
      const acc = Math.min(1, m.winHits / m.winShots)
      m.fireAccuracy += alpha * (acc - m.fireAccuracy)
    } else {
      // No shots fired at the player — enemies are passive (low accuracy
      // evidence decays toward 0 so a quiet stage reads as weak).
      m.fireAccuracy += alpha * (0 - m.fireAccuracy)
    }
    // Approach: cells toward base per tick per enemy; ~0.06 cells/tick per
    // enemy when beelining (1px/tick ÷ 16px). 4 enemies ≈ 0.25/tick.
    const approachRate = m.winApproach / ticks
    const attack = Math.min(1, approachRate / 0.25)
    m.attackTendency += alpha * (attack - m.attackTendency)
    // Coordination: aligned enemies as a fraction of the 4-on-field cap.
    const coord = Math.min(1, m.winAligned / ticks / MAX_ENEMIES_ALIVE)
    m.coordination += alpha * (coord - m.coordination)
    // Discipline: turns per tick per enemy; tactical enemies turn far more
    // than the 'none'-tier wanderers that only turn on wall contact.
    // Normalize so 0.1 turns/tick/enemy reads as ~1.0 (≈ 1 turn / 0.17s).
    const turnRate = m.winTurns / ticks / MAX_ENEMIES_ALIVE
    const disc = Math.min(1, turnRate / 0.1)
    m.discipline += alpha * (disc - m.discipline)

    // Estimated level — weighted blend of the four features.
    const prior = staticPriorLevel(self)
    const dyn =
      0.35 * m.fireAccuracy + 0.25 * m.attackTendency + 0.2 * m.coordination + 0.2 * m.discipline
    const blend = self.params.enemyModelMode >= 2 ? prior * 0.5 + dyn * 0.5 : dyn
    m.estimatedLevel = Math.max(0, Math.min(1, blend))

    m.winShots = 0
    m.winHits = 0
    m.winApproach = 0
    m.winAligned = 0
    m.winTurns = 0
    m.winTicks = 0
    // Reset HP baseline to prevent cross-window HP drops (including death
    // transitions) from being attributed to the next window's winHits.
    self._enemyModelLastHp = p ? (p.alive ? p.hp : 0) : 0
  }
}

/**
 * Static prior (混合模式): blend the observable estimate with the spawn-rolled
 * `aiState.level` of live enemies. Weights come from
 * `enemyTierWeightCommander/Veteran` (0 = prior OFF, pure dynamic). Rookie and
 * below contribute 0 — only the high tiers carry prior signal. Pure
 * observation (reads tank.aiState — allowed as an OPTIONAL prior, §4.2b).
 */
function staticPriorLevel(self: GodAIInput): number {
  const wc = self.params.enemyTierWeightCommander
  const wv = self.params.enemyTierWeightVeteran
  if (wc <= 0 && wv <= 0) return 0
  const w = self.world
  const tanks = w.tanks
  let sum = 0
  let count = 0
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.spawnTimer > 0 || !t.aiState) continue
    const lvl = t.aiState.level
    if (lvl === 'commander') sum += wc
    else if (lvl === 'veteran') sum += wv
    else continue
    count++
  }
  if (count === 0) return 0
  // Normalize by MAX_ENEMIES_ALIVE (not count) to align with the dynamic
  // features' normalization — a single commander should not read as full
  // threat when the field could hold 4.
  return Math.min(1, sum / MAX_ENEMIES_ALIVE)
}

/**
 * Survival pressure (plan §3.2): 1 when the player must stop taking risks,
 * else 0. Activated by:
 *   - survivalModeLives > 0 AND world.lives ≤ survivalModeLives (命数盲 fix), OR
 *   - enemyAccuracyRaisesSurvival > 0 AND estimated fire accuracy ≥ that
 *     threshold (the enemy hits — take fewer risks NOW, not when lives run
 *     out), OR
 *   - coordinationRiskWeight > 0 AND estimated coordination × weight ≥ 1
 *     (surrounded — same early-activation logic).
 * All consumers default OFF ⇒ 0 at defaults (byte-identical).
 */
export function survivalPressure(self: GodAIInput): number {
  const p = self.params
  if (p.survivalModeLives > 0 && self.world.lives <= p.survivalModeLives) return 1
  const m = self._enemyModel
  if (!m || !m.active) return 0
  if (p.enemyAccuracyRaisesSurvival > 0 && m.fireAccuracy >= p.enemyAccuracyRaisesSurvival) return 1
  if (p.coordinationRiskWeight > 0 && m.coordination * p.coordinationRiskWeight >= 1) return 1
  return 0
}

/** Consumer-facing accessor: the estimated enemy level [0,1]. */
export function estimatedEnemyLevel(self: GodAIInput): number {
  const m = self._enemyModel
  return m && m.active ? m.estimatedLevel : 0
}
