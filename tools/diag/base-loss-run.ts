/**
 * base-loss-run.ts — instrumented single-run forensics for base losses.
 *
 * Answers, for every run that ends with `base_destroyed`:
 *   1. that the run lost the base at all           (outcome)
 *   2. player lives at the moment of base loss     (livesAtLoss)
 *   3. player distance to base at that moment      (distToBaseAtLoss)
 *   4. player HP when the KILLING bullet was fired (fire.hp / fire.hitsRemaining)
 *   5. enemy bullets threatening the player then   (fire.threatCount*)
 *   6. enemy bullets able to hit the player within
 *      1 s of that shot                            (fire.eta60Count* / fire.observed*)
 *
 * How the killing bullet is identified (exactly, not heuristically):
 *   `SimulationCombatMixin.damageBase(bullet)` is the single funnel for base
 *   damage. TypeScript `private` is compile-time only, so an own-property
 *   assignment on the Simulation *instance* shadows the prototype method and
 *   `this.damageBase(...)` resolves to our wrapper first. We record the bullet
 *   identity + the live World context, then delegate to the original. The base
 *   is an HP pool, so several bullets hit it over a run — the killing bullet is
 *   the one present when `baseHp` reaches 0.
 *
 * Read-only observation (AGENTS §2.1/§2.3): the wrapper never mutates the
 * World and never draws from `world.rng`, so an instrumented run is
 * byte-identical to an uninstrumented one. That is what lets pass 2 replay
 * pass 1 exactly.
 *
 * Two passes, because metrics 4-6 are anchored at the tick the killing bullet
 * was FIRED, which is only known once the base is already gone:
 *   pass 1 — find the killing bullet + its fire tick.
 *   pass 2 — re-run the same (seed, stage, difficulty) and snapshot the World
 *            at that fire tick, then watch the following 60 ticks.
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { CELL, BASE_POS, START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { PLAYER_PROGRESSION } from '../../src/config/combat'
import type { Bullet, StageData, Tank, TankKind } from '../../src/types'

/** One second at the fixed 60 Hz timestep (AGENTS §2.3). */
export const ONE_SECOND_TICKS = 60

/**
 * Alignment tolerance for "this bullet is on a line with the player", in px.
 * Mirrors `hasLethalBulletWithinWindowImpl` (src/ai/god/SuicideReturn.ts) so
 * the measurement uses the strategy's own definition of a threat rather than
 * a second, subtly different one.
 */
const ALIGN_PX = 32

/** Reference per-shot damage of a basic enemy under the pool model. */
const REFERENCE_DAMAGE = 100

export interface ThreatCounts {
  /** Enemy bullets aligned with + approaching the player (any ETA). */
  all: number
  /** Of those, the ones that would KILL rather than merely damage. */
  lethal: number
  /** Aligned + approaching AND impact within `ONE_SECOND_TICKS`. */
  eta60: number
  /** Of those, the lethal ones — the exact predicate of §116 condition 5. */
  eta60Lethal: number
}

export interface FireContext {
  /** Tick at which the killing bullet left its barrel. */
  tick: number
  /** Ticks of flight between muzzle and base. */
  flightTicks: number
  /** Was the player tank alive and on the field? */
  playerAlive: boolean
  /** >0 while the player is still materialising (spawn-shield invulnerable). */
  playerSpawnTimer: number
  /** Player HP pool at the fire tick. */
  hp: number
  maxHp: number
  /** HP expressed as "hits it can still absorb" from a basic enemy. */
  hitsRemaining: number
  /** Player star level (3★ survives a lethal hit by spending a star). */
  playerLevel: number
  /** Lives still in stock at the fire tick. */
  lives: number
  /** Player Manhattan distance to base, in cells. */
  distToBase: number
  /** Threat census excluding the base-killing bullet itself. */
  threats: ThreatCounts
  /** Did the base-killing bullet ALSO happen to threaten the player? */
  killBulletThreatensPlayer: boolean
  /**
   * Distinct enemy bullets observed to threaten the player at any point in
   * [fireTick, fireTick + 60] as the run actually played out. Complements the
   * predictive `threats.eta60`, which is evaluated only at the fire tick.
   */
  observedDistinct: number
  observedDistinctLethal: number
  /** Ticks actually observed — < 60 when the run ended inside the window. */
  observedTicks: number
  /** Did the player actually die inside the 1 s window? */
  playerDiedInWindow: boolean
}

export interface BaseLossRecord {
  seed: number
  stageIndex: number
  difficulty: string
  /** Tick at which baseHp hit 0. */
  tick: number
  /** Total ticks the run lasted. */
  runTicks: number
  /** Kind of tank that fired the killing bullet. */
  killerKind: TankKind
  /** How many bullets landed on the base across the whole run. */
  baseHitsTotal: number
  // ---- metric 2 / 3, captured at the destruction instant ----
  livesAtLoss: number
  playerAliveAtLoss: boolean
  distToBaseAtLoss: number
  playerHpAtLoss: number
  playerLevelAtLoss: number
  /** Live, fully-spawned enemies on the field at the loss instant. */
  liveEnemiesAtLoss: number
  // ---- metrics 4 / 5 / 6, captured at the fire tick ----
  fire?: FireContext
}

export interface RunResult {
  seed: number
  stageIndex: number
  difficulty: string
  outcome: 'stage_clear' | 'base_destroyed' | 'lives_exhausted' | 'timeout'
  ticks: number
  loss?: BaseLossRecord
}

interface BaseHit {
  tick: number
  bulletId: number
  ownerKind: TankKind
  baseHpBefore: number
}

/** Resolve `damageBase` on the Simulation prototype chain (mixin-safe). */
function findDamageBase(sim: Simulation): (b: Bullet) => void {
  let p: object | null = Object.getPrototypeOf(sim)
  while (p) {
    if (Object.prototype.hasOwnProperty.call(p, 'damageBase')) {
      return (p as Record<string, unknown>)['damageBase'] as (b: Bullet) => void
    }
    p = Object.getPrototypeOf(p)
  }
  throw new Error('base-loss-run: damageBase not found on the Simulation prototype chain')
}

/** Would this bullet kill the player outright? Mirrors SuicideReturn. */
function bulletWouldKill(p: Tank, b: Bullet): boolean {
  if (p.isPlayer && (p.level ?? 0) >= PLAYER_PROGRESSION.maximumLevel) return false
  return b.damage >= p.hp
}

/** Is this enemy bullet aligned with and closing on the player? */
function threatens(b: Bullet, pcx: number, pcy: number): { hit: boolean; etaTicks: number } {
  const bcx = b.x + b.w / 2
  const bcy = b.y + b.h / 2
  const vertical = b.dir === 'up' || b.dir === 'down'
  const aligned = vertical ? Math.abs(bcx - pcx) < ALIGN_PX : Math.abs(bcy - pcy) < ALIGN_PX
  if (!aligned) return { hit: false, etaTicks: Infinity }
  const approaching =
    (b.dir === 'down' && bcy < pcy) ||
    (b.dir === 'up' && bcy > pcy) ||
    (b.dir === 'right' && bcx < pcx) ||
    (b.dir === 'left' && bcx > pcx)
  if (!approaching) return { hit: false, etaTicks: Infinity }
  const dist = vertical ? Math.abs(bcy - pcy) : Math.abs(bcx - pcx)
  return { hit: true, etaTicks: b.speed > 0 ? dist / b.speed : Infinity }
}

/** Census of enemy bullets bearing down on the player, right now. */
function censusThreats(world: World, p: Tank, excludeBulletId: number): ThreatCounts {
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  const out: ThreatCounts = { all: 0, lethal: 0, eta60: 0, eta60Lethal: 0 }
  for (const b of world.bullets) {
    if (!b.alive || b.isPlayer) continue
    if (b.id === excludeBulletId) continue
    const { hit, etaTicks } = threatens(b, pcx, pcy)
    if (!hit) continue
    const lethal = bulletWouldKill(p, b)
    out.all++
    if (lethal) out.lethal++
    if (etaTicks <= ONE_SECOND_TICKS) {
      out.eta60++
      if (lethal) out.eta60Lethal++
    }
  }
  return out
}

function manhattanCellsToBase(t: Tank): number {
  const pcx = t.x + t.w / 2
  const pcy = t.y + t.h / 2
  const bcx = BASE_POS.col * CELL + CELL
  const bcy = BASE_POS.row * CELL + CELL
  return Math.round((Math.abs(pcx - bcx) + Math.abs(pcy - bcy)) / CELL)
}

function buildWorld(
  seed: number,
  difficulty: string,
): { world: World; sim: Simulation; input: GodAIInput } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, godRng)
  const sim = new Simulation(world, input)
  return { world, sim, input }
}

export interface RunOpts {
  seed: number
  stage: StageData
  stageIndex: number
  difficulty: string
  maxTicks?: number
  godAIParams?: GodAIParams
}

/**
 * Pass 1 — play the run out, remember every bullet's muzzle tick, and capture
 * the World the instant the base dies.
 */
function pass1(opts: RunOpts): RunResult & { killBulletId?: number; fireTick?: number } {
  const { world, sim, input } = buildWorld(opts.seed, opts.difficulty)
  const maxTicks = opts.maxTicks ?? 36000

  const baseHits: BaseHit[] = []
  /** bullet id → tick it was fired. Pruned as bullets die. */
  const fireTickById = new Map<number, number>()
  let atLoss: {
    lives: number
    playerAlive: boolean
    dist: number
    hp: number
    level: number
    liveEnemies: number
  } | null = null

  const orig = findDamageBase(sim)
  let tick = 0
  ;(sim as unknown as Record<string, unknown>)['damageBase'] = function (this: unknown, b: Bullet) {
    const hpBefore = world.baseHp
    baseHits.push({ tick, bulletId: b.id, ownerKind: b.ownerKind, baseHpBefore: hpBefore })
    // Snapshot the World *before* delegating: after the call the base is gone
    // and the Simulation may already have flipped state to 'gameover'.
    const p = world.player
    let liveEnemies = 0
    for (const t of world.tanks) if (!t.isPlayer && t.alive && t.spawnTimer <= 0) liveEnemies++
    atLoss = {
      lives: world.lives,
      playerAlive: !!p?.alive,
      dist: p ? manhattanCellsToBase(p) : -1,
      hp: p?.hp ?? 0,
      level: p?.level ?? 0,
      liveEnemies,
    }
    return orig.call(this, b)
  }

  world.loadStageData(opts.stage, opts.stageIndex)
  input.reset()

  let outcome: RunResult['outcome'] = 'timeout'
  for (tick = 1; tick <= maxTicks; tick++) {
    sim.tick()
    input.endFrame()
    // Record EVERY bullet's muzzle tick, player shots included: the God AI
    // still occasionally blows up its own base through the protection ring
    // (killerKind === 'player', cf. §74/§79), and those losses need the same
    // fire-tick anchoring as enemy ones.
    for (const e of world.consumeEvents()) {
      if (e.type === 'bullet_fired') fireTickById.set(e.bullet.id, tick)
    }
    if (world.state === 'stageclear' || world.state === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (world.state === 'gameover') {
      outcome = world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'
      break
    }
  }
  const runTicks = Math.min(tick, maxTicks)

  const base = {
    seed: opts.seed,
    stageIndex: opts.stageIndex,
    difficulty: opts.difficulty,
    outcome,
    ticks: runTicks,
  }
  if (outcome !== 'base_destroyed' || baseHits.length === 0 || !atLoss) return base

  const kill = baseHits[baseHits.length - 1]
  const snap = atLoss as NonNullable<typeof atLoss>
  const loss: BaseLossRecord = {
    seed: opts.seed,
    stageIndex: opts.stageIndex,
    difficulty: opts.difficulty,
    tick: kill.tick,
    runTicks,
    killerKind: kill.ownerKind,
    baseHitsTotal: baseHits.length,
    livesAtLoss: snap.lives,
    playerAliveAtLoss: snap.playerAlive,
    distToBaseAtLoss: snap.dist,
    playerHpAtLoss: snap.hp,
    playerLevelAtLoss: snap.level,
    liveEnemiesAtLoss: snap.liveEnemies,
  }
  return { ...base, loss, killBulletId: kill.bulletId, fireTick: fireTickById.get(kill.bulletId) }
}

/**
 * Pass 2 — replay the identical run and snapshot the World at `fireTick`,
 * then watch the 1 s window that follows.
 */
function pass2(opts: RunOpts, killBulletId: number, fireTick: number): FireContext | undefined {
  const { world, sim, input } = buildWorld(opts.seed, opts.difficulty)
  const maxTicks = opts.maxTicks ?? 36000
  world.loadStageData(opts.stage, opts.stageIndex)
  input.reset()

  let ctx: FireContext | undefined
  const observed = new Set<number>()
  const observedLethal = new Set<number>()
  let observedTicks = 0
  let playerDiedInWindow = false
  const windowEnd = fireTick + ONE_SECOND_TICKS

  for (let tick = 1; tick <= maxTicks; tick++) {
    sim.tick()
    input.endFrame()
    const events = world.consumeEvents()

    if (tick === fireTick) {
      const p = world.player
      ctx = {
        tick: fireTick,
        flightTicks: 0,
        playerAlive: !!p?.alive,
        playerSpawnTimer: p?.spawnTimer ?? 0,
        hp: p?.hp ?? 0,
        maxHp: p?.maxHp ?? 0,
        hitsRemaining: p ? Math.max(0, Math.ceil(p.hp / REFERENCE_DAMAGE)) : 0,
        playerLevel: p?.level ?? 0,
        lives: world.lives,
        distToBase: p ? manhattanCellsToBase(p) : -1,
        threats:
          p && p.alive
            ? censusThreats(world, p, killBulletId)
            : { all: 0, lethal: 0, eta60: 0, eta60Lethal: 0 },
        killBulletThreatensPlayer: false,
        observedDistinct: 0,
        observedDistinctLethal: 0,
        observedTicks: 0,
        playerDiedInWindow: false,
      }
      if (p && p.alive) {
        const kb = world.bullets.find((b) => b.id === killBulletId)
        if (kb) {
          ctx.killBulletThreatensPlayer = threatens(kb, p.x + p.w / 2, p.y + p.h / 2).hit
        }
      }
    }

    if (ctx && tick >= fireTick && tick <= windowEnd) {
      observedTicks = tick - fireTick
      const p = world.player
      if (p && p.alive && p.spawnTimer <= 0) {
        const pcx = p.x + p.w / 2
        const pcy = p.y + p.h / 2
        for (const b of world.bullets) {
          if (!b.alive || b.isPlayer || b.id === killBulletId) continue
          if (!threatens(b, pcx, pcy).hit) continue
          observed.add(b.id)
          if (bulletWouldKill(p, b)) observedLethal.add(b.id)
        }
      }
      for (const e of events) {
        if (e.type === 'tank_destroyed' && e.tank.isPlayer) playerDiedInWindow = true
      }
    }

    if (world.state === 'stageclear' || world.state === 'victory' || world.state === 'gameover') {
      if (ctx) ctx.flightTicks = tick - fireTick
      break
    }
    if (ctx && tick >= windowEnd && world.baseHp <= 0) break
  }

  if (ctx) {
    ctx.observedDistinct = observed.size
    ctx.observedDistinctLethal = observedLethal.size
    ctx.observedTicks = observedTicks
    ctx.playerDiedInWindow = playerDiedInWindow
  }
  return ctx
}

/** Full forensics for one (seed, stage, difficulty) cell. */
export function runForensics(opts: RunOpts): RunResult {
  const r = pass1(opts)
  if (r.outcome !== 'base_destroyed' || !r.loss) {
    return {
      seed: r.seed,
      stageIndex: r.stageIndex,
      difficulty: r.difficulty,
      outcome: r.outcome,
      ticks: r.ticks,
    }
  }
  if (r.killBulletId !== undefined && r.fireTick !== undefined) {
    const fire = pass2(opts, r.killBulletId, r.fireTick)
    if (fire) {
      fire.flightTicks = r.loss.tick - r.fireTick
      r.loss.fire = fire
    }
  }
  return {
    seed: r.seed,
    stageIndex: r.stageIndex,
    difficulty: r.difficulty,
    outcome: r.outcome,
    ticks: r.ticks,
    loss: r.loss,
  }
}
