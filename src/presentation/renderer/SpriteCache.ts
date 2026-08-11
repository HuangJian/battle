import { TANK, BULLET, CELL } from '../../constants'
import type { SpriteLibrary } from './SpriteLibrary'
import { createOffscreenCanvas } from '../../utils/canvas'
import type { ThemeColors } from '../../types'
import {
  drawWaterTile,
  AURA_CONFIGS,
  AURA_BUCKETS,
  drawAllyAuraPaths,
  drawHpLevelAuraPaths,
  drawCommanderAuraPaths,
} from './SpriteArtist'

/**
 * SpriteCache — pre-rasterizes SVG sprites to canvas bitmaps at init time.
 *
 * This is the single biggest rendering optimization: SVG drawImage requires
 * the browser to parse + rasterize the SVG tree on every call (or at least
 * check cache validity).  Canvas-to-canvas drawImage is a pure GPU blit.
 *
 * Tank sprites are pre-rendered in all 4 directions, eliminating per-frame
 * save/translate/rotate/drawImage/restore overhead.
 */

/** Render size for tank sprites (matches SpriteArtist's 1.28 scale) */
const TANK_RENDER_SIZE = TANK * 1.28 // ~41px

/** Canvas size for rotated sprites (must fit the diagonal) */
const SPRITE_CANVAS_SIZE = Math.ceil(TANK_RENDER_SIZE * Math.SQRT2) // ~58px

/** Bullet render size (matches SpriteArtist's 1.5 scale) */
const BULLET_RENDER_SIZE = BULLET * 1.5 // 9px

/** Explosion pre-render size (SVG artboard is 96×96) */
const EXPLOSION_SIZE = 96

/**
 * Power-up glow pulse frequency (matches SpriteArtist.drawPowerUp's
 * `Math.sin(frame * 0.11)`). Used by `auraBucket(frame, POWERUP_GLOW_FREQ)`
 * to quantize the pulse into 16 buckets for pre-rendered glow bitmaps.
 */
export const POWERUP_GLOW_FREQ = 0.11

/**
 * Canvas size for pre-rendered power-up glow bitmaps (logical px). Sized to
 * fit the max glow radius: `CELL * (0.66 + 0.06 * 1.0) * 2 = CELL * 1.44 ≈ 24`.
 */
const POWERUP_GLOW_CANVAS_SIZE = Math.ceil(CELL * 0.72 * 2) // 24

/**
 * Pre-computed sprite keys for starbuf/hit/insignia overlays. Module-level
 * constants replace per-call template strings (`fx.starbuf${stage}` etc.) —
 * those were GC pressure in the per-tank-per-frame hot path (6 tanks × 1-2
 * overlays = up to 12 short-lived strings/frame). Array/object indexing is
 * zero-allocation.
 */
const STARBUF_KEYS = ['fx.starbuf1', 'fx.starbuf2', 'fx.starbuf3'] as const
const HIT_KEYS = ['fx.hit1', 'fx.hit2', 'fx.hit3', 'fx.hit4'] as const
const INSIGNIA_KEYS: Record<string, string> = {
  rookie: 'fx.insignia.rookie',
  soldier: 'fx.insignia.soldier',
  veteran: 'fx.insignia.veteran',
}

/** Rotation values for each direction (matches SpriteArtist) */
const ROTATIONS = [0, Math.PI / 2, Math.PI, -Math.PI / 2] // up, right, down, left

export const DIR_TO_INDEX: Record<string, number> = {
  up: 0,
  right: 1,
  down: 2,
  left: 3,
}

export class SpriteCache {
  private tankSprites = new Map<string, CanvasImageSource[]>()
  private effectSprites = new Map<string, CanvasImageSource>()
  /** Player level-up star-buffer overlays — pre-rendered in all 4 directions so they rotate with the tank (they mimic the tank silhouette with side "tread" bars). */
  private starbufSprites = new Map<string, CanvasImageSource[]>()
  /** Enemy hit/damage overlays — same reasoning as star-buffers: the art mimics the tank silhouette with side "tread" bars, so it must rotate with the enemy tank. */
  private hitSprites = new Map<string, CanvasImageSource[]>()
  /** Rank insignia (Rookie/Soldier/Veteran): non-rotated centered chevron badges, drawn on the hull before the commander crown (plan §6). */
  private insigniaSprites = new Map<string, CanvasImageSource>()
  private itemSprites = new Map<string, CanvasImageSource>()
  private bulletSprite: CanvasImageSource | null = null
  private explosionSprite: CanvasImageSource | null = null
  /** Two phase-animated water frames (theme-aware), rebuilt on theme change. */
  private waterSprites: CanvasImageSource[] = []
  /**
   * Pre-rendered aura bitmaps (R3). Key = aura type (`'ally'`, `'hp2'`–`'hp6'`,
   * `'commander'`); value = array of `AURA_BUCKETS` bitmaps, one per pulse
   * bucket. Built once at init; rebuilt on DPR change. Eliminates per-frame
   * path rasterization for auras — 1 `drawImage` replaces 2–7 path ops.
   */
  private auraSprites = new Map<string, CanvasImageSource[]>()
  /**
   * Pre-rendered power-up glow bitmaps (R4-glow). 16 pulse buckets, each
   * rasterizing the golden radial gradient at the bucket's pulse value.
   * Eliminates per-frame `createRadialGradient` + 3 `addColorStop` + path
   * rasterization — replaced by a single `drawImage` blit. The glow is
   * theme-independent (fixed golden colors), so safe to bake at build time.
   */
  private powerUpGlowSprites: CanvasImageSource[] = []
  /**
   * Lazy-built composite tank bitmaps (R5-B). Outer key = `tankKey` (passed
   * through from caller, no construction). Inner array indexed by
   * `dirIndex * 20 + overlayNum * 10 + stage` (numeric — zero allocation).
   * Replaces the old `Map<string, CanvasImageSource>` with template-string
   * keys (`${tankKey}:${dirIdx}:${overlay}:${stage}`) — that was 1 short-lived
   * string per tank-with-overlay per frame (up to 12/frame in combat), which
   * is GC pressure on old machines. Array indexing is zero-allocation.
   *
   * Memory: ≤ 6 tankKeys × 80 slots × 8 bytes = 3.8 KB for the index arrays;
   * bitmaps themselves are lazy-built (~54 KB each, typically 10–20 entries).
   */
  private compositeTankCache = new Map<string, (CanvasImageSource | undefined)[]>()
  private static readonly COMPOSITE_TANK_ARR_SIZE = 80 // 4 dirs × 2 overlays × 10 stages (sparse)
  private dpr: number
  private _built = false

  constructor(dpr: number) {
    this.dpr = dpr
  }

  get built(): boolean {
    return this._built
  }

  /** Canvas size for tank/effect sprites (used by SpriteArtist for positioning) */
  get canvasSize(): number {
    return SPRITE_CANVAS_SIZE
  }

  build(lib: SpriteLibrary): void {
    if (this._built) return

    // --- Tank sprites: pre-render all 4 directions ---
    const tankKeys = [
      'tank.player1',
      'tank.basic',
      'tank.fast',
      'tank.power',
      'tank.armor',
      'tank.ally',
      'tank.decoy',
    ]
    for (const key of tankKeys) {
      const img = lib.get(key)
      if (!img) continue
      const canvases: CanvasImageSource[] = []
      for (const rot of ROTATIONS) {
        canvases.push(this.renderRotated(img, TANK_RENDER_SIZE, rot))
      }
      this.tankSprites.set(key, canvases)
    }

    // --- Effect overlays (non-rotated, at tank scale) ---
    // Only the shield lives here: it's a radially-symmetric bubble centered on
    // the tank, so it reads identically at any rotation and doesn't need to
    // follow the tank's facing.
    const effectKeys = ['fx.shield']
    for (const key of effectKeys) {
      const img = lib.get(key)
      if (!img) continue
      this.effectSprites.set(key, this.renderEffect(img))
    }

    // --- Star-buffer overlays (player level-up aura) ---
    // These mimic the tank silhouette with side "tread" bars, so they must
    // rotate WITH the player tank. Pre-render all 4 directions like tank
    // sprites; otherwise they sit upright on top of a left/right-facing tank
    // and read as mismatched vertical treads on the sides.
    const starbufKeys = ['fx.starbuf1', 'fx.starbuf2', 'fx.starbuf3']
    for (const key of starbufKeys) {
      const img = lib.get(key)
      if (!img) continue
      const canvases: CanvasImageSource[] = []
      for (const rot of ROTATIONS) {
        canvases.push(this.renderRotated(img, TANK_RENDER_SIZE, rot))
      }
      this.starbufSprites.set(key, canvases)
    }

    // --- Enemy hit/damage overlays (fx.hit1–hit4) ---
    // Same reasoning as the star-buffer: the art mimics the tank silhouette
    // with side "tread" bars, so it must rotate WITH the enemy tank. Pre-render
    // all 4 directions; otherwise they sit upright on a left/right-facing enemy
    // and read as mismatched vertical treads on the sides. (fx.hit0 is a
    // complete halo and is never drawn — the blit path clamps stage to >0.)
    const hitKeys = ['fx.hit1', 'fx.hit2', 'fx.hit3', 'fx.hit4']
    for (const key of hitKeys) {
      const img = lib.get(key)
      if (!img) continue
      const canvases: CanvasImageSource[] = []
      for (const rot of ROTATIONS) {
        canvases.push(this.renderRotated(img, TANK_RENDER_SIZE, rot))
      }
      this.hitSprites.set(key, canvases)
    }

    // --- Rank insignia (fx.insignia.rookie/soldier/veteran) ---
    // Pre-rotated 180° at bake time: drawInsignia always rotates the badge
    // by PI about its center, so baking that rotation into the sprite lets the
    // render path use a plain drawImage instead of save/translate/rotate/
    // restore — eliminating one save/restore pair per non-commander tank.
    const insigniaKeys = ['fx.insignia.rookie', 'fx.insignia.soldier', 'fx.insignia.veteran']
    for (const key of insigniaKeys) {
      const img = lib.get(key)
      if (!img) continue
      this.insigniaSprites.set(key, this.renderRotated(img, TANK_RENDER_SIZE, Math.PI))
    }

    // --- Item sprites (non-rotated, at tank cell size) ---
    const itemKeys = ['item.star', 'item.bomb', 'item.shield', 'item.freeze', 'item.tank', 'item.repair', 'item.decoy']
    for (const key of itemKeys) {
      const img = lib.get(key)
      if (!img) continue
      this.itemSprites.set(key, this.renderItemAtSize(img, TANK))
    }

    // --- Bullet sprite ---
    // Not baked here: the bullet is theme-colored (glow + core + tip), so it is
    // rasterized per-theme in rebuildBullet(), which runs on first render and on
    // every theme change. The original fixed gold SVG was invisible on the
    // Modern Retro cream field.

    // --- Explosion sprite (at artboard size, scaled during draw) ---
    const expImg = lib.get('fx.explosion')
    if (expImg) {
      this.explosionSprite = this.renderItemAtSize(expImg, EXPLOSION_SIZE)
    }

    // --- Aura bitmaps (R3): pre-render 16 pulse buckets per aura variant.
    // Theme-independent (aura colors are hardcoded), so safe to bake here.
    // Eliminates per-frame path rasterization for ally/hp/commander auras.
    this.rebuildAuras()

    // --- Power-up glow bitmaps (R4-glow): pre-render 16 pulse buckets of the
    // golden radial gradient. Eliminates per-frame createRadialGradient.
    this.rebuildPowerUpGlow()

    this._built = true
  }

  /**
   * Pre-rasterize the two water wave phases (theme-aware) into bitmaps. Called
   * once at init and again whenever the theme changes, so `drawWater` can blit
   * a phase per frame instead of redrawing water cells or (worse) allocating a
   * graphics-state per water cell via the old SVG path.
   */
  rebuildWater(theme: ThemeColors): void {
    this.waterSprites = []
    for (let phase = 0; phase < 2; phase++) {
      const { canvas, ctx } = createOffscreenCanvas(CELL * this.dpr, CELL * this.dpr, this.dpr)
      drawWaterTile(ctx, 0, 0, CELL, theme, phase)
      this.waterSprites.push(canvas)
    }
  }

  /**
   * Pre-rasterize all aura variants (ally / hp2–hp6 / commander) into bitmaps,
   * `AURA_BUCKETS` (=16) pulse buckets each (R3). Called once at init; rebuilt
   * on DPR change. Each bitmap is rasterized at a bucket-center pulse value;
   * at runtime `SpriteArtist.draw*Aura` quantizes the frame pulse to a bucket
   * index and `drawImage`s the bitmap — eliminating per-frame path
   * rasterization, `createRadialGradient` (commander), and manual property
   * save/restore.
   *
   * Memory: ~7 variants × 16 buckets × (38²–72²) px × 4 bytes ≈ 1.5 MB at DPR=2.
   * Acceptable: auras are gameplay-relevant (HP level / commander ID) and
   * always drawn; the bitmap blit is the cheapest possible draw path.
   */
  rebuildAuras(): void {
    this.auraSprites.clear()
    for (const key in AURA_CONFIGS) {
      const cfg = AURA_CONFIGS[key]
      const buckets: CanvasImageSource[] = []
      for (let b = 0; b < AURA_BUCKETS; b++) {
        const pulse = (b + 0.5) / AURA_BUCKETS
        const { canvas, ctx } = createOffscreenCanvas(
          cfg.canvasSize * this.dpr,
          cfg.canvasSize * this.dpr,
          this.dpr,
        )
        // Draw at (offset, offset) so the aura bbox top-left sits at canvas (0, 0).
        const ox = cfg.offset
        const oy = cfg.offset
        if (key === 'ally') {
          drawAllyAuraPaths(ctx, ox, oy, TANK, pulse)
        } else if (key === 'commander') {
          drawCommanderAuraPaths(ctx, ox, oy, TANK, pulse)
        } else {
          const level = parseInt(key.slice(2), 10)
          drawHpLevelAuraPaths(ctx, ox, oy, TANK, level, pulse)
        }
        // Reset ctx state (the path functions mutate fillStyle/strokeStyle/etc.).
        ctx.globalAlpha = 1
        buckets.push(canvas)
      }
      this.auraSprites.set(key, buckets)
    }
  }

  /**
   * Pre-rasterize the power-up glow halo (golden radial gradient) into 16
   * pulse-bucket bitmaps (R4-glow). Each bitmap bakes in the bucket's pulse
   * value, which determines the gradient's outer radius and per-stop alphas.
   * At runtime, `drawPowerUp` quantizes the frame pulse to a bucket index and
   * `drawImage`s the bitmap — eliminating per-frame `createRadialGradient` +
   * 3 `addColorStop` + `beginPath`+`arc`+`fill`.
   *
   * The glow is drawn at CELL size (power-ups are always CELL×CELL). The
   * bitmap is `POWERUP_GLOW_CANVAS_SIZE` (24px) wide, centered on the glow
   * center. Pixel-identical to the direct gradient fill (same colors, same
   * radius, same alphas — just pre-rasterized).
   *
   * Memory: 16 × (24 × dpr)² × 4 bytes ≈ 147 KB @ DPR=2. Negligible.
   */
  rebuildPowerUpGlow(): void {
    this.powerUpGlowSprites = []
    const cs = POWERUP_GLOW_CANVAS_SIZE
    const half = cs / 2
    for (let b = 0; b < AURA_BUCKETS; b++) {
      const pulse = (b + 0.5) / AURA_BUCKETS
      const glowR = CELL * (0.66 + 0.06 * pulse)
      const { canvas, ctx } = createOffscreenCanvas(cs * this.dpr, cs * this.dpr, this.dpr)
      const g = ctx.createRadialGradient(half, half, CELL * 0.12, half, half, glowR)
      g.addColorStop(0, `rgba(255, 224, 130, ${0.4 + 0.22 * pulse})`)
      g.addColorStop(0.55, `rgba(255, 200, 70, ${0.16 + 0.1 * pulse})`)
      g.addColorStop(1, 'rgba(255, 200, 70, 0)')
      ctx.fillStyle = g
      ctx.beginPath()
      ctx.arc(half, half, glowR, 0, Math.PI * 2)
      ctx.fill()
      this.powerUpGlowSprites.push(canvas)
    }
  }

  /**
   * Pre-rasterize the bullet as a theme-colored bitmap (glow + core + tip),
   * mirroring rebuildWater. Called once on first render and again on every
   * theme change so the bullet honors `theme.colors.bullet` / `bulletGlow`
   * instead of a fixed gold SVG — which was invisible on the Modern Retro
   * cream field. Player and enemy bullets share this look (the theme defines a
   * single bullet color), consistent with the rest of the theme system.
   */
  rebuildBullet(theme: ThemeColors): void {
    const size = BULLET_RENDER_SIZE
    const { canvas, ctx } = createOffscreenCanvas(size * this.dpr, size * this.dpr, this.dpr)
    const c = size / 2
    // Soft glow halo — layered translucent rings of the theme glow color
    ctx.fillStyle = theme.bulletGlow
    ctx.globalAlpha = 0.16
    ctx.beginPath()
    ctx.arc(c, c, c, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalAlpha = 0.28
    ctx.beginPath()
    ctx.arc(c, c, c * 0.72, 0, Math.PI * 2)
    ctx.fill()
    ctx.globalAlpha = 1
    // Solid core in the theme bullet color
    ctx.fillStyle = theme.bullet
    ctx.beginPath()
    ctx.arc(c, c, size * 0.27, 0, Math.PI * 2)
    ctx.fill()
    // Bright tip highlight
    ctx.fillStyle = '#ffffff'
    ctx.beginPath()
    ctx.arc(c, c, size * 0.1, 0, Math.PI * 2)
    ctx.fill()
    this.bulletSprite = canvas
  }

  // ---- Pre-render helpers ----

  private renderRotated(
    img: CanvasImageSource,
    renderSize: number,
    rotation: number,
  ): CanvasImageSource {
    const cs = SPRITE_CANVAS_SIZE
    const { canvas, ctx } = createOffscreenCanvas(cs * this.dpr, cs * this.dpr, this.dpr)
    ctx.translate(cs / 2, cs / 2)
    ctx.rotate(rotation)
    ctx.drawImage(img, -renderSize / 2, -renderSize / 2, renderSize, renderSize)
    return canvas
  }

  private renderEffect(img: CanvasImageSource): CanvasImageSource {
    const cs = SPRITE_CANVAS_SIZE
    const { canvas, ctx } = createOffscreenCanvas(cs * this.dpr, cs * this.dpr, this.dpr)
    const offset = (cs - TANK_RENDER_SIZE) / 2
    ctx.drawImage(img, offset, offset, TANK_RENDER_SIZE, TANK_RENDER_SIZE)
    return canvas
  }

  private renderItemAtSize(img: CanvasImageSource, size: number): CanvasImageSource {
    const { canvas, ctx } = createOffscreenCanvas(size * this.dpr, size * this.dpr, this.dpr)
    ctx.drawImage(img, 0, 0, size, size)
    return canvas
  }

  // ---- Getters ----

  getTankSprite(key: string, dirIndex: number): CanvasImageSource | undefined {
    return this.tankSprites.get(key)?.[dirIndex]
  }

  getEffectSprite(key: string): CanvasImageSource | undefined {
    return this.effectSprites.get(key)
  }

  /** Player level-up star-buffer overlay for the given stage (1–3), pre-rotated to the tank's direction. */
  getStarbufSprite(stage: number, dirIndex: number): CanvasImageSource | undefined {
    return this.starbufSprites.get(STARBUF_KEYS[stage - 1])?.[dirIndex]
  }

  /** Enemy hit/damage overlay for the given stage (1–4), pre-rotated to the tank's direction. */
  getHitSprite(stage: number, dirIndex: number): CanvasImageSource | undefined {
    return this.hitSprites.get(HIT_KEYS[stage - 1])?.[dirIndex]
  }

  /** Rank insignia overlay for the given tier (Rookie/Soldier/Veteran), centered on the hull. */
  getInsigniaSprite(level: string): CanvasImageSource | undefined {
    return this.insigniaSprites.get(INSIGNIA_KEYS[level])
  }

  getItemSprite(key: string): CanvasImageSource | undefined {
    return this.itemSprites.get(key)
  }

  getBulletSprite(): CanvasImageSource | null {
    return this.bulletSprite
  }

  getExplosionSprite(): CanvasImageSource | null {
    return this.explosionSprite
  }

  getWaterSprite(phase: number): CanvasImageSource | undefined {
    return this.waterSprites[phase % 2]
  }

  /** Pre-rendered aura bitmap for the given type and pulse bucket (R3). */
  getAuraSprite(key: string, bucket: number): CanvasImageSource | undefined {
    return this.auraSprites.get(key)?.[bucket]
  }

  /** Pre-rendered power-up glow bitmap for the given pulse bucket (R4-glow). */
  getPowerUpGlowSprite(bucket: number): CanvasImageSource | undefined {
    return this.powerUpGlowSprites[bucket]
  }

  /** Canvas size (logical px) for power-up glow bitmaps — for draw positioning. */
  get powerUpGlowCanvasSize(): number {
    return POWERUP_GLOW_CANVAS_SIZE
  }

  /**
   * Lazy-built composite tank bitmap (R5-B). Returns a single bitmap that is
   * the tank body sprite + overlay (starbuf/hit) composited together, so the
   * render path issues 1 `drawImage` instead of 2. Built on first access for a
   * given (tankKey, dirIndex, overlayKind, stage); stored in a numeric-indexed
   * array per tankKey (zero-allocation lookup — no string key construction).
   * Returns `undefined` if either source sprite is missing (caller falls back
   * to the 2-draw path).
   *
   * @param tankKey  Sprite key for the tank body (e.g. `'tank.player1'`).
   * @param dirIndex 0–3 (up/right/down/left).
   * @param overlayKind `'starbuf'` (player level overlay) or `'hit'` (enemy hit overlay).
   * @param stage  1–3 for starbuf, 1–4 for hit. MUST be > 0 (stage 0 = no overlay = no composite).
   */
  getCompositeTankSprite(
    tankKey: string,
    dirIndex: number,
    overlayKind: 'starbuf' | 'hit',
    stage: number,
  ): CanvasImageSource | undefined {
    // Numeric index: dirIndex(0-3) × 20 + overlayNum(0-1) × 10 + stage(0-4).
    // Max index = 3×20 + 1×10 + 4 = 74 < 80 (COMPOSITE_TANK_ARR_SIZE).
    const overlayNum = overlayKind === 'starbuf' ? 0 : 1
    const idx = dirIndex * 20 + overlayNum * 10 + stage
    let arr = this.compositeTankCache.get(tankKey)
    if (!arr) {
      arr = Array.from({
        length: SpriteCache.COMPOSITE_TANK_ARR_SIZE,
      }) as (CanvasImageSource | undefined)[]
      this.compositeTankCache.set(tankKey, arr)
    }
    const cached = arr[idx]
    if (cached) return cached
    const body = this.tankSprites.get(tankKey)?.[dirIndex]
    if (!body) return undefined
    const overlay =
      overlayKind === 'starbuf'
        ? this.starbufSprites.get(STARBUF_KEYS[stage - 1])?.[dirIndex]
        : this.hitSprites.get(HIT_KEYS[stage - 1])?.[dirIndex]
    if (!overlay) return undefined
    const cs = SPRITE_CANVAS_SIZE
    const { canvas, ctx } = createOffscreenCanvas(cs * this.dpr, cs * this.dpr, this.dpr)
    ctx.drawImage(body, 0, 0, cs, cs)
    ctx.drawImage(overlay, 0, 0, cs, cs)
    arr[idx] = canvas
    return canvas
  }

  clear(): void {
    this.tankSprites.clear()
    this.effectSprites.clear()
    this.starbufSprites.clear()
    this.hitSprites.clear()
    this.insigniaSprites.clear()
    this.itemSprites.clear()
    this.bulletSprite = null
    this.explosionSprite = null
    this.waterSprites = []
    this.auraSprites.clear()
    this.powerUpGlowSprites = []
    this.compositeTankCache.clear()
    this._built = false
  }
}
