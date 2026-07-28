/**
 * ReplayController — video-player-style overlay for replay playback.
 *
 * Positioned at the bottom of the screen, semi-transparent.
 * Features: play/pause, draggable progress bar, speed controls.
 */
export interface ReplayControllerCallbacks {
  onPlayPause: () => void
  onSeek: (progress: number) => void
  onSpeedChange: (speed: number) => void
  onExit: () => void
}

const SPEEDS = [0.5, 1, 1.5, 2, 4]

export class ReplayController {
  readonly el: HTMLElement
  private callbacks: ReplayControllerCallbacks | null = null

  // DOM elements
  private playPauseBtn: HTMLElement
  private progressTrack: HTMLElement
  private progressFill: HTMLElement
  private progressHandle: HTMLElement
  private speedBtn: HTMLElement
  private timeDisplay: HTMLElement
  private exitBtn: HTMLElement

  // State
  private _isPaused = false
  private _currentSpeed = 1
  private _isDragging = false

  constructor() {
    this.el = document.createElement('div')
    this.el.className = 'replay-controller'
    this.el.innerHTML = `
      <div class="rc-inner">
        <button class="rc-btn rc-play-pause" type="button" aria-label="Play/Pause">
          <span class="rc-icon-pause">⏸</span>
          <span class="rc-icon-play" style="display:none">▶</span>
        </button>

        <div class="rc-progress-wrapper">
          <div class="rc-progress-track">
            <div class="rc-progress-fill"></div>
            <div class="rc-progress-handle"></div>
          </div>
        </div>

        <span class="rc-time">0:00 / 0:00</span>

        <button class="rc-btn rc-speed" type="button" aria-label="Speed">1×</button>

        <button class="rc-btn rc-exit" type="button" aria-label="Exit replay">✕</button>
      </div>
    `

    // Cache elements
    this.playPauseBtn = this.el.querySelector('.rc-play-pause')!
    this.progressTrack = this.el.querySelector('.rc-progress-track')!
    this.progressFill = this.el.querySelector('.rc-progress-fill')!
    this.progressHandle = this.el.querySelector('.rc-progress-handle')!
    this.speedBtn = this.el.querySelector('.rc-speed')!
    this.timeDisplay = this.el.querySelector('.rc-time')!
    this.exitBtn = this.el.querySelector('.rc-exit')!

    this.setupEventListeners()
  }

  init(callbacks: ReplayControllerCallbacks): void {
    this.callbacks = callbacks
  }

  private setupEventListeners(): void {
    // Play/Pause
    this.playPauseBtn.addEventListener('click', () => {
      this.callbacks?.onPlayPause()
    })

    // Progress bar click
    this.progressTrack.addEventListener('click', (e) => {
      if (this._isDragging) return
      const rect = this.progressTrack.getBoundingClientRect()
      const progress = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
      this.callbacks?.onSeek(progress)
    })

    // Progress bar drag
    this.progressHandle.addEventListener('mousedown', (e) => {
      e.preventDefault()
      this._isDragging = true
      const onMove = (ev: MouseEvent) => {
        const rect = this.progressTrack.getBoundingClientRect()
        const progress = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width))
        this.updateProgress(progress)
        this.callbacks?.onSeek(progress)
      }
      const onUp = () => {
        this._isDragging = false
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
      }
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    })

    // Touch support for progress bar
    this.progressTrack.addEventListener('touchstart', (e) => {
      e.preventDefault()
      this._isDragging = true
      const onMove = (ev: TouchEvent) => {
        const touch = ev.touches[0]
        const rect = this.progressTrack.getBoundingClientRect()
        const progress = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width))
        this.updateProgress(progress)
        this.callbacks?.onSeek(progress)
      }
      const onEnd = () => {
        this._isDragging = false
        window.removeEventListener('touchmove', onMove)
        window.removeEventListener('touchend', onEnd)
      }
      window.addEventListener('touchmove', onMove)
      window.addEventListener('touchend', onEnd)
    })

    // Speed control
    this.speedBtn.addEventListener('click', () => {
      const idx = SPEEDS.indexOf(this._currentSpeed)
      const next = SPEEDS[(idx + 1) % SPEEDS.length]
      this.callbacks?.onSpeedChange(next)
    })

    // Exit
    this.exitBtn.addEventListener('click', () => {
      this.callbacks?.onExit()
    })
  }

  show(): void {
    this.el.hidden = false
  }

  hide(): void {
    this.el.hidden = true
  }

  setPaused(paused: boolean): void {
    this._isPaused = paused
    const pauseIcon = this.playPauseBtn.querySelector('.rc-icon-pause') as HTMLElement
    const playIcon = this.playPauseBtn.querySelector('.rc-icon-play') as HTMLElement
    if (pauseIcon) pauseIcon.style.display = paused ? 'none' : ''
    if (playIcon) playIcon.style.display = paused ? '' : 'none'
  }

  setSpeed(speed: number): void {
    this._currentSpeed = speed
    this.speedBtn.textContent = speed === 1 ? '1×' : `${speed}×`
    this.speedBtn.classList.toggle('rc-speed-fast', speed > 1)
  }

  updateProgress(progress: number): void {
    if (this._isDragging) return
    this._progress = progress
    this.progressFill.style.width = `${Math.round(progress * 100)}%`
    this.progressHandle.style.left = `${Math.round(progress * 100)}%`
  }

  updateTime(currentMs: number, totalMs: number): void {
    this._duration = totalMs
    this.timeDisplay.textContent = `${formatTime(currentMs)} / ${formatTime(totalMs)}`
  }

  get isPaused(): boolean {
    return this._isPaused
  }

  get currentSpeed(): number {
    return this._currentSpeed
  }
}

function formatTime(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `${min}:${String(sec).padStart(2, '0')}`
}
