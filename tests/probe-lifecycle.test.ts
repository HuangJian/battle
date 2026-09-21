import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { probeTickAction, probeTerminalAction } from '../src/probe/lifecycle'
import type { GameState } from '../src/types'

// ============================================================
// Human-opening probe — loop lifecycle policy (plan v7 §T3 ⑦/⑧)
//
// `LoopController` takes the whole `Game` and this repo has no DOM test
// environment, so the loop's probe branches used to be verified by code review
// only. The DECISIONS now live in `src/probe/lifecycle.ts` and are pinned here;
// the last test guards the wiring so the loop cannot re-inline them.
// ============================================================

/** Every GameState, so the sweeps below cannot silently miss one. */
const ALL_STATES: GameState[] = [
  'menu',
  'playing',
  'paused',
  'stageclear',
  'gameover',
  'victory',
  'recovery',
]

describe('probe tick budget (⑦ max_ticks)', () => {
  it('finishes only for an active probe that spent its budget', () => {
    expect(probeTickAction(true, true)).toBe('finish-timeout')
    expect(probeTickAction(true, false)).toBe('tick')
    expect(probeTickAction(false, true)).toBe('tick')
    expect(probeTickAction(false, false)).toBe('tick')
  })

  it('never short-circuits a normal run', () => {
    // The loop passes `probe.tickBudgetSpent` for both kinds of run; a normal
    // run must tick regardless of what that flag happens to be.
    expect(probeTickAction(false, true)).toBe('tick')
  })
})

describe('probe terminal branches (⑧)', () => {
  it('probe + stage clear → the session pack, never the ReplayManager', () => {
    expect(
      probeTerminalAction({
        probeActive: true,
        state: 'stageclear',
        prevState: 'playing',
        enteredGameOver: false,
      }),
    ).toEqual({ kind: 'finish-probe', outcome: 'clear', stop: false })
  })

  it('probe + death → finish the run and STOP; recovery is never entered', () => {
    const action = probeTerminalAction({
      probeActive: true,
      state: 'gameover',
      prevState: 'playing',
      enteredGameOver: false,
    })
    expect(action).toEqual({ kind: 'finish-probe', outcome: 'gameover', stop: true })
  })

  it('a normal stage clear still finalizes the victory recording', () => {
    expect(
      probeTerminalAction({
        probeActive: false,
        state: 'stageclear',
        prevState: 'playing',
        enteredGameOver: false,
      }),
    ).toEqual({ kind: 'finalize', outcome: 'clear' })
  })

  it('a normal death still enters recovery (the probe exemption is opt-in)', () => {
    expect(
      probeTerminalAction({
        probeActive: false,
        state: 'gameover',
        prevState: 'playing',
        enteredGameOver: false,
      }),
    ).toEqual({ kind: 'recover' })
  })

  it('does nothing for a stage clear already handled, or a run already parked', () => {
    // Stage clear → next iteration sees prev === stageclear (no double finalize).
    expect(
      probeTerminalAction({
        probeActive: true,
        state: 'stageclear',
        prevState: 'stageclear',
        enteredGameOver: false,
      }),
    ).toEqual({ kind: 'none' })
    // A finished probe run is parked in 'paused' by `finishRun` — the loop must
    // not react to it again.
    expect(
      probeTerminalAction({
        probeActive: true,
        state: 'paused',
        prevState: 'gameover',
        enteredGameOver: true,
      }),
    ).toEqual({ kind: 'none' })
    // Game over is intercepted exactly once (the loop's guard).
    expect(
      probeTerminalAction({
        probeActive: false,
        state: 'gameover',
        prevState: 'gameover',
        enteredGameOver: true,
      }),
    ).toEqual({ kind: 'none' })
  })

  it('EVERY probe state is non-recovery, and every death stops the loop', () => {
    // The invariant behind "gameover 不进 recovery", swept over all states:
    // a probe run must never produce the recovery action.
    for (const state of ALL_STATES) {
      for (const prevState of ALL_STATES) {
        const action = probeTerminalAction({
          probeActive: true,
          state,
          prevState,
          enteredGameOver: state === 'gameover',
        })
        expect(action.kind).not.toBe('recover')
        expect(action.kind === 'finish-probe' ? action.outcome !== 'timeout' : true).toBe(true)
      }
    }
    for (const prevState of ALL_STATES) {
      const action = probeTerminalAction({
        probeActive: true,
        state: 'gameover',
        prevState,
        enteredGameOver: false,
      })
      expect(action).toEqual({ kind: 'finish-probe', outcome: 'gameover', stop: true })
    }
  })

  it('leaves every non-terminal state alone, probe or not', () => {
    for (const state of ALL_STATES) {
      if (state === 'stageclear' || state === 'gameover') continue
      for (const probeActive of [true, false]) {
        expect(
          probeTerminalAction({ probeActive, state, prevState: 'playing', enteredGameOver: false }),
        ).toEqual({ kind: 'none' })
      }
    }
  })
})

describe('wiring — the loop uses the tested policy, not a copy of it', () => {
  const LOOP = readFileSync(join(import.meta.dir, '..', 'src/game/GameLoop.ts'), 'utf8')

  it('GameLoop routes both decisions through src/probe/lifecycle.ts', () => {
    expect(LOOP).toContain('probeTickAction(')
    expect(LOOP).toContain('probeTerminalAction(')
  })

  it('the loop no longer hard-codes an outcome or a recovery for a probe run', () => {
    // Outcome literals belong to the policy; the loop consumes what it returns.
    expect(LOOP).toContain('this.g.probe.finishRun(terminal.outcome)')
    expect(LOOP).not.toContain("finishRun('gameover')")
    expect(LOOP).not.toContain("finishRun('clear')")
    // No probe branch may reach recovery: there is exactly one startRecovery()
    // call left in the live branch, inside the policy's 'recover' case.
    expect(LOOP.match(/startRecovery\(\)/g)).toHaveLength(1)
    expect(LOOP).toMatch(/kind === 'recover'[\s\S]{0,600}startRecovery\(\)/)
  })
})
