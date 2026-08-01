import type { World } from '../../game/World'
import type { TileMap } from '../../game/TileMap'
import { CELL, GRID, FIELD, TANK, POWERUP_TIMEOUT_MS } from '../../constants'
import { SpriteArtist } from './SpriteArtist'
import type { SpriteLibrary } from './SpriteLibrary'
import type { SpriteCache } from './SpriteCache'
import type { Camera } from '../Camera'
import type { AnimationSystem } from '../AnimationSystem'
import type { ParticleSystem } from '../ParticleSystem'
import type { EffectsSystem } from '../EffectsSystem'
import type { ThemeColors, TerrainType, Tank } from '../../types'
import { createOffscreenCanvas } from '../../utils/canvas'
import { getHpLevel } from '../../config/hp-level'

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
export class GameRenderer {
  ctx: CanvasRenderingContext2D
  canvas: HTMLCanvasElement
  artist: SpriteArtist
  private camera: Camera
  private animations: AnimationSystem
  private particles: ParticleSystem
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
  private terrainCache: CanvasImageSource
  private terrainCacheCtx: CanvasRenderingContext2D
  private terrainCacheDirty = true

  // ---- Forest cache ----
  private forestCache: CanvasImageSource
  private forestCacheCtx: CanvasRenderingContext2D
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
  private hasForest = false

  // ---- Water cell positions (for direct rendering each frame) ----
  private waterCells: Array<{ c: number; r: number }> = []

  // ---- Vignette cache (offscreen canvas per theme) ----
  private vignetteCanvas: CanvasImageSource
  private vignetteCtx: CanvasRenderingContext2D
  private vignetteDirty = true

  // ---- Gradient cache ----
  private cachedBgGradient: CanvasGradient | null = null
  private cachedTheme: ThemeColors | null = null
  /**
   * Bg gradient + theme cached for the TERRAIN CACHE context (R5-A). A separate
   * gradient is required because `CanvasGradient` objects are tied to the context
   * they were created on — the terrainCacheCtx is a different context from the
   * main one. Bakes the background fill into the static terrain layer so the
   * per-frame full-field `fillRect` is eliminated (camera-at-rest path: a single
   * opaque `drawImage` replaces `fillRect` + alpha-blended `drawImage`).
   */
  private cachedCacheBgGradient: CanvasGradient | null = null
  private cachedCacheTheme: ThemeColors | null = null

  // ---- Water sprite cache (theme-aware, phase-animated) ----
  private waterSpriteDirty = true
  private bulletSpriteDirty = true

  /** Base (eagle) damage fraction 0..1, derived from world each frame. */
  private baseDamageFrac = 0

  // ---- Base transform components (for allocation-free debris rendering) ----
  private _baseDpr = 1
  private _baseCamX = 0
  private _baseCamY = 0

  // ---- Reusable buffers for incremental terrain cache rebuild (P1) ----
  // Replaces a per-call `new Set<number>(tm.dirtyCells)` + 4-element neighbor
  // array of tuples — those were ~7 short-lived heap objects every time a
  // bullet impacted terrain (combat/burst). On a 20-year-old machine the
  // resulting minor GC churn is felt as frame hitches.
  //
  // `_dirtyMark` is a 676-byte flat tag grid (1 = cell needs repaint, 0 = skip).
  // `_dirtyList` is the sparse list of marked indices, reset to length=0 after
  // each rebuild. Both are zero-allocation in steady state.
  private _dirtyMark = new Uint8Array(GRID * GRID)
  private _dirtyList: number[] = []

  constructor(
    canvas: HTMLCanvasElement,
    camera: Camera,
    animations: AnimationSystem,
    particles: ParticleSystem,
    effects: EffectsSystem,
    dpr: number = 1,
    lib?: SpriteLibrary,
  ) {
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

    const vc = createOffscreenCanvas(FIELD * dpr, FIELD * dpr, dpr)
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

    const vc = createOffscreenCanvas(FIELD * dpr, FIELD * dpr, dpr)
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

  /**
   * Composite the static terrain layer. Its own method (rather than an inline
   * `drawImage`) so the ablation benchmark can no-op exactly this stage, and so
   * the blit rectangle has one place to change.
   */
  private blitTerrain(): void {
    this.ctx.drawImage(this.terrainCache, 0, 0, FIELD, FIELD)
  }

  /**
   * Composite the forest layer over the tanks. Skipped entirely when the stage
   * has no forest (`hasForest`); otherwise a single full-field drawImage. Source
   * coordinates are in cache bitmap pixels (the cache is FIELD*dpr wide);
   * destination coordinates are logical, because the main context already
   * carries the DPR transform.
   */
  private blitForest(): void {
    if (!this.hasForest) return // stage has no forest — nothing to composite
    this.ctx.drawImage(this.forestCache, 0, 0, FIELD, FIELD)
  }

  /**
   * Recompute `hasForest` from the tile map. Cheap (676 cells) and only runs
   * when the terrain/forest cache changes, so it is not a per-frame cost.
   */
  private recomputeHasForest(tm: TileMap): void {
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (tm.get(c, r) === 'forest') {
          this.hasForest = true
          return
        }
      }
    }
    this.hasForest = false
  }

  // ---- Background ----

  private fillBackground(world: World): void {
    const ctx = this.ctx
    const t = world.theme

    if (t.bgGradient) {
      if (!this.cachedBgGradient || this.cachedTheme !== t) {
        const g = ctx.createLinearGradient(0, -10, 0, FIELD + 10)
        g.addColorStop(0, t.bgGradient[0])
        g.addColorStop(1, t.bgGradient[1])
        this.cachedBgGradient = g
        this.cachedTheme = t
      }
      ctx.fillStyle = this.cachedBgGradient
    } else {
      ctx.fillStyle = t.bg
    }
    ctx.fillRect(-10, -10, FIELD + 20, FIELD + 20)
  }

  /**
   * Paint the background into a rect of the TERRAIN CACHE context (R5-A). The
   * bg is baked into the static cache so the per-frame full-field `fillRect` is
   * skipped when the camera is at rest. Uses the same gradient definition as
   * `fillBackground` (absolute user-space coords `0,-10 → 0,FIELD+10`) so a
   * sub-rect `fillRect(x,y,w,h)` paints the identical slice the main-canvas
   * fill would have produced there. Gradient is cached per-theme on this
   * context (separate from the main ctx's `cachedBgGradient` because
   * `CanvasGradient` is context-bound).
   */
  private paintCacheBg(
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    w: number,
    h: number,
    theme: ThemeColors,
  ): void {
    if (theme.bgGradient) {
      if (!this.cachedCacheBgGradient || this.cachedCacheTheme !== theme) {
        const g = ctx.createLinearGradient(0, -10, 0, FIELD + 10)
        g.addColorStop(0, theme.bgGradient[0])
        g.addColorStop(1, theme.bgGradient[1])
        this.cachedCacheBgGradient = g
        this.cachedCacheTheme = theme
      }
      ctx.fillStyle = this.cachedCacheBgGradient
    } else {
      ctx.fillStyle = theme.bg
    }
    ctx.fillRect(x, y, w, h)
  }

  // ---- Terrain cache ----

  private updateTerrainCache(world: World): void {
    const tm = world.tileMap
    if (!this.terrainCacheDirty && !tm.dirty && tm.dirtyCells.length === 0) return

    if (this.terrainCacheDirty || tm.dirty) {
      // Full rebuild — stage load, theme change, or base destruction (ruins).
      this.terrainCacheDirty = false
      tm.dirty = false
      this.rebuildTerrainCache(world)
      this.rebuildForestCache(world)
      this.recomputeHasForest(tm)
      this.scanWaterCells(world)
      tm.dirtyCells.length = 0
    } else {
      // Incremental rebuild — only the cells that actually changed (plus their
      // orthogonal neighbours, so auto-tiled steel/ice re-derive their patch
      // perimeter when a neighbour is destroyed). Turns "a brick got shot" from
      // a full 26×26 cache rebuild into O(changed cells).
      //
      // Zero-allocation path (P1): the previous implementation built a `Set`
      // and a 4-element tuple array per call — short-lived heap objects that
      // triggered minor GC on every terrain-damage frame. We now mark cells in
      // a reusable Uint8Array and collect the unique indices in a reusable
      // number[], then walk that list and clear marks in the same pass.
      const mark = this._dirtyMark
      const list = this._dirtyList
      // Phase 1 — mark dirty cells + orthogonal neighbours (dedup via mark).
      for (let i = 0; i < tm.dirtyCells.length; i++) {
        const idx = tm.dirtyCells[i]
        if (mark[idx] === 0) {
          mark[idx] = 1
          list.push(idx)
        }
        const c = idx % GRID
        const r = (idx - c) / GRID
        // Inline the 4-neighbour scan — avoids allocating a tuple array.
        if (c > 0) {
          const n = idx - 1
          if (mark[n] === 0) {
            mark[n] = 1
            list.push(n)
          }
        }
        if (c < GRID - 1) {
          const n = idx + 1
          if (mark[n] === 0) {
            mark[n] = 1
            list.push(n)
          }
        }
        if (r > 0) {
          const n = idx - GRID
          if (mark[n] === 0) {
            mark[n] = 1
            list.push(n)
          }
        }
        if (r < GRID - 1) {
          const n = idx + GRID
          if (mark[n] === 0) {
            mark[n] = 1
            list.push(n)
          }
        }
      }
      // Phase 2 — repaint each marked cell, then clear its mark in the same
      // iteration so the buffers are clean for the next call.
      const artist = this.artist
      const savedCtx = artist.ctx // restore after — draw helpers use artist.ctx
      for (let i = 0; i < list.length; i++) {
        const idx = list[i]
        mark[idx] = 0 // reset for next call
        const c = idx % GRID
        const r = (idx - c) / GRID
        const type = tm.get(c, r)
        if (type === 'water') continue // water isn't in the terrain cache
        if (type === 'forest') {
          artist.ctx = this.forestCacheCtx
          this.redrawForestCell(c, r)
          // Repaint bg on the terrain cache under the forest overlay (R5-A: the
          // cache is opaque — a `clearRect` would punch a transparent hole and
          // show stale canvas content beneath the blit). Forest hides terrain,
          // so only the bg should appear under the (separately drawn) forest
          // overlay.
          this.paintCacheBg(this.terrainCacheCtx, c * CELL, r * CELL, CELL, CELL, this.artist.theme)
        } else {
          artist.ctx = this.terrainCacheCtx
          this.redrawTerrainCell(c, r, type, tm)
          // Clear any stale forest overlay left at this cell.
          this.forestCacheCtx.clearRect(c * CELL, r * CELL, CELL, CELL)
        }
      }
      list.length = 0
      artist.ctx = savedCtx
      // A destroyed forest cell can shrink the box and a newly drawn one can
      // grow it, so recompute rather than only expanding — otherwise the box
      // would ratchet outward and lose the saving over a long stage.
      this.recomputeHasForest(tm)
      tm.dirtyCells.length = 0
    }
  }

  /**
   * Reusable 4-slot neighbour mask buffer (P4). Avoids allocating a fresh
   * `[boolean, boolean, boolean, boolean]` tuple + the `at` closure on every
   * call to {@link neighborMask}. Reads are `arr[0..3]` = (n, e, s, w).
   * Callers must consume the values before the next call to `neighborMask`.
   */
  private _nmask: boolean[] = [false, false, false, false]

  /**
   * Fill {@link _nmask} with the 4-neighbour same-type flags for cell (c, r).
   *
   * P4: previously returned a fresh 4-tuple AND allocated a closure (`at`) per
   * call. With 4 call sites inside the per-dirty-cell `redrawTerrainCell` /
   * `rebuildTerrainCache` paths, a brick-destroy burst could allocate ~12
   * short-lived objects (4 calls × (1 tuple + 1 closure + destructuring
   * intermediate)). Inlined bounds checks + reusable buffer = zero allocation.
   */
  private neighborMask(tm: TileMap, c: number, r: number, type: TerrainType): void {
    // North
    const hasN = r > 0 && tm.get(c, r - 1) === type
    // East
    const hasE = c < GRID - 1 && tm.get(c + 1, r) === type
    // South
    const hasS = r < GRID - 1 && tm.get(c, r + 1) === type
    // West
    const hasW = c > 0 && tm.get(c - 1, r) === type
    const m = this._nmask
    m[0] = hasN
    m[1] = hasE
    m[2] = hasS
    m[3] = hasW
  }

  /**
   * Redraw a single terrain cell in place (used for incremental updates).
   * Reproduces exactly what the full rebuild would draw for that cell:
   * flat clear for empty space, or the tile art for a solid tile.
   */
  private redrawTerrainCell(c: number, r: number, type: TerrainType, tm: TileMap): void {
    const ctx = this.terrainCacheCtx
    const x = c * CELL
    const y = r * CELL
    // Repaint bg for this cell (R5-A: bg is baked into the opaque cache, so a
    // destroyed tile reveals the bg rather than transparency). Equivalent to
    // the old `clearRect` for the visual result, because the cache is composited
    // as an opaque blit — there is no "behind the cache" to show through.
    this.paintCacheBg(ctx, x, y, CELL, CELL, this.artist.theme)

    if (type === 'empty') {
      // Empty space: clean flat ground (bg painted above).
      return
    }

    const artist = this.artist
    switch (type) {
      case 'brick':
        artist.drawBrick(x, y, CELL)
        break
      case 'steel': {
        this.neighborMask(tm, c, r, 'steel')
        const m = this._nmask
        artist.drawSteel(x, y, CELL, m[0], m[1], m[2], m[3])
        break
      }
      case 'ice': {
        this.neighborMask(tm, c, r, 'ice')
        const m = this._nmask
        artist.drawIce(x, y, CELL, m[0], m[1], m[2], m[3])
        break
      }
      case 'base': {
        // The base is ONE crystal spanning 2×2, drawn from the block's
        // TOP-LEFT cell. This cell may be a NON-top-left base cell reached via
        // neighbour expansion (e.g. an adjacent brick was destroyed). If we only
        // repainted this single 16×16 cell and drew nothing (because
        // isBaseTopLeft is false), the chunk of the crystal overlapping this
        // cell would be erased forever — the reported "base loses a piece" bug.
        // So always walk back to the block's top-left and repaint the full
        // crystal.
        let tlC = c
        let tlR = r
        while (tlC > 0 && tm.get(tlC - 1, tlR) === 'base') tlC--
        while (tlR > 0 && tm.get(tlC, tlR - 1) === 'base') tlR--
        artist.drawBase(tlC * CELL, tlR * CELL, CELL * 2, false, this.baseDamageFrac)
        break
      }
    }
  }

  /** Redraw a single forest cell in the forest cache (clear or draw). */
  private redrawForestCell(c: number, r: number): void {
    const ctx = this.forestCacheCtx
    const x = c * CELL
    const y = r * CELL
    ctx.clearRect(x, y, CELL, CELL)
    this.artist.drawForest(x, y, CELL)
  }

  private rebuildTerrainCache(world: World): void {
    const ctx = this.terrainCacheCtx
    const tm = world.tileMap
    const artist = this.artist
    const savedCtx = artist.ctx
    artist.ctx = ctx

    // Bake the background into the static cache (R5-A). The cache becomes
    // opaque (bg + tiles), so the per-frame blit is a fast source-copy and the
    // separate full-field `fillRect` is eliminated on the camera-at-rest path.
    // Replaces the old `clearRect(0,0,FIELD,FIELD)` — empty cells now carry the
    // bg colour/gradient instead of transparency, which is what makes the
    // single-blit replacement visually equivalent.
    this.paintCacheBg(ctx, 0, 0, FIELD, FIELD, world.theme)

    // Static terrain only (NO water — water is rendered separately each frame).
    // No grid lines on empty ground — flat cell feel, see DECISIONS.md §29.
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        const type = tm.get(c, r)
        if (type === 'empty' || type === 'forest' || type === 'water') continue

        const x = c * CELL
        const y = r * CELL

        switch (type) {
          case 'brick':
            artist.drawBrick(x, y, CELL)
            break
          case 'steel': {
            this.neighborMask(tm, c, r, 'steel')
            const m = this._nmask
            artist.drawSteel(x, y, CELL, m[0], m[1], m[2], m[3])
            break
          }
          case 'ice': {
            this.neighborMask(tm, c, r, 'ice')
            const m = this._nmask
            artist.drawIce(x, y, CELL, m[0], m[1], m[2], m[3])
            break
          }
          case 'base':
            // Draw the whole 2×2 base as ONE crystal (only from its top-left cell).
            if (tm.isBaseTopLeft(c, r)) {
              artist.drawBase(c * CELL, r * CELL, CELL * 2, false, this.baseDamageFrac)
            }
            break
        }
      }
    }

    // Destroyed base ruins — one shattered crystal across the 2×2 block
    if (tm.isBaseDestroyed()) {
      const bp = tm.getBasePos()
      if (bp) artist.drawBase(bp.x, bp.y, CELL * 2, true)
    }

    artist.ctx = savedCtx
  }

  private rebuildForestCache(world: World): void {
    const ctx = this.forestCacheCtx
    const tm = world.tileMap
    const artist = this.artist
    const savedCtx = artist.ctx
    artist.ctx = ctx

    ctx.clearRect(0, 0, FIELD, FIELD)

    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        const type = tm.get(c, r)
        if (type !== 'forest') continue
        artist.drawForest(c * CELL, r * CELL, CELL)
      }
    }

    artist.ctx = savedCtx
  }

  /** Scan and cache water cell positions for efficient per-frame rendering. */
  private scanWaterCells(world: World): void {
    this.waterCells = []
    const tm = world.tileMap
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        if (tm.get(c, r) === 'water') {
          this.waterCells.push({ c, r })
        }
      }
    }
  }

  // ---- Water (direct render each frame — animated, few tiles) ----

  private renderWater(world: World): void {
    const artist = this.artist
    const frame = world.frame
    for (let i = 0; i < this.waterCells.length; i++) {
      const { c, r } = this.waterCells[i]
      artist.drawWater(c * CELL, r * CELL, CELL, frame)
    }
  }

  // ---- Tanks ----

  private renderTanks(world: World, tanks: Tank[]): void {
    const ctx = this.ctx
    const frame = world.frame
    const artist = this.artist
    for (let ti = 0; ti < tanks.length; ti++) {
      const tank = tanks[ti]
      if (!tank.alive) continue

      if (tank.spawnTimer > 0) {
        artist.drawSpawn(tank.x, tank.y, tank.w, frame)
        continue
      }

      const vc = this.animations.get(tank.id)
      const animFrame = vc ? this.animations.getFrame(vc) : (frame >> 2) & 1

      // Draw HP level visual decoration aura (Level 2~6)
      const hpLevel = getHpLevel(tank.hp)
      if (hpLevel > 1) {
        artist.drawHpLevelAura(tank.x, tank.y, tank.w, hpLevel, frame)
      }

      if (tank.allegiance === 'ally') {
        // 天降神兵 allied guard — distinct purple unit (no enemy crown/insignia).
        artist.drawAllyTank(tank.x, tank.y, tank.w, tank.dir, animFrame)
      } else if (tank.isPlayer) {
        // Lie-Back-Win-Mode: use player2 sprite for God AI tank.
        if (tank === world.player2) {
          artist.drawPlayer2Tank(tank.x, tank.y, tank.w, tank.dir, tank.level ?? 0, animFrame)
        } else {
          artist.drawPlayerTank(tank.x, tank.y, tank.w, tank.dir, tank.level ?? 0, animFrame)
        }
      } else {
        const isCommander = tank.aiState?.isCommander === true
        artist.drawEnemyTank(
          tank.x,
          tank.y,
          tank.w,
          tank.dir,
          tank.kind,
          animFrame,
          (tank.flashTimer ?? 0) > 0,
          tank.hp,
          Math.min(tank.hitCount ?? 0, 4),
          isCommander,
        )
      }

      if (tank.bonus) {
        const blink = Math.floor(frame / 10) % 2 === 0
        if (blink) {
          ctx.strokeStyle = '#ff4040'
          ctx.lineWidth = 1
          ctx.strokeRect(tank.x - 1, tank.y - 1, tank.w + 2, tank.h + 2)
        }
      }

      if (tank.shieldTimer && tank.shieldTimer > 0) {
        artist.drawShield(tank.x, tank.y, tank.w, frame)
      }

      // Allies get a distinct purple friendly aura (not the enemy rank
      // insignia / commander crown); everyone else draws the insignia LAST so
      // it sits above the HP level border, bonus frame, and shield.
      if (tank.allegiance === 'ally') {
        artist.drawAllyAura(tank.x, tank.y, tank.w, frame)
      } else {
        artist.drawInsignia(
          tank.x,
          tank.y,
          tank.w,
          tank.aiState?.level ?? 'none',
          tank.aiState?.isCommander === true,
        )
      }
    }
  }

  // ---- Bullets ----

  private renderBullets(world: World): void {
    const artist = this.artist
    const bullets = world.bullets
    // P6: index loop instead of `for...of` — dense arrays optimize identically
    // in V8, but `for...of` may allocate an iterator object on holey arrays
    // (post-compaction). The cost is zero on dev hardware and a real win on
    // older JS engines.
    for (let i = 0; i < bullets.length; i++) {
      const bullet = bullets[i]
      if (!bullet.alive) continue
      artist.drawBullet(bullet.x, bullet.y, bullet.w, bullet.dir)
    }
  }

  // ---- Power-ups ----

  private renderPowerUps(world: World): void {
    const frame = world.frame
    const artist = this.artist
    const pus = world.powerUps
    for (let i = 0; i < pus.length; i++) {
      const pu = pus[i]
      if (!pu.alive) continue
      artist.drawPowerUp(pu.x, pu.y, pu.w, pu.type, frame, pu.lifeTimer, POWERUP_TIMEOUT_MS)
    }
  }

  // ---- Explosions ----

  private renderExplosions(world: World): void {
    const artist = this.artist
    const exps = world.explosions
    for (let i = 0; i < exps.length; i++) {
      const exp = exps[i]
      const progress = 1 - exp.timer / exp.maxTimer
      artist.drawExplosion(exp.x, exp.y, exp.size, progress, exp.kind)
    }
  }

  // ---- Particles (batched by type to minimize state changes) ----

  private renderParticles(): void {
    const ctx = this.ctx
    const pool = this.particles.pool
    const count = this.particles.activeCount
    // Common case: no live particles. Skip five loop set-ups and — more
    // importantly — the unconditional `setTransform` below, which is a real
    // Skia/napi call. Still normalize globalAlpha exactly as the full path does,
    // so a leftover alpha from an earlier stage cannot bleed into popups.
    if (count === 0) {
      ctx.globalAlpha = 1
      return
    }

    // Pass 1: spark particles (fillRect — batch fillStyle changes)
    let lastFill = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'spark') continue
      ctx.globalAlpha = p.life / p.maxLife
      if (p.color !== lastFill) {
        ctx.fillStyle = p.color
        lastFill = p.color
      }
      ctx.fillRect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size)
    }

    // Low-quality mode: skip the four decorative particle passes below (debris,
    // smoke, ring, flash). These use expensive per-particle path rasterization
    // (`beginPath`+`arc`+`fill`/`stroke`) or per-particle `setTransform`+`rotate`
    // — the dominant render cost during explosions on software rasterizers (old
    // machines without GPU). The explosion sprite itself (`renderExplosions`)
    // is still drawn, so the event remains clearly visible; sparks (pass 1) are
    // retained as hit-direction feedback. This is the single largest lowQuality
    // saving during the burst-heavy frames that would otherwise drop FPS.
    if (this.lowQuality) {
      ctx.globalAlpha = 1
      return
    }

    // Pass 2: debris particles (rotated). Use setTransform directly instead of
    // save()/restore() per particle — save() allocates a graphics-state object
    // on every call, which is GC pressure during explosions (lots of debris).
    let drewDebris = false
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'debris') continue
      drewDebris = true
      ctx.globalAlpha = p.life / p.maxLife
      ctx.fillStyle = p.color
      // Equivalent to translate(p) then rotate, without pushing a saved state:
      // screen = dpr * (local + p + cameraOffset).
      ctx.setTransform(
        this._baseDpr,
        0,
        0,
        this._baseDpr,
        (p.x + this._baseCamX) * this._baseDpr,
        (p.y + this._baseCamY) * this._baseDpr,
      )
      ctx.rotate(p.rotation)
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size)
    }
    // Restore base transform for the remaining passes (smoke/ring/flash/popups).
    // Only needed if pass 2 actually moved the transform — a `setTransform` is a
    // real napi/Skia call (~300ns), and on the common frame there is no debris.
    if (drewDebris) {
      ctx.setTransform(
        this._baseDpr,
        0,
        0,
        this._baseDpr,
        this._baseCamX * this._baseDpr,
        this._baseCamY * this._baseDpr,
      )
    }

    // Pass 3: smoke particles (arc fill — batch by minimizing fillStyle changes)
    lastFill = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'smoke') continue
      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha * 0.4
      if (p.color !== lastFill) {
        ctx.fillStyle = p.color
        lastFill = p.color
      }
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * (1 + (1 - alpha) * 0.5), 0, Math.PI * 2)
      ctx.fill()
    }

    // Pass 4: ring particles (arc stroke)
    let lastStroke = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'ring') continue
      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha
      if (p.color !== lastStroke) {
        ctx.strokeStyle = p.color
        lastStroke = p.color
      }
      ctx.lineWidth = Math.max(1, p.size * alpha)
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * (1 + (1 - alpha) * 2), 0, Math.PI * 2)
      ctx.stroke()
    }

    // Pass 5: flash particles (arc fill)
    lastFill = ''
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'flash') continue
      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha * 0.8
      if (p.color !== lastFill) {
        ctx.fillStyle = p.color
        lastFill = p.color
      }
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size * alpha, 0, Math.PI * 2)
      ctx.fill()
    }

    ctx.globalAlpha = 1
  }

  // ---- Score Popups ----

  private renderPopups(world: World): void {
    // Popups are transient (briefly after a kill). Skip entirely on the common
    // frame where there are none — avoids forcing a `ctx.font` parse and two
    // `textAlign` state writes 60×/sec for no work.
    if (world.popups.length === 0) return
    const ctx = this.ctx
    ctx.font = 'bold 11px "Courier New", monospace'
    ctx.textAlign = 'center'
    const popups = world.popups
    for (let i = 0; i < popups.length; i++) {
      const popup = popups[i]
      const alpha = Math.min(1, popup.timer / 500)
      const offsetY = (1 - popup.timer / 1500) * 20
      ctx.globalAlpha = alpha
      ctx.fillStyle = 'rgba(0,0,0,0.6)'
      ctx.fillText(popup.text, popup.x + TANK / 2 + 1, popup.y - 2 - offsetY + 1)
      ctx.fillStyle = world.theme.hudAccent
      ctx.fillText(popup.text, popup.x + TANK / 2, popup.y - 2 - offsetY)
    }
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
  }

  // ---- Vignette (cached offscreen canvas) ----

  private drawVignette(world: World): void {
    const ctx = this.ctx

    // Rebuild vignette cache when theme changes
    if (this.vignetteDirty || this.cachedTheme !== world.theme) {
      const vctx = this.vignetteCtx
      vctx.clearRect(0, 0, FIELD, FIELD)
      const gradient = vctx.createRadialGradient(
        FIELD / 2,
        FIELD / 2,
        FIELD * 0.35,
        FIELD / 2,
        FIELD / 2,
        FIELD * 0.75,
      )
      gradient.addColorStop(0, 'rgba(0,0,0,0)')
      gradient.addColorStop(1, world.theme.vignetteColor)
      vctx.fillStyle = gradient
      vctx.fillRect(0, 0, FIELD, FIELD)
      this.cachedTheme = world.theme
      this.vignetteDirty = false
    }

    // Single full-field composite. NOTE: a sub-rect "ring" blit (skipping the
    // fully-transparent center) was prototyped and *rejected* — in the Skia
    // backend the 9-arg drawImage pays an extractSubset overhead that makes it
    // ~2-3× slower than this whole-image fast path, i.e. a regression. The
    // transparent center is a no-op source-over here, so the full blit is both
    // correct and the fastest path. See DECISIONS.md §10 (render perf).
    ctx.drawImage(this.vignetteCanvas, 0, 0, FIELD, FIELD)
  }
}
