import type { ThemeColors } from '../types'
import type { Direction } from '../constants'

/**
 * SpriteFactory — draws all game sprites programmatically with Canvas 2D.
 * No external image assets needed.
 * Adding a new sprite = adding a draw method here.
 */
export class SpriteFactory {
  ctx: CanvasRenderingContext2D
  theme: ThemeColors

  constructor(ctx: CanvasRenderingContext2D, theme: ThemeColors) {
    this.ctx = ctx
    this.theme = theme
  }

  setTheme(theme: ThemeColors): void {
    this.theme = theme
  }

  // ---- Terrain ----

  drawBrick(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    ctx.fillStyle = t.brick
    ctx.fillRect(x, y, size, size)
    ctx.fillStyle = t.brickDark
    // Brick pattern: mortar lines
    const half = size / 2
    ctx.fillRect(x, y + half - 1, size, 1)
    ctx.fillRect(x + half - 1, y, 1, half)
    ctx.fillRect(x, y + size - 1, size, 1)
    ctx.fillRect(x + half - 1, y + half, 1, half)
    // Highlight
    ctx.fillStyle = t.brick
    ctx.fillRect(x + 1, y + 1, half - 2, half - 2)
    ctx.fillRect(x + half + 1, y + half + 1, half - 2, half - 2)
  }

  drawSteel(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    ctx.fillStyle = t.steel
    ctx.fillRect(x, y, size, size)
    ctx.fillStyle = t.steelDark
    ctx.fillRect(x, y + size - 2, size, 2)
    ctx.fillRect(x + size - 2, y, 2, size)
    ctx.fillStyle = '#e0e0e0'
    ctx.fillRect(x + 1, y + 1, size - 3, 2)
    ctx.fillRect(x + 1, y + 1, 2, size - 3)
  }

  drawWater(x: number, y: number, size: number, frame: number): void {
    const t = this.theme
    const ctx = this.ctx
    ctx.fillStyle = t.water
    ctx.fillRect(x, y, size, size)
    ctx.fillStyle = t.waterDark
    // Wave pattern
    const offset = Math.floor(frame / 30) % 2
    if (offset === 0) {
      ctx.fillRect(x + 2, y + 3, size - 4, 2)
      ctx.fillRect(x + 2, y + size - 5, size - 4, 2)
    } else {
      ctx.fillRect(x + 4, y + 3, size - 6, 2)
      ctx.fillRect(x, y + size - 5, size - 4, 2)
    }
  }

  drawForest(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    ctx.fillStyle = t.forest
    ctx.fillRect(x, y, size, size)
    ctx.fillStyle = t.forestDark
    // Tree pattern
    const s = size / 4
    ctx.fillRect(x + s, y, s * 2, s)
    ctx.fillRect(x, y + s, size, s * 2)
    ctx.fillRect(x + s, y + s * 3, s * 2, s)
  }

  drawIce(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    ctx.fillStyle = t.ice
    ctx.fillRect(x, y, size, size)
    ctx.fillStyle = '#a0e0ff'
    ctx.fillRect(x + 2, y + 2, size / 3, 2)
    ctx.fillRect(x + size / 2, y + size / 2, size / 3, 2)
  }

  drawBase(x: number, y: number, size: number, destroyed: boolean): void {
    const t = this.theme
    const ctx = this.ctx
    if (destroyed) {
      ctx.fillStyle = '#404040'
      ctx.fillRect(x, y, size, size)
      ctx.fillStyle = '#606060'
      ctx.fillRect(x + 2, y + 2, size - 4, size - 4)
      return
    }
    // Eagle symbol
    ctx.fillStyle = t.base
    ctx.fillRect(x, y, size, size)
    ctx.fillStyle = t.baseDark
    const s = size / 4
    // Body
    ctx.fillRect(x + s, y + s, s * 2, s * 2)
    // Wings
    ctx.fillRect(x, y + s, s, s)
    ctx.fillRect(x + s * 3, y + s, s, s)
    // Head
    ctx.fillStyle = t.base
    ctx.fillRect(x + s, y, s * 2, s)
    // Beak
    ctx.fillStyle = '#e04040'
    ctx.fillRect(x + (s * 3) / 2, y + s / 2, s, s / 2)
  }

  // ---- Tanks ----

  drawTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    bodyColor: string,
    turretColor: string,
    frame: number,
    level: number = 0,
  ): void {
    const ctx = this.ctx
    const s = size / 8 // unit size
    const t = (frame >> 2) & 1 // tread animation

    ctx.save()
    ctx.translate(x + size / 2, y + size / 2)
    // Rotate to direction
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    ctx.rotate(rot)
    ctx.translate(-size / 2, -size / 2)

    // Treads
    ctx.fillStyle = '#404040'
    ctx.fillRect(0, 0, s, size)
    ctx.fillRect(size - s, 0, s, size)
    // Tread details
    ctx.fillStyle = '#606060'
    for (let i = 0; i < 8; i++) {
      const yy = i * s + (t ? 0 : s / 2)
      if (yy < size) {
        ctx.fillRect(0, yy, s, s / 2)
        ctx.fillRect(size - s, yy, s, s / 2)
      }
    }

    // Body
    ctx.fillStyle = bodyColor
    ctx.fillRect(s, s * 2, size - s * 2, size - s * 4)

    // Body details (level-based)
    if (level >= 2) {
      ctx.fillStyle = '#f0f0f0'
      ctx.fillRect(s * 2, s * 3, s * 4, s * 2)
    }

    // Turret
    ctx.fillStyle = turretColor
    ctx.fillRect(s * 2, s * 2, s * 4, s * 4)
    // Turret center
    ctx.fillStyle = bodyColor
    ctx.fillRect(s * 3, s * 3, s * 2, s * 2)

    // Cannon
    ctx.fillStyle = turretColor
    ctx.fillRect(s * 3, 0, s * 2, s * 3)

    ctx.restore()
  }

  drawPlayerTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    level: number,
    frame: number,
  ): void {
    const t = this.theme
    const body = level >= 3 ? t.playerBody3 : t.playerBody
    const turret = t.playerTurret
    this.drawTank(x, y, size, dir, body, turret, frame, level)

    // Shield effect
    // (drawn separately by renderer when shieldTimer > 0)
  }

  drawEnemyTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    kind: string,
    frame: number,
    flash: boolean,
    hp: number,
    _maxHp: number,
  ): void {
    const t = this.theme
    let body: string
    let turret: string

    switch (kind) {
      case 'basic':
        body = t.enemyBasic
        turret = '#e0e0e0'
        break
      case 'fast':
        body = t.enemyFast
        turret = '#e0ffff'
        break
      case 'power':
        body = t.enemyPower
        turret = '#ffe0ff'
        break
      case 'armor':
        body = flash ? t.enemyArmorFlash : hp <= 1 ? t.enemyArmorFlash : t.enemyArmor
        turret = '#ffffe0'
        break
      default:
        body = t.enemyBasic
        turret = '#e0e0e0'
    }

    this.drawTank(x, y, size, dir, body, turret, frame, 0)
  }

  // ---- Bullets ----

  drawBullet(x: number, y: number, size: number, dir: Direction): void {
    const ctx = this.ctx
    const t = this.theme
    ctx.fillStyle = t.bullet
    // Bullet shape varies by direction
    const cx = x + size / 2
    const cy = y + size / 2
    ctx.beginPath()
    if (dir === 'up' || dir === 'down') {
      ctx.fillRect(cx - 1, y, 3, size)
    } else {
      ctx.fillRect(x, cy - 1, size, 3)
    }
    ctx.fill()
  }

  // ---- Power-ups ----

  drawPowerUp(x: number, y: number, size: number, type: string, frame: number): void {
    const ctx = this.ctx
    const t = this.theme
    const blink = Math.floor(frame / 8) % 2 === 0

    // Background
    ctx.fillStyle = blink ? t.powerUp : '#202020'
    ctx.fillRect(x, y, size, size)
    ctx.strokeStyle = t.powerUpGlow
    ctx.lineWidth = 2
    ctx.strokeRect(x + 1, y + 1, size - 2, size - 2)

    // Icon
    ctx.fillStyle = '#f0f0f0'
    const s = size / 8
    const cx = x + size / 2
    const cy = y + size / 2

    switch (type) {
      case 'star':
        // Star shape
        ctx.fillStyle = t.powerUpGlow
        ctx.beginPath()
        for (let i = 0; i < 5; i++) {
          const angle = (i * 4 * Math.PI) / 5 - Math.PI / 2
          const r = size * 0.3
          const px = cx + Math.cos(angle) * r
          const py = cy + Math.sin(angle) * r
          if (i === 0) ctx.moveTo(px, py)
          else ctx.lineTo(px, py)
        }
        ctx.closePath()
        ctx.fill()
        break

      case 'bomb':
        ctx.fillStyle = '#404040'
        ctx.beginPath()
        ctx.arc(cx, cy + s, size * 0.3, 0, Math.PI * 2)
        ctx.fill()
        ctx.fillStyle = '#e04040'
        ctx.fillRect(cx - s, cy - s * 2, s, s)
        break

      case 'shield':
        ctx.fillStyle = t.powerUpGlow
        ctx.beginPath()
        ctx.moveTo(cx, y + s)
        ctx.lineTo(x + size - s, y + s * 2)
        ctx.lineTo(x + size - s * 2, y + size - s)
        ctx.lineTo(cx, y + size - s)
        ctx.lineTo(x + s * 2, y + size - s)
        ctx.lineTo(x + s, y + s * 2)
        ctx.closePath()
        ctx.fill()
        break

      case 'freeze':
        ctx.strokeStyle = '#80c0ff'
        ctx.lineWidth = 2
        ctx.beginPath()
        for (let i = 0; i < 6; i++) {
          const angle = (i * Math.PI) / 3
          ctx.moveTo(cx, cy)
          ctx.lineTo(cx + Math.cos(angle) * size * 0.3, cy + Math.sin(angle) * size * 0.3)
        }
        ctx.stroke()
        break

      case 'tank':
        // Mini tank icon
        ctx.fillStyle = '#e0e0e0'
        ctx.fillRect(x + s * 2, y + s * 2, s * 4, s * 4)
        ctx.fillRect(x + s * 3, y + s, s * 2, s)
        break

      case 'helmet':
        ctx.fillStyle = t.powerUpGlow
        ctx.beginPath()
        ctx.arc(cx, cy + s, size * 0.3, Math.PI, 0)
        ctx.fill()
        ctx.fillRect(cx - size * 0.3, cy + s, size * 0.6, s * 2)
        break
    }
  }

  // ---- Spawn Animation ----

  drawSpawn(x: number, y: number, size: number, frame: number): void {
    const ctx = this.ctx
    const t = this.theme
    const phase = Math.floor(frame / 4) % 4
    const s = size / 2
    const cx = x + size / 2
    const cy = y + size / 2

    ctx.fillStyle = t.spawn
    // Star/spawn pattern
    switch (phase) {
      case 0:
        ctx.fillRect(cx - s, y, s * 2, size)
        break
      case 1:
        ctx.fillRect(x, cy - s, size, s * 2)
        break
      case 2:
        ctx.fillRect(x, y, size, size)
        ctx.fillStyle = '#000'
        ctx.fillRect(cx - s / 2, cy - s / 2, s, s)
        break
      case 3:
        ctx.fillRect(cx - s, y, s * 2, size)
        ctx.fillRect(x, cy - s, size, s * 2)
        break
    }
  }

  // ---- Shield Effect ----

  drawShield(x: number, y: number, size: number, frame: number): void {
    const ctx = this.ctx
    const blink = Math.floor(frame / 3) % 2 === 0
    ctx.strokeStyle = blink ? '#ffffff' : '#80c0ff'
    ctx.lineWidth = 2
    ctx.strokeRect(x - 2, y - 2, size + 4, size + 4)
  }

  // ---- Explosion ----

  drawExplosion(x: number, y: number, size: number, progress: number, kind: 'small' | 'big'): void {
    const ctx = this.ctx
    const t = this.theme
    const r = size * (0.3 + progress * 0.7)
    const cx = x
    const cy = y

    if (kind === 'big') {
      // Multi-layered explosion
      ctx.fillStyle = t.explosion3
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.fill()

      ctx.fillStyle = t.explosion2
      ctx.beginPath()
      ctx.arc(cx, cy, r * 0.7, 0, Math.PI * 2)
      ctx.fill()

      ctx.fillStyle = t.explosion1
      ctx.beginPath()
      ctx.arc(cx, cy, r * 0.4, 0, Math.PI * 2)
      ctx.fill()
    } else {
      ctx.fillStyle = t.explosion1
      ctx.beginPath()
      ctx.arc(cx, cy, r * 0.5, 0, Math.PI * 2)
      ctx.fill()
    }
  }
}
