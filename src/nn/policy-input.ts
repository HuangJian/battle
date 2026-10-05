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
import { decodeMove } from './action-space'
import {
  createDecisionGateConfig,
  createDecisionGateState,
  decisionDue,
  decisionReadout,
  markDecision,
  type DecisionGateConfig,
  type DecisionGateState,
  type DecisionReadout,
} from './decision-gate'
import { buildModelFromText, type ModelLike } from './infer'
import { resolveLatestWeights } from './weights'
import { join } from 'path'
import { readFileSync, existsSync } from 'fs'

export interface NNInputOptions {
  /** Explicit weights file. If omitted, the latest versioned/active weights
   *  under `weightsDir` are auto-discovered (plan: no manual rename). */
  weightsPath?: string
  /** Directory to auto-discover weights in (default: <cwd>/nn-training/weights). */
  weightsDir?: string
  /** Decision-tick subsample period K (training default: 10). */
  decisionK?: number
  /**
   * x2 事件 rung（plan/new-era-stop.plan.md §6 R2）：给决策门加 threat-ONSET 事件
   * （均匀 K ∪ 事件，Δt ≥ 3）。默认 false = 均匀 K 旧行为（x1 首刀逐字节不变）。
   * **判决链与部署链必须同值**（tools/sim/eval-game-parity.test.ts 钉住）。
   */
  decisionEvents?: boolean
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
  /** 决策门（唯一实现 = src/nn/decision-gate.ts）：均匀 K ∪ threat-ONSET + 最小间隔闸。 */
  private gateCfg: DecisionGateConfig
  private gate: DecisionGateState = createDecisionGateState()

  // committed (held) action for the current inter-decision window
  private moveDir: Direction | null = null
  // B案 (plan/new-era-stop): move index 0 = **STOP** — `moveDir = null` lets
  // `SimulationPlayer` set `moving = false`. "Continue straight" is a 1..4
  // direction (the heading is observable in ch6 `self`), so there is no keep
  // class and no held-direction (keep) semantic. dims/heads/masks are unchanged.
  private firing = false

  // A decision is committed (and holds until the next due tick). Only the lazy
  // fallback for callers that never reset()/endFrame() decides on read.
  private committed = false

  constructor(world: World, opts: NNInputOptions = {}) {
    this.world = world
    this.model = loadModel(opts)
    this.K = opts.decisionK ?? 10
    this.gateCfg = createDecisionGateConfig(opts.decisionEvents === true, this.K)
  }

  /** 决策门读数（Δt/事件计数；测试与部署诊断用；只读）。 */
  readDecisionStats(): DecisionReadout {
    return decisionReadout(this.gate)
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
   *
   * x2：到期判定全部走 `decisionDue`（shared gate）——调用方**每 tick** 调本函数即可，
   * 沿检测（prevThreat）与最小间隔闸要的就是逐 tick 喂。
   */
  endFrame(): void {
    const w = this.world
    if (w.state === 'playing' && decisionDue(w.frame, w, this.gate, this.gateCfg)) {
      this.decide()
    }
  }

  reset(): void {
    this.moveDir = null
    this.firing = false
    // 新一局 = 新决策流（Δt 读数不跨局；suppressEventsUntilTick 归零）。
    this.gate = createDecisionGateState()
    // Tick 0's decision, on the freshly loaded stage (callers reset() after
    // `world.loadStageData(...)` — the builders' `t = 0` observation).
    this.decide()
    markDecision(this.gate, this.world.frame)
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
    // v4：extra 一并喂（旧布局模型忽略第三参；新架构必须）。
    this.model.forward(this.encoder.obs, this.encoder.scalars, this.encoder.extra)

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
    // B案: index 0 = STOP (decodeMove(0) === null ⇒ moving=false). 1..4 = the
    // four directions. Held for the rest of the inter-decision window.
    this.moveDir = decodeMove(bestMove)

    // --- fire head (argmax over 2: 0 release, 1 hold) ---
    const fr = this.model.fireLogits
    const fireHold = fr[1] > fr[0] && masks.fire[1] === 1
    this.firing = fireHold

    // v2: item head removed — AI never activates guard/frenzy/rewind.

    this.committed = true
  }
}
