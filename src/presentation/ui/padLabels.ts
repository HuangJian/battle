/**
 * Pad button label formatting (§348 follow-up) — presentation-pure helpers,
 * headless-testable (AGENTS §8). Kept out of ControlsPanel so tests don't
 * need DOM, mirroring the formatKeyCode placement in HudView.
 */
import { GAMEPAD_BUTTONS } from '../../game/settings'

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
