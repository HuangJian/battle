// ================================================================
// EffectSpriteSlice — extracted from the former SpriteArtistEffects.ts mixin
// (plan/refactor.agy.md §1.1 mixin→composition). Bodies moved verbatim:
// `this.<slice-own>` stayed; everything else goes through the owning
// core instance back-reference (`this.r`).
// ================================================================
import type { Direction } from '../../constants'
import { POWERUP_GLOW_FREQ, paintPowerUpGlow, explosionSizeAt, explosionAlphaAt } from './SpriteArtistCore'
import {
  ITEM_KEY_MAP,
  HP_LEVEL_KEYS,
  AURA_CONFIGS,
  auraBucket,
  drawHpLevelAuraPaths,
  drawCommanderAuraPaths,
} from './SpriteArtistCore'
import type { SpriteArtistCore } from './SpriteArtistCore'

export class EffectSpriteSlice {
  constructor(private r: SpriteArtistCore) {}
  // ================================================================
  // Bullets
  // ================================================================

  drawBullet(x: number, y: number, size: number, dir: Direction): void {
    // Fast path: pre-rasterized bullet bitmap
    const cache = this.r.spriteCache
    if (cache?.built) {
      const sprite = cache.getBulletSprite()
      if (sprite) {
        const s2 = size * 1.5
        const cx = x + size / 2
        const cy = y + size / 2
        this.r.ctx.drawImage(sprite, cx - s2 / 2, cy - s2 / 2, s2, s2)
        return
      }
    }
    if (this.r.drawSvgCentered('bullet', x, y, size, 0, 1.5)) return
    const t = this.r.theme
    const ctx = this.r.ctx
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
    const ctx = this.r.ctx
    const cx = x + size / 2
    const cy = y + size / 2
    const key = ITEM_KEY_MAP[type]

    // --- animated golden halo (the "sparkle / glow" base of the unified look) ---
    // R4-glow: pre-rendered 16-pulse-bucket bitmap replaces per-frame
    // createRadialGradient + 3 addColorStop + arc fill. Pixel-identical.
    const cache = this.r.spriteCache
    if (cache?.built) {
      const glowSprite = cache.getPowerUpGlowSprite(auraBucket(frame, POWERUP_GLOW_FREQ))
      if (glowSprite) {
        const gs = cache.powerUpGlowCanvasSize
        ctx.drawImage(glowSprite, cx - gs / 2, cy - gs / 2, gs, gs)
      } else {
        this.drawPowerUpGlowDirect(cx, cy, size, frame)
      }
    } else {
      this.drawPowerUpGlowDirect(cx, cy, size, frame)
    }

    // --- pre-rasterized (or direct SVG) pentagon-framed item bitmap ---
    let drawn = false
    if (key) {
      const cache = this.r.spriteCache
      if (cache?.built) {
        const sprite = cache.getItemSprite(key)
        if (sprite) {
          ctx.drawImage(sprite, x, y, size, size)
          drawn = true
        }
      }
      if (!drawn && this.r.drawSvgCentered(key, x, y, size)) drawn = true
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

  /**
   * Direct (non-cached) power-up glow — fallback when SpriteCache is not
   * available (procedural / no-cache themes). Delegates to the shared
   * painter (§2.3) with the frame pulse, so it is pixel-identical to the
   * pre-rendered bitmap path by construction. Power-ups are always
   * CELL-sized (size === CELL).
   */
  drawPowerUpGlowDirect(cx: number, cy: number, size: number, frame: number): void {
    const p = 0.5 + 0.5 * Math.sin(frame * POWERUP_GLOW_FREQ)
    paintPowerUpGlow(this.r.ctx, cx, cy, size, p)
  }

  /** Draw countdown timer on power-up (top-right corner, visible from spawn) */
  drawPowerUpCountdown(
    cx: number,
    cy: number,
    size: number,
    lifeTimer: number,
    maxLife: number,
  ): void {
    const ctx = this.r.ctx
    const remaining = Math.max(0, maxLife - lifeTimer)
    const seconds = Math.ceil(remaining / 1000)

    // Show countdown from the beginning (not just last 10 seconds)
    // Position at top-right corner of the power-up bounding box
    const padding = size * 0.08
    const x = cx + size * 0.5 - padding
    const y = cy - size * 0.5 + padding

    // Font size: reasonable, not covering the power-up shape
    const fontSize = Math.max(9, size * 0.28)
    const prevFont = ctx.font
    const prevAlign = ctx.textAlign
    const prevBaseline = ctx.textBaseline
    const prevFillStyle = ctx.fillStyle
    // P0 GC fix: cache the font string per fontSize (power-ups are always
    // CELL-sized → fontSize is constant → string computed once, reused).
    ctx.font =
      this.r.fontStringCache[fontSize] ??
      (this.r.fontStringCache[fontSize] = `bold ${fontSize}px monospace`)
    ctx.textAlign = 'right'
    ctx.textBaseline = 'top'

    // Background rounded rect for readability (small, top-right)
    // Cache the width: the font is deterministic in `fontSize`, so key on it.
    // P0 GC fix: nested cache (fontSize → text → width) replaces the old
    // `Record<"${fontSize}:${text}", width>` — no template-string key alloc.
    const secStr = String(seconds)
    let sizeCache = this.r.digitWidthCache[fontSize]
    if (!sizeCache) {
      sizeCache = {}
      this.r.digitWidthCache[fontSize] = sizeCache
    }
    const textWidth = sizeCache[secStr] ?? (sizeCache[secStr] = ctx.measureText(secStr).width)
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

    ctx.fillText(secStr, x, y)
    ctx.font = prevFont
    ctx.textAlign = prevAlign
    ctx.textBaseline = prevBaseline
    ctx.fillStyle = prevFillStyle
  }

  /** Twinkling 4-point sparkles orbiting the item — the animated "sparkle". */
  drawPowerUpSparkles(cx: number, cy: number, size: number, frame: number): void {
    const ctx = this.r.ctx
    const n = 4
    const R = size * 0.44
    const prevCap = ctx.lineCap
    const prevAlpha = ctx.globalAlpha
    const prevStroke = ctx.strokeStyle
    const prevLW = ctx.lineWidth
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
    ctx.lineCap = prevCap
    ctx.globalAlpha = prevAlpha
    ctx.strokeStyle = prevStroke
    ctx.lineWidth = prevLW
  }

  /** Last-resort draw if the SVG sprite is missing: a plain gold pentagon + glyph. */
  drawPowerUpFallback(x: number, y: number, size: number, type: string): void {
    const ctx = this.r.ctx
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

    // Type-specific colors
    let fillColor = '#28409E'
    let strokeColor = '#F4C430'
    let glyphColor = '#FFE9A8'

    switch (type) {
      case 'fence':
        fillColor = '#808080'
        strokeColor = '#C0C0C0'
        glyphColor = '#E0E0E0'
        break
      case 'boat':
        fillColor = '#2060A0'
        strokeColor = '#40A0FF'
        glyphColor = '#80D0FF'
        break
    }

    ctx.fillStyle = fillColor
    ctx.fill()
    ctx.strokeStyle = strokeColor
    ctx.lineWidth = Math.max(1.5, size * 0.08)
    ctx.stroke()
    ctx.fillStyle = glyphColor
    ctx.beginPath()
    ctx.arc(cx, cy, size * 0.18, 0, Math.PI * 2)
    ctx.fill()
  }

  // ================================================================
  // Spawn Animation
  // ================================================================

  drawSpawn(x: number, y: number, size: number, frame: number): void {
    const ctx = this.r.ctx
    const t = this.r.theme
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
    const cache = this.r.spriteCache
    if (cache?.built) {
      const sprite = cache.getEffectSprite('fx.shield')
      if (sprite) {
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        this.r.ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        return
      }
    }
    if (this.r.drawSvgCentered('fx.shield', x, y, size, 0, 1.28)) return
    const ctx = this.r.ctx
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
    const ctx = this.r.ctx
    const t = this.r.theme

    // Fast path: pre-rasterized explosion bitmap (canvas-to-canvas blit, no SVG rasterize)
    const cache = this.r.spriteCache
    const expSprite = cache?.built ? cache.getExplosionSprite() : null
    if (expSprite) {
      this.blitExplosion(expSprite, x, y, size, progress, kind)
      return
    }

    // SVG fallback
    const img = this.r.lib?.get('fx.explosion')
    if (img) {
      this.blitExplosion(img, x, y, size, progress, kind)
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

  /** Blit an explosion source (cached bitmap or SVG image) with the shared
   *  grow/fade math (§2.3) — both drawExplosion fast paths were hand-copies. */
  private blitExplosion(
    src: CanvasImageSource,
    x: number,
    y: number,
    size: number,
    progress: number,
    kind: 'small' | 'big',
  ): void {
    const s2 = explosionSizeAt(size, progress, kind)
    const ctx = this.r.ctx
    ctx.globalAlpha = explosionAlphaAt(progress)
    ctx.drawImage(src, x - s2 / 2, y - s2 / 2, s2, s2)
    ctx.globalAlpha = 1
  }

  /**
   * Draw visual HP Level aura decoration around/under tank.
   * Levels 2~6 each feature a visually distinct ring shape & color.
   *
   * R3: fast path blits a pre-rendered bitmap (16 pulse buckets per level) —
   * 1 drawImage replaces 1–2 path ops + manual property save/restore. Fallback
   * draws paths directly when the SpriteCache is not built.
   */
  drawHpLevelAura(x: number, y: number, size: number, hpLevel: number, frame: number): void {
    if (hpLevel <= 1 || hpLevel > 6) return
    const key = HP_LEVEL_KEYS[hpLevel]
    const cfg = AURA_CONFIGS[key]
    const cache = this.r.spriteCache
    if (cache?.built) {
      const sprite = cache.getAuraSprite(key, auraBucket(frame, cfg.freq))
      if (sprite) {
        this.r.ctx.drawImage(sprite, x - cfg.offset, y - cfg.offset, cfg.canvasSize, cfg.canvasSize)
        return
      }
    }
    const ctx = this.r.ctx
    const pulse = Math.sin(frame * cfg.freq) * 0.5 + 0.5
    const prevStroke = ctx.strokeStyle
    const prevLW = ctx.lineWidth
    const prevAlpha = ctx.globalAlpha
    const prevFill = ctx.fillStyle
    drawHpLevelAuraPaths(ctx, x, y, size, hpLevel, pulse)
    ctx.strokeStyle = prevStroke
    ctx.lineWidth = prevLW
    ctx.globalAlpha = prevAlpha
    ctx.fillStyle = prevFill
  }

  /**
   * Draw elite commander visual decoration — a prominent pulsing aura
   * that makes commanders immediately recognizable on the battlefield.
   *
   * R3: fast path blits a pre-rendered bitmap (16 pulse buckets) — 1 drawImage
   * replaces 7 path ops (2 ring strokes + 4 spike fills + 1 gradient fill) +
   * manual property save/restore + a per-frame `createRadialGradient`. The
   * gradient allocation alone is a measurable cost on software rasterizers.
   * Fallback draws paths directly when the SpriteCache is not built.
   */
  drawCommanderAura(x: number, y: number, size: number, frame: number): void {
    const cfg = AURA_CONFIGS.commander
    const cache = this.r.spriteCache
    if (cache?.built) {
      const sprite = cache.getAuraSprite('commander', auraBucket(frame, cfg.freq))
      if (sprite) {
        this.r.ctx.drawImage(sprite, x - cfg.offset, y - cfg.offset, cfg.canvasSize, cfg.canvasSize)
        return
      }
    }
    const ctx = this.r.ctx
    const pulse = Math.sin(frame * cfg.freq) * 0.5 + 0.5
    const prevStroke = ctx.strokeStyle
    const prevLW = ctx.lineWidth
    const prevAlpha = ctx.globalAlpha
    const prevFill = ctx.fillStyle
    drawCommanderAuraPaths(ctx, x, y, size, pulse)
    ctx.strokeStyle = prevStroke
    ctx.lineWidth = prevLW
    ctx.globalAlpha = prevAlpha
    ctx.fillStyle = prevFill
  }
}
