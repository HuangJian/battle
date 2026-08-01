/**
 * ReplayController — video-player-style overlay for replay playback.
 *
 * Positioned at the bottom of the screen, semi-transparent.
 * Features: play/pause, draggable progress bar, speed dropdown,
 * thumbnail preview on hover, auto-hide during idle playback.
 */
export interface ReplayControllerCallbacks {
  onPlayPause: () => void
  onSeek: (progress: number) => void
  onSpeedChange: (speed: number) => void
  onExit: () => void
  onHoverStart: () => void
  onProgressHover: (progress: number, x: number, y: number) => void
  onProgressHoverEnd: () => void
  onReplayAgain: () => void
  onBackToMenu: () => void
  onExport: () => void
}

const SPEEDS = [0.5, 1, 1.5, 2, 4]

/** Auto-hide delay (ms) — controller fades out after this idle period. */
const AUTO_HIDE_DELAY = 3000

export class ReplayController {
  readonly el: HTMLElement
  private callbacks: ReplayControllerCallbacks | null = null

  // DOM elements
  private playPauseBtn: HTMLElement
  private progressTrack: HTMLElement
  private progressFill: HTMLElement
  private progressHandle: HTMLElement
  private speedBtn: HTMLElement
  private speedDropdown: HTMLElement
  private timeDisplay: HTMLElement
  private exitBtn: HTMLElement
  private exportBtn: HTMLElement
  private endOverlay: HTMLElement
  private endTitle: HTMLElement
  private endMeta: HTMLElement
  private replayAgainBtn: HTMLElement
  private backToMenuBtn: HTMLElement

  // State
  private _isPaused = false
  private _currentSpeed = 1
  private _isDragging = false
  private _duration = 0

  // Thumbnail preview
  private thumbnailEl: HTMLElement
  private thumbnailCanvas: HTMLCanvasElement
  private thumbnailCtx: CanvasRenderingContext2D
  private _lastHoverProgress = -1

  // Auto-hide
  private _hideTimer: ReturnType<typeof setTimeout> | null = null
  private _isActive = false
  private _isEnded = false

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
          <div class="rc-thumbnail" style="display:none">
            <canvas class="rc-thumbnail-canvas" width="160" height="160"></canvas>
            <span class="rc-thumbnail-time"></span>
          </div>
        </div>

        <span class="rc-time">0:00 / 0:00</span>

        <div class="rc-speed-wrapper">
          <button class="rc-btn rc-speed" type="button" aria-label="Speed">1×</button>
          <div class="rc-speed-dropdown" style="display:none">
            ${SPEEDS.map((s) => `<button class="rc-speed-option" data-speed="${s}" type="button">${s}×</button>`).join('')}
          </div>
        </div>

        <button class="rc-btn rc-export" type="button" aria-label="Export replay" title="Export .replay file">⤓</button>

        <button class="rc-btn rc-exit" type="button" aria-label="Exit replay">✕</button>
      </div>
    `

    // Cache controller elements
    this.playPauseBtn = this.el.querySelector('.rc-play-pause')!
    this.progressTrack = this.el.querySelector('.rc-progress-track')!
    this.progressFill = this.el.querySelector('.rc-progress-fill')!
    this.progressHandle = this.el.querySelector('.rc-progress-handle')!
    this.speedBtn = this.el.querySelector('.rc-speed')!
    this.speedDropdown = this.el.querySelector('.rc-speed-dropdown')!
    this.timeDisplay = this.el.querySelector('.rc-time')!
    this.exitBtn = this.el.querySelector('.rc-exit')!
    this.exportBtn = this.el.querySelector('.rc-export')!

    // Thumbnail — move to document.body so position:fixed is relative to
    // the viewport (backdrop-filter on the controller would trap it).
    this.thumbnailEl = this.el.querySelector('.rc-thumbnail')!
    this.thumbnailCanvas = this.el.querySelector('.rc-thumbnail-canvas') as HTMLCanvasElement
    this.thumbnailCtx = this.thumbnailCanvas.getContext('2d')!
    document.body.appendChild(this.thumbnailEl)

    // End overlay — create as a separate element on document.body so it
    // is completely independent of the controller's DOM tree.
    this.endOverlay = document.createElement('div')
    this.endOverlay.className = 'rc-end-overlay'
    this.endOverlay.style.display = 'none'
    this.endOverlay.innerHTML = `
      <div class="rc-end-info">
        <span class="rc-end-title"></span>
        <span class="rc-end-meta"></span>
      </div>
      <div class="rc-end-actions">
        <button class="rc-end-btn rc-end-replay" type="button" data-i18n="replay.ctrl.replayAgain">↻ REPLAY</button>
        <button class="rc-end-btn rc-end-menu" type="button" data-i18n="replay.ctrl.backToMenu">✕ MENU</button>
      </div>
    `
    document.body.appendChild(this.endOverlay)
    this.endTitle = this.endOverlay.querySelector('.rc-end-title')!
    this.endMeta = this.endOverlay.querySelector('.rc-end-meta')!
    this.replayAgainBtn = this.endOverlay.querySelector('.rc-end-replay')!
    this.backToMenuBtn = this.endOverlay.querySelector('.rc-end-menu')!

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

    // Progress bar click — seek to position
    // Guard against click events fired after a drag (mouseup triggers click)
    let _draggedSinceLastClick = false
    this.progressTrack.addEventListener('click', (e) => {
      if (this._isDragging || _draggedSinceLastClick) {
        _draggedSinceLastClick = false
        return
      }
      const rect = this.progressTrack.getBoundingClientRect()
      const progress = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
      this.callbacks?.onSeek(progress)
    })
    // Track drag starts so the subsequent click is suppressed
    this.progressHandle.addEventListener('mousedown', () => {
      _draggedSinceLastClick = true
    })

    // Progress bar drag — seek on mouse up
    this.progressHandle.addEventListener('mousedown', (e) => {
      e.preventDefault()
      e.stopPropagation()
      this._isDragging = true
      const onMove = (ev: MouseEvent) => {
        const rect = this.progressTrack.getBoundingClientRect()
        const progress = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width))
        this._updateProgressUI(progress)
      }
      const onUp = (ev: MouseEvent) => {
        this._isDragging = false
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        const rect = this.progressTrack.getBoundingClientRect()
        const progress = Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width))
        this.callbacks?.onSeek(progress)
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
        this._updateProgressUI(progress)
      }
      const onEnd = (ev: TouchEvent) => {
        this._isDragging = false
        window.removeEventListener('touchmove', onMove)
        window.removeEventListener('touchend', onEnd)
        if (ev.changedTouches.length > 0) {
          const touch = ev.changedTouches[0]
          const rect = this.progressTrack.getBoundingClientRect()
          const progress = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width))
          this.callbacks?.onSeek(progress)
        }
      }
      window.addEventListener('touchmove', onMove)
      window.addEventListener('touchend', onEnd)
    })

    // Progress bar hover — show thumbnail preview
    this.progressTrack.addEventListener('mouseenter', () => {
      if (this._isDragging) return
      this._lastHoverProgress = -1
      this.callbacks?.onHoverStart()
    })

    this.progressTrack.addEventListener('mousemove', (e) => {
      if (this._isDragging) return
      const rect = this.progressTrack.getBoundingClientRect()
      const progress = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
      this._showThumbnail(progress, e.clientX, e.clientY)
    })

    this.progressTrack.addEventListener('mouseleave', () => {
      this._hideThumbnail()
      this.callbacks?.onProgressHoverEnd()
    })

    // Speed dropdown toggle
    this.speedBtn.addEventListener('click', (e) => {
      e.stopPropagation()
      const isOpen = this.speedDropdown.style.display !== 'none'
      this.speedDropdown.style.display = isOpen ? 'none' : 'block'
    })

    // Speed option clicks
    this.speedDropdown.querySelectorAll('.rc-speed-option').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation()
        const speed = parseFloat((e.target as HTMLElement).dataset.speed || '1')
        this.setSpeed(speed)
        this.callbacks?.onSpeedChange(speed)
        this.speedDropdown.style.display = 'none'
      })
    })

    // Close speed dropdown on outside click
    document.addEventListener('click', () => {
      this.speedDropdown.style.display = 'none'
    })

    // Exit
    this.exitBtn.addEventListener('click', () => {
      this.callbacks?.onExit()
    })

    // Export
    this.exportBtn.addEventListener('click', () => {
      this.callbacks?.onExport()
    })

    // End-of-replay overlay buttons
    this.replayAgainBtn.addEventListener('click', () => {
      this.callbacks?.onReplayAgain()
    })
    this.backToMenuBtn.addEventListener('click', () => {
      this.callbacks?.onBackToMenu()
    })

    // Mouse movement on controller — reset auto-hide timer
    this.el.addEventListener('mousemove', () => {
      this._resetHideTimer()
    })
  }

  show(): void {
    this._isActive = true
    this._isEnded = false
    this._isPaused = false
    this.el.hidden = false
    this.el.classList.remove('rc-fading')
    this.endOverlay.style.display = 'none'
    this._lastHoverProgress = -1
    this._resetHideTimer()
  }

  hide(): void {
    this._isActive = false
    this.el.hidden = true
    this.endOverlay.style.display = 'none'
    this._clearHideTimer()
  }

  /** Show controller without auto-hide (for ended state). */
  showPersistent(): void {
    this._isActive = true
    this._isEnded = true
    this.el.hidden = false
    this.el.classList.remove('rc-fading')
    this._clearHideTimer()
    this.endOverlay.style.display = 'flex'
  }

  /** Populate the end overlay with replay metadata. */
  setEndMetadata(meta: { title: string; details: string; result?: string }): void {
    this.endTitle.textContent = meta.title
    this.endMeta.textContent = meta.details
    this.endOverlay.classList.toggle('rc-end-victory', meta.result === 'clear')
    this.endOverlay.classList.toggle(
      'rc-end-defeat',
      meta.result !== 'clear' && meta.result != null,
    )
  }

  setPaused(paused: boolean): void {
    this._isPaused = paused
    const pauseIcon = this.playPauseBtn.querySelector('.rc-icon-pause') as HTMLElement
    const playIcon = this.playPauseBtn.querySelector('.rc-icon-play') as HTMLElement
    if (pauseIcon) pauseIcon.style.display = paused ? 'none' : ''
    if (playIcon) playIcon.style.display = paused ? '' : 'none'

    if (!paused) {
      this._resetHideTimer()
    } else {
      this._clearHideTimer()
      this.el.classList.remove('rc-fading')
    }
  }

  setSpeed(speed: number): void {
    this._currentSpeed = speed
    this.speedBtn.textContent = speed === 1 ? '1×' : `${speed}×`
    this.speedBtn.classList.toggle('rc-speed-fast', speed > 1)
    // Update dropdown selection
    this.speedDropdown.querySelectorAll('.rc-speed-option').forEach((btn) => {
      const opt = btn as HTMLElement
      opt.classList.toggle('selected', parseFloat(opt.dataset.speed || '1') === speed)
    })
  }

  updateProgress(progress: number): void {
    if (this._isDragging) return
    this._updateProgressUI(progress)
  }

  private _updateProgressUI(progress: number): void {
    this.progressFill.style.width = `${Math.round(progress * 100)}%`
    this.progressHandle.style.left = `${Math.round(progress * 100)}%`
  }

  updateTime(currentMs: number, totalMs: number): void {
    this._duration = totalMs
    this.timeDisplay.textContent = `${formatTime(currentMs)} / ${formatTime(totalMs)}`
  }

  /** Update thumbnail canvas with rendered frame data. */
  updateThumbnail(imageData: ImageData): void {
    this.thumbnailCtx.putImageData(imageData, 0, 0)
  }

  /** Update thumbnail from a canvas source. */
  updateThumbnailCanvas(sourceCanvas: HTMLCanvasElement): void {
    this.thumbnailCtx.drawImage(sourceCanvas, 0, 0, 160, 160)
  }

  /** Get the thumbnail canvas for direct rendering. */
  getThumbnailCanvas(): HTMLCanvasElement {
    return this.thumbnailCanvas
  }

  private _showThumbnail(progress: number, mouseX: number, mouseY?: number): void {
    if (this._duration <= 0) return
    const currentMs = Math.round(progress * this._duration)
    const timeEl = this.thumbnailEl.querySelector('.rc-thumbnail-time')
    if (timeEl) timeEl.textContent = formatTime(currentMs)

    // Position thumbnail using viewport-relative coordinates (position: fixed).
    // Vertically: center the thumbnail on the mouse Y.
    // Horizontally: center on mouse X, fully above the cursor.
    const thumbWidth = 160
    const thumbHeight = 160
    const gap = 12
    const my = mouseY ?? 0

    let left = mouseX - thumbWidth / 2
    left = Math.max(8, Math.min(left, window.innerWidth - thumbWidth - 8))

    // Center vertically on mouse Y, but clamp so the entire thumbnail
    // stays above the cursor (bottom edge ≤ mouse Y - gap).
    let top = my - thumbHeight - gap
    top = Math.max(8, top)

    this.thumbnailEl.style.left = `${left}px`
    this.thumbnailEl.style.top = `${top}px`
    this.thumbnailEl.style.bottom = 'auto'
    this.thumbnailEl.style.display = 'block'

    // Skip expensive thumbnail render if progress hasn't changed meaningfully
    if (Math.abs(progress - this._lastHoverProgress) < 0.005) return
    this._lastHoverProgress = progress
    this.callbacks?.onProgressHover(progress, mouseX, mouseY ?? 0)
  }

  private _hideThumbnail(): void {
    this.thumbnailEl.style.display = 'none'
  }

  /** Reset the auto-hide timer (3 seconds of no mouse movement). */
  private _resetHideTimer(): void {
    if (this._isEnded || this._isPaused) return
    this._clearHideTimer()
    this.el.classList.remove('rc-fading')
    this._hideTimer = setTimeout(() => {
      this.el.classList.add('rc-fading')
    }, AUTO_HIDE_DELAY)
  }

  private _clearHideTimer(): void {
    if (this._hideTimer !== null) {
      clearTimeout(this._hideTimer)
      this._hideTimer = null
    }
  }

  get isPaused(): boolean {
    return this._isPaused
  }

  get currentSpeed(): number {
    return this._currentSpeed
  }

  get isActive(): boolean {
    return this._isActive
  }
}

function formatTime(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return `${min}:${String(sec).padStart(2, '0')}`
}
