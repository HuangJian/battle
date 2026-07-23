import type { ThemeColors } from '../../types'
import type { Direction } from '../../constants'
import type { SpriteLibrary } from './SpriteLibrary'
import type { SpriteCache } from './SpriteCache'
import { DIR_TO_INDEX } from './SpriteCache'

/**
 * Draw a single water tile (procedural, theme-aware, phase-animated) into `ctx`
 * at (x, y), sized `size`×`size`. Extracted as a shared helper so SpriteCache
 * can pre-rasterize the two wave phases into bitmaps for cheap per-frame blits,
 * while the no-cache fallback still animates identically.
 */
export function drawWaterTile(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  theme: ThemeColors,
  phase: number,
): void {
  const s = size / 4
  // Base
  ctx.fillStyle = theme.water
  ctx.fillRect(x, y, size, size)

  // Wave layers
  ctx.fillStyle = theme.waterDark
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

/** Maps enemy tank kind → sprite key (module-level to avoid per-call allocation). */
const TANK_KEY_MAP: Record<string, string> = {
  basic: 'tank.basic',
  fast: 'tank.fast',
  power: 'tank.power',
  armor: 'tank.armor',
}

/** Maps power-up type → sprite key (module-level to avoid per-call allocation). */
const ITEM_KEY_MAP: Record<string, string> = {
  star: 'item.star',
  bomb: 'item.bomb',
  shield: 'item.shield',
  freeze: 'item.freeze',
  tank: 'item.tank',
  helmet: 'item.helmet',
}

/**
 * SpriteArtist — enhanced programmatic sprite drawing.
 * Draws all game sprites with Canvas 2D primitives at higher visual quality.
 * Theme-aware: colors come from the active theme.
 *
 * Performance: when a SpriteCache is available, tank/effect/bullet/explosion
 * sprites are drawn from pre-rasterized canvas bitmaps instead of SVG images.
 * This eliminates SVG parse/rasterize overhead and per-frame rotation.
 */
export class SpriteArtist {
  ctx: CanvasRenderingContext2D
  theme: ThemeColors
  lib: SpriteLibrary | null = null
  spriteCache: SpriteCache | null = null

  constructor(ctx: CanvasRenderingContext2D, theme: ThemeColors) {
    this.ctx = ctx
    this.theme = theme
  }

  setTheme(theme: ThemeColors): void {
    this.theme = theme
  }

  setLibrary(lib: SpriteLibrary): void {
    this.lib = lib
  }

  setSpriteCache(cache: SpriteCache): void {
    this.spriteCache = cache
  }

  /**
   * Draws an SVG sprite (96x96 artboard) centered in a size×size cell.
   * `scale` lets tanks slightly overflow the cell so treads reach the edges.
   * Returns false when the sprite is not loaded, so callers can fall back
   * to the procedural drawing.
   */
  private drawSvgCentered(
    key: string,
    x: number,
    y: number,
    size: number,
    rotationRad = 0,
    scale = 1,
  ): boolean {
    const img = this.lib?.get(key)
    if (!img) return false
    const ctx = this.ctx
    const s2 = size * scale
    if (!rotationRad) {
      // No rotation: blit directly. Avoids a per-call save()/restore() pair
      // (graphics-state allocation + stack push/pop) on the hot path — the
      // common case for water, power-ups, and base tiles.
      ctx.imageSmoothingEnabled = true
      ctx.drawImage(img, x + (size - s2) / 2, y + (size - s2) / 2, s2, s2)
      return true
    }
    const cx = x + size / 2
    const cy = y + size / 2
    ctx.save()
    ctx.translate(cx, cy)
    ctx.rotate(rotationRad)
    ctx.imageSmoothingEnabled = true
    ctx.drawImage(img, -s2 / 2, -s2 / 2, s2, s2)
    ctx.restore()
    return true
  }

  // ================================================================
  // Terrain
  // ================================================================

  drawBrick(x: number, y: number, size: number): void {
    if (this.drawSvgCentered('terrain.brick', x, y, size)) return
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
    if (this.drawSvgCentered('terrain.steel', x, y, size)) return
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
    // Fast path: pre-rasterized, phase-animated water bitmap (cheap blit, no
    // per-frame save/translate/restore). Replaces the old static-SVG path that
    // allocated a graphics state per water cell every frame and never animated.
    const cache = this.spriteCache
    if (cache?.built) {
      const phase = Math.floor(frame / 20) % 2
      const sprite = cache.getWaterSprite(phase)
      if (sprite) {
        this.ctx.drawImage(sprite, x, y, size, size)
        return
      }
    }
    // Fallback (no cache built yet): procedural animated water
    const phase = Math.floor(frame / 20) % 2
    drawWaterTile(this.ctx, x, y, size, this.theme, phase)
  }

  drawForest(x: number, y: number, size: number): void {
    if (this.drawSvgCentered('terrain.forest', x, y, size)) return
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
    if (this.drawSvgCentered('terrain.ice', x, y, size)) return
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

  /**
   * Draws the base. `x,y` is the TOP-LEFT pixel of the 2×2 base block and
   * `size` is the full block size (2×CELL). The base is a single 3D energy
   * crystal spanning the whole block (not four separate tiles) — the caller
   * (GameRenderer) only invokes this once, for the block's top-left cell.
   */
  drawBase(x: number, y: number, size: number, destroyed: boolean): void {
    const key = destroyed ? 'terrain.base_ruins' : 'terrain.base'
    if (this.drawSvgCentered(key, x, y, size)) return

    // Procedural fallback (only when the SVG is not yet loaded)
    const ctx = this.ctx
    const cx = x + size / 2
    if (destroyed) {
      ctx.fillStyle = '#5b6670'
      ctx.beginPath()
      ctx.moveTo(cx, y + size * 0.15)
      ctx.lineTo(x + size * 0.3, y + size * 0.55)
      ctx.lineTo(cx, y + size * 0.9)
      ctx.lineTo(x + size * 0.7, y + size * 0.55)
      ctx.closePath()
      ctx.fill()
      return
    }
    // Intact crystal fallback
    const g = ctx.createLinearGradient(0, y, 0, y + size)
    g.addColorStop(0, '#EAFBFF')
    g.addColorStop(1, '#3E9BE0')
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.moveTo(cx, y + size * 0.1)
    ctx.lineTo(x + size * 0.22, y + size * 0.45)
    ctx.lineTo(cx, y + size * 0.55)
    ctx.lineTo(x + size * 0.78, y + size * 0.45)
    ctx.closePath()
    ctx.fill()
    ctx.beginPath()
    ctx.moveTo(x + size * 0.22, y + size * 0.45)
    ctx.lineTo(cx, y + size * 0.55)
    ctx.lineTo(cx, y + size * 0.9)
    ctx.closePath()
    ctx.fill()
    ctx.fillStyle = '#ffffff'
    ctx.globalAlpha = 0.5
    ctx.beginPath()
    ctx.ellipse(cx, y + size * 0.5, size * 0.1, size * 0.14, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalAlpha = 1
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
    // Fast path: use pre-rasterized + pre-rotated sprite (no save/translate/rotate/restore)
    const cache = this.spriteCache
    if (cache?.built) {
      const dirIdx = DIR_TO_INDEX[dir] ?? 0
      const sprite = cache.getTankSprite('tank.player1', dirIdx)
      if (sprite) {
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        const ctx = this.ctx
        ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        // Star buffer overlay (pre-rotated to match the tank direction)
        const stage = Math.max(0, Math.min(level ?? 0, 3))
        if (stage > 0) {
          const overlay = cache.getStarbufSprite(stage, dirIdx)
          if (overlay) ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
        }
        return
      }
    }

    // SVG fallback
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    if (this.drawSvgCentered('tank.player1', x, y, size, rot, 1.28)) {
      const stage = Math.max(0, Math.min(level ?? 0, 3))
      if (stage > 0) this.drawSvgCentered(`fx.starbuf${stage}`, x, y, size, rot, 1.28)
      return
    }
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
    hitStage = 0,
  ): void {
    const key = TANK_KEY_MAP[kind] ?? 'tank.basic'

    // Fast path: use pre-rasterized + pre-rotated sprite
    const cache = this.spriteCache
    if (cache?.built) {
      const dirIdx = DIR_TO_INDEX[dir] ?? 0
      const sprite = cache.getTankSprite(key, dirIdx)
      if (sprite) {
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        const ctx = this.ctx
        ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        // Hit overlay — rotates with the enemy tank (it mimics the tank silhouette with side "tread" bars).
        const stage = Math.max(0, Math.min(hitStage, 4))
        if (stage > 0) {
          const overlay = cache.getHitSprite(stage, dirIdx)
          if (overlay) ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
        }
        return
      }
    }

    // SVG fallback
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    if (this.drawSvgCentered(key, x, y, size, rot, 1.28)) {
      const stage = Math.max(0, Math.min(hitStage, 4))
      if (stage > 0) this.drawSvgCentered(`fx.hit${stage}`, x, y, size, rot, 1.28)
      return
    }
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
    // Fast path: pre-rasterized bullet bitmap
    const cache = this.spriteCache
    if (cache?.built) {
      const sprite = cache.getBulletSprite()
      if (sprite) {
        const s2 = size * 1.5
        const cx = x + size / 2
        const cy = y + size / 2
        this.ctx.drawImage(sprite, cx - s2 / 2, cy - s2 / 2, s2, s2)
        return
      }
    }
    if (this.drawSvgCentered('bullet', x, y, size, 0, 1.5)) return
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

  drawPowerUp(
    x: number,
    y: number,
    size: number,
    type: string,
    frame: number,
    lifeTimer?: number,
    maxLife?: number,
  ): void {
    const ctx = this.ctx
    const cx = x + size / 2
    const cy = y + size / 2
    const key = ITEM_KEY_MAP[type]

    // --- animated golden halo (the "sparkle / glow" base of the unified look) ---
    const pulse = 0.5 + 0.5 * Math.sin(frame * 0.11)
    const glowR = size * (0.66 + 0.06 * pulse)
    const g = ctx.createRadialGradient(cx, cy, size * 0.12, cx, cy, glowR)
    g.addColorStop(0, `rgba(255, 224, 130, ${0.4 + 0.22 * pulse})`)
    g.addColorStop(0.55, `rgba(255, 200, 70, ${0.16 + 0.1 * pulse})`)
    g.addColorStop(1, 'rgba(255, 200, 70, 0)')
    ctx.save()
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(cx, cy, glowR, 0, Math.PI * 2)
    ctx.fill()
    ctx.restore()

    // --- pre-rasterized (or direct SVG) pentagon-framed item bitmap ---
    let drawn = false
    if (key) {
      const cache = this.spriteCache
      if (cache?.built) {
        const sprite = cache.getItemSprite(key)
        if (sprite) {
          ctx.drawImage(sprite, x, y, size, size)
          drawn = true
        }
      }
      if (!drawn && this.drawSvgCentered(key, x, y, size)) drawn = true
    }

    // --- fallback: draw a unified pentagon badge so items never go invisible ---
    if (!drawn) {
      this.drawPowerUpFallback(x, y, size, type)
    }

    // --- twinkling sparkles on top (unified "闪闪发光" effect) ---
    this.drawPowerUpSparkles(cx, cy, size, frame)

    // --- countdown timer display ---
    if (lifeTimer !== undefined && maxLife !== undefined && maxLife > 0) {
      this.drawPowerUpCountdown(cx, cy, size, lifeTimer, maxLife)
    }
  }

  /** Draw countdown timer on power-up (top-right corner, visible from spawn) */
  private drawPowerUpCountdown(
    cx: number,
    cy: number,
    size: number,
    lifeTimer: number,
    maxLife: number,
  ): void {
    const ctx = this.ctx
    const remaining = Math.max(0, maxLife - lifeTimer)
    const seconds = Math.ceil(remaining / 1000)

    // Show countdown from the beginning (not just last 10 seconds)
    // Position at top-right corner of the power-up bounding box
    const padding = size * 0.08
    const x = cx + size * 0.5 - padding
    const y = cy - size * 0.5 + padding

    // Font size: reasonable, not covering the power-up shape
    const fontSize = Math.max(9, size * 0.28)
    ctx.save()
    ctx.font = `bold ${fontSize}px monospace`
    ctx.textAlign = 'right'
    ctx.textBaseline = 'top'

    // Background rounded rect for readability (small, top-right)
    const textWidth = ctx.measureText(String(seconds)).width
    const bgPadding = fontSize * 0.25
    const bgWidth = textWidth + bgPadding * 2
    const bgHeight = fontSize + bgPadding * 0.8
    const bgX = x - bgWidth + bgPadding
    const bgY = y - bgPadding * 0.4
    const radius = bgHeight * 0.35

    ctx.fillStyle = 'rgba(0, 0, 0, 0.75)'
    ctx.beginPath()
    ctx.roundRect(bgX, bgY, bgWidth, bgHeight, radius)
    ctx.fill()

    // Countdown text - color changes based on urgency
    if (seconds <= 3) {
      ctx.fillStyle = '#ff4444' // Red for urgent
    } else if (seconds <= 5) {
      ctx.fillStyle = '#ffaa00' // Orange for warning
    } else if (seconds <= 10) {
      ctx.fillStyle = '#ffff00' // Yellow for attention
    } else {
      ctx.fillStyle = '#ffffff' // White for normal
    }

    ctx.fillText(String(seconds), x, y)
    ctx.restore()
  }

  /** Twinkling 4-point sparkles orbiting the item — the animated "sparkle". */
  private drawPowerUpSparkles(cx: number, cy: number, size: number, frame: number): void {
    const ctx = this.ctx
    const n = 4
    const R = size * 0.44
    ctx.save()
    ctx.lineCap = 'round'
    for (let i = 0; i < n; i++) {
      const ang = (i / n) * Math.PI * 2 + frame * 0.025
      const sx = cx + Math.cos(ang) * R
      const sy = cy + Math.sin(ang) * R * 0.82
      const tw = 0.5 + 0.5 * Math.sin(frame * 0.16 + i * 1.7)
      const len = size * 0.07 * (0.5 + 0.7 * tw)
      ctx.globalAlpha = 0.25 + 0.65 * tw
      ctx.strokeStyle = '#FFF6C8'
      ctx.lineWidth = Math.max(1, size * 0.03)
      ctx.beginPath()
      ctx.moveTo(sx - len, sy)
      ctx.lineTo(sx + len, sy)
      ctx.moveTo(sx, sy - len)
      ctx.lineTo(sx, sy + len)
      ctx.stroke()
    }
    ctx.restore()
  }

  /** Last-resort draw if the SVG sprite is missing: a plain gold pentagon + glyph. */
  private drawPowerUpFallback(x: number, y: number, size: number, _type: string): void {
    const ctx = this.ctx
    const cx = x + size / 2
    const cy = y + size / 2
    ctx.beginPath()
    for (let i = 0; i < 5; i++) {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / 5
      const r = size * 0.46
      const px = cx + Math.cos(a) * r
      const py = cy + Math.sin(a) * r
      if (i === 0) ctx.moveTo(px, py)
      else ctx.lineTo(px, py)
    }
    ctx.closePath()
    ctx.fillStyle = '#28409E'
    ctx.fill()
    ctx.strokeStyle = '#F4C430'
    ctx.lineWidth = Math.max(1.5, size * 0.08)
    ctx.stroke()
    ctx.fillStyle = '#FFE9A8'
    ctx.beginPath()
    ctx.arc(cx, cy, size * 0.18, 0, Math.PI * 2)
    ctx.fill()
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
    // Fast path: pre-rasterized shield bitmap
    const cache = this.spriteCache
    if (cache?.built) {
      const sprite = cache.getEffectSprite('fx.shield')
      if (sprite) {
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        this.ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        return
      }
    }
    if (this.drawSvgCentered('fx.shield', x, y, size, 0, 1.28)) return
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

    // Fast path: pre-rasterized explosion bitmap (canvas-to-canvas blit, no SVG rasterize)
    const cache = this.spriteCache
    const expSprite = cache?.built ? cache.getExplosionSprite() : null
    if (expSprite) {
      const grow = kind === 'big' ? 1.0 : 0.7
      const s2 = size * (0.6 + progress * (1.0 + grow))
      const alpha = progress < 0.7 ? 1 : Math.max(0, 1 - (progress - 0.7) / 0.3)
      ctx.globalAlpha = alpha
      ctx.drawImage(expSprite, x - s2 / 2, y - s2 / 2, s2, s2)
      ctx.globalAlpha = 1
      return
    }

    // SVG fallback
    const img = this.lib?.get('fx.explosion')
    if (img) {
      const grow = kind === 'big' ? 1.0 : 0.7
      const s2 = size * (0.6 + progress * (1.0 + grow))
      const alpha = progress < 0.7 ? 1 : Math.max(0, 1 - (progress - 0.7) / 0.3)
      ctx.globalAlpha = alpha
      ctx.drawImage(img, x - s2 / 2, y - s2 / 2, s2, s2)
      ctx.globalAlpha = 1
      return
    }

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
