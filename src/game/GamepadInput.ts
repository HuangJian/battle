import type { Direction } from '../constants'
import type { InputLike } from './Input'

// ================================================================
// GamepadInput — gamepad support (DECISIONS §347c)
//
// The browser Gamepad API is POLLED (navigator.getGamepads()), not
// event-driven: there are no reliable per-frame "just pressed" events.
// Edge detection therefore lives here — a pure snapshot-diff layer:
//
//   readSnapshot()   pure GamepadSnapshot → neutral levels
//   GamepadInput     poll-to-poll diff → InputLike edges + levels
//   CompositeInput   keyboard OR gamepad merge (pad priority)
//   GamepadManager   navigator slotting: pad[0]→P1, pad[1]→P2
//
// Standard-gamepad mapping (W3C mapping="standard"):
//   left stick / d-pad … move     A/Cross    (0) fire
//   B/Circle (1) guard            X/Square   (2) frenzy
//   Y/Triangle (3) rewind         Start      (9) pause / menu confirm
//
// Input devices stay OUTSIDE the World (AGENTS §2.2); the recorder taps the
// SAME composite InputLike the sim consumes (DECISIONS #75), so a pad-driven
// run replays byte-identically.
// ================================================================

/** Minimal structural snapshot of one Gamepad (subset we consume). */
export interface GamepadSnapshot {
  axes: readonly number[]
  buttons: readonly { pressed: boolean; value: number }[]
  connected: boolean
  mapping: string
}

/** Standard-mapping button indices we bind. */
export const GAMEPAD_BUTTONS = {
  fire: 0, // A / Cross
  guard: 1, // B / Circle
  frenzy: 2, // X / Square
  rewind: 3, // Y / Triangle
  pause: 9, // Start / Options
  dpadUp: 12,
  dpadDown: 13,
  dpadLeft: 14,
  dpadRight: 15,
} as const

/** Stick axes beyond this magnitude count as a direction (16-bit pads drift). */
export const GAMEPAD_DEADZONE = 0.5

/** Neutral per-frame levels of one pad (what readSnapshot returns). */
export interface PadFrame {
  dir: Direction | null
  fire: boolean
  guard: boolean
  frenzy: boolean
  rewind: boolean
  pause: boolean
}

const NEUTRAL: PadFrame = {
  dir: null,
  fire: false,
  guard: false,
  frenzy: false,
  rewind: false,
  pause: false,
}

/**
 * Pure snapshot → levels. No mutation, no DOM — unit-testable headlessly.
 * Stick has priority over d-pad when both are engaged (intentional single
 * direction: the movement system is axis-locked anyway).
 */
export function readSnapshot(pad: GamepadSnapshot | null, deadzone = GAMEPAD_DEADZONE): PadFrame {
  if (!pad || !pad.connected) return NEUTRAL

  let dir: Direction | null = null
  const ax = pad.axes[0] ?? 0
  const ay = pad.axes[1] ?? 0
  // Dominant axis wins for diagonal stick pushes (tank moves one axis/tick).
  if (Math.abs(ax) >= deadzone || Math.abs(ay) >= deadzone) {
    if (Math.abs(ax) >= Math.abs(ay)) dir = ax > 0 ? 'right' : 'left'
    else dir = ay > 0 ? 'down' : 'up'
  }
  if (dir === null) {
    const b = pad.buttons
    if (b[GAMEPAD_BUTTONS.dpadUp]?.pressed) dir = 'up'
    else if (b[GAMEPAD_BUTTONS.dpadDown]?.pressed) dir = 'down'
    else if (b[GAMEPAD_BUTTONS.dpadLeft]?.pressed) dir = 'left'
    else if (b[GAMEPAD_BUTTONS.dpadRight]?.pressed) dir = 'right'
  }

  const btn = (i: number): boolean => !!pad.buttons[i]?.pressed
  return {
    dir,
    fire: btn(GAMEPAD_BUTTONS.fire),
    guard: btn(GAMEPAD_BUTTONS.guard),
    frenzy: btn(GAMEPAD_BUTTONS.frenzy),
    rewind: btn(GAMEPAD_BUTTONS.rewind),
    pause: btn(GAMEPAD_BUTTONS.pause),
  }
}

/**
 * One player's gamepad as an InputLike. `pollSnapshot()` is fed once per
 * RENDER frame by the GamepadManager (before handleFrameInput); edge state
 * is diffed against the previous poll, NOT cleared by endFrame — so N sim
 * ticks inside one render frame all see the same press edge (fixed-timestep
 * catch-up never loses a super-item press, mirroring Input's contract).
 */
export class GamepadInput implements InputLike {
  private prev: PadFrame = NEUTRAL
  private cur: PadFrame = NEUTRAL

  /** Feed one polled hardware snapshot (once per render frame). */
  pollSnapshot(pad: GamepadSnapshot | null): void {
    this.prev = this.cur
    this.cur = readSnapshot(pad)
  }

  getMoveDirection(): Direction | null {
    return this.cur.dir
  }

  /** Level, not edge: holding A fires on every sim tick (like Space held). */
  isFiring(): boolean {
    return this.cur.fire
  }

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    if (kind === 'guard') return this.cur.guard && !this.prev.guard
    if (kind === 'frenzy') return this.cur.frenzy && !this.prev.frenzy
    return this.cur.rewind && !this.prev.rewind
  }

  /** Start edge — read by GameMenu once per render frame (pause/confirm). */
  isPausePressed(): boolean {
    return this.cur.pause && !this.prev.pause
  }

  /** Clear only the CURRENT edge window — the next poll re-diffs from `cur`.
   *  (Held keys must survive across consumed menu frames, matching Input.) */
  endFrame(): void {
    this.prev = this.cur
  }

  /** Drop all state — used when a screen transition must not carry a press. */
  reset(): void {
    this.prev = NEUTRAL
    this.cur = NEUTRAL
  }
}

/**
 * Merge a keyboard InputLike with an optional gamepad InputLike: the pad
 * wins when it reports a direction, booleans OR. Identity of the delegate
 * refs is preserved so endFrame()/reset() reach both sources.
 */
export class CompositeInput implements InputLike {
  constructor(
    private readonly keyboard: InputLike,
    private readonly pad: InputLike | null,
  ) {}

  getMoveDirection(): Direction | null {
    return this.pad?.getMoveDirection() ?? this.keyboard.getMoveDirection()
  }

  isFiring(): boolean {
    return this.keyboard.isFiring() || (this.pad?.isFiring() ?? false)
  }

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    return this.keyboard.wasItemPressed(kind) || (this.pad?.wasItemPressed(kind) ?? false)
  }

  endFrame(): void {
    this.keyboard.endFrame()
    this.pad?.endFrame()
  }

  reset(): void {
    this.keyboard.reset()
    this.pad?.reset()
  }
}

/** Connect/disconnect transition surfaced by GamepadManager for toasts. */
export interface GamepadEvent {
  player: 1 | 2
  type: 'connected' | 'disconnected'
}

/**
 * Device slotting over navigator.getGamepads(): the first two CONNECTED
 * pads map to player1 / player2 and slots stay STABLE across polls — a
 * mid-session unplug never swaps players' pads. All navigator access is
 * funneled through one overridable seam (`collect`) so the slotting and
 * transition logic is testable headlessly.
 */
export class GamepadManager {
  private p1Pad: GamepadSnapshot | null = null
  private p2Pad: GamepadSnapshot | null = null
  private events: GamepadEvent[] = []

  readonly p1 = new GamepadInput()
  readonly p2 = new GamepadInput()

  /** Overridable navigator seam — returns the live pads in slot order. */
  protected collect(): (GamepadSnapshot | null)[] {
    const nav = navigator as Navigator & { getGamepads?: () => (GamepadSnapshot | null)[] }
    if (!nav.getGamepads) return []
    return nav.getGamepads()
  }

  /**
   * Poll once per render frame: re-slot devices (stably), diff connect /
   * disconnect transitions, and feed both GamepadInputs.
   */
  poll(): void {
    const pads = this.collect()
    const connected: GamepadSnapshot[] = []
    for (const p of pads) {
      if (p && p.connected) connected.push(p)
    }
    const newP1 = connected[0] ?? null
    const newP2 = connected[1] ?? null
    if (!!newP1 !== !!this.p1Pad) {
      this.events.push({ player: 1, type: newP1 ? 'connected' : 'disconnected' })
    }
    if (!!newP2 !== !!this.p2Pad) {
      this.events.push({ player: 2, type: newP2 ? 'connected' : 'disconnected' })
    }
    this.p1Pad = newP1
    this.p2Pad = newP2
    this.p1.pollSnapshot(this.p1Pad)
    this.p2.pollSnapshot(this.p2Pad)
  }

  /** Headless test seam: poll with injected snapshots instead of navigator. */
  pollForTests(pads: (GamepadSnapshot | null)[]): void {
    const prevP1 = this.p1Pad
    const prevP2 = this.p2Pad
    const connected: GamepadSnapshot[] = []
    for (const p of pads) {
      if (p && p.connected) connected.push(p)
    }
    this.p1Pad = connected[0] ?? null
    this.p2Pad = connected[1] ?? null
    if (!!this.p1Pad !== !!prevP1) {
      this.events.push({ player: 1, type: this.p1Pad ? 'connected' : 'disconnected' })
    }
    if (!!this.p2Pad !== !!prevP2) {
      this.events.push({ player: 2, type: this.p2Pad ? 'connected' : 'disconnected' })
    }
    this.p1.pollSnapshot(this.p1Pad)
    this.p2.pollSnapshot(this.p2Pad)
  }

  get p1Snapshot(): GamepadSnapshot | null {
    return this.p1Pad
  }

  get p2Snapshot(): GamepadSnapshot | null {
    return this.p2Pad
  }

  /** Drain queued connect/disconnect transitions (Game wires them to toasts). */
  consumeEvents(): GamepadEvent[] {
    const out = this.events
    this.events = []
    return out
  }
}
