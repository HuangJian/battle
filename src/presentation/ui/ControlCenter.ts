import type { World } from '../../game/World'
import { THEME_DEFINITIONS } from '../../config/theme'
import { t, localizeRoot } from '../../i18n'

// ================================================================
// Control Center (plan §13)
//
// The left sidebar is the unified access point for meta-game modules:
//
//   Control Center
//   ├── Snapshot Manager   (manual save · snapshot browser · counts)
//   ├── Replays            (replay browser · counts)
//   ├── Gameplay           (key bindings · current run info)
//   ├── Display            (fullscreen · performance mode)
//   ├── Developer          (debug overlay)
//   └── Reserved           (mods / statistics)
//
// Read-only on the World; every action is a callback into Game.
// ================================================================

export interface ControlCenterCallbacks {
  onManualSave: () => void
  onOpenBrowser: () => void
  /** Open the Replay Browser. */
  onOpenReplays: () => void
  /** Open a local .replay file (not imported to database). */
  onOpenLocalReplay?: () => void
  onOpenControls: () => void
  /** Pause the game/replay so the theme dropdown can be shown (idempotent). */
  onThemePause: () => void
  /** Cycle to the next theme (Alt+T): pauses then advances (idempotent pause). */
  onThemeCycle: () => void
  /** Switch to a specific theme by config key (dropdown pick): pauses then sets. */
  onSelectTheme: (key: string) => void
  /** Toggle the developer Performance Observatory overlay (Alt+D). */
  onTogglePerf: () => void
  /** Toggle fullscreen mode (Alt+F). */
  onToggleFullscreen: () => void
  /** Toggle Performance Mode (DPR cap + render-FPS cap). */
  onTogglePerformance: () => void
  /** Toggle Lie-Back-Win-Mode (coop with God AI). */
  onToggleCoop: () => void
  /** Snapshot counts for the status line. */
  getCounts: () => { total: number; manual: number; manualLimit: number }
  /** Replay counts for the status line. */
  getReplayCounts: () => { total: number; favorites: number }
  /** Check if the game is currently playing (for auto-pause). */
  isPlaying: () => boolean
  /** Pause the game. */
  onPause: () => void
}

export class ControlCenter {
  readonly el: HTMLElement
  private callbacks: ControlCenterCallbacks | null = null
  private countLine: HTMLElement
  private replayCountLine: HTMLElement
  private gameplayInfo: HTMLElement
  private collapsed = false
  /** Save button — disabled when not in a gameplay state. */
  private saveBtnEl: HTMLButtonElement | null = null
  private perfBtn: HTMLButtonElement | null = null
  private perfState: HTMLElement | null = null
  private fullscreenBtn: HTMLButtonElement | null = null
  private fullscreenState: HTMLElement | null = null
  private perfModeBtn: HTMLButtonElement | null = null
  private perfModeState: HTMLElement | null = null
  private coopBtn: HTMLButtonElement | null = null
  private coopState: HTMLElement | null = null

  // Theme switcher (GAMEPLAY) — button label + dropdown
  private themeBtnEl: HTMLButtonElement | null = null
  private themeNameEl: HTMLElement | null = null
  private themeDropdownEl: HTMLElement | null = null
  private themeOptionEls: HTMLElement[] = []
  private themeDropdownOpen = false
  /** key → display name, for the live label. */
  private themeNames = new Map<string, string>()
  private lastThemeName = ''

  // Cached last-written values (avoid per-frame DOM churn)
  private lastCounts = ''
  private lastReplayCounts = ''
  private lastGameplay = ''

  constructor() {
    this.el = document.createElement('aside')
    this.el.className = 'control-center'
    this.el.innerHTML = `
      <div class="cc-header">
        <span class="cc-title" data-i18n="cc.title">CONTROL CENTER</span>
        <button class="cc-collapse" data-cc="collapse" type="button" title="Collapse" data-i18n-attr="title:cc.titleCollapse">◀</button>
      </div>
      <div class="cc-body" data-cc="body">
        <section class="cc-section">
          <h3 class="cc-section-title" data-i18n="cc.section.snapshots">SNAPSHOT MANAGER</h3>
          <button class="cc-btn" data-cc="save" type="button">
            <span data-i18n="cc.save">Save Snapshot Now</span><kbd>Alt+S</kbd>
          </button>
          <button class="cc-btn" data-cc="browser" type="button">
            <span data-i18n="cc.snapshotBrowser">Snapshot Browser</span><span class="cc-btn-arrow">›</span>
          </button>
          <div class="cc-info" data-cc="counts" data-i18n="cc.noSnapshots">No snapshots</div>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title" data-i18n="cc.section.replays">REPLAYS</h3>
          <button class="cc-btn" data-cc="replays" type="button">
            <span data-i18n="cc.replayBrowser">Replay Browser</span><span class="cc-btn-arrow">›</span>
          </button>
          <button class="cc-btn" data-cc="local-replay" type="button">
            <span data-i18n="cc.openLocalReplay">Open Local Replay</span><span class="cc-btn-arrow">›</span>
          </button>
          <div class="cc-info" data-cc="replay-counts" data-i18n="cc.noReplays">No replays</div>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title" data-i18n="cc.section.gameplay">GAMEPLAY</h3>
          <div class="cc-theme-wrap">
            <button class="cc-btn" data-cc="theme" type="button" aria-pressed="false" title="Switch theme (Alt+T) — click to pick" data-i18n-attr="title:cc.titleTheme">
              <span class="cc-theme-label"><span data-i18n="cc.theme">Theme</span>: <span data-cc="theme-name">—</span></span>
              <kbd>Alt+T</kbd>
            </button>
            <div class="cc-theme-dropdown" data-cc="theme-dropdown" hidden></div>
          </div>
          <button class="cc-btn" data-cc="controls" type="button">
            <span data-i18n="cc.keyBindings">Key Bindings</span><span class="cc-btn-arrow">›</span>
          </button>
          <button class="cc-btn" data-cc="coop" type="button" aria-pressed="false" title="Toggle Lie-Back-Win-Mode (God AI co-op)" data-i18n-attr="title:cc.titleCoop">
            <span data-i18n="cc.lieBackWin">Lie-Back Win</span>
            <span class="cc-perf-meta"><span class="cc-perf-state" data-cc="coop-state">OFF</span></span>
          </button>
          <div class="cc-info" data-cc="gameplay">—</div>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title" data-i18n="cc.section.display">DISPLAY</h3>
          <button class="cc-btn" data-cc="fullscreen" type="button" aria-pressed="false" title="Toggle fullscreen mode (Alt+F)" data-i18n-attr="title:cc.titleFullscreen">
            <span data-i18n="cc.fullscreen">Fullscreen</span>
            <span class="cc-perf-meta"><kbd>Alt+F</kbd><span class="cc-perf-state" data-cc="fullscreen-state">OFF</span></span>
          </button>
          <button class="cc-btn" data-cc="perfmode" type="button" aria-pressed="false" title="Toggle Performance Mode (DPR cap + render FPS cap)" data-i18n-attr="title:cc.titlePerfMode">
            <span data-i18n="cc.perfMode">Performance Mode</span>
            <span class="cc-perf-meta"><span class="cc-perf-state" data-cc="perfmode-state">OFF</span></span>
          </button>
        </section>
        <section class="cc-section">
          <h3 class="cc-section-title" data-i18n="cc.section.developer">DEVELOPER</h3>
          <button class="cc-btn" data-cc="perf" type="button" aria-pressed="false" title="Toggle the Performance Observatory debug HUD" data-i18n-attr="title:cc.titleDebug">
            <span data-i18n="cc.debugOverlay">Debug Overlay</span>
            <span class="cc-perf-meta"><kbd>Alt+D</kbd><span class="cc-perf-state" data-cc="perf-state">OFF</span></span>
          </button>
        </section>
        <section class="cc-section cc-reserved">
          <h3 class="cc-section-title" data-i18n="cc.section.reserved">RESERVED</h3>
          <div class="cc-btn cc-btn-disabled"><span data-i18n="cc.mods">Mods</span><span class="cc-soon" data-i18n="cc.soon">SOON</span></div>
          <div class="cc-btn cc-btn-disabled"><span data-i18n="cc.statistics">Statistics</span><span class="cc-soon" data-i18n="cc.soon">SOON</span></div>
        </section>
      </div>
    `

    this.countLine = this.el.querySelector('[data-cc="counts"]')!
    this.replayCountLine = this.el.querySelector('[data-cc="replay-counts"]')!
    this.gameplayInfo = this.el.querySelector('[data-cc="gameplay"]')!

    const wire = (sel: string, fn: () => void, autoPause = false) => {
      const btn = this.el.querySelector(sel) as HTMLButtonElement
      btn.addEventListener('click', () => {
        // Blur so Space/Enter can't re-trigger the button during gameplay.
        btn.blur()
        // Auto-pause when clicking CC buttons during active gameplay.
        if (autoPause && this.callbacks?.isPlaying()) {
          this.callbacks.onPause()
        }
        fn()
      })
    }
    this.saveBtnEl = this.el.querySelector('[data-cc="save"]') as HTMLButtonElement
    wire('[data-cc="save"]', () => this.callbacks?.onManualSave())
    wire('[data-cc="browser"]', () => this.callbacks?.onOpenBrowser(), true)
    wire('[data-cc="replays"]', () => this.callbacks?.onOpenReplays(), true)
    wire('[data-cc="local-replay"]', () => this.callbacks?.onOpenLocalReplay?.(), true)
    wire('[data-cc="controls"]', () => this.callbacks?.onOpenControls(), true)
    wire('[data-cc="perf"]', () => this.callbacks?.onTogglePerf())
    wire('[data-cc="fullscreen"]', () => this.callbacks?.onToggleFullscreen())
    wire('[data-cc="perfmode"]', () => this.callbacks?.onTogglePerformance())
    wire('[data-cc="coop"]', () => this.callbacks?.onToggleCoop())

    this.perfBtn = this.el.querySelector('[data-cc="perf"]') as HTMLButtonElement
    this.perfState = this.el.querySelector('[data-cc="perf-state"]')
    this.fullscreenBtn = this.el.querySelector('[data-cc="fullscreen"]') as HTMLButtonElement
    this.fullscreenState = this.el.querySelector('[data-cc="fullscreen-state"]')
    this.perfModeBtn = this.el.querySelector('[data-cc="perfmode"]') as HTMLButtonElement
    this.perfModeState = this.el.querySelector('[data-cc="perfmode-state"]')
    this.coopBtn = this.el.querySelector('[data-cc="coop"]') as HTMLButtonElement
    this.coopState = this.el.querySelector('[data-cc="coop-state"]')

    // Theme switcher — build the dropdown list and wire the button.
    this.themeBtnEl = this.el.querySelector('[data-cc="theme"]') as HTMLButtonElement
    this.themeNameEl = this.el.querySelector('[data-cc="theme-name"]')
    this.themeDropdownEl = this.el.querySelector('[data-cc="theme-dropdown"]')
    for (const def of THEME_DEFINITIONS) {
      this.themeNames.set(def.key, def.name)
      const opt = document.createElement('div')
      opt.className = 'cc-theme-option'
      opt.dataset.themeKey = def.key
      opt.innerHTML = `<span class="cc-theme-swatch" style="background:${def.colors.accentPrimary}"></span><span class="cc-theme-name">${t(`theme.${def.key}`)}</span>`
      opt.addEventListener('click', (e) => {
        e.stopPropagation()
        this.callbacks?.onSelectTheme(def.key)
        this.closeThemeDropdown()
      })
      this.themeDropdownEl?.appendChild(opt)
      this.themeOptionEls.push(opt)
    }
    this.themeBtnEl?.addEventListener('click', () => {
      this.themeBtnEl?.blur()
      // Pause the game/replay on open (no-op if already paused / in menu).
      if (!this.themeDropdownOpen) {
        this.callbacks?.onThemePause()
      }
      this.toggleThemeDropdown()
    })
    // Close the dropdown when clicking anywhere outside the theme control.
    document.addEventListener('click', (e) => {
      if (!this.themeDropdownOpen) return
      const t = e.target as Node
      if (this.themeBtnEl?.contains(t) || this.themeDropdownEl?.contains(t)) return
      this.closeThemeDropdown()
    })

    const collapseBtn = this.el.querySelector('[data-cc="collapse"]') as HTMLButtonElement
    collapseBtn.addEventListener('click', () => {
      collapseBtn.blur()
      this.collapsed = !this.collapsed
      this.el.classList.toggle('collapsed', this.collapsed)
      collapseBtn.textContent = this.collapsed ? '▶' : '◀'
    })

    // Localize this panel's static strings (incl. the data-i18n-attr tooltips)
    // at construction, so they're correct even before UIManager.refreshText().
    localizeRoot(this.el)
  }

  init(callbacks: ControlCenterCallbacks): void {
    this.callbacks = callbacks
  }

  /** Reflect the Performance Observatory overlay's on/off state in the
   *  DEVELOPER panel button (highlighted + ON/OFF label). Keeps the Control
   *  Center in sync whether the overlay was toggled here or via the Alt+D key. */
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

  /** Reflect Performance Mode state in the DISPLAY panel button. */
  setPerfModeState(on: boolean): void {
    if (this.perfModeBtn) {
      this.perfModeBtn.classList.toggle('selected', on)
      this.perfModeBtn.setAttribute('aria-pressed', String(on))
    }
    if (this.perfModeState) {
      this.perfModeState.textContent = on ? 'ON' : 'OFF'
      this.perfModeState.classList.toggle('on', on)
    }
  }

  /** Reflect coop (Lie-Back-Win) state in the GAMEPLAY panel button. */
  setCoopState(on: boolean): void {
    if (this.coopBtn) {
      this.coopBtn.classList.toggle('selected', on)
      this.coopBtn.setAttribute('aria-pressed', String(on))
    }
    if (this.coopState) {
      this.coopState.textContent = on ? 'ON' : 'OFF'
      this.coopState.classList.toggle('on', on)
    }
  }

  /** Toggle the theme dropdown open/closed (and the button's aria state). */
  private toggleThemeDropdown(): void {
    this.themeDropdownOpen = !this.themeDropdownOpen
    this.themeDropdownEl?.classList.toggle('open', this.themeDropdownOpen)
    this.themeDropdownEl?.toggleAttribute('hidden', !this.themeDropdownOpen)
    this.themeBtnEl?.setAttribute('aria-pressed', String(this.themeDropdownOpen))
  }

  /** Close the theme dropdown (e.g. after a selection or an outside click). */
  private closeThemeDropdown(): void {
    if (!this.themeDropdownOpen) return
    this.themeDropdownOpen = false
    this.themeDropdownEl?.classList.remove('open')
    this.themeDropdownEl?.setAttribute('hidden', '')
    this.themeBtnEl?.setAttribute('aria-pressed', 'false')
  }

  /**
   * Refresh status lines from the World (cheap, change-guarded).
   * Also auto-pauses the game when any CC button is clicked during play,
   * and disables the Save button when not in a gameplay state.
   */
  update(world: World): void {
    if (!this.callbacks) return

    // Enable / disable the save button based on game state.
    const inPlay = world.state === 'playing' || world.state === 'paused'
    if (this.saveBtnEl) {
      this.saveBtnEl.disabled = !inPlay
    }

    const c = this.callbacks.getCounts()
    const countsText =
      c.total === 0
        ? t('cc.noSnapshots')
        : t('cc.counts.fmt', { total: c.total, manual: c.manual, limit: c.manualLimit })
    if (countsText !== this.lastCounts) {
      this.lastCounts = countsText
      this.countLine.textContent = countsText
    }

    const rc = this.callbacks.getReplayCounts()
    const replayText =
      rc.total === 0
        ? t('cc.noReplays')
        : rc.favorites > 0
          ? t('cc.replays.fmtFav', { total: rc.total, fav: rc.favorites })
          : t('cc.replays.fmt', { total: rc.total })
    if (replayText !== this.lastReplayCounts) {
      this.lastReplayCounts = replayText
      this.replayCountLine.textContent = replayText
    }

    const diffName = t(`difficulty.${world.difficultyKey}`) || world.difficulty.name
    const inRun = world.state !== 'menu' && world.state !== 'victory'
    const gameplayText = inRun
      ? t('cc.gameplay.inRun', {
          difficulty: diffName,
          n: String(world.stageIndex + 1).padStart(2, '0'),
          name: world.currentStageName,
        })
      : t('cc.gameplay.menu', {
          difficulty: diffName,
          theme: t(`theme.${world.themeKey}`) || world.themeKey,
        })
    if (gameplayText !== this.lastGameplay) {
      this.lastGameplay = gameplayText
      this.gameplayInfo.textContent = gameplayText
    }

    // Theme switcher label + dropdown highlight — only when the theme changes.
    const themeName = t(`theme.${world.themeKey}`) || this.themeNames.get(world.themeKey) || world.themeKey
    if (themeName !== this.lastThemeName) {
      this.lastThemeName = themeName
      if (this.themeNameEl) this.themeNameEl.textContent = themeName
      for (const opt of this.themeOptionEls) {
        opt.classList.toggle('selected', opt.dataset.themeKey === world.themeKey)
      }
    }
  }
}
