import type { ThemeColors } from '../../types'
import type { Direction } from '../../constants'

/**
 * SpriteArtist — enhanced programmatic sprite drawing.
 * Draws all game sprites with Canvas 2D primitives at higher visual quality.
 * Theme-aware: colors come from the active theme.
 */
export class SpriteArtist {
  ctx: CanvasRenderingContext2D
  theme: ThemeColors

  constructor(ctx: CanvasRenderingContext2D, theme: ThemeColors) {
    this.ctx = ctx
    this.theme = theme
  }

  setTheme(theme: ThemeColors): void {
    this.theme = theme
  }

  // ================================================================
  // Terrain
  // ================================================================

  drawBrick(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    const s = size / 4

    // Base
    ctx.fillStyle = t.brick
    ctx.fillRect(x, y, size, size)

    // Brick pattern with mortar
    ctx.fillStyle = t.brickDark
    // Horizontal mortar lines
    ctx.fillRect(x, y + s - 1, size, 1)
    ctx.fillRect(x, y + s * 2 - 1, size, 1)
    ctx.fillRect(x, y + s * 3 - 1, size, 1)
    // Vertical mortar (offset rows)
    ctx.fillRect(x + s * 2 - 1, y, 1, s)
    ctx.fillRect(x + s - 1, y + s, 1, s)
    ctx.fillRect(x + s * 3 - 1, y + s, 1, s)
    ctx.fillRect(x + s * 2 - 1, y + s * 2, 1, s)
    ctx.fillRect(x + s - 1, y + s * 3, 1, s)
    ctx.fillRect(x + s * 3 - 1, y + s * 3, 1, s)

    // Highlights on brick faces
    ctx.fillStyle = 'rgba(255,255,255,0.08)'
    ctx.fillRect(x + 1, y + 1, s - 2, s - 2)
    ctx.fillRect(x + s * 2 + 1, y + 1, s - 2, s - 2)
    ctx.fillRect(x + s + 1, y + s + 1, s - 2, s - 2)
    ctx.fillRect(x + s * 3 + 1, y + s + 1, s - 2, s - 2)
    ctx.fillRect(x + 1, y + s * 2 + 1, s - 2, s - 2)
    ctx.fillRect(x + s * 2 + 1, y + s * 2 + 1, s - 2, s - 2)
  }

  drawSteel(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    const s = size / 4

    // Base
    ctx.fillStyle = t.steel
    ctx.fillRect(x, y, size, size)

    // Metallic gradient effect
    ctx.fillStyle = 'rgba(255,255,255,0.15)'
    ctx.fillRect(x, y, size, s)
    ctx.fillStyle = 'rgba(0,0,0,0.1)'
    ctx.fillRect(x, y + s * 3, size, s)

    // Border
    ctx.fillStyle = t.steelDark
    ctx.fillRect(x, y + size - 2, size, 2)
    ctx.fillRect(x + size - 2, y, 2, size)

    // Inner panels
    ctx.fillStyle = t.steelDark
    ctx.fillRect(x + s - 1, y, 1, size)
    ctx.fillRect(x + s * 3 - 1, y, 1, size)
    ctx.fillRect(x, y + s * 2 - 1, size, 1)

    // Rivets
    ctx.fillStyle = 'rgba(255,255,255,0.3)'
    ctx.fillRect(x + 2, y + 2, 2, 2)
    ctx.fillRect(x + size - 4, y + 2, 2, 2)
    ctx.fillRect(x + 2, y + size - 4, 2, 2)
    ctx.fillRect(x + size - 4, y + size - 4, 2, 2)
  }

  drawWater(x: number, y: number, size: number, frame: number): void {
    const t = this.theme
    const ctx = this.ctx
    const s = size / 4
    const phase = Math.floor(frame / 20) % 2

    // Base
    ctx.fillStyle = t.water
    ctx.fillRect(x, y, size, size)

    // Wave layers
    ctx.fillStyle = t.waterDark
    if (phase === 0) {
      ctx.fillRect(x + s, y + s, s * 2, 1)
      ctx.fillRect(x, y + s * 3, s * 2, 1)
    } else {
      ctx.fillRect(x + s * 2, y + s, s * 2, 1)
      ctx.fillRect(x + s, y + s * 3, s * 2, 1)
    }

    // Highlights
    ctx.fillStyle = 'rgba(255,255,255,0.15)'
    if (phase === 0) {
      ctx.fillRect(x + s, y + s - 2, s * 2, 1)
      ctx.fillRect(x, y + s * 3 - 2, s * 2, 1)
    } else {
      ctx.fillRect(x + s * 2, y + s - 2, s * 2, 1)
      ctx.fillRect(x + s, y + s * 3 - 2, s * 2, 1)
    }
  }

  drawForest(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    const s = size / 4

    // Base
    ctx.fillStyle = t.forest
    ctx.fillRect(x, y, size, size)

    // Tree canopy clusters
    ctx.fillStyle = t.forestDark
    // Tree 1 (top-left)
    ctx.fillRect(x + s, y, s * 2, s)
    ctx.fillRect(x, y + s, s, s)
    ctx.fillRect(x + s, y + s, s * 2, s)
    // Tree 2 (bottom-right)
    ctx.fillRect(x + s * 2, y + s * 2, s * 2, s)
    ctx.fillRect(x + s * 3, y + s * 3, s, s)
    ctx.fillRect(x + s, y + s * 3, s * 2, s)

    // Highlights
    ctx.fillStyle = 'rgba(255,255,255,0.1)'
    ctx.fillRect(x + s, y, s, 1)
    ctx.fillRect(x + s * 2, y + s * 2, s, 1)
  }

  drawIce(x: number, y: number, size: number): void {
    const t = this.theme
    const ctx = this.ctx
    const s = size / 4

    ctx.fillStyle = t.ice
    ctx.fillRect(x, y, size, size)

    // Crystalline pattern
    ctx.fillStyle = 'rgba(255,255,255,0.3)'
    ctx.fillRect(x + s, y + s, s * 2, 1)
    ctx.fillRect(x + s * 2, y + s * 2, s * 2, 1)
    ctx.fillRect(x + s, y + s, 1, s * 2)
    ctx.fillRect(x + s * 2, y + s * 2, 1, s * 2)

    // Shine
    ctx.fillStyle = 'rgba(255,255,255,0.5)'
    ctx.fillRect(x + 2, y + 2, 3, 1)
    ctx.fillRect(x + 2, y + 2, 1, 3)
  }

  drawBase(x: number, y: number, size: number, destroyed: boolean): void {
    const t = this.theme
    const ctx = this.ctx
    const s = size / 4

    if (destroyed) {
      // Ruins
      ctx.fillStyle = '#3a2a10'
      ctx.fillRect(x, y, size, size)
      ctx.fillStyle = '#5a4a20'
      ctx.fillRect(x + s, y + s, s * 2, s * 2)
      ctx.fillStyle = '#2a1a08'
      ctx.fillRect(x + s, y + s * 2, s, s)
      ctx.fillRect(x + s * 2, y + s, s, s)
      return
    }

    // Base background
    ctx.fillStyle = t.base
    ctx.fillRect(x, y, size, size)

    // Eagle body
    ctx.fillStyle = t.baseDark
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
    ctx.fillRect(x + s * 2, y + s, s, s / 2)
    // Feet
    ctx.fillStyle = t.baseDark
    ctx.fillRect(x + s, y + s * 3, s / 2, s)
    ctx.fillRect(x + s * 2 + s / 2, y + s * 3, s / 2, s)
  }

  // ================================================================
  // Tanks
  // ================================================================

  drawTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    bodyColor: string,
    turretColor: string,
    animFrame: number,
    level: number = 0,
  ): void {
    const ctx = this.ctx
    const s = size / 8

    ctx.save()
    ctx.translate(x + size / 2, y + size / 2)
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    ctx.rotate(rot)
    ctx.translate(-size / 2, -size / 2)

    // Treads (left and right)
    ctx.fillStyle = '#303030'
    ctx.fillRect(0, 0, s, size)
    ctx.fillRect(size - s, 0, s, size)

    // Tread details (animated)
    ctx.fillStyle = '#505050'
    for (let i = 0; i < 8; i++) {
      const yy = i * s + (animFrame ? 0 : s / 2)
      if (yy < size) {
        ctx.fillRect(0, yy, s, s / 2)
        ctx.fillRect(size - s, yy, s, s / 2)
      }
    }

    // Tread highlights
    ctx.fillStyle = '#606060'
    ctx.fillRect(0, 0, s, 1)
    ctx.fillRect(size - s, 0, s, 1)

    // Body
    ctx.fillStyle = bodyColor
    ctx.fillRect(s, s * 2, size - s * 2, size - s * 4)

    // Body highlight (top)
    ctx.fillStyle = 'rgba(255,255,255,0.15)'
    ctx.fillRect(s, s * 2, size - s * 2, s)

    // Body shadow (bottom)
    ctx.fillStyle = 'rgba(0,0,0,0.2)'
    ctx.fillRect(s, size - s * 3, size - s * 2, s)

    // Level details
    if (level >= 1) {
      ctx.fillStyle = '#f0f0f0'
      ctx.fillRect(s * 2, s * 5, s, s)
    }
    if (level >= 2) {
      ctx.fillStyle = '#f0f0f0'
      ctx.fillRect(s * 5, s * 5, s, s)
      // Armor plate
      ctx.fillStyle = 'rgba(255,255,255,0.1)'
      ctx.fillRect(s * 2, s * 3, s * 4, s * 2)
    }
    if (level >= 3) {
      ctx.fillStyle = '#f0f0f0'
      ctx.fillRect(s * 3, s * 5, s * 2, s)
    }

    // Turret
    ctx.fillStyle = turretColor
    ctx.fillRect(s * 2, s * 2, s * 4, s * 4)

    // Turret highlight
    ctx.fillStyle = 'rgba(255,255,255,0.2)'
    ctx.fillRect(s * 2, s * 2, s * 4, s)

    // Turret center
    ctx.fillStyle = bodyColor
    ctx.fillRect(s * 3, s * 3, s * 2, s * 2)

    // Cannon
    ctx.fillStyle = turretColor
    ctx.fillRect(s * 3, 0, s * 2, s * 3)
    // Cannon tip
    ctx.fillStyle = 'rgba(0,0,0,0.3)'
    ctx.fillRect(s * 3, 0, s * 2, s / 2)

    ctx.restore()
  }

  drawPlayerTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    level: number,
    animFrame: number,
  ): void {
    const t = this.theme
    const body = level >= 3 ? t.playerBody3 : level >= 2 ? t.playerBody2 : t.playerBody
    this.drawTank(x, y, size, dir, body, t.playerTurret, animFrame, level)
  }

  drawEnemyTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    kind: string,
    animFrame: number,
    flash: boolean,
    hp: number,
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

    this.drawTank(x, y, size, dir, body, turret, animFrame, 0)
  }

  // ================================================================
  // Bullets
  // ================================================================

  drawBullet(x: number, y: number, size: number, dir: Direction): void {
    const t = this.theme
    const ctx = this.ctx
    const cx = x + size / 2
    const cy = y + size / 2

    // Glow
    ctx.fillStyle = t.bulletGlow
    ctx.globalAlpha = 0.3
    if (dir === 'up' || dir === 'down') {
      ctx.fillRect(cx - 2, y - 1, 5, size + 2)
    } else {
      ctx.fillRect(x - 1, cy - 2, size + 2, 5)
    }
    ctx.globalAlpha = 1

    // Core
    ctx.fillStyle = t.bullet
    if (dir === 'up' || dir === 'down') {
      ctx.fillRect(cx - 1, y, 3, size)
    } else {
      ctx.fillRect(x, cy - 1, size, 3)
    }

    // Bright tip
    ctx.fillStyle = '#ffffff'
    if (dir === 'up') {
      ctx.fillRect(cx - 1, y, 3, 2)
    } else if (dir === 'down') {
      ctx.fillRect(cx - 1, y + size - 2, 3, 2)
    } else if (dir === 'left') {
      ctx.fillRect(x, cy - 1, 2, 3)
    } else {
      ctx.fillRect(x + size - 2, cy - 1, 2, 3)
    }
  }

  // ================================================================
  // Power-ups
  // ================================================================

  drawPowerUp(x: number, y: number, size: number, type: string, frame: number): void {
    const ctx = this.ctx
    const t = this.theme
    const blink = Math.floor(frame / 10) % 2 === 0
    const s = size / 8
    const cx = x + size / 2
    const cy = y + size / 2

    // Outer glow
    ctx.fillStyle = t.powerUpGlow
    ctx.globalAlpha = blink ? 0.2 : 0.05
    ctx.fillRect(x - 2, y - 2, size + 4, size + 4)
    ctx.globalAlpha = 1

    // Background
    ctx.fillStyle = blink ? t.powerUp : '#202020'
    ctx.fillRect(x, y, size, size)

    // Border
    ctx.strokeStyle = t.powerUpGlow
    ctx.lineWidth = 2
    ctx.strokeRect(x + 1, y + 1, size - 2, size - 2)

    // Icon
    switch (type) {
      case 'star':
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
        ctx.fillStyle = '#ffe040'
        ctx.fillRect(cx - s / 2, cy - s * 2, s / 2, s / 2)
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

  // ================================================================
  // Spawn Animation
  // ================================================================

  drawSpawn(x: number, y: number, size: number, frame: number): void {
    const ctx = this.ctx
    const t = this.theme
    const phase = Math.floor(frame / 4) % 4
    const s = size / 2
    const cx = x + size / 2
    const cy = y + size / 2

    ctx.fillStyle = t.spawn
    switch (phase) {
      case 0:
        ctx.fillRect(cx - s, y, s * 2, size)
        break
      case 1:
        ctx.fillRect(x, cy - s, size, s * 2)
        break
      case 2:
        ctx.fillRect(x, y, size, size)
        ctx.fillStyle = t.bg
        ctx.fillRect(cx - s / 2, cy - s / 2, s, s)
        break
      case 3:
        ctx.fillRect(cx - s, y, s * 2, size)
        ctx.fillRect(x, cy - s, size, s * 2)
        break
    }
  }

  // ================================================================
  // Shield Effect
  // ================================================================

  drawShield(x: number, y: number, size: number, frame: number): void {
    const ctx = this.ctx
    const blink = Math.floor(frame / 4) % 2 === 0
    ctx.strokeStyle = blink ? 'rgba(255,255,255,0.7)' : 'rgba(128,192,255,0.5)'
    ctx.lineWidth = 2
    ctx.strokeRect(x - 2, y - 2, size + 4, size + 4)

    // Inner glow
    ctx.strokeStyle = blink ? 'rgba(128,192,255,0.3)' : 'rgba(255,255,255,0.2)'
    ctx.lineWidth = 1
    ctx.strokeRect(x - 1, y - 1, size + 2, size + 2)
  }

  // ================================================================
  // Explosions (enhanced multi-stage)
  // ================================================================

  drawExplosion(x: number, y: number, size: number, progress: number, kind: 'small' | 'big'): void {
    const ctx = this.ctx
    const t = this.theme

    if (kind === 'big') {
      // Flash phase (0-0.15)
      if (progress < 0.15) {
        const flashAlpha = 1 - progress / 0.15
        ctx.fillStyle = `rgba(255,255,255,${flashAlpha * 0.6})`
        ctx.beginPath()
        ctx.arc(x, y, size * 1.2, 0, Math.PI * 2)
        ctx.fill()
      }

      // Expand phase
      const r = size * (0.2 + progress * 0.8)
      const fadeAlpha = progress < 0.7 ? 1 : 1 - (progress - 0.7) / 0.3

      // Outer
      ctx.globalAlpha = fadeAlpha * 0.7
      ctx.fillStyle = t.explosion3
      ctx.beginPath()
      ctx.arc(x, y, r, 0, Math.PI * 2)
      ctx.fill()

      // Mid
      ctx.globalAlpha = fadeAlpha * 0.85
      ctx.fillStyle = t.explosion2
      ctx.beginPath()
      ctx.arc(x, y, r * 0.65, 0, Math.PI * 2)
      ctx.fill()

      // Inner
      ctx.globalAlpha = fadeAlpha
      ctx.fillStyle = t.explosion1
      ctx.beginPath()
      ctx.arc(x, y, r * 0.35, 0, Math.PI * 2)
      ctx.fill()

      // Core
      if (progress < 0.5) {
        ctx.globalAlpha = 1 - progress * 2
        ctx.fillStyle = '#ffffff'
        ctx.beginPath()
        ctx.arc(x, y, r * 0.15, 0, Math.PI * 2)
        ctx.fill()
      }

      ctx.globalAlpha = 1
    } else {
      const r = size * (0.3 + progress * 0.5)
      const fadeAlpha = progress < 0.7 ? 1 : 1 - (progress - 0.7) / 0.3

      ctx.globalAlpha = fadeAlpha
      ctx.fillStyle = t.explosion1
      ctx.beginPath()
      ctx.arc(x, y, r * 0.5, 0, Math.PI * 2)
      ctx.fill()

      ctx.globalAlpha = fadeAlpha * 0.6
      ctx.fillStyle = t.explosion2
      ctx.beginPath()
      ctx.arc(x, y, r * 0.3, 0, Math.PI * 2)
      ctx.fill()

      ctx.globalAlpha = 1
    }
  }
}
