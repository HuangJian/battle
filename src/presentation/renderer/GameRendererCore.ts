import type { World } from '../../game/World'
import type { TileMap } from '../../game/TileMap'
import { FIELD, GRID } from '../../constants'
import { SpriteArtist } from './SpriteArtist'
import type { SpriteLibrary } from './SpriteLibrary'
import type { SpriteCache } from './SpriteCache'
import type { Camera } from '../Camera'
import type { AnimationSystem } from '../AnimationSystem'
import type { ParticleSystem } from '../ParticleSystem'
import type { EffectsSystem } from '../EffectsSystem'
import type { ThemeColors, TerrainType, Tank } from '../../types'
import { createOffscreenCanvas } from '../../utils/canvas'
import { TerrainRenderSlice } from './GameRendererTerrain'
import { EntityRenderSlice } from './GameRendererEntities'
import { EffectsRenderSlice } from './GameRendererEffects'

/**
 * GameRenderer — renders the game world to a canvas.
 *
 * Advanced performance techniques:
 * 1. Terrain cache: static tiles (brick/steel/ice/base) pre-rendered to
 *    an offscreen canvas, rebuilt only when terrain or theme changes. Empty
 *    cells stay flat — no grid lines on plain ground (DECISIONS §29).
 * 2. Water separation: water tiles drawn directly each frame (cheap, few tiles)
 *    so the terrain cache stays static — no rebuilds for water animation.
 * 3. Forest cache: forest tiles pre-rendered to a separate offscreen canvas.
 * 4. Vignette cache: pre-rendered to an offscreen canvas per theme — replaces
 *    a full-screen gradient fillRect with a single drawImage.
 * 5. Canvas context: `alpha:false` + `desynchronized:true` for GPU-optimized blits.
 * 6. Camera transform via setTransform (no save/restore overhead).
 * 7. Particle batching: iterate by type to minimize state changes.
 */
export class GameRendererCore {
  ctx: CanvasRenderingContext2D
  canvas: HTMLCanvasElement
  artist: SpriteArtist
  camera: Camera
  animations: AnimationSystem
  particles: ParticleSystem
  private effects: EffectsSystem

  private dpr: number

  /**
   * Low-quality render mode (set by Performance Mode). When true, skips purely
   * decorative rendering that is expensive on software rasterizers:
   *   - Vignette full-screen blit (~1.4ms/frame on Skia software — the single
   *     most expensive operation in the render path; on a 20-year-old machine
   *     with no GPU this could be 7–14ms, nearly the entire frame budget).
   *   - Tank contact shadow (6 fillRect-equivalents per frame in a 6-tank scene;
   *     the shadow is a modern decoration, absent from the classic original).
   * Gameplay-relevant visuals (auras, insignia, hit overlays, shields) are
   * NEVER skipped — only decorative elements that don't affect readability.
   */
  lowQuality = false

  // ---- Dev draw-call counter (Performance Observatory, Alt+D overlay) ----
  /** Count of drawImage/fill/strokeRect calls in the most recent frame. */
  debugDrawCalls = 0
  private _countDraws = false
  private _origDrawImage: ((...a: any[]) => void) | null = null
  private _origFillRect: ((...a: any[]) => void) | null = null
  private _origFill: ((...a: any[]) => void) | null = null
  private _origStrokeRect: ((...a: any[]) => void) | null = null

  // ---- Terrain cache (static: brick/steel/ice/base, NO water, NO grid) ----
  terrainCache: CanvasImageSource
  terrainCacheCtx: CanvasRenderingContext2D
  terrainCacheDirty = true

  // ---- Forest cache ----
  forestCache: CanvasImageSource
  forestCacheCtx: CanvasRenderingContext2D
  /**
   * Whether the stage has any forest tiles. Compositing the full 1024×1024
   * forest surface costs a full-screen drawImage every frame even though it is
   * mostly transparent; on forest-free stages that draw is a pure no-op but
   * still pays the full-blit cost, so we skip it entirely. Recomputed whenever
   * the terrain/forest cache is rebuilt (see `recomputeHasForest`); the blit
   * itself is always a full-field drawImage — a sub-rect "bbox" blit was
   * prototyped and rejected because the 9-arg drawImage is ~2-3× slower than
   * the whole-image fast path in the Skia backend.
   */
  hasForest = false

  // ---- Water cell positions (for direct rendering each frame) ----
  waterCells: Array<{ c: number; r: number }> = []

  // ---- Vignette cache (offscreen canvas per theme) ----
  vignetteCanvas: CanvasImageSource
  vignetteCtx: CanvasRenderingContext2D
  vignetteDirty = true

  // ---- Gradient cache ----
  cachedBgGradient: CanvasGradient | null = null
  cachedTheme: ThemeColors | null = null
  /**
   * Bg gradient + theme cached for the TERRAIN CACHE context (R5-A). A separate
   * gradient is required because `CanvasGradient` objects are tied to the context
   * they were created on — the terrainCacheCtx is a different context from the
   * main one. Bakes the background fill into the static terrain layer so the
   * per-frame full-field `fillRect` is eliminated (camera-at-rest path: a single
   * opaque `drawImage` replaces `fillRect` + alpha-blended `drawImage`).
   */
  cachedCacheBgGradient: CanvasGradient | null = null
  cachedCacheTheme: ThemeColors | null = null

  // ---- Water sprite cache (theme-aware, phase-animated) ----
  private waterSpriteDirty = true
  private bulletSpriteDirty = true

  /** Base (eagle) damage fraction 0..1, derived from world each frame. */
  baseDamageFrac = 0

  // ---- Base transform components (for allocation-free debris rendering) ----
  _baseDpr = 1
  _baseCamX = 0
  _baseCamY = 0

  // ---- Reusable buffers for incremental terrain cache rebuild (P1) ----
  // Replaces a per-call `new Set<number>(tm.dirtyCells)` + 4-element neighbor
  // array of tuples — those were ~7 short-lived heap objects every time a
  // bullet impacted terrain (combat/burst). On a 20-year-old machine the
  // resulting minor GC churn is felt as frame hitches.
  //
  // `_dirtyMark` is a 676-byte flat tag grid (1 = cell needs repaint, 0 = skip).
  // `_dirtyList` is the sparse list of marked indices, reset to length=0 after
  // each rebuild. Both are zero-allocation in steady state.
  _dirtyMark = new Uint8Array(GRID * GRID)
  _dirtyList: number[] = []

  // ---- Subsystem slices (§1.1 composition; back-references only) ----
  private readonly terrainSlice: TerrainRenderSlice
  private readonly entitySlice: EntityRenderSlice
  private readonly effectsSlice: EffectsRenderSlice

  constructor(
    canvas: HTMLCanvasElement,
    camera: Camera,
    animations: AnimationSystem,
    particles: ParticleSystem,
    effects: EffectsSystem,
    dpr: number = 1,
    lib?: SpriteLibrary,
  ) {
    // Slices take a back-reference; bodies never run during construction.
    this.terrainSlice = new TerrainRenderSlice(this)
    this.entitySlice = new EntityRenderSlice(this)
    this.effectsSlice = new EffectsRenderSlice(this)

    this.canvas = canvas
    this.dpr = dpr
    canvas.width = FIELD * dpr
    canvas.height = FIELD * dpr
    // alpha:false — canvas is always opaque (background drawn first), skips alpha compositing
    // desynchronized:true — hints the browser to use a lower-latency GPU pipeline
    const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true })
    if (!ctx) throw new Error('Canvas 2D context not available')
    this.ctx = ctx
    this.ctx.imageSmoothingEnabled = true
    this.artist = new SpriteArtist(ctx, {} as never)
    if (lib) this.artist.setLibrary(lib)
    this.camera = camera
    this.animations = animations
    this.particles = particles
    this.effects = effects

    // Create offscreen terrain cache canvases at DPR resolution (uses OffscreenCanvas when available)
    const tc = createOffscreenCanvas(FIELD * dpr, FIELD * dpr, dpr)
    this.terrainCache = tc.canvas
    this.terrainCacheCtx = tc.ctx

    const fc = createOffscreenCanvas(FIELD * dpr, FIELD * dpr, dpr)
    this.forestCache = fc.canvas
    this.forestCacheCtx = fc.ctx

    // Vignette cache at 1× logical resolution: the vignette is a smooth radial
    // gradient (center fully transparent → edge vignetteColor), so upscaling it
    // to the field at blit time is visually lossless (measured: alpha-only
    // 1/255 deltas, 0% in the transparent center). Rendering it at DPR cuts the
    // per-frame full-field alpha blit area 4× — the single most expensive
    // operation on software rasterizers. (§R6)
    const vc = createOffscreenCanvas(FIELD, FIELD)
    this.vignetteCanvas = vc.canvas
    this.vignetteCtx = vc.ctx
  }

  getDpr(): number {
    return this.dpr
  }

  /**
   * Resize the renderer to a new device-pixel-ratio at runtime (Performance
   * Mode toggles DPR 1 ↔ 2). Reallocates the canvas backing store and the
   * offscreen terrain/forest/vignette caches at the new resolution, resets the
   * context state, and marks every cache dirty so they rebuild on the next
   * render. No gameplay state is touched.
   */
  setDpr(dpr: number): void {
    if (dpr === this.dpr) return
    this.dpr = dpr
    this.canvas.width = FIELD * dpr
    this.canvas.height = FIELD * dpr
    // Resizing the backing store resets the 2D context state.
    this.ctx.imageSmoothingEnabled = true

    const tc = createOffscreenCanvas(FIELD * dpr, FIELD * dpr, dpr)
    this.terrainCache = tc.canvas
    this.terrainCacheCtx = tc.ctx

    const fc = createOffscreenCanvas(FIELD * dpr, FIELD * dpr, dpr)
    this.forestCache = fc.canvas
    this.forestCacheCtx = fc.ctx

    const vc = createOffscreenCanvas(FIELD, FIELD)
    this.vignetteCanvas = vc.canvas
    this.vignetteCtx = vc.ctx

    // Force a full rebuild of all cached layers at the new resolution.
    this.terrainCacheDirty = true
    this.vignetteDirty = true
    this.cachedBgGradient = null
    this.cachedTheme = null
    this.cachedCacheBgGradient = null
    this.cachedCacheTheme = null
    this.waterSpriteDirty = true
    this.bulletSpriteDirty = true
  }

  setTheme(theme: ThemeColors, themeKey?: string): void {
    if (theme === this.artist.theme) return
    this.artist.setTheme(theme)
    // SVG sprites are tuned for the Modern Retro palette. Classic and Neon
    // themes have different colour priorities (e.g. NES-authentic grays vs.
    // the SVG's red enemy_basic), so their tanks/terrain must use the
    // theme-aware procedural fallback in SpriteArtist instead.
    this.artist.skipSvg = themeKey !== undefined && themeKey !== 'modern'
    this.terrainCacheDirty = true
    this.cachedBgGradient = null
    this.vignetteDirty = true
    this.waterSpriteDirty = true
    this.bulletSpriteDirty = true
  }

  setSpriteCache(cache: SpriteCache): void {
    this.artist.setSpriteCache(cache)
  }

  /**
   * Arm/disarm a dev-only draw-call counter for the Performance Observatory
   * (Alt+D) overlay. When on, `drawImage`/`fillRect`/`fill`/`strokeRect` on the
   * main canvas context are wrapped to increment {@link debugDrawCalls}; when
   * off the context methods are restored and the counter is reset to 0.
   *
   * Gated so it is zero-cost when the overlay is off. `Game` resets
   * `debugDrawCalls` to 0 at the start of each rendered frame, so the overlay
   * reads the count for exactly that frame. The counter only tallies draws on
   * the on-screen context — the offscreen terrain/forest/vignette caches draw
   * to separate contexts and are intentionally excluded.
   */
  setDrawCallCounting(on: boolean): void {
    if (on === this._countDraws) return
    const ctx = this.ctx as unknown as Record<string, any>
    if (on) {
      this._origDrawImage = (this.ctx.drawImage as any).bind(this.ctx)
      this._origFillRect = (this.ctx.fillRect as any).bind(this.ctx)
      this._origFill = (this.ctx.fill as any).bind(this.ctx)
      this._origStrokeRect = (this.ctx.strokeRect as any).bind(this.ctx)
      ctx.drawImage = (...a: any[]) => {
        this.debugDrawCalls++
        this._origDrawImage!(...a)
      }
      ctx.fillRect = (...a: any[]) => {
        this.debugDrawCalls++
        this._origFillRect!(...a)
      }
      ctx.fill = (...a: any[]) => {
        this.debugDrawCalls++
        this._origFill!(...a)
      }
      ctx.strokeRect = (...a: any[]) => {
        this.debugDrawCalls++
        this._origStrokeRect!(...a)
      }
    } else {
      if (this._origDrawImage) ctx.drawImage = this._origDrawImage
      if (this._origFillRect) ctx.fillRect = this._origFillRect
      if (this._origFill) ctx.fill = this._origFill
      if (this._origStrokeRect) ctx.strokeRect = this._origStrokeRect
      this.debugDrawCalls = 0
    }
    this._countDraws = on
  }

  // ================================================================
  // Main render
  // ================================================================

  /**
   * @param tanks Optional pre-computed `world.allTanks` buffer. `allTanks` is a
   * getter that rebuilds a shared array on every access; the caller
   * (PresentationLayer.render) already needs it for `updateVisualState`, so it
   * threads the same buffer here instead of paying for a second rebuild. When
   * omitted (tests, tools, direct callers) the getter is used — identical result.
   */
  render(world: World, tanks?: Tank[]): void {
    // Pass theme key so skipSvg is set correctly for Classic/Neon themes.
    this.setTheme(world.theme, world.themeKey)
    // Sync low-quality flag to artist (cheap boolean write; gates shadow skip).
    this.artist.lowQuality = this.lowQuality
    this.baseDamageFrac = world.baseMaxHp > 0 ? Math.max(0, 1 - world.baseHp / world.baseMaxHp) : 0
    const ctx = this.ctx
    const dpr = this.dpr

    // Camera offset
    const cam = this.camera.getOffset()

    // Combine DPR + camera into a single setTransform (no save/restore needed)
    ctx.setTransform(dpr, 0, 0, dpr, cam.x * dpr, cam.y * dpr)

    // Cache base transform so the debris pass can reset without save/restore
    this._baseDpr = dpr
    this._baseCamX = cam.x
    this._baseCamY = cam.y

    // 1+2. Static layer — bg baked into terrainCache (R5-A).
    //   Camera at rest: a single opaque `drawImage` replaces the old
    //   `fillRect`(bg) + alpha-blended `drawImage`(terrain) pair. The cache is
    //   opaque (bg + tiles), so the blit is a fast source-copy rather than a
    //   per-pixel alpha blend — meaningful on software rasterizers (old machines
    //   without GPU), where a full-field gradient `fillRect` + alpha blit can
    //   eat a large fraction of the frame budget.
    //   Camera shifted (shake/pan): fill the overscroll border with bg first so
    //   no stale pixels leak at the edges, then blit the cache over the interior.
    this.updateTerrainCache(world)
    if (cam.x !== 0 || cam.y !== 0) {
      this.fillBackground(world)
    }
    this.blitTerrain()

    // 3. Water tiles (drawn directly — cheap, few tiles, animated)
    if (this.waterSpriteDirty) {
      this.artist.spriteCache?.rebuildWater(world.theme)
      this.waterSpriteDirty = false
    }
    this.renderWater(world)

    // 4. Tanks
    this.renderTanks(world, tanks ?? world.allTanks)

    // 5. Bullets (rebuild the theme-colored bullet bitmap if the theme changed)
    if (this.bulletSpriteDirty) {
      this.artist.spriteCache?.rebuildBullet(world.theme)
      this.bulletSpriteDirty = false
    }
    this.renderBullets(world)

    // 6. Power-ups
    this.renderPowerUps(world)

    // 7. Forest (cached, drawn on top of tanks for hiding)
    this.blitForest()

    // 8. Explosions
    this.renderExplosions(world)

    // 9. Particles (batched by type)
    this.renderParticles()

    // 10. Score popups
    this.renderPopups(world)

    // Reset transform for screen-space effects
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    // 11. Screen flash (over everything)
    const flash = this.effects.getFlash()
    if (flash) {
      ctx.fillStyle = flash.color
      ctx.globalAlpha = flash.intensity
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.globalAlpha = 1
    }

    // 12. Vignette (cached offscreen canvas — one drawImage)
    // Skipped in low-quality mode: the full-screen alpha blit is the single
    // most expensive operation on software rasterizers (~1.4ms/frame on Skia,
    // estimated 7–14ms on a 20-year-old machine without GPU). The vignette is
    // purely decorative — its absence does not affect gameplay readability.
    if (!this.lowQuality) {
      this.drawVignette(world)
    }
  }

  // ================================================================
  // Subsystem stubs — overridden by the GameRenderer*Mixin classes
  // (terrain/water, entities, effects). Throwing stubs keep the render()
  // orchestrator type-safe before composition.
  // ================================================================

  protected blitTerrain(): void {
    this.terrainSlice.blitTerrain()
  }
  protected blitForest(): void {
    this.terrainSlice.blitForest()
  }
  protected recomputeHasForest(_tm: TileMap): void {
    this.terrainSlice.recomputeHasForest(_tm)
  }
  protected fillBackground(_world: World): void {
    this.terrainSlice.fillBackground(_world)
  }
  protected paintCacheBg(
    _ctx: CanvasRenderingContext2D,
    _x: number,
    _y: number,
    _w: number,
    _h: number,
    _theme: ThemeColors,
  ): void {
    this.terrainSlice.paintCacheBg(_ctx, _x, _y, _w, _h, _theme)
  }
  protected updateTerrainCache(_world: World): void {
    this.terrainSlice.updateTerrainCache(_world)
  }
  protected neighborMask(_tm: TileMap, _c: number, _r: number, _type: TerrainType): void {
    this.terrainSlice.neighborMask(_tm, _c, _r, _type)
  }
  protected redrawTerrainCell(_c: number, _r: number, _type: TerrainType, _tm: TileMap): void {
    this.terrainSlice.redrawTerrainCell(_c, _r, _type, _tm)
  }
  protected redrawForestCell(_c: number, _r: number): void {
    this.terrainSlice.redrawForestCell(_c, _r)
  }
  protected rebuildTerrainCache(_world: World): void {
    this.terrainSlice.rebuildTerrainCache(_world)
  }
  protected rebuildForestCache(_world: World): void {
    this.terrainSlice.rebuildForestCache(_world)
  }
  protected scanWaterCells(_world: World): void {
    this.terrainSlice.scanWaterCells(_world)
  }
  protected renderWater(_world: World): void {
    this.terrainSlice.renderWater(_world)
  }
  protected renderTanks(_world: World, _tanks: Tank[]): void {
    this.entitySlice.renderTanks(_world, _tanks)
  }
  protected renderBullets(_world: World): void {
    this.entitySlice.renderBullets(_world)
  }
  protected renderPowerUps(_world: World): void {
    this.entitySlice.renderPowerUps(_world)
  }
  protected renderExplosions(_world: World): void {
    this.effectsSlice.renderExplosions(_world)
  }
  protected renderParticles(): void {
    this.effectsSlice.renderParticles()
  }
  protected renderPopups(_world: World): void {
    this.effectsSlice.renderPopups(_world)
  }
  protected drawVignette(_world: World): void {
    this.effectsSlice.drawVignette(_world)
  }
}
