import type { ThemeColors } from '../../types'
import type { Direction } from '../../constants'
import { TANK_KEY_MAP, ITEM_KEY_MAP } from './SpriteKeyMaps'
import type { SpriteLibrary } from './SpriteLibrary'
import type { SpriteCache } from './SpriteCache'
import { TerrainSpriteSlice } from './SpriteArtistTerrain'
import { TankSpriteSlice } from './SpriteArtistTanks'
import { EffectSpriteSlice } from './SpriteArtistEffects'

// Registry-derived key maps (§2.2) — re-exported here so existing consumers
// (slices, SpriteCache-adjacent code) keep their import path.
export { TANK_KEY_MAP, ITEM_KEY_MAP }

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

/**
 * Power-up glow pulse frequency — single source for `drawPowerUp`'s pulse
 * (`sin(frame * POWERUP_GLOW_FREQ)`) and the pre-rendered glow buckets
 * (`auraBucket(frame, POWERUP_GLOW_FREQ)`). Lives here so both SpriteCache
 * and the effects slice import ONE constant instead of a literal that had to
 * be kept in sync by hand (plan/refactor.trae.md §2.3).
 */
export const POWERUP_GLOW_FREQ = 0.11

/**
 * Paint the power-up glow halo (golden radial gradient) centered at (cx, cy)
 * for a CELL-sized power-up at pulse `p` ∈ [0, 1].
 *
 * Single source for BOTH consumers that used to hand-copy this math
 * (plan/refactor.trae.md §2.3):
 *  - SpriteCache.rebuildPowerUpGlow — bakes it into 16 pulse-bucket bitmaps;
 *  - EffectSpriteSlice.drawPowerUpGlowDirect — per-frame fallback when no
 *    cache bitmap exists.
 * Pixel-identical between the two paths by construction.
 */
export function paintPowerUpGlow(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  size: number,
  p: number,
): void {
  const glowR = size * (0.66 + 0.06 * p)
  const g = ctx.createRadialGradient(cx, cy, size * 0.12, cx, cy, glowR)
  g.addColorStop(0, `rgba(255, 224, 130, ${0.4 + 0.22 * p})`)
  g.addColorStop(0.55, `rgba(255, 200, 70, ${0.16 + 0.1 * p})`)
  g.addColorStop(1, 'rgba(255, 200, 70, 0)')
  const prevFill = ctx.fillStyle
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(cx, cy, glowR, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = prevFill
}

/**
 * Explosion bitmap draw geometry shared by the cached-blit and SVG-fallback
 * paths of drawExplosion: grown side length at progress `progress`.
 * `grow` = extra expansion for big vs small blasts.
 */
export function explosionSizeAt(size: number, progress: number, kind: 'small' | 'big'): number {
  return size * (0.6 + progress * (1.0 + (kind === 'big' ? 1.0 : 0.7)))
}

/** Explosion fade alpha at progress: opaque until 70%, then linear to 0. */
export function explosionAlphaAt(progress: number): number {
  return progress < 0.7 ? 1 : Math.max(0, 1 - (progress - 0.7) / 0.3)
}

// ================================================================
// Aura pre-rendering (R3 — eliminates per-frame path rasterization)
// ================================================================
//
// Auras (ally / hp-level / commander) are per-tank decorative rings whose
// only animation is a slow sine pulse on alpha (and a few shape params).
// Drawing them every frame via `beginPath`+`stroke`/`fill`+`createRadialGradient`
// is the dominant per-tank cost on software rasterizers (old machines w/o GPU).
//
// Strategy: pre-render N=16 pulse buckets per (type, level) into offscreen
// bitmaps at init time. At runtime, quantize the frame-derived pulse to a
// bucket index and `drawImage` the bitmap — 1 blit replaces 2–7 path ops,
// and the per-frame `createRadialGradient` (commander glow) is eliminated.
//
// Lossy aspect (DECISIONS.md §N): pulse is quantized to 16 buckets, so alpha
// changes in 6.25% steps instead of continuously. Visually indistinguishable
// at the pulse frequencies used (period ~50–80 frames). Anti-aliasing of path
// edges is preserved because the bitmap is rasterized at full alpha and blitted
// with the bucket's alpha baked in (drawImage multiplies per-pixel alpha).
//
// For commander, two out-of-phase pulses (0.12 + 0.08) drive different rings.
// Pre-rendering collapses them to a single pulse (pulse2 := pulse1); the inner
// ring then pulses in sync with the outer instead of slightly offset. This is
// the only visible approximation, and it is subtle (both are slow sine waves).

/** Number of pulse buckets per aura variant. 16 = ~6% alpha steps, visually smooth. */
export const AURA_BUCKETS = 16

export interface AuraConfig {
  /** Offscreen canvas size (logical px, before DPR). */
  canvasSize: number
  /** Aura bbox top-left offset from tank top-left: bitmap drawn at (tankX - offset, tankY - offset). */
  offset: number
  /** Pulse frequency: pulse = sin(frame * freq) * 0.5 + 0.5. */
  freq: number
}

/**
 * Per-aura config. `canvasSize` is sized to fit the largest extent of that aura
 * (e.g. commander crown spikes extend ~33px from center → 72px canvas with
 * offset 20 so center sits at canvas-relative (36, 36)).
 */
export const AURA_CONFIGS: Record<string, AuraConfig> = {
  ally: { canvasSize: 38, offset: 3, freq: 0.13 },
  hp2: { canvasSize: 40, offset: 4, freq: 0.08 },
  hp3: { canvasSize: 50, offset: 7, freq: 0.1 },
  hp4: { canvasSize: 44, offset: 6, freq: 0.12 },
  hp5: { canvasSize: 48, offset: 8, freq: 0.14 },
  hp6: { canvasSize: 56, offset: 12, freq: 0.16 },
  commander: { canvasSize: 72, offset: 20, freq: 0.12 },
}

/** Quantize a frame-based pulse into a bucket index [0, AURA_BUCKETS). */
export function auraBucket(frame: number, freq: number): number {
  const pulse = Math.sin(frame * freq) * 0.5 + 0.5
  const b = (pulse * AURA_BUCKETS) | 0
  return b < 0 ? 0 : b >= AURA_BUCKETS ? AURA_BUCKETS - 1 : b
}

/** Maps hpLevel (2–6) → aura config key. */
export const HP_LEVEL_KEYS: Record<number, string> = {
  2: 'hp2',
  3: 'hp3',
  4: 'hp4',
  5: 'hp5',
  6: 'hp6',
}

/** Draw ally aura paths at absolute coords (x, y = tank top-left). No save/restore. */
export function drawAllyAuraPaths(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  pulse: number,
): void {
  const m = 3
  const bx = x - m
  const by = y - m
  const bw = size + m * 2
  const bh = size + m * 2

  ctx.strokeStyle = '#B98CFF'
  ctx.lineWidth = 2
  ctx.globalAlpha = 0.7 + pulse * 0.3
  ctx.beginPath()
  ctx.ellipse(bx + bw / 2, by + bh / 2, bw * 0.55, bh * 0.55, 0, 0, Math.PI * 2)
  ctx.stroke()

  const cx = bx + bw / 2
  const top = by + 1
  ctx.globalAlpha = 0.9
  ctx.fillStyle = '#E6D4FF'
  ctx.beginPath()
  ctx.moveTo(cx, top + 5)
  ctx.lineTo(cx - 4, top + 1)
  ctx.lineTo(cx + 4, top + 1)
  ctx.closePath()
  ctx.fill()
}

/** Jagged (sawtooth) rectangle stroke helper for hp-level auras. */
function strokeJaggedRect(
  ctx: CanvasRenderingContext2D,
  rx: number,
  ry: number,
  rw: number,
  rh: number,
  notch = 2,
): void {
  ctx.beginPath()
  ctx.moveTo(rx, ry)
  ctx.lineTo(rx + rw * 0.33, ry - notch)
  ctx.lineTo(rx + rw * 0.66, ry + notch)
  ctx.lineTo(rx + rw, ry)

  ctx.lineTo(rx + rw + notch, ry + rh * 0.33)
  ctx.lineTo(rx + rw - notch, ry + rh * 0.66)
  ctx.lineTo(rx + rw, ry + rh)

  ctx.lineTo(rx + rw * 0.66, ry + rh + notch)
  ctx.lineTo(rx + rw * 0.33, ry + rh - notch)
  ctx.lineTo(rx, ry + rh)

  ctx.lineTo(rx - notch, ry + rh * 0.66)
  ctx.lineTo(rx + notch, ry + rh * 0.33)
  ctx.closePath()
  ctx.stroke()
}

/** Draw hp-level aura paths (level 2–6) at absolute coords. No save/restore. */
export function drawHpLevelAuraPaths(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  hpLevel: number,
  pulse: number,
): void {
  if (hpLevel <= 1 || hpLevel > 6) return
  const margin = 2
  const bx = x - margin
  const by = y - margin
  const bw = size + margin * 2
  const bh = size + margin * 2

  switch (hpLevel) {
    case 2: {
      ctx.strokeStyle = '#2ecc71'
      ctx.lineWidth = 1.5
      ctx.globalAlpha = 0.75 + pulse * 0.25
      ctx.strokeRect(bx, by, bw, bh)
      break
    }
    case 3: {
      ctx.strokeStyle = '#3498db'
      ctx.lineWidth = 1.5
      ctx.globalAlpha = 0.85
      ctx.strokeRect(bx, by, bw, bh)
      const gap = 3 + pulse * 1.5
      ctx.lineWidth = 1
      ctx.globalAlpha = 0.4 + pulse * 0.4
      ctx.strokeRect(bx - gap, by - gap, bw + gap * 2, bh + gap * 2)
      break
    }
    case 4: {
      ctx.strokeStyle = '#9b59b6'
      ctx.lineWidth = 2
      ctx.globalAlpha = 0.85 + pulse * 0.15
      strokeJaggedRect(ctx, bx, by, bw, bh, 2 + pulse * 1)
      break
    }
    case 5: {
      ctx.strokeStyle = '#e67e22'
      ctx.lineWidth = 2
      ctx.globalAlpha = 0.9
      strokeJaggedRect(ctx, bx, by, bw, bh, 2.5)
      const len = 5
      ctx.lineWidth = 1.5
      ctx.globalAlpha = 0.6 + pulse * 0.3
      const g = 3
      ctx.beginPath()
      ctx.moveTo(bx - g, by - g + len)
      ctx.lineTo(bx - g, by - g)
      ctx.lineTo(bx - g + len, by - g)
      ctx.moveTo(bx + bw + g - len, by - g)
      ctx.lineTo(bx + bw + g, by - g)
      ctx.lineTo(bx + bw + g, by - g + len)
      ctx.moveTo(bx + bw + g, by + bh + g - len)
      ctx.lineTo(bx + bw + g, by + bh + g)
      ctx.lineTo(bx + bw + g - len, by + bh + g)
      ctx.moveTo(bx - g + len, by + bh + g)
      ctx.lineTo(bx - g, by + bh + g)
      ctx.lineTo(bx - g, by + bh + g - len)
      ctx.stroke()
      break
    }
    case 6: {
      ctx.strokeStyle = '#e74c3c'
      ctx.lineWidth = 2.5
      ctx.globalAlpha = 0.95
      strokeJaggedRect(ctx, bx, by, bw, bh, 3)
      const g = 4 + pulse * 2
      ctx.lineWidth = 1.5
      ctx.globalAlpha = 0.5 + pulse * 0.4
      strokeJaggedRect(ctx, bx - g, by - g, bw + g * 2, bh + g * 2, 3.5)
      break
    }
  }
}

/** Draw commander aura paths at absolute coords. No save/restore. */
export function drawCommanderAuraPaths(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  size: number,
  pulse: number,
): void {
  const margin = 4
  const bx = x - margin
  const by = y - margin
  const bw = size + margin * 2
  const bh = size + margin * 2
  // Collapse two out-of-phase pulses (0.12 + 0.08) into one for bucketing.
  const pulse2 = pulse

  ctx.strokeStyle = '#f4c430'
  ctx.lineWidth = 2.5
  ctx.globalAlpha = 0.8 + pulse * 0.2
  ctx.beginPath()
  ctx.ellipse(bx + bw / 2, by + bh / 2, bw * 0.6, bh * 0.6, 0, 0, Math.PI * 2)
  ctx.stroke()

  ctx.strokeStyle = '#ffd700'
  ctx.lineWidth = 1.5
  ctx.globalAlpha = 0.6 + pulse2 * 0.4
  ctx.beginPath()
  ctx.ellipse(bx + bw / 2, by + bh / 2, bw * 0.45, bh * 0.45, 0, 0, Math.PI * 2)
  ctx.stroke()

  const cx = bx + bw / 2
  const cy = by + bh / 2
  const spikeLen = 8 + pulse * 3
  ctx.fillStyle = '#f4c430'
  ctx.globalAlpha = 0.9
  for (let i = 0; i < 4; i++) {
    const angle = (i * Math.PI) / 2 - Math.PI / 2
    const baseR = Math.max(bw, bh) * 0.55
    ctx.beginPath()
    ctx.moveTo(cx + Math.cos(angle) * baseR, cy + Math.sin(angle) * baseR)
    ctx.lineTo(cx + Math.cos(angle) * (baseR + spikeLen), cy + Math.sin(angle) * (baseR + spikeLen))
    ctx.lineTo(cx + Math.cos(angle + 0.15) * baseR, cy + Math.sin(angle + 0.15) * baseR)
    ctx.closePath()
    ctx.fill()
  }

  const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(bw, bh) * 0.4)
  glow.addColorStop(0, `rgba(255, 215, 0, ${0.3 + pulse * 0.2})`)
  glow.addColorStop(1, 'rgba(255, 215, 0, 0)')
  ctx.fillStyle = glow
  ctx.globalAlpha = 0.5
  ctx.beginPath()
  ctx.arc(cx, cy, Math.max(bw, bh) * 0.4, 0, Math.PI * 2)
  ctx.fill()
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
export class SpriteArtistCore {
  ctx: CanvasRenderingContext2D
  theme: ThemeColors
  lib: SpriteLibrary | null = null
  spriteCache: SpriteCache | null = null
  /**
   * When true, skip all SVG sprite rendering and fall through to the
   * theme-aware procedural drawing path. Set per-theme: Classic and Neon
   * themes have different palette priorities than the SVGs (which are
   * tuned for Modern Retro), so their tanks/terrain must use the procedural
   * fallback that reads from `this.theme`.
   */
  skipSvg = false

  /**
   * Low-quality render mode (mirrors GameRenderer.lowQuality). When true,
   * skips the tank contact shadow — a decorative ellipse that adds one
   * fillRect-equivalent draw per tank (6/frame in a typical scene). The shadow
   * is absent from the classic original; skipping it does not affect gameplay
   * readability.
   */
  lowQuality = false

  /**
   * Cached `measureText` widths for power-up countdown digits (R4 / P2-A).
   * Nested by `fontSize` (number — no string allocation) then by digit text.
   * The font is derived deterministically from the power-up size, so the key
   * space is tiny; caching avoids a ~6 µs `measureText` call per power-up per
   * frame. Nested structure replaces the old `Record<string, number>` keyed by
   * ``${fontSize}:${text}`` — that template string was 1 allocation per
   * power-up per frame.
   */
  digitWidthCache: Record<number, Record<string, number>> = {}

  /**
   * Cached `ctx.font` strings per `fontSize` (P0 GC fix). Power-ups are always
   * CELL-sized, so `fontSize` is constant → the font string is computed once
   * and reused. Avoids a template-string allocation per power-up per frame.
   */
  fontStringCache: Record<number, string> = {}

  // ---- Subsystem slices (§1.1 composition; back-references only) ----
  private readonly spriteTerrainSlice: TerrainSpriteSlice
  private readonly tankSpriteSlice: TankSpriteSlice
  private readonly spriteEffectSlice: EffectSpriteSlice

  constructor(ctx: CanvasRenderingContext2D, theme: ThemeColors) {
    // Slices take a back-reference; bodies never run during construction.
    this.spriteTerrainSlice = new TerrainSpriteSlice(this)
    this.tankSpriteSlice = new TankSpriteSlice(this)
    this.spriteEffectSlice = new EffectSpriteSlice(this)
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
  drawSvgCentered(
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
  // Public draw* API — thin delegators into the three slices below.
  //
  // This is deliberate, NOT dead forwarding: the slices call each other's
  // draws through this facade (`this.r.drawCommanderAura(...)` from
  // TankSpriteSlice etc.), and it keeps every external caller — renderer
  // slices, tests, tools — on one stable surface regardless of which slice
  // owns the body today. Parameter names are the real drawing coordinates.
  // ================================================================

  // ---- Terrain (bodies: spriteTerrainSlice) ----
  drawBrick(x: number, y: number, size: number): void {
    this.spriteTerrainSlice.drawBrick(x, y, size)
  }
  drawSteel(x: number, y: number, size: number, n = false, e = false, s = false, w = false): void {
    this.spriteTerrainSlice.drawSteel(x, y, size, n, e, s, w)
  }
  drawWater(x: number, y: number, size: number, frame: number): void {
    this.spriteTerrainSlice.drawWater(x, y, size, frame)
  }
  drawForest(x: number, y: number, size: number): void {
    this.spriteTerrainSlice.drawForest(x, y, size)
  }
  drawIce(x: number, y: number, size: number, n = false, e = false, s = false, w = false): void {
    this.spriteTerrainSlice.drawIce(x, y, size, n, e, s, w)
  }
  drawBase(x: number, y: number, size: number, destroyed: boolean, damage = 0): void {
    this.spriteTerrainSlice.drawBase(x, y, size, destroyed, damage)
  }
  // ---- Tanks (bodies: tankSpriteSlice) ----
  drawTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    bodyColor: string,
    turretColor: string,
    animFrame: number,
    level = 0,
  ): void {
    this.tankSpriteSlice.drawTank(x, y, size, dir, bodyColor, turretColor, animFrame, level)
  }
  drawPlayerTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    level: number,
    animFrame: number,
  ): void {
    this.tankSpriteSlice.drawPlayerTank(x, y, size, dir, level, animFrame)
  }
  drawPlayer2Tank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    level: number,
    animFrame: number,
  ): void {
    this.tankSpriteSlice.drawPlayer2Tank(x, y, size, dir, level, animFrame)
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
    isCommander = false,
  ): void {
    this.tankSpriteSlice.drawEnemyTank(
      x,
      y,
      size,
      dir,
      kind,
      animFrame,
      flash,
      hp,
      hitStage,
      isCommander,
    )
  }
  drawAllyTank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    animFrame: number,
    isDecoy = false,
  ): void {
    this.tankSpriteSlice.drawAllyTank(x, y, size, dir, animFrame, isDecoy)
  }
  drawAllyAura(x: number, y: number, size: number, frame: number): void {
    this.tankSpriteSlice.drawAllyAura(x, y, size, frame)
  }
  drawInsignia(x: number, y: number, size: number, level: string, isCommander = false): void {
    this.tankSpriteSlice.drawInsignia(x, y, size, level, isCommander)
  }
  // ---- Effects (bodies: spriteEffectSlice) ----
  drawBullet(x: number, y: number, size: number, dir: Direction): void {
    this.spriteEffectSlice.drawBullet(x, y, size, dir)
  }
  drawPowerUp(
    x: number,
    y: number,
    size: number,
    type: string,
    frame: number,
    lifeTimer?: number,
    maxLife?: number,
  ): void {
    this.spriteEffectSlice.drawPowerUp(x, y, size, type, frame, lifeTimer, maxLife)
  }
  drawSpawn(x: number, y: number, size: number, frame: number): void {
    this.spriteEffectSlice.drawSpawn(x, y, size, frame)
  }
  drawShield(x: number, y: number, size: number, frame: number): void {
    this.spriteEffectSlice.drawShield(x, y, size, frame)
  }
  drawExplosion(x: number, y: number, size: number, progress: number, kind: 'small' | 'big'): void {
    this.spriteEffectSlice.drawExplosion(x, y, size, progress, kind)
  }
  drawHpLevelAura(x: number, y: number, size: number, hpLevel: number, frame: number): void {
    this.spriteEffectSlice.drawHpLevelAura(x, y, size, hpLevel, frame)
  }
  drawCommanderAura(x: number, y: number, size: number, frame: number): void {
    this.spriteEffectSlice.drawCommanderAura(x, y, size, frame)
  }
}
