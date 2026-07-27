import type { World } from './World'
import type { Tank, Bullet, PowerUpType, IntelligenceLevel } from '../types'
import {
  CELL,
  TANK,
  BULLET,
  FIELD,
  TICK_MS,
  MAX_ENEMIES_ALIVE,
  ENEMIES_PER_STAGE,
  DIR_VECTORS,
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
  BOAT_DURATION_MS,
  BASE_POS,
  GRID,
} from '../constants'
import { resolveProfile, profileToStats, PLAYER_PROGRESSION } from '../config/combat'
import { killScore, stageClearScore, ITEM_SCORE } from '../config/score'
import { applyEliteModifier } from '../config/combat'
import { rollSpeedJitter, spawnBulletSpeedPxPerTick } from '../config/speed'
import { nextFireIntervalMs } from '../config/fire-rate'
import { genId } from './World'
import { Input } from './Input'
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

/** Power-up types a bonus enemy can drop (module-level to avoid per-drop allocation). */
const POWERUP_TYPES: PowerUpType[] = [
  'star',
  'bomb',
  'shield',
  'freeze',
  'tank',
  'helmet',
  'fence',
  'boat',
]

/**
 * Simulation — the only layer allowed to modify the World.
 * Runs all game systems in a fixed timestep.
 */
export class Simulation {
  world: World
  input: Input
  /** Tactical Intelligence Framework — owns all enemy decision-making. */
  private ai: TacticalIntelligence

  constructor(world: World, input: Input) {
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
          // formula (classic still instakills). Returning immediately consumes
          // the bullet on the first overlapping base cell (the base spans 2×2),
          // so damage is applied exactly once per shot.
          this.damageBase(this.bulletFirePower(bullet))
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

      // Player bullets hit enemies, enemy bullets hit player
      if (bullet.isPlayer && tank.isPlayer) continue
      if (!bullet.isPlayer && !tank.isPlayer) continue

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
        } else {
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
          w.pushEvent({ type: 'tank_destroyed', tank, by: 'player' })

          // Drop power-up if bonus enemy
          if (tank.bonus) {
            this.spawnPowerUp()
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
      // Player bullet vs enemy bullet
      if (bullet.isPlayer === other.isPlayer) continue
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

  private spawnPowerUp(): void {
    const w = this.world
    const type = w.rng.pick(POWERUP_TYPES)

    // Random position (not on walls) — entropy from world.rng for determinism.
    let x = 0,
      y = 0
    let tries = 0
    do {
      x = w.rng.int(12) * 2 * CELL
      y = w.rng.int(12) * 2 * CELL
      tries++
    } while (tries < 20 && w.rectHitsTerrain(x, y, TANK, TANK))

    w.addPowerUp({
      id: genId(),
      type,
      x,
      y,
      w: TANK,
      h: TANK,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })
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

      case 'helmet':
        p.shieldTimer = RESPAWN_SHIELD_MS
        break

      case 'fence':
        // Place steel tiles around the base (eagle) to protect it
        this.applyFencePowerUp()
        break

      case 'boat':
        // Grant amphibious movement: can traverse water and ice without penalty
        this.applyBoatPowerUp()
        break
    }
  }

  private applyFencePowerUp(): void {
    const w = this.world
    const baseCol = BASE_POS.col
    const baseRow = BASE_POS.row

    // Place steel tiles around the base (2x2 base at col 12, row 24)
    // Create a protective fence: 3 tiles wide on each side of the base
    const positions: { col: number; row: number }[] = []

    // Top row (above base)
    for (let c = baseCol - 1; c <= baseCol + 2; c++) {
      if (c >= 0 && c < GRID) positions.push({ col: c, row: baseRow - 1 })
    }
    // Bottom row (below base)
    for (let c = baseCol - 1; c <= baseCol + 2; c++) {
      if (c >= 0 && c < GRID) positions.push({ col: c, row: baseRow + 2 })
    }
    // Left column
    for (let r = baseRow - 1; r <= baseRow + 2; r++) {
      if (r >= 0 && r < GRID) positions.push({ col: baseCol - 1, row: r })
    }
    // Right column
    for (let r = baseRow - 1; r <= baseRow + 2; r++) {
      if (r >= 0 && r < GRID) positions.push({ col: baseCol + 2, row: r })
    }

    // Place steel tiles (up to FENCE_STEEL_COUNT)
    let placed = 0
    for (const pos of positions) {
      if (placed >= FENCE_STEEL_COUNT) break
      const existing = w.tileMap.get(pos.col, pos.row)
      if (existing === 'empty' || existing === 'brick') {
        w.tileMap.set(pos.col, pos.row, 'steel')
        placed++
      }
    }
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

    // Stage clear — all enemies defeated
    if (w.enemiesRemaining <= 0 && w.tanks.length === 0) {
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
