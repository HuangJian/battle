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
    const tankKeys = ['tank.player1', 'tank.basic', 'tank.fast', 'tank.power', 'tank.armor']
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
    const effectKeys = [
      'fx.shield',
      'fx.starbuf1',
      'fx.starbuf2',
      'fx.starbuf3',
      'fx.hit0',
      'fx.hit1',
      'fx.hit2',
      'fx.hit3',
      'fx.hit4',
    ]
    for (const key of effectKeys) {
      const img = lib.get(key)
      if (!img) continue
      this.effectSprites.set(key, this.renderEffect(img))
    }

    // --- Item sprites (non-rotated, at tank cell size) ---
    const itemKeys = ['item.star', 'item.bomb', 'item.shield', 'item.freeze']
    for (const key of itemKeys) {
      const img = lib.get(key)
      if (!img) continue
      this.itemSprites.set(key, this.renderItemAtSize(img, TANK))
    }

    // --- Bullet sprite ---
    const bulletImg = lib.get('bullet')
    if (bulletImg) {
      this.bulletSprite = this.renderItemAtSize(bulletImg, BULLET_RENDER_SIZE)
    }

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
    this.itemSprites.clear()
    this.bulletSprite = null
    this.explosionSprite = null
    this.waterSprites = []
    this._built = false
  }
}
