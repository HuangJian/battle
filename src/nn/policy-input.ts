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
import { NNModel, buildModelFromText } from './infer'
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
let cachedModel: NNModel | null = null
let cachedModelPath: string | null = null

function loadModel(opts: NNInputOptions): NNModel {
  let path = opts.weightsPath
  if (!path) {
    const dir = opts.weightsDir ?? join(process.cwd(), 'nn-training', 'weights')
    path = resolveLatestWeights(dir) ?? undefined
  }
  if (!path || !existsSync(path)) {
    throw new Error(`NNInput: no weights found (weightsPath=${opts.weightsPath}, dir=${opts.weightsDir})`)
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
 * lifetime contract as GodAIInput). `getMoveDirection()` / `isFiring()` /
 * `wasItemPressed()` lazily run inference on the first call of each tick and
 * hold the result until `endFrame()` clears the per-tick flag.
 */
export class NNInput implements InputLike {
  private world: World
  private model: NNModel
  private encoder = new ObsEncoder()
  private K: number

  // committed (held) action for the current inter-decision window
  private moveDir: Direction | null = null
  private firing = false
  private guardPulse = false
  private frenzyPulse = false

  // per-tick evaluation guard
  private thought = false
  private forceThink = true // think at least once after reset()
  private prevGuardStock = 0
  private prevFrenzyStock = 0

  constructor(world: World, opts: NNInputOptions = {}) {
    this.world = world
    this.model = loadModel(opts)
    this.K = opts.decisionK ?? 10
  }

  getMoveDirection(): Direction | null {
    if (!this.thought) this.think()
    return this.moveDir
  }

  isFiring(): boolean {
    if (!this.thought) this.think()
    return this.firing
  }

  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    if (!this.thought) this.think()
    if (kind === 'guard') return this.guardPulse
    if (kind === 'frenzy') return this.frenzyPulse
    return false
  }

  endFrame(): void {
    // Release the per-tick edge pulses and allow a fresh decision next tick.
    this.thought = false
    this.guardPulse = false
    this.frenzyPulse = false
  }

  reset(): void {
    this.thought = false
    this.forceThink = true
    this.moveDir = null
    this.firing = false
    this.guardPulse = false
    this.frenzyPulse = false
    this.prevGuardStock = 0
    this.prevFrenzyStock = 0
  }

  /** Run the NN forward at most once per tick, gated by the decision cadence. */
  private think(): void {
    if (this.thought) return
    const w = this.world

    // Decide whether a new decision is due this tick.
    const frame = w.frame
    const guardStock = w.guardStock
    const frenzyStock = w.frenzyStock
    const itemAppeared = guardStock > this.prevGuardStock || frenzyStock > this.prevFrenzyStock
    const due = this.forceThink || frame % this.K === 0 || itemAppeared
    this.prevGuardStock = guardStock
    this.prevFrenzyStock = frenzyStock

    if (!due) {
      // Hold the previously committed action; do NOT run the conv forward.
      this.thought = true
      return
    }

    // Encode current world state and run the forward pass.
    this.encoder.encode(w)
    this.model.forward(this.encoder.obs, this.encoder.scalars)

    const masks = computeMasks(w)

    // --- move head (argmax over 5) ---
    const mv = this.model.moveLogits
    let bestMove = 0
    let bestMoveV = mv[0]
    for (let i = 1; i < 5; i++) if (mv[i] > bestMoveV) { bestMoveV = mv[i]; bestMove = i }
    // v1 move mask is all-valid; fall back to none if the chosen slot is masked.
    if (masks.move[bestMove] !== 1) bestMove = 0
    this.moveDir = bestMove === 0 ? null : DIR_DECODE[bestMove - 1]

    // --- fire head (argmax over 2: 0 release, 1 hold) ---
    const fr = this.model.fireLogits
    const fireHold = fr[1] > fr[0] && masks.fire[1] === 1
    this.firing = fireHold

    // --- item head (argmax over 3: 0 none, 1 guard, 2 frenzy) ---
    const it = this.model.itemLogits
    let bestItem = 0
    let bestItemV = it[0]
    for (let i = 1; i < 3; i++) if (it[i] > bestItemV) { bestItemV = it[i]; bestItem = i }
    this.guardPulse = bestItem === 1 && masks.item[1] === 1
    this.frenzyPulse = bestItem === 2 && masks.item[2] === 1

    this.forceThink = false
    this.thought = true
  }
}
