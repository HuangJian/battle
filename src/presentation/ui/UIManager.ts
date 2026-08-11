import type { World } from '../../game/World'
import type { ThemeColors, KeyBindings } from '../../types'
import { DEFAULT_KEYS, eventToBinding, isModifierCode, parseBinding } from '../../game/Input'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../../config/difficulty'
import { THEME_DEFINITIONS } from '../../config/theme'
import { STAGES, localizedStageName } from '../../config/stages'

import { SnapshotBrowser } from './SnapshotBrowser'
import { ReplayBrowser } from './ReplayBrowser'
import { ControlCenter } from './ControlCenter'
import { PerfOverlay } from './PerfOverlay'
import { ReplayController } from './ReplayController'
import { RECOVERY_OPTION_COUNT } from '../../snapshot/RecoveryController'
import { i18n, t, localizeRoot } from '../../i18n'

/**
 * Menu action callbacks registered by Game so mouse clicks on the start
 * screen can mutate World state through the same code paths as keyboard
 * input (UIManager itself stays read-only on the World).
 */
export interface MenuActions {
  /** Select a difficulty by its config key (e.g. 'easy'). */
  selectDifficulty(key: string): void
  /** Select a theme by its config key (e.g. 'default'). */
  selectTheme(key: string): void
  /** Select a language/locale by its code (e.g. 'en', 'zh'). */
  selectLanguage(code: string): void
  /** Step the stage selector by -1 (prev) or +1 (next). */
  cycleStage(dir: -1 | 1): void
  /** Select a specific stage by index. */
  selectStage(index: number): void
  /** Start the game with the current menu selections. */
  start(): void
  /** Resume from the last manually-saved snapshot (only offered when one exists). */
  resume(): void
  /** Open the controls / key-bindings panel. */
  openControls(): void
}

/**
 * UIManager — manages all HTML/CSS UI overlay elements.
 * Creates and updates DOM elements for menu, HUD, and game state overlays.
 * Never modifies the World.
 */
export class UIManager {
  private root: HTMLElement
  canvas: HTMLCanvasElement
  private hudBar: HTMLElement
  private hudScore: HTMLElement
  private hudScore2: HTMLElement | null = null
  private hudScore2Wrap: HTMLElement | null = null
  private hudLives: HTMLElement
  private hudLives2: HTMLElement | null = null
  private hudCoopLives: HTMLElement | null = null
  private hudStage: HTMLElement
  /** Localized stage name shown under the stage number in the HUD center. */
  private hudStageName: HTMLElement
  private hudEnemies: HTMLElement
  private hudHiScore: HTMLElement
  private hudStar: HTMLElement
  private hudReplay: HTMLElement
  private hudReplayDifficulty: HTMLElement
  /** 督战 (supervise) HUD badge — visible while God AI fights as player1. */
  private hudSpectate: HTMLElement
  /** Take Over button — visible when paused in spectate mode. */
  private hudTakeoverBtn: HTMLElement
  /** Callback for the Take Over button (set by Game). */
  onSpectateTakeover: (() => void) | null = null
  /** Callback for the Take Over button while a replay is active (set by GameReplay). */
  onReplayTakeover: (() => void) | null = null
  /** 督战 battle-speed chip (×1.5 / ×2 / ×4) — hidden at ×1. */
  private hudSpeed: HTMLElement
  private hudSpeedValue: HTMLElement
  private lastBattleSpeed = 1
  private lastSpectate = false
  readonly replayController: ReplayController
  private buffShield: HTMLElement
  private buffShieldTime: HTMLElement
  private buffFreeze: HTMLElement
  private buffFreezeTime: HTMLElement
  private buffFence: HTMLElement
  private buffFenceTime: HTMLElement
  private overlay: HTMLElement
  private menuScreen: HTMLElement
  private pauseScreen: HTMLElement
  private gameOverScreen: HTMLElement
  private stageClearScreen: HTMLElement
  private victoryScreen: HTMLElement
  private recoveryScreen: HTMLElement
  private recoveryOptions: HTMLElement[] = []
  /** Per-option availability, set by Game while the recovery menu is open. */
  private recoveryAvailability: boolean[] = []
  private footer: HTMLElement

  // ---- Snapshot framework UI (plan §12, §13) ----
  readonly snapshotBrowser: SnapshotBrowser
  readonly replayBrowser: ReplayBrowser
  readonly controlCenter: ControlCenter
  private toastEl: HTMLElement
  private toastTimer = 0

  // ---- Performance Observatory (Alt+D dev overlay) ----
  readonly perfOverlay: PerfOverlay

  // ---- Controls / key-bindings panel ----
  private controlsScreen: HTMLElement
  private controlsKeyButtons = new Map<keyof KeyBindings, HTMLElement>()
  private controlsBindings: KeyBindings = { ...DEFAULT_KEYS }
  private controlsOnChanged: (() => void) | null = null
  private listeningAction: keyof KeyBindings | null = null
  private controlsOpen = false

  /** Menu action callbacks (mouse support). Registered by Game. */
  private menuActions: MenuActions | null = null

  /** Ordered list of rebindable gameplay actions shown in the controls panel.
   *  Only gameplay-relevant keys: movement, fire, pause, and active items. */
  private static readonly CONTROL_ACTIONS: ReadonlyArray<keyof KeyBindings> = [
    'up',
    'down',
    'left',
    'right',
    'fire',
    'pause',
    'guard',
    'frenzy',
    'rewind',
  ]

  // Start as a sentinel so the first showScreen('menu') in the constructor
  // actually applies (the guard bails when screen === currentScreen). If this
  // were pre-set to 'menu', the initial menu would never receive the
  // 'active'/'visible' classes and would render invisible on first load.
  private currentScreen = ''
  private animatedScore = 0
  private displayScore = 0
  private lastThemeKey = ''

  // Cached DOM elements for menu (avoid querySelectorAll every frame)
  private menuDiffOptions: HTMLElement[] = []
  private menuThemeOptions: HTMLElement[] = []
  private menuLangOptions: HTMLElement[] = []
  private menuRows: HTMLElement[] = []
  private menuStageValue: HTMLElement | null = null
  private menuStageName: HTMLElement | null = null
  private menuStageDropdown: HTMLElement | null = null
  private stageDropdownOpen = false
  private menuHiScore: HTMLElement | null = null
  private menuResumeStage: HTMLElement | null = null
  private resumeTarget: { stage: number; stageName: string; score: number } | null = null
  private menuResumeInfo: HTMLElement | null = null
  private menuStartBtn: HTMLElement | null = null
  private menuResumeHint: HTMLElement | null = null
  private menuStartHint: HTMLElement | null = null
  private menuControlsHint: HTMLElement | null = null
  /** Whether a resumable manual snapshot exists (set after boot hydration). */
  private hasResume = false
  private stageClearName: HTMLElement | null = null
  private victoryScoreEl: HTMLElement | null = null
  private recoveryCountdownNum: HTMLElement | null = null

  // Last HUD values (avoid unnecessary textContent writes)
  private lastScore = -1
  private lastHiScore = -1
  private lastStage = -1
  /** Last written localized stage name, so a language switch re-renders it. */
  private lastStageName = ''
  private lastEnemies = -1
  private lastLives = -1
  private lastLives2 = -1
  private lastScore2 = -1
  private lastStar = -1
  // Super power-up inventory counters (DECISIONS.md §31)
  private hudGuard: HTMLElement
  private hudFrenzy: HTMLElement
  private hudSacrifice: HTMLElement
  private hudRewind: HTMLElement
  private superItems: HTMLElement[]
  private lastGuard = -1
  private lastFrenzy = -1
  private lastSacrifice = -1
  private lastRewind = -1
  // Super item key labels (dynamic, reflects rebound keys)
  private hudGuardLabel: HTMLElement | null = null
  private hudFrenzyLabel: HTMLElement | null = null
  private hudRewindLabel: HTMLElement | null = null
  // Buff countdowns: remaining whole seconds last written (-1 = chip hidden).
  private lastShieldSec = -1
  private lastFreezeSec = -1
  private lastFenceSec = -1
  private lastMenuCursor = -1
  private lastDifficultyKey = ''
  private lastThemeKeyMenu = ''
  private lastSelectedStage = -1
  private lastHighScoreMenu = -1
  private lastStageClear = ''
  private lastVictory = ''

  constructor(root: HTMLElement) {
    this.root = root
    this.root.className = 'game-wrapper'

    // HUD bar (above canvas)
    this.hudBar = this.createElement('div', 'hud-bar')
    this.hudBar.innerHTML = `
      <div class="hud-group hud-left">
        <div class="hud-item">
          <span class="hud-label" data-i18n="hud.score">SCORE</span>
          <span class="hud-value" data-hud="score">000000</span>
        </div>
        <div class="hud-item" data-hud="score2-wrap" style="display:none">
          <span class="hud-label" style="color:#f0c040" data-i18n="hud.god">GOD</span>
          <span class="hud-value" data-hud="score2" style="color:#f0c040">000000</span>
        </div>
        <div class="hud-item">
          <span class="hud-label" data-i18n="hud.hi">HI</span>
          <span class="hud-value hud-hi" data-hud="hiscore">000000</span>
        </div>
        <div class="hud-item">
          <span class="hud-label" data-i18n="hud.star">STAR</span>
          <span class="hud-value hud-star" data-hud="star"></span>
        </div>
      </div>        <div class="hud-group hud-center">
        <div class="hud-item hud-replay" data-hud="replay" hidden>
          <span class="hud-label" data-i18n="hud.replayMode">REPLAY MODE</span>
          <span class="hud-replay-difficulty" data-hud="replay-difficulty"></span>
        </div>
        <div class="hud-item hud-spectate" data-hud="spectate" hidden>
          <span class="hud-label" data-i18n="hud.spectate">SPECTATE</span>
        </div>
        <div class="hud-item hud-speed" data-hud="speed" hidden>
          <span class="hud-label" data-i18n="hud.speed">SPEED</span>
          <span class="hud-value hud-speed-value" data-hud="speed-value">×1</span>
        </div>
        <div class="hud-item hud-stage">
          <div class="hud-stage-head">
            <span class="hud-label" data-i18n="hud.stage">STAGE</span>
            <span class="hud-value" data-hud="stage">01</span>
          </div>
          <span class="hud-stage-name" data-hud="stage-name"></span>
        </div>
        <div class="hud-buffs" data-hud="buffs">
          <div class="buff-chip buff-shield" data-buff="shield" hidden>
            <span class="buff-icon">🛡</span>
            <span class="buff-time" data-buff-time="shield">0</span>
          </div>
          <div class="buff-chip buff-freeze" data-buff="freeze" hidden>
            <span class="buff-icon">❄</span>
            <span class="buff-time" data-buff-time="freeze">0</span>
          </div>
          <div class="buff-chip buff-fence" data-buff="fence" hidden>
            <span class="buff-icon">🔧</span>
            <span class="buff-time" data-buff-time="fence">0</span>
          </div>
        </div>
        <div class="hud-pause" data-hud="pause">
          <span class="hud-pause-title"><span class="hud-pause-dot"></span><span data-i18n="pause.title">PAUSED</span></span>
          <span class="hud-pause-hint" data-i18n="hud.pauseHint">P Resume</span>
          <button class="hud-takeover-btn" data-hud="takeover" type="button" hidden data-i18n="hud.takeover">🎮 Take Over</button>
        </div>
      </div>
      <div class="hud-group hud-right">
        <div class="hud-item">
          <span class="hud-label" data-i18n="hud.lives">LIVES</span>
          <span class="hud-value hud-lives" data-hud="lives">♥♥♥</span>
        </div>
        <div class="hud-item" data-hud="coop-lives" style="display:none">
          <span class="hud-label" data-i18n="hud.god">GOD</span>
          <span class="hud-value hud-lives" data-hud="lives2" style="color:#f0c040">—</span>
        </div>
        <div class="hud-item">
          <span class="hud-label" data-i18n="hud.enemy">ENEMY</span>
          <span class="hud-value" data-hud="enemies">20</span>
        </div>
        <div class="hud-item hud-super">
          <span class="hud-label" data-hud-super-label="guard">Guardian&lt;F5&gt;</span>
          <span class="hud-value" data-hud="guard">0</span>
        </div>
        <div class="hud-item hud-super">
          <span class="hud-label" data-hud-super-label="frenzy">Frenzy&lt;F6&gt;</span>
          <span class="hud-value" data-hud="frenzy">0</span>
        </div>
        <div class="hud-item hud-super">
          <span class="hud-label" data-i18n="hud.sacrifice">同归</span>
          <span class="hud-value" data-hud="sacrifice">0</span>
        </div>
        <div class="hud-item hud-super">
          <span class="hud-label" data-hud-super-label="rewind">Time Box&lt;F7&gt;</span>
          <span class="hud-value" data-hud="rewind">0</span>
        </div>
      </div>
    `

    // Game container (canvas + overlay)
    const gameContainer = this.createElement('div', 'game-container')

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

    // Menu screen
    this.menuScreen = this.createMenuScreen()

    // Pause screen
    this.pauseScreen = this.createElement('div', 'ui-screen ui-paused')
    this.pauseScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title" data-i18n="pause.title">PAUSED</h2>
        <p class="ui-hint" data-i18n="pause.hint">Press P to resume</p>
      </div>
    `

    // Game over screen
    this.gameOverScreen = this.createElement('div', 'ui-screen ui-gameover')
    this.gameOverScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-danger" data-i18n="gameover.title">GAME OVER</h2>
        <p class="ui-hint" data-i18n="gameover.hint">Press Alt+R or Enter to return to menu</p>
      </div>
    `

    // Stage clear screen
    this.stageClearScreen = this.createElement('div', 'ui-screen ui-stageclear')
    this.stageClearScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-success" data-i18n="stageclear.title">STAGE CLEAR</h2>
        <p class="ui-stage-name" data-stage="name">Stage 1 Complete</p>
      </div>
    `

    // Victory screen
    this.victoryScreen = this.createElement('div', 'ui-screen ui-victory')
    this.victoryScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-success" data-i18n="victory.title">VICTORY!</h2>
        <p class="ui-score-display">Final Score: <span data-victory="score">0</span></p>
        <p class="ui-hint" data-i18n="victory.hint">Press Alt+R or Enter to play again</p>
      </div>
    `

    // Recovery screen (mission failed → rewind / restart)
    this.recoveryScreen = this.createRecoveryScreen()

    this.overlay.appendChild(this.menuScreen)
    this.overlay.appendChild(this.pauseScreen)
    this.overlay.appendChild(this.gameOverScreen)
    this.overlay.appendChild(this.stageClearScreen)
    this.overlay.appendChild(this.victoryScreen)
    this.overlay.appendChild(this.recoveryScreen)

    // Controls / key-bindings screen
    this.controlsScreen = this.createControlsScreen()
    this.overlay.appendChild(this.controlsScreen)

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
    this.root.appendChild(this.hudBar)
    gameContainer.appendChild(this.overlay)
    this.root.appendChild(gameContainer)
    this.root.appendChild(this.footer)

    // Cache elements
    this.hudScore = this.hudBar.querySelector('[data-hud="score"]')!
    this.hudScore2 = this.hudBar.querySelector('[data-hud="score2"]')
    this.hudScore2Wrap = this.hudBar.querySelector('[data-hud="score2-wrap"]')
    this.hudLives = this.hudBar.querySelector('[data-hud="lives"]')!
    this.hudLives2 = this.hudBar.querySelector('[data-hud="lives2"]')
    this.hudCoopLives = this.hudBar.querySelector('[data-hud="coop-lives"]')
    this.hudStage = this.hudBar.querySelector('[data-hud="stage"]')!
    this.hudStageName = this.hudBar.querySelector('[data-hud="stage-name"]')!
    this.hudEnemies = this.hudBar.querySelector('[data-hud="enemies"]')!
    this.hudHiScore = this.hudBar.querySelector('[data-hud="hiscore"]')!
    this.hudStar = this.hudBar.querySelector('[data-hud="star"]')!
    this.hudReplay = this.hudBar.querySelector('[data-hud="replay"]')!
    this.hudReplayDifficulty = this.hudBar.querySelector('[data-hud="replay-difficulty"]')!
    this.hudSpectate = this.hudBar.querySelector('[data-hud="spectate"]')!
    this.hudTakeoverBtn = this.hudBar.querySelector('[data-hud="takeover"]')!
    this.hudSpeed = this.hudBar.querySelector('[data-hud="speed"]')!
    this.hudSpeedValue = this.hudBar.querySelector('[data-hud="speed-value"]')!

    // Replay Controller (video player style)
    this.replayController = new ReplayController()
    this.replayController.hide()
    this.root.appendChild(this.replayController.el)
    // Take Over button — routes to the replay callback while a replay is
    // active, otherwise to the spectate callback. Both live on the HUD so the
    // entry point is consistent across 督战 (spectate) and replay modes.
    this.hudTakeoverBtn.addEventListener('click', () => {
      if (this.replayController.isActive) {
        this.onReplayTakeover?.()
      } else {
        this.onSpectateTakeover?.()
      }
    })
    this.hudGuard = this.hudBar.querySelector('[data-hud="guard"]')!
    this.hudFrenzy = this.hudBar.querySelector('[data-hud="frenzy"]')!
    this.hudSacrifice = this.hudBar.querySelector('[data-hud="sacrifice"]')!
    this.hudRewind = this.hudBar.querySelector('[data-hud="rewind"]')!
    this.superItems = Array.from(this.hudBar.querySelectorAll('.hud-super'))
    this.hudGuardLabel = this.hudBar.querySelector('[data-hud-super-label="guard"]')
    this.hudFrenzyLabel = this.hudBar.querySelector('[data-hud-super-label="frenzy"]')
    this.hudRewindLabel = this.hudBar.querySelector('[data-hud-super-label="rewind"]')
    this.buffShield = this.hudBar.querySelector('[data-buff="shield"]')!
    this.buffShieldTime = this.hudBar.querySelector('[data-buff-time="shield"]')!
    this.buffFreeze = this.hudBar.querySelector('[data-buff="freeze"]')!
    this.buffFreezeTime = this.hudBar.querySelector('[data-buff-time="freeze"]')!
    this.buffFence = this.hudBar.querySelector('[data-buff="fence"]')!
    this.buffFenceTime = this.hudBar.querySelector('[data-buff-time="fence"]')!

    // Cache menu DOM elements (avoid querySelectorAll every frame)
    this.menuDiffOptions = Array.from(
      this.menuScreen.querySelectorAll('[data-difficulty="options"] .menu-option'),
    ) as HTMLElement[]
    this.menuThemeOptions = Array.from(
      this.menuScreen.querySelectorAll('[data-theme="options"] .menu-option'),
    ) as HTMLElement[]
    this.menuLangOptions = Array.from(
      this.menuScreen.querySelectorAll('[data-language="options"] .menu-option'),
    ) as HTMLElement[]
    this.menuRows = Array.from(this.menuScreen.querySelectorAll('.menu-row')) as HTMLElement[]
    this.menuStageValue = this.menuScreen.querySelector('[data-stage="value"]')
    this.menuStageName = this.menuScreen.querySelector('[data-stage="name"]')
    this.menuStageDropdown = this.menuScreen.querySelector('[data-stage="dropdown"]')
    this.menuHiScore = this.menuScreen.querySelector('[data-menu="hiscore"]')
    this.menuResumeStage = this.menuScreen.querySelector('[data-menu="resume-stage"]')
    this.menuResumeInfo = this.menuScreen.querySelector('[data-menu="resume-info"]')
    this.menuStartBtn = this.menuScreen.querySelector('[data-menu="start"]')
    this.menuResumeHint = this.menuScreen.querySelector('[data-menu="resume-hint"]')
    this.menuStartHint = this.menuScreen.querySelector('[data-menu="start-hint"]')
    this.menuControlsHint = this.menuScreen.querySelector('[data-menu="controls-hint"]')
    this.stageClearName = this.stageClearScreen.querySelector('[data-stage="name"]')
    this.victoryScoreEl = this.victoryScreen.querySelector('[data-victory="score"]')
    this.recoveryCountdownNum = this.recoveryScreen.querySelector(
      '[data-recovery="countdown-number"]',
    )

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

  private createMenuScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-menu')
    screen.innerHTML = `
      <div class="menu-panel">
        <div class="menu-header">
          <h1 class="menu-title" data-i18n="menu.title">BATTLE CITY</h1>
          <p class="menu-subtitle" data-i18n="menu.subtitle">Faithful to the classic. Designed for the future.</p>
        </div>
        <div class="menu-section">
          <div class="menu-row menu-resume" data-menu="resume">
            <div class="menu-resume-main">
              <span class="menu-resume-label" data-i18n="menu.resume.label">RESUME</span>
              <span class="menu-resume-stage" data-menu="resume-stage">STAGE 01</span>
            </div>
            <div class="menu-resume-info" data-menu="resume-info" data-i18n="menu.resume.info">Continue from your last manual save</div>
            <span class="menu-enter-hint" data-menu="resume-hint" data-i18n="menu.resume.hint">Enter ↵</span>
          </div>
          <div class="menu-row" data-menu="difficulty">
            <span class="menu-label" data-i18n="menu.difficulty">DIFFICULTY</span>
            <div class="menu-options" data-difficulty="options"></div>
          </div>
          <div class="menu-row" data-menu="theme">
            <span class="menu-label" data-i18n="menu.theme">THEME</span>
            <div class="menu-options" data-theme="options"></div>
          </div>
          <div class="menu-row" data-menu="language">
            <span class="menu-label" data-i18n="menu.language">LANGUAGE</span>
            <div class="menu-options" data-language="options"></div>
          </div>
          <div class="menu-row" data-menu="stage">
            <span class="menu-label" data-i18n="menu.stage">STAGE</span>
            <div class="menu-stage-dropdown" data-stage="dropdown">
              <div class="menu-stage-trigger" data-stage="trigger">
                <span class="menu-stage-value" data-stage="value">01 / 35</span>
                <span class="menu-stage-name" data-stage="name">Outpost</span>
                <span class="menu-stage-chevron">▾</span>
              </div>
              <div class="menu-stage-list" data-stage="list"></div>
            </div>
          </div>
          <div class="menu-row" data-menu="start-row">
          <div class="menu-start-button" data-menu="start" data-i18n="menu.start.newGame">NEW GAME</div>
          <span class="menu-enter-hint" data-menu="start-hint" data-i18n="menu.start.hint">Enter ↵</span>
        </div>
        <div class="menu-row" data-menu="controls">
          <div class="menu-controls-button" data-i18n="menu.controls">⚙ CONTROLS</div>
          <span class="menu-enter-hint" data-menu="controls-hint" data-i18n="menu.controls.hint">Enter ↵</span>
        </div>
        <div class="menu-controls">
          <span data-i18n="menu.nav.select">↑ ↓ Select</span>
          <span data-i18n="menu.nav.change">← → Change</span>
        </div>
        <div class="menu-hiscore">
          <span data-i18n="menu.hiscoreLabel">High Score:</span> <span data-menu="hiscore">0</span>
        </div>
      </div>
    `

    // Populate difficulty options
    const diffContainer = screen.querySelector('[data-difficulty="options"]')!
    for (const key of DIFFICULTY_KEYS) {
      const diff = DIFFICULTIES[key]
      const opt = this.createElement('div', 'menu-option')
      opt.dataset.value = key
      opt.textContent = t(`difficulty.${key}`) || diff.name
      opt.addEventListener('click', () => this.menuActions?.selectDifficulty(key))
      diffContainer.appendChild(opt)
    }

    // Populate theme options
    const themeContainer = screen.querySelector('[data-theme="options"]')!
    for (const def of THEME_DEFINITIONS) {
      const opt = this.createElement('div', 'menu-option')
      opt.dataset.value = def.key
      opt.textContent = t(`theme.${def.key}`) || def.name
      opt.addEventListener('click', () => this.menuActions?.selectTheme(def.key))
      themeContainer.appendChild(opt)
    }

    // Populate language options — native names, cycleable via the menu.
    const langContainer = screen.querySelector('[data-language="options"]')!
    for (const code of i18n.available) {
      const opt = this.createElement('div', 'menu-option')
      opt.dataset.value = code
      opt.textContent = i18n.name(code)
      opt.addEventListener('click', () => this.menuActions?.selectLanguage(code))
      langContainer.appendChild(opt)
    }

    // Stage dropdown — populate and wire
    const stageList = screen.querySelector('[data-stage="list"]') as HTMLElement | null
    const stageTrigger = screen.querySelector('[data-stage="trigger"]') as HTMLElement | null
    if (stageList) {
      for (let i = 0; i < STAGES.length; i++) {
        const item = this.createElement('div', 'menu-stage-item')
        item.dataset.stageIndex = String(i)
        item.innerHTML = `<span class="menu-stage-item-num">${String(i + 1).padStart(2, '0')}</span><span class="menu-stage-item-name">${localizedStageName(i)}</span>`
        item.addEventListener('click', (e) => {
          e.stopPropagation()
          this.menuActions?.selectStage(i)
          this.closeStageDropdown()
        })
        stageList.appendChild(item)
      }
    }
    stageTrigger?.addEventListener('click', (e) => {
      e.stopPropagation()
      this.toggleStageDropdown()
    })
    // Close dropdown when clicking outside
    screen.addEventListener('click', () => this.closeStageDropdown())

    // Start button — mouse equivalent of Enter/Space (new game)
    const startBtn = screen.querySelector('[data-menu="start"]') as HTMLElement | null
    startBtn?.addEventListener('click', () => this.menuActions?.start())

    // Resume row — mouse equivalent of the default confirm when a manual
    // snapshot exists (routes through Game.menuResume → recovery.beginLoad).
    const resumeRow = screen.querySelector('[data-menu="resume"]') as HTMLElement | null
    resumeRow?.addEventListener('click', () => this.menuActions?.resume())

    // Open the controls panel
    const controlsBtn = screen.querySelector('[data-menu="controls"]') as HTMLElement | null
    if (controlsBtn) {
      controlsBtn.addEventListener('click', () => this.openControls())
    }

    return screen
  }

  /**
   * Register the menu action callbacks (mouse support). Called once from
   * Game so clicks on the start screen route through the same World-mutating
   * code paths as keyboard input. UIManager itself stays read-only.
   */
  initMenuActions(actions: MenuActions): void {
    this.menuActions = actions
  }

  /**
   * Tell the menu about the last manually-saved snapshot (called once after
   * boot hydration). When one exists the start screen shows a prominent
   * RESUME row (the default highlighted action) and relabels the bottom button
   * to NEW GAME; when none exists the resume row is hidden and the original
   * start behaviour is preserved.
   */
  setResumeTarget(target: { stage: number; stageName: string; score: number } | null): void {
    this.resumeTarget = target
    this.hasResume = !!target
    this.menuScreen.classList.toggle('has-resume', this.hasResume)

    if (this.menuResumeStage) {
      this.menuResumeStage.textContent = target
        ? t('menu.resume.stageFormat', { n: String(target.stage + 1).padStart(2, '0') })
        : t('menu.resume.stageFormat', { n: '01' })
    }
    if (this.menuResumeInfo) {
      this.menuResumeInfo.textContent = target
        ? t('menu.resume.infoDetailed', {
            stage: String(target.stage + 1),
            name: localizedStageName(target.stage),
            score: target.score,
          })
        : t('menu.resume.info')
    }
    if (this.menuStartBtn) {
      this.menuStartBtn.textContent = this.hasResume
        ? t('menu.start.newGame')
        : t('menu.start.startGame')
    }
  }

  /**
   * Re-localize every static string in the UI. Called once at boot and again
   * whenever the active locale changes (subscribed via `i18n.subscribe`).
   * Elements declare keys with `data-i18n`; a few labels that depend on live
   * data — difficulty/theme/language option text and the super-item key
   * bindings — are refreshed explicitly here.
   */
  refreshText(): void {
    // Localize everything under the app root, plus any overlays the
    // ReplayController appends directly to <body> (the end-of-replay card).
    localizeRoot(document.body)
    // Difficulty / theme option labels are built once from config; re-apply
    // the catalog strings so they follow the active locale.
    for (const opt of this.menuDiffOptions) {
      const key = opt.dataset.value
      if (key) {
        const label = t(`difficulty.${key}`)
        opt.textContent = label === `difficulty.${key}` ? DIFFICULTIES[key].name : label
      }
    }
    for (const opt of this.menuThemeOptions) {
      const key = opt.dataset.value
      if (key) {
        const label = t(`theme.${key}`)
        opt.textContent = label === `theme.${key}` ? key : label
      }
    }
    // Language option labels are native names — no re-localization needed,
    // but the selected highlight must follow the active locale (e.g. when the
    // user clicks an option, or the LANGUAGE menu row cycles it).
    this.updateLanguageHighlight()
    // Stage list + resume-target names are built once; re-localize them so a
    // live language switch updates the menu without a full rebuild.
    this.refreshStageList()
    if (this.resumeTarget) this.setResumeTarget(this.resumeTarget)
    // Super-item key labels (name + current binding) follow the locale.
    this.updateSuperKeyLabels()
  }

  /** Re-apply localized names to the (once-built) menu stage dropdown items. */
  private refreshStageList(): void {
    const list = this.menuScreen.querySelector('[data-stage="list"]')
    if (!list) return
    list.querySelectorAll<HTMLElement>('.menu-stage-item').forEach((item) => {
      const idx = Number(item.dataset.stageIndex)
      const nameEl = item.querySelector<HTMLElement>('.menu-stage-item-name')
      if (nameEl && !Number.isNaN(idx)) nameEl.textContent = localizedStageName(idx)
    })
  }

  /** Highlight the active language option in the LANGUAGE menu row. */
  private updateLanguageHighlight(): void {
    for (const opt of this.menuLangOptions) {
      opt.classList.toggle('selected', opt.dataset.value === i18n.locale)
    }
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
      menu: this.menuScreen,
      paused: this.pauseScreen,
      gameover: this.gameOverScreen,
      stageclear: this.stageClearScreen,
      victory: this.victoryScreen,
      recovery: this.recoveryScreen,
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
      this.closeStageDropdown()
    }

    // Show/hide HUD based on state
    if (screen === 'menu' || screen === 'victory') {
      this.hudBar.classList.remove('visible')
    } else {
      this.hudBar.classList.add('visible')
    }

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
      this.recoveryScreen.classList.remove('fading', 'countdown')
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
    if (this.controlsOpen) return

    // Same for the Snapshot Browser — it owns the overlay while open.
    if (this.snapshotBrowser.isOpen()) return

    // Animate score
    this.animatedScore = world.score
    if (this.displayScore !== this.animatedScore) {
      const diff = this.animatedScore - this.displayScore
      this.displayScore += Math.sign(diff) * Math.max(1, Math.abs(diff) * 0.15)
      if (Math.abs(this.animatedScore - this.displayScore) < 1) {
        this.displayScore = this.animatedScore
      }
    }

    // HUD — only write to DOM when values actually change
    const scoreVal = Math.round(this.displayScore)
    if (scoreVal !== this.lastScore) {
      this.hudScore.textContent = String(scoreVal).padStart(6, '0')
      this.lastScore = scoreVal
    }
    if (world.highScore !== this.lastHiScore) {
      this.hudHiScore.textContent = String(world.highScore).padStart(6, '0')
      this.lastHiScore = world.highScore
    }
    if (world.stageIndex !== this.lastStage) {
      this.hudStage.textContent = String(world.stageIndex + 1).padStart(2, '0')
      this.lastStage = world.stageIndex
      this.lastStageName = '' // force the name to re-render for the new stage
    }
    // Stage name (localized) — re-render when the stage changes OR when the
    // active language changed (the number guard above leaves same-stage
    // language switches untouched, so check the name string too).
    const stageName = localizedStageName(world.stageIndex)
    if (stageName !== this.lastStageName) {
      this.hudStageName.textContent = stageName
      this.lastStageName = stageName
    }
    if (world.enemiesRemaining !== this.lastEnemies) {
      this.hudEnemies.textContent = String(world.enemiesRemaining)
      this.lastEnemies = world.enemiesRemaining
    }
    // 督战 (supervise) badge — synced from the World so toggle, stage restore,
    // and startGame resets all converge on the same source of truth.
    if (world.spectate !== this.lastSpectate) {
      this.lastSpectate = world.spectate
      this.hudSpectate.hidden = !world.spectate
    }
    if (world.lives !== this.lastLives) {
      const hearts = '♥'.repeat(Math.max(0, world.lives))
      this.hudLives.textContent = hearts || '—'
      this.lastLives = world.lives
    }
    // Co-op God score (Lie-Back-Win-Mode Q1)
    if (this.hudScore2Wrap && this.hudScore2) {
      const showScore2 = world.coop
      this.hudScore2Wrap.style.display = showScore2 ? '' : 'none'
      if (showScore2 && world.score2 !== this.lastScore2) {
        this.hudScore2.textContent = String(world.score2).padStart(6, '0')
        this.lastScore2 = world.score2
      }
    }
    // Co-op God lives (Lie-Back-Win-Mode)
    if (this.hudCoopLives && this.hudLives2) {
      const showCoop = world.coop && world.lives2 > 0
      this.hudCoopLives.style.display = showCoop ? '' : 'none'
      if (showCoop && world.lives2 !== this.lastLives2) {
        this.hudLives2.textContent = '♥'.repeat(Math.max(0, world.lives2))
        this.lastLives2 = world.lives2
      }
    }

    // Player star level (★ power-up). Show only filled stars; no empty
    // placeholders. If the player has no stars, show nothing.
    if (world.playerLevel !== this.lastStar) {
      const lvl = Math.max(0, world.playerLevel)
      this.hudStar.textContent = lvl > 0 ? '★'.repeat(lvl) : '--'
      this.lastStar = world.playerLevel
    }

    // Super power-up inventory counters (DECISIONS.md §31). Written only when
    // the count actually changes. Hidden in classic mode (no 强力道具).
    const hideSuper = world.rules.superDropChance === 0
    for (const el of this.superItems) {
      if (el.hidden !== hideSuper) el.hidden = hideSuper
    }
    if (!hideSuper) {
      if (world.guardStock !== this.lastGuard) {
        this.hudGuard.textContent = String(world.guardStock)
        this.lastGuard = world.guardStock
      }
      if (world.frenzyStock !== this.lastFrenzy) {
        this.hudFrenzy.textContent = String(world.frenzyStock)
        this.lastFrenzy = world.frenzyStock
      }
      if (world.sacrificeStock !== this.lastSacrifice) {
        this.hudSacrifice.textContent = String(world.sacrificeStock)
        this.lastSacrifice = world.sacrificeStock
      }
      if (world.rewindStock !== this.lastRewind) {
        this.hudRewind.textContent = String(world.rewindStock)
        this.lastRewind = world.rewindStock
      }
    }

    // Active timed buffs (shield / freeze) — countdown shown outside the field
    this.updateBuffs(world)

    // Menu state — only update when in menu
    if (world.state === 'menu') {
      this.updateMenu(world)
    }

    // Stage clear — only write while actually on the stage-clear screen, and
    // only when the text changed. Previously this rebuilt a template-literal
    // string and wrote a hidden element's textContent EVERY frame during play.
    if (world.state === 'stageclear' && this.stageClearName) {
      const txt = t('stageclear.name', { n: world.stageIndex + 1, name: world.currentStageName })
      if (txt !== this.lastStageClear) {
        this.stageClearName.textContent = txt
        this.lastStageClear = txt
      }
    }

    // Victory — same guard (only on the victory screen, only on change)
    if (world.state === 'victory' && this.victoryScoreEl) {
      const v = String(world.score)
      if (v !== this.lastVictory) {
        this.victoryScoreEl.textContent = v
        this.lastVictory = v
      }
    }

    // Recovery screen
    if (world.state === 'recovery') {
      this.updateRecovery(world)
    }

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
    this.hudBar.classList.toggle('paused', screen === 'paused' || replayPaused)
    this.hudTakeoverBtn.hidden = !((world.spectate && screen === 'paused') || replayPaused)
  }

  /** Show or hide the persistent REPLAY indicator in the HUD center area. */
  setReplayMode(isReplay: boolean, isPaused = false, difficulty?: string): void {
    this.hudReplay.hidden = !isReplay
    if (isReplay) {
      this.hudReplayDifficulty.textContent = difficulty ?? ''
      this.replayController.show()
      this.replayController.setPaused(isPaused)
    } else {
      this.hudReplayDifficulty.textContent = ''
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
    if (speed === this.lastBattleSpeed) return
    this.lastBattleSpeed = speed
    this.hudSpeed.hidden = speed === 1
    this.hudSpeedValue.textContent = `×${speed}`
  }

  /**
   * Update the timed-buff countdown chips in the HUD. Time-limited buffs:
   * SHIELD (spawn protection, via player.shieldTimer), FREEZE (freeze/clock
   * pickup, via world.freezeTimer), and FENCE (steel ring, via
   * fenceExpireFrame). Star / extra life / bomb are instant or permanent and
   * intentionally have no timer.
   *
   * DOM writes are keyed on the remaining WHOLE second so the text only
   * changes ~once per second, and a chip's `hidden` attribute flips only on
   * the transition to/from 0 — no per-frame DOM churn.
   */
  private updateBuffs(world: World): void {
    const shieldMs = world.player?.alive ? (world.player.shieldTimer ?? 0) : 0
    this.updateBuffChip(this.buffShield, this.buffShieldTime, shieldMs, 'shield')

    this.updateBuffChip(this.buffFreeze, this.buffFreezeTime, world.freezeTimer, 'freeze')

    // Fence countdown: fenceExpireFrame is absolute; convert to ms remaining.
    const fenceMs =
      world.fenceExpireFrame !== undefined && world.fenceExpireFrame > world.frame
        ? (world.fenceExpireFrame - world.frame) * (1000 / 60)
        : 0
    this.updateBuffChip(this.buffFence, this.buffFenceTime, fenceMs, 'fence')
  }

  /** Reflect a single buff's remaining time into its chip; hide it at 0. */
  private updateBuffChip(
    chip: HTMLElement,
    timeEl: HTMLElement,
    ms: number,
    which: 'shield' | 'freeze' | 'fence',
  ): void {
    const sec = ms > 0 ? Math.ceil(ms / 1000) : 0
    const last =
      which === 'shield'
        ? this.lastShieldSec
        : which === 'fence'
          ? this.lastFenceSec
          : this.lastFreezeSec
    if (sec === last) return
    if (which === 'shield') this.lastShieldSec = sec
    else if (which === 'fence') this.lastFenceSec = sec
    else this.lastFreezeSec = sec

    if (sec > 0) {
      timeEl.textContent = String(sec)
      chip.hidden = false
    } else {
      chip.hidden = true
    }
  }

  /** Toggle the developer Performance Observatory overlay (Alt+D hotkey / Control
   *  Center button). Keeps the Control Center's DEVELOPER button in sync. */
  togglePerfOverlay(): void {
    this.perfOverlay.toggle()
    this.controlCenter.setPerfState(this.perfOverlay.active)
  }

  // ---- Stage dropdown ----

  private toggleStageDropdown(): void {
    this.stageDropdownOpen = !this.stageDropdownOpen
    this.menuStageDropdown?.classList.toggle('open', this.stageDropdownOpen)
  }

  private closeStageDropdown(): void {
    if (!this.stageDropdownOpen) return
    this.stageDropdownOpen = false
    this.menuStageDropdown?.classList.remove('open')
  }

  private updateMenu(world: World): void {
    // Highlight selected difficulty — only when changed
    if (world.difficultyKey !== this.lastDifficultyKey) {
      this.lastDifficultyKey = world.difficultyKey
      for (const opt of this.menuDiffOptions) {
        opt.classList.toggle('selected', opt.dataset.value === world.difficultyKey)
      }
    }

    // Highlight selected theme — only when changed
    if (world.themeKey !== this.lastThemeKeyMenu) {
      this.lastThemeKeyMenu = world.themeKey
      for (const opt of this.menuThemeOptions) {
        opt.classList.toggle('selected', opt.dataset.value === world.themeKey)
      }
    }

    // Highlight selected language (also re-applied on locale change via
    // refreshText, so this stays correct after a click or menu-cycle).
    this.updateLanguageHighlight()

    // Highlight selected menu row (cursor) — only when changed.
    // Row indices shift down by one when the RESUME row is present.
    // Full order: RESUME? / DIFFICULTY / THEME / LANGUAGE / STAGE / NEW GAME / CONTROLS
    if (world.menuCursor !== this.lastMenuCursor) {
      this.lastMenuCursor = world.menuCursor
      const off = this.hasResume ? 1 : 0
      // Close stage dropdown when cursor moves away from STAGE row
      if (world.menuCursor !== off + 3) {
        this.closeStageDropdown()
      }
      for (const row of this.menuRows) {
        const idx =
          row.dataset.menu === 'resume'
            ? 0
            : row.dataset.menu === 'difficulty'
              ? off
              : row.dataset.menu === 'theme'
                ? off + 1
                : row.dataset.menu === 'language'
                  ? off + 2
                  : row.dataset.menu === 'stage'
                    ? off + 3
                    : row.dataset.menu === 'start-row'
                      ? off + 4
                      : row.dataset.menu === 'controls'
                        ? off + 5
                        : -1
        row.classList.toggle('selected', idx === world.menuCursor)
      }
      // Show ENTER hint only on RESUME and NEW GAME rows when selected
      if (this.menuResumeHint) {
        this.menuResumeHint.classList.toggle('visible', this.hasResume && world.menuCursor === 0)
      }
      if (this.menuStartHint) {
        this.menuStartHint.classList.toggle('visible', world.menuCursor === off + 4)
      }
      if (this.menuControlsHint) {
        this.menuControlsHint.classList.toggle('visible', world.menuCursor === off + 5)
      }
    }

    // Stage selector display — only when changed
    if (world.selectedStage !== this.lastSelectedStage) {
      this.lastSelectedStage = world.selectedStage
      if (this.menuStageValue) {
        this.menuStageValue.textContent = `${String(world.selectedStage + 1).padStart(2, '0')} / ${String(STAGES.length).padStart(2, '0')}`
      }
      if (this.menuStageName) {
        this.menuStageName.textContent = localizedStageName(world.selectedStage) ?? ''
      }
    }

    // High score — only when changed
    if (world.highScore !== this.lastHighScoreMenu) {
      this.lastHighScoreMenu = world.highScore
      if (this.menuHiScore) {
        this.menuHiScore.textContent = String(world.highScore)
      }
    }
  }

  // ---- Recovery Screen ----

  private createRecoveryScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-recovery')
    // Five options, in RECOVERY_OPTIONS order (plan §11):
    // continue · loadLatest · replayStage · restartStage · chooseSnapshot
    screen.innerHTML = `
      <div class="recovery-menu" data-recovery="menu">
        <h2 class="recovery-title ui-danger" data-i18n="recovery.title">MISSION FAILED</h2>
        <p class="recovery-subtitle" data-i18n="recovery.subtitle">Rewind time and try again</p>
        <div class="recovery-options">
          <div class="recovery-option" data-recovery-option="0">
            <span class="recovery-option-icon">🏳</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label" data-i18n="recovery.option.continue.label">Continue</span>
              <span class="recovery-option-desc" data-i18n="recovery.option.continue.desc">Accept defeat — classic game over</span>
            </div>
          </div>
          <div class="recovery-option" data-recovery-option="1">
            <span class="recovery-option-icon">⏪</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label" data-i18n="recovery.option.loadLatest.label">Load Latest Snapshot</span>
              <span class="recovery-option-desc" data-i18n="recovery.option.loadLatest.desc">Return to the most recent safe moment</span>
            </div>
          </div>
          <div class="recovery-option" data-recovery-option="2">
            <span class="recovery-option-icon">↻</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label" data-i18n="recovery.option.replay.label">Replay This Stage</span>
              <span class="recovery-option-desc" data-i18n="recovery.option.replay.desc">Load the stage-start snapshot</span>
            </div>
          </div>
          <div class="recovery-option" data-recovery-option="3">
            <span class="recovery-option-icon">⚑</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label" data-i18n="recovery.option.restart.label">Restart Without Loading</span>
              <span class="recovery-option-desc">Fresh stage start — no snapshot</span>
            </div>
          </div>
          <div class="recovery-option" data-recovery-option="4">
            <span class="recovery-option-icon">🗂</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label" data-i18n="recovery.option.choose.label">Choose a Snapshot…</span>
              <span class="recovery-option-desc" data-i18n="recovery.option.choose.desc">Open the Snapshot Browser</span>
            </div>
          </div>
        </div>
        <div class="recovery-controls">
          <span data-i18n="recovery.controls">↑ ↓ Select    Enter Confirm    Alt+R Menu</span>
        </div>
      </div>
      <div class="recovery-countdown" data-recovery="countdown">
        <span class="countdown-number" data-recovery="countdown-number">3</span>
        <span class="countdown-label" data-i18n="recovery.countdown">READY</span>
      </div>
    `

    // Cache option elements
    const opts = screen.querySelectorAll('[data-recovery-option]')
    opts.forEach((el) => {
      this.recoveryOptions.push(el as HTMLElement)
    })

    return screen
  }

  /** Update recovery overlay from world state */
  private updateRecovery(world: World): void {
    if (world.state !== 'recovery') return

    // Toggle fading / countdown classes
    this.recoveryScreen.classList.toggle('fading', world.recoveryFading)

    const isCountdown = world.recoveryCountdown > 0
    this.recoveryScreen.classList.toggle('countdown', isCountdown)

    // Show countdown number
    if (isCountdown) {
      if (this.recoveryCountdownNum) {
        this.recoveryCountdownNum.textContent = String(world.recoveryCountdown)
      }
      return
    }

    // Update option selection and availability
    for (let i = 0; i < this.recoveryOptions.length; i++) {
      const opt = this.recoveryOptions[i]
      opt.classList.toggle('selected', i === world.recoveryCursor)
      // Availability comes from the RecoveryController (via Game) — a
      // disabled option is greyed out but still selectable (soft-denied
      // on confirm), matching the classic menu feel.
      const available = this.recoveryAvailability[i] ?? true
      opt.classList.toggle('disabled', !available)
    }
  }

  /**
   * Publish per-option availability for the recovery menu. Called by Game
   * when entering the recovery flow (Game asks the RecoveryController —
   * UIManager itself never inspects snapshots).
   */
  setRecoveryAvailability(availability: boolean[]): void {
    this.recoveryAvailability = availability.slice(0, RECOVERY_OPTION_COUNT)
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

  // ---- Controls / Key Bindings Panel ----

  /**
   * Wire the live key-bindings object (the same reference the Input system
   * reads) and a persistence callback. Called once from Game after the
   * PresentationLayer is constructed.
   */
  initControls(bindings: KeyBindings, onChanged: () => void): void {
    this.controlsBindings = bindings
    this.controlsOnChanged = onChanged
    this.refreshAllKeyButtons()
    this.updateSuperKeyLabels()
    // Capture-phase listener so a rebind key never reaches the game Input
    // (which listens on window in the bubble phase). We only act while the
    // panel is open, so normal gameplay input is unaffected.
    window.addEventListener('keydown', this.onControlsKeyDown, true)
  }

  /** Whether the controls panel is currently open (a UI-modal, not a world state). */
  isControlsOpen(): boolean {
    return this.controlsOpen
  }

  /** Expose layout elements so PresentationLayer can measure reserved vertical
   *  space when sizing the canvas. Read-only access only. */
  get hudBarEl(): HTMLElement {
    return this.hudBar
  }
  get footerEl(): HTMLElement {
    return this.footer
  }

  /**
   * Open the controls panel as a modal overlay over whatever screen is
   * currently active (menu, recovery, gameover). The underlying screen
   * keeps its `active` class; `showScreen()` will re-sync on close.
   */
  openControls(): void {
    if (this.controlsOpen) return
    this.controlsOpen = true
    this.controlsScreen.classList.add('active')
    this.listeningAction = null
    this.refreshAllKeyButtons()
  }

  /**
   * Close the controls panel. The underlying screen's `active` class was
   * never removed, so it is already visible; `update() → showScreen()` on
   * the next frame will confirm the correct screen (menu, recovery, or
   * gameover) — no forced class swap needed.
   */
  closeControls(): void {
    if (!this.controlsOpen) return
    this.controlsOpen = false
    this.listeningAction = null
    this.controlsScreen.classList.remove('active')
  }

  private createControlsScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-controls')
    const panel = this.createElement('div', 'ui-panel controls-panel')
    panel.innerHTML = `
      <h2 class="ui-title" data-i18n="controls.title">KEY BINDINGS</h2>
      <p class="ui-hint" data-i18n="controls.hint">Click a key, then press a new one</p>
      <div class="controls-list" data-controls="list"></div>
      <div class="controls-actions">
        <button class="controls-btn" data-controls="reset" type="button" data-i18n="controls.reset">Reset Defaults</button>
        <button class="controls-btn controls-btn-primary" data-controls="back" type="button" data-i18n="controls.back">Back</button>
      </div>
      <p class="ui-hint" data-i18n="controls.escHint">Press Esc to go back</p>
    `

    const list = panel.querySelector('[data-controls="list"]') as HTMLElement
    for (const action of UIManager.CONTROL_ACTIONS) {
      const row = this.createElement('div', 'controls-row')
      const labelEl = this.createElement('span', 'controls-label')
      labelEl.dataset.i18n = `controls.actions.${action}`
      const btn = this.createElement('button', 'controls-key-btn') as HTMLButtonElement
      btn.type = 'button'
      btn.dataset.action = action
      btn.textContent = this.formatKey(this.controlsBindings[action])
      btn.addEventListener('click', () => this.onKeyButtonClick(action))
      row.appendChild(labelEl)
      row.appendChild(btn)
      list.appendChild(row)
      this.controlsKeyButtons.set(action, btn)
    }

    const resetBtn = panel.querySelector('[data-controls="reset"]') as HTMLElement
    resetBtn.addEventListener('click', () => this.resetBindings())
    const backBtn = panel.querySelector('[data-controls="back"]') as HTMLElement
    backBtn.addEventListener('click', () => this.closeControls())

    screen.appendChild(panel)
    return screen
  }

  private onKeyButtonClick(action: keyof KeyBindings): void {
    // Toggle listening mode for this action.
    if (this.listeningAction === action) {
      this.cancelListening()
      return
    }
    this.listeningAction = action
    const btn = this.controlsKeyButtons.get(action)
    if (btn) {
      btn.classList.add('listening')
      btn.classList.remove('conflict')
      btn.textContent = t('controls.pressKey')
    }
    // Clear listening state on any other buttons.
    for (const [other, otherBtn] of this.controlsKeyButtons) {
      if (other !== action) {
        otherBtn.classList.remove('listening')
        otherBtn.textContent = this.formatKey(this.controlsBindings[other])
      }
    }
  }

  private cancelListening(): void {
    this.listeningAction = null
    this.refreshAllKeyButtons()
  }

  private resetBindings(): void {
    for (const action of UIManager.CONTROL_ACTIONS) {
      this.controlsBindings[action] = DEFAULT_KEYS[action]
    }
    this.listeningAction = null
    this.refreshAllKeyButtons()
    this.updateSuperKeyLabels()
    this.controlsOnChanged?.()
  }

  private refreshAllKeyButtons(): void {
    for (const action of UIManager.CONTROL_ACTIONS) {
      this.refreshKeyButton(action)
    }
  }

  private refreshKeyButton(action: keyof KeyBindings): void {
    const btn = this.controlsKeyButtons.get(action)
    if (!btn) return
    btn.classList.remove('listening', 'conflict')
    btn.textContent = this.formatKey(this.controlsBindings[action])
  }

  /** Update HUD super-item key labels (Guardian / Frenzy / Time Box) to reflect current bindings + locale. */
  private updateSuperKeyLabels(): void {
    const pairs: Array<[HTMLElement | null, keyof KeyBindings, string]> = [
      [this.hudGuardLabel, 'guard', t('hud.guard')],
      [this.hudFrenzyLabel, 'frenzy', t('hud.frenzy')],
      [this.hudRewindLabel, 'rewind', t('hud.rewind')],
    ]
    for (const [el, action, name] of pairs) {
      if (el)
        el.textContent = `${name}<${this.formatCode(parseBinding(this.controlsBindings[action]).code)}>`
    }
  }

  /** Reject keys reserved for panel navigation, and duplicates of other actions. */
  private findConflict(action: keyof KeyBindings, binding: string): keyof KeyBindings | null {
    if (binding === 'Escape' || binding === 'Tab') return action // reserved
    for (const other of UIManager.CONTROL_ACTIONS) {
      // Exact binding-string match: a modifier combo (Shift+R) is distinct
      // from its bare key (R), so they must not collide on the same action.
      if (other !== action && this.controlsBindings[other] === binding) return other
    }
    return null
  }

  private flashConflict(action: keyof KeyBindings): void {
    const btn = this.controlsKeyButtons.get(action)
    if (!btn) return
    btn.classList.add('conflict')
    window.setTimeout(() => btn.classList.remove('conflict'), 600)
  }

  private formatKey(binding: string): string {
    const spec = parseBinding(binding)
    const mods: string[] = []
    if (spec.ctrl) mods.push('Ctrl')
    if (spec.shift) mods.push('Shift')
    if (spec.alt) mods.push('Alt')
    if (spec.meta) mods.push('Meta')
    const base = this.formatCode(spec.code)
    return mods.length ? `${mods.join('+')}+${base}` : base
  }

  /** Modifier prefix for a live event, e.g. "Alt" or "Ctrl+Shift". */
  private modifierPrefix(e: KeyboardEvent): string {
    const mods: string[] = []
    if (e.ctrlKey) mods.push('Ctrl')
    if (e.shiftKey) mods.push('Shift')
    if (e.altKey) mods.push('Alt')
    if (e.metaKey) mods.push('Meta')
    return mods.join('+')
  }

  /** Render a bare `KeyboardEvent.code` (no modifiers) into a short label. */
  private formatCode(code: string): string {
    if (code.startsWith('Arrow')) {
      return (
        (
          { ArrowUp: '↑', ArrowDown: '↓', ArrowLeft: '←', ArrowRight: '→' } as Record<
            string,
            string
          >
        )[code] ?? code
      )
    }
    if (code === 'Space') return 'SPACE'
    if (code === 'Escape') return 'ESC'
    if (code === 'Enter') return 'ENTER'
    if (code.startsWith('Key')) return code.slice(3)
    if (code.startsWith('Digit')) return code.slice(5)
    if (code.startsWith('Numpad')) return 'NP' + code.slice(6)
    return code
  }

  private onControlsKeyDown = (e: KeyboardEvent): void => {
    if (!this.controlsOpen) return
    // Own all key input while the panel is open so the game Input never sees
    // it (prevents the menu cursor from moving behind the panel, and stops
    // the rebind key from being registered as "pressed").
    e.preventDefault()
    e.stopImmediatePropagation()

    if (this.listeningAction) {
      const action = this.listeningAction
      const btn = this.controlsKeyButtons.get(action)
      if (e.code === 'Escape') {
        this.cancelListening()
        return
      }
      // A pure modifier key (Alt/Shift/Ctrl/Meta) can't be a binding's primary
      // key. Ignore its keydown so capturing "Alt+S" doesn't finalize on the
      // Alt key itself ("Alt+AltLeft"); show a live preview of the held
      // modifiers instead and wait for the real primary key.
      if (isModifierCode(e.code)) {
        if (btn) btn.textContent = `${this.modifierPrefix(e)}+…`
        return
      }
      const binding = eventToBinding(e)
      const conflict = this.findConflict(action, binding)
      if (conflict) {
        this.flashConflict(action)
        return
      }
      this.controlsBindings[action] = binding
      this.listeningAction = null
      this.refreshKeyButton(action)
      this.updateSuperKeyLabels()
      this.controlsOnChanged?.()
      return
    }

    // Not listening: Esc / Enter closes the panel.
    if (e.code === 'Escape' || e.code === 'Enter') {
      this.closeControls()
    }
  }
}
