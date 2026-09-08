import { describe, it, expect } from 'bun:test'
import {
  buildPadLegendRows,
  padLegendChipsHtml,
  formatPadButton,
} from '../src/presentation/ui/padLabels'
import { DEFAULT_PAD_BINDINGS, PAD_ACTIONS, GAMEPAD_BUTTONS } from '../src/game/settings'
import { t } from '../src/i18n'

/**
 * Menu-screen gamepad legend (2p-review P1-3): the legend used to render 8
 * anonymous `<kbd>` chips (e.g. `BUTTON 7` after a rebind) — the row's action
 * name was dropped at the HTML-assembly step, exactly when a rebind makes the
 * chip unreadable. The assembly is a pure function so it is regression-tested
 * headlessly (AGENTS §8).
 */

describe('buildPadLegendRows', () => {
  it('yields one row per rebindable pad action, in PAD_ACTIONS order', () => {
    const rows = buildPadLegendRows(DEFAULT_PAD_BINDINGS)
    expect(rows.map((r) => r.action)).toEqual([...PAD_ACTIONS])
    // Every row carries its own action name + the bound button's label.
    for (const row of rows) {
      expect(row.action.length).toBeGreaterThan(0)
      expect(row.label.length).toBeGreaterThan(0)
    }
  })

  it('reflects a rebound button (fire → RB/BUTTON 7)', () => {
    const rows = buildPadLegendRows({ ...DEFAULT_PAD_BINDINGS, fire: 7 })
    expect(rows.find((r) => r.action === 'fire')!.label).toBe('BUTTON 7')
  })
})

describe('padLegendChipsHtml (P1-3)', () => {
  it('prefixes every chip with the localized action name', () => {
    const rows = buildPadLegendRows(DEFAULT_PAD_BINDINGS)
    const html = padLegendChipsHtml(rows, (key) => `[${key}]`)
    expect(html).toContain('<kbd>[controls.padActions.up] D-PAD ↑</kbd>')
    expect(html).toContain('<kbd>[controls.padActions.fire] A / ✕</kbd>')
    expect(html).toContain('<kbd>[controls.padActions.rewind] Y / △</kbd>')
    expect(html.split('<kbd>').length - 1).toBe(PAD_ACTIONS.length)
  })

  it('a rebind to an anonymous button stays readable — the name prefix carries it', () => {
    const rows = buildPadLegendRows({ ...DEFAULT_PAD_BINDINGS, fire: 7 })
    const html = padLegendChipsHtml(rows, t) // real catalog (default locale: en)
    expect(html).toContain('Pad Fire BUTTON 7')
    expect(html).not.toContain('<kbd>BUTTON 7</kbd>') // never anonymous
  })
})

describe('formatPadButton', () => {
  it('labels the standard mapping buttons', () => {
    expect(formatPadButton(GAMEPAD_BUTTONS.fire)).toBe('A / ✕')
    expect(formatPadButton(GAMEPAD_BUTTONS.guard)).toBe('B / ◯')
    expect(formatPadButton(GAMEPAD_BUTTONS.frenzy)).toBe('X / □')
    expect(formatPadButton(GAMEPAD_BUTTONS.rewind)).toBe('Y / △')
    expect(formatPadButton(GAMEPAD_BUTTONS.pause)).toBe('START')
    expect(formatPadButton(GAMEPAD_BUTTONS.dpadUp)).toBe('D-PAD ↑')
  })

  it('falls back to a numbered label for non-standard buttons', () => {
    expect(formatPadButton(7)).toBe('BUTTON 7')
    expect(formatPadButton(5)).toBe('BUTTON 5')
  })
})
