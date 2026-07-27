import type { World } from './World'
import type { Tank, Bullet, PowerUpType, IntelligenceLevel, TankKind } from '../types'
import {
  CELL,
  TANK,
  BULLET,
  FIELD,
  TICK_MS,
  MAX_ENEMIES_ALIVE,
  ENEMIES_PER_STAGE,
  DIR_VECTORS,
  Direction,
  ICE_ACCEL_TRACTION,
  ICE_DECEL_TRACTION,
  RESPAWN_SHIELD_MS,
  ENEMY_SPAWNS,
  POWERUP_TIMEOUT_MS,
  POWERUP_PICKUP_WINDOW_MS,
  POWERUP_PICKUP_END_DELAY_MS,
  STAGE_CLEAR_DELAY_MS,
  POWERUP_DURATION_MS,
  FENCE_STEEL_COUNT,
  FENCE_DURATION_FRAMES,
  BOAT_DURATION_MS,
  BASE_POS,
  GRID,
} from '../constants'
import { resolveProfile, profileToStats, PLAYER_PROGRESSION } from '../config/combat'
import { killScore, stageClearScore, ITEM_SCORE, SCORE_DROP_INTERVAL } from '../config/score'
import {
  SUPER_POWERUP_DROP_CHANCE,
  SUPER_POWERUP_TYPES,
  FRENZY_SHOTS,
  SACRIFICE_BASE_RADIUS_CELLS,
} from '../config/powerups'
import { applyEliteModifier } from '../config/combat'
import { rollSpeedJitter, spawnBulletSpeedPxPerTick } from '../config/speed'
import { nextFireIntervalMs } from '../config/fire-rate'
import { genId } from './World'
import type { InputLike } from './Input'
import { TacticalIntelligence } from '../ai/TacticalIntelligence'
import { rollTier, COMMANDER_ALIVE_CAP } from '../ai/config'
import { snap, aabb } from '../utils/helpers'

// Spawn points derived from the design constants (ENEMY_SPAWNS, in tile
// coords). The third authentic point is col 6 (x = 96), NOT the old hardcoded
// col 24 (x = 384) which jammed a tank against the right wall (FIELD = 416,
// tank = 32 ⇒ a tank at x = 384 occupies x = 384..416 and can only move
// down/left; two such tanks meeting at the edge deadlock with zero free
// directions). See fix in updateSpawning().
const ENEMY_SPAWN_POINTS = ENEMY_SPAWNS.map((s) => ({ x: s.col * CELL, y: s.row * CELL }))

/** 天降神兵 guard lifespan: 2 minutes at 60 ticks/s (§31 Phase 2). */
const GUARD_LIFESPAN_FRAMES = 120 * 60
/** Guard kinds are randomly chosen; all use normal enemy combat stats. */
const GUARD_KINDS: TankKind[] = ['basic', 'fast', 'power', 'armor']
/** Accompanying "balance" enemies use a lighter pool. */
const EXTRA_ENEMY_KINDS: TankKind[] = ['basic', 'fast', 'power']

/**
 * In-grid cells forming the 1-tile-thick protective ring around the 2×2 base
 * (base at BASE_POS col/row). Shared by applyFencePowerUp (place steel) and
 * updateFence/ expireFence (revert to brick) so both always agree on which
 * cells are "the ring". The bottom edge (row BASE_POS.row + 2) is off-grid
 * (GRID=26, base at row 24), so the ring is the 3 in-grid sides (top + left +
 * right) — exactly where the original permanent fence placed its steel.
 */
function baseRingPositions(): Array<{ col: number; row: number }> {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const cells: Array<{ col: number; row: number }> = []
  const consider = (col: number, row: number) => {
    if (col >= 0 && col < GRID && row >= 0 && row < GRID) cells.push({ col, row })
  }
  // Top edge
  for (let c = bc - 1; c <= bc + 2; c++) consider(c, br - 1)
  // Left edge (mid rows only; corners already covered by top edge)
  consider(bc - 1, br)
  consider(bc - 1, br + 1)
  // Right edge (mid rows only)
  consider(bc + 2, br)
  consider(bc + 2, br + 1)
  return cells
}

/** Power-up types a bonus enemy can drop (module-level to avoid per-drop allocation). */
const POWERUP_TYPES: PowerUpType[] = [
  'star',
  'bomb',
  'shield',
  'freeze',
  'tank',
  'fence',
  'boat',
]

/** Normal pool with `boat` removed — used on stages that have no water, since
 *  the boat (amphibious) power-up is useless without water to cross. */
const POWERUP_TYPES_NO_BOAT: PowerUpType[] = POWERUP_TYPES.filter((t) => t !== 'boat')

/**
 * Simulation — the only layer allowed to modify the World.
 * Runs all game systems in a fixed timestep.
 */
export class Simulation {
  world: World
  input: InputLike
  /** Tactical Intelligence Framework — owns all enemy decision-making. */
  private ai: TacticalIntelligence

  constructor(world: World, input: InputLike) {
    this.world = world
    this.input = input
    this.ai = new TacticalIntelligence()
  }

  /** Run one simulation tick (1/60s) */
  tick(): void {
    const w = this.world
    w.frame++

    if (w.state === 'playing') {
      this.updatePlaying()
    } else if (w.state === 'stageclear') {
      this.updateStageClear()
    } else if (w.state === 'gameover') {
      this.updateGameOver()
    }
  }

  // ================================================================
  // Playing state
  // ================================================================

  private updatePlaying(): void {
    const w = this.world

    // Run statistics — total play time advances only while playing.
    w.playTimeMs += 1000 / 60

    // Update timers
    if (w.freezeTimer > 0) w.freezeTimer -= 1000 / 60
    if (w.spawnTimer > 0) w.spawnTimer -= 1000 / 60
    if (w.pickupWindowTimer > 0) w.pickupWindowTimer -= TICK_MS

    // Update player boat timer
    if (w.player && w.player.boatTimer && w.player.boatTimer > 0) {
      w.player.boatTimer -= 1000 / 60
      if (w.player.boatTimer < 0) w.player.boatTimer = 0
    }

    // Update spawn animations
    this.updateSpawnTimers()

    // Spawn enemies
    this.updateSpawning()

    // Player input
    this.updatePlayer()

    // Enemy AI
    this.updateEnemyAI()

    // Allied guard AI (天降神兵, §31 Phase 2) — runs before movement so the
    // guard's intent (dir/moving) is applied this tick.
    this.updateGuards()

    // Fence power-up: revert steel ring to brick when its timer expires.
    this.updateFence()

    // Movement
    this.updateMovement()

    // Bullets
    this.updateBullets()

    // Power-ups
    this.updatePowerUps()

    // Explosions & popups
    this.updateExplosions()
    this.updatePopups()

    // Check game conditions
    this.checkConditions()

    // Cleanup
    w.removeDeadEntities()
  }

  // ================================================================
  // Spawn System
  // ================================================================

  private updateSpawnTimers(): void {
    const w = this.world
    for (const tank of w.allTanks) {
      if (tank.spawnTimer > 0) {
        tank.spawnTimer -= 1000 / 60
        if (tank.spawnTimer < 0) tank.spawnTimer = 0
      }
      if (tank.shieldTimer && tank.shieldTimer > 0) {
        tank.shieldTimer -= 1000 / 60
        if (tank.shieldTimer < 0) tank.shieldTimer = 0
      }
      if (tank.boatTimer && tank.boatTimer > 0) {
        tank.boatTimer -= 1000 / 60
        if (tank.boatTimer < 0) tank.boatTimer = 0
      }
      if (tank.flashTimer !== undefined && tank.flashTimer > 0) {
        tank.flashTimer -= 1000 / 60
      }
    }
  }

  private updateSpawning(): void {
    const w = this.world
    if (w.spawnQueue.length === 0) return
    if (w.enemyCount >= MAX_ENEMIES_ALIVE) return
    if (w.spawnTimer > 0) return

    const entry = w.spawnQueue[0]
    const n = ENEMY_SPAWN_POINTS.length

    // Try every spawn point in rotation and use the first one that is clear of
    // tanks. Previously the code retried only the *current* point (decrementing
    // the index on failure), so a single occupied/stuck point would stall ALL
    // enemy spawns forever. Now an occupied point is simply skipped and the next
    // one is tried; if none are clear we just retry next frame and rotate the
    // start index so we don't keep re-checking the same blocked point first.
    for (let i = 0; i < n; i++) {
      const idx = (w.spawnPointIndex + i) % n
      const pt = ENEMY_SPAWN_POINTS[idx]

      // Skip spawn points overlapping blocking terrain (brick/steel/water/base).
      // Several authentic stages place terrain on top of a spawn cell — e.g.
      // col 6 is steel on stage 2, brick on stages 9/19/21, water on stages
      // 20/26/31, steel on stage 25. Without this check the enemy was created
      // *inside* that terrain and then jammed: every candidate move overlapped
      // the very cell it stood on, so rectHitsTerrain() rejected all four
      // directions and the tank sat at the spawn point forever. Treat a
      // terrain-blocked point exactly like an occupied one — skip it and fall
      // through to the next clear point (col 0 / col 12 are always clear on
      // every stage, so a spawn always succeeds).
      if (w.rectHitsTerrain(pt.x, pt.y, TANK, TANK)) continue

      // Check if spawn area is clear of other tanks (inline rect — no per-retry allocation)
      let canSpawn = true
      for (const tank of w.allTanks) {
        if (aabb(pt.x, pt.y, TANK, TANK, tank.x, tank.y, tank.w, tank.h)) {
          canSpawn = false
          break
        }
      }
      if (!canSpawn) continue

      // Create the enemy tank (base profile/stats; tier & boost applied after).
      const tank = w.createTank(entry.kind, pt.x, pt.y, 'down')
      tank.bonus = entry.bonus

      // ---- Spawn-time tier roll (plan §5) ----
      // Decide the FINAL tier BEFORE finalizing stats so a cap downgrade can
      // veto the +15% boost cleanly (§5.3 [D10-fix]).
      const remainingSpawns = ENEMIES_PER_STAGE - w.enemiesSpawned
      let tier: IntelligenceLevel
      if (w.commanderQuotaRemaining > 0 && remainingSpawns <= w.commanderQuotaRemaining) {
        // Floor guarantee: force a Commander attempt so the difficulty's
        // minimum commander count is always satisfiable (§5.1 [D9-fix]).
        // Forced rolls consume NO RNG draw (tier-roll gate spirit).
        tier = 'commander'
        w.commanderQuotaRemaining -= 1
      } else {
        tier = rollTier(w.difficultyKey, w.rng)
        // Count a natural commander roll against the floor only while it is
        // still outstanding. The floor is a MINIMUM guarantee, so the counter
        // clamps at 0 (never negative) once satisfied — extra natural
        // commander spawns beyond the floor are just bonus, not debt.
        if (tier === 'commander' && w.commanderQuotaRemaining > 0) w.commanderQuotaRemaining -= 1
      }

      let isCommander = false
      let finalLevel = tier
      if (tier === 'commander') {
        // Cap: at most COMMANDER_ALIVE_CAP commander-tier tanks alive on
        // screen (active + inactive both count, §5.1). A roll against a
        // full cap downgrades to ACTUAL Veteran — no boost, no crown.
        let aliveCmd = 0
        for (const t of w.tanks) {
          if (t.alive && t.aiState?.level === 'commander') aliveCmd++
        }
        if (aliveCmd >= COMMANDER_ALIVE_CAP) {
          finalLevel = 'veteran'
        } else {
          isCommander = true
        }
      }

      // Apply the +15% combat boost to EVERY commander-tier spawn (incl.
      // inactive ones), per §5.3 [D10]. A cap-downgraded Veteran gets
      // nothing — decide tier first, then boost conditionally.
      if (isCommander) {
        const eliteProfile = applyEliteModifier(
          tank.profile ?? resolveProfile(tank.kind, 0),
          tank.kind,
        )
        tank.profile = eliteProfile
        const eliteStats = profileToStats(eliteProfile, tank.kind, tank.level ?? 0)
        tank.speed = eliteStats.speed
        tank.bulletSpeed = eliteStats.bulletSpeed
        tank.bulletPower = eliteStats.bulletPower
        tank.damage = eliteStats.damage
        tank.fireCooldown = eliteStats.fireCooldown
        tank.nextFireInterval = eliteStats.fireCooldown
        tank.maxHp = eliteStats.maxHp
        tank.hp = eliteStats.maxHp
      }

      // Stamp the rolled tier onto the brain (createTank used a placeholder).
      // commanderTimer stays at its createTank default; Simulation sets the
      // 1s office delay when this tank BECOMES the active commander.
      if (tank.aiState) {
        tank.aiState.level = finalLevel
        tank.aiState.isCommander = isCommander
      }

      w.tanks.push(tank)
      w.spawnQueue.shift()
      w.enemiesSpawned++
      w.spawnTimer = 1500 // 1.5s between spawns
      w.spawnPointIndex = (idx + 1) % n
      return
    }

    // All points blocked this frame — advance the start index and retry next frame.
    w.spawnPointIndex = (w.spawnPointIndex + 1) % n
  }

  // ================================================================
  // Player System
  // ================================================================

  private updatePlayer(): void {
    const w = this.world
    const p = w.player
    if (!p || !p.alive) return
    if (p.spawnTimer > 0) return // still spawning

    // --- 狂暴宣泄 barrage in progress: player is locked (no move / turn /
    //     other items). Auto-fire only. ---
    if (w.frenzyTimer > 0) {
      this.updateFrenzy(p)
      return
    }

    // --- Active super-item release (F5 天降神兵 / F6 狂暴宣泄) ---
    // 天降神兵 is Phase 2 (ally AI + faction); in Phase 1 the super pool
    // excludes 'guard', so guardStock is always 0 and this is a no-op.
    if (this.input.wasItemPressed('guard') && w.guardStock > 0) {
      this.activateGuard(p)
    }
    if (this.input.wasItemPressed('frenzy') && w.frenzyStock > 0) {
      this.activateFrenzy(p)
    }

    // Movement
    const dir = this.input.getMoveDirection()
    if (dir !== null) {
      p.dir = dir
      p.moving = true
    } else {
      p.moving = false
    }

    // Firing
    if (this.input.isFiring()) {
      this.tryFire(p)
    }
  }

  /**
   * Advance an active 狂暴宣泄 barrage. The player is locked to `frenzyDir`
   * and cannot move/turn/use other items (updatePlayer returns before reading
   * movement). Shells fire at 1/5 of the player's normal fire interval using
   * the player's CURRENT bullet stats (star buff included). Driven by the same
   * `frame * (1000/60)` clock as tryFire so it is deterministic/snapshot-safe.
   */
  private updateFrenzy(p: Tank): void {
    const w = this.world
    const now = w.frame * (1000 / 60)
    p.dir = w.frenzyDir
    p.moving = false
    while (w.frenzyShotsLeft > 0 && now - w.frenzyLastFire >= w.frenzyInterval) {
      this.spawnFrenzyShot(p)
      w.frenzyShotsLeft -= 1
      w.frenzyLastFire += w.frenzyInterval
    }
    w.frenzyTimer -= 1000 / 60
    if (w.frenzyTimer <= 0 || w.frenzyShotsLeft <= 0) {
      w.frenzyTimer = 0
      w.frenzyShotsLeft = 0
    }
  }

  /** Fire one 狂暴宣泄 shell (player's current stats, locked direction). */
  private spawnFrenzyShot(p: Tank): void {
    const w = this.world
    const v = DIR_VECTORS[w.frenzyDir]
    const bx = p.x + p.w / 2 - BULLET / 2 + v.dx * (p.w / 2)
    const by = p.y + p.h / 2 - BULLET / 2 + v.dy * (p.h / 2)
    const bullet: Bullet = {
      id: genId(),
      x: bx,
      y: by,
      w: BULLET,
      h: BULLET,
      dir: w.frenzyDir,
      alive: true,
      ownerId: p.id,
      ownerKind: p.kind,
      isPlayer: true,
      allegiance: 'player',
      speed: spawnBulletSpeedPxPerTick(p.kind, p.level ?? 0, w.bulletSeq++, w.frame),
      power: p.bulletPower,
      damage: p.damage,
    }
    w.addBullet(bullet)
    w.pushEvent({ type: 'bullet_fired', bullet })
  }

  /** Activate a 狂暴宣泄 barrage (consume one from inventory). */
  private activateFrenzy(p: Tank): void {
    const w = this.world
    w.frenzyStock--
    const interval = Math.max(1, p.nextFireInterval / 5)
    w.frenzyInterval = interval
    w.frenzyShotsLeft = FRENZY_SHOTS
    w.frenzyDir = p.dir
    // Fire the first shell immediately on the next tick.
    w.frenzyLastFire = w.frame * (1000 / 60) - interval
    w.frenzyTimer = FRENZY_SHOTS * interval
  }

  /**
   * Activate 天降神兵 (DECISIONS.md §31 Phase 2): summon a base guard ally and
   * (for balance) accompanying "balance" enemies that are outside the per-stage
   * 20-enemy cap. When no guards are currently active, 1 accompanying enemy is
   * spawned; once 1+ guards are active, each new summon adds 2 (one already
   * alive + the one being summoned counts as "active" only after this check,
   * so the FIRST guard → 1, subsequent → 2).
   */
  private activateGuard(p: Tank): void {
    const w = this.world
    if (w.guardStock <= 0) return

    let activeGuards = 0
    for (const a of w.allies) {
      if (a.alive && a.spawnTimer <= 0) activeGuards++
    }
    const extraCount = activeGuards === 0 ? 1 : 2

    w.guardStock--
    this.spawnGuard(p)
    for (let i = 0; i < extraCount; i++) this.spawnAccompanyingEnemy(p)
  }

  /** Spawn one allied guard of a random type beside the base. */
  private spawnGuard(p: Tank): void {
    const w = this.world
    const kind = GUARD_KINDS[Math.floor(w.rng.next() * GUARD_KINDS.length)]
    const base = w.tileMap.getBasePos()
    // Spawn on the side of the base OPPOSITE the player (spec): if the player is
    // left of the base, spawn right; otherwise left.
    let side: 'left' | 'right' = 'right'
    if (base) {
      const baseCx = base.x + CELL
      const playerCx = p.x + p.w / 2
      side = playerCx < baseCx ? 'right' : 'left'
    }
    const pos = this.baseSideSpawnCell(side)
    const tank = w.createTank(kind, pos.x, pos.y, 'up')
    // Promotion to third faction (§31 Phase 2).
    tank.allegiance = 'ally'
    tank.isPlayer = false
    tank.spawnTimer = 1000
    if (tank.aiState) {
      // Commander-grade brain so the guard fights competently; pinned to a
      // base-defence posture. It is NEVER considered for enemy command
      // authority (recomputeActiveCommander only scans world.tanks).
      tank.aiState.level = 'commander'
      tank.aiState.isCommander = true
      tank.aiState.strategicGoal = 'defendBase'
      tank.aiState.tacticalGoal = 'defendBase'
      const bx = base ? base.x + CELL : pos.x
      const by = base ? base.y + CELL : pos.y
      tank.aiState.targetX = bx
      tank.aiState.targetY = by
    }
    // 2-minute lifespan (absolute frame).
    tank.guardExpireFrame = w.frame + GUARD_LIFESPAN_FRAMES
    w.allies.push(tank)
  }

  /**
   * Find a clear spawn cell on the requested side of the base (scanning rows
   * around the base for terrain- and tank-free space). Falls back to the base's
   * own column if every candidate is blocked.
   */
  private baseSideSpawnCell(side: 'left' | 'right'): { x: number; y: number } {
    const w = this.world
    const base = w.tileMap.getBasePos()
    const fallback = { x: CELL * 8, y: CELL * 24 }
    if (!base) return fallback
    const baseCol = Math.floor(base.x / CELL)
    const baseRow = Math.floor(base.y / CELL)
    // One cell to the right of the 2×2 base (col baseCol+2) or one to the left
    // (col baseCol-1).
    const col = side === 'right' ? baseCol + 2 : baseCol - 1
    for (let r = baseRow - 2; r <= baseRow + 2; r++) {
      const x = col * CELL
      const y = r * CELL
      if (!w.isInBounds(x, y, TANK, TANK)) continue
      if (w.rectHitsTerrain(x, y, TANK, TANK)) continue
      let blocked = false
      for (const t of w.allTanks) {
        if (t.alive && aabb(x, y, TANK, TANK, t.x, t.y, t.w, t.h)) {
          blocked = true
          break
        }
      }
      if (!blocked) return { x, y }
    }
    return { x: col * CELL, y: baseRow * CELL }
  }

  /**
   * Spawn one accompanying "balance" enemy (outside the per-stage 20-cap). Uses
   * the normal enemy spawn points and AI; flagged isExtra so it never counts
   * toward enemiesRemaining / stage clear, but still scores when killed.
   */
  private spawnAccompanyingEnemy(_p: Tank): void {
    const w = this.world
    const pt = this.findClearEnemySpawnPoint()
    if (!pt) return // all spawn points blocked — skip (never force a jam)
    const kind = EXTRA_ENEMY_KINDS[Math.floor(w.rng.next() * EXTRA_ENEMY_KINDS.length)]
    const tank = w.createTank(kind, pt.x, pt.y, 'down')
    tank.isExtra = true
    tank.bonus = false
    // Note: does NOT increment enemiesSpawned / enemiesRemaining — deliberately
    // outside the per-stage progression (§31 Phase 2).
    w.tanks.push(tank)
  }

  /** Pick the first clear enemy spawn point (rotation starts at spawnPointIndex). */
  private findClearEnemySpawnPoint(): { x: number; y: number } | null {
    const w = this.world
    const n = ENEMY_SPAWN_POINTS.length
    for (let i = 0; i < n; i++) {
      const idx = (w.spawnPointIndex + i) % n
      const pt = ENEMY_SPAWN_POINTS[idx]
      if (w.rectHitsTerrain(pt.x, pt.y, TANK, TANK)) continue
      let can = true
      for (const t of w.allTanks) {
        if (t.alive && aabb(pt.x, pt.y, TANK, TANK, t.x, t.y, t.w, t.h)) {
          can = false
          break
        }
      }
      if (can) return pt
    }
    return null
  }

  /**
   * Allied guard AI (天降神兵, §31 Phase 2). A focused "Commander-defend"
   * policy (deterministic via world.rng): seek the nearest enemy, defend the
   * base when none, and fire only when aligned with a target and the line of
   * sight is clear of terrain. Reuses the standard movement/fire primitives so
   * the guard obeys the same collision & friendly-fire rules as everyone else.
   *
   * (Design note: the spec says "use the Commander AI". The enemy tactical
   * pipeline is goaled at ATTACKING the base/player, so running it verbatim on
   * an ally would steer it into the player's base. This dedicated defender
   * policy honours the observable intent — competent, base-defending fire —
   * without that hazard. It can be promoted to the full pipeline later if a
   * 'defendBase'-only goal branch is added.)
   */
  private updateGuards(): void {
    const w = this.world
    for (const g of w.allies) {
      if (!g.alive) continue
      if (g.spawnTimer > 0) continue // still spawning — no intent yet

      // Lifespan expiry → retire the guard (no score, no drops).
      if (g.guardExpireFrame !== undefined && w.frame >= g.guardExpireFrame) {
        g.alive = false
        this.createExplosion(g.x + g.w / 2, g.y + g.h / 2, 'big')
        continue
      }

      const gx = g.x + g.w / 2
      const gy = g.y + g.h / 2

      // Nearest hostile tank.
      let target: Tank | null = null
      let bestD = Infinity
      for (const e of w.tanks) {
        if (!e.alive || e.spawnTimer > 0 || e.allegiance !== 'enemy') continue
        const d = Math.hypot(e.x + e.w / 2 - gx, e.y + e.h / 2 - gy)
        if (d < bestD) {
          bestD = d
          target = e
        }
      }

      let tx = gx
      let ty = gy
      if (target) {
        tx = target.x + target.w / 2
        ty = target.y + target.h / 2
      } else {
        const base = w.tileMap.getBasePos()
        if (base) {
          tx = base.x + CELL
          ty = base.y + CELL
        }
      }

      // Primary-axis direction toward the target (defend-by-intercept).
      const dx = tx - gx
      const dy = ty - gy
      const dir: Direction =
        Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
      g.dir = dir
      g.moving = true

      // Fire when aligned with the target and the LOS is clear of terrain.
      if (target) {
        const ex = target.x + target.w / 2
        const ey = target.y + target.h / 2
        let fireDir: Direction | null = null
        if (Math.abs(ex - gx) < CELL * 0.6 && Math.abs(ey - gy) > CELL * 0.6) {
          fireDir = ey < gy ? 'up' : 'down'
        } else if (Math.abs(ey - gy) < CELL * 0.6 && Math.abs(ex - gx) > CELL * 0.6) {
          fireDir = ex < gx ? 'left' : 'right'
        }
        if (fireDir && this.lineClearForAlly(g, fireDir, target)) {
          g.dir = fireDir
          this.tryFire(g)
        }
      }
    }
  }

  /** True if no brick/steel/base tile lies between the guard and its target. */
  private lineClearForAlly(g: Tank, dir: Direction, target: Tank): boolean {
    const w = this.world
    const v = DIR_VECTORS[dir]
    const sx = g.x + g.w / 2
    const sy = g.y + g.h / 2
    const tx = target.x + target.w / 2
    const ty = target.y + target.h / 2
    const maxDist = Math.hypot(tx - sx, ty - sy)
    for (let d = CELL; d <= maxDist; d += CELL) {
      const cx = sx + v.dx * d
      const cy = sy + v.dy * d
      const col = Math.floor(cx / CELL)
      const row = Math.floor(cy / CELL)
      const tt = w.tileMap.get(col, row)
      if (tt === 'brick' || tt === 'steel' || tt === 'base') return false
    }
    return true
  }

  /**
   * 同归于尽 (DECISIONS.md §31): when the player loses a life, release ALL
   * accumulated sacrifice items at once. Blast radius = 5 + (stock−1) cells,
   * destroying every enemy and every brick wall within it. Enemies killed by
   * the blast use the normal kill accounting (score / killCount /
   * enemiesRemaining), so they count exactly like a regular kill.
   */
  private triggerSacrificeAoE(player: Tank): void {
    const w = this.world
    if (w.sacrificeStock <= 0) return

    const radiusCells = SACRIFICE_BASE_RADIUS_CELLS + (w.sacrificeStock - 1)
    const radiusPx = radiusCells * CELL
    const cx = player.x + player.w / 2
    const cy = player.y + player.h / 2

    // Destroy enemies within radius (normal kill accounting). Allies are
    // friendly — the blast only consumes hostile tanks (§31 Phase 2).
    for (const t of w.tanks) {
      if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
      const tx = t.x + t.w / 2
      const ty = t.y + t.h / 2
      if (Math.hypot(tx - cx, ty - cy) <= radiusPx) {
        t.alive = false
        this.createExplosion(t.x + t.w / 2, t.y + t.h / 2, 'big')
        const gained = killScore(w.difficultyKey, t.aiState?.level, w.stageIndex)
        w.score += gained
        w.enemiesRemaining--
        w.killCount++
        w.addPopup({ id: genId(), x: t.x, y: t.y, text: String(gained), timer: 1500 })
        w.pushEvent({ type: 'tank_destroyed', tank: t, by: 'player' })
      }
    }

    // Destroy brick walls within radius (16×16 cells).
    const c0 = Math.max(0, Math.floor((cx - radiusPx) / CELL))
    const c1 = Math.min(GRID - 1, Math.floor((cx + radiusPx) / CELL))
    const r0 = Math.max(0, Math.floor((cy - radiusPx) / CELL))
    const r1 = Math.min(GRID - 1, Math.floor((cy + radiusPx) / CELL))
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        if (w.tileMap.get(c, r) === 'brick') {
          w.tileMap.destroy(c, r)
        }
      }
    }

    this.createExplosion(cx, cy, 'big')
    w.sacrificeStock = 0
  }

  // ================================================================
  // Enemy AI System
  // ================================================================

  private updateEnemyAI(): void {
    // Recompute command authority ONCE per tick, before the AI layer runs
    // (plan §4). The One-Author invariant: Simulation owns World writes;
    // the AI layer only reads `world.activeCommanderId`.
    this.recomputeActiveCommander()
    // Delegate all enemy decision-making to the Tactical Intelligence
    // Framework. It reads the World (Perception) and writes tank intent
    // (direction / firing) back — never hidden state, never Math.random().
    this.ai.update(this.world, (tank) => this.tryFire(tank))
  }

  /**
   * Derive command authority for this tick (plan §4 [D2][D3]).
   * Active Commander = the alive commander-tier tank with the highest
   * `spawnSeq` (most-recently born); null when none is alive. On a
   * change, the new active tank's `commanderTimer` is overwritten to
   * 1000 ms — its 1s office delay measured from taking office (not
   * from spawn). Succession is automatic: when the active dies, the
   * previously-born survivor is now the argmax and regains command.
   */
  private recomputeActiveCommander(): void {
    const w = this.world
    let bestId: number | null = null
    let bestSeq = -Infinity
    for (const t of w.tanks) {
      if (!t.alive || t.spawnTimer > 0 || !t.aiState) continue
      if (t.aiState.level === 'commander') {
        if (t.aiState.spawnSeq > bestSeq) {
          bestSeq = t.aiState.spawnSeq
          bestId = t.id
        }
      }
    }
    const prev = w.activeCommanderId
    w.activeCommanderId = bestId
    if (bestId !== null && bestId !== prev) {
      const active = w.tanks.find((t) => t.id === bestId)
      if (active?.aiState) active.aiState.commanderTimer = 1000
    }
  }

  // ================================================================
  // Movement System
  // ================================================================

  private updateMovement(): void {
    const w = this.world
    for (const tank of w.allTanks) {
      if (!tank.alive || tank.spawnTimer > 0) continue
      // A non-moving tank with residual ice velocity must still be simulated
      // so it keeps gliding to a stop; only a fully-stopped, idle tank is skipped.
      if (!tank.moving && tank.vx === 0 && tank.vy === 0) continue

      // Enemy freeze — a frozen tank can't act, so bleed off any momentum and skip.
      if (!tank.isPlayer && w.freezeTimer > 0) {
        tank.vx = 0
        tank.vy = 0
        continue
      }

      // ---- Velocity / ice-momentum integration ----
      // Desired velocity comes from the tank's movement intent (dir when moving).
      const dirV = DIR_VECTORS[tank.dir]
      const wantX = tank.moving ? dirV.dx * tank.speed : 0
      const wantY = tank.moving ? dirV.dy * tank.speed : 0

      const onIce = w.isTankOnIce(tank)
      if (onIce) {
        // Low traction: ease velocity toward the desired value. Accelerating
        // (target non-zero) uses ICE_ACCEL_TRACTION; decelerating (target zero
        // — input released, or the axis being abandoned on a perpendicular
        // turn) uses ICE_DECEL_TRACTION so the tank coasts. That glide is the
        // "slippery" ice feel.
        const tx = wantX === 0 ? ICE_DECEL_TRACTION : ICE_ACCEL_TRACTION
        const ty = wantY === 0 ? ICE_DECEL_TRACTION : ICE_ACCEL_TRACTION
        tank.vx += (wantX - tank.vx) * tx
        tank.vy += (wantY - tank.vy) * ty
        // Kill sub-pixel jitter once a decelerating axis has all but stopped.
        if (wantX === 0 && Math.abs(tank.vx) < 0.02) tank.vx = 0
        if (wantY === 0 && Math.abs(tank.vy) < 0.02) tank.vy = 0
      } else {
        // Normal ground: instant traction = crisp, current control (unchanged).
        tank.vx = wantX
        tank.vy = wantY
      }

      // ---- Axis-lock: keep movement strictly one axis at a time so the
      // off-axis coordinate stays grid-aligned (the collision system assumes
      // axis-aligned tanks; a tank is exactly one 2×2 block wide). The
      // dominant (larger |velocity|) axis wins; the other is zeroed. During a
      // perpendicular turn on ice the OLD axis keeps gliding until it decays
      // below the new one — i.e. you can't instantly change direction on ice.
      const axis: 'x' | 'y' = Math.abs(tank.vx) >= Math.abs(tank.vy) ? 'x' : 'y'
      if (axis === 'x') tank.vy = 0
      else tank.vx = 0

      // No actual motion this tick (both velocities rounded to 0) → done.
      if (tank.vx === 0 && tank.vy === 0) continue

      // Snap the perpendicular (off-axis) coordinate to the grid so the tank
      // stays aligned to a row/column while sliding along the other axis.
      if (axis === 'x') tank.y = snap(tank.y, CELL)
      else tank.x = snap(tank.x, CELL)

      // Try to move along the (single) velocity axis.
      const newX = tank.x + tank.vx
      const newY = tank.y + tank.vy

      // Check bounds
      if (!w.isInBounds(newX, newY, tank.w, tank.h)) {
        if (axis === 'x') {
          tank.x = tank.vx < 0 ? 0 : FIELD - tank.w
          tank.vx = 0
        } else {
          tank.y = tank.vy < 0 ? 0 : FIELD - tank.h
          tank.vy = 0
        }
        if (tank.aiState) tank.aiState.thinkTimer = 0
        continue
      }

      // Check terrain collision
      const canTraverseWater = w.canTankTraverseWater(tank)
      if (w.rectHitsTerrain(newX, newY, tank.w, tank.h, canTraverseWater)) {
        // Snap to the cell boundary on the travel axis and stop there.
        if (axis === 'x') {
          tank.x = snap(tank.x, CELL)
          tank.vx = 0
        } else {
          tank.y = snap(tank.y, CELL)
          tank.vy = 0
        }
        if (tank.aiState) tank.aiState.thinkTimer = 0
        continue
      }

      // Check tank-tank collision
      if (this.tankHitsTank(tank, newX, newY)) {
        if (axis === 'x') tank.vx = 0
        else tank.vy = 0
        if (tank.aiState) tank.aiState.thinkTimer = 0
        continue
      }

      // Move is valid
      tank.x = newX
      tank.y = newY
    }
  }

  private tankHitsTank(self: Tank, newX: number, newY: number): boolean {
    const w = this.world
    for (const other of w.allTanks) {
      if (other === self || !other.alive) continue
      // NOTE: spawning tanks (spawnTimer > 0) DO block movement. Previously
      // they were skipped here, which let a moving tank drive *into* a tank
      // that was still in its spawn animation. The two would overlap, and once
      // the spawn timer expired they were jammed at the corner/edge with zero
      // free directions — multiple enemies permanently stuck at the spawn point.
      // Spawn safety is still guaranteed: updateSpawning() refuses to create a
      // tank on top of any existing tank (including spawning ones), so the only
      // overlap path was movement, now closed. (Bullets still pass through
      // spawning tanks — they are invulnerable — via the separate check in
      // bulletHitsTank.)
      if (aabb(newX, newY, self.w, self.h, other.x, other.y, other.w, other.h)) {
        return true
      }
    }
    return false
  }

  // ================================================================
  // Bullet System
  // ================================================================

  private tryFire(tank: Tank): void {
    const w = this.world
    const now = w.frame * (1000 / 60)
    // Fire rate is governed SOLELY by the tank's `nextFireInterval` — a value
    // frozen at the previous shot from the fire-rate standard (config/fire-rate.ts):
    // the kind's base interval × a deterministic per-fire jitter in
    // random(0.95, 1.05). It is a fixed per-type cadence measured in time, and
    // therefore cannot depend on whether any previous bullet is still in flight
    // or has hit something.
    //
    // A previous implementation additionally gated the PLAYER on a
    // max-concurrent-bullets count (1 at base level, 2 once promoted). That
    // cap coupled the player's fire rate to bullet *lifetime*: because a
    // bullet only disappears after it strikes terrain/a tank or leaves the
    // field, the next shot was forced to wait for the previous one to
    // resolve — so the effective rate depended on whether the last shell hit.
    // The cap is intentionally removed: fire rate is `nextFireInterval`, period.
    if (now - tank.lastFire < tank.nextFireInterval) return

    const v = DIR_VECTORS[tank.dir]

    // Bullet starts at the center of the tank's front edge
    const bx = tank.x + tank.w / 2 - BULLET / 2 + v.dx * (tank.w / 2)
    const by = tank.y + tank.h / 2 - BULLET / 2 + v.dy * (tank.h / 2)

    const bullet: Bullet = {
      id: genId(),
      x: bx,
      y: by,
      w: BULLET,
      h: BULLET,
      dir: tank.dir,
      alive: true,
      ownerId: tank.id,
      ownerKind: tank.kind,
      isPlayer: tank.isPlayer ?? false,
      allegiance: tank.allegiance,
      // Per-bullet speed jitter: actual = base × random(0.95, 1.05). The jitter
      // seeds off the World's monotonic `bulletSeq` (not the module-level
      // genId, not the AI's world-RNG), so it is reproducible across runs and
      // snapshot-safe while NEVER perturbing enemy AI decisions (see
      // config/speed.ts). The base (tank.bulletSpeed) comes from the per-kind
      // table there.
      speed: spawnBulletSpeedPxPerTick(tank.kind, tank.level ?? 0, w.bulletSeq++, w.frame),
      power: tank.bulletPower,
      damage: tank.damage,
    }

    w.addBullet(bullet)
    tank.lastFire = now
    // Freeze the NEXT shot's interval now: base interval × per-fire jitter
    // random(0.95, 1.05), seeded deterministically from (tank fire-count,
    // world frame) so it is reproducible from World state (snapshot/Replay-safe)
    // and — critically — does NOT depend on the global `genId` counter, which
    // is NOT reset between Worlds. Using the per-World fire-count keeps firing
    // timing identical across separate runs (determinism invariant, AGENTS §2.3)
    // and never draws from `world.rng` (which would perturb the AI's stream).
    tank.fireCount += 1
    tank.nextFireInterval = nextFireIntervalMs(tank.kind, tank.level ?? 0, tank.fireCount, w.frame)
    w.pushEvent({ type: 'bullet_fired', bullet })
  }

  private updateBullets(): void {
    const w = this.world
    for (const bullet of w.bullets) {
      if (!bullet.alive) continue

      // Move
      const v = DIR_VECTORS[bullet.dir]
      bullet.x += v.dx * bullet.speed
      bullet.y += v.dy * bullet.speed

      // Out of bounds
      if (bullet.x < 0 || bullet.x > FIELD || bullet.y < 0 || bullet.y > FIELD) {
        bullet.alive = false
        this.createExplosion(bullet.x, bullet.y, 'small')
        continue
      }

      // Check terrain collision
      if (this.bulletHitsTerrain(bullet)) {
        bullet.alive = false
        continue
      }

      // Check tank collision
      if (this.bulletHitsTank(bullet)) {
        bullet.alive = false
        continue
      }

      // Check bullet-bullet collision
      if (this.bulletHitsBullet(bullet)) {
        bullet.alive = false
        continue
      }
    }
  }

  private bulletHitsTerrain(bullet: Bullet): boolean {
    const w = this.world
    const c0 = Math.floor(bullet.x / CELL)
    const r0 = Math.floor(bullet.y / CELL)
    const c1 = Math.floor((bullet.x + bullet.w - 1) / CELL)
    const r1 = Math.floor((bullet.y + bullet.h - 1) / CELL)

    let hit = false
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const type = w.tileMap.get(c, r)
        if (type === 'empty') continue

        if (type === 'base') {
          // Player AND enemy bullets damage the base via the same firepower
          // formula (classic still instakills). Allied guard bullets DEFEND the
          // base and must never damage it (§31 Phase 2) — but they still stop
          // on it.
          if (bullet.allegiance !== 'ally') this.damageBase(this.bulletFirePower(bullet))
          return true
        } else if (type === 'brick') {
          w.tileMap.destroy(c, r)
          hit = true
          this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
        } else if (type === 'steel') {
          if (bullet.power >= 2) {
            w.tileMap.destroy(c, r)
            this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
          } else {
            // Ricochet effect on steel — small spark explosion even when not destroyed
            this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
          }
          hit = true // Bullet stops regardless
        }
      }
    }
    return hit
  }

  /**
   * Resolve a bullet's shooter into a single `firepower` number (0..100), which
   * is all the base-damage routine needs. The player's firepower scales with
   * its current star level; enemy tiers do NOT change firepower (level 0).
   */
  private bulletFirePower(bullet: Bullet): number {
    const w = this.world
    return bullet.ownerKind === 'player'
      ? resolveProfile('player', w.playerLevel).firepower
      : resolveProfile(bullet.ownerKind, 0).firepower
  }

  /**
   * Apply one bullet's damage to the base (eagle). The only input is the
   * shooter's `firePower` number — nothing about the kind, star level, or
   * difficulty is known here. Damage IS the firepower value (user spec:
   * `damage = firePower`), so a stronger gun chips more off the single pool.
   * Player and enemy bullets use the SAME path (same `bulletFirePower`
   * resolver upstream).
   *
   * Only when baseHp reaches 0 are all base cells cleared and the
   * `base_destroyed` event emitted; otherwise the base stays up but its damage
   * overlay is refreshed for the renderer.
   */
  private damageBase(firePower: number): void {
    const w = this.world
    const dmg = firePower
    const bp = w.tileMap.getBasePos()
    if (bp) {
      this.createExplosion(bp.x + CELL, bp.y + CELL, 'small')
    }
    if (dmg >= w.baseHp) {
      w.baseHp = 0
      w.tileMap.destroyAllBaseCells()
      w.pushEvent({ type: 'base_destroyed' })
    } else {
      w.baseHp -= dmg
      w.tileMap.markBaseDamaged()
    }
  }

  private bulletHitsTank(bullet: Bullet): boolean {
    const w = this.world
    for (const tank of w.allTanks) {
      if (!tank.alive || tank.id === bullet.ownerId) continue
      if (tank.spawnTimer > 0) continue // spawning = invulnerable

      // 3-way friendly-fire (DECISIONS.md §31 Phase 2): a bullet hits a tank
      // only when exactly one of them is on the enemy side. The player+ally
      // team has no friendly fire among/between itself; enemy bullets also
      // strike allied guards.
      const bulletEnemy = bullet.allegiance === 'enemy'
      const tankEnemy = tank.allegiance === 'enemy'
      if (bulletEnemy === tankEnemy) continue

      if (!aabb(bullet.x, bullet.y, bullet.w, bullet.h, tank.x, tank.y, tank.w, tank.h)) continue

      // Shield check
      if (tank.shieldTimer && tank.shieldTimer > 0) {
        // Bullet bounces off shield — just destroy bullet
        this.createExplosion(bullet.x, bullet.y, 'small')
        return true
      }

      // Damage tank — cosmetic / HP effect ONLY. A non-lethal hit must NEVER
      // change a tank's identity or combat capability (issue #2): we do not
      // swap `kind`, mutate `profile`, or alter any derived stat (speed,
      // bulletSpeed, fireCooldown, bulletPower). The tank keeps its type and
      // appearance; only `hp` drops (by the bullet's per-shot `damage`) and a
      // damage *decoration* (hitCount) rises so the renderer can layer on
      // type-preserving scorch/crack decals. This is the canonical
      // firepower/HP model: hits-to-kill = ceil(target.maxHp / bullet.damage).
      tank.hp -= bullet.damage
      tank.hitCount = Math.min((tank.hitCount ?? 0) + 1, 4)
      this.createExplosion(bullet.x, bullet.y, 'small')

      if (tank.hp <= 0) {
        tank.alive = false
        this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')

        if (tank.isPlayer) {
          w.pushEvent({ type: 'tank_destroyed', tank, by: 'enemy' })
          w.pushEvent({ type: 'player_hit' })
        } else if (tank.allegiance === 'ally') {
          // Allied guard destroyed — no score, no kill credit, no drops. The
          // guard simply stops fighting (§31 Phase 2).
          w.pushEvent({ type: 'tank_destroyed', tank, by: 'enemy' })
        } else {
          const gained = killScore(w.difficultyKey, tank.aiState?.level, w.stageIndex)
          w.score += gained
          w.killCount++
          // Accompanying "balance" enemies (isExtra) are outside the per-stage
          // 20-enemy count, so they never decrement enemiesRemaining / block
          // stage clear — but they still count as a normal kill for score
          // (§31 Phase 2).
          if (!tank.isExtra) w.enemiesRemaining--
          w.addPopup({
            id: genId(),
            x: tank.x,
            y: tank.y,
            text: String(gained),
            timer: 1500,
          })
          w.pushEvent({ type: 'tank_destroyed', tank, by: 'player' })

          // --- Item drop rules (item-drop v1, DECISIONS.md §30) ---
          // 1) Bonus enemies (level-design flagged) drop a power-up on death.
          // 2) Elite (commander-tier) enemies always drop a power-up on death.
          // 3) Every 10th enemy killed drops a power-up (kill-cadence reward).
          // 4) Score milestone: every SCORE_DROP_INTERVAL (5000) points
          //    accumulated drops a power-up. A single large score gain can
          //    cross several milestones at once and thus drop several.
          // A drop triggered by the FINAL enemy of a non-final stage is deferred
          // (buffered on world.pendingDrops) so the stage-clear transition
          // doesn't wipe it; it is released on the first enemy kill of the next
          // stage — which may therefore drop several power-ups at once.
          // Extra (balance) enemies are excluded — they don't progress drops.
          if (!tank.isExtra) {
            this.flushPendingDrops() // release drops deferred from a prior stage
            const isElite = tank.aiState?.isCommander === true
            const isTenthKill = w.killCount % 10 === 0

            // Collect this kill's guaranteed drops, each anchored on the slain
            // enemy's tile.
            const drops: { x: number; y: number }[] = []
            if (tank.bonus || isElite || isTenthKill) {
              drops.push({ x: tank.x, y: tank.y })
            }

            // Rule 4 — score milestone. Count how many SCORE_DROP_INTERVAL
            // boundaries the new score crossed *in this single kill* so a big
            // jackpot can drop several power-ups at once.
            const beforeScore = w.score - gained
            const milestones =
              Math.floor(w.score / SCORE_DROP_INTERVAL) -
              Math.floor(beforeScore / SCORE_DROP_INTERVAL)
            for (let i = 0; i < milestones; i++) drops.push({ x: tank.x, y: tank.y })

            if (drops.length > 0) {
              const isFinalEnemy = w.enemiesRemaining <= 0
              const hasNextStage = w.stageIndex + 1 < w.totalStages
              if (isFinalEnemy && hasNextStage) {
                for (const d of drops) w.pendingDrops.push(this.buildDrop(d)) // defer
              } else {
                for (const d of drops) this.spawnPowerUp(d) // drop immediately
              }
            }
          }
        }
      } else {
        // Armor tank flash
        if (tank.kind === 'armor') {
          tank.flashTimer = 200
        }
      }
      return true
    }
    return false
  }

  private bulletHitsBullet(bullet: Bullet): boolean {
    const w = this.world
    for (const other of w.bullets) {
      if (other === bullet || !other.alive) continue
      // Bullets cancel only across opposing sides (player/ally vs enemy).
      const bulletEnemy = bullet.allegiance === 'enemy'
      const otherEnemy = other.allegiance === 'enemy'
      if (bulletEnemy === otherEnemy) continue
      if (aabb(bullet.x, bullet.y, bullet.w, bullet.h, other.x, other.y, other.w, other.h)) {
        other.alive = false
        this.createExplosion((bullet.x + other.x) / 2, (bullet.y + other.y) / 2, 'small')
        return true
      }
    }
    return false
  }

  // ================================================================
  // Power-up System
  // ================================================================

  /**
   * Build a drop descriptor (type + terrain-safe position). The `world.rng`
   * pick happens HERE so a buffered drop is fully resolved and deterministic —
   * flushing later only materialises it (no extra RNG consumption).
   */
  private buildDrop(at?: { x: number; y: number }): { type: PowerUpType; x: number; y: number } {
    const w = this.world
    const type = this.rollPowerUpType()

    // Prefer the slain enemy's tile so the reward feels earned. Fall back to a
    // random clear tile if that spot is blocked (entropy from world.rng so the
    // whole sequence stays deterministic / snapshot-safe).
    let x = 0,
      y = 0
    let placed = false
    if (at && !w.rectHitsTerrain(at.x, at.y, TANK, TANK)) {
      x = at.x
      y = at.y
      placed = true
    }
    if (!placed) {
      let tries = 0
      do {
        x = w.rng.int(12) * 2 * CELL
        y = w.rng.int(12) * 2 * CELL
        tries++
      } while (tries < 20 && w.rectHitsTerrain(x, y, TANK, TANK))
    }

    return { type, x, y }
  }

  /**
   * Pick a power-up type for a drop (DECISIONS.md §31). Every drop source
   * (elite / every-10-kills / every-5000-pts / bonus) funnels through here, so
   * the 10% super-item chance is uniform across all of them. A super drop rolls
   * equally among `SUPER_POWERUP_TYPES` (Phase 1: frenzy + sacrifice; guard
   * joins in Phase 2). All randomness comes from `world.rng` → deterministic.
   */
  private rollPowerUpType(): PowerUpType {
    const w = this.world
    if (w.rng.next() < SUPER_POWERUP_DROP_CHANCE) {
      return w.rng.pick(SUPER_POWERUP_TYPES)
    }
    // The boat (amphibious) power-up is only meaningful where there is water to
    // cross — drop it only on water stages (DECISIONS.md §31 follow-up).
    const pool = w.tileMap.hasWater() ? POWERUP_TYPES : POWERUP_TYPES_NO_BOAT
    return w.rng.pick(pool)
  }

  /** Spawn a power-up immediately at the given (or random) position. */
  private spawnPowerUp(at?: { x: number; y: number }): void {
    this.spawnBuiltDrop(this.buildDrop(at))
  }

  private spawnBuiltDrop(d: { type: PowerUpType; x: number; y: number }): void {
    const w = this.world
    w.addPowerUp({
      id: genId(),
      type: d.type,
      x: d.x,
      y: d.y,
      w: TANK,
      h: TANK,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })
  }

  /** Release every drop deferred from a previous stage (item-drop v1). */
  private flushPendingDrops(): void {
    const w = this.world
    if (w.pendingDrops.length === 0) return
    const hasWater = w.tileMap.hasWater()
    for (const d of w.pendingDrops) {
      // Don't materialise a boat drop on a stage with no water (it would be
      // useless and look like a bug). Deferred drops keep their already-rolled
      // type/position, so we just skip them here rather than re-rolling.
      if (d.type === 'boat' && !hasWater) continue
      this.spawnBuiltDrop(d)
    }
    w.pendingDrops = []
  }

  private updatePowerUps(): void {
    const w = this.world
    const p = w.player
    if (!p || !p.alive) return

    const dt = 1000 / 60

    for (const pu of w.powerUps) {
      if (!pu.alive) continue
      pu.blinkTimer += dt
      pu.lifeTimer += dt

      // Despawn power-up after timeout
      if (pu.lifeTimer >= POWERUP_TIMEOUT_MS) {
        pu.alive = false
        continue
      }

      // Check player pickup
      if (aabb(p.x, p.y, p.w, p.h, pu.x, pu.y, pu.w, pu.h)) {
        pu.alive = false
        this.applyPowerUp(pu.type)
        w.score += ITEM_SCORE
        w.pushEvent({ type: 'powerup_collected', powerUp: pu.type, by: 'player' })
      }
    }
  }

  private applyPowerUp(type: PowerUpType): void {
    const w = this.world
    const p = w.player
    if (!p) return

    switch (type) {
      case 'star':
        // Player progression is universal: every star raises ALL capability
        // dimensions together (plan §11). Re-derive the tank's concrete stats
        // from the new profile. Current HP is intentionally NOT refilled — a
        // star is power, not a repair.
        // Classic mode caps the star *level* at maximumLevel; every other mode
        // accumulates WITHOUT bound (the per-star gain decays past the
        // balanced×150% threshold inside playerProfile). The cap is a
        // classic-only, pickup-time constraint.
        const classicCap = w.difficultyKey === 'classic'
        const atCap = classicCap && (p.level ?? 0) >= PLAYER_PROGRESSION.maximumLevel
        if (!atCap) {
          p.level = (p.level ?? 0) + 1
          w.playerLevel = p.level
          const stats = profileToStats(resolveProfile('player', p.level), 'player', p.level)
          p.speed = stats.speed * rollSpeedJitter(this.world.rng)
          p.bulletSpeed = stats.bulletSpeed
          p.bulletPower = stats.bulletPower
          p.fireCooldown = stats.fireCooldown
          p.maxHp = stats.maxHp
          p.profile = resolveProfile('player', p.level)
        }
        break

      case 'bomb':
        // Destroy all enemies on screen
        for (const tank of w.tanks) {
          if (!tank.alive) continue
          tank.alive = false
          this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')
          const gained = killScore(w.difficultyKey, tank.aiState?.level, w.stageIndex)
          w.score += gained
          w.enemiesRemaining--
          w.killCount++
          w.addPopup({
            id: genId(),
            x: tank.x,
            y: tank.y,
            text: String(gained),
            timer: 1500,
          })
        }
        break

      case 'shield':
        p.shieldTimer = POWERUP_DURATION_MS
        break

      case 'freeze':
        w.freezeTimer = POWERUP_DURATION_MS
        break

      case 'tank':
        w.lives++
        break

      case 'fence':
        // Place steel tiles around the base (eagle) to protect it
        this.applyFencePowerUp()
        break

      case 'boat':
        // Grant amphibious movement: can traverse water and ice without penalty
        this.applyBoatPowerUp()
        break

      // ---- Super power-ups (强力道具, DECISIONS.md §31) ----
      // Picked up into an inventory (accumulated), not applied instantly.
      case 'guard':
        // 天降神兵 — accumulate; released actively with F5 (Phase 2 summon).
        w.guardStock++
        break
      case 'frenzy':
        // 狂暴宣泄 — accumulate; released actively with F6.
        w.frenzyStock++
        break
      case 'sacrifice':
        // 同归于尽 — accumulate; released passively when a life is lost.
        w.sacrificeStock++
        break
    }
  }

  private applyFencePowerUp(): void {
    const w = this.world
    // Place a protective steel ring around the base (top + left + right sides;
    // the bottom edge is off-grid). The ring lasts FENCE_DURATION_FRAMES, then
    // reverts to brick in updateFence().
    let placed = 0
    for (const pos of baseRingPositions()) {
      if (placed >= FENCE_STEEL_COUNT) break
      const existing = w.tileMap.get(pos.col, pos.row)
      if (existing === 'empty' || existing === 'brick') {
        w.tileMap.set(pos.col, pos.row, 'steel')
        placed++
      }
    }
    w.fenceExpireFrame = w.frame + FENCE_DURATION_FRAMES
  }

  /**
   * Tick the fence power-up: when its steel ring timer expires, revert the ring
   * cells that are still steel back to brick walls. Cells left as steel are the
   * ones the fence created; original brick/empty/steel terrain is untouched.
   */
  private updateFence(): void {
    const w = this.world
    if (w.fenceExpireFrame === undefined) return
    if (w.frame < w.fenceExpireFrame) return
    for (const pos of baseRingPositions()) {
      if (w.tileMap.get(pos.col, pos.row) === 'steel') {
        w.tileMap.set(pos.col, pos.row, 'brick')
      }
    }
    w.fenceExpireFrame = undefined
  }

  private applyBoatPowerUp(): void {
    const w = this.world
    const p = w.player
    if (!p) return

    // Grant amphibious movement for BOAT_DURATION_MS
    p.boatTimer = BOAT_DURATION_MS
  }

  // ================================================================
  // Explosion System
  // ================================================================

  private createExplosion(x: number, y: number, kind: 'small' | 'big'): void {
    const w = this.world
    const size = kind === 'big' ? TANK : BULLET * 2
    const maxTimer = kind === 'big' ? 500 : 200
    w.addExplosion({
      id: genId(),
      x,
      y,
      size,
      timer: maxTimer,
      maxTimer,
      kind,
    })
    w.pushEvent({ type: 'explosion', x, y, kind })
  }

  private updateExplosions(): void {
    const w = this.world
    for (const exp of w.explosions) {
      exp.timer -= 1000 / 60
    }
  }

  private updatePopups(): void {
    const w = this.world
    for (const popup of w.popups) {
      popup.timer -= 1000 / 60
    }
  }

  // ================================================================
  // Win/Lose Conditions
  // ================================================================

  private checkConditions(): void {
    const w = this.world

    // Base destroyed = game over
    if (w.tileMap.isBaseDestroyed()) {
      w.state = 'gameover'
      w.gameOverTimer = 3000
      w.saveHighScore()
      this.createExplosion(FIELD / 2, FIELD - CELL * 2, 'big')
      return
    }

    // Player destroyed
    if (w.player && !w.player.alive) {
      // 同归于尽 (DECISIONS.md §31): passively release ALL accumulated
      // sacrifice items, destroying enemies + brick walls within a radius.
      this.triggerSacrificeAoE(w.player)
      // A dead player can't keep a barrage running — cancel any active frenzy.
      w.frenzyTimer = 0
      w.frenzyShotsLeft = 0
      w.lives--
      if (w.lives <= 0) {
        w.state = 'gameover'
        w.gameOverTimer = 3000
        w.saveHighScore()
      } else {
        // Star buff does NOT persist across respawns (user bug fix): losing the
        // tank discards ALL earned star upgrades and reverts the player to the
        // difficulty's starting level — classic Battle City behaviour (death
        // resets the tank to its baseline form). The respawned player is then
        // rebuilt from this reset level; only the difficulty baseline remains.
        w.playerLevel = w.difficulty.playerStartLevel
        // Respawn player
        w.spawnPlayer()
        w.player.shieldTimer = RESPAWN_SHIELD_MS
      }
      return
    }

    // Stage clear — all (non-extra) enemies defeated. Accompanying "balance"
    // enemies (isExtra) are outside the per-stage count and must NOT block
    // stage clear (§31 Phase 2).
    if (w.enemiesRemaining <= 0 && w.tanks.every((t) => t.isExtra || !t.alive)) {
      const hasAlivePowerUp = w.powerUps.some((p) => p.alive)

      if (!hasAlivePowerUp) {
        // Nothing left to collect → end the stage.
        // If we were collecting bonuses, the "1s after pickup" rule applies;
        // otherwise this is the normal immediate clear.
        w.state = 'stageclear'
        w.stageClearTimer = w.pickupWindowEntered
          ? POWERUP_PICKUP_END_DELAY_MS
          : STAGE_CLEAR_DELAY_MS
        w.pickupWindowTimer = 0
        w.pickupWindowEntered = false
        w.score += stageClearScore(w.stageIndex)
        w.pushEvent({ type: 'stage_clear', stage: w.stageIndex })
        return
      }

      // Power-ups remain on the field.
      if (!w.pickupWindowEntered) {
        // Begin the bonus-collection window (once). Stay in 'playing' so the
        // player can still move and pick items up.
        w.pickupWindowEntered = true
        w.pickupWindowTimer = POWERUP_PICKUP_WINDOW_MS
        w.addPopup({
          id: genId(),
          x: FIELD / 2 - TANK / 2,
          y: FIELD / 2 - TANK,
          text: 'BONUS TIME!',
          timer: 1800,
        })
        return
      }

      // Window already running: if it has elapsed, end the stage (timeout).
      if (w.pickupWindowTimer <= 0) {
        w.state = 'stageclear'
        w.stageClearTimer = POWERUP_PICKUP_END_DELAY_MS
        w.pickupWindowEntered = false
        w.score += stageClearScore(w.stageIndex)
        w.pushEvent({ type: 'stage_clear', stage: w.stageIndex })
        return
      }
      // Window active with items still unclaimed → keep playing.
    }
  }

  // ================================================================
  // Stage Transition
  // ================================================================

  private updateStageClear(): void {
    const w = this.world
    w.stageClearTimer -= 1000 / 60
    if (w.stageClearTimer <= 0) {
      w.loadStage(w.stageIndex + 1)
    }
    this.updateExplosions()
    this.updatePopups()
  }

  private updateGameOver(): void {
    const w = this.world
    w.gameOverTimer -= 1000 / 60
    this.updateExplosions()
    this.updatePopups()
  }

  // ================================================================
  // Pause
  // ================================================================

  togglePause(): void {
    const w = this.world
    if (w.state === 'playing') {
      w.state = 'paused'
    } else if (w.state === 'paused') {
      w.state = 'playing'
    }
  }
}
