import type { VisualComponent } from '../types'
import type { Direction } from '../constants'

/**
 * Animation definitions — data-driven animation configs.
 * Each sprite type defines its animations with fps and frame counts.
 */
export interface AnimationDef {
  fps: number
  frames: number
  loop: boolean
}

export const ANIMATION_DEFS: Record<string, Record<string, AnimationDef>> = {
  tank: {
    idle: { fps: 2, frames: 1, loop: true },
    move: { fps: 8, frames: 2, loop: true },
    spawn: { fps: 6, frames: 4, loop: true },
    destroy: { fps: 15, frames: 6, loop: false },
  },
  bullet: {
    fly: { fps: 10, frames: 2, loop: true },
  },
  explosion: {
    small: { fps: 20, frames: 4, loop: false },
    big: { fps: 15, frames: 8, loop: false },
  },
  powerup: {
    idle: { fps: 4, frames: 2, loop: true },
  },
}

/**
 * AnimationSystem — manages time-based animation state for entities.
 * Tracks visual components per entity and computes current frame.
 */
export class AnimationSystem {
  private components = new Map<number, VisualComponent>()

  /** Get or create a visual component for an entity */
  getOrCreate(
    entityId: number,
    sprite: string,
    direction: Direction,
    level: number = 0,
  ): VisualComponent {
    let vc = this.components.get(entityId)
    if (!vc) {
      vc = {
        entityId,
        sprite,
        animation: 'idle',
        direction,
        elapsed: 0,
        alpha: 1,
        scale: 1,
        flash: false,
        level,
      }
      this.components.set(entityId, vc)
    }
    return vc
  }

  /** Update animation elapsed time */
  update(dt: number): void {
    for (const vc of this.components.values()) {
      vc.elapsed += dt
    }
  }

  /** Set the animation for a component, resetting elapsed if it changed */
  setAnimation(vc: VisualComponent, animation: string): void {
    if (vc.animation !== animation) {
      vc.animation = animation
      vc.elapsed = 0
    }
  }

  /** Compute the current frame for a visual component */
  getFrame(vc: VisualComponent): number {
    const spriteDefs = ANIMATION_DEFS[vc.sprite]
    if (!spriteDefs) return 0
    const def = spriteDefs[vc.animation]
    if (!def) return 0

    const frameDuration = 1000 / def.fps
    const frame = Math.floor(vc.elapsed / frameDuration)
    if (def.loop) {
      return frame % def.frames
    }
    return Math.min(frame, def.frames - 1)
  }

  /** Check if a non-looping animation has finished */
  isFinished(vc: VisualComponent): boolean {
    const spriteDefs = ANIMATION_DEFS[vc.sprite]
    if (!spriteDefs) return false
    const def = spriteDefs[vc.animation]
    if (!def || def.loop) return false
    const frameDuration = 1000 / def.fps
    return vc.elapsed >= frameDuration * def.frames
  }

  /**
   * Remove visual components whose entity was not seen alive this frame.
   * Uses a frame stamp (set on the component by the presentation layer) instead
   * of a per-frame Set, so cleanup is allocation-free and O(components).
   */
  cleanup(currentFrame: number): void {
    for (const [id, vc] of this.components) {
      if (vc.lastSeenFrame !== currentFrame) {
        this.components.delete(id)
      }
    }
  }

  /** Get a component by entity ID */
  get(entityId: number): VisualComponent | undefined {
    return this.components.get(entityId)
  }

  /** Clear all components */
  clear(): void {
    this.components.clear()
  }
}
