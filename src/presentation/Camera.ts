import type { CameraState } from '../types'

/**
 * Camera — provides screen shake and offset for the game canvas.
 * Does not affect simulation; only transforms render output.
 */
export class Camera {
  state: CameraState

  constructor() {
    this.state = {
      x: 0,
      y: 0,
      shake: 0,
      shakeDecay: 0.85,
      offsetX: 0,
      offsetY: 0,
      scale: 1,
    }
  }

  /** Trigger screen shake with given intensity */
  shakeScreen(intensity: number): void {
    this.state.shake = Math.max(this.state.shake, intensity)
  }

  /** Set a sustained offset (for future panning) */
  setOffset(x: number, y: number): void {
    this.state.offsetX = x
    this.state.offsetY = y
  }

  /** Update camera — called every render frame */
  update(dt: number): void {
    const s = this.state
    // Apply shake decay
    if (s.shake > 0.01) {
      s.shake *= Math.pow(s.shakeDecay, dt / 16.67)
    } else {
      s.shake = 0
    }
  }

  /**
   * Get the current render offset (shake + sustained offset).
   * Returns a reused object — never allocates — so the per-frame render path
   * stays allocation-free. Callers must read it immediately (it is mutated on
   * the next call).
   */
  private _offset: { x: number; y: number } = { x: 0, y: 0 }
  getOffset(): { x: number; y: number } {
    const s = this.state
    const shakeX = (Math.random() - 0.5) * s.shake * 2
    const shakeY = (Math.random() - 0.5) * s.shake * 2
    this._offset.x = s.offsetX + shakeX
    this._offset.y = s.offsetY + shakeY
    return this._offset
  }

  reset(): void {
    this.state.shake = 0
    this.state.offsetX = 0
    this.state.offsetY = 0
  }
}
