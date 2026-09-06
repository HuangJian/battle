/**
 * Pad button label formatting (§351a follow-up) — presentation-pure helpers,
 * headless-testable (AGENTS §8). Kept out of ControlsPanel so tests don't
 * need DOM, mirroring the formatKeyCode placement in HudView.
 */
import { GAMEPAD_BUTTONS, PAD_ACTIONS } from '../../game/settings'
import type { PadBindings } from '../../types'

/** Re-exported for consumers that already import from this module. */
export { GAMEPAD_BUTTONS } from '../../game/settings'

/** Standard-mapping index → short label (Xbox / PlayStation names shown). */
export function formatPadButton(index: number): string {
  switch (index) {
    case GAMEPAD_BUTTONS.fire:
      return 'A / ✕'
    case GAMEPAD_BUTTONS.guard:
      return 'B / ◯'
    case GAMEPAD_BUTTONS.frenzy:
      return 'X / □'
    case GAMEPAD_BUTTONS.rewind:
      return 'Y / △'
    case GAMEPAD_BUTTONS.pause:
      return 'START'
    case GAMEPAD_BUTTONS.dpadUp:
      return 'D-PAD ↑'
    case GAMEPAD_BUTTONS.dpadDown:
      return 'D-PAD ↓'
    case GAMEPAD_BUTTONS.dpadLeft:
      return 'D-PAD ←'
    case GAMEPAD_BUTTONS.dpadRight:
      return 'D-PAD →'
    default:
      return `BUTTON ${index}`
  }
}

/**
 * One row of the menu-screen pad legend (§351a follow-up): a rebindable
 * action plus the human-readable label of its currently-bound button.
 */
export interface PadLegendRow {
  action: string
  label: string
}

/**
 * Legend rows for the menu screen, read from the LIVE PadBindings — the
 * menu re-renders this after a Controls-panel rebind, so the legend never
 * goes stale. Pure + headless (AGENTS §8); the row order follows
 * {@link PAD_ACTIONS} and never reshuffles on rebind. Start/pause is fixed
 * and intentionally omitted (it is not rebindable).
 */
export function buildPadLegendRows(pads: PadBindings): PadLegendRow[] {
  const rows: PadLegendRow[] = []
  for (const action of PAD_ACTIONS) {
    rows.push({ action, label: formatPadButton(pads[action]) })
  }
  return rows
}
