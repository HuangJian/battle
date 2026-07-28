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
import { killScore, stageClearScore } from '../config/score'
import {
  SUPER_POWERUP_TYPES,
  POWERUP_TIERS,
  POWERUP_TIER_WEIGHTS,
  FRENZY_SHOTS,
  SACRIFICE_BASE_RADIUS_CELLS,
} from '../config/powerups'
import {
  EMP_DURATION_MS,
  MINE_ARM_MS,
  MINE_RADIUS_CELLS,
  DECOY_LIFESPAN_FRAMES,
} from '../constants'
import { applyEliteModifier } from '../config/combat'
import { rollSpeedJitter, spawnBulletSpeedPxPerTick } from '../config/speed'
import { hasStarPerk } from '../config/rules'
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
    if (w.empTimer > 0) w.empTimer -= 1000 / 60
    if (w.spawnTimer > 0) w.spawnTimer -= 1000 / 60
    if (w.pickupWindowTimer > 0) w.pickupWindowTimer -= TICK_MS

    // Update mine arm timers
    for (const mine of w.mines) {
      if (mine.alive && mine.armTimer > 0) {
        mine.armTimer -= 1000 / 60
        if (mine.armTimer < 0) mine.armTimer = 0
      }
    }

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

    // Mine collision detection (after bullets, before cleanup)
    this.updateMines()

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
        const eliteStats = profileToStats(eliteProfile, tank.kind, tank.level ?? 0, w.rules)
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
      w.spawnTimer = w.rules.spawnIntervalMs // classic 1.8s, others 1.5s
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
    // Rewind (时光宝盒): triggered by F7
    if (this.input.wasItemPressed('rewind') && w.rewindStock > 0) {
      this.activateRewind(p)
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
      speed: spawnBulletSpeedPxPerTick(
        p.kind,
        p.level ?? 0,
        w.bulletSeq++,
        w.frame,
        w.rules.bulletSpeedCps,
        w.rules.playerBulletSpeedPerStarCps,
      ),
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
   * Activate 时光宝盒 (new-powerups-plan §4.3): consume one rewind stock and
   * trigger a manual rewind to the most recent snapshot.
   */
  private activateRewind(_p: Tank): void {
    const w = this.world
    if (w.rewindStock <= 0) return
    if (w.state !== 'playing') return
    // Signal Game.ts to trigger RecoveryController.beginManualRewind()
    // (actual rewind logic is in Game.ts, not Simulation — AGENTS §2.1)
    w.rewindStock--
    w.rewindPending = true
  }

  // ================================================================
  // Decoy System (new-powerups-plan §4.4)
  // ================================================================

  /**
   * Activate 诱饵 (Decoy): spawn a fake player ally that draws enemy fire.
   * The decoy moves toward enemies but never fires.
   */
  private activateDecoy(p: Tank): void {
    const w = this.world
    // Spawn as basic tank (minimal stats) then override for decoy role
    const tank = w.createTank('basic', p.x, p.y, p.dir)
    tank.allegiance = 'ally'
    tank.isPlayer = false
    tank.isDecoy = true
    tank.kind = 'player' // visual: looks like the player
    tank.spawnTimer = 500
    // Decoy has 1 HP and moves toward enemies (never fires)
    tank.hp = 1
    tank.maxHp = 1
    if (tank.aiState) {
      tank.aiState.strategicGoal = 'advance' // move toward enemies
      tank.aiState.tacticalGoal = 'advance'
    }
    // Lifespan expiry (absolute frame)
    tank.guardExpireFrame = w.frame + DECOY_LIFESPAN_FRAMES
    w.allies.push(tank)
  }

  // ================================================================
  // Mine System (new-powerups-plan §4.5)
  // ================================================================

  /**
   * Place a mine at the player's current grid position.
   * Called when the player picks up a 'mine' power-up.
   */
  private placeMine(p: Tank): void {
    const w = this.world
    const x = Math.round(p.x / CELL) * CELL
    const y = Math.round(p.y / CELL) * CELL
    w.mines.push({
      id: genId(),
      x,
      y,
      w: TANK,
      h: TANK,
      armTimer: MINE_ARM_MS,
      alive: true,
    })
  }

  /**
   * Check mine collisions: enemy tanks stepping on armed mines, or enemy
   * bullets hitting armed mines. On detonation, deal AoE damage to nearby
   * enemies and destroy nearby brick walls (same pattern as sacrifice).
   */
  private updateMines(): void {
    const w = this.world
    for (const mine of w.mines) {
      if (!mine.alive) continue
      // Mine must be armed (armTimer <= 0) to detonate
      if (mine.armTimer > 0) continue

      let detonate = false

      // Check enemy tank collision
      for (const tank of w.tanks) {
        if (!tank.alive || tank.allegiance !== 'enemy' || tank.spawnTimer > 0) continue
        if (aabb(mine.x, mine.y, mine.w, mine.h, tank.x, tank.y, tank.w, tank.h)) {
          detonate = true
          break
        }
      }

      // Check enemy bullet collision
      if (!detonate) {
        for (const bullet of w.bullets) {
          if (!bullet.alive || bullet.allegiance !== 'enemy') continue
          if (aabb(mine.x, mine.y, mine.w, mine.h, bullet.x, bullet.y, bullet.w, bullet.h)) {
            detonate = true
            bullet.alive = false
            break
          }
        }
      }

      if (detonate) {
        mine.alive = false
        const cx = mine.x + mine.w / 2
        const cy = mine.y + mine.h / 2
        const radiusPx = MINE_RADIUS_CELLS * CELL

        // Damage enemies in radius (normal kill accounting)
        for (const tank of w.tanks) {
          if (!tank.alive || tank.allegiance !== 'enemy' || tank.spawnTimer > 0) continue
          const tx = tank.x + tank.w / 2
          const ty = tank.y + tank.h / 2
          if (Math.hypot(tx - cx, ty - cy) <= radiusPx) {
            tank.alive = false
            this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')
            const gained = killScore(
              w.difficultyKey,
              tank.aiState?.level,
              w.stageIndex,
              w.rules,
              tank.kind,
            )
            w.score += gained
            w.enemiesRemaining--
            w.killCount++
            w.addPopup({ id: genId(), x: tank.x, y: tank.y, text: String(gained), timer: 1500 })
            w.pushEvent({ type: 'tank_destroyed', tank, by: 'player' })
          }
        }

        // Destroy brick walls in radius
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
      }
    }
    // Remove dead mines
    w.mines = w.mines.filter((m) => m.alive)
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
        const gained = killScore(w.difficultyKey, t.aiState?.level, w.stageIndex, w.rules, t.kind)
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

    // EMP silencer: when empTimer > 0, ENEMY tanks cannot fire. Friendly
    // 天降神兵 guards must keep firing — they are allies, not hostiles, so the
    // player's own EMP must not neutralize them (new-powerups-plan §4.2).
    if (tank.allegiance === 'enemy' && w.empTimer > 0) return

    // Decoys never fire — they are fake tanks whose only job is to draw enemy
    // fire, never to shoot back (new-powerups-plan §4.4).
    if (tank.isDecoy) return

    // Fire-rate limiter — two mutually-exclusive models (config/rules.ts,
    // plan/classic-faithful-feel.md Phase 2):
    //
    //  - 'cooldown' (modern relax/hard/chaos): a fixed per-type TIME gate.
    //    `nextFireInterval` is frozen at the previous shot from the fire-rate
    //    standard (config/fire-rate.ts: base interval × deterministic per-fire
    //    jitter). Rate is independent of whether the previous bullet is still
    //    in flight — purely a time cadence.
    //
    //  - 'bulletCap' (classic FC-1985): fire rate is governed ONLY by the
    //    on-screen bullet cap. The player may fire again the instant the
    //    previous shell resolves (strikes terrain/a tank or leaves the field)
    //    — there is NO separate time cooldown. This is the faithful FC feel:
    //    holding fire yields a steady cadence paced by bullet *travel*, not by
    //    an artificial timer. A prior draft layered `baseFireIntervalMs()` on
    //    top of the cap; that produced a spurious ~1.2 s wait after every shot
    //    and is removed (user-reported bug, 2026-07-28).
    if (w.rules.fireModel === 'cooldown') {
      if (now - tank.lastFire < tank.nextFireInterval) return
    }

    // On-screen bullet cap (classic 'bulletCap' model, plan Phase 2). Count the
    // tank's own live bullets; block the shot once the cap is reached. The
    // cap is `maxBullets[kind]`, plus +1 for the player at/above
    // `playerDoubleShotLevel` (2★ → double-shot, FC-style). A canceled bullet
    // frees its slot on the next frame (no twin-spawn — issue #12).
    if (w.rules.fireModel === 'bulletCap') {
      // Minimum cooldown between shots: prevents instant refire when a bullet
      // resolves at close range. Without this floor, a bullet that hits a tank
      // 1 cell away resolves in 1 frame and the player fires again immediately
      // — machine-gun feel. 300ms ≈ 18 frames at 60fps is responsive but
      // prevents the exploit. (Data: rules.bulletCapMinCooldownMs.)
      if (
        w.rules.bulletCapMinCooldownMs > 0 &&
        now - tank.lastFire < w.rules.bulletCapMinCooldownMs
      ) {
        return
      }
      const cap =
        (w.rules.maxBullets[tank.kind] ?? 1) +
        (tank.kind === 'player' && (tank.level ?? 0) >= w.rules.playerDoubleShotLevel ? 1 : 0)
      let inFlight = 0
      for (const b of w.bullets) {
        if (b.alive && b.ownerId === tank.id) inFlight++
      }
      if (inFlight >= cap) return
    }

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
      speed: spawnBulletSpeedPxPerTick(
        tank.kind,
        tank.level ?? 0,
        w.bulletSeq++,
        w.frame,
        w.rules.bulletSpeedCps,
        w.rules.playerBulletSpeedPerStarCps,
      ),
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
          const gained = killScore(
            w.difficultyKey,
            tank.aiState?.level,
            w.stageIndex,
            w.rules,
            tank.kind,
          )
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
            const r = w.rules
            // Collect this kill's guaranteed drops, each anchored on the slain
            // enemy's tile. The rule set depends on the active GameplayRules.
            const drops: { x: number; y: number }[] = []
            if (r.dropSchedule === 'fixed') {
              // FC: the power-up carrier enemies (marked `bonus` at spawn from
              // `fixedDropKillIndices`) drop when destroyed — faithful to the
              // 1985 game where the flashing red enemy IS the drop, regardless
              // of kill order. No kill-counter logic leaks in here.
              if (tank.bonus) {
                drops.push({ x: tank.x, y: tank.y })
              }
            } else {
              const isElite = tank.aiState?.isCommander === true
              const isTenthKill = r.dropOnEveryNKills > 0 && w.killCount % r.dropOnEveryNKills === 0
              if (
                tank.bonus ||
                (r.dropOnEliteKill && isElite) ||
                (r.dropOnEveryNKills > 0 && isTenthKill)
              ) {
                drops.push({ x: tank.x, y: tank.y })
              }
              // Score milestone: every `dropOnScoreMilestone` points crossed in
              // this single kill can drop several power-ups at once.
              if (r.dropOnScoreMilestone > 0) {
                const beforeScore = w.score - gained
                const milestones =
                  Math.floor(w.score / r.dropOnScoreMilestone) -
                  Math.floor(beforeScore / r.dropOnScoreMilestone)
                for (let i = 0; i < milestones; i++) drops.push({ x: tank.x, y: tank.y })
              }
            }

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
   *
   * Position randomization: applies a weighted random offset from the enemy's
   * position based on `rules.dropPositionWeights` (50/30/20 near/mid/far).
   * The offset is snapped to grid-aligned cells and falls back to a random
   * clear tile if the offset position is blocked (terrain/out-of-bounds).
   */
  private buildDrop(at?: { x: number; y: number }): { type: PowerUpType; x: number; y: number } {
    const w = this.world
    const type = this.rollPowerUpType()

    // --- Position: weighted random offset from enemy position ---
    // Roll a tier (near/mid/far) from the configured weights, then pick a
    // random direction and distance within that tier's range. All randomness
    // flows through world.rng → deterministic / snapshot-safe.
    let x = 0
    let y = 0
    let placed = false

    if (at) {
      // Roll tier: near(0.5) / mid(0.3) / far(0.2)
      const weights = w.rules.dropPositionWeights
      const ranges = w.rules.dropPositionRanges
      const totalWeight = weights.near + weights.mid + weights.far

      // Guard: if all weights are 0, no offset — use exact enemy position.
      let tierRange = 0
      if (totalWeight > 0) {
        const roll = w.rng.next() * totalWeight
        if (roll < weights.near) {
          tierRange = ranges.near
        } else if (roll < weights.near + weights.mid) {
          tierRange = ranges.mid
        } else {
          tierRange = ranges.far
        }
      }

      // Pick a random direction (4 cardinal) and distance (1..tierRange cells).
      const dirs = [
        { dx: 1, dy: 0 },
        { dx: -1, dy: 0 },
        { dx: 0, dy: 1 },
        { dx: 0, dy: -1 },
      ]
      const dirIdx = Math.floor(w.rng.next() * 4)
      const dir = dirs[dirIdx]
      const dist = 1 + Math.floor(w.rng.next() * tierRange)

      // Snap anchor to grid — tank.x/y may not be grid-aligned after
      // movement (on-axis coordinate is not snapped, only off-axis).
      const anchorX = Math.round(at.x / CELL) * CELL
      const anchorY = Math.round(at.y / CELL) * CELL
      const clampedX = Math.max(0, Math.min(FIELD - TANK, anchorX + dir.dx * dist * CELL))
      const clampedY = Math.max(0, Math.min(FIELD - TANK, anchorY + dir.dy * dist * CELL))

      if (!w.rectHitsTerrain(clampedX, clampedY, TANK, TANK)) {
        x = clampedX
        y = clampedY
        placed = true
      }
    }

    // Fallback: random clear tile (unchanged from original).
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
   * equally among `SUPER_POWERUP_TYPES` (frenzy / sacrifice / guard / rewind).
   * All randomness comes from `world.rng` → deterministic.
   */
  private rollPowerUpType(): PowerUpType {
    const w = this.world
    const r = w.rules

    // Only modern mode uses the 3-tier weighted system (plan §3.1).
    // Classic uses fixedDropKillIndices and does not run this path.
    if (r.dropSchedule === 'modern') {
      // Build active tiers (super only if superDropChance > 0)
      const activeTiers: { name: string; items: PowerUpType[]; weight: number }[] = []
      // The 3-tier system (plan §3.1) defines the drop pool directly via
      // POWERUP_TIERS — these lists are the source of truth, NOT
      // r.allowedPowerups (which only gates classic-vs-modern and the old
      // single pool). Filtering the SUPER tier against allowedPowerups would
      // DROP every 强力道具 in modern mode, because super items are
      // intentionally absent from allowedPowerups — they are inventory/stock
      // items, not instant pickups. So we use the tier lists verbatim and only
      // apply the (unchanged) water-gate to the boat.
      const superItems = POWERUP_TIERS.super
      if (r.superDropChance > 0 && superItems.length > 0) {
        activeTiers.push({ name: 'super', items: superItems, weight: POWERUP_TIER_WEIGHTS.super })
      }
      const practicalItems = POWERUP_TIERS.practical
      if (practicalItems.length > 0) {
        activeTiers.push({
          name: 'practical',
          items: practicalItems,
          weight: POWERUP_TIER_WEIGHTS.practical,
        })
      }
      let normalItems = POWERUP_TIERS.normal
      if (!w.tileMap.hasWater()) {
        normalItems = normalItems.filter((t) => t !== 'boat')
      }
      if (normalItems.length > 0) {
        activeTiers.push({
          name: 'normal',
          items: normalItems,
          weight: POWERUP_TIER_WEIGHTS.normal,
        })
      }

      // Normalize weights across active tiers and pick one tier
      const totalWeight = activeTiers.reduce((s, t) => s + t.weight, 0)
      if (totalWeight <= 0) {
        // Fallback: pick from allowedPowerups directly
        let pool = r.allowedPowerups
        if (!w.tileMap.hasWater()) pool = pool.filter((t) => t !== 'boat')
        return w.rng.pick(pool)
      }
      const roll = w.rng.next() * totalWeight
      let cumulative = 0
      for (const tier of activeTiers) {
        cumulative += tier.weight
        if (roll < cumulative) {
          return w.rng.pick(tier.items)
        }
      }
      return w.rng.pick(activeTiers[activeTiers.length - 1].items)
    }

    // Classic path: use SUPER_POWERUP_TYPES directly (unchanged)
    if (r.superDropChance > 0 && w.rng.next() < r.superDropChance) {
      return w.rng.pick(SUPER_POWERUP_TYPES)
    }
    let pool = r.allowedPowerups
    if (!w.tileMap.hasWater()) {
      pool = pool.filter((t) => t !== 'boat')
    }
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
        w.score += w.rules.itemScore
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
        const atCap =
          w.difficultyKey === 'classic' && (p.level ?? 0) >= PLAYER_PROGRESSION.maximumLevel
        if (!atCap) {
          p.level = (p.level ?? 0) + 1
          w.playerLevel = p.level
          const stats = profileToStats(
            resolveProfile('player', p.level),
            'player',
            p.level,
            w.rules,
          )
          p.speed = stats.speed * (w.rules.speedJitter ? rollSpeedJitter(this.world.rng) : 1)
          p.bulletSpeed = stats.bulletSpeed
          p.bulletPower = stats.bulletPower
          p.fireCooldown = stats.fireCooldown
          p.maxHp = stats.maxHp
          p.profile = resolveProfile('player', p.level)
          // Functional star ladder (classic only, plan Phase 3). Matches FC:
          // 1★ fast bullet → 2★ double-shot (realized by the bullet cap in
          // tryFire) → 3★ steel-pierce. Non-classic stays universal-growth.
          if (w.rules.starModel === 'functional') {
            // Perks are cumulative in FC (a 2★ tank keeps the fast bullet it
            // earned at 1★), so query across every level ≤ current, not just
            // the current level's introduced-perk list (see hasStarPerk).
            if (hasStarPerk(w.rules, p.level ?? 0, 'fastBullet')) {
              p.bulletSpeed = stats.bulletSpeed * w.rules.fastBulletMult
            }
            if (hasStarPerk(w.rules, p.level ?? 0, 'steelPierce')) {
              p.bulletPower = 2
            }
          }
        }
        break

      case 'bomb':
        // Destroy all enemies on screen
        for (const tank of w.tanks) {
          if (!tank.alive) continue
          tank.alive = false
          this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')
          const gained = killScore(
            w.difficultyKey,
            tank.aiState?.level,
            w.stageIndex,
            w.rules,
            tank.kind,
          )
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
        // Timed buff: accumulate duration so picking up another while one is
        // active stacks (e.g. 3s left + 20s = 23s). See DECISIONS.md §33.
        p.shieldTimer = (p.shieldTimer ?? 0) + POWERUP_DURATION_MS
        break

      case 'freeze':
        // Timed buff: accumulate duration (same rule as shield). Freezing all
        // enemies again adds a full POWERUP_DURATION_MS on top of any remaining.
        w.freezeTimer = w.freezeTimer + POWERUP_DURATION_MS
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

      // ---- New power-ups (new-powerups-plan) ----
      case 'repair':
        // Fully restore the PLAYER tank's HP (new-powerups-plan §4.1).
        this.applyRepairPowerUp()
        break

      case 'emp':
        // Freeze all enemies for EMP_DURATION_MS (accumulates on re-pickup)
        w.empTimer += EMP_DURATION_MS
        break

      case 'rewind':
        // Add one rewind stock (accumulated); activated with F7
        w.rewindStock++
        break

      case 'decoy':
        // Spawn an ally decoy that attracts enemy fire
        this.activateDecoy(p)
        break

      case 'mine':
        // Place a mine at the player's current position
        this.placeMine(p)
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
    // Timed buff: accumulate duration rather than reset. If a fence ring is
    // already up, picking up another extends it by a full FENCE_DURATION_FRAMES
    // (same stacking rule as shield/freeze/boat). The steel ring is re-laid
    // idempotently over empty/brick cells, so re-applying is safe.
    w.fenceExpireFrame = (w.fenceExpireFrame ?? w.frame) + FENCE_DURATION_FRAMES
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

    // Timed buff: accumulate duration (same rule as shield/freeze). Picking up
    // another boat while one is active extends amphibious movement.
    p.boatTimer = (p.boatTimer ?? 0) + BOAT_DURATION_MS
  }

  /**
   * Apply Repair power-up (new-powerups-plan §4.1): fully restore the PLAYER
   * tank's HP. Unlike 星星 (star) — which deliberately does NOT refill HP —
   * Repair is the dedicated healing item that fills the gap left by star's
   * omission. (The eagle/base has its own HP and is unaffected.)
   */
  private applyRepairPowerUp(): void {
    const w = this.world
    const p = w.player
    if (!p) return
    p.hp = p.maxHp
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
        w.score += stageClearScore(w.stageIndex, w.rules)
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
        w.score += stageClearScore(w.stageIndex, w.rules)
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
