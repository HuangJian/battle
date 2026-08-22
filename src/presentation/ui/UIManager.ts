import type { World } from '../../game/World'
import type { ThemeColors, KeyBindings } from '../../types'
import { localizeRoot } from '../../i18n'
import { i18n, t } from '../../i18n'

import { SnapshotBrowser } from './SnapshotBrowser'
import { ReplayBrowser } from './ReplayBrowser'
import { ControlCenter } from './ControlCenter'
import { PerfOverlay } from './PerfOverlay'
import { ReplayController } from './ReplayController'
import { HudView } from './HudView'
import { MenuScreen, type MenuActions } from './MenuScreen'
import { ControlsPanel } from './ControlsPanel'
import { OverlayManager } from './OverlayManager'

export type { MenuActions } from './MenuScreen'

/**
 * UIManager — orchestrates all HTML/CSS UI overlay elements (§2.4).
 * The per-concern work lives in four composed sub-controllers:
 *
 * - {@link HudView}       — HUD bar + per-frame world sync
 * - {@link MenuScreen}    — start menu rendering & interaction
 * - {@link ControlsPanel} — key-binding modal
 * - {@link OverlayManager}— pause / game over / stage clear / victory / recovery
 *
 * plus the already-extracted SnapshotBrowser / ReplayBrowser / ControlCenter /
 * PerfOverlay / ReplayController components. What remains here is assembly,
 * the shared frame orchestration (`update` → sync slices → `showScreen`),
 * theme CSS-variable propagation, i18n re-localization bridging, and toasts.
 * Never modifies the World.
 */
export class UIManager {
  private root: HTMLElement
  canvas: HTMLCanvasElement
  private overlay: HTMLElement
  private footer: HTMLElement

  // ---- Composed UI slices ----
  private hud: HudView
  private menu: MenuScreen
  private controls: ControlsPanel
  private overlays: OverlayManager

  readonly replayController: ReplayController
  readonly snapshotBrowser: SnapshotBrowser
  readonly replayBrowser: ReplayBrowser
  readonly controlCenter: ControlCenter
  readonly perfOverlay: PerfOverlay

  /** Callback for the Take Over button (set by Game). */
  onSpectateTakeover: (() => void) | null = null
  /** Callback for the Take Over button while a replay is active (set by GameReplay). */
  onReplayTakeover: (() => void) | null = null

  private toastEl: HTMLElement
  private toastTimer = 0

  private lastThemeKey = ''

  // Start as a sentinel so the first showScreen('menu') in the constructor
  // actually applies (the guard bails when screen === currentScreen). If this
  // were pre-set to 'menu', the initial menu would never receive the
  // 'active'/'visible' classes and would render invisible on first load.
  private currentScreen = ''

  constructor(root: HTMLElement) {
    this.root = root
    this.root.className = 'game-wrapper'

    // Game container (canvas + overlay)
    const gameContainer = document.createElement('div')
    gameContainer.className = 'game-container'

    // Canvas (created by UIManager, managed by PresentationLayer)
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'game-canvas'
    // Make the canvas focusable so the game can programmatically reclaim
    // keyboard focus (e.g. after a stage transition or when returning from a
    // browser overlay) instead of requiring a manual click. See Game.refocusGame.
    this.canvas.tabIndex = 0
    gameContainer.appendChild(this.canvas)

    // Overlay (covers canvas)
    this.overlay = this.createElement('div', 'ui-overlay')

    // HUD bar (above canvas) — Take Over routes to replay or spectate
    // depending on which mode is active at click time.
    this.hud = new HudView(this.createElement.bind(this), () => {
      if (this.replayController.isActive) {
        this.onReplayTakeover?.()
      } else {
        this.onSpectateTakeover?.()
      }
    })

    // Menu screen (start screen)
    this.menu = new MenuScreen(this.createElement.bind(this), () => this.openControls())

    // Game-state overlay screens
    this.overlays = new OverlayManager(this.createElement.bind(this))

    this.overlay.appendChild(this.menu.el)
    this.overlay.appendChild(this.overlays.pauseScreen)
    this.overlay.appendChild(this.overlays.gameOverScreen)
    this.overlay.appendChild(this.overlays.stageClearScreen)
    this.overlay.appendChild(this.overlays.victoryScreen)
    this.overlay.appendChild(this.overlays.recoveryScreen)

    // Controls / key-bindings modal
    this.controls = new ControlsPanel(this.createElement.bind(this))
    this.controls.onSuperLabelsChanged = () => this.updateSuperKeyLabels()
    this.overlay.appendChild(this.controls.el)

    // Snapshot Browser (plan §12) — full-overlay modal screen
    this.snapshotBrowser = new SnapshotBrowser()
    this.overlay.appendChild(this.snapshotBrowser.screen)

    // Replay Browser (plan/replay.md) — full-overlay modal screen
    this.replayBrowser = new ReplayBrowser()
    this.overlay.appendChild(this.replayBrowser.screen)

    // Control Center sidebar (plan §13)
    this.controlCenter = new ControlCenter()
    this.root.appendChild(this.controlCenter.el)

    // Toast notifications (manual save confirmation / capacity warnings)
    this.toastEl = this.createElement('div', 'ui-toast')
    this.root.appendChild(this.toastEl)

    // Footer
    this.footer = this.createElement('div', 'footer')
    this.footer.innerHTML = `
      <span>P</span> <span data-i18n="footer.pause">Pause</span> &nbsp;·&nbsp;
      <span>Alt+R</span> <span data-i18n="footer.reset">Reset</span> &nbsp;·&nbsp;
      <span>Alt+S</span> <span data-i18n="footer.save">Save</span> &nbsp;·&nbsp;
      <span>Alt+&lt; Alt+&gt;</span> <span data-i18n="footer.speed">Speed</span>
    `

    // Performance Observatory (Alt+D) — fixed-position dev overlay (read-only).
    this.perfOverlay = new PerfOverlay()
    this.perfOverlay.onCopied = () => this.notify(t('toast.perfCopied'), 'info')
    this.root.appendChild(this.perfOverlay.el)

    // Assemble
    this.root.appendChild(this.hud.el)
    gameContainer.appendChild(this.overlay)
    this.root.appendChild(gameContainer)
    this.root.appendChild(this.footer)

    // Replay Controller (video player style)
    this.replayController = new ReplayController()
    this.replayController.hide()
    this.root.appendChild(this.replayController.el)

    // Cache menu DOM elements (avoid querySelectorAll every frame)
    this.menu.cacheElements()

    this.showScreen('menu')

    // Re-localize the entire UI (including the persisted choice) on boot and
    // whenever the language changes at runtime.
    i18n.subscribe(() => this.refreshText())
    this.refreshText()
  }

  private createElement(tag: string, className: string): HTMLElement {
    const el = document.createElement(tag)
    el.className = className
    return el
  }

  /**
   * Register the menu action callbacks (mouse support). Called once from
   * Game so clicks on the start screen route through the same World-mutating
   * code paths as keyboard input. UIManager itself stays read-only.
   */
  initMenuActions(actions: MenuActions): void {
    this.menu.initMenuActions(actions)
  }

  /**
   * Tell the menu about the last manually-saved snapshot (called once after
   * boot hydration) so the RESUME row can be offered.
   */
  setResumeTarget(target: { stage: number; stageName: string; score: number } | null): void {
    this.menu.setResumeTarget(target)
  }

  /**
   * Re-localize every static string in the UI. Called once at boot and again
   * whenever the active locale changes (subscribed via `i18n.subscribe`).
   * Elements declare keys with `data-i18n`; labels built from live data are
   * refreshed explicitly by their owning slice.
   */
  refreshText(): void {
    // Localize everything under the app root, plus any overlays the
    // ReplayController appends directly to <body> (the end-of-replay card).
    localizeRoot(document.body)
    this.menu.refreshLocalized()
    // Super-item key labels (name + current binding) follow the locale.
    this.updateSuperKeyLabels()
  }

  /** Re-render the HUD super-item labels (bindings + locale changed). */
  private updateSuperKeyLabels(): void {
    this.hud.updateSuperKeyLabels(this.controls.currentBindings)
  }

  /** Apply theme colors as CSS variables — only when theme key changes */
  applyThemeIfChanged(colors: ThemeColors, themeKey: string): void {
    if (themeKey === this.lastThemeKey) return
    this.lastThemeKey = themeKey
    this.applyTheme(colors, themeKey)
  }

  /** Apply theme colors as CSS variables */
  private applyTheme(colors: ThemeColors, themeKey?: string): void {
    const root = document.documentElement
    // Toggle body class for theme-specific CSS overrides (e.g. .ui-menu
    // text colours on dark overlay for Modern Retro only).
    document.body.classList.toggle('theme-modern', themeKey === 'modern')
    const vars: Record<string, string> = {
      '--theme-bg': colors.bgGradient
        ? `linear-gradient(180deg, ${colors.bgGradient[0]}, ${colors.bgGradient[1]})`
        : colors.bg,
      '--theme-panel-bg': colors.panelBg,
      '--theme-panel-border': colors.panelBorder,
      '--theme-panel-shadow': colors.panelShadow,
      '--theme-text-primary': colors.textPrimary,
      '--theme-text-secondary': colors.textSecondary,
      '--theme-text-muted': colors.textMuted,
      '--theme-accent-primary': colors.accentPrimary,
      '--theme-accent-secondary': colors.accentSecondary,
      '--theme-button-bg': colors.buttonBg,
      '--theme-button-hover': colors.buttonHover,
      '--theme-button-active': colors.buttonActive,
      '--theme-overlay-bg': colors.overlayBg,
      '--theme-danger': colors.danger,
      '--theme-success': colors.success,
      '--theme-hud-accent': colors.hudAccent,
    }
    for (const [key, value] of Object.entries(vars)) {
      root.style.setProperty(key, value)
    }
  }

  /** Show a specific screen, hide others */
  showScreen(screen: string): void {
    if (screen === this.currentScreen) return
    this.currentScreen = screen

    const screens: Record<string, HTMLElement> = {
      menu: this.menu.el,
      ...this.overlays.screens,
    }

    for (const [name, el] of Object.entries(screens)) {
      if (name === screen) {
        el.classList.add('active')
      } else {
        el.classList.remove('active')
      }
    }

    // Close stage dropdown when leaving menu
    if (screen !== 'menu') {
      this.menu.closeStageDropdown()
    }

    // Show/hide HUD based on state
    this.hud.setVisible(!(screen === 'menu' || screen === 'victory'))

    // Pause indicator (HUD .paused badge + Take Over button) is toggled in
    // update() so it also covers replay pause, where world.state stays
    // 'playing' and the pause is owned by the ReplayController.

    // Footer hints (P Pause · Alt+R Reset · Alt+S Save) — only during gameplay,
    // not on menu / victory / recovery screens.
    this.footer.classList.toggle(
      'visible',
      screen === 'playing' ||
        screen === 'paused' ||
        screen === 'gameover' ||
        screen === 'stageclear',
    )

    // Reset recovery screen sub-state when leaving recovery
    if (screen !== 'recovery') {
      this.overlays.clearRecoverySubState()
    }

    // Show/hide overlay
    if (screen === 'playing') {
      this.overlay.classList.remove('visible')
    } else {
      this.overlay.classList.add('visible')
    }
  }

  /** Update HUD and menu display from world state */
  update(world: World): void {
    // Control Center sidebar — always live (change-guarded internally).
    this.controlCenter.update(world)

    // While the controls panel is open it owns the overlay (manually
    // toggled), so skip the per-frame screen sync that would otherwise
    // re-activate the menu behind it.
    if (this.controls.isOpen()) return

    // Same for the Snapshot Browser — it owns the overlay while open.
    if (this.snapshotBrowser.isOpen()) return

    // HUD bar sync (change-guarded internally).
    this.hud.syncWorld(world)

    // Menu state — only update when in menu
    if (world.state === 'menu') {
      this.menu.syncWorld(world)
    }

    // Stage clear / victory / recovery overlay text (each guarded internally).
    this.overlays.syncWorld(world)

    // During replay playback (and after it ends) the world can reach a
    // terminal state (gameover / victory / stageclear) by replaying the
    // original run. We must NOT surface the normal GAME OVER / VICTORY
    // popups — the replay's own centered end overlay is the canonical
    // end-of-replay UI. Keep the battlefield visible behind it.
    let screen = world.state
    if (
      this.replayController.isActive &&
      (screen === 'gameover' || screen === 'victory' || screen === 'stageclear')
    ) {
      screen = 'playing'
    }
    this.showScreen(screen)

    // Take Over entry point lives on the HUD for BOTH 督战 (spectate) and
    // replay, so the two modes share one consistent control. A spectate pause
    // flips world.state to 'paused'; a replay pause is owned by the
    // ReplayController (world.state stays 'playing'), so check both.
    const replayPaused = this.replayController.isActive && this.replayController.isPaused
    this.hud.setPauseState(
      screen === 'paused' || replayPaused,
      (world.spectate && screen === 'paused') || replayPaused,
    )
  }

  /** Show or hide the persistent REPLAY indicator in the HUD center area. */
  setReplayMode(isReplay: boolean, isPaused = false, difficulty?: string): void {
    this.hud.setReplayBadge(isReplay, difficulty)
    if (isReplay) {
      this.replayController.show()
      this.replayController.setPaused(isPaused)
    } else {
      this.replayController.hide()
    }
  }

  /** Update the replay progress bar (0 → 1). */
  setReplayProgress(progress: number): void {
    this.replayController.updateProgress(progress)
  }

  /** Update the replay time display. */
  setReplayTime(currentMs: number, totalMs: number): void {
    this.replayController.updateTime(currentMs, totalMs)
  }

  /** Update the replay speed display. */
  setReplaySpeed(speed: number): void {
    this.replayController.setSpeed(speed)
  }

  /** Show the live battle-speed chip (hidden at ×1). */
  setBattleSpeed(speed: number): void {
    this.hud.setBattleSpeed(speed)
  }

  /** Toggle the developer Performance Observatory overlay (Alt+D hotkey / Control
   *  Center button). Keeps the Control Center's DEVELOPER button in sync. */
  togglePerfOverlay(): void {
    this.perfOverlay.toggle()
    this.controlCenter.setPerfState(this.perfOverlay.active)
  }

  // ---- Toast notifications ----

  /** Show a transient toast (manual save confirmations, capacity warnings). */
  notify(message: string, kind: 'info' | 'warn' = 'info'): void {
    this.toastEl.textContent = message
    this.toastEl.className = `ui-toast visible ui-toast-${kind}`
    window.clearTimeout(this.toastTimer)
    this.toastTimer = window.setTimeout(() => {
      this.toastEl.classList.remove('visible')
    }, 2600)
  }

  /** Get the footer element */
  getFooter(): HTMLElement {
    return this.footer
  }

  // ---- Controls / Key Bindings Panel (delegates) ----

  /**
   * Wire the live key-bindings object (the same reference the Input system
   * reads) and a persistence callback. Called once from Game after the
   * PresentationLayer is constructed.
   */
  initControls(bindings: KeyBindings, onChanged: () => void): void {
    this.controls.initControls(bindings, onChanged)
  }

  /** Whether the controls panel is currently open (a UI-modal, not a world state). */
  isControlsOpen(): boolean {
    return this.controls.isOpen()
  }

  /** Expose layout elements so PresentationLayer can measure reserved vertical
   *  space when sizing the canvas. Read-only access only. */
  get hudBarEl(): HTMLElement {
    return this.hud.el
  }
  get footerEl(): HTMLElement {
    return this.footer
  }

  /** Open the controls panel (modal overlay — see ControlsPanel.open). */
  openControls(): void {
    this.controls.open()
  }

  /** Close the controls panel (see ControlsPanel.close). */
  closeControls(): void {
    this.controls.close()
  }

  /**
   * Publish per-option availability for the recovery menu. Called by Game
   * when entering the recovery flow (Game asks the RecoveryController —
   * UIManager itself never inspects snapshots).
   */
  setRecoveryAvailability(availability: boolean[]): void {
    this.overlays.setRecoveryAvailability(availability)
  }
}
