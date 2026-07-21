import type { GameEvent } from '../types'

/**
 * AudioManager — generates 8-bit style sound effects with Web Audio API.
 * No external audio files needed.
 */
export class AudioManager {
  private ctx: AudioContext | null = null
  private masterGain: GainNode | null = null
  private volume = 0.3
  private enabled = true

  init(): void {
    if (this.ctx) return
    try {
      const AC =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      this.ctx = new AC()
      this.masterGain = this.ctx.createGain()
      this.masterGain.gain.value = this.volume
      this.masterGain.connect(this.ctx.destination)
    } catch {
      this.enabled = false
    }
  }

  setVolume(v: number): void {
    this.volume = v
    if (this.masterGain) {
      this.masterGain.gain.value = v
    }
  }

  resume(): void {
    if (this.ctx && this.ctx.state === 'suspended') {
      this.ctx.resume()
    }
  }

  // ---- Low-level sound generation ----

  private beep(
    freq: number,
    duration: number,
    type: OscillatorType = 'square',
    gain: number = 0.3,
  ): void {
    if (!this.ctx || !this.masterGain || !this.enabled) return
    const osc = this.ctx.createOscillator()
    const env = this.ctx.createGain()
    osc.type = type
    osc.frequency.value = freq
    env.gain.setValueAtTime(0, this.ctx.currentTime)
    env.gain.linearRampToValueAtTime(gain, this.ctx.currentTime + 0.01)
    env.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + duration)
    osc.connect(env)
    env.connect(this.masterGain)
    osc.start()
    osc.stop(this.ctx.currentTime + duration)
  }

  private sweep(
    freqStart: number,
    freqEnd: number,
    duration: number,
    type: OscillatorType = 'square',
    gain: number = 0.3,
  ): void {
    if (!this.ctx || !this.masterGain || !this.enabled) return
    const osc = this.ctx.createOscillator()
    const env = this.ctx.createGain()
    osc.type = type
    osc.frequency.setValueAtTime(freqStart, this.ctx.currentTime)
    osc.frequency.exponentialRampToValueAtTime(
      Math.max(1, freqEnd),
      this.ctx.currentTime + duration,
    )
    env.gain.setValueAtTime(0, this.ctx.currentTime)
    env.gain.linearRampToValueAtTime(gain, this.ctx.currentTime + 0.01)
    env.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + duration)
    osc.connect(env)
    env.connect(this.masterGain)
    osc.start()
    osc.stop(this.ctx.currentTime + duration)
  }

  private noise(duration: number, gain: number = 0.3, filterFreq: number = 1000): void {
    if (!this.ctx || !this.masterGain || !this.enabled) return
    const bufferSize = this.ctx.sampleRate * duration
    const buffer = this.ctx.createBuffer(1, bufferSize, this.ctx.sampleRate)
    const data = buffer.getChannelData(0)
    for (let i = 0; i < bufferSize; i++) {
      data[i] = Math.random() * 2 - 1
    }
    const source = this.ctx.createBufferSource()
    source.buffer = buffer
    const filter = this.ctx.createBiquadFilter()
    filter.type = 'lowpass'
    filter.frequency.value = filterFreq
    const env = this.ctx.createGain()
    env.gain.setValueAtTime(gain, this.ctx.currentTime)
    env.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + duration)
    source.connect(filter)
    filter.connect(env)
    env.connect(this.masterGain)
    source.start()
    source.stop(this.ctx.currentTime + duration)
  }

  // ---- Game sound effects ----

  playShoot(): void {
    this.sweep(800, 200, 0.08, 'square', 0.15)
  }

  playEnemyShoot(): void {
    this.sweep(400, 100, 0.06, 'square', 0.1)
  }

  playExplosionSmall(): void {
    this.noise(0.1, 0.2, 2000)
  }

  playExplosionBig(): void {
    this.noise(0.4, 0.3, 800)
    this.sweep(200, 50, 0.3, 'sawtooth', 0.2)
  }

  playBrick(): void {
    this.noise(0.05, 0.15, 3000)
  }

  playSteel(): void {
    this.beep(1200, 0.03, 'square', 0.1)
  }

  playPowerUp(): void {
    this.sweep(400, 1200, 0.15, 'square', 0.2)
    setTimeout(() => this.sweep(600, 1600, 0.1, 'square', 0.15), 80)
  }

  playPlayerHit(): void {
    this.noise(0.3, 0.3, 500)
    this.sweep(300, 50, 0.3, 'sawtooth', 0.2)
  }

  playGameOver(): void {
    this.sweep(400, 50, 0.8, 'sawtooth', 0.25)
  }

  playStageClear(): void {
    const notes = [523, 659, 784, 1047]
    notes.forEach((freq, i) => {
      setTimeout(() => this.beep(freq, 0.15, 'square', 0.2), i * 120)
    })
  }

  playMenuSelect(): void {
    this.beep(800, 0.05, 'square', 0.15)
  }

  playPause(): void {
    this.beep(600, 0.05, 'square', 0.1)
  }

  playRecoveryStart(): void {
    // Descending sweep — signifies time rewind
    this.sweep(800, 200, 0.4, 'sawtooth', 0.2)
  }

  playCountdownBeep(): void {
    this.beep(880, 0.08, 'square', 0.15)
  }

  playCountdownGo(): void {
    this.beep(1320, 0.15, 'square', 0.2)
  }

  /** Stop all currently sounding oscillators/sources immediately. */
  stopAll(): void {
    if (!this.ctx) return
    // A brute-force way to silence everything: suspend and resume.
    // Any scheduled sources that haven't finished will be cut off.
    try {
      this.ctx.suspend()
      // Resume on next microtask so future sounds work
      setTimeout(() => {
        this.ctx?.resume()
      }, 0)
    } catch {
      /* ignore */
    }
  }

  // ---- Event handler ----

  handleEvents(events: GameEvent[]): void {
    for (const event of events) {
      switch (event.type) {
        case 'bullet_fired':
          if (event.bullet.isPlayer) this.playShoot()
          else this.playEnemyShoot()
          break
        case 'explosion':
          if (event.kind === 'big') this.playExplosionBig()
          else this.playExplosionSmall()
          break
        case 'tank_destroyed':
          if (event.tank.isPlayer) this.playPlayerHit()
          else this.playExplosionBig()
          break
        case 'powerup_collected':
          this.playPowerUp()
          break
        case 'base_destroyed':
          this.playExplosionBig()
          break
        case 'player_hit':
          this.playPlayerHit()
          break
        case 'stage_clear':
          this.playStageClear()
          break
      }
    }
  }
}
