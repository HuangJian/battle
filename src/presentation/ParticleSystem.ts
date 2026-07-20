import type { Particle, EmitterConfig } from '../types'

const POOL_SIZE = 500

/**
 * ParticleSystem — pool-based particle engine.
 * Pre-allocates particles and reuses them.
 */
export class ParticleSystem {
  private pool: Particle[] = []
  private activeCount = 0

  constructor() {
    for (let i = 0; i < POOL_SIZE; i++) {
      this.pool.push(this.createParticle())
    }
  }

  private createParticle(): Particle {
    return {
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      life: 0,
      maxLife: 1,
      size: 1,
      color: '#fff',
      type: 'spark',
      gravity: 0,
      drag: 0.98,
      rotation: 0,
      rotSpeed: 0,
      active: false,
    }
  }

  /** Emit particles from a configuration */
  emit(config: EmitterConfig): void {
    for (let i = 0; i < config.count; i++) {
      const p = this.getFreeParticle()
      if (!p) break

      const angle = config.angleMin + Math.random() * (config.angleMax - config.angleMin)
      const speed = config.speedMin + Math.random() * (config.speedMax - config.speedMin)
      const life = config.lifeMin + Math.random() * (config.lifeMax - config.lifeMin)
      const size = config.sizeMin + Math.random() * (config.sizeMax - config.sizeMin)
      const color = config.colors[Math.floor(Math.random() * config.colors.length)]

      const spreadX = (Math.random() - 0.5) * config.spread * 2
      const spreadY = (Math.random() - 0.5) * config.spread * 2

      p.x = config.x + spreadX
      p.y = config.y + spreadY
      p.vx = Math.cos(angle) * speed
      p.vy = Math.sin(angle) * speed
      p.life = life
      p.maxLife = life
      p.size = size
      p.color = color
      p.type = config.type
      p.gravity = config.gravity
      p.drag = config.drag
      p.rotation = Math.random() * Math.PI * 2
      p.rotSpeed = (Math.random() - 0.5) * 0.3
      p.active = true
    }
  }

  private getFreeParticle(): Particle | null {
    for (let i = 0; i < POOL_SIZE; i++) {
      if (!this.pool[i].active) {
        this.activeCount = Math.max(this.activeCount, i + 1)
        return this.pool[i]
      }
    }
    return null
  }

  /** Update all active particles */
  update(dt: number): void {
    const dts = dt / 16.67 // normalize to 60fps units
    for (let i = 0; i < this.activeCount; i++) {
      const p = this.pool[i]
      if (!p.active) continue

      p.life -= dt
      if (p.life <= 0) {
        p.active = false
        continue
      }

      p.vx *= Math.pow(p.drag, dts)
      p.vy *= Math.pow(p.drag, dts)
      p.vy += p.gravity * dts
      p.x += p.vx * dts
      p.y += p.vy * dts
      p.rotation += p.rotSpeed * dts
    }
  }

  /** Get all active particles for rendering */
  getActiveParticles(): Particle[] {
    const result: Particle[] = []
    for (let i = 0; i < this.activeCount; i++) {
      if (this.pool[i].active) {
        result.push(this.pool[i])
      }
    }
    return result
  }

  /** Clear all particles */
  clear(): void {
    for (const p of this.pool) {
      p.active = false
    }
    this.activeCount = 0
  }
}
