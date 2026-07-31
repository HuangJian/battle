import type { ThemeColors } from '../../types'
import type { Direction } from '../../constants'
import type { SpriteLibrary } from './SpriteLibrary'
import type { SpriteCache } from './SpriteCache'
import { DIR_TO_INDEX, POWERUP_GLOW_FREQ } from './SpriteCache'

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
const HP_LEVEL_KEYS: Record<number, string> = { 2: 'hp2', 3: 'hp3', 4: 'hp4', 5: 'hp5', 6: 'hp6' }

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
  fence: 'item.fence',
  boat: 'item.boat',
  frenzy: 'item.frenzy',
  sacrifice: 'item.sacrifice',
  guard: 'item.guard',
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
  private digitWidthCache: Record<number, Record<string, number>> = {}

  /**
   * Cached `ctx.font` strings per `fontSize` (P0 GC fix). Power-ups are always
   * CELL-sized, so `fontSize` is constant → the font string is computed once
   * and reused. Avoids a template-string allocation per power-up per frame.
   */
  private fontStringCache: Record<number, string> = {}

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
    if (!this.skipSvg && this.drawSvgCentered('terrain.brick', x, y, size)) return
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

  /**
   * Steel wall, auto-tiled. `n/e/s/w` are true when the orthogonal neighbour is
   * also steel. A connected patch reads as ONE reinforced wall (铜墙铁壁): the
   * interior is a seamless fill, hinges strap every internal steel-steel seam
   * together, and obvious rivets pin the four outer corners.
   */
  drawSteel(x: number, y: number, size: number, n = false, e = false, s = false, w = false): void {
    const t = this.theme
    const ctx = this.ctx
    const s4 = size / 4
    const TAU = Math.PI * 2

    // Base fill — flat (no per-tile gradient), so a patch is one continuous slab.
    ctx.fillStyle = t.steel
    ctx.fillRect(x, y, size, size)

    // Seamless brushed-metal hatch: parallel diagonal lines, period = size,
    // so the texture tiles across cells without a visible seam.
    ctx.save()
    ctx.beginPath()
    ctx.rect(x, y, size, size)
    ctx.clip()
    ctx.lineWidth = 1
    for (let b = -size; b <= size; b += s4) {
      const k = Math.round(b / s4)
      ctx.strokeStyle = k % 2 === 0 ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)'
      ctx.beginPath()
      ctx.moveTo(x, y + b)
      ctx.lineTo(x + size, y + size + b)
      ctx.stroke()
    }
    ctx.restore()

    // Unified patch outline + bevel — only on sides bordering a different tile.
    const dark = t.steelDark
    const light = 'rgba(255,255,255,0.22)'
    const bevel = 2
    if (!n) {
      ctx.fillStyle = light
      ctx.fillRect(x, y, size, bevel)
      ctx.fillStyle = dark
      ctx.fillRect(x, y, size, 1)
    }
    if (!s) {
      ctx.fillStyle = dark
      ctx.fillRect(x, y + size - bevel, size, bevel)
    }
    if (!w) {
      ctx.fillStyle = light
      ctx.fillRect(x, y, bevel, size)
      ctx.fillStyle = dark
      ctx.fillRect(x, y, 1, size)
    }
    if (!e) {
      ctx.fillStyle = dark
      ctx.fillRect(x + size - bevel, y, bevel, size)
    }

    // Hinges straddling each internal (steel↔steel) seam — plates bolted together.
    const cx = x + size / 2
    const cy = y + size / 2
    const hinge = (ex: number, ey: number, vertical: boolean) => {
      const len = size * 0.5
      const thick = 4
      ctx.fillStyle = dark
      if (vertical) ctx.fillRect(ex - thick / 2, ey - len / 2, thick, len)
      else ctx.fillRect(ex - len / 2, ey - thick / 2, len, thick)
      ctx.fillStyle = 'rgba(255,255,255,0.3)'
      ctx.beginPath()
      ctx.arc(ex, ey, 1.4, 0, TAU)
      ctx.fill()
      ctx.fillStyle = 'rgba(0,0,0,0.35)'
      ctx.beginPath()
      ctx.arc(ex, ey, 0.8, 0, TAU)
      ctx.fill()
    }
    if (n) hinge(cx, y, false)
    if (s) hinge(cx, y + size, false)
    if (w) hinge(x, cy, true)
    if (e) hinge(x + size, cy, true)

    // Obvious rivets pinning the patch's four outer corners.
    const rivet = (rcx: number, rcy: number) => {
      ctx.fillStyle = 'rgba(0,0,0,0.4)'
      ctx.beginPath()
      ctx.arc(rcx, rcy + 0.5, 3.2, 0, TAU)
      ctx.fill()
      ctx.fillStyle = t.steelDark
      ctx.beginPath()
      ctx.arc(rcx, rcy, 2.6, 0, TAU)
      ctx.fill()
      ctx.fillStyle = 'rgba(255,255,255,0.25)'
      ctx.beginPath()
      ctx.arc(rcx, rcy, 1.6, 0, TAU)
      ctx.fill()
      ctx.fillStyle = 'rgba(255,255,255,0.85)'
      ctx.beginPath()
      ctx.arc(rcx - 0.8, rcy - 0.8, 0.9, 0, TAU)
      ctx.fill()
    }
    const o = 4.5
    if (!n && !w) rivet(x + o, y + o)
    if (!n && !e) rivet(x + size - o, y + o)
    if (!s && !w) rivet(x + o, y + size - o)
    if (!s && !e) rivet(x + size - o, y + size - o)
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
    if (!this.skipSvg && this.drawSvgCentered('terrain.forest', x, y, size)) return
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

  /**
   * Ice / snow field, auto-tiled. `n/e/s/w` true when the orthogonal neighbour is
   * also ice. A connected snowfield reads as ONE frozen surface: flat fill with
   * no per-tile gradient, a crack web whose fissures join across tiles (so the
   * whole patch shares one continuous network instead of per-tile snowflakes),
   * and a frost rim only around the perimeter.
   */
  drawIce(x: number, y: number, size: number, n = false, e = false, s = false, w = false): void {
    const t = this.theme
    const ctx = this.ctx
    const a = size / 3

    // Flat base — no per-tile gradient, so a snowfield is one continuous surface.
    ctx.fillStyle = t.ice
    ctx.fillRect(x, y, size, size)

    // Seamless crack web: 4 diagonal segments touching edges at 1/3 & 2/3.
    // Neighbours use the same fractions, so fissures join across tiles into one
    // connected frozen network rather than a grid of separate snowflakes.
    ctx.strokeStyle = 'rgba(255,255,255,0.45)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, y + a)
    ctx.lineTo(x + a * 2, y) // left@1/3 -> top@2/3
    ctx.moveTo(x, y + a * 2)
    ctx.lineTo(x + a, y + size) // left@2/3 -> bottom@1/3
    ctx.moveTo(x + size, y + a)
    ctx.lineTo(x + a * 2, y + size) // right@1/3 -> bottom@2/3
    ctx.moveTo(x + size, y + a * 2)
    ctx.lineTo(x + a, y) // right@2/3 -> top@1/3
    ctx.stroke()

    // Frost rim around the patch perimeter (boundary sides only).
    ctx.fillStyle = 'rgba(255,255,255,0.4)'
    const f = 2
    if (!n) ctx.fillRect(x, y, size, f)
    if (!s) ctx.fillRect(x, y + size - f, size, f)
    if (!w) ctx.fillRect(x, y, f, size)
    if (!e) ctx.fillRect(x + size - f, y, f, size)

    // A couple of sparkles for a frosty sheen (fixed, tiling positions).
    ctx.fillStyle = 'rgba(255,255,255,0.7)'
    ctx.fillRect(x + size * 0.22, y + size * 0.28, 1, 1)
    ctx.fillRect(x + size * 0.72, y + size * 0.62, 1, 1)
  }

  /**
   * Draws the base. `x,y` is the TOP-LEFT pixel of the 2×2 base block and
   * `size` is the full block size (2×CELL). The base is a single 3D energy
   * crystal spanning the whole block (not four separate tiles) — the caller
   * (GameRenderer) only invokes this once, for the block's top-left cell.
   */
  drawBase(x: number, y: number, size: number, destroyed: boolean, damage = 0): void {
    const key = destroyed ? 'terrain.base_ruins' : 'terrain.base'
    if (!this.skipSvg && this.drawSvgCentered(key, x, y, size)) {
      if (damage > 0 && !destroyed) this.drawBaseDamage(x, y, size, damage)
      return
    }

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
    if (damage > 0) this.drawBaseDamage(x, y, size, damage)
  }

  /**
   * Deterministic (no RNG) crack + scorch overlay drawn on top of the base
   * crystal. `damage` is 0..1; more damage → more / darker cracks. Geometry is
   * derived from the crack index (never from random draws) so the overlay stays
   * stable across frames (no flicker).
   */
  private drawBaseDamage(x: number, y: number, size: number, damage: number): void {
    const ctx = this.ctx
    const cx = x + size / 2
    const cy = y + size / 2
    const d = Math.max(0, Math.min(1, damage))

    // Save only the properties we mutate (avoids save()/restore() allocation).
    const prevAlpha = ctx.globalAlpha
    const prevFill = ctx.fillStyle
    const prevStroke = ctx.strokeStyle
    const prevLW = ctx.lineWidth
    const prevCap = ctx.lineCap
    const prevJoin = ctx.lineJoin

    // Scorch tint over the crystal body.
    ctx.globalAlpha = 0.16 + d * 0.34
    ctx.fillStyle = '#14181d'
    ctx.beginPath()
    ctx.moveTo(cx, y + size * 0.12)
    ctx.lineTo(x + size * 0.2, y + size * 0.45)
    ctx.lineTo(cx, y + size * 0.55)
    ctx.lineTo(x + size * 0.8, y + size * 0.45)
    ctx.closePath()
    ctx.fill()

    // Jagged cracks radiating in from the perimeter toward the core.
    const n = Math.max(1, Math.round(d * 6))
    ctx.strokeStyle = 'rgba(15,18,22,0.85)'
    ctx.lineWidth = Math.max(1, size * 0.03)
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    for (let i = 0; i < n; i++) {
      const ang = (Math.PI * 2 * i) / n + ((i * 2.3999632) % 1) * 0.7
      const r0 = size * 0.5
      const r1 = size * (0.1 + (i % 3) * 0.06)
      const px = cx + Math.cos(ang) * r0
      const py = cy + Math.sin(ang) * r0
      const ex = cx + Math.cos(ang) * r1
      const ey = cy + Math.sin(ang) * r1
      const kink = (i % 2 === 0 ? 1 : -1) * size * 0.09
      const mx = cx + (Math.cos(ang) * (r0 + r1)) / 2 + Math.sin(ang) * kink
      const my = cy + (Math.sin(ang) * (r0 + r1)) / 2 - Math.cos(ang) * kink
      ctx.beginPath()
      ctx.moveTo(px, py)
      ctx.lineTo(mx, my)
      ctx.lineTo(ex, ey)
      ctx.stroke()
    }

    ctx.globalAlpha = prevAlpha
    ctx.fillStyle = prevFill
    ctx.strokeStyle = prevStroke
    ctx.lineWidth = prevLW
    ctx.lineCap = prevCap
    ctx.lineJoin = prevJoin
  }

  // ================================================================
  // Tanks
  // ================================================================

  /**
   * Ground shadow drawn UNDER a tank, in world (non-rotated) space. Tank
   * sprites are pre-rotated per facing direction, so any shadow baked into the
   * sprite would spin with the body; this keeps the contact shadow fixed at the
   * tank's footprint regardless of direction.
   */
  private drawTankShadow(x: number, y: number, size: number): void {
    if (this.lowQuality) return // decorative — skipped in Performance Mode
    const ctx = this.ctx
    const cx = x + size / 2
    // Contact shadow sits at the BOTTOM of the footprint, low enough that a
    // clear crescent peeks out below the tank body (not hidden under it).
    const cy = y + size * 0.95
    // Only `fillStyle` is mutated here, so a cheap save/restore of that one
    // property replaces a full `save()`/`restore()` graphics-state push (R3 / P1-D).
    const prev = ctx.fillStyle
    ctx.fillStyle = 'rgba(0,0,0,0.22)'
    ctx.beginPath()
    ctx.ellipse(cx, cy, size * 0.45, size * 0.126, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = prev
  }

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
    // Non-rotating ground shadow (drawn under the tank, before any path)
    this.drawTankShadow(x, y, size)

    // Fast path: use pre-rasterized + pre-rotated sprite (no save/translate/rotate/restore)
    // Skip when skipSvg is set (Classic/Neon themes use procedural fallback
    // with theme-aware colors instead of the Modern-Retro-tuned SVGs).
    const cache = this.spriteCache
    if (cache?.built && !this.skipSvg) {
      const dirIdx = DIR_TO_INDEX[dir] ?? 0
      const stage = Math.max(0, Math.min(level ?? 0, 3))
      const cs = cache.canvasSize
      const cx = x + size / 2
      const cy = y + size / 2
      // R5-B: if there's a starbuf overlay, use the lazy-built composite bitmap
      // (body + overlay in one) — 1 drawImage instead of 2.
      if (stage > 0) {
        const composite = cache.getCompositeTankSprite('tank.player1', dirIdx, 'starbuf', stage)
        if (composite) {
          this.ctx.drawImage(composite, cx - cs / 2, cy - cs / 2, cs, cs)
          return
        }
      }
      const sprite = cache.getTankSprite('tank.player1', dirIdx)
      if (sprite) {
        const ctx = this.ctx
        ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        if (stage > 0) {
          const overlay = cache.getStarbufSprite(stage, dirIdx)
          if (overlay) ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
        }
        return
      }
    }

    // SVG fallback (also skipped when skipSvg is set)
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    if (!this.skipSvg && this.drawSvgCentered('tank.player1', x, y, size, rot, 1.28)) {
      const stage = Math.max(0, Math.min(level ?? 0, 3))
      if (stage > 0) this.drawSvgCentered(`fx.starbuf${stage}`, x, y, size, rot, 1.28)
      return
    }
    const t = this.theme
    const body = level >= 3 ? t.playerBody3 : level >= 2 ? t.playerBody2 : t.playerBody
    this.drawTank(x, y, size, dir, body, t.playerTurret, animFrame, level)
  }

  drawPlayer2Tank(
    x: number,
    y: number,
    size: number,
    dir: Direction,
    level: number,
    animFrame: number,
  ): void {
    // Lie-Back-Win-Mode: God AI tank uses the tank.player2 sprite.
    this.drawTankShadow(x, y, size)
    const cache = this.spriteCache
    if (cache?.built && !this.skipSvg) {
      const dirIdx = DIR_TO_INDEX[dir] ?? 0
      const stage = Math.max(0, Math.min(level ?? 0, 3))
      const cs = cache.canvasSize
      const cx = x + size / 2
      const cy = y + size / 2
      // R5-B: composite body + starbuf overlay into one blit.
      if (stage > 0) {
        const composite = cache.getCompositeTankSprite('tank.player2', dirIdx, 'starbuf', stage)
        if (composite) {
          this.ctx.drawImage(composite, cx - cs / 2, cy - cs / 2, cs, cs)
          return
        }
      }
      const sprite = cache.getTankSprite('tank.player2', dirIdx)
      if (sprite) {
        this.ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        if (stage > 0) {
          const overlay = cache.getStarbufSprite(stage, dirIdx)
          if (overlay) this.ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
        }
        return
      }
    }
    // SVG fallback: use tank.player2 asset.
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    if (!this.skipSvg && this.drawSvgCentered('tank.player2', x, y, size, rot, 1.28)) {
      const stage = Math.max(0, Math.min(level ?? 0, 3))
      if (stage > 0) this.drawSvgCentered(`fx.starbuf${stage}`, x, y, size, rot, 1.28)
      return
    }
    // Procedural fallback — use silver/green colors for player2.
    const t = this.theme
    this.drawTank(x, y, size, dir, t.playerBody2, t.playerTurret, animFrame, level)
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
    const key = TANK_KEY_MAP[kind] ?? 'tank.basic'

    // Non-rotating ground shadow (drawn under the tank, before any path)
    this.drawTankShadow(x, y, size)

    // Fast path: use pre-rasterized + pre-rotated sprite
    // Skip when skipSvg is set (Classic/Neon themes use procedural fallback
    // with theme-aware colors instead of the Modern-Retro-tuned SVGs).
    const cache = this.spriteCache
    if (cache?.built && !this.skipSvg) {
      const dirIdx = DIR_TO_INDEX[dir] ?? 0
      const stage = Math.max(0, Math.min(hitStage, 4))
      const cs = cache.canvasSize
      const cx = x + size / 2
      const cy = y + size / 2
      const ctx = this.ctx
      // R5-B: if there's a hit overlay, use the lazy-built composite bitmap
      // (body + overlay in one) — 1 drawImage instead of 2.
      if (stage > 0) {
        const composite = cache.getCompositeTankSprite(key, dirIdx, 'hit', stage)
        if (composite) {
          ctx.drawImage(composite, cx - cs / 2, cy - cs / 2, cs, cs)
          if (isCommander) this.drawCommanderAura(x, y, size, animFrame)
          return
        }
      }
      const sprite = cache.getTankSprite(key, dirIdx)
      if (sprite) {
        ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        if (stage > 0) {
          const overlay = cache.getHitSprite(stage, dirIdx)
          if (overlay) ctx.drawImage(overlay, cx - cs / 2, cy - cs / 2, cs, cs)
        }
        // Rank insignia (Rookie/Soldier/Veteran) is now drawn by the caller
        // LAST (see `drawInsignia`) so it sits above the HP level border,
        // bonus frame, and shield. Commanders draw the crown INSTEAD
        // (crown-xor-insignia, no stacking); None draws nothing (plan §6).
        // Commander visual decoration (prominent aura)
        if (isCommander) {
          this.drawCommanderAura(x, y, size, animFrame)
        }
        return
      }
    }

    // SVG fallback (also skipped when skipSvg is set)
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    if (!this.skipSvg && this.drawSvgCentered(key, x, y, size, rot, 1.28)) {
      const stage = Math.max(0, Math.min(hitStage, 4))
      if (stage > 0) this.drawSvgCentered(`fx.hit${stage}`, x, y, size, rot, 1.28)
      // Insignia drawn by the caller last (see `drawInsignia`).
      if (isCommander) this.drawCommanderAura(x, y, size, animFrame)
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
    if (isCommander) {
      this.drawCommanderAura(x, y, size, animFrame)
    }
  }

  /**
   * Draws the 天降神兵 allied guard — a TANK (same silhouette as every other
   * unit so it reads as a real combatant) but with a distinct PURPLE body and a
   * shield emblem on the turret in place of the player's star. Uses the
   * dedicated `tank.ally` sprite. Rotates to face direction like any tank.
   *
   * The underlying `kind` still governs HP/AI behaviour, but every ally reads as
   * the same friendly purple guard. Allies deliberately do NOT draw the enemy
   * rank insignia or commander crown — their friendly status is conveyed by the
   * purple body + shield emblem + ally aura.
   */
  drawAllyTank(x: number, y: number, size: number, dir: Direction, animFrame: number): void {
    const key = 'tank.ally'

    // Non-rotating ground shadow (same as every other tank)
    this.drawTankShadow(x, y, size)

    // Fast path: pre-rasterized + pre-rotated sprite
    // Skip when skipSvg is set (Classic/Neon themes use procedural fallback).
    const cache = this.spriteCache
    if (cache?.built && !this.skipSvg) {
      const dirIdx = DIR_TO_INDEX[dir] ?? 0
      const sprite = cache.getTankSprite(key, dirIdx)
      if (sprite) {
        const cs = cache.canvasSize
        const cx = x + size / 2
        const cy = y + size / 2
        this.ctx.drawImage(sprite, cx - cs / 2, cy - cs / 2, cs, cs)
        return
      }
    }

    // SVG fallback (also skipped when skipSvg is set)
    const rot =
      dir === 'up' ? 0 : dir === 'right' ? Math.PI / 2 : dir === 'down' ? Math.PI : -Math.PI / 2
    if (!this.skipSvg && this.drawSvgCentered(key, x, y, size, rot, 1.28)) return

    // Procedural fallback — purple ally tank (hardcoded; the real path is the
    // pre-rasterized purple sprite above, so theming the fallback isn't needed).
    this.drawTank(x, y, size, dir, '#8A4FD8', '#A06BE8', animFrame, 0)
  }

  /**
   * Draw the allied-guard aura — a soft pulsing purple ring + a small upward
   * chevron "friendly beacon" so the ally stays unmistakable on a busy field
   * (deliberately distinct from the gold enemy commander crown).
   *
   * R3: fast path blits a pre-rendered bitmap (16 pulse buckets) — 1 drawImage
   * replaces 2 path ops + manual property save/restore. Fallback draws paths
   * directly when the SpriteCache is not built.
   */
  drawAllyAura(x: number, y: number, size: number, frame: number): void {
    const cfg = AURA_CONFIGS.ally
    const cache = this.spriteCache
    if (cache?.built) {
      const sprite = cache.getAuraSprite('ally', auraBucket(frame, cfg.freq))
      if (sprite) {
        this.ctx.drawImage(sprite, x - cfg.offset, y - cfg.offset, cfg.canvasSize, cfg.canvasSize)
        return
      }
    }
    const ctx = this.ctx
    const pulse = Math.sin(frame * cfg.freq) * 0.5 + 0.5
    const prevStroke = ctx.strokeStyle
    const prevLW = ctx.lineWidth
    const prevAlpha = ctx.globalAlpha
    const prevFill = ctx.fillStyle
    drawAllyAuraPaths(ctx, x, y, size, pulse)
    ctx.strokeStyle = prevStroke
    ctx.lineWidth = prevLW
    ctx.globalAlpha = prevAlpha
    ctx.fillStyle = prevFill
  }

  /**
   * Draw the rank insignia (Rookie/Soldier/Veteran) as a standalone,
   * caller-driven pass. Kept OUT of `drawEnemyTank` so the renderer can
   * invoke it LAST — guaranteeing it renders above the HP level border,
   * the bonus frame, and the shield bubble (user: z-index above HP 等级边框).
   * Commanders draw the crown instead (crown-xor-insignia); None draws nothing.
   * The badge is enlarged 1.5× then a further 1.4× over the base
   * (cs/6 → cs/4 → ~cs/2.86) and "rides on" the tank's top-right corner
   * — its center sits on the corner point, so it straddles the edge (half
   * inside, half outside the hull). It is rotated 180° about its own center.
   */
  drawInsignia(x: number, y: number, size: number, level: string, isCommander = false): void {
    if (isCommander || level === 'none') return
    const cache = this.spriteCache
    const cs = cache?.canvasSize ?? Math.ceil(size * Math.SQRT2)
    const ins = (cs / 6) * 1.5 * 1.4 // base 1/6, +1.5×, +1.4×
    const cx = x + size / 2
    const cy = y + size / 2
    // Ride on the top-right corner: badge center on the corner point.
    const ix = cx + size / 2 - ins / 2
    const iy = cy - size / 2 - ins / 2
    if (cache?.built) {
      const img = cache.getInsigniaSprite(level)
      if (img) {
        // Sprite is pre-rotated 180° at bake time (SpriteCache.renderRotated),
        // so a plain drawImage suffices — no save/translate/rotate/restore.
        this.ctx.drawImage(img, ix, iy, ins, ins)
        return
      }
    }
    // SVG fallback — drawSvgCentered rotates about the badge center.
    this.drawSvgCentered(`fx.insignia.${level}`, ix, iy, ins, Math.PI, 1)
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
    // R4-glow: pre-rendered 16-pulse-bucket bitmap replaces per-frame
    // createRadialGradient + 3 addColorStop + arc fill. Pixel-identical.
    const cache = this.spriteCache
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

  /**
   * Direct (non-cached) power-up glow — fallback when SpriteCache is not
   * available (procedural / no-cache themes). Pixel-identical to the
   * pre-rendered bitmap path (R4-glow): same colors, same radii, same pulse
   * formula (`sin(frame * 0.11) * 0.5 + 0.5`). Inner radius `size * 0.12`,
   * outer `size * (0.66 + 0.06 * pulse)` — matches `rebuildPowerUpGlow` since
   * power-ups are always CELL-sized (size === CELL).
   */
  private drawPowerUpGlowDirect(cx: number, cy: number, size: number, frame: number): void {
    const ctx = this.ctx
    const pulse = 0.5 + 0.5 * Math.sin(frame * 0.11)
    const glowR = size * (0.66 + 0.06 * pulse)
    const g = ctx.createRadialGradient(cx, cy, size * 0.12, cx, cy, glowR)
    g.addColorStop(0, `rgba(255, 224, 130, ${0.4 + 0.22 * pulse})`)
    g.addColorStop(0.55, `rgba(255, 200, 70, ${0.16 + 0.1 * pulse})`)
    g.addColorStop(1, 'rgba(255, 200, 70, 0)')
    const prevFill = ctx.fillStyle
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(cx, cy, glowR, 0, Math.PI * 2)
    ctx.fill()
    ctx.fillStyle = prevFill
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
    const prevFont = ctx.font
    const prevAlign = ctx.textAlign
    const prevBaseline = ctx.textBaseline
    const prevFillStyle = ctx.fillStyle
    // P0 GC fix: cache the font string per fontSize (power-ups are always
    // CELL-sized → fontSize is constant → string computed once, reused).
    ctx.font =
      this.fontStringCache[fontSize] ??
      (this.fontStringCache[fontSize] = `bold ${fontSize}px monospace`)
    ctx.textAlign = 'right'
    ctx.textBaseline = 'top'

    // Background rounded rect for readability (small, top-right)
    // Cache the width: the font is deterministic in `fontSize`, so key on it.
    // P0 GC fix: nested cache (fontSize → text → width) replaces the old
    // `Record<"${fontSize}:${text}", width>` — no template-string key alloc.
    const secStr = String(seconds)
    let sizeCache = this.digitWidthCache[fontSize]
    if (!sizeCache) {
      sizeCache = {}
      this.digitWidthCache[fontSize] = sizeCache
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
  private drawPowerUpSparkles(cx: number, cy: number, size: number, frame: number): void {
    const ctx = this.ctx
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
  private drawPowerUpFallback(x: number, y: number, size: number, type: string): void {
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
    const cache = this.spriteCache
    if (cache?.built) {
      const sprite = cache.getAuraSprite(key, auraBucket(frame, cfg.freq))
      if (sprite) {
        this.ctx.drawImage(sprite, x - cfg.offset, y - cfg.offset, cfg.canvasSize, cfg.canvasSize)
        return
      }
    }
    const ctx = this.ctx
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
    const cache = this.spriteCache
    if (cache?.built) {
      const sprite = cache.getAuraSprite('commander', auraBucket(frame, cfg.freq))
      if (sprite) {
        this.ctx.drawImage(sprite, x - cfg.offset, y - cfg.offset, cfg.canvasSize, cfg.canvasSize)
        return
      }
    }
    const ctx = this.ctx
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
