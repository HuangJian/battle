import type { KeyBindings } from '../../types'
import { DEFAULT_KEYS, eventToBinding, isModifierCode, parseBinding } from '../../game/Input'
import { t } from '../../i18n'
import { formatKeyCode } from './HudView'

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
  private onChanged: (() => void) | null = null
  private listeningAction: keyof KeyBindings | null = null
  private openFlag = false

  /** Invoked whenever bindings change so HUD super-item labels re-render. */
  onSuperLabelsChanged: (() => void) | null = null

  constructor(private readonly createElement: (tag: string, className: string) => HTMLElement) {
    this.el = this.build()
  }

  /**
   * Wire the live key-bindings object (the same reference the Input system
   * reads) and a persistence callback. Called once from Game after the
   * PresentationLayer is constructed.
   */
  initControls(bindings: KeyBindings, onChanged: () => void): void {
    this.bindings = bindings
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
    this.refreshAllKeyButtons()
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
    this.el.classList.remove('active')
  }

  private build(): HTMLElement {
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
    for (const action of ControlsPanel.CONTROL_ACTIONS) {
      const row = this.createElement('div', 'controls-row')
      const labelEl = this.createElement('span', 'controls-label')
      labelEl.dataset.i18n = `controls.actions.${action}`
      const btn = this.createElement('button', 'controls-key-btn') as HTMLButtonElement
      btn.type = 'button'
      btn.dataset.action = action
      btn.textContent = this.formatKey(this.bindings[action])
      btn.addEventListener('click', () => this.onKeyButtonClick(action))
      row.appendChild(labelEl)
      row.appendChild(btn)
      list.appendChild(row)
      this.keyButtons.set(action, btn)
    }

    const resetBtn = panel.querySelector('[data-controls="reset"]') as HTMLElement
    resetBtn.addEventListener('click', () => this.resetBindings())
    const backBtn = panel.querySelector('[data-controls="back"]') as HTMLElement
    backBtn.addEventListener('click', () => this.close())

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
        otherBtn.textContent = this.formatKey(this.bindings[other])
      }
    }
  }

  private cancelListening(): void {
    this.listeningAction = null
    this.refreshAllKeyButtons()
  }

  private resetBindings(): void {
    for (const action of ControlsPanel.CONTROL_ACTIONS) {
      this.bindings[action] = DEFAULT_KEYS[action]
    }
    this.listeningAction = null
    this.refreshAllKeyButtons()
    this.onSuperLabelsChanged?.()
    this.onChanged?.()
  }

  private refreshAllKeyButtons(): void {
    for (const action of ControlsPanel.CONTROL_ACTIONS) {
      this.refreshKeyButton(action)
    }
  }

  private refreshKeyButton(action: keyof KeyBindings): void {
    const btn = this.keyButtons.get(action)
    if (!btn) return
    btn.classList.remove('listening', 'conflict')
    btn.textContent = this.formatKey(this.bindings[action])
  }

  /** Reject keys reserved for panel navigation, and duplicates of other actions. */
  private findConflict(action: keyof KeyBindings, binding: string): keyof KeyBindings | null {
    if (binding === 'Escape' || binding === 'Tab') return action // reserved
    for (const other of ControlsPanel.CONTROL_ACTIONS) {
      // Exact binding-string match: a modifier combo (Shift+R) is distinct
      // from its bare key (R), so they must not collide on the same action.
      if (other !== action && this.bindings[other] === binding) return other
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
      this.bindings[action] = binding
      this.listeningAction = null
      this.refreshKeyButton(action)
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
