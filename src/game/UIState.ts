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

// ── Menu row order — single source for the row ↔ menuCursor contract ──────
// (遗留 #6 / plan/refactor.zcode.md §2.5 follow-up: this mapping used to be
// encoded twice as `off + N` arithmetic in GameMenu.ts and once more in
// MenuScreen.ts.) RESUME sits at index 0 only when a resumable manual
// snapshot exists; without it the row is hidden and every index shifts down
// by one. Game.ts/GameMenu own the cursor VALUE semantics and consume these
// helpers; MenuScreen consumes them for highlighting.
export const MENU_ROW_KEYS = [
  'resume',
  'difficulty',
  'theme',
  'language',
  'stage',
  'start-row',
  'controls',
] as const

export type MenuRowKey = (typeof MENU_ROW_KEYS)[number]

/** Cursor index of a menu row (-1 = not a cursor row). */
export function menuRowIndex(key: MenuRowKey, hasResume: boolean): number {
  const i = MENU_ROW_KEYS.indexOf(key)
  if (i < 0) return -1
  return !hasResume && i > 0 ? i - 1 : i
}

/** Number of navigable rows (the hidden RESUME row is not navigable). */
export function menuRowCount(hasResume: boolean): number {
  return hasResume ? MENU_ROW_KEYS.length : MENU_ROW_KEYS.length - 1
}
