// ================================================================
// TankFactory — tank entity construction (plan/refactor.agy.md §1.3
// Phase B). Extracted verbatim from World.createTank so the World class
// stops being a 100-line entity factory on top of its state-keeper job.
//
// The factory is a pure function of (World, kind, position, dir, slot):
// it reads config + the World's current player levels / rules / RNG and
// returns a fully-formed Tank. It mutates exactly two World counters that
// are part of construction itself: `spawnSeqCounter` (enemy brain birth
// order) and the RNG stream draws. All callers go through
// `world.createTank(...)`, which delegates here.
// ================================================================
import { CELL, TANK, RESPAWN_SHIELD_MS, TURN_SENTINEL_MS } from '../constants'
import type { Direction } from '../constants'
import type { AIState, GoalType, Tank, TankKind } from '../types'
import { resolveProfile, profileToStats } from '../config/combat'
import { hasStarPerk } from '../config/rules'
import { rollSpeedJitter } from '../config/speed'
import { INTELLIGENCE_LEVELS, STRATEGIC_INTERVAL_MS, COMMANDER_INTERVAL_MS } from '../ai/config'
import { genId, type World } from './World'

/**
 * Build a tank entity.
 *
 * @param playerSlot Which player tank is being created (1 = P1, 2 = P2/God AI).
 *   Only meaningful when `kind === 'player'`: it selects which star level
 *   drives the spawned stats (playerLevel for P1, playerLevel2 for P2). Enemy
 *   and ally tanks ignore it.
 */
export function createTank(
  world: World,
  kind: TankKind,
  x: number,
  y: number,
  dir: Direction,
  playerSlot = 1,
): Tank {
  // Combat Capability System: stats come from the tank's profile, not
  // hardcoded numbers. Player profiles scale with star level; enemies use
  // their fixed archetype profile (modified only when promoted to elite).
  const isPlayer = kind === 'player'
  // P2 (God AI / Lie-Back-Win-Mode) must use its OWN star level so its
  // combat power (HP/speed/fire) tracks the stars IT collects, not P1's.
  const playerLevel = isPlayer ? (playerSlot === 2 ? world.playerLevel2 : world.playerLevel) : 0
  const profile = resolveProfile(kind, playerLevel)
  const stats = profileToStats(profile, kind, playerLevel, world.rules)
  // Enemy combat stats (including HP/armor) are fixed per archetype and never
  // scaled by difficulty — difficulty only changes the tier distribution that
  // enemies are rolled from (plan/AI-Tier-System-Revision.md §5). Scaling
  // enemy HP here would "enhance enemy power", which is explicitly forbidden.
  const hp = stats.maxHp

  // Functional star ladder: the player's `fastBullet` perk (classic) is a
  // multiplier on the base bullet speed. Apply it at spawn so a stage-
  // persistent star level is correct, not just on star pickup (Simulation).
  let bulletSpeed = stats.bulletSpeed
  if (
    isPlayer &&
    world.rules.starModel === 'functional' &&
    hasStarPerk(world.rules, playerLevel, 'fastBullet')
  ) {
    bulletSpeed *= world.rules.fastBulletMult
  }

  // Enemy brains are initialized here (on the World — no hidden state).
  // The Tactical Intelligence Framework reads/writes these fields every tick.
  // `level` is a PLACEHOLDER ('rookie'); the real tier is rolled at spawn
  // time in `Simulation.updateSpawning` (plan §5) which overwrites
  // `aiState.level` / `isCommander` there. `spawnSeq` is stamped from
  // the World's monotonic counter so command authority is derivable.
  let aiState: AIState | undefined
  if (kind !== 'player') {
    const base = world.tileMap.getBasePos()
    const placeholder = INTELLIGENCE_LEVELS['rookie']
    aiState = {
      level: 'rookie',
      isCommander: false,
      spawnSeq: world.spawnSeqCounter++,
      thinkTimer: 200 + world.rng.next() * 600,
      fireTimer: 400 + world.rng.next() * 600,
      currentDir: dir,
      tacticalGoal: 'advance' as GoalType,
      targetX: base ? base.x + CELL : x + TANK / 2,
      targetY: base ? base.y + CELL : y + TANK / 2,
      strategicTimer: STRATEGIC_INTERVAL_MS * (0.8 + world.rng.next() * 0.4),
      strategicGoal: 'attackBase' as GoalType,
      reactionTimer: placeholder.reactionTime,
      dodgeLock: 0,
      vertOnlyTicks: 0,
      commanderTimer: COMMANDER_INTERVAL_MS,
      directive: 'none',
      directiveAge: 1e9,
      directiveSeq: 0,
      directiveCompliant: false,
    }
  }

  return {
    id: genId(),
    x,
    y,
    w: TANK,
    h: TANK,
    dir,
    alive: true,
    kind,
    // Per-instance speed jitter (±5%): identical archetypes don't move in
    // lockstep, but it's drawn from world.rng so it stays deterministic.
    speed: stats.speed * (world.rules.speedJitter ? rollSpeedJitter(world.rng) : 1),
    hp,
    maxHp: hp,
    bulletPower: stats.bulletPower,
    damage: stats.damage,
    bulletSpeed,
    fireCooldown: stats.fireCooldown,
    nextFireInterval: stats.fireCooldown,
    fireCount: 0,
    lastFire: 0,
    moving: false,
    vx: 0,
    vy: 0,
    spawnTimer: 1000,
    // §86c: Initialize turn cooldown tracking. prevMoveDir = dir so the
    // first frame doesn't register as a turn. lastTurnMs = -9999 so
    // the first real turn is always allowed.
    prevMoveDir: dir,
    lastTurnMs: TURN_SENTINEL_MS,
    level: isPlayer ? playerLevel : 0,
    shieldTimer: kind === 'player' ? RESPAWN_SHIELD_MS : 0,
    isPlayer: kind === 'player',
    allegiance: kind === 'player' ? 'player' : 'enemy',
    profile,
    flashTimer: 0,
    hitCount: 0,
    aiState,
    bonus: false,
  }
}
