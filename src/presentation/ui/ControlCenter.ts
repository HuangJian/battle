import type { World } from '../../game/World'

// ================================================================
// Control Center (plan §13)
//
// The left sidebar is the unified access point for meta-game modules:
//
//   Control Center
//   ├── Snapshot Manager   (manual save · snapshot browser · counts)
//   ├── Controls           (key bindings)
//   ├── Gameplay           (current run info)
//   └── Reserved           (themes / accessibility / mods / statistics)
//
// Read-only on the World; every action is a callback into Game.
// ================================================================

export interface ControlCenterCallbacks {
  onManualSave: () => void
  onOpenBrowser: () => void
  /** Open the Replay Browser. */
  onOpenReplays: () => void
  onOpenControls: () => void
  /** Toggle the developer Performance Observatory overlay (F6). */
  onTogglePerf: () => void
  /** Toggle fullscreen mode (Alt+F). */
  onToggleFullscreen: () => void
  /** Snapshot counts for the status line. */
  getCounts: () => { total: number; manual: number; manualLimit: number }
  /** Replay counts for the status line. */
  getReplayCounts: () => { total: number; favorites: number }
}

export class ControlCenter {
  readonly el: HTMLElement
  private callbacks: ControlCenterCallbacks | null = null
  private countLine: HTMLElement
  private replayCountLine: HTMLElement
  private gameplayInfo: HTMLElement
  private collapsed = false
  private perfBtn: HTMLButtonElement | null = null
  private perfState: HTMLElement | null = null
  private fullscreenBtn: HTMLButtonElement | null = null
  private fullscreenState: HTMLElement | null = null

  // Cached last-written values (avoid per-frame DOM churn)
  private lastCounts = ''
  private lastReplayCounts = ''
  private lastGameplay = ''

  constructor() {
    this.el = document.createElement('aside')
    this.el.className = 'control-center'
    this.el.innerHTML = `
      <div class="cc-header">
        <span class="cc-title">CONTROL CENTER</span>
        <button class="cc-collapse" data-cc="collapse" type="button" title="Collapse">◀</button>
      </div>
      <div class="cc-body" data-cc="body">
        <section class="cc-section">
          <h3 class="cc-section-title">SNAPSHOT MANAGER</h3>
          <button class="cc-btn" data-cc="save" type="button">
            <span>Manual Save</span><kbd>Alt+S</kbd>
          </button>
          <button class="cc-btn" data-cc="browser" type="button">
            <span>Snapshot Browser</span><span class="cc-btn-arrow">›</span>
          </button>
          <div class="cc-info" data-cc="counts">No snapshots</div>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title">REPLAYS</h3>
          <button class="cc-btn" data-cc="replays" type="button">
            <span>Replay Browser</span><span class="cc-btn-arrow">›</span>
          </button>
          <div class="cc-info" data-cc="replay-counts">No replays</div>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title">CONTROLS</h3>
          <button class="cc-btn" data-cc="controls" type="button">
            <span>Key Bindings</span><span class="cc-btn-arrow">›</span>
          </button>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title">GAMEPLAY</h3>
          <div class="cc-info" data-cc="gameplay">—</div>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title">DISPLAY</h3>
          <button class="cc-btn" data-cc="fullscreen" type="button" aria-pressed="false" title="Toggle fullscreen mode (Alt+F)">
            <span>Fullscreen</span>
            <span class="cc-perf-meta"><kbd>Alt+F</kbd><span class="cc-perf-state" data-cc="fullscreen-state">OFF</span></span>
          </button>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title">DEVELOPER</h3>
          <button class="cc-btn" data-cc="perf" type="button" aria-pressed="false" title="Toggle the Performance Observatory debug HUD">
            <span>Debug Overlay</span>
            <span class="cc-perf-meta"><kbd>F6</kbd><span class="cc-perf-state" data-cc="perf-state">OFF</span></span>
          </button>
        </section>
        <section class="cc-section cc-reserved">
          <h3 class="cc-section-title">RESERVED</h3>
          <div class="cc-btn cc-btn-disabled"><span>Themes</span><span class="cc-soon">SOON</span></div>
          <div class="cc-btn cc-btn-disabled"><span>Accessibility</span><span class="cc-soon">SOON</span></div>
          <div class="cc-btn cc-btn-disabled"><span>Mods</span><span class="cc-soon">SOON</span></div>
          <div class="cc-btn cc-btn-disabled"><span>Statistics</span><span class="cc-soon">SOON</span></div>
        </section>
      </div>
    `

    this.countLine = this.el.querySelector('[data-cc="counts"]')!
    this.replayCountLine = this.el.querySelector('[data-cc="replay-counts"]')!
    this.gameplayInfo = this.el.querySelector('[data-cc="gameplay"]')!

    const wire = (sel: string, fn: () => void) => {
      const btn = this.el.querySelector(sel) as HTMLButtonElement
      btn.addEventListener('click', () => {
        // Blur so Space/Enter can't re-trigger the button during gameplay.
        btn.blur()
        fn()
      })
    }
    wire('[data-cc="save"]', () => this.callbacks?.onManualSave())
    wire('[data-cc="browser"]', () => this.callbacks?.onOpenBrowser())
    wire('[data-cc="replays"]', () => this.callbacks?.onOpenReplays())
    wire('[data-cc="controls"]', () => this.callbacks?.onOpenControls())
    wire('[data-cc="perf"]', () => this.callbacks?.onTogglePerf())
    wire('[data-cc="fullscreen"]', () => this.callbacks?.onToggleFullscreen())

    this.perfBtn = this.el.querySelector('[data-cc="perf"]') as HTMLButtonElement
    this.perfState = this.el.querySelector('[data-cc="perf-state"]')
    this.fullscreenBtn = this.el.querySelector('[data-cc="fullscreen"]') as HTMLButtonElement
    this.fullscreenState = this.el.querySelector('[data-cc="fullscreen-state"]')

    const collapseBtn = this.el.querySelector('[data-cc="collapse"]') as HTMLButtonElement
    collapseBtn.addEventListener('click', () => {
      collapseBtn.blur()
      this.collapsed = !this.collapsed
      this.el.classList.toggle('collapsed', this.collapsed)
      collapseBtn.textContent = this.collapsed ? '▶' : '◀'
    })
  }

  init(callbacks: ControlCenterCallbacks): void {
    this.callbacks = callbacks
  }

  /** Reflect the Performance Observatory overlay's on/off state in the
   *  DEVELOPER panel button (highlighted + ON/OFF label). Keeps the Control
   *  Center in sync whether the overlay was toggled here or via the F6 key. */
  setPerfState(on: boolean): void {
    if (this.perfBtn) {
      this.perfBtn.classList.toggle('selected', on)
      this.perfBtn.setAttribute('aria-pressed', String(on))
    }
    if (this.perfState) {
      this.perfState.textContent = on ? 'ON' : 'OFF'
      this.perfState.classList.toggle('on', on)
    }
  }

  /** Reflect fullscreen state in the DISPLAY panel button. */
  setFullscreenState(on: boolean): void {
    if (this.fullscreenBtn) {
      this.fullscreenBtn.classList.toggle('selected', on)
      this.fullscreenBtn.setAttribute('aria-pressed', String(on))
    }
    if (this.fullscreenState) {
      this.fullscreenState.textContent = on ? 'ON' : 'OFF'
      this.fullscreenState.classList.toggle('on', on)
    }
  }

  /** Refresh status lines from the World (cheap, change-guarded). */
  update(world: World): void {
    if (!this.callbacks) return

    const c = this.callbacks.getCounts()
    const countsText =
      c.total === 0
        ? 'No snapshots'
        : `${c.total} snapshot${c.total === 1 ? '' : 's'} · manual ${c.manual}/${c.manualLimit}`
    if (countsText !== this.lastCounts) {
      this.lastCounts = countsText
      this.countLine.textContent = countsText
    }

    const rc = this.callbacks.getReplayCounts()
    const replayText =
      rc.total === 0
        ? 'No replays'
        : rc.favorites > 0
          ? `${rc.total} replay${rc.total === 1 ? '' : 's'} · ★ ${rc.favorites}`
          : `${rc.total} replay${rc.total === 1 ? '' : 's'}`
    if (replayText !== this.lastReplayCounts) {
      this.lastReplayCounts = replayText
      this.replayCountLine.textContent = replayText
    }

    const inRun = world.state !== 'menu' && world.state !== 'victory'
    const gameplayText = inRun
      ? `${world.difficulty.name} · Stage ${String(world.stageIndex + 1).padStart(2, '0')} · ${world.currentStageName}`
      : `${world.difficulty.name} · ${world.themeKey}`
    if (gameplayText !== this.lastGameplay) {
      this.lastGameplay = gameplayText
      this.gameplayInfo.textContent = gameplayText
    }
  }
}
