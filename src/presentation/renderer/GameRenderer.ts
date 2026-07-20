import type { World } from '../../game/World'
import { CELL, GRID, FIELD, TANK } from '../../constants'
import { SpriteArtist } from './SpriteArtist'
import type { Camera } from '../Camera'
import type { AnimationSystem } from '../AnimationSystem'
import type { ParticleSystem } from '../ParticleSystem'
import type { EffectsSystem } from '../EffectsSystem'

/**
 * GameRenderer — renders the game world to an offscreen canvas.
 * Applies camera transform, draws all entities, particles, and effects.
 * Never modifies the World.
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

  constructor(
    canvas: HTMLCanvasElement,
    camera: Camera,
    animations: AnimationSystem,
    particles: ParticleSystem,
    effects: EffectsSystem,
    dpr: number = 1,
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
    this.camera = camera
    this.animations = animations
    this.particles = particles
    this.effects = effects
  }

  getDpr(): number {
    return this.dpr
  }

  setTheme(theme: typeof this.artist.theme): void {
    this.artist.setTheme(theme)
  }

  render(world: World): void {
    this.setTheme(world.theme)
    const ctx = this.ctx

    // Apply DPR scaling — reset transform and scale
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0)

    // Camera offset
    const cam = this.camera.getOffset()

    ctx.save()
    ctx.translate(cam.x, cam.y)

    // Clear with background
    ctx.fillStyle = world.theme.bg
    ctx.fillRect(-10, -10, FIELD + 20, FIELD + 20)

    // Grid lines (subtle)
    this.drawGrid(world)

    // 1. Terrain (except forest)
    this.renderTerrain(world, false)

    // 2. Tanks
    this.renderTanks(world)

    // 3. Bullets
    this.renderBullets(world)

    // 4. Power-ups
    this.renderPowerUps(world)

    // 5. Forest (on top of tanks for hiding)
    this.renderTerrain(world, true)

    // 6. Explosions
    this.renderExplosions(world)

    // 7. Particles
    this.renderParticles()

    // 8. Score popups
    this.renderPopups(world)

    ctx.restore()

    // 9. Screen flash (over everything)
    const flash = this.effects.getFlash()
    if (flash) {
      ctx.fillStyle = flash.color
      ctx.globalAlpha = flash.intensity
      ctx.fillRect(0, 0, FIELD, FIELD)
      ctx.globalAlpha = 1
    }

    // 10. Vignette
    this.drawVignette(world)
  }

  // ---- Grid ----

  private drawGrid(world: World): void {
    const ctx = this.ctx
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
  }

  // ---- Terrain ----

  private renderTerrain(world: World, forestOnly: boolean): void {
    const tm = world.tileMap
    const frame = world.frame

    for (let r = 0; r < GRID; r++) {
      for (let c = 0; c < GRID; c++) {
        const type = tm.get(c, r)
        if (type === 'empty') continue
        if (type === 'forest' && !forestOnly) continue
        if (type !== 'forest' && forestOnly) continue

        const x = c * CELL
        const y = r * CELL

        switch (type) {
          case 'brick':
            this.artist.drawBrick(x, y, CELL)
            break
          case 'steel':
            this.artist.drawSteel(x, y, CELL)
            break
          case 'water':
            this.artist.drawWater(x, y, CELL, frame)
            break
          case 'forest':
            this.artist.drawForest(x, y, CELL)
            break
          case 'ice':
            this.artist.drawIce(x, y, CELL)
            break
          case 'base':
            this.artist.drawBase(x, y, CELL, false)
            break
        }
      }
    }

    // Draw destroyed base ruins
    if (!forestOnly && world.tileMap.isBaseDestroyed()) {
      for (let r = 24; r <= 25; r++) {
        for (let c = 12; c <= 13; c++) {
          if (r < GRID && c < GRID) {
            this.artist.drawBase(c * CELL, r * CELL, CELL, true)
          }
        }
      }
    }
  }

  // ---- Tanks ----

  private renderTanks(world: World): void {
    const frame = world.frame
    for (const tank of world.allTanks) {
      if (!tank.alive) continue

      // Spawn animation
      if (tank.spawnTimer > 0) {
        this.artist.drawSpawn(tank.x, tank.y, tank.w, frame)
        continue
      }

      // Determine animation frame
      const vc = this.animations.get(tank.id)
      const animFrame = vc ? this.animations.getFrame(vc) : (frame >> 2) & 1

      // Draw tank
      if (tank.isPlayer) {
        this.artist.drawPlayerTank(
          tank.x,
          tank.y,
          tank.w,
          tank.dir,
          tank.level ?? 0,
          animFrame,
        )
      } else {
        this.artist.drawEnemyTank(
          tank.x,
          tank.y,
          tank.w,
          tank.dir,
          tank.kind,
          animFrame,
          (tank.flashTimer ?? 0) > 0,
          tank.hp,
        )
      }

      // Bonus enemy indicator
      if (tank.bonus) {
        const blink = Math.floor(frame / 10) % 2 === 0
        if (blink) {
          this.ctx.strokeStyle = '#ff4040'
          this.ctx.lineWidth = 1
          this.ctx.strokeRect(tank.x - 1, tank.y - 1, tank.w + 2, tank.h + 2)
        }
      }

      // Shield effect
      if (tank.shieldTimer && tank.shieldTimer > 0) {
        this.artist.drawShield(tank.x, tank.y, tank.w, frame)
      }
    }
  }

  // ---- Bullets ----

  private renderBullets(world: World): void {
    for (const bullet of world.bullets) {
      if (!bullet.alive) continue
      this.artist.drawBullet(bullet.x, bullet.y, bullet.w, bullet.dir)
    }
  }

  // ---- Power-ups ----

  private renderPowerUps(world: World): void {
    const frame = world.frame
    for (const pu of world.powerUps) {
      if (!pu.alive) continue
      this.artist.drawPowerUp(pu.x, pu.y, pu.w, pu.type, frame)
    }
  }

  // ---- Explosions ----

  private renderExplosions(world: World): void {
    for (const exp of world.explosions) {
      const progress = 1 - exp.timer / exp.maxTimer
      this.artist.drawExplosion(exp.x, exp.y, exp.size, progress, exp.kind)
    }
  }

  // ---- Particles ----

  private renderParticles(): void {
    const ctx = this.ctx
    const particles = this.particles.getActiveParticles()

    for (const p of particles) {
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
    ctx.fillStyle = gradient
    ctx.fillRect(0, 0, FIELD, FIELD)
  }
}
