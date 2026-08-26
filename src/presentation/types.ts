// ============================================================
// Presentation Layer Types (plan/refactor.agy.md §2.6)
//
// Types consumed exclusively by src/presentation/. Canonical
// definitions live here; src/types.ts re-exports them so existing
// imports keep working. Simulation/config/UI types stay in the root
// types.ts — ThemeColors deliberately remains there because it is a
// CONFIG-layer contract (config/theme.ts), not presentation state.
// ============================================================

import type { Direction } from '../constants'

/** Visual component — tracks the visual state of a simulation entity */
export interface VisualComponent {
  entityId: number
  sprite: string // e.g. "tank.player", "bullet", "explosion.big"
  animation: string // e.g. "idle", "move", "spawn", "destroy"
  direction: Direction
  elapsed: number // ms since animation started
  alpha: number
  scale: number
  flash: boolean
  level: number
  /**
   * Frame stamp used for mark-and-sweep cleanup of stale visual components
   * (avoids allocating a Set every render frame). Set to world.frame each
   * time the component is seen as alive; components whose stamp is stale are
   * removed by AnimationSystem.cleanup().
   */
  lastSeenFrame?: number
}

/** Particle — a single visual particle */
export interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  maxLife: number
  size: number
  color: string
  type: 'spark' | 'debris' | 'smoke' | 'ring' | 'flash'
  gravity: number
  drag: number
  rotation: number
  rotSpeed: number
  active: boolean
}

/** Particle emitter configuration */
export interface EmitterConfig {
  x: number
  y: number
  count: number
  speedMin: number
  speedMax: number
  lifeMin: number
  lifeMax: number
  sizeMin: number
  sizeMax: number
  colors: string[]
  type: Particle['type']
  gravity: number
  drag: number
  angleMin: number // radians
  angleMax: number
  spread: number // positional spread radius
}

/** Camera state */
export interface CameraState {
  x: number
  y: number
  shake: number
  shakeDecay: number
  offsetX: number
  offsetY: number
  scale: number
}
