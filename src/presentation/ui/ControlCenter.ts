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
  onOpenControls: () => void
  /** Snapshot counts for the status line. */
  getCounts: () => { total: number; manual: number; manualLimit: number }
}

export class ControlCenter {
  readonly el: HTMLElement
  private callbacks: ControlCenterCallbacks | null = null
  private countLine: HTMLElement
  private gameplayInfo: HTMLElement
  private collapsed = false

  // Cached last-written values (avoid per-frame DOM churn)
  private lastCounts = ''
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
            <span>Manual Save</span><kbd>Shift+S</kbd>
          </button>
          <button class="cc-btn" data-cc="browser" type="button">
            <span>Snapshot Browser</span><span class="cc-btn-arrow">›</span>
          </button>
          <div class="cc-info" data-cc="counts">No snapshots</div>
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
    wire('[data-cc="controls"]', () => this.callbacks?.onOpenControls())

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
