import type { World } from './World'
import type { Tank, Bullet, PowerUpType } from '../types'
import type { Direction } from '../constants'
import {
  CELL,
  TANK,
  BULLET,
  GRID,
  FIELD,
  MAX_ENEMIES_ALIVE,
  FIRE_COOLDOWN,
  PLAYER_SPEED,
  DIR_VECTORS,
  FREEZE_DURATION_MS,
  SHIELD_DURATION_MS,
  RESPAWN_SHIELD_MS,
} from '../constants'
import { TANK_CONFIGS } from '../config/tanks'
import { genId } from './World'
import { Input } from './Input'
import { snap, aabb, ALL_DIRS } from '../utils/helpers'

const ENEMY_SPAWN_POINTS = [
  { x: 0, y: 0 },
  { x: 12 * CELL, y: 0 },
  { x: 24 * CELL, y: 0 },
]

/**
 * Simulation — the only layer allowed to modify the World.
 * Runs all game systems in a fixed timestep.
 */
export class Simulation {
  world: World
  input: Input
  private spawnPointIndex = 0

  constructor(world: World, input: Input) {
    this.world = world
    this.input = input
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

    // Update timers
    if (w.freezeTimer > 0) w.freezeTimer -= 1000 / 60
    if (w.spawnTimer > 0) w.spawnTimer -= 1000 / 60

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

    // Check if spawn point is clear
    const entry = w.spawnQueue[0]
    const pt = ENEMY_SPAWN_POINTS[this.spawnPointIndex % ENEMY_SPAWN_POINTS.length]
    this.spawnPointIndex++

    // Check if spawn area is clear of other tanks
    const spawnRect = { x: pt.x, y: pt.y, w: TANK, h: TANK }
    let canSpawn = true
    for (const tank of w.allTanks) {
      if (
        aabb(spawnRect.x, spawnRect.y, spawnRect.w, spawnRect.h, tank.x, tank.y, tank.w, tank.h)
      ) {
        canSpawn = false
        break
      }
    }
    if (!canSpawn) {
      this.spawnPointIndex-- // retry same point next frame
      return
    }

    // Create the enemy tank
    const tank = w.createTank(entry.kind, pt.x, pt.y, 'down')
    tank.bonus = entry.bonus
    w.tanks.push(tank)
    w.spawnQueue.shift()
    w.enemiesSpawned++
    w.spawnTimer = 1500 // 1.5s between spawns
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
    const w = this.world
    const frozen = w.freezeTimer > 0

    for (const tank of w.tanks) {
      if (!tank.alive || tank.spawnTimer > 0) continue
      if (frozen) {
        tank.moving = false
        continue
      }

      const ai = tank.aiState
      if (!ai) continue

      // Think timer — change direction periodically
      ai.thinkTimer -= 1000 / 60
      if (ai.thinkTimer <= 0) {
        // Choose a new direction
        // 40% chance to aim at base or player, 60% random
        const r = Math.random()
        if (r < 0.3 && w.player) {
          // Move toward player
          ai.currentDir = this.dirToward(tank, w.player.x, w.player.y)
        } else if (r < 0.5) {
          // Move toward base
          const base = w.tileMap.getBasePos()
          if (base) {
            ai.currentDir = this.dirToward(tank, base.x, base.y)
          } else {
            ai.currentDir = ALL_DIRS[Math.floor(Math.random() * 4)]
          }
        } else {
          ai.currentDir = ALL_DIRS[Math.floor(Math.random() * 4)]
        }
        ai.thinkTimer = 500 + Math.random() * 1500
        tank.dir = ai.currentDir
        tank.moving = true
      }

      // Fire timer
      ai.fireTimer -= 1000 / 60
      if (ai.fireTimer <= 0) {
        this.tryFire(tank)
        ai.fireTimer = 300 + Math.random() * 1200
      }

      tank.moving = true
      tank.dir = ai.currentDir
    }
  }

  private dirToward(tank: Tank, tx: number, ty: number): Direction {
    const dx = tx - tank.x
    const dy = ty - tank.y
    if (Math.abs(dx) > Math.abs(dy)) {
      return dx > 0 ? 'right' : 'left'
    } else {
      return dy > 0 ? 'down' : 'up'
    }
  }

  // ================================================================
  // Movement System
  // ================================================================

  private updateMovement(): void {
    const w = this.world
    for (const tank of w.allTanks) {
      if (!tank.alive || tank.spawnTimer > 0) continue
      if (!tank.moving) continue

      // Enemy freeze
      if (!tank.isPlayer && w.freezeTimer > 0) continue

      // Align to grid when turning
      this.alignTank(tank)

      // Try to move
      const dist = tank.speed
      const v = DIR_VECTORS[tank.dir]
      const newX = tank.x + v.dx * dist
      const newY = tank.y + v.dy * dist

      // Check bounds
      if (!w.isInBounds(newX, newY, tank.w, tank.h)) {
        // Try to align to edge
        if (tank.dir === 'left') tank.x = 0
        else if (tank.dir === 'right') tank.x = FIELD - tank.w
        else if (tank.dir === 'up') tank.y = 0
        else if (tank.dir === 'down') tank.y = FIELD - tank.h
        // Force AI to rethink
        if (tank.aiState) tank.aiState.thinkTimer = 0
        continue
      }

      // Check terrain collision
      if (w.rectHitsTerrain(newX, newY, tank.w, tank.h)) {
        // Try to align to nearest cell boundary
        this.alignToWall(tank)
        if (tank.aiState) tank.aiState.thinkTimer = 0
        continue
      }

      // Check tank-tank collision
      if (this.tankHitsTank(tank, newX, newY)) {
        if (tank.aiState) tank.aiState.thinkTimer = 0
        continue
      }

      // Move is valid
      tank.x = newX
      tank.y = newY
    }
  }

  private alignTank(tank: Tank): void {
    // When moving horizontally, snap Y to nearest half-cell
    // When moving vertically, snap X to nearest half-cell
    if (tank.dir === 'left' || tank.dir === 'right') {
      tank.y = snap(tank.y, CELL)
    } else {
      tank.x = snap(tank.x, CELL)
    }
  }

  private alignToWall(tank: Tank): void {
    // Snap tank position to the wall it hit
    const v = DIR_VECTORS[tank.dir]
    if (v.dx !== 0) {
      tank.x = snap(tank.x, CELL)
    }
    if (v.dy !== 0) {
      tank.y = snap(tank.y, CELL)
    }
  }

  private tankHitsTank(self: Tank, newX: number, newY: number): boolean {
    const w = this.world
    for (const other of w.allTanks) {
      if (other === self || !other.alive) continue
      if (other.spawnTimer > 0) continue // spawning tanks don't block
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
    if (now - tank.lastFire < tank.fireCooldown) return

    // Player bullet limit
    if (tank.isPlayer) {
      const maxBullets = (tank.level ?? 0) >= 2 ? 2 : 1
      const activeBullets = w.bullets.filter((b) => b.alive && b.ownerId === tank.id).length
      if (activeBullets >= maxBullets) return
    }

    const cfg = TANK_CONFIGS[tank.kind]
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
      speed: cfg.bulletSpeed,
      power: tank.isPlayer ? ((tank.level ?? 0) >= 2 ? 2 : 1) : cfg.bulletPower,
    }

    w.addBullet(bullet)
    tank.lastFire = now
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
          // Destroy ALL base cells — any hit ends the game
          for (let br = 0; br < GRID; br++) {
            for (let bc = 0; bc < GRID; bc++) {
              if (w.tileMap.get(bc, br) === 'base') {
                w.tileMap.destroy(bc, br)
              }
            }
          }
          hit = true
          this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'big')
          w.pushEvent({ type: 'base_destroyed' })
        } else if (type === 'brick') {
          w.tileMap.destroy(c, r)
          hit = true
          this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
        } else if (type === 'steel') {
          if (bullet.power >= 2) {
            w.tileMap.destroy(c, r)
            this.createExplosion(c * CELL + CELL / 2, r * CELL + CELL / 2, 'small')
          }
          hit = true // Bullet stops regardless
        }
      }
    }
    return hit
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

      // Damage tank
      tank.hp--
      tank.hitCount = (tank.hitCount ?? 0) + 1
      this.createExplosion(bullet.x, bullet.y, 'small')

      if (tank.hp <= 0) {
        tank.alive = false
        this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')

        if (tank.isPlayer) {
          w.pushEvent({ type: 'tank_destroyed', tank, by: 'enemy' })
          w.pushEvent({ type: 'player_hit' })
        } else {
          const cfg = TANK_CONFIGS[tank.kind]
          w.score += cfg.score
          w.enemiesRemaining--
          w.addPopup({
            id: genId(),
            x: tank.x,
            y: tank.y,
            text: String(cfg.score),
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
    const types: PowerUpType[] = ['star', 'bomb', 'shield', 'freeze', 'tank', 'helmet']
    const type = types[Math.floor(Math.random() * types.length)]

    // Random position (not on walls)
    let x = 0,
      y = 0
    let tries = 0
    do {
      x = Math.floor(Math.random() * 12) * 2 * CELL
      y = Math.floor(Math.random() * 12) * 2 * CELL
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
    })
  }

  private updatePowerUps(): void {
    const w = this.world
    const p = w.player
    if (!p || !p.alive) return

    for (const pu of w.powerUps) {
      if (!pu.alive) continue
      pu.blinkTimer += 1000 / 60

      // Check player pickup
      if (aabb(p.x, p.y, p.w, p.h, pu.x, pu.y, pu.w, pu.h)) {
        pu.alive = false
        this.applyPowerUp(pu.type)
        w.pushEvent({ type: 'powerup_collected', powerUp: pu.type, by: 'player' })
      }
    }

    // Remove power-ups after some time? In classic, they stay until collected.
    // But let's add a timeout for gameplay.
    // Actually, classic power-ups stay. Let's keep them.
  }

  private applyPowerUp(type: PowerUpType): void {
    const w = this.world
    const p = w.player
    if (!p) return

    switch (type) {
      case 'star':
        if ((p.level ?? 0) < 3) {
          p.level = (p.level ?? 0) + 1
          w.playerLevel = p.level
          p.fireCooldown = FIRE_COOLDOWN[p.level] ?? 250
          if (p.level >= 3) p.speed = PLAYER_SPEED[3]
        }
        break

      case 'bomb':
        // Destroy all enemies on screen
        for (const tank of w.tanks) {
          if (!tank.alive) continue
          tank.alive = false
          this.createExplosion(tank.x + tank.w / 2, tank.y + tank.h / 2, 'big')
          const cfg = TANK_CONFIGS[tank.kind]
          w.score += cfg.score
          w.enemiesRemaining--
          w.addPopup({
            id: genId(),
            x: tank.x,
            y: tank.y,
            text: String(cfg.score),
            timer: 1500,
          })
        }
        break

      case 'shield':
        p.shieldTimer = SHIELD_DURATION_MS
        break

      case 'freeze':
        w.freezeTimer = FREEZE_DURATION_MS
        break

      case 'tank':
        w.lives++
        break

      case 'helmet':
        p.shieldTimer = RESPAWN_SHIELD_MS
        break
    }
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
        // Respawn player
        w.spawnPlayer()
        w.player.shieldTimer = RESPAWN_SHIELD_MS
      }
      return
    }

    // Stage clear — all enemies defeated
    if (w.enemiesRemaining <= 0 && w.tanks.length === 0) {
      w.state = 'stageclear'
      w.stageClearTimer = 3000
      w.pushEvent({ type: 'stage_clear', stage: w.stageIndex })
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
