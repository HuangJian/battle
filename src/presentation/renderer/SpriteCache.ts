import { TANK, BULLET, CELL } from '../../constants'
import type { SpriteLibrary } from './SpriteLibrary'
import { createOffscreenCanvas } from '../../utils/canvas'
import type { ThemeColors } from '../../types'
import { drawWaterTile } from './SpriteArtist'

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
    const tankKeys = ['tank.player1', 'tank.basic', 'tank.fast', 'tank.power', 'tank.armor', 'tank.ally']
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
    // Non-rotated centered badges (like the shield): a small chevron
    // cluster reads identically at any tank facing, so it needn't follow
    // the hull rotation. Drawn after the hull + hit overlay, before the
    // commander crown (plan §6). Pre-rasterized here for the same
    // GPU-blit reason as every other overlay.
    const insigniaKeys = ['fx.insignia.rookie', 'fx.insignia.soldier', 'fx.insignia.veteran']
    for (const key of insigniaKeys) {
      const img = lib.get(key)
      if (!img) continue
      this.insigniaSprites.set(key, this.renderEffect(img))
    }

    // --- Item sprites (non-rotated, at tank cell size) ---
    const itemKeys = [
      'item.star',
      'item.bomb',
      'item.shield',
      'item.freeze',
      'item.tank',
    ]
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
    return this.starbufSprites.get(`fx.starbuf${stage}`)?.[dirIndex]
  }

  /** Enemy hit/damage overlay for the given stage (1–4), pre-rotated to the tank's direction. */
  getHitSprite(stage: number, dirIndex: number): CanvasImageSource | undefined {
    return this.hitSprites.get(`fx.hit${stage}`)?.[dirIndex]
  }

  /** Rank insignia overlay for the given tier (Rookie/Soldier/Veteran), centered on the hull. */
  getInsigniaSprite(level: string): CanvasImageSource | undefined {
    return this.insigniaSprites.get(`fx.insignia.${level}`)
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
    this._built = false
  }
}
