import { describe, it, expect } from 'bun:test'
import { buildPadLegendRows, GAMEPAD_BUTTONS } from '../src/presentation/ui/padLabels'
import { DEFAULT_PAD_BINDINGS } from '../src/game/settings'

/**
 * Menu-screen gamepad legend (§348 follow-up): a compact row list built
 * purely from the live PadBindings, rendered near the nav hints. Headless —
 * pure functions only (AGENTS §8).
 */

describe('buildPadLegendRows', () => {
  it('renders the 8 rebindable actions with their default button labels', () => {
    const rows = buildPadLegendRows(DEFAULT_PAD_BINDINGS)
    expect(rows.map((r) => r.label)).toEqual([
      'D-PAD ↑', 'D-PAD ↓', 'D-PAD ←', 'D-PAD →',
      'A / ✕', 'B / ◯', 'X / □', 'Y / △',
    ])
    expect(rows.map((r) => r.action)).toEqual([
      'up', 'down', 'left', 'right', 'fire', 'guard', 'frenzy', 'rewind',
    ])
  })

  it('follows a rebind (fire moved to RB → label updates, actions keep order)', () => {
    const rows = buildPadLegendRows({ ...DEFAULT_PAD_BINDINGS, fire: 7 })
    expect(rows.find((r) => r.action === 'fire')?.label).toBe('BUTTON 7')
    // Row order is stable — the legend never reshuffles on rebind.
    expect(rows.map((r) => r.action)).toEqual([
      'up', 'down', 'left', 'right', 'fire', 'guard', 'frenzy', 'rewind',
    ])
  })

  it('covers every rebindable action exactly once (no duplicates)', () => {
    const rows = buildPadLegendRows(DEFAULT_PAD_BINDINGS)
    const actions = rows.map((r) => r.action)
    expect(new Set(actions).size).toBe(8)
  })

  it('does not include the fixed Start/pause row', () => {
    const rows = buildPadLegendRows(DEFAULT_PAD_BINDINGS)
    expect(rows.some((r) => r.label === 'START')).toBe(false)
    expect(GAMEPAD_BUTTONS.pause).toBe(9)
  })

  it('is a pure function (two calls with equal inputs give equal outputs)', () => {
    expect(buildPadLegendRows(DEFAULT_PAD_BINDINGS)).toEqual(
      buildPadLegendRows({ ...DEFAULT_PAD_BINDINGS }),
    )
  })
})
