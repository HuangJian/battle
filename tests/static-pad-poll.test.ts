import { describe, it, expect } from 'bun:test'
import { wantsStaticPadPoll, anyGamepadConnected, STATIC_PAD_POLL_MS } from '../src/game/GameLoop'

/**
 * Static-screen pad-poll gating (2p-review R2-P1): static screens (menu /
 * paused / gameover / victory) run no rAF loop, so the GamepadManager is only
 * polled on keydown — a pure-gamepad user's pad edges freeze (menu Start can't
 * confirm, pause Start can't resume, gameover Start can't return). The fix is
 * a minimal interval gated on: game live (not stopped / hidden / mid-playback)
 * AND a pad present AND a static screen. These pure decisions are what the
 * LoopController driver applies; the DOM/rAF mechanics themselves are covered
 * by the真机 manual checklist in the review.
 */

describe('wantsStaticPadPoll — R2-P1 presence gating', () => {
  const live = {
    running: true,
    hidden: false,
    playback: null,
    padPresent: true,
  }

  it('true on every loop-idle static screen when a pad is present', () => {
    for (const worldState of ['menu', 'paused', 'gameover', 'victory']) {
      expect(wantsStaticPadPoll({ ...live, worldState })).toBe(true)
    }
  })

  it('false on action states (the rAF loop owns pad polling there)', () => {
    for (const worldState of ['playing', 'stageclear', 'recovery']) {
      expect(wantsStaticPadPoll({ ...live, worldState })).toBe(false)
    }
  })

  it('false without a pad — keyboard-only users keep the 0-loop idle', () => {
    expect(wantsStaticPadPoll({ ...live, padPresent: false, worldState: 'menu' })).toBe(false)
  })

  it('false when the game is stopped, hidden, or mid-playback', () => {
    expect(wantsStaticPadPoll({ ...live, running: false, worldState: 'menu' })).toBe(false)
    expect(wantsStaticPadPoll({ ...live, hidden: true, worldState: 'menu' })).toBe(false)
    expect(wantsStaticPadPoll({ ...live, playback: {}, worldState: 'menu' })).toBe(false)
    // Playback drives the world into LOW_POWER states but OWNS its own input.
    expect(wantsStaticPadPoll({ ...live, playback: {}, worldState: 'gameover' })).toBe(false)
  })

  it('the cadence is a sane sub-second interval (not a busy loop)', () => {
    expect(STATIC_PAD_POLL_MS).toBeGreaterThan(0)
    expect(STATIC_PAD_POLL_MS).toBeLessThanOrEqual(1000)
  })
})

describe('anyGamepadConnected — R2-P1 presence scan (navigator seam)', () => {
  it('reads no-pads from undefined / empty / all-null inputs', () => {
    expect(anyGamepadConnected(undefined)).toBe(false)
    expect(anyGamepadConnected([])).toBe(false)
    expect(anyGamepadConnected([null, null])).toBe(false)
  })

  it('true iff at least one pad is connected', () => {
    expect(anyGamepadConnected([{ connected: false } as Gamepad, null])).toBe(false)
    expect(anyGamepadConnected([{ connected: true } as Gamepad, null])).toBe(true)
    expect(anyGamepadConnected([null, { connected: true } as Gamepad])).toBe(true)
  })
})
