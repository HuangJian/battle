/**
 * Menu & recovery-overlay UI navigation state (plan/refactor.agy.md §1.3
 * Phase A). Grouped into one struct on the World so gameplay state and menu
 * navigation state stay visibly separated — the One-Author invariant
 * (AGENTS §2.1) governs *gameplay* state; these fields are pure menu/overlay
 * bookkeeping that Simulation never reads or writes. They are written by the
 * Game-layer controllers (GameMenu / GameSnapshot / RecoveryController) and
 * read by presentation (UIManager / PresentationLayer).
 *
 * Not serialized: a snapshot captures gameplay, not which menu row was
 * highlighted (see tests/serializer-field-guard.test.ts EXCLUDED).
 */
export interface UIState {
  /** Start-menu cursor row (RESUME? / DIFFICULTY / THEME / LANGUAGE / STAGE / NEW GAME / CONTROLS). */
  menuCursor: number
  /** Stage selected in the start menu (0-based). */
  selectedStage: number
  /** Selected option index in the recovery overlay menu. */
  recoveryCursor: number
  /** Recovery countdown; 0 = none, 3/2/1 = counting down. */
  recoveryCountdown: number
  /** True while fading to black before a snapshot restore. */
  recoveryFading: boolean
}

export function createUIState(): UIState {
  return {
    menuCursor: 0,
    selectedStage: 0,
    recoveryCursor: 0,
    recoveryCountdown: 0,
    recoveryFading: false,
  }
}
