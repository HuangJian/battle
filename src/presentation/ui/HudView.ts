import { TICK_MS } from '../../constants'
import type { World } from '../../game/World'
import type { KeyBindings } from '../../types'
import { parseBinding } from '../../game/Input'
import { localizedStageName } from '../../config/stages'
import { t } from '../../i18n'

/**
 * HudView — the in-game HUD bar and its per-frame sync logic
 * (plan/refactor.agy.md §2.4). Owns every `[data-hud]` element: score /
 * hi-score / stage / enemies / lives (+ coop God row), star level, spectate
 * & replay badges, battle-speed chip, timed-buff chips, super-item counters,
 * and the Take Over button.
 *
 * DOM writes are change-guarded (`last*` mirrors) so a 60 FPS sync only
 * touches the DOM when a displayed value actually changed. Bodies were
 * extracted verbatim from UIManager (§256 slice pattern).
 */
export class HudView {
  readonly el: HTMLElement

  // Cached HUD elements
  private scoreEl: HTMLElement
  private score2El: HTMLElement | null = null
  private score2Wrap: HTMLElement | null = null
  private livesEl: HTMLElement
  private lives2El: HTMLElement | null = null
  private coopLivesEl: HTMLElement | null = null
  private stageEl: HTMLElement
  /** Localized stage name shown under the stage number in the HUD center. */
  private stageNameEl: HTMLElement
  private enemiesEl: HTMLElement
  private hiScoreEl: HTMLElement
  private starEl: HTMLElement
  private replayBadge: HTMLElement
  private replayDifficultyEl: HTMLElement
  /** 督战 (supervise) HUD badge — visible while God AI fights as player1. */
  private spectateBadge: HTMLElement
  /** Take Over button — visible when paused in spectate mode. */
  private takeoverBtn: HTMLElement
  /** 督战 battle-speed chip (×1.5 / ×2 / ×4) — hidden at ×1. */
  private speedChip: HTMLElement
  private speedValueEl: HTMLElement
  private buffShield: HTMLElement
  private buffShieldTime: HTMLElement
  private buffFreeze: HTMLElement
  private buffFreezeTime: HTMLElement
  private buffFence: HTMLElement
  private buffFenceTime: HTMLElement
  // Super power-up inventory counters (DECISIONS.md §31)
  private guardEl: HTMLElement
  private frenzyEl: HTMLElement
  private sacrificeEl: HTMLElement
  private rewindEl: HTMLElement
  private superItems: HTMLElement[]
  /** Super item key labels (dynamic, reflect rebound keys + locale). */
  readonly guardLabel: HTMLElement | null
  readonly frenzyLabel: HTMLElement | null
  readonly rewindLabel: HTMLElement | null

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
  private lastGuard = -1
  private lastFrenzy = -1
  private lastSacrifice = -1
  private lastRewind = -1
  // Buff countdowns: remaining whole seconds last written (-1 = chip hidden).
  private lastShieldSec = -1
  private lastFreezeSec = -1
  private lastFenceSec = -1
  private lastSpectate = false
  private lastBattleSpeed = 1

  // Score animation state
  private animatedScore = 0
  private displayScore = 0

  /**
   * @param createElement shared element factory (owned by UIManager)
   * @param onTakeoverClick click handler for the Take Over button — the host
   *   routes it to the replay or spectate callback depending on active mode.
   */
  constructor(
    createElement: (tag: string, className: string) => HTMLElement,
    onTakeoverClick: () => void,
  ) {
    this.el = createElement('div', 'hud-bar')
    this.el.innerHTML = `
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

    // Cache elements (same selectors as the original UIManager constructor)
    const q = (sel: string) => this.el.querySelector(sel) as HTMLElement
    this.scoreEl = q('[data-hud="score"]')
    this.score2El = q('[data-hud="score2"]')
    this.score2Wrap = q('[data-hud="score2-wrap"]')
    this.livesEl = q('[data-hud="lives"]')
    this.lives2El = q('[data-hud="lives2"]')
    this.coopLivesEl = q('[data-hud="coop-lives"]')
    this.stageEl = q('[data-hud="stage"]')
    this.stageNameEl = q('[data-hud="stage-name"]')
    this.enemiesEl = q('[data-hud="enemies"]')
    this.hiScoreEl = q('[data-hud="hiscore"]')
    this.starEl = q('[data-hud="star"]')
    this.replayBadge = q('[data-hud="replay"]')
    this.replayDifficultyEl = q('[data-hud="replay-difficulty"]')
    this.spectateBadge = q('[data-hud="spectate"]')
    this.takeoverBtn = q('[data-hud="takeover"]')
    this.speedChip = q('[data-hud="speed"]')
    this.speedValueEl = q('[data-hud="speed-value"]')
    this.guardEl = q('[data-hud="guard"]')
    this.frenzyEl = q('[data-hud="frenzy"]')
    this.sacrificeEl = q('[data-hud="sacrifice"]')
    this.rewindEl = q('[data-hud="rewind"]')
    this.superItems = Array.from(this.el.querySelectorAll('.hud-super'))
    this.guardLabel = this.el.querySelector('[data-hud-super-label="guard"]')
    this.frenzyLabel = this.el.querySelector('[data-hud-super-label="frenzy"]')
    this.rewindLabel = this.el.querySelector('[data-hud-super-label="rewind"]')
    this.buffShield = q('[data-buff="shield"]')
    this.buffShieldTime = q('[data-buff-time="shield"]')
    this.buffFreeze = q('[data-buff="freeze"]')
    this.buffFreezeTime = q('[data-buff-time="freeze"]')
    this.buffFence = q('[data-buff="fence"]')
    this.buffFenceTime = q('[data-buff-time="fence"]')

    // Take Over entry point — the host decides replay vs spectate routing.
    this.takeoverBtn.addEventListener('click', onTakeoverClick)
  }

  /** Show/hide the whole HUD bar (menu & victory hide it). */
  setVisible(visible: boolean): void {
    this.el.classList.toggle('visible', visible)
  }

  /** Show or hide the persistent REPLAY indicator in the HUD center area. */
  setReplayBadge(isReplay: boolean, difficulty?: string): void {
    this.replayBadge.hidden = !isReplay
    this.replayDifficultyEl.textContent = isReplay ? (difficulty ?? '') : ''
  }

  /** Show the live battle-speed chip (hidden at ×1). */
  setBattleSpeed(speed: number): void {
    if (speed === this.lastBattleSpeed) return
    this.lastBattleSpeed = speed
    this.speedChip.hidden = speed === 1
    this.speedValueEl.textContent = `×${speed}`
  }

  /** Toggle the `.paused` badge + Take Over button visibility (per-frame). */
  setPauseState(paused: boolean, takeoverVisible: boolean): void {
    this.el.classList.toggle('paused', paused)
    this.takeoverBtn.hidden = !takeoverVisible
  }

  /** Re-render the super-item key labels from the current bindings + locale. */
  updateSuperKeyLabels(bindings: KeyBindings): void {
    const pairs: Array<[HTMLElement | null, keyof KeyBindings, string]> = [
      [this.guardLabel, 'guard', t('hud.guard')],
      [this.frenzyLabel, 'frenzy', t('hud.frenzy')],
      [this.rewindLabel, 'rewind', t('hud.rewind')],
    ]
    for (const [el, action, name] of pairs) {
      if (el) el.textContent = `${name}<${formatKeyCode(parseBinding(bindings[action]).code)}>`
    }
  }

  /**
   * Per-frame HUD sync from world state. Change-guarded throughout —
   * extracted verbatim from UIManager.update ("Animate score" … buffs).
   */
  syncWorld(world: World): void {
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
      this.scoreEl.textContent = String(scoreVal).padStart(6, '0')
      this.lastScore = scoreVal
    }
    if (world.highScore !== this.lastHiScore) {
      this.hiScoreEl.textContent = String(world.highScore).padStart(6, '0')
      this.lastHiScore = world.highScore
    }
    if (world.stageIndex !== this.lastStage) {
      this.stageEl.textContent = String(world.stageIndex + 1).padStart(2, '0')
      this.lastStage = world.stageIndex
      this.lastStageName = '' // force the name to re-render for the new stage
    }
    // Stage name (localized) — re-render when the stage changes OR when the
    // active language changed (the number guard above leaves same-stage
    // language switches untouched, so check the name string too).
    const stageName = localizedStageName(world.stageIndex)
    if (stageName !== this.lastStageName) {
      this.stageNameEl.textContent = stageName
      this.lastStageName = stageName
    }
    if (world.enemiesRemaining !== this.lastEnemies) {
      this.enemiesEl.textContent = String(world.enemiesRemaining)
      this.lastEnemies = world.enemiesRemaining
    }
    // 督战 (supervise) badge — synced from the World so toggle, stage restore,
    // and startGame resets all converge on the same source of truth.
    if (world.spectate !== this.lastSpectate) {
      this.lastSpectate = world.spectate
      this.spectateBadge.hidden = !world.spectate
    }
    if (world.lives !== this.lastLives) {
      const hearts = '♥'.repeat(Math.max(0, world.lives))
      this.livesEl.textContent = hearts || '—'
      this.lastLives = world.lives
    }
    // Co-op God score (Lie-Back-Win-Mode Q1)
    if (this.score2Wrap && this.score2El) {
      const showScore2 = world.coop
      this.score2Wrap.style.display = showScore2 ? '' : 'none'
      if (showScore2 && world.score2 !== this.lastScore2) {
        this.score2El.textContent = String(world.score2).padStart(6, '0')
        this.lastScore2 = world.score2
      }
    }
    // Co-op God lives (Lie-Back-Win-Mode)
    if (this.coopLivesEl && this.lives2El) {
      const showCoop = world.coop && world.lives2 > 0
      this.coopLivesEl.style.display = showCoop ? '' : 'none'
      if (showCoop && world.lives2 !== this.lastLives2) {
        this.lives2El.textContent = '♥'.repeat(Math.max(0, world.lives2))
        this.lastLives2 = world.lives2
      }
    }

    // Player star level (★ power-up). Show only filled stars; no empty
    // placeholders. If the player has no stars, show nothing.
    if (world.playerLevel !== this.lastStar) {
      const lvl = Math.max(0, world.playerLevel)
      this.starEl.textContent = lvl > 0 ? '★'.repeat(lvl) : '--'
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
        this.guardEl.textContent = String(world.guardStock)
        this.lastGuard = world.guardStock
      }
      if (world.frenzyStock !== this.lastFrenzy) {
        this.frenzyEl.textContent = String(world.frenzyStock)
        this.lastFrenzy = world.frenzyStock
      }
      if (world.sacrificeStock !== this.lastSacrifice) {
        this.sacrificeEl.textContent = String(world.sacrificeStock)
        this.lastSacrifice = world.sacrificeStock
      }
      if (world.rewindStock !== this.lastRewind) {
        this.rewindEl.textContent = String(world.rewindStock)
        this.lastRewind = world.rewindStock
      }
    }

    // Active timed buffs (shield / freeze) — countdown shown outside the field
    this.updateBuffs(world)
  }

  /**
   * Update the timed-buff countdown chips in the HUD. Time-limited buffs:
   * SHIELD (spawn protection), FREEZE (freeze/clock pickup), and FENCE
   * (steel ring). Star / extra life / bomb are instant or permanent and
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
        ? (world.fenceExpireFrame - world.frame) * TICK_MS
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
}

/** Render a bare `KeyboardEvent.code` (no modifiers) into a short label. */
export function formatKeyCode(code: string): string {
  if (code.startsWith('Arrow')) {
    return (
      ({ ArrowUp: '↑', ArrowDown: '↓', ArrowLeft: '←', ArrowRight: '→' } as Record<string, string>)[
        code
      ] ?? code
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
