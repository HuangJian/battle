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
import type { ThemeColors, TerrainType } from '../../types'
import { createOffscreenCanvas } from '../../utils/canvas'

/**
 * GameRenderer — renders the game world to a canvas.
 *
 * Advanced performance techniques:
 * 1. Terrain cache: static tiles (grid + brick/steel/ice/base) pre-rendered to
 *    an offscreen canvas, rebuilt only when terrain or theme changes.
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

  // ---- Terrain cache (static: grid + brick/steel/ice/base, NO water) ----
  private terrainCache: CanvasImageSource
  private terrainCacheCtx: CanvasRenderingContext2D
  private terrainCacheDirty = true

  // ---- Forest cache ----
  private forestCache: CanvasImageSource
  private forestCacheCtx: CanvasRenderingContext2D

  // ---- Water cell positions (for direct rendering each frame) ----
  private waterCells: Array<{ c: number; r: number }> = []

  // ---- Vignette cache (offscreen canvas per theme) ----
  private vignetteCanvas: CanvasImageSource
  private vignetteCtx: CanvasRenderingContext2D
  private vignetteDirty = true

  // ---- Gradient cache ----
  private cachedBgGradient: CanvasGradient | null = null
  private cachedTheme: ThemeColors | null = null

  // ---- Water sprite cache (theme-aware, phase-animated) ----
  private waterSpriteDirty = true

  // ---- Base transform components (for allocation-free debris rendering) ----
  private _baseDpr = 1
  private _baseCamX = 0
  private _baseCamY = 0

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

  setTheme(theme: ThemeColors): void {
    if (theme === this.artist.theme) return
    this.artist.setTheme(theme)
    this.terrainCacheDirty = true
    this.cachedBgGradient = null
    this.vignetteDirty = true
    this.waterSpriteDirty = true
  }

  setSpriteCache(cache: SpriteCache): void {
    this.artist.setSpriteCache(cache)
  }

  // ================================================================
  // Main render
  // ================================================================

  render(world: World): void {
    this.setTheme(world.theme)
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

    // 1. Background fill
    this.fillBackground(world)

    // 2. Static terrain cache (grid + brick/steel/ice/base — NO water)
    this.updateTerrainCache(world)
    ctx.drawImage(this.terrainCache, 0, 0, FIELD, FIELD)

    // 3. Water tiles (drawn directly — cheap, few tiles, animated)
    if (this.waterSpriteDirty) {
      this.artist.spriteCache?.rebuildWater(world.theme)
      this.waterSpriteDirty = false
    }
    this.renderWater(world)

    // 4. Tanks
    this.renderTanks(world)

    // 5. Bullets
    this.renderBullets(world)

    // 6. Power-ups
    this.renderPowerUps(world)

    // 7. Forest (cached, drawn on top of tanks for hiding)
    ctx.drawImage(this.forestCache, 0, 0, FIELD, FIELD)

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
    this.drawVignette(world)
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
      this.scanWaterCells(world)
      tm.dirtyCells.length = 0
    } else {
      // Incremental rebuild — only the cells that actually changed. This turns
      // "a brick got shot" from a full 26×26 cache rebuild into O(changed cells).
      const cells = tm.dirtyCells
      const artist = this.artist
      const savedCtx = artist.ctx // restore after — draw helpers use artist.ctx
      for (let i = 0; i < cells.length; i++) {
        const idx = cells[i]
        const c = idx % GRID
        const r = (idx - c) / GRID
        const type = tm.get(c, r)
        if (type === 'forest') {
          artist.ctx = this.forestCacheCtx
          this.redrawForestCell(c, r)
          // Clear any stale terrain tile under the (opaque) forest overlay.
          this.terrainCacheCtx.clearRect(c * CELL, r * CELL, CELL, CELL)
        } else {
          artist.ctx = this.terrainCacheCtx
          this.redrawTerrainCell(c, r, type, world.theme, tm)
          // Clear any stale forest overlay left at this cell.
          this.forestCacheCtx.clearRect(c * CELL, r * CELL, CELL, CELL)
        }
      }
      artist.ctx = savedCtx
      tm.dirtyCells.length = 0
    }
  }

  /**
   * Redraw a single terrain cell in place (used for incremental updates).
   * Reproduces exactly what the full rebuild would draw for that cell:
   * grid lines for empty space, or the tile art for a solid tile.
   */
  private redrawTerrainCell(
    c: number,
    r: number,
    type: TerrainType,
    theme: ThemeColors,
    tm: TileMap,
  ): void {
    const ctx = this.terrainCacheCtx
    const x = c * CELL
    const y = r * CELL
    ctx.clearRect(x, y, CELL, CELL)

    if (type === 'empty') {
      // Empty space: just the grid lines crossing this cell.
      ctx.strokeStyle = theme.gridLineColor
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(x + CELL, y)
      ctx.moveTo(x, y + CELL)
      ctx.lineTo(x + CELL, y + CELL)
      ctx.moveTo(x, y)
      ctx.lineTo(x, y + CELL)
      ctx.moveTo(x + CELL, y)
      ctx.lineTo(x + CELL, y + CELL)
      ctx.stroke()
      return
    }

    const artist = this.artist
    switch (type) {
      case 'brick':
        artist.drawBrick(x, y, CELL)
        break
      case 'steel':
        artist.drawSteel(x, y, CELL)
        break
      case 'ice':
        artist.drawIce(x, y, CELL)
        break
      case 'base':
        // The base is ONE crystal spanning 2×2; only the top-left cell draws it.
        if (tm.isBaseTopLeft(c, r)) {
          artist.drawBase(x, y, CELL * 2, false)
        }
        break
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

    ctx.clearRect(0, 0, FIELD, FIELD)

    // Grid lines
    ctx.strokeStyle = world.theme.gridLineColor
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 0; i <= GRID; i++) {
      ctx.moveTo(i * CELL, 0)
      ctx.lineTo(i * CELL, FIELD)
      ctx.moveTo(0, i * CELL)
      ctx.lineTo(FIELD, i * CELL)
    }
    ctx.stroke()

    // Static terrain only (NO water — water is rendered separately each frame)
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
          case 'steel':
            artist.drawSteel(x, y, CELL)
            break
          case 'ice':
            artist.drawIce(x, y, CELL)
            break
          case 'base':
            // Draw the whole 2×2 base as ONE crystal (only from its top-left cell).
            if (tm.isBaseTopLeft(c, r)) {
              artist.drawBase(c * CELL, r * CELL, CELL * 2, false)
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

  private renderTanks(world: World): void {
    const ctx = this.ctx
    const frame = world.frame
    const artist = this.artist
    for (const tank of world.allTanks) {
      if (!tank.alive) continue

      if (tank.spawnTimer > 0) {
        artist.drawSpawn(tank.x, tank.y, tank.w, frame)
        continue
      }

      const vc = this.animations.get(tank.id)
      const animFrame = vc ? this.animations.getFrame(vc) : (frame >> 2) & 1

      if (tank.isPlayer) {
        artist.drawPlayerTank(tank.x, tank.y, tank.w, tank.dir, tank.level ?? 0, animFrame)
      } else {
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
    }
  }

  // ---- Bullets ----

  private renderBullets(world: World): void {
    const artist = this.artist
    for (const bullet of world.bullets) {
      if (!bullet.alive) continue
      artist.drawBullet(bullet.x, bullet.y, bullet.w, bullet.dir)
    }
  }

  // ---- Power-ups ----

  private renderPowerUps(world: World): void {
    const frame = world.frame
    const artist = this.artist
    for (const pu of world.powerUps) {
      if (!pu.alive) continue
      artist.drawPowerUp(pu.x, pu.y, pu.w, pu.type, frame, pu.lifeTimer, POWERUP_TIMEOUT_MS)
    }
  }

  // ---- Explosions ----

  private renderExplosions(world: World): void {
    const artist = this.artist
    for (const exp of world.explosions) {
      const progress = 1 - exp.timer / exp.maxTimer
      artist.drawExplosion(exp.x, exp.y, exp.size, progress, exp.kind)
    }
  }

  // ---- Particles (batched by type to minimize state changes) ----

  private renderParticles(): void {
    const ctx = this.ctx
    const pool = this.particles.pool
    const count = this.particles.activeCount

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

    // Pass 2: debris particles (rotated). Use setTransform directly instead of
    // save()/restore() per particle — save() allocates a graphics-state object
    // on every call, which is GC pressure during explosions (lots of debris).
    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active || p.type !== 'debris') continue
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
    // Restore base transform for the remaining passes (smoke/ring/flash/popups)
    ctx.setTransform(
      this._baseDpr,
      0,
      0,
      this._baseDpr,
      this._baseCamX * this._baseDpr,
      this._baseCamY * this._baseDpr,
    )

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
    for (const popup of world.popups) {
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

    ctx.drawImage(this.vignetteCanvas, 0, 0, FIELD, FIELD)
  }
}
