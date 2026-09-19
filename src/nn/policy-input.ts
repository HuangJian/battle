/**
 * policy-input.ts — NN player input for the headless simulation (plan §NN-M1).
 *
 * Implements `InputLike` so the existing `runSimulation` (or a God-AI coop
 * slot) can drive the player tank with the trained NN policy, exactly like
 * GodAIInput / ReplayInput / AutoFireInput do. No World mutation — only reads.
 *
 * Decision-tick gating (plan §1.3): the BC policy is trained on event-type
 * decision ticks (turn / fire-edge / item / subsample every K ticks). The
 * tank input is *held* semantics, so between decision ticks we keep the last
 * committed action and only re-run the (relatively expensive) conv forward
 * when a new decision is due. This (a) matches the training distribution and
 * (b) cuts inference count ~10x vs running every tick.
 */

import type { Direction } from '../constants'
import type { World } from '../game/World'
import type { InputLike } from '../game/Input'
import { ObsEncoder, computeMasks } from './obs-encoder'
import { buildModelFromText, type ModelLike } from './infer'
import { resolveLatestWeights } from './weights'
import { join } from 'path'
import { readFileSync, existsSync } from 'fs'

const DIR_DECODE: Direction[] = ['up', 'down', 'left', 'right']

export interface NNInputOptions {
  /** Explicit weights file. If omitted, the latest versioned/active weights
   *  under `weightsDir` are auto-discovered (plan: no manual rename). */
  weightsPath?: string
  /** Directory to auto-discover weights in (default: <cwd>/nn-training/weights). */
  weightsDir?: string
  /** Decision-tick subsample period K (training default: 10). */
  decisionK?: number
}

// ---- module-level model cache (one load per process / per worker thread) ----
let cachedModel: ModelLike | null = null
let cachedModelPath: string | null = null

function loadModel(opts: NNInputOptions): ModelLike {
  let path = opts.weightsPath
  if (!path) {
    const dir = opts.weightsDir ?? join(process.cwd(), 'nn-training', 'weights')
    path = resolveLatestWeights(dir) ?? undefined
  }
  if (!path || !existsSync(path)) {
    throw new Error(
      `NNInput: no weights found (weightsPath=${opts.weightsPath}, dir=${opts.weightsDir})`,
    )
  }
  // Reuse the decoded model when the resolved path is unchanged.
  if (cachedModel && cachedModelPath === path) return cachedModel
  const text = readFileSync(path, 'utf8')
  const model = buildModelFromText(text)
  cachedModel = model
  cachedModelPath = path
  return model
}

/**
 * NN-driven player input. Constructed with the live `world` reference (same
 * lifetime contract as GodAIInput).
 *
 * Decision instant (2026-09-19): a decision is taken on the state at the **end of
 * a tick** — i.e. inside `endFrame()`, plus once at `reset()` for tick 0 — and it
 * drives the following tick(s) until the next due decision. That state is exactly
 * the state every corpus/eval builder observes at its own loop iteration `t`, right
 * before its `sim.tick()` (export-rl-rollout.ts:642, export-nn-replays.ts:116,
 * export-eval-game.ts:482), so the policy sees the same state distribution at
 * training time, at eval time and on remote nodes.
 *
 * Why not decide mid-tick: `Simulation.tick()` decrements timers, arms mines and
 * runs `updateSpawning()` *before* reading player input (Simulation.ts:205-245), so
 * a decision taken there sees a state the policy was never trained on — measured
 * 2026-09-19: identical (weights, stage, seed) diverged from the node-side
 * export-eval-game in 6/6 games (first action flip at tick 290 of stage 0 seed 1).
 *
 * Inferences are gated by the K-subsample like the builders do: cadence is checked
 * on `world.frame` (= completed ticks = the builders' `t`), so due ticks are
 * t = 0, K, 2K, … for all implementations. `getMoveDirection()` / `isFiring()` only
 * read the committed action; they never run the forward mid-tick.
 */
export class NNInput implements InputLike {
  private world: World
  private model: ModelLike
  private encoder = new ObsEncoder()
  private K: number

  // committed (held) action for the current inter-decision window
  private moveDir: Direction | null = null
  // Last *commanded* direction. Held-action BC semantic: a `none` prediction
  // (move index 0) means "keep the current heading", NOT "stop". God-AI's
  // none-label comes from its held direction, so returning null here would
  // freeze the tank (SimulationPlayer sets moving=false on null) and lock the
  // world in a static state the model never recovers from -> 0% win. We hold
  // the last commanded direction instead, which keeps the state active.
  private lastDir: Direction = 'up'
  private firing = false

  // A decision is committed (and holds until the next due tick). Only the lazy
  // fallback for callers that never reset()/endFrame() decides on read.
  private committed = false

  constructor(world: World, opts: NNInputOptions = {}) {
    this.world = world
    this.model = loadModel(opts)
    this.K = opts.decisionK ?? 10
  }

  getMoveDirection(): Direction | null {
    if (!this.committed) this.decide()
    return this.moveDir
  }

  isFiring(): boolean {
    if (!this.committed) this.decide()
    return this.firing
  }

  // v2: AI 不使用主动道具 — guard/frenzy/rewind 一律不激活。
  wasItemPressed(kind?: 'guard' | 'frenzy' | 'rewind'): false {
    void kind
    return false
  }

  /**
   * Called by the caller after each completed `sim.tick()`. The world here is at
   * the decision state the corpus/eval builders observe (`world.frame` = their
   * `t`), so a due tick takes its decision now and holds it into the next tick.
   * Terminal states take no decision — the builders stop at the same point.
   */
  endFrame(): void {
    const w = this.world
    if (w.state === 'playing' && w.frame % this.K === 0) this.decide()
  }

  reset(): void {
    this.moveDir = null
    this.lastDir = 'up'
    this.firing = false
    // Tick 0's decision, on the freshly loaded stage (callers reset() after
    // `world.loadStageData(...)` — the builders' `t = 0` observation).
    this.decide()
  }

  /**
   * M1 divergence-probe support (tools/diag/divergence-probe.ts): force a
   * decision on the CURRENT state (bypassing the endFrame cadence) and read the
   * greedy argmax. Read-only relative to the World; never mutates gameplay state.
   */
  thinkNow(): void {
    this.decide()
  }

  /** Greedy move-argmax (0-4) from the latest forward pass. */
  moveArgmax(): number {
    const mv = this.model.moveLogits
    let b = 0
    for (let i = 1; i < 5; i++) if (mv[i] > mv[b]) b = i
    return b
  }

  /** Greedy fire-argmax (0 release / 1 hold) from the latest forward pass. */
  fireArgmax(): number {
    const fr = this.model.fireLogits
    return fr[1] > fr[0] ? 1 : 0
  }

  /**
   * Encode the current world state, run one forward pass and commit the greedy
   * action (masked argmax, held-action semantics). Called only at decision
   * instants: `reset()`, a due `endFrame()`, `thinkNow()`, or the lazy fallback
   * for callers that never drive the tick loop.
   */
  private decide(): void {
    const w = this.world

    // Encode current world state and run the forward pass.
    this.encoder.encode(w)
    this.model.forward(this.encoder.obs, this.encoder.scalars)

    const masks = computeMasks(w)

    // --- move head (argmax over 5) ---
    const mv = this.model.moveLogits
    let bestMove = 0
    let bestMoveV = mv[0]
    for (let i = 1; i < 5; i++)
      if (mv[i] > bestMoveV) {
        bestMoveV = mv[i]
        bestMove = i
      }
    // v1 move mask is all-valid; fall back to none if the chosen slot is masked.
    if (masks.move[bestMove] !== 1) bestMove = 0
    // Held-action semantic: index 0 (none) = keep current heading (see lastDir
    // field). Only a *real* direction updates lastDir; none holds it. This keeps
    // the tank moving and the world state active, avoiding the freeze deadlock.
    if (bestMove === 0) {
      this.moveDir = this.lastDir
    } else {
      this.lastDir = DIR_DECODE[bestMove - 1]
      this.moveDir = this.lastDir
    }

    // --- fire head (argmax over 2: 0 release, 1 hold) ---
    const fr = this.model.fireLogits
    const fireHold = fr[1] > fr[0] && masks.fire[1] === 1
    this.firing = fireHold

    // v2: item head removed — AI never activates guard/frenzy/rewind.

    this.committed = true
  }
}
