import type { World } from '../../game/World'
import { CELL, GRID, FIELD, TANK } from '../../constants'
import { SpriteArtist } from './SpriteArtist'
import type { SpriteLibrary } from './SpriteLibrary'
import type { Camera } from '../Camera'
import type { AnimationSystem } from '../AnimationSystem'
import type { ParticleSystem } from '../ParticleSystem'
import type { EffectsSystem } from '../EffectsSystem'
import type { ThemeColors } from '../../types'

/**
 * GameRenderer — renders the game world to a canvas.
 * Applies camera transform, draws all entities, particles, and effects.
 * Never modifies the World.
 *
 * Performance: terrain (grid lines + all non-forest tiles) and forest tiles
 * are cached to offscreen canvases, rebuilt only when the tile map changes,
 * the water animation phase flips, or the theme changes.
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

  // ---- Terrain cache ----
  /** Cached grid lines + non-forest terrain (brick, steel, water, ice, base, ruins). */
  private terrainCache: HTMLCanvasElement
  private terrainCacheCtx: CanvasRenderingContext2D
  /** Cached forest terrain (drawn on top of tanks). */
  private forestCache: HTMLCanvasElement
  private forestCacheCtx: CanvasRenderingContext2D
  /** Last water animation phase rendered into the cache. */
  private lastWaterPhase = -1
  /** Whether the terrain cache needs a full rebuild. */
  private terrainCacheDirty = true

  // ---- Gradient cache ----
  private cachedBgGradient: CanvasGradient | null = null
  private cachedVignette: CanvasGradient | null = null
  private cachedTheme: ThemeColors | null = null

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
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Canvas 2D context not available')
    this.ctx = ctx
    this.ctx.imageSmoothingEnabled = true
    this.artist = new SpriteArtist(ctx, {} as never)
    if (lib) this.artist.setLibrary(lib)
    this.camera = camera
    this.animations = animations
    this.particles = particles
    this.effects = effects

    // Create offscreen terrain cache canvases at DPR resolution
    this.terrainCache = document.createElement('canvas')
    this.terrainCache.width = FIELD * dpr
    this.terrainCache.height = FIELD * dpr
    this.terrainCacheCtx = this.terrainCache.getContext('2d')!
    this.terrainCacheCtx.scale(dpr, dpr)
    this.terrainCacheCtx.imageSmoothingEnabled = true

    this.forestCache = document.createElement('canvas')
    this.forestCache.width = FIELD * dpr
    this.forestCache.height = FIELD * dpr
    this.forestCacheCtx = this.forestCache.getContext('2d')!
    this.forestCacheCtx.scale(dpr, dpr)
    this.forestCacheCtx.imageSmoothingEnabled = true
  }

  getDpr(): number {
    return this.dpr
  }

  setTheme(theme: ThemeColors): void {
    // Only update when the theme object actually changes (rare — menu selection)
    if (theme === this.artist.theme) return
    this.artist.setTheme(theme)
    this.terrainCacheDirty = true
    this.cachedBgGradient = null
    this.cachedVignette = null
  }

  // ================================================================
  // Main render
  // ================================================================

  render(world: World): void {
    this.setTheme(world.theme)
    const ctx = this.ctx

    // Apply DPR scaling — reset transform and scale
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)

    // Camera offset
    const cam = this.camera.getOffset()

    ctx.save()
    ctx.translate(cam.x, cam.y)

    // 1. Background fill (with margin for camera shake) — cheap, one fillRect
    this.fillBackground(world)

    // 2. Grid lines + non-forest terrain (cached)
    this.updateTerrainCache(world)
    ctx.drawImage(this.terrainCache, 0, 0, FIELD, FIELD)

    // 3. Tanks
    this.renderTanks(world)

    // 4. Bullets
    this.renderBullets(world)

    // 5. Power-ups
    this.renderPowerUps(world)

    // 6. Forest (cached, drawn on top of tanks for hiding)
    ctx.drawImage(this.forestCache, 0, 0, FIELD, FIELD)

    // 7. Explosions
    this.renderExplosions(world)

    // 8. Particles
    this.renderParticles()

    // 9. Score popups
    this.renderPopups(world)

    ctx.restore()

    // 10. Screen flash (over everything)
    const flash = this.effects.getFlash()
    if (flash) {
      ctx.fillStyle = flash.color
      ctx.globalAlpha = flash.intensity
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.globalAlpha = 1
    }

    // 11. Vignette (cached gradient)
    this.drawVignette(world)
  }

  // ---- Background ----

  private fillBackground(world: World): void {
    const ctx = this.ctx
    const t = world.theme

    if (t.bgGradient) {
      // Cache the gradient — recreate only when theme changes
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
    const waterPhase = Math.floor(world.frame / 20) % 2
    const tm = world.tileMap

    // Rebuild only when something actually changed
    if (!this.terrainCacheDirty && !tm.dirty && waterPhase === this.lastWaterPhase) return

    this.terrainCacheDirty = false
    this.lastWaterPhase = waterPhase
    tm.dirty = false

    this.rebuildTerrainCache(world)
    this.rebuildForestCache(world)
  }

  private rebuildTerrainCache(world: World): void {
    const ctx = this.terrainCacheCtx
    const tm = world.tileMap
    const artist = this.artist
    // Swap artist context to the offscreen canvas
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

    // Non-forest terrain
    const frame = world.frame
    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        const type = tm.get(c, r)
        if (type === 'empty' || type === 'forest') continue

        const x = c * CELL
        const y = r * CELL

        switch (type) {
          case 'brick':
            artist.drawBrick(x, y, CELL)
            break
          case 'steel':
            artist.drawSteel(x, y, CELL)
            break
          case 'water':
            artist.drawWater(x, y, CELL, frame)
            break
          case 'ice':
            artist.drawIce(x, y, CELL)
            break
          case 'base':
            artist.drawBase(x, y, CELL, false)
            break
        }
      }
    }

    // Destroyed base ruins
    if (tm.isBaseDestroyed()) {
      for (let r = 24; r <= 25; r++) {
        for (let c = 12; c <= 13; c++) {
          if (r < GRID && c < GRID) {
            artist.drawBase(c * CELL, r * CELL, CELL, true)
          }
        }
      }
    }

    // Restore artist context
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

  // ---- Tanks ----

  private renderTanks(world: World): void {
    const ctx = this.ctx
    const frame = world.frame
    const artist = this.artist
    for (const tank of world.allTanks) {
      if (!tank.alive) continue

      // Spawn animation
      if (tank.spawnTimer > 0) {
        artist.drawSpawn(tank.x, tank.y, tank.w, frame)
        continue
      }

      // Determine animation frame
      const vc = this.animations.get(tank.id)
      const animFrame = vc ? this.animations.getFrame(vc) : (frame >> 2) & 1

      // Draw tank
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

      // Bonus enemy indicator
      if (tank.bonus) {
        const blink = Math.floor(frame / 10) % 2 === 0
        if (blink) {
          ctx.strokeStyle = '#ff4040'
          ctx.lineWidth = 1
          ctx.strokeRect(tank.x - 1, tank.y - 1, tank.w + 2, tank.h + 2)
        }
      }

      // Shield effect
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
      artist.drawPowerUp(pu.x, pu.y, pu.w, pu.type, frame)
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

  // ---- Particles ----

  private renderParticles(): void {
    const ctx = this.ctx
    // Iterate pool directly — avoids allocating a new array every frame
    const pool = this.particles.pool
    const count = this.particles.activeCount

    for (let i = 0; i < count; i++) {
      const p = pool[i]
      if (!p.active) continue

      const alpha = p.life / p.maxLife
      ctx.globalAlpha = alpha

      switch (p.type) {
        case 'spark':
          ctx.fillStyle = p.color
          ctx.fillRect(p.x - p.size / 2, p.y - p.size / 2, p.size, p.size)
          break

        case 'debris':
          ctx.save()
          ctx.translate(p.x, p.y)
          ctx.rotate(p.rotation)
          ctx.fillStyle = p.color
          ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size)
          ctx.restore()
          break

        case 'smoke':
          ctx.fillStyle = p.color
          ctx.globalAlpha = alpha * 0.4
          ctx.beginPath()
          ctx.arc(p.x, p.y, p.size * (1 + (1 - alpha) * 0.5), 0, Math.PI * 2)
          ctx.fill()
          break

        case 'ring':
          ctx.strokeStyle = p.color
          ctx.lineWidth = Math.max(1, p.size * alpha)
          ctx.beginPath()
          ctx.arc(p.x, p.y, p.size * (1 + (1 - alpha) * 2), 0, Math.PI * 2)
          ctx.stroke()
          break

        case 'flash':
          ctx.fillStyle = p.color
          ctx.globalAlpha = alpha * 0.8
          ctx.beginPath()
          ctx.arc(p.x, p.y, p.size * alpha, 0, Math.PI * 2)
          ctx.fill()
          break
      }
    }
    ctx.globalAlpha = 1
  }

  // ---- Score Popups ----

  private renderPopups(world: World): void {
    const ctx = this.ctx
    ctx.font = 'bold 11px "Courier New", monospace'
    ctx.textAlign = 'center'
    for (const popup of world.popups) {
      const alpha = Math.min(1, popup.timer / 500)
      const offsetY = (1 - popup.timer / 1500) * 20
      ctx.globalAlpha = alpha
      // Shadow
      ctx.fillStyle = 'rgba(0,0,0,0.6)'
      ctx.fillText(popup.text, popup.x + TANK / 2 + 1, popup.y - 2 - offsetY + 1)
      // Text
      ctx.fillStyle = world.theme.hudAccent
      ctx.fillText(popup.text, popup.x + TANK / 2, popup.y - 2 - offsetY)
    }
    ctx.globalAlpha = 1
    ctx.textAlign = 'left'
  }

  // ---- Vignette ----

  private drawVignette(world: World): void {
    const ctx = this.ctx
    // Cache the gradient — recreate only when theme changes
    if (!this.cachedVignette || this.cachedTheme !== world.theme) {
      const gradient = ctx.createRadialGradient(
        FIELD / 2,
        FIELD / 2,
        FIELD * 0.35,
        FIELD / 2,
        FIELD / 2,
        FIELD * 0.75,
      )
      gradient.addColorStop(0, 'rgba(0,0,0,0)')
      gradient.addColorStop(1, world.theme.vignetteColor)
      this.cachedVignette = gradient
      this.cachedTheme = world.theme
    }
    ctx.fillStyle = this.cachedVignette
    ctx.fillRect(0, 0, FIELD, FIELD)
  }
}
