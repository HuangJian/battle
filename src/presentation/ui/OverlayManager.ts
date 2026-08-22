import type { World } from '../../game/World'
import { RECOVERY_OPTION_COUNT } from '../../snapshot/RecoveryController'
import { t } from '../../i18n'

/**
 * OverlayManager — the game-state overlay screens: PAUSED, GAME OVER,
 * STAGE CLEAR, VICTORY, and the MISSION FAILED recovery menu with its
 * countdown/fade sub-states (plan/refactor.agy.md §2.4). Extracted
 * verbatim from UIManager (§256 slice pattern).
 *
 * Screen activation (`showScreen`) stays on UIManager — it also owns the
 * overlay visibility, HUD bar visibility and footer hints that switch in
 * lockstep — but every overlay's DOM lives here.
 */
export class OverlayManager {
  readonly pauseScreen: HTMLElement
  readonly gameOverScreen: HTMLElement
  readonly stageClearScreen: HTMLElement
  readonly victoryScreen: HTMLElement
  readonly recoveryScreen: HTMLElement

  /** Nameable screen map for UIManager.showScreen(). */
  get screens(): Record<string, HTMLElement> {
    return {
      paused: this.pauseScreen,
      gameover: this.gameOverScreen,
      stageclear: this.stageClearScreen,
      victory: this.victoryScreen,
      recovery: this.recoveryScreen,
    }
  }

  private recoveryOptions: HTMLElement[] = []
  /** Per-option availability, set by Game while the recovery menu is open. */
  private recoveryAvailability: boolean[] = []

  private stageClearName: HTMLElement | null = null
  private victoryScoreEl: HTMLElement | null = null
  private countdownNum: HTMLElement | null = null

  // Change guards — only write while actually on the screen AND when changed
  private lastStageClear = ''
  private lastVictory = ''

  constructor(private readonly createElement: (tag: string, className: string) => HTMLElement) {
    this.pauseScreen = this.createPauseScreen()
    this.gameOverScreen = this.createGameOverScreen()
    this.stageClearScreen = this.createStageClearScreen()
    this.victoryScreen = this.createVictoryScreen()
    this.recoveryScreen = this.createRecoveryScreen()
    this.cacheElements()
  }

  private createPauseScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-paused')
    screen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title" data-i18n="pause.title">PAUSED</h2>
        <p class="ui-hint" data-i18n="pause.hint">Press P to resume</p>
      </div>
    `
    return screen
  }

  private createGameOverScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-gameover')
    screen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-danger" data-i18n="gameover.title">GAME OVER</h2>
        <p class="ui-hint" data-i18n="gameover.hint">Press Alt+R or Enter to return to menu</p>
      </div>
    `
    return screen
  }

  private createStageClearScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-stageclear')
    screen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-success" data-i18n="stageclear.title">STAGE CLEAR</h2>
        <p class="ui-stage-name" data-stage="name">Stage 1 Complete</p>
      </div>
    `
    return screen
  }

  private createVictoryScreen(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-victory')
    screen.innerHTML = `
      <div class="ui-panel">
        <h2 class="ui-title ui-success" data-i18n="victory.title">VICTORY!</h2>
        <p class="ui-score-display">Final Score: <span data-victory="score">0</span></p>
        <p class="ui-hint" data-i18n="victory.hint">Press Alt+R or Enter to play again</p>
      </div>
    `
    return screen
  }

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

  private cacheElements(): void {
    this.stageClearName = this.stageClearScreen.querySelector('[data-stage="name"]')
    this.victoryScoreEl = this.victoryScreen.querySelector('[data-victory="score"]')
    this.countdownNum = this.recoveryScreen.querySelector('[data-recovery="countdown-number"]')
  }

  /** Reset recovery sub-state classes when leaving the recovery flow. */
  clearRecoverySubState(): void {
    this.recoveryScreen.classList.remove('fading', 'countdown')
  }

  /**
   * Publish per-option availability for the recovery menu. Called by Game
   * when entering the recovery flow (Game asks the RecoveryController —
   * the UI layer itself never inspects snapshots).
   */
  setRecoveryAvailability(availability: boolean[]): void {
    this.recoveryAvailability = availability.slice(0, RECOVERY_OPTION_COUNT)
  }

  /** Per-frame overlay text sync (stage-clear / victory / recovery states). */
  syncWorld(world: World): void {
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
  }

  /** Update recovery overlay from world state */
  private updateRecovery(world: World): void {
    if (world.state !== 'recovery') return

    // Toggle fading / countdown classes
    this.recoveryScreen.classList.toggle('fading', world.ui.recoveryFading)

    const isCountdown = world.ui.recoveryCountdown > 0
    this.recoveryScreen.classList.toggle('countdown', isCountdown)

    // Show countdown number
    if (isCountdown) {
      if (this.countdownNum) {
        this.countdownNum.textContent = String(world.ui.recoveryCountdown)
      }
      return
    }

    // Update option selection and availability
    for (let i = 0; i < this.recoveryOptions.length; i++) {
      const opt = this.recoveryOptions[i]
      opt.classList.toggle('selected', i === world.ui.recoveryCursor)
      // Availability comes from the RecoveryController (via Game) — a
      // disabled option is greyed out but still selectable (soft-denied
      // on confirm), matching the classic menu feel.
      const available = this.recoveryAvailability[i] ?? true
      opt.classList.toggle('disabled', !available)
    }
  }
}
