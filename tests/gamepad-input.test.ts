import { describe, it, expect } from 'bun:test'
import type { GamepadSnapshot } from '../src/game/GamepadInput'
import {
  readSnapshot,
  GamepadInput,
  CompositeInput,
  GamepadManager,
  GAMEPAD_DEADZONE,
  GAMEPAD_BUTTONS,
} from '../src/game/GamepadInput'
import { DEFAULT_KEYS, DEFAULT_P2_KEYS, type InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'

/**
 * Gamepad support (DECISIONS §351): the browser Gamepad API is polled —
 * there are no reliable per-frame `justPressed` events — so the edge
 * detection lives in a pure snapshot-diff layer that is regression-tested
 * headlessly (AGENTS §8): fixed snapshots, no DOM, no rAF.
 *
 * Standard-gamepad mapping: left stick / d-pad = move, A/Cross (0) = fire,
 * B/Circle (1) = guard, X/Square (2) = frenzy, Y/Triangle (3) = rewind,
 * Start (9) = pause/menu-confirm.
 */

/** Build a Gamepad-like plain object (tests construct these directly). */
function pad(over: Partial<GamepadSnapshot> = {}): GamepadSnapshot {
  return {
    axes: [0, 0, 0, 0],
    buttons: new Array(17).fill(0).map(() => ({ pressed: false, value: 0 })),
    connected: true,
    mapping: 'standard',
    ...over,
  }
}

describe('Gamepad snapshot reader (pure)', () => {
  it('maps dpad buttons to directions, stick priority over dpad', () => {
    // D-pad up (button 12).
    let s = pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 12, value: i === 12 ? 1 : 0 })) })
    expect(readSnapshot(s, GAMEPAD_DEADZONE).dir).toBe('up')
    // D-pad right (button 15).
    s = pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 15, value: i === 15 ? 1 : 0 })) })
    expect(readSnapshot(s, GAMEPAD_DEADZONE).dir).toBe('right')
    // Stick pushes left beyond deadzone → left, overriding d-pad up.
    s = pad({ axes: [-0.8, 0, 0, 0], buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 12, value: i === 12 ? 1 : 0 })) })
    expect(readSnapshot(s, GAMEPAD_DEADZONE).dir).toBe('left')
    // Stick within deadzone → ignored (d-pad wins instead).
    s = pad({ axes: [-GAMEPAD_DEADZONE * 0.5, 0, 0, 0], buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 12, value: i === 12 ? 1 : 0 })) })
    expect(readSnapshot(s, GAMEPAD_DEADZONE).dir).toBe('up')
  })

  it('fire is button 0; guard/frenzy/rewind are 1/2/3', () => {
    const s = pad({
      buttons: new Array(17)
        .fill(0)
        .map((_, i) => ({ pressed: i === 0 || i === 2, value: i === 0 || i === 2 ? 1 : 0 })),
    })
    const f = readSnapshot(s, GAMEPAD_DEADZONE)
    expect(f.fire).toBe(true)
    expect(f.frenzy).toBe(true)
    expect(f.guard).toBe(false)
    expect(f.rewind).toBe(false)
  })

  it('pause is button 9 (Start)', () => {
    const s = pad({
      buttons: new Array(17)
        .fill(0)
        .map((_, i) => ({ pressed: i === 9, value: i === 9 ? 1 : 0 })),
    })
    expect(readSnapshot(s, GAMEPAD_DEADZONE).pause).toBe(true)
  })

  it('disconnected pads read as all-neutral', () => {
    const s = pad({ connected: false, axes: [1, 1, 0, 0] })
    const f = readSnapshot(s, GAMEPAD_DEADZONE)
    expect(f.dir).toBeNull()
    expect(f.fire).toBe(false)
    expect(f.pause).toBe(false)
  })
})

describe('GamepadInput edge detection', () => {
  it('held buttons fire exactly once (edge semantics for wasItemPressed/pause)', () => {
    const g = new GamepadInput()
    g.pollSnapshot(pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 1, value: i === 1 ? 1 : 0 })) }))
    expect(g.wasItemPressed('guard')).toBe(true)
    g.endFrame()
    // Still held — no new edge.
    g.pollSnapshot(pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 1, value: i === 1 ? 1 : 0 })) }))
    expect(g.wasItemPressed('guard')).toBe(false)
    g.endFrame()
    // Released, then pressed again → new edge.
    g.pollSnapshot(pad())
    g.endFrame()
    g.pollSnapshot(pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 1, value: i === 1 ? 1 : 0 })) }))
    expect(g.wasItemPressed('guard')).toBe(true)
  })

  it('isFiring is level-based (held), movement is level-based', () => {
    const g = new GamepadInput()
    g.pollSnapshot(pad())
    expect(g.isFiring()).toBe(false)
    expect(g.getMoveDirection()).toBeNull()
    g.pollSnapshot(pad({ axes: [0, -1, 0, 0], buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 0, value: 1 })) }))
    expect(g.getMoveDirection()).toBe('up')
    expect(g.isFiring()).toBe(true)
    // No endFrame between polls is fine for level reads.
    g.pollSnapshot(pad())
    expect(g.isFiring()).toBe(false)
    expect(g.getMoveDirection()).toBeNull()
  })

  it('isPausePressed edges once per press', () => {
    const g = new GamepadInput()
    g.pollSnapshot(pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 9, value: 1 })) }))
    expect(g.isPausePressed()).toBe(true)
    g.endFrame()
    g.pollSnapshot(pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 9, value: 1 })) }))
    expect(g.isPausePressed()).toBe(false)
  })

  it('reset() clears edges and previous-state (stale press cannot carry across screens)', () => {
    const g = new GamepadInput()
    g.pollSnapshot(pad({ buttons: new Array(17).fill(0).map((_, i) => ({ pressed: i === 9, value: 1 })) }))
    g.reset()
    expect(g.isPausePressed()).toBe(false)
  })
})

describe('CompositeInput — keyboard + gamepad merge', () => {
  const idle: InputLike = {
    getMoveDirection: () => null,
    isFiring: () => false,
    wasItemPressed: () => false,
    endFrame: () => {},
    reset: () => {},
  }

  function stub(dir: Direction | null, fire: boolean, guard = false): InputLike {
    return {
      ...idle,
      getMoveDirection: () => dir,
      isFiring: () => fire,
      wasItemPressed: (k) => k === 'guard' && guard,
    }
  }

  it('gamepad direction wins over keyboard; keyboard fire ORs with pad fire', () => {
    const kb = stub('left', false)
    const gp = stub('right', true)
    const c = new CompositeInput(kb, gp)
    expect(c.getMoveDirection()).toBe('right')
    expect(c.isFiring()).toBe(true)
  })

  it('falls back to keyboard when the pad is idle', () => {
    const kb = stub('up', true)
    const c = new CompositeInput(kb, stub(null, false))
    expect(c.getMoveDirection()).toBe('up')
    expect(c.isFiring()).toBe(true)
  })

  it('super-item edges OR across sources; endFrame reaches both', () => {
    let kbEnded = false
    const kb: InputLike = {
      ...idle,
      wasItemPressed: (k) => k === 'guard',
      endFrame: () => {
        kbEnded = true
      },
    }
    const gp = stub(null, false, false)
    const c = new CompositeInput(kb, gp)
    expect(c.wasItemPressed('guard')).toBe(true)
    c.endFrame()
    expect(kbEnded).toBe(true)
  })

  it('implements the full InputLike contract (null pad tolerated)', () => {
    const c = new CompositeInput(stub('down', false), null)
    expect(c.getMoveDirection()).toBe('down')
    c.reset()
  })
})

describe('GamepadManager — device slotting (pure, injected snapshots)', () => {
  function managerWith(pads: (GamepadSnapshot | null)[]): GamepadManager {
    const m = new GamepadManager()
    m.pollForTests(pads)
    return m
  }

  it('assigns pad[0] to player1, pad[1] to player2, and keeps slots stable', () => {
    const a = pad()
    const b = pad()
    const m = managerWith([a, b])
    expect(m.p1Snapshot).toBe(a)
    expect(m.p2Snapshot).toBe(b)
    // P2 unplugs; P1 keeps its slot (no swap).
    const a2 = pad()
    const m2 = managerWith([a2, null])
    expect(m2.p1Snapshot).toBe(a2)
    expect(m2.p2Snapshot).toBeNull()
  })

  it('a newly plugged second pad takes the free P2 slot', () => {
    const a = pad()
    const m = managerWith([a, null])
    expect(m.p2Snapshot).toBeNull()
    const b = pad()
    const m2 = managerWith([a, b])
    expect(m2.p2Snapshot).toBe(b)
  })

  it('reports connect/disconnect transitions for toasts', () => {
    const m = managerWith([pad(), null])
    // First-ever poll transitions from "nothing" to "P1 present" — that IS a
    // connect event (so a pad plugged in before page load still greets).
    expect(m.consumeEvents()).toEqual([{ player: 1, type: 'connected' }])
    // Steady state across repeated polls of the SAME manager: no transitions.
    m.pollForTests([pad(), null])
    expect(m.consumeEvents()).toEqual([])
    // P2 plugs in on the same manager → one connect for P2 only.
    m.pollForTests([pad(), pad()])
    expect(m.consumeEvents()).toEqual([{ player: 2, type: 'connected' }])
    // Both unplug → two disconnects.
    m.pollForTests([null, null])
    expect(m.consumeEvents()).toEqual([
      { player: 1, type: 'disconnected' },
      { player: 2, type: 'disconnected' },
    ])
  })

  it('button indices match the standard-mapping constants', () => {
    expect(GAMEPAD_BUTTONS.fire).toBe(0)
    expect(GAMEPAD_BUTTONS.guard).toBe(1)
    expect(GAMEPAD_BUTTONS.frenzy).toBe(2)
    expect(GAMEPAD_BUTTONS.rewind).toBe(3)
    expect(GAMEPAD_BUTTONS.pause).toBe(9)
    expect(GAMEPAD_BUTTONS.dpadUp).toBe(12)
    expect(GAMEPAD_BUTTONS.dpadDown).toBe(13)
    expect(GAMEPAD_BUTTONS.dpadLeft).toBe(14)
    expect(GAMEPAD_BUTTONS.dpadRight).toBe(15)
  })
})

describe('P2 key bindings unaffected by gamepad support (contract guard)', () => {
  it('keyboard defaults stay unchanged', () => {
    expect(DEFAULT_KEYS.fire).toBe('Space')
    expect(DEFAULT_P2_KEYS.fire).toBe('KeyF')
    expect(DEFAULT_P2_KEYS.guard).toBe('KeyR')
    expect(DEFAULT_P2_KEYS.rewind).toBe('KeyG')
  })
})
