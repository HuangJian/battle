import type { World } from '../game/World'
import type { GameEvent, EmitterConfig } from '../types'
import { FIELD, TANK } from '../constants'
import { Camera } from './Camera'
import { AnimationSystem } from './AnimationSystem'
import { ParticleSystem } from './ParticleSystem'
import { EffectsSystem } from './EffectsSystem'
import { GameRenderer } from './renderer/GameRenderer'
import { UIManager } from './ui/UIManager'

/**
 * PresentationLayer — the main presentation orchestrator.
 * Sits between the World (read-only) and the display.
 * Manages camera, animations, particles, effects, and UI.
 * Never modifies the World.
 */
export class PresentationLayer {
  camera: Camera
  animations: AnimationSystem
  particles: ParticleSystem
  effects: EffectsSystem
  renderer: GameRenderer
  ui: UIManager
  private dpr: number

  constructor(root: HTMLElement) {
    this.camera = new Camera()
    this.animations = new AnimationSystem()
    this.particles = new ParticleSystem()
    this.effects = new EffectsSystem()

    // Create UI first — it creates the canvas
    this.ui = new UIManager(root)

    this.dpr = Math.min(window.devicePixelRatio || 1, 2) // cap at 2x for performance

    this.renderer = new GameRenderer(
      this.ui.canvas,
      this.camera,
      this.animations,
      this.particles,
      this.effects,
      this.dpr,
    )
  }

  /** Process game events for visual effects */
  handleEvents(events: GameEvent[]): void {
    for (const event of events) {
      switch (event.type) {
        case 'explosion':
          this.onExplosion(event.x, event.y, event.kind)
          break
        case 'tank_destroyed':
          this.onTankDestroyed(event.tank, event.by)
          break
        case 'base_destroyed':
          this.onBaseDestroyed()
          break
        case 'powerup_collected':
          this.onPowerUpCollected(event.powerUp)
          break
        case 'stage_clear':
          this.effects.triggerFlash('#ffffff', 0.4)
          break
        case 'player_hit':
          this.camera.shakeScreen(10)
          this.effects.triggerFlash('#ff4040', 0.35)
          this.effects.triggerHitPause(80)
          break
        case 'bullet_fired':
          // Small muzzle flash particles
          this.onBulletFired(event.bullet)
          break
      }
    }
  }

  private onExplosion(x: number, y: number, kind: 'small' | 'big'): void {
    if (kind === 'big') {
      this.camera.shakeScreen(6)
      this.effects.triggerFlash('#ffaa40', 0.15)

      // Flash particle
      this.particles.emit(this.makeFlashEmitter(x, y))

      // Expanding ring
      this.particles.emit(this.makeRingEmitter(x, y))

      // Sparks
      this.particles.emit(this.makeSparkEmitter(x, y, 12, 2, 5))

      // Debris
      this.particles.emit(this.makeDebrisEmitter(x, y, 8))

      // Smoke
      this.particles.emit(this.makeSmokeEmitter(x, y, 5))
    } else {
      this.camera.shakeScreen(2)

      // Small sparks
      this.particles.emit(this.makeSparkEmitter(x, y, 5, 1, 3))
    }
  }

  private onTankDestroyed(
    tank: { x: number; y: number; w: number; h: number; isPlayer?: boolean },
    by: 'player' | 'enemy' | 'self',
  ): void {
    const cx = tank.x + tank.w / 2
    const cy = tank.y + tank.h / 2

    if (tank.isPlayer) {
      this.camera.shakeScreen(14)
      this.effects.triggerFlash('#ff4040', 0.4)
      this.effects.triggerHitPause(120)
      // Extra debris for player
      this.particles.emit(this.makeDebrisEmitter(cx, cy, 12))
    } else if (by === 'player') {
      this.camera.shakeScreen(4)
      // Debris from destroyed enemy
      this.particles.emit(this.makeDebrisEmitter(cx, cy, 6))
    }
  }

  private onBaseDestroyed(): void {
    this.camera.shakeScreen(20)
    this.effects.triggerFlash('#ff2020', 0.6)
    this.effects.triggerHitPause(200)

    // Big particle burst at base location
    const cx = FIELD / 2
    const cy = FIELD - TANK
    this.particles.emit(this.makeSparkEmitter(cx, cy, 20, 3, 7))
    this.particles.emit(this.makeDebrisEmitter(cx, cy, 15))
    this.particles.emit(this.makeSmokeEmitter(cx, cy, 8))
  }

  private onPowerUpCollected(_type: string): void {
    // Sparkle effect will be at player position, handled in updateVisualState
  }

  private onBulletFired(bullet: { x: number; y: number; dir: string; isPlayer: boolean }): void {
    if (bullet.isPlayer) {
      // Small muzzle flash
      this.particles.emit({
        x: bullet.x,
        y: bullet.y,
        count: 3,
        speedMin: 0.5,
        speedMax: 1.5,
        lifeMin: 100,
        lifeMax: 200,
        sizeMin: 1,
        sizeMax: 2,
        colors: ['#ffe060', '#ffaa40'],
        type: 'spark',
        gravity: 0,
        drag: 0.9,
        angleMin: 0,
        angleMax: Math.PI * 2,
        spread: 3,
      })
    }
  }

  // ---- Emitter configs ----

  private makeSparkEmitter(
    x: number,
    y: number,
    count: number,
    speedMin: number,
    speedMax: number,
  ): EmitterConfig {
    return {
      x,
      y,
      count,
      speedMin: speedMin * 0.5,
      speedMax: speedMax * 0.5,
      lifeMin: 200,
      lifeMax: 500,
      sizeMin: 1,
      sizeMax: 3,
      colors: ['#ffe040', '#ff8020', '#ff4020', '#ffffff'],
      type: 'spark',
      gravity: 0.05,
      drag: 0.92,
      angleMin: 0,
      angleMax: Math.PI * 2,
      spread: 4,
    }
  }

  private makeDebrisEmitter(x: number, y: number, count: number): EmitterConfig {
    return {
      x,
      y,
      count,
      speedMin: 1,
      speedMax: 3,
      lifeMin: 400,
      lifeMax: 800,
      sizeMin: 2,
      sizeMax: 4,
      colors: ['#808080', '#606060', '#a0a0a0', '#404040'],
      type: 'debris',
      gravity: 0.15,
      drag: 0.95,
      angleMin: 0,
      angleMax: Math.PI * 2,
      spread: 8,
    }
  }

  private makeSmokeEmitter(x: number, y: number, count: number): EmitterConfig {
    return {
      x,
      y,
      count,
      speedMin: 0.2,
      speedMax: 0.8,
      lifeMin: 600,
      lifeMax: 1000,
      sizeMin: 4,
      sizeMax: 8,
      colors: ['#606060', '#404040', '#808080'],
      type: 'smoke',
      gravity: -0.02,
      drag: 0.96,
      angleMin: -Math.PI / 2 - 0.5,
      angleMax: -Math.PI / 2 + 0.5,
      spread: 6,
    }
  }

  private makeFlashEmitter(x: number, y: number): EmitterConfig {
    return {
      x,
      y,
      count: 1,
      speedMin: 0,
      speedMax: 0,
      lifeMin: 150,
      lifeMax: 150,
      sizeMin: 20,
      sizeMax: 20,
      colors: ['#ffffff'],
      type: 'flash',
      gravity: 0,
      drag: 1,
      angleMin: 0,
      angleMax: 0,
      spread: 0,
    }
  }

  private makeRingEmitter(x: number, y: number): EmitterConfig {
    return {
      x,
      y,
      count: 1,
      speedMin: 0,
      speedMax: 0,
      lifeMin: 300,
      lifeMax: 300,
      sizeMin: 8,
      sizeMax: 8,
      colors: ['#ffe040'],
      type: 'ring',
      gravity: 0,
      drag: 1,
      angleMin: 0,
      angleMax: 0,
      spread: 0,
    }
  }

  // ---- Update & Render ----

  /** Update visual state from world, then render everything */
  render(world: World, dt: number): void {
    // Update visual state from world
    this.updateVisualState(world)

    // Update presentation systems
    this.animations.update(dt)
    this.particles.update(dt)
    this.camera.update(dt)
    this.effects.update(dt)

    // Apply theme to UI
    this.ui.applyTheme(world.theme)

    // Render game world
    this.renderer.render(world)

    // Update HTML UI
    this.ui.update(world)
  }

  /** Sync visual components with simulation entities */
  private updateVisualState(world: World): void {
    const activeIds = new Set<number>()

    for (const tank of world.allTanks) {
      if (!tank.alive) continue
      activeIds.add(tank.id)

      const vc = this.animations.getOrCreate(tank.id, 'tank', tank.dir, tank.level ?? 0)
      vc.direction = tank.dir
      vc.level = tank.level ?? 0
      vc.flash = (tank.flashTimer ?? 0) > 0

      if (tank.spawnTimer > 0) {
        this.animations.setAnimation(vc, 'spawn')
      } else if (tank.moving) {
        this.animations.setAnimation(vc, 'move')
      } else {
        this.animations.setAnimation(vc, 'idle')
      }
    }

    // Clean up visual components for dead entities
    this.animations.cleanup(activeIds)
  }

  /** Reset presentation state (e.g., when returning to menu) */
  reset(): void {
    this.animations.clear()
    this.particles.clear()
    this.effects.reset()
    this.camera.reset()
  }
}
