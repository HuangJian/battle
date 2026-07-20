/**
 * EffectsSystem — manages screen-level visual effects.
 * Handles flash, hit-pause, and slow-motion moments.
 */
export class EffectsSystem {
  /** Screen flash intensity (0-1), fades over time */
  private flashIntensity = 0
  private flashColor = '#ffffff'

  /** Hit pause — freezes simulation rendering briefly */
  private hitPauseTimer = 0

  /** Slow motion factor (1 = normal, 0.5 = half speed) */
  private slowMoFactor = 1
  private slowMoTimer = 0

  update(dt: number): void {
    if (this.flashIntensity > 0) {
      this.flashIntensity = Math.max(0, this.flashIntensity - dt / 200)
    }
    if (this.hitPauseTimer > 0) {
      this.hitPauseTimer -= dt
    }
    if (this.slowMoTimer > 0) {
      this.slowMoTimer -= dt
      if (this.slowMoTimer <= 0) {
        this.slowMoFactor = 1
      }
    }
  }

  triggerFlash(color: string, intensity: number = 0.4): void {
    this.flashColor = color
    this.flashIntensity = Math.max(this.flashIntensity, intensity)
  }

  triggerHitPause(duration: number = 60): void {
    this.hitPauseTimer = Math.max(this.hitPauseTimer, duration)
  }

  triggerSlowMo(factor: number, duration: number): void {
    this.slowMoFactor = factor
    this.slowMoTimer = duration
  }

  getFlash(): { color: string; intensity: number } | null {
    if (this.flashIntensity <= 0) return null
    return { color: this.flashColor, intensity: this.flashIntensity }
  }

  get isPaused(): boolean {
    return this.hitPauseTimer > 0
  }

  get timeScale(): number {
    return this.slowMoFactor
  }

  reset(): void {
    this.flashIntensity = 0
    this.hitPauseTimer = 0
    this.slowMoFactor = 1
    this.slowMoTimer = 0
  }
}
