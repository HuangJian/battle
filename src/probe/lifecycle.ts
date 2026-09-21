import type { GameState } from '../types'

// ================================================================
// Human-opening probe — loop lifecycle policy (plan v7 §T3 ⑦/⑧)
//
// The probe's two exemptions from the normal run lifecycle are DECISIONS about
// what the fixed-timestep loop may do:
//
//   ⑦ `max_ticks` budget — a probe run stops at the budget instead of ticking
//      until some terminal state; nothing under `src/` reads `maxTicks`, so the
//      budget is a Game-layer concern.
//   ⑧ terminal branches — a probe CLEAR goes to the session pack instead of the
//      ReplayManager, and a probe DEATH must NOT enter recovery: with one life
//      death is the expected end of most runs, and `startRecovery()` (the
//      snapshot-rewind menu) would hijack the operator's flow and break the
//      "1 life" semantics.
//
// Both live here as PURE functions of the loop's own inputs, because the loop
// itself (`LoopController`, which takes the whole `Game`) cannot be unit-tested:
// the repo has no DOM test environment. Keeping the decision — not a copy of it
// — in a pure module is what makes "gameover 不进 recovery" a test assertion
// instead of a code review.
//
// GameLoop calls these; `tests/probe-lifecycle.test.ts` pins the table AND
// guards the wiring (a re-inlined branch would fail the source guard).
//
// Pure module: no DOM, no fs, no RNG, reads nothing (all inputs are arguments).
// ================================================================

/** How a probe run can end. Mirrors `ProbeController.finishRun`'s argument. */
export type ProbeRunOutcome = 'clear' | 'gameover' | 'timeout'

/**
 * Top-of-tick decision for a probe run (⑦): tick normally, or finish on the
 * budget. Applies ONLY to an active probe — `budgetSpent` is false for a normal
 * run, and a normal run is never short-circuited by it.
 */
export function probeTickAction(
  probeActive: boolean,
  budgetSpent: boolean,
): 'tick' | 'finish-timeout' {
  return probeActive && budgetSpent ? 'finish-timeout' : 'tick'
}

export interface ProbeTerminalInput {
  /** `Game.probe.isActive` — false for every normal run. */
  probeActive: boolean
  /** `world.state` after this tick. */
  state: GameState
  /** `Game.prevWorldState` (the state before this tick). */
  prevState: GameState
  /** The loop's per-call guard: game over is intercepted exactly once. */
  enteredGameOver: boolean
}

/**
 * What the loop must do after a tick lands on a terminal state.
 *
 *   'none'         — keep ticking (also covers a run already parked in
 *                    'paused' by `finishRun`, and a stage clear that was
 *                    already handled on an earlier iteration).
 *   'finish-probe' — hand the run to the probe session pack. `stop` = break out
 *                    of the tick loop (a step-clear parks the world in 'paused'
 *                    instead, which fails the loop's own state gate next
 *                    iteration — see `ProbeController.finishRun`).
 *   'finalize'     — normal run: save the victory recording.
 *   'recover'      — normal run: finalize the defeat recording and enter the
 *                    snapshot-rewind recovery flow.
 */
export type ProbeTerminalAction =
  | { kind: 'none' }
  | { kind: 'finish-probe'; outcome: ProbeRunOutcome; stop: boolean }
  | { kind: 'finalize'; outcome: 'clear' }
  | { kind: 'recover' }

export function probeTerminalAction(input: ProbeTerminalInput): ProbeTerminalAction {
  const { probeActive, state, prevState, enteredGameOver } = input

  if (state === 'stageclear' && prevState !== 'stageclear') {
    // Probe clears go to the pack, not the ReplayManager: the pack owns the
    // exact bytes it will be annotated from, and replay retention must not be
    // able to evict that evidence.
    if (probeActive) return { kind: 'finish-probe', outcome: 'clear', stop: false }
    return { kind: 'finalize', outcome: 'clear' }
  }

  if (state === 'gameover' && !enteredGameOver) {
    // ⑧ — a 1-life probe dies constantly, and recovery would both hijack the
    // flow and break "1 life". The probe NEVER returns 'recover'.
    if (probeActive) return { kind: 'finish-probe', outcome: 'gameover', stop: true }
    return { kind: 'recover' }
  }

  return { kind: 'none' }
}
