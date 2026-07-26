import type { World } from '../game/World'
import type { Tank, AIState, GoalType, CommanderDirective } from '../types'
import type { Direction } from '../constants'
import {
  CELL,
  TANK,
  FIELD,
  DIR_VECTORS,
  TACTICAL_INTERVAL_MS,
  STRATEGIC_INTERVAL_MS,
  COMMANDER_INTERVAL_MS,
  DODGE_LOCK_MS,
  NONE_TURN_MIN_MS,
  NONE_TURN_JITTER_MS,
  NONE_FIRE_JITTER_MS,
} from '../constants'
import { opposite, ALL_DIRS, snap } from '../utils/helpers'
import { INTELLIGENCE_LEVELS } from './config'
import { capabilityBias } from '../config/combat'
import type { IntelligenceConfig, Situation, Perception } from './types'
import { perceive, analyze, dirToward, manhattan } from './perception'

/**
 * ai/TacticalIntelligence.ts — the decision pipeline.
 *
 * Pipeline (per the plan's §4 architecture):
 *
 *   World → Perception → Situation Analysis → Goal Evaluation →
 *   Decision → Action Planner → Execution
 *
 * Thinking hierarchy (§6):
 *   - Strategic   : every ~20s  (stable long-term objective)
 *   - Tactical    : every ~5s   (dynamic goal + route target)
 *   - Reactive    : every tick  (bullet avoidance, commitment hold)
 *
 * All randomness flows through `world.rng` (AGENTS.md §2.3) so the framework
 * is fully deterministic under identical inputs (Definition of Done #8). No
 * gameplay state is stored outside the World — the per-tank `AIState` brain is
 * the only memory, and it lives on the tank.
 *
 * The manager holds no persistent fields of its own; it is a stateless
 * orchestrator that reads/writes brain state on the World.
 */
export class TacticalIntelligence {
  private readonly dt = 1000 / 60 // ms per simulation tick

  /** Entry point called by Simulation.updateEnemyAI. */
  update(world: World, fire: (tank: Tank) => void): void {
    const frozen = world.freezeTimer > 0
    if (frozen) {
      // Enemy freeze (power-up): stop, think nothing. Reactivity resumes after.
      for (const t of world.tanks) {
        if (t.alive && t.spawnTimer <= 0 && t.aiState) t.moving = false
      }
      return
    }

    // Command authority (world.activeCommanderId) is recomputed by Simulation
    // once per tick BEFORE this update runs — the AI layer only reads it
    // (One-Author invariant, AI-Tier-System-Revision §4).

    for (const tank of world.tanks) {
      if (!tank.alive || tank.spawnTimer > 0 || !tank.aiState) continue
      if (tank.aiState.level === 'none') {
        // Classic branch (§3): minimal wander+fire, no tactical pipeline.
        this.updateNoneTank(world, tank, fire)
      } else {
        this.updateTank(world, tank, fire)
      }
    }
  }

  // ================================================================
  // None tier — classic Battle City behavior branch (§3)
  // ================================================================

  /**
   * Random wander with a downward/toward-base bias, random fire on a
   * cooldown-plus-jitter schedule, direction re-roll on wall collision or
   * timer expiry. No perception, no goals, no dodging, deaf to directives.
   * All randomness through `world.rng` — deterministic and snapshot-safe.
   */
  private updateNoneTank(world: World, tank: Tank, fire: (tank: Tank) => void): void {
    const brain = tank.aiState!
    brain.thinkTimer -= this.dt
    brain.fireTimer -= this.dt

    // Blocked ahead? (terrain/bounds only — tank-vs-tank jams unstick via the
    // periodic timer re-roll, like the original game's bump-and-turn feel).
    const gx = snap(tank.x, CELL)
    const gy = snap(tank.y, CELL)
    const v = DIR_VECTORS[brain.currentDir]
    const nx = gx + v.dx * CELL
    const ny = gy + v.dy * CELL
    const blocked = !world.isInBounds(nx, ny, TANK, TANK) || world.rectHitsTerrain(nx, ny, TANK, TANK)

    if (brain.thinkTimer <= 0 || blocked) {
      const open: Direction[] = []
      for (const d of ALL_DIRS) {
        const dv = DIR_VECTORS[d]
        const ox = gx + dv.dx * CELL
        const oy = gy + dv.dy * CELL
        if (world.isInBounds(ox, oy, TANK, TANK) && !world.rectHitsTerrain(ox, oy, TANK, TANK)) {
          open.push(d)
        }
      }
      if (open.length === 0) {
        // Fully boxed in — back out (see chooseDirection's jam note).
        brain.currentDir = opposite(brain.currentDir)
      } else {
        brain.currentDir = pickClassicDir(open, world)
      }
      brain.thinkTimer = NONE_TURN_MIN_MS + world.rng.next() * NONE_TURN_JITTER_MS
    }

    if (brain.fireTimer <= 0) {
      fire(tank)
      // Cooldown + jitter: guarantees ≥1 fire attempt per
      // (nextFireInterval + NONE_FIRE_JITTER_MS) window (objective floor, §3).
      brain.fireTimer = tank.nextFireInterval + world.rng.next() * NONE_FIRE_JITTER_MS
    }

    tank.dir = brain.currentDir
    tank.moving = true
  }

  // ================================================================
  // Per-tank pipeline
  // ================================================================

  private updateTank(world: World, tank: Tank, fire: (tank: Tank) => void): void {
    const brain = tank.aiState!
    // Tier capabilities are fixed data — no difficulty scaling (revision §2).
    const cfg = INTELLIGENCE_LEVELS[brain.level]

    // --- Decrement timers (all in ms) ---
    brain.thinkTimer -= this.dt
    brain.strategicTimer -= this.dt
    brain.fireTimer -= this.dt
    brain.reactionTimer = Math.max(0, brain.reactionTimer - this.dt)
    brain.dodgeLock = Math.max(0, brain.dodgeLock - this.dt)
    brain.directiveAge += this.dt
    brain.commanderTimer -= this.dt

    // --- Commander broadcast — ACTIVE command identity gates it, not the
    // born-as-commander flag (revision §4). Inactive Commanders never
    // broadcast; they fight as "super Veterans".
    if (tank.id === world.activeCommanderId && brain.commanderTimer <= 0) {
      this.broadcastDirective(world, tank, cfg)
      brain.commanderTimer = COMMANDER_INTERVAL_MS * (0.8 + world.rng.next() * 0.4)
    }

    // --- Observe once; reuse for tactical + reactive + firing ---
    const p = perceive(world, tank, cfg)
    const s = analyze(world, tank, p, cfg)

    // --- Strategic layer (stable long-term objective) — active Commander
    // only: inactive Commanders lose strategic thinking (revision §4).
    if (cfg.strategicThinking && tank.id === world.activeCommanderId && brain.strategicTimer <= 0) {
      this.strategicThink(world, tank, brain, cfg, p, s)
      brain.strategicTimer = STRATEGIC_INTERVAL_MS * (0.85 + world.rng.next() * 0.3)
    }

    // --- Tactical layer (dynamic goal + route target) ---
    if (brain.thinkTimer <= 0) {
      this.tacticalThink(world, tank, brain, cfg, p, s)
      brain.thinkTimer = TACTICAL_INTERVAL_MS * (0.7 + world.rng.next() * 0.6)
    }

    // --- Reactive layer (bullet avoidance, every tick) ---
    this.reactiveDodge(world, tank, brain, cfg, p, s)

    // --- Firing decision ---
    this.updateFiring(world, tank, brain, cfg, p, s, fire)

    // --- Execution: translate the brain's intent into World state ---
    tank.dir = brain.currentDir
    tank.moving = true
  }

  // ================================================================
  // Strategic layer
  // ================================================================

  private strategicThink(
    world: World,
    _tank: Tank,
    brain: AIState,
    _cfg: IntelligenceConfig,
    p: Perception,
    s: Situation,
  ): void {
    // The stable long-term objective. Default: attack the base. Occasionally
    // divert to the player if they are an easy, close target; retreat if
    // cornered and fragile. Goal stability (§12): this changes rarely.
    let goal: GoalType = 'attackBase'
    const r = world.rng.next()
    if (p.hasPlayer && s.distToPlayer < FIELD * 0.4 && r < 0.4) {
      goal = 'attackPlayer'
    } else if (s.threat && _tank.hp <= 1 && r < 0.3) {
      goal = 'retreat'
    }
    brain.strategicGoal = goal
  }

  // ================================================================
  // Tactical layer — goal evaluation (dynamic scoring, §9)
  // ================================================================

  private tacticalThink(
    world: World,
    tank: Tank,
    brain: AIState,
    cfg: IntelligenceConfig,
    p: Perception,
    s: Situation,
  ): void {
    const goal = this.evaluateGoals(world, tank, brain, cfg, p, s)
    brain.tacticalGoal = goal

    const target = this.targetForGoal(world, tank, brain, cfg, p, s, goal)
    brain.targetX = target.x
    brain.targetY = target.y

    brain.currentDir = this.chooseDirection(world, tank, brain, cfg, p, s, target.x, target.y)
  }

  /** Score every candidate goal and return the highest. */
  private evaluateGoals(
    _world: World,
    tank: Tank,
    brain: AIState,
    cfg: IntelligenceConfig,
    p: Perception,
    s: Situation,
  ): GoalType {
    const w = cfg.weights
    const maxDist = FIELD
    const threatPenalty = s.threat ? 0.35 : 0
    const followsDirective =
      brain.directive !== 'none' && brain.directiveCompliant && brain.directiveAge < COMMANDER_INTERVAL_MS * 1.5

    // Combat Capability bias (plan §14): the tank evaluates its OWN strengths.
    // A high-mobility tank presses/flanks harder; a high-armor tank pushes more
    // aggressively (lower risk); a high-firepower tank weighs attacks higher.
    const bias = tank.profile ? capabilityBias(tank.profile) : { flank: 0, push: 0, attack: 0 }
    const pushAttack = (bias.attack + bias.push) * 0.8

    // attackBase
    let attackBase = 0
    if (p.hasBase) {
      const closeness = 1 - Math.min(1, s.distToBase / maxDist)
      attackBase = w.attackBase * (0.4 + 0.6 * closeness) - threatPenalty + pushAttack
      if (brain.strategicGoal === 'attackBase') attackBase += 0.5
      if (followsDirective && brain.directive === 'defendBase') attackBase += 0.6
    }

    // attackPlayer
    let attackPlayer = 0
    if (p.hasPlayer) {
      const closeness = 1 - Math.min(1, s.distToPlayer / maxDist)
      attackPlayer =
        w.attackPlayer * (0.4 + 0.6 * closeness) +
        (s.playerInLineOfFire ? 0.4 : 0) -
        threatPenalty +
        pushAttack
      if (brain.strategicGoal === 'attackPlayer') attackPlayer += 0.4
      if (followsDirective && brain.directive === 'attackTogether') attackPlayer += 0.6
    }

    // destroyWall (only meaningful when a wall blocks the path / is in LOS)
    let destroyWall = -Infinity
    if (s.pathBlocked || s.wallInLineOfFire) {
      destroyWall = w.destroyWall * 1.0 + (s.wallInLineOfFire ? 0.3 : 0) + bias.attack * 0.5
    }

    // retreat (fragile + threatened). High armor already self-limits this by
    // shrinking the "fragile" window, so heavy tanks naturally retreat less.
    let retreat = -Infinity
    if (s.threat && tankHp(tank) <= 1) {
      retreat = w.retreat * 0.9 - bias.push * 0.3
    }

    // regroup (directive / congestion)
    let regroup = -Infinity
    if (s.congestion >= 2 || (followsDirective && brain.directive === 'spreadOut')) {
      regroup = w.regroup * (0.4 + s.congestion * 0.15) + bias.flank * 0.3
      if (followsDirective && brain.directive === 'spreadOut') regroup += 0.5
      if (followsDirective && brain.directive === 'defendBase') regroup += 0.3
    }

    // advance (always-present baseline; mobility makes flanking cheaper)
    const advance = w.advance * 0.3 + bias.flank * 0.6

    const scores: Array<[GoalType, number]> = [
      ['attackBase', attackBase],
      ['attackPlayer', attackPlayer],
      ['destroyWall', destroyWall],
      ['retreat', retreat],
      ['regroup', regroup],
      ['advance', advance],
    ]
    scores.sort((a, b) => b[1] - a[1])
    return scores[0][0]
  }

  /** Compute the pixel route target for the chosen goal (with directive bias). */
  private targetForGoal(
    _world: World,
    tank: Tank,
    brain: AIState,
    _cfg: IntelligenceConfig,
    p: Perception,
    _s: Situation,
    goal: GoalType,
  ): { x: number; y: number } {
    const cx = tank.x + tank.w / 2
    const cy = tank.y + tank.h / 2

    // Baseline objective: base if present, else player, else forward.
    let tx = p.hasBase ? p.baseX : p.hasPlayer ? p.playerX : cx
    let ty = p.hasBase ? p.baseY : p.hasPlayer ? p.playerY : cy + TANK

    if (goal === 'retreat') {
      // Flee to the map corner farthest from the player and away from the base.
      const corners = [
        { x: CELL * 2, y: CELL * 2 },
        { x: FIELD - CELL * 2, y: CELL * 2 },
        { x: CELL * 2, y: FIELD - CELL * 2 },
        { x: FIELD - CELL * 2, y: FIELD - CELL * 2 },
      ]
      let best = corners[0]
      let bestScore = -Infinity
      for (const c of corners) {
        const fromPlayer = p.hasPlayer ? manhattan(c.x, c.y, p.playerX, p.playerY) : FIELD
        const fromBase = p.hasBase ? manhattan(c.x, c.y, p.baseX, p.baseY) : 0
        const score = fromPlayer - fromBase * 0.5
        if (score > bestScore) {
          bestScore = score
          best = c
        }
      }
      return best
    }

    // Directive bias (teamwork tanks only; others ignore it — §14).
    if (
      brain.directive !== 'none' &&
      brain.directiveCompliant &&
      brain.directiveAge < COMMANDER_INTERVAL_MS * 1.5
    ) {
      switch (brain.directive) {
        case 'pushLeft':
          tx = Math.min(tx, FIELD * 0.33)
          break
        case 'pushRight':
          tx = Math.max(tx, FIELD * 0.66)
          break
        case 'spreadOut': {
          // Steer away from the teammate centroid.
          if (p.teammates.length > 0) {
            let mx = 0
            let my = 0
            for (const t of p.teammates) {
              mx += t.x
              my += t.y
            }
            mx /= p.teammates.length
            my /= p.teammates.length
            tx = clamp(tx + (cx - mx) * 0.6, CELL, FIELD - CELL)
            ty = clamp(ty + (cy - my) * 0.6, CELL, FIELD - CELL)
          }
          break
        }
        default:
          break
      }
    }

    return { x: tx, y: ty }
  }

  /**
   * Pick the best open direction toward (tx, ty). Scoring rewards progress,
   * the straightest axis, momentum, and avoids reversing / dead ends. Low
   * intelligence introduces route noise (imperfection model, §13).
   */
  private chooseDirection(
    world: World,
    tank: Tank,
    brain: AIState,
    cfg: IntelligenceConfig,
    p: Perception,
    _s: Situation,
    tx: number,
    ty: number,
  ): Direction {
    const cx = tank.x + tank.w / 2
    const cy = tank.y + tank.h / 2
    const desired = dirToward(cx, cy, tx, ty)
    const options = p.openDirs
    if (options.length === 0) {
      // Fully boxed in — every direction is blocked by terrain or another tank
      // (e.g. two enemies nose-to-nose in a 1-wide corridor, or wedged in a
      // dead-end pocket). Returning `currentDir` here would make the tank ram
      // the *same* blocked direction every tick → permanent freeze ("enemies
      // stuck on each other"). Reverse instead so the tank backs out of the
      // pocket; on a corridor with an open end this unzips the whole jam (the
      // tank at the open end is never boxed, moves, and frees the rest). If the
      // reverse is also blocked the tank simply can't move this tick — but at
      // least one member of any multi-tank jam with an opening will find it.
      return opposite(brain.currentDir)
    }

    const scored = options.map((d) => ({
      d,
      score: this.dirScore(world, tank, d, desired, tx, ty),
    }))
    scored.sort((a, b) => b.score - a.score)

    // Imperfection: occasionally commit to a suboptimal route.
    if (world.rng.next() < cfg.routeNoise && scored.length > 1) {
      const idx = 1 + world.rng.int(Math.min(scored.length - 1, 2))
      return scored[idx].d
    }
    return scored[0].d
  }

  private dirScore(
    world: World,
    tank: Tank,
    d: Direction,
    desired: Direction,
    tx: number,
    ty: number,
  ): number {
    const v = DIR_VECTORS[d]
    const nx = tank.x + v.dx * CELL
    const ny = tank.y + v.dy * CELL
    const before = manhattan(tank.x, tank.y, tx, ty)
    const after = manhattan(nx, ny, tx, ty)
    let score = (before - after) * 2
    if (d === desired) score += 1.5
    if (d === tank.dir) score += 0.5
    if (d === opposite(tank.dir)) score -= 1.0
    // Dead-end penalty: discourage entering pockets (≤1 open exit).
    const open = this.openCountAt(world, nx, ny)
    if (open <= 1) score -= 1.5
    return score
  }

  /** Count open directions from a position (terrain only, ignores tanks). */
  private openCountAt(world: World, x: number, y: number): number {
    // Grid-align the reference point so sub-cell drift doesn't skew the
    // dead-end penalty the way it skewed canStep (see canStep notes).
    const gx = snap(x, CELL)
    const gy = snap(y, CELL)
    let c = 0
    for (const d of ALL_DIRS) {
      const v = DIR_VECTORS[d]
      const nx = gx + v.dx * TANK
      const ny = gy + v.dy * TANK
      if (world.isInBounds(nx, ny, TANK, TANK) && !world.rectHitsTerrain(nx, ny, TANK, TANK)) c++
    }
    return c
  }

  // ================================================================
  // Reactive layer — bullet avoidance (§11)
  // ================================================================

  private reactiveDodge(
    world: World,
    tank: Tank,
    brain: AIState,
    cfg: IntelligenceConfig,
    p: Perception,
    s: Situation,
  ): void {
    if (!s.threat) return // no incoming bullet — stay the course

    // Delayed reaction (imperfection): ignore the threat until the delay elapses.
    if (brain.reactionTimer > 0) return
    // Committed to a dodge — hold it (prevents jitter).
    if (brain.dodgeLock > 0) return

    // Imperfection: may simply fail to dodge this time.
    if (world.rng.next() > cfg.dodgeProbability) {
      brain.reactionTimer = cfg.reactionTime
      return
    }

    const bullet = s.threat
    const vertical = bullet.dir === 'up' || bullet.dir === 'down'
    const candidates: Direction[] = vertical ? ['left', 'right'] : ['up', 'down']
    const safe = candidates.filter((d) => p.openDirs.includes(d))

    if (safe.length > 0) {
      // Prefer the escape that keeps making progress toward the objective.
      safe.sort(
        (a, b) =>
          this.dirProgress(tank, a, brain.targetX, brain.targetY) -
          this.dirProgress(tank, b, brain.targetX, brain.targetY),
      )
      brain.currentDir = safe[0]
    } else if (p.openDirs.length > 0) {
      // No perpendicular escape — step any open way that isn't into the bullet.
      const away = p.openDirs.filter((d) => d !== bullet.dir)
      brain.currentDir = away.length > 0 ? away[0] : p.openDirs[0]
    }

    brain.dodgeLock = DODGE_LOCK_MS
    brain.reactionTimer = cfg.reactionTime
  }

  private dirProgress(tank: Tank, d: Direction, tx: number, ty: number): number {
    const v = DIR_VECTORS[d]
    const nx = tank.x + v.dx * CELL
    const ny = tank.y + v.dy * CELL
    return manhattan(tank.x, tank.y, tx, ty) - manhattan(nx, ny, tx, ty)
  }

  // ================================================================
  // Firing decision
  // ================================================================

  private updateFiring(
    world: World,
    tank: Tank,
    brain: AIState,
    cfg: IntelligenceConfig,
    _p: Perception,
    s: Situation,
    fire: (tank: Tank) => void,
  ): void {
    if (brain.fireTimer > 0) return

    let shoot = false
    if (s.baseInLineOfFire || s.playerInLineOfFire) {
      shoot = true // always take a clear kill / base shot
    } else if (s.wallInLineOfFire) {
      // Break a wall that blocks progress toward the objective.
      const g = brain.tacticalGoal
      if (g === 'destroyWall' || g === 'attackBase' || g === 'attackPlayer') shoot = true
    } else if (world.rng.next() < effectiveAggression(cfg, tank)) {
      shoot = true // opportunistic fire when no clear shot
    }

    // aimError: even a good shot can be "fumbled" by low intelligence.
    if (shoot && (s.baseInLineOfFire || s.playerInLineOfFire) && world.rng.next() < cfg.aimError) {
      shoot = false
    }

    if (shoot) {
      fire(tank)
      // Fire cadence is a fixed per-type base, jittered per shot by ±5% (the
      // fire-rate standard). The actual gate in Simulation.tryFire uses the
      // tank's frozen `nextFireInterval`. The AI's own re-decision delay tracks
      // that same frozen value so the two never disagree and the tank re-tries
      // right as the gate opens (rather than every 250 ms).
      brain.fireTimer = tank.nextFireInterval
    } else {
      brain.fireTimer = 250 // re-check soon
    }
  }

  // ================================================================
  // Commander system (§8) — influence, never control
  // ================================================================

  /** The commander evaluates the battlefield and broadcasts a directive. */
  private broadcastDirective(world: World, commander: Tank, cfg: IntelligenceConfig): void {
    const directive = this.chooseDirective(world, commander, cfg)
    // Bump the world-level directive seq BEFORE assigning, so every receiver
    // caches the new (seq-keyed) compliance roll against this exact broadcast
    // (revision §2.2 [D7]). Succession-safe: a new commander incrementing
    // the counter automatically invalidates stale cached rolls.
    world.directiveSeqCounter += 1
    const seq = world.directiveSeqCounter
    for (const t of world.tanks) {
      if (!t.alive || t.spawnTimer > 0 || t === commander || !t.aiState) continue
      if (t.aiState.level === 'none') continue // deaf — separate branch, ignores directives
      t.aiState.directive = directive
      t.aiState.directiveAge = 0
      t.aiState.directiveSeq = seq
      // One compliance roll per directive, cached until the next broadcast.
      const lvl = INTELLIGENCE_LEVELS[t.aiState.level]
      t.aiState.directiveCompliant = world.rng.next() < lvl.compliance
    }
  }

  private chooseDirective(world: World, commander: Tank, cfg: IntelligenceConfig): CommanderDirective {
    const p = perceive(world, commander, cfg)
    const baseX = p.baseX
    const baseY = p.baseY

    // Base under pressure → defend it.
    let minBaseDist = Infinity
    for (const t of world.tanks) {
      if (!t.alive || t.spawnTimer > 0 || !t.aiState) continue
      const d = manhattan(t.x + t.w / 2, t.y + t.h / 2, baseX, baseY)
      if (d < minBaseDist) minBaseDist = d
    }
    if (minBaseDist < FIELD * 0.4) return 'defendBase'

    // Player is close → converge on them.
    if (p.hasPlayer && manhattan(p.selfX, p.selfY, p.playerX, p.playerY) < FIELD * 0.5) {
      return 'attackTogether'
    }

    // Crowded → spread out.
    if (p.congestion >= 2) return 'spreadOut'

    // Side bias → push the under-defended flank.
    let left = 0
    let right = 0
    for (const t of world.tanks) {
      if (!t.alive || t.spawnTimer > 0 || t.isPlayer || !t.aiState) continue
      if (t.x + t.w / 2 <= FIELD / 2) left++
      else right++
    }
    if (right > left + 1) return 'pushLeft'
    if (left > right + 1) return 'pushRight'

    return world.rng.pick([
      'attackTogether',
      'spreadOut',
      'pushLeft',
      'pushRight',
    ] as CommanderDirective[])
  }
}

// ---- small local helpers ----

function tankHp(tank: Tank): number {
  return tank.hp
}

/**
 * Firing willingness blends the intelligence tier's `aggression` with the
 * tank's own fire-control capability (plan §4.3 — fire control connects
 * directly with AI). A fire-control 80 tank fires ~0.94×aggression; a 50 tank
 * ~0.85×. Bounded to [0,1].
 */
function effectiveAggression(cfg: IntelligenceConfig, tank: Tank): number {
  const fc = tank.profile ? tank.profile.fireControl : 50
  return Math.min(1, cfg.aggression * (0.7 + 0.3 * (fc / 100)))
}

function clamp(v: number, min: number, max: number): number {
  return v < min ? min : v > max ? max : v
}

/**
 * pickClassicDir — classic wander bias (§3). Among open directions, weight
 * toward the base (downward — enemies spawn at the top and the eagle sits at
 * the bottom, so "down" is "toward base") so None-tier tanks still drift
 * toward the objective like the original game while allowing lateral jukes. A
 * single `world.rng` draw keeps the None branch fully deterministic.
 */
function pickClassicDir(open: Direction[], world: World): Direction {
  const weights: Record<Direction, number> = { down: 3, left: 1, right: 1, up: 0.35 }
  let total = 0
  for (const d of open) total += weights[d]
  let r = world.rng.next() * total
  for (const d of open) {
    r -= weights[d]
    if (r <= 0) return d
  }
  return open[open.length - 1]
}
