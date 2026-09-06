import type { KeyBindings, PadBindings } from '../../types'
import { DEFAULT_KEYS, DEFAULT_P2_KEYS, eventToBinding, isModifierCode, parseBinding } from '../../game/Input'
import {
  P2_ACTIVE_ACTIONS,
  PAD_ACTIONS,
  findCrossPlayerConflict,
  findPadConflict,
  type P2Action,
  type PadAction,
} from '../../game/settings'
import { t } from '../../i18n'
import { formatKeyCode } from './HudView'
import { formatPadButton } from './padLabels'
import type { GamepadSnapshot } from '../../game/GamepadInput'
import { firstPressedPadButton } from '../../game/GamepadInput'
import { DEFAULT_PAD_BINDINGS } from '../../game/settings'

/**
 * ControlsPanel — the key-bindings modal: action list, click-to-rebind flow,
 * conflict detection, and defaults reset (plan/refactor.agy.md §2.4).
 * Extracted verbatim from UIManager (§256 slice pattern).
 *
 * The panel owns the LIVE bindings object (the same reference the Input
 * system reads) after {@link initControls}; persistence is delegated to the
 * `onChanged` callback provided by Game.
 */
export class ControlsPanel {
  /** Ordered list of rebindable gameplay actions shown in the panel.
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

  /** The `ui-screen ui-controls` root element (appended to the overlay). */
  readonly el: HTMLElement

  private keyButtons = new Map<keyof KeyBindings, HTMLElement>()
  private bindings: KeyBindings = { ...DEFAULT_KEYS }
  /** P2's live bindings object (the same reference P2's Input reads). */
  private bindings2: KeyBindings = { ...DEFAULT_P2_KEYS }
  /** Live gamepad bindings object (the same reference GamepadManager reads). */
  private padBindings: PadBindings | null = null
  private onChanged: (() => void) | null = null
  private listeningAction: keyof KeyBindings | PadAction | null = null
  private openFlag = false
  /** Which tab is shown: player 1, player 2, or the gamepad mapping. */
  private activeTab: 'p1' | 'p2' | 'pad' = 'p1'
  private p1TabBtn: HTMLButtonElement | null = null
  private p2TabBtn: HTMLButtonElement | null = null
  private padTabBtn: HTMLButtonElement | null = null
  private listEl: HTMLElement | null = null
  /** While capturing a pad binding: rAF poll of the live GamepadSnapshot. */
  private padCaptureRaf = 0
  /** While capturing a pad binding: the live snapshot reader seam. */
  getSnapshot: (() => GamepadSnapshot | null) | null = null

  /** Invoked whenever bindings change so HUD super-item labels re-render. */
  onSuperLabelsChanged: (() => void) | null = null

  constructor(private readonly createElement: (tag: string, className: string) => HTMLElement) {
    this.el = this.build()
  }

  /**
   * Wire the live key-bindings objects (the same references the Input
   * systems read) and a persistence callback. Called once from Game after
   * the PresentationLayer is constructed.
   */
  initControls(
    bindings: KeyBindings,
    bindings2: KeyBindings,
    onChanged: () => void,
    padBindings?: PadBindings,
  ): void {
    this.bindings = bindings
    this.bindings2 = bindings2
    this.padBindings = padBindings ?? null
    this.onChanged = onChanged
    this.refreshAllKeyButtons()
    this.onSuperLabelsChanged?.()
    // Capture-phase listener so a rebind key never reaches the game Input
    // (which listens on window in the bubble phase). We only act while the
    // panel is open, so normal gameplay input is unaffected.
    window.addEventListener('keydown', this.onKeyDown, true)
  }

  /** Whether the controls panel is currently open (a UI-modal, not a world state). */
  isOpen(): boolean {
    return this.openFlag
  }

  /** Current live bindings (read by the super-item label refresh bridge). */
  get currentBindings(): KeyBindings {
    return this.bindings
  }

  /** Current live P2 bindings (read by the super-item label refresh bridge). */
  get currentBindings2(): KeyBindings {
    return this.bindings2
  }

  /** Current live gamepad bindings (present only when Game wired them). */
  get currentPadBindings(): PadBindings | null {
    return this.padBindings
  }

  /**
   * Open the controls panel as a modal overlay over whatever screen is
   * currently active (menu, recovery, gameover). The underlying screen
   * keeps its `active` class; `showScreen()` will re-sync on close.
   */
  open(): void {
    if (this.openFlag) return
    this.openFlag = true
    this.el.classList.add('active')
    this.listeningAction = null
    this.renderActiveTab()
  }

  /**
   * Close the controls panel. The underlying screen's `active` class was
   * never removed, so it is already visible; `update() → showScreen()` on
   * the next frame will confirm the correct screen (menu, recovery, or
   * gameover) — no forced class swap needed.
   */
  close(): void {
    if (!this.openFlag) return
    this.openFlag = false
    this.listeningAction = null
    this.stopPadCapture()
    this.el.classList.remove('active')
  }

  private build(): HTMLElement {
    const screen = this.createElement('div', 'ui-screen ui-controls')
    const panel = this.createElement('div', 'ui-panel controls-panel')
    panel.innerHTML = `
      <h2 class="ui-title" data-i18n="controls.title">KEY BINDINGS</h2>
      <p class="ui-hint" data-i18n="controls.hint">Click a key, then press a new one</p>
      <div class="controls-tabs" data-controls="tabs">
        <button class="controls-tab" data-controls="tab-p1" type="button" data-i18n="controls.tabP1">Player 1</button>
        <button class="controls-tab" data-controls="tab-p2" type="button" data-i18n="controls.tabP2">Player 2</button>
        <button class="controls-tab" data-controls="tab-pad" type="button" data-i18n="controls.tabPad">Gamepad</button>
      </div>
      <div class="controls-list" data-controls="list"></div>
      <div class="controls-actions">
        <button class="controls-btn" data-controls="reset" type="button" data-i18n="controls.reset">Reset Defaults</button>
        <button class="controls-btn controls-btn-primary" data-controls="back" type="button" data-i18n="controls.back">Back</button>
      </div>
      <p class="ui-hint" data-i18n="controls.escHint">Press Esc to go back</p>
    `

    this.p1TabBtn = panel.querySelector('[data-controls="tab-p1"]') as HTMLButtonElement
    this.p2TabBtn = panel.querySelector('[data-controls="tab-p2"]') as HTMLButtonElement
    this.padTabBtn = panel.querySelector('[data-controls="tab-pad"]') as HTMLButtonElement
    this.listEl = panel.querySelector('[data-controls="list"]') as HTMLElement
    this.p1TabBtn.addEventListener('click', () => this.selectTab('p1'))
    this.p2TabBtn.addEventListener('click', () => this.selectTab('p2'))
    this.padTabBtn.addEventListener('click', () => this.selectTab('pad'))

    this.renderActiveTab()

    const resetBtn = panel.querySelector('[data-controls="reset"]') as HTMLElement
    resetBtn.addEventListener('click', () => this.resetBindings())
    const backBtn = panel.querySelector('[data-controls="back"]') as HTMLElement
    backBtn.addEventListener('click', () => this.close())

    screen.appendChild(panel)
    return screen
  }

  /** Switch the visible binding list to a tab (idempotent). */
  private selectTab(tab: 'p1' | 'p2' | 'pad'): void {
    if (this.activeTab === tab) return
    this.activeTab = tab
    this.listeningAction = null
    this.renderActiveTab()
  }

  /**
    * Render the active player's rows into the list. Rows are rebuilt per
    * switch (a tab flip is a rare UI event — allocation cost is irrelevant);
    * the per-frame hot path never touches this.
    */
  private renderActiveTab(): void {
    if (!this.listEl || !this.p1TabBtn || !this.p2TabBtn || !this.padTabBtn) return
    this.p1TabBtn.classList.toggle('active', this.activeTab === 'p1')
    this.p2TabBtn.classList.toggle('active', this.activeTab === 'p2')
    this.p2TabBtn.setAttribute('aria-pressed', String(this.activeTab === 'p2'))
    this.padTabBtn.classList.toggle('active', this.activeTab === 'pad')
    this.padTabBtn.setAttribute('aria-pressed', String(this.activeTab === 'pad'))
    // Tab-specific hint (keyboard tabs vs the pad capture instruction).
    const hint = this.el.querySelector('[data-i18n="controls.hint"]')
    if (hint) hint.textContent = t(this.activeTab === 'pad' ? 'controls.padHint' : 'controls.hint')
    this.keyButtons.clear()
    this.listEl.innerHTML = ''
    if (this.activeTab === 'p1') {
      for (const action of ControlsPanel.CONTROL_ACTIONS) this.appendRow(action, this.bindings)
    } else if (this.activeTab === 'p2') {
      for (const action of P2_ACTIVE_ACTIONS) this.appendRow(action, this.bindings2)
    } else {
      // Gamepad tab: the panel may be opened before Game wires pad bindings
      // (defensive) — render the standard defaults read-only rather than
      // crashing on null.
      for (const action of PAD_ACTIONS) this.appendPadRow(action, this.padBindings ?? DEFAULT_PAD_BINDINGS)
    }
  }

  /** Append one action row (label + key button) bound to the given key set. */
  private appendRow(action: keyof KeyBindings, keys: KeyBindings): void {
    if (!this.listEl) return
    const row = this.createElement('div', 'controls-row')
    const labelEl = this.createElement('span', 'controls-label')
    labelEl.dataset.i18n = `controls.actions.${action}`
    const btn = this.createElement('button', 'controls-key-btn') as HTMLButtonElement
    btn.type = 'button'
    btn.dataset.action = action
    btn.textContent = this.formatKey(keys[action])
    btn.addEventListener('click', () => this.onKeyButtonClick(action))
    row.appendChild(labelEl)
    row.appendChild(btn)
    this.listEl.appendChild(row)
    this.keyButtons.set(action, btn)
  }

  /** Append one gamepad action row (label + pad-button button). */
  private appendPadRow(action: PadAction, pads: PadBindings): void {
    if (!this.listEl) return
    const row = this.createElement('div', 'controls-row')
    const labelEl = this.createElement('span', 'controls-label')
    labelEl.dataset.i18n = `controls.padActions.${action}`
    const btn = this.createElement('button', 'controls-key-btn') as HTMLButtonElement
    btn.type = 'button'
    btn.dataset.action = action
    btn.textContent = formatPadButton(pads[action])
    btn.addEventListener('click', () => this.onPadButtonClick(action))
    row.appendChild(labelEl)
    row.appendChild(btn)
    this.listEl.appendChild(row)
    this.keyButtons.set(action, btn)
  }

  /** Begin listening for a pad-button press for `action` (rAF poll loop). */
  private onPadButtonClick(action: PadAction): void {
    if (!this.padBindings) return
    if (this.listeningAction === action) {
      this.cancelListening()
      return
    }
    this.listeningAction = action
    const btn = this.keyButtons.get(action)
    if (btn) {
      btn.classList.add('listening')
      btn.classList.remove('conflict')
      btn.textContent = t('controls.pressPadButton')
    }
    for (const [other, otherBtn] of this.keyButtons) {
      if (other !== action) {
        otherBtn.classList.remove('listening')
        otherBtn.textContent =
          this.activeTab === 'pad'
            ? formatPadButton(this.padBindings[other as PadAction])
            : this.formatKey((this.activeTab === 'p1' ? this.bindings : this.bindings2)[other as keyof KeyBindings])
      }
    }
    this.startPadCapture()
  }

  /**
   * rAF capture loop: poll the live pad each frame; the FIRST newly-pressed
   * button is the new binding. Cancelled by Esc (the keydown listener still
   * runs — pad events cannot cancel it) or by closing the tab/panel.
   */
  private startPadCapture(): void {
    this.stopPadCapture()
    const step = (): void => {
      if (!this.openFlag || this.activeTab !== 'pad' || !this.listeningAction) return
      const snap = this.getSnapshot?.() ?? null
      const pressed = firstPressedPadButton(snap)
      if (pressed !== null && this.padBindings) {
        const action = this.listeningAction as PadAction
        const conflict = findPadConflict(action, pressed, this.padBindings)
        if (conflict) {
          this.flashConflict(action)
        } else {
          this.padBindings[action] = pressed
          this.listeningAction = null
          this.refreshAllKeyButtons()
          this.onChanged?.()
        }
        this.stopPadCapture()
        return
      }
      this.padCaptureRaf = requestAnimationFrame(step)
    }
    this.padCaptureRaf = requestAnimationFrame(step)
  }

  private stopPadCapture(): void {
    if (this.padCaptureRaf !== 0) {
      cancelAnimationFrame(this.padCaptureRaf)
      this.padCaptureRaf = 0
    }
  }

  private onKeyButtonClick(action: keyof KeyBindings): void {
    // Toggle listening mode for this action.
    if (this.listeningAction === action) {
      this.cancelListening()
      return
    }
    this.listeningAction = action
    const keys = this.activeTab === 'p1' ? this.bindings : this.bindings2
    const btn = this.keyButtons.get(action)
    if (btn) {
      btn.classList.add('listening')
      btn.classList.remove('conflict')
      btn.textContent = t('controls.pressKey')
    }
    // Clear listening state on any other buttons.
    for (const [other, otherBtn] of this.keyButtons) {
      if (other !== action) {
        otherBtn.classList.remove('listening')
        otherBtn.textContent = this.formatKey(keys[other])
      }
    }
  }

  private cancelListening(): void {
    this.listeningAction = null
    this.stopPadCapture()
    this.refreshAllKeyButtons()
  }

  private resetBindings(): void {
    // Reset the ACTIVE tab's set against ITS OWN defaults (P2 repairs to
    // WASD+F, not P1's arrows/space; the pad tab repairs to the standard
    // mapping). Cross-player conflicts can only appear if the player
    // manually re-creates them — the defaults are disjoint — and the user
    // can always resolve those interactively.
    if (this.activeTab === 'pad') {
      if (this.padBindings) Object.assign(this.padBindings, DEFAULT_PAD_BINDINGS)
    } else {
      const defaults = this.activeTab === 'p1' ? DEFAULT_KEYS : DEFAULT_P2_KEYS
      const keys = this.activeTab === 'p1' ? this.bindings : this.bindings2
      for (const action of Object.keys(defaults) as (keyof KeyBindings)[]) {
        keys[action] = defaults[action]
      }
    }
    this.listeningAction = null
    this.stopPadCapture()
    this.refreshAllKeyButtons()
    this.onSuperLabelsChanged?.()
    this.onChanged?.()
  }

  private refreshAllKeyButtons(): void {
    for (const action of this.keyButtons.keys()) {
      if (this.activeTab === 'pad') {
        const btn = this.keyButtons.get(action)
        if (btn && this.padBindings) {
          btn.classList.remove('listening', 'conflict')
          btn.textContent = formatPadButton(this.padBindings[action as PadAction])
        }
      } else {
        const keys = this.activeTab === 'p1' ? this.bindings : this.bindings2
        this.refreshKeyButton(action, keys)
      }
    }
  }

  private refreshKeyButton(action: keyof KeyBindings, keys: KeyBindings): void {
    const btn = this.keyButtons.get(action)
    if (!btn) return
    btn.classList.remove('listening', 'conflict')
    btn.textContent = this.formatKey(keys[action])
  }

  /** Reject keys reserved for panel navigation, same-player duplicates, and
   *  cross-player collisions on the actions both players actively drive. */
  private findConflict(action: keyof KeyBindings, binding: string): keyof KeyBindings | null {
    if (binding === 'Escape' || binding === 'Tab') return action // reserved
    const keys = this.activeTab === 'p1' ? this.bindings : this.bindings2
    for (const [other] of this.keyButtons) {
      // Exact binding-string match: a modifier combo (Shift+R) is distinct
      // from its bare key (R), so they must not collide on the same action.
      if (other !== action && keys[other] === binding) return other
    }
    // Cross-player: system keys (pause/reset/…) are P1-global — P2's Input
    // is never polled for them, so its mirrored defaults can never actually
    // clash. Only the active action set is cross-checked, against both sets
    // directly (independent of which tab is visible).
    if (P2_ACTIVE_ACTIONS.includes(action as P2Action)) {
      return findCrossPlayerConflict(
        this.activeTab === 'p2' ? 2 : 1,
        action as P2Action,
        binding,
        this.bindings,
        this.bindings2,
      )
    }
    return null
  }

  private flashConflict(action: keyof KeyBindings): void {
    const btn = this.keyButtons.get(action)
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
    const base = formatKeyCode(spec.code)
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

  private onKeyDown = (e: KeyboardEvent): void => {
    if (!this.openFlag) return
    // Own all key input while the panel is open so the game Input never sees
    // it (prevents the menu cursor from moving behind the panel, and stops
    // the rebind key from being registered as "pressed").
    e.preventDefault()
    e.stopImmediatePropagation()

    if (this.listeningAction) {
      const action = this.listeningAction
      const btn = this.keyButtons.get(action)
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
      const keys = this.activeTab === 'p1' ? this.bindings : this.bindings2
      keys[action] = binding
      this.listeningAction = null
      this.refreshKeyButton(action, keys)
      this.onSuperLabelsChanged?.()
      this.onChanged?.()
      return
    }

    // Not listening: Esc / Enter closes the panel.
    if (e.code === 'Escape' || e.code === 'Enter') {
      this.close()
    }
  }
}
