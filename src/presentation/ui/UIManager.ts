import type { World } from '../../game/World'
import type { ThemeColors } from '../../types'
import { DIFFICULTIES, DIFFICULTY_KEYS } from '../../config/difficulty'
import { THEME_DEFINITIONS } from '../../config/theme'
import { STAGES } from '../../config/stages'

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
  private hudLives: HTMLElement
  private hudStage: HTMLElement
  private hudEnemies: HTMLElement
  private hudHiScore: HTMLElement
  private overlay: HTMLElement
  private menuScreen: HTMLElement
  private pauseScreen: HTMLElement
  private gameOverScreen: HTMLElement
  private stageClearScreen: HTMLElement
  private victoryScreen: HTMLElement
  private recoveryScreen: HTMLElement
  private recoveryOptions: HTMLElement[] = []
  private footer: HTMLElement

  private currentScreen = 'menu'
  private animatedScore = 0
  private displayScore = 0

  constructor(root: HTMLElement) {
    this.root = root
    this.root.className = 'game-wrapper'

    // HUD bar (above canvas)
    this.hudBar = this.createElement('div', 'hud-bar')
    this.hudBar.innerHTML = `
      <div class="hud-group hud-left">
        <div class="hud-item">
          <span class="hud-label">SCORE</span>
          <span class="hud-value" data-hud="score">000000</span>
        </div>
        <div class="hud-item">
          <span class="hud-label">HI</span>
          <span class="hud-value hud-hi" data-hud="hiscore">000000</span>
        </div>
      </div>
      <div class="hud-group hud-center">
        <div class="hud-item">
          <span class="hud-label">STAGE</span>
          <span class="hud-value" data-hud="stage">01</span>
        </div>
      </div>
      <div class="hud-group hud-right">
        <div class="hud-item">
          <span class="hud-label">LIVES</span>
          <span class="hud-value hud-lives" data-hud="lives">♥♥♥</span>
        </div>
        <div class="hud-item">
          <span class="hud-label">ENEMY</span>
          <span class="hud-value" data-hud="enemies">20</span>
        </div>
      </div>
    `

    // Game container (canvas + overlay)
    const gameContainer = this.createElement('div', 'game-container')

    // Canvas (created by UIManager, managed by PresentationLayer)
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'game-canvas'
    gameContainer.appendChild(this.canvas)

    // Overlay (covers canvas)
    this.overlay = this.createElement('div', 'ui-overlay')

    // Menu screen
    this.menuScreen = this.createMenuScreen()

    // Pause screen
    this.pauseScreen = this.createElement('div', 'ui-screen ui-paused')
    this.pauseScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title">PAUSED</h2>
        <p class="ui-hint">Press <kbd>P</kbd> to resume</p>
      </div>
    `

    // Game over screen
    this.gameOverScreen = this.createElement('div', 'ui-screen ui-gameover')
    this.gameOverScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-danger">GAME OVER</h2>
        <p class="ui-hint">Press <kbd>R</kbd> or <kbd>Enter</kbd> to return to menu</p>
      </div>
    `

    // Stage clear screen
    this.stageClearScreen = this.createElement('div', 'ui-screen ui-stageclear')
    this.stageClearScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-success">STAGE CLEAR</h2>
        <p class="ui-stage-name" data-stage="name">Stage 1 Complete</p>
      </div>
    `

    // Victory screen
    this.victoryScreen = this.createElement('div', 'ui-screen ui-victory')
    this.victoryScreen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-success">VICTORY!</h2>
        <p class="ui-score-display">Final Score: <span data-victory="score">0</span></p>
        <p class="ui-hint">Press <kbd>R</kbd> or <kbd>Enter</kbd> to play again</p>
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

    // Footer
    this.footer = this.createElement('div', 'footer')
    this.footer.innerHTML = `
      <span>↑↓</span> Select &nbsp;·&nbsp;
      <span>←→</span> Change &nbsp;·&nbsp;
      <span>Enter</span> Start &nbsp;·&nbsp;
      <span>P</span> Pause &nbsp;·&nbsp;
      <span>R</span> Reset &nbsp;·&nbsp;
      <span>T</span> Theme
    `

    // Assemble
    this.root.appendChild(this.hudBar)
    gameContainer.appendChild(this.overlay)
    this.root.appendChild(gameContainer)
    this.root.appendChild(this.footer)

    // Cache elements
    this.hudScore = this.hudBar.querySelector('[data-hud="score"]')!
    this.hudLives = this.hudBar.querySelector('[data-hud="lives"]')!
    this.hudStage = this.hudBar.querySelector('[data-hud="stage"]')!
    this.hudEnemies = this.hudBar.querySelector('[data-hud="enemies"]')!
    this.hudHiScore = this.hudBar.querySelector('[data-hud="hiscore"]')!

    this.showScreen('menu')
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
          <h1 class="menu-title">BATTLE CITY</h1>
          <p class="menu-subtitle">Faithful to the classic. Designed for the future.</p>
        </div>
        <div class="menu-section">
          <div class="menu-row" data-menu="difficulty">
            <span class="menu-label">DIFFICULTY</span>
            <div class="menu-options" data-difficulty="options"></div>
          </div>
          <div class="menu-row" data-menu="theme">
            <span class="menu-label">THEME</span>
            <div class="menu-options" data-theme="options"></div>
          </div>
          <div class="menu-row" data-menu="stage">
            <span class="menu-label">STAGE</span>
            <div class="menu-stage-selector">
              <span class="menu-stage-arrow" data-stage="prev">◀</span>
              <span class="menu-stage-value" data-stage="value">01 / 35</span>
              <span class="menu-stage-arrow" data-stage="next">▶</span>
            </div>
            <span class="menu-stage-name" data-stage="name">Outpost</span>
          </div>
        </div>
        <div class="menu-start">
          <div class="menu-start-button">PRESS ENTER TO START</div>
        </div>
        <div class="menu-controls">
          <span>↑ ↓ Select Row</span>
          <span>← → Change</span>
          <span><kbd>T</kbd> Theme</span>
          <span><kbd>Enter</kbd> Start</span>
        </div>
        <div class="menu-hiscore">
          High Score: <span data-menu="hiscore">0</span>
        </div>
      </div>
    `

    // Populate difficulty options
    const diffContainer = screen.querySelector('[data-difficulty="options"]')!
    for (const key of DIFFICULTY_KEYS) {
      const diff = DIFFICULTIES[key]
      const opt = this.createElement('div', 'menu-option')
      opt.dataset.value = key
      opt.textContent = diff.name
      diffContainer.appendChild(opt)
    }

    // Populate theme options
    const themeContainer = screen.querySelector('[data-theme="options"]')!
    for (const def of THEME_DEFINITIONS) {
      const opt = this.createElement('div', 'menu-option')
      opt.dataset.value = def.key
      opt.textContent = def.name
      themeContainer.appendChild(opt)
    }

    return screen
  }

  /** Apply theme colors as CSS variables */
  applyTheme(colors: ThemeColors): void {
    const root = document.documentElement
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

    // Show/hide HUD based on state
    if (screen === 'menu' || screen === 'victory') {
      this.hudBar.classList.remove('visible')
    } else {
      this.hudBar.classList.add('visible')
    }

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
    // Animate score
    this.animatedScore = world.score
    if (this.displayScore !== this.animatedScore) {
      const diff = this.animatedScore - this.displayScore
      this.displayScore += Math.sign(diff) * Math.max(1, Math.abs(diff) * 0.15)
      if (Math.abs(this.animatedScore - this.displayScore) < 1) {
        this.displayScore = this.animatedScore
      }
    }

    // HUD
    this.hudScore.textContent = String(Math.round(this.displayScore)).padStart(6, '0')
    this.hudHiScore.textContent = String(world.highScore).padStart(6, '0')
    this.hudStage.textContent = String(world.stageIndex + 1).padStart(2, '0')
    this.hudEnemies.textContent = String(world.enemiesRemaining)

    // Lives as hearts
    const hearts = '♥'.repeat(Math.max(0, world.lives))
    this.hudLives.textContent = hearts || '—'

    // Menu state
    this.updateMenu(world)

    // Stage clear
    const stageName = this.stageClearScreen.querySelector('[data-stage="name"]')
    if (stageName) {
      stageName.textContent = `Stage ${world.stageIndex + 1}: ${world.currentStageName} Complete`
    }

    // Victory
    const victoryScore = this.victoryScreen.querySelector('[data-victory="score"]')
    if (victoryScore) {
      victoryScore.textContent = String(world.score)
    }

    // Recovery screen
    this.updateRecovery(world)

    // Show correct screen
    this.showScreen(world.state)
  }

  private updateMenu(world: World): void {
    // Highlight selected difficulty
    const diffOptions = this.menuScreen.querySelectorAll('[data-difficulty="options"] .menu-option')
    diffOptions.forEach((el) => {
      const opt = el as HTMLElement
      if (opt.dataset.value === world.difficultyKey) {
        opt.classList.add('selected')
      } else {
        opt.classList.remove('selected')
      }
    })

    // Highlight selected theme
    const themeOptions = this.menuScreen.querySelectorAll('[data-theme="options"] .menu-option')
    themeOptions.forEach((el) => {
      const opt = el as HTMLElement
      if (opt.dataset.value === world.themeKey) {
        opt.classList.add('selected')
      } else {
        opt.classList.remove('selected')
      }
    })

    // Highlight selected menu row (cursor)
    const menuRows = this.menuScreen.querySelectorAll('.menu-row')
    menuRows.forEach((el) => {
      const row = el as HTMLElement
      const idx =
        row.dataset.menu === 'difficulty'
          ? 0
          : row.dataset.menu === 'theme'
            ? 1
            : row.dataset.menu === 'stage'
              ? 2
              : -1
      row.classList.toggle('selected', idx === world.menuCursor)
    })

    // Stage selector display
    const stageValue = this.menuScreen.querySelector('[data-stage="value"]')
    if (stageValue) {
      stageValue.textContent = `${String(world.selectedStage + 1).padStart(2, '0')} / ${String(STAGES.length).padStart(2, '0')}`
    }
    const stageName = this.menuScreen.querySelector('[data-stage="name"]')
    if (stageName) {
      stageName.textContent = STAGES[world.selectedStage]?.name ?? ''
    }

    // High score
    const hiScore = this.menuScreen.querySelector('[data-menu="hiscore"]')
    if (hiScore) {
      hiScore.textContent = String(world.highScore)
    }
  }

  // ---- Recovery Screen ----

  private createRecoveryScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-recovery')
    screen.innerHTML = `
      <div class="recovery-menu" data-recovery="menu">
        <h2 class="recovery-title ui-danger">MISSION FAILED</h2>
        <p class="recovery-subtitle">Rewind time and try again</p>
        <div class="recovery-options">
          <div class="recovery-option" data-recovery-option="0">
            <span class="recovery-option-icon">⏪</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label">30 Seconds Ago</span>
              <span class="recovery-option-desc">Restore recent gameplay</span>
            </div>
          </div>
          <div class="recovery-option" data-recovery-option="1">
            <span class="recovery-option-icon">⏪⏪</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label">60 Seconds Ago</span>
              <span class="recovery-option-desc">Restore oldest available</span>
            </div>
          </div>
          <div class="recovery-option" data-recovery-option="2">
            <span class="recovery-option-icon">↻</span>
            <div class="recovery-option-text">
              <span class="recovery-option-label">Restart Stage</span>
              <span class="recovery-option-desc">Return to stage start</span>
            </div>
          </div>
        </div>
        <div class="recovery-controls">
          <span>↑ ↓ Select</span>
          <span><kbd>Enter</kbd> Confirm</span>
          <span><kbd>R</kbd> Menu</span>
        </div>
      </div>
      <div class="recovery-countdown" data-recovery="countdown">
        <span class="countdown-number" data-recovery="countdown-number">3</span>
        <span class="countdown-label">READY</span>
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
      const numEl = this.recoveryScreen.querySelector('[data-recovery="countdown-number"]')
      if (numEl) {
        numEl.textContent = String(world.recoveryCountdown)
      }
      return
    }

    // Update option selection and availability
    for (let i = 0; i < this.recoveryOptions.length; i++) {
      const opt = this.recoveryOptions[i]
      opt.classList.toggle('selected', i === world.recoveryCursor)
      // The Game / RecoverySystem determines availability; we just read
      // the history size via the option being non-disabled by default.
      // Actual availability is checked on confirm in Game.handleRecoveryInput.
    }
  }

  /** Get the footer element */
  getFooter(): HTMLElement {
    return this.footer
  }
}
