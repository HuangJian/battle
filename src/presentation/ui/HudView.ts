import { TICK_MS } from '../../constants'
import type { World } from '../../game/World'
import type { KeyBindings } from '../../types'
import { parseBinding } from '../../game/Input'
import { localizedStageName } from '../../config/stages'
import { t, localizeRoot } from '../../i18n'
import { superStockTotal } from '../../game/superStocks'

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
  /** Shared element factory (owned by UIManager) — also used to lazily
   *  create the P2 super-key label rows (2p-review P0-1). */
  private readonly createElement: (tag: string, className: string) => HTMLElement

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
  /** P2's separate inventory counters (双打/躺赢 per-player split — shown
   *  only while a player2 tank exists; gold, after the P2 key chip). */
  private guard2El: HTMLElement
  private frenzy2El: HTMLElement
  private sacrifice2El: HTMLElement
  private rewind2El: HTMLElement
  /** Rail header stock total (`×N`, sum of BOTH players' inventories). */
  private superTotalEl: HTMLElement
  /**
   * The super-item inventory — a SIDE RAIL beside the playfield (owned here,
   * placed by UIManager next to the game container). Visible only when the
   * mode has super items (non-classic) AND the HUD is up. Public so the host
   * can measure it (resizeCanvas reserves its width) and place it.
   */
  readonly superRail: HTMLElement
  private superItems: HTMLElement[]
  /** HUD bar visibility as of the last setVisible (mirrors .visible class). */
  private hudVisible = false
  /** Classic mode (no 强力道具) — last value written, drives the rail hide. */
  private hideSuper = false
  /** Super item key labels (dynamic, reflect rebound keys + locale). */
  readonly guardLabel: HTMLElement | null
  readonly frenzyLabel: HTMLElement | null
  readonly rewindLabel: HTMLElement | null
  /** 双打 Two-Player: P2's own super-key labels (colored rows). Created
   *  lazily by updateSuperKeyLabels when P2 bindings are first supplied. */
  private guardLabel2: HTMLElement | null = null
  private frenzyLabel2: HTMLElement | null = null
  private rewindLabel2: HTMLElement | null = null
  /** Mirrors world.twoPlayer as of the last syncWorld — drives the P2 label
   *  rows' visibility (the flag can flip without any binding/locale change). */
  private twoPlayerLabels = false

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
  private lastGuard2 = -1
  private lastFrenzy2 = -1
  private lastSacrifice2 = -1
  private lastRewind2 = -1
  private lastSuperTotal = -1
  /** Whether the P2 stock counters are currently shown (mirror). */
  private p2StocksShown = false
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
   * @param onSuperRailToggle fired (change-guarded) whenever the super-item
   *   rail's VISIBLE state flips — the host re-sizes the canvas because the
   *   rail reserves real horizontal space beside the playfield.
   */
  constructor(
    createElement: (tag: string, className: string) => HTMLElement,
    onTakeoverClick: () => void,
    private readonly onSuperRailToggle?: () => void,
  ) {
    this.createElement = createElement
    this.el = createElement('div', 'hud-bar')
    this.el.innerHTML = `
      <div class="hud-top-row">
        <div class="hud-group hud-left">
          <div class="hud-item">
            <span class="hud-label" data-i18n="hud.score">SCORE</span>
            <span class="hud-value" data-hud="score">000000</span>
          </div>
          <div class="hud-item" data-hud="score2-wrap" style="display:none">
            <span class="hud-label hud-p2-label" data-i18n="hud.god">GOD</span>
            <span class="hud-value hud-p2-value" data-hud="score2">000000</span>
          </div>
          <div class="hud-item">
            <span class="hud-label" data-i18n="hud.hi">HI</span>
            <span class="hud-value hud-hi" data-hud="hiscore">000000</span>
          </div>
          <div class="hud-item">
            <span class="hud-label" data-i18n="hud.star">STAR</span>
            <span class="hud-value hud-star" data-hud="star"></span>
          </div>
        </div>
        <div class="hud-group hud-center">
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
            <span class="hud-label" data-i18n="hud.stage">STAGE</span>
            <span class="hud-value" data-hud="stage">01</span>
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
            <span class="hud-label hud-p2-label" data-i18n="hud.god">GOD</span>
            <span class="hud-value hud-lives hud-p2-value" data-hud="lives2">—</span>
          </div>
          <div class="hud-item">
            <span class="hud-label" data-i18n="hud.enemy">ENEMY</span>
            <span class="hud-value" data-hud="enemies">20</span>
          </div>
        </div>
      </div>
    `

    // Super-item inventory — a SIDE RAIL beside the playfield (not part of the
    // HUD bar): a real layout sibling of the canvas, visible only in non-
    // classic modes (superDropChance > 0) while the HUD is up. Building it as
    // a separate element lets UIManager place it next to the game container.
    this.superRail = createElement('div', 'hud-super-rail')
    this.superRail.hidden = true
    this.superRail.innerHTML = `
      <div class="hud-super-head">
        <span class="hud-super-title" data-i18n="hud.superItems">SUPER ITEMS</span>
        <span class="hud-super-total" data-hud="super-total">×0</span>
      </div>
      <div class="hud-item hud-super">
        <span class="hud-super-icon">🛡</span>
        <span class="hud-label" data-hud-super-label="guard">Guardian&lt;F5&gt;</span>
        <span class="hud-value" data-hud="guard">0</span>
        <span class="hud-super-p2-stock" data-hud="guard2" hidden>0</span>
      </div>
      <div class="hud-item hud-super">
        <span class="hud-super-icon">❄</span>
        <span class="hud-label" data-hud-super-label="frenzy">Frenzy&lt;F6&gt;</span>
        <span class="hud-value" data-hud="frenzy">0</span>
        <span class="hud-super-p2-stock" data-hud="frenzy2" hidden>0</span>
      </div>
      <div class="hud-item hud-super">
        <span class="hud-super-icon">💥</span>
        <span class="hud-label" data-i18n="hud.sacrifice">同归</span>
        <span class="hud-value" data-hud="sacrifice">0</span>
        <span class="hud-super-p2-stock" data-hud="sacrifice2" hidden>0</span>
      </div>
      <div class="hud-item hud-super">
        <span class="hud-super-icon">⏱</span>
        <span class="hud-label" data-hud-super-label="rewind">Time Box&lt;F7&gt;</span>
        <span class="hud-value" data-hud="rewind">0</span>
        <span class="hud-super-p2-stock" data-hud="rewind2" hidden>0</span>
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
    // The super-item stock counters live in the RAIL (beside the playfield),
    // not in the hud-bar — query them there.
    const rq = (sel: string) => this.superRail.querySelector(sel) as HTMLElement
    this.guardEl = rq('[data-hud="guard"]')
    this.frenzyEl = rq('[data-hud="frenzy"]')
    this.sacrificeEl = rq('[data-hud="sacrifice"]')
    this.rewindEl = rq('[data-hud="rewind"]')
    this.guard2El = rq('[data-hud="guard2"]')
    this.frenzy2El = rq('[data-hud="frenzy2"]')
    this.sacrifice2El = rq('[data-hud="sacrifice2"]')
    this.rewind2El = rq('[data-hud="rewind2"]')
    this.superTotalEl = rq('[data-hud="super-total"]')
    this.superItems = Array.from(this.superRail.querySelectorAll('.hud-super'))
    this.guardLabel = this.superRail.querySelector('[data-hud-super-label="guard"]')
    this.frenzyLabel = this.superRail.querySelector('[data-hud-super-label="frenzy"]')
    this.rewindLabel = this.superRail.querySelector('[data-hud-super-label="rewind"]')
    this.buffShield = q('[data-buff="shield"]')
    this.buffShieldTime = q('[data-buff-time="shield"]')
    this.buffFreeze = q('[data-buff="freeze"]')
    this.buffFreezeTime = q('[data-buff-time="freeze"]')
    this.buffFence = q('[data-buff="fence"]')
    this.buffFenceTime = q('[data-buff-time="fence"]')

    // Take Over entry point — the host decides replay vs spectate routing.
    this.takeoverBtn.addEventListener('click', onTakeoverClick)
  }

  /** Show/hide the whole HUD bar (menu & victory hide it). The super-item
   *  rail follows the same visibility (it is HUD chrome, not a menu widget). */
  setVisible(visible: boolean): void {
    this.el.classList.toggle('visible', visible)
    if (this.hudVisible !== visible) {
      this.hudVisible = visible
      this.applySuperRailVisibility()
    }
  }

  /**
   * Single writer for the super-item rail's visibility: shown only while the
   * HUD is up AND the mode actually has super items (non-classic). The change
   * is change-guarded and fires the host callback — the rail reserves real
   * horizontal space beside the playfield, so the canvas must be re-sized
   * when it appears/disappears (classic ↔ non-classic, menu ↔ play).
   */
  private applySuperRailVisibility(): void {
    const hidden = !this.hudVisible || this.hideSuper
    if (this.superRail.hidden === hidden) return
    this.superRail.hidden = hidden
    this.onSuperRailToggle?.()
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

  /** Re-render the super-item key labels from the current bindings + locale.
   *  When P2 bindings are provided, a second colored row per item is rendered
   *  for two-player mode (stocks are world-global — either human may spend
   *  one — so each player's own release key is shown). The P2 rows are
   *  CREATED lazily on the first call that supplies P2 bindings (2p-review
   *  P0-1: the update/show-hide logic shipped without the create step, so
   *  the rows never appeared). */
  updateSuperKeyLabels(bindings: KeyBindings, bindings2?: KeyBindings): void {
    const actions: Array<['guard' | 'frenzy' | 'rewind', string]> = [
      ['guard', t('hud.guard')],
      ['frenzy', t('hud.frenzy')],
      ['rewind', t('hud.rewind')],
    ]
    // P1 rows (always present in the template).
    for (const [action, name] of actions) {
      const el =
        action === 'guard'
          ? this.guardLabel
          : action === 'frenzy'
            ? this.frenzyLabel
            : this.rewindLabel
      if (el) el.textContent = formatSuperKeyLabel(name, bindings[action])
    }
    // P2 rows — lazily created, then kept in sync. Visible only in
    // two-player mode; syncWorld mirrors the flag so a mode flip without a
    // label change still shows/hides the rows.
    if (bindings2) {
      for (const [action] of actions) {
        const el2 = this.ensureP2Label(action)
        if (el2) {
          // The P2 chip shows only the bare KEY (gold chip after the stock
          // counter) — P1's label already names the item, so a second full
          // "Guardian<R>" row would only add vertical clutter (HUD redesign).
          el2.textContent = formatKeyCode(parseBinding(bindings2[action]).code)
          el2.hidden = !this.twoPlayerLabels
        }
      }
    }
  }

  /** Lazily create (and cache) the P2 release-key chip inside the super item.
   *  A small gold key chip appended AFTER the stock counter — "Guardian<F5> 2
   *  [R]" — so two-player mode gains the P2 hint on the SAME line instead of
   *  a third stacked row (HUD redesign). Created hidden and flipped by
   *  syncWorld/updates. */
  private ensureP2Label(action: 'guard' | 'frenzy' | 'rewind'): HTMLElement | null {
    const p1 =
      action === 'guard'
        ? this.guardLabel
        : action === 'frenzy'
          ? this.frenzyLabel
          : this.rewindLabel
    const existing =
      action === 'guard'
        ? this.guardLabel2
        : action === 'frenzy'
          ? this.frenzyLabel2
          : this.rewindLabel2
    if (existing) return existing
    const item = p1?.parentElement
    if (!item) return null
    const el = this.createElement('span', 'hud-super-p2')
    el.hidden = !this.twoPlayerLabels
    // Insert after the stock counter so the P2 key reads as a trailing hint.
    const valueEl = item.querySelector('.hud-value')
    if (valueEl) valueEl.after(el)
    else item.appendChild(el)
    if (action === 'guard') this.guardLabel2 = el
    else if (action === 'frenzy') this.frenzyLabel2 = el
    else this.rewindLabel2 = el
    return el
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
    // Co-op God score (Lie-Back-Win-Mode Q1) — 双打 twoPlayer shows P2's
    // human score in the same slot.
    if (this.score2Wrap && this.score2El) {
      const showScore2 = world.coop || world.twoPlayer
      this.score2Wrap.style.display = showScore2 ? '' : 'none'
      if (showScore2 && world.score2 !== this.lastScore2) {
        this.score2El.textContent = String(world.score2).padStart(6, '0')
        this.lastScore2 = world.score2
      }
    }
    // Co-op God lives (Lie-Back-Win-Mode) — 双打 twoPlayer shares the slot;
    // the label switches from GOD to P2 for the human driver.
    if (this.coopLivesEl && this.lives2El) {
      const showCoop = (world.coop || world.twoPlayer) && world.lives2 > 0
      this.coopLivesEl.style.display = showCoop ? '' : 'none'
      if (showCoop && world.lives2 !== this.lastLives2) {
        this.lives2El.textContent = '♥'.repeat(Math.max(0, world.lives2))
        this.lastLives2 = world.lives2
      }
      if (this.coopLivesEl.dataset.mode !== (world.twoPlayer ? '2p' : 'god')) {
        this.coopLivesEl.dataset.mode = world.twoPlayer ? '2p' : 'god'
        const label = this.coopLivesEl.querySelector('.hud-label')
        if (label) label.setAttribute('data-i18n', world.twoPlayer ? 'hud.p2' : 'hud.god')
        if (label) localizeRoot(this.coopLivesEl)
      }
    }

    // Player star level (★ power-up). Show only filled stars; no empty
    // placeholders. If the player has no stars, show nothing. Levels beyond
    // MAX_HUD_STARS are clamped to a compact "★★★★★+N" so an unbounded star
    // string can never widen or wrap the HUD row (HUD redesign).
    if (world.playerLevel !== this.lastStar) {
      this.starEl.textContent = formatStarLevel(world.playerLevel)
      this.lastStar = world.playerLevel
    }

    // Super power-up inventory counters (DECISIONS.md §31). Written only when
    // the count actually changes. In classic mode (no 强力道具) the whole rail
    // is hidden — it lives beside the playfield, not in the HUD bar (HUD
    // redesign follow-up: the rail appears only when the mode has super items).
    const hideSuper = world.rules.superDropChance === 0
    this.hideSuper = hideSuper
    this.applySuperRailVisibility()
    for (const el of this.superItems) {
      if (el.hidden !== hideSuper) el.hidden = hideSuper
    }
    // P2's stock counters (per-player inventories, superStocks.ts): shown
    // whenever a player2 tank exists (双打 human / coop God AI / dual
    // spectate) AND the mode has super items — change-guarded so a P2
    // enable/disable flip without a stock change still shows/hides them.
    const p2StocksShown = !!world.player2 && !hideSuper
    if (this.p2StocksShown !== p2StocksShown) {
      this.p2StocksShown = p2StocksShown
      for (const el of [this.guard2El, this.frenzy2El, this.sacrifice2El, this.rewind2El]) {
        el.hidden = !p2StocksShown
      }
    }

    // 双打 Two-Player: show/hide the P2 super-key label rows. The flag can
    // flip (CC toggle / snapshot restore) without any binding or locale
    // change, so this change-guarded sync lives here, not only in
    // updateSuperKeyLabels. Rows mirror the P1 items' classic-mode hiding.
    if (this.twoPlayerLabels !== world.twoPlayer) {
      this.twoPlayerLabels = world.twoPlayer
      for (const el of [this.guardLabel2, this.frenzyLabel2, this.rewindLabel2]) {
        if (el) el.hidden = !world.twoPlayer || hideSuper
      }
    } else if (!world.twoPlayer) {
      // classic-mode gate above flipped while 2p rows are already hidden —
      // nothing to do (they only show when twoPlayer && !hideSuper).
    } else {
      // twoPlayer unchanged and on: follow the classic-mode hiding.
      for (const el of [this.guardLabel2, this.frenzyLabel2, this.rewindLabel2]) {
        if (el && el.hidden !== hideSuper) el.hidden = hideSuper
      }
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
      // P2's own counters (hidden elements stay in sync so a flip-in shows
      // the current value immediately).
      if (world.guardStock2 !== this.lastGuard2) {
        this.guard2El.textContent = String(world.guardStock2)
        this.lastGuard2 = world.guardStock2
      }
      if (world.frenzyStock2 !== this.lastFrenzy2) {
        this.frenzy2El.textContent = String(world.frenzyStock2)
        this.lastFrenzy2 = world.frenzyStock2
      }
      if (world.sacrificeStock2 !== this.lastSacrifice2) {
        this.sacrifice2El.textContent = String(world.sacrificeStock2)
        this.lastSacrifice2 = world.sacrificeStock2
      }
      if (world.rewindStock2 !== this.lastRewind2) {
        this.rewind2El.textContent = String(world.rewindStock2)
        this.lastRewind2 = world.rewindStock2
      }
      // Rail header total — BOTH players' inventories at a glance
      // (change-guarded; superStocks.ts).
      const total = superStockTotal(world)
      if (total !== this.lastSuperTotal) {
        this.superTotalEl.textContent = `×${total}`
        this.lastSuperTotal = total
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

/**
 * Render one HUD super-item key label: `Name<Key>`. Pure so both the P1 and
 * two-player P2 label rows are regression-tested headlessly (AGENTS §8).
 * Modifier prefixes are deliberately dropped — the HUD hint is a compact
 * physical-key reminder; a rebinding to a modifier COMBO still communicates
 * its primary key here.
 */
export function formatSuperKeyLabel(name: string, binding: string): string {
  return `${name}<${formatKeyCode(parseBinding(binding).code)}>`
}

/**
 * Maximum stars shown in the HUD before the overflow counter kicks in.
 * Non-classic star levels accumulate WITHOUT bound, so an uncapped string of
 * ★ would widen (and, in the old stacked layout, wrap) the HUD bar.
 */
export const MAX_HUD_STARS = 5

/**
 * Render the player star level compactly (HUD redesign): up to
 * MAX_HUD_STARS stars, then a `+N` overflow counter — `★★★★★+7` for level 12.
 * Level 0 renders as `--` (no stars, matching the pre-redesign HUD). Pure —
 * headless-testable like formatSuperKeyLabel.
 */
export function formatStarLevel(level: number): string {
  const lvl = Math.max(0, level)
  if (lvl === 0) return '--'
  if (lvl <= MAX_HUD_STARS) return '★'.repeat(lvl)
  return '★'.repeat(MAX_HUD_STARS) + `+${lvl - MAX_HUD_STARS}`
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
