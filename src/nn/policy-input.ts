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

import { CELL, TICK_MS, type Direction } from '../constants'
import type { World } from '../game/World'
import type { InputLike } from '../game/Input'
import { ObsEncoder, computeMasks, restrictAdvanceMask } from './obs-encoder'
import { decodeMove } from './action-space'
import { HOLD_DIM, MOVE4_DECODE, decodeMove4 } from './action-space-v6'
import { corridorCells, nearestEnemyDistPx, nearestPickupCenter } from './danger-metrics'
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
import {
  ICE_GLIDE_MIN_SPEED,
  alternativeMoveDir,
  brickAheadWithin,
  brickBlocksAdvance,
  c4MoveBias,
  crossfireEscapeDir,
  duelShotReady,
  e1MoveBias,
  freezeAttackDir,
  leadMissShot,
  pincerBreakoutDir,
  reachablePickupDir,
  steelBlockedMove,
  steelOnlyShot,
  turnWillCommit,
  uselessShot,
  usefulFire,
} from './inference-rules'
import { iceGlideAdjust } from '../ai/god/Navigator'
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
  /**
   * 推理道具偏置（2026-11-12）：>0 且「道具在 `pickupRadius` 格内、最近敌车 >
   * `enemyClearCells` 格」时，给朝道具的 move logits 加这么多分。默认 0 = 关。
   */
  pickupBias?: number
  pickupRadius?: number
  enemyClearCells?: number
  /**
   * C1 v1 威胁车道扣分权重（plan/nn-input-inference-rules.plan.md §3 C1）。
   * ⚠ v1 在 175 对 A/B 中判负（control 39.43% → rule 26.29%，Δ −13.1pp，
   * p=0.0052）；**默认 0，不得在未重新预注册/新筛选段的情况下启用**。
   * >0 时对四个 move 方向按 threat/tti/laneE/hitDist 扣分。
   */
  c1Weight?: number
  /**
   * C4 v1 无敌窗主动换血/冲道具的 move-score 加成（默认 0）。
   * ⚠ v1 在 175 对 A/B 中**中性**（win 39.43% → 38.86%，Δ −0.57pp，p=1.0；
   * clearT 11105→10633 为次要读数），未过 win 门槛；**默认 0，不得部署**。
   * 仅 `shieldTimer>0`（真无敌）时生效；冻结窗不属于 C4（见 C5）。
   */
  c4Bias?: number
  c4PickupRadius?: number
  c4EnemyRadius?: number
  /** E1 残敌 BFS 目标偏置（0 = 关，默认 0）；仅最近敌 >8 格时生效。
   *  ⚠ v1 A/B 175 对：39.43% → 32.57%（Δ −6.86pp, p=0.162），墙钟 +20%
   *  ⇒ 默认 0，不得部署；BFS 版本仅作实验口。 */
  e1Bias?: number
  /** 顶钢空枪门（默认 false；只用于评估/部署推理，训练链恒 off）。
   *  175 对 screening +7.43pp（p=0.142）→ 700 对 confirm **+6.00pp**
   *  （p=0.0052, CI [+2.57,+9.57]）⇒ 确认通过；已接 `export-eval-game`
   *  （`--skip-steel-only-shots`），关闭态 3/3 byte-identical。 */
  skipSteelOnlyShots?: boolean
  /** 拆砖免绕路（默认 false；只用于评估/部署推理，训练链恒 off）。
   *  沿所选 move 方向：顶砖挡路（hold=1）或 2 格内有砖（推进）且策略没开火时，
   *  强制朝该方向开枪拆墙。基地保护圈内的砖不拆（`brickAheadWithin` 排除）。 */
  breakBricksOnPath?: boolean
  /** 冰面滑行预判（默认 false；实验口，部署待 A/B）：冰上滑行中请求反向时
   *  改为松键（hold=1、保持当前朝向），避免真倒车/过冲。复用 God `iceGlideAdjust`。 */
  releaseReverseOnIce?: boolean
  /**
   * B8 通用废射门（默认 false）：要开火但该方向静态 `usefulFire=false` 且首阻弹格
   * 不是 brick ⇒ 抑制。B1 的超集；A/B 两臂都开 B1，只测增量。
   */
  suppressUselessFire?: boolean
  /**
   * B10 预测门（默认 false）：`leadHitForDir=0`（外推后仍打不到）且首阻弹格不是
   * brick ⇒ 抑制。比 B8 更严。
   */
  suppressLeadMissFire?: boolean
  /** B11 钢挡路换向（默认 false）：所选方向 1 格被挡且车道首阻弹格是钢，另有可走方向 ⇒ 改走。 */
  avoidSteelPath?: boolean
  /** C3 包夹突围偏置权重（默认 0）：4 格内敌人在两侧同时出现 ⇒ 朝远离最近敌方向加分。 */
  breakPincer?: number
  /** C5 冻结期进攻偏置权重（默认 0）：`freezeTimer>0` 且 8 格内有活敌 ⇒ 朝最近敌加分。 */
  attackWhileFrozen?: number
  /** C7 对射先手（默认 false）：fire-ready 且正前方 13 格内敌人正对玩家 ⇒ 强制开火。 */
  counterFireFirst?: boolean
  /** C8 交叉火力偏置权重（默认 0）：威胁车道 ≥2 且 4 格内有活敌 ⇒ 朝更远方向加分。 */
  crossfireAwareness?: number
  /** D1 可达道具偏置权重（默认 0）：存在 `ETA≤TTL` 活道具且 4 格内无敌人 ⇒ 朝道具加分。 */
  pickupReachBias?: number
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
 * Relative-direction index used by `computePolicyExtra` (0 front / 1 back /
 * 2 left / 3 right, relative to the player's committed facing). Must stay
 * byte-identical to `policy-extra.ts::relIndex`'s ordering.
 */
const OPPOSITE: Record<Direction, Direction> = {
  up: 'down',
  down: 'up',
  left: 'right',
  right: 'left',
}
const LEFT_OF: Record<Direction, Direction> = {
  up: 'left',
  left: 'down',
  down: 'right',
  right: 'up',
}

function relIndexOf(facing: Direction, dir: Direction): number {
  if (dir === facing) return 0
  if (dir === OPPOSITE[facing]) return 1
  if (dir === LEFT_OF[facing]) return 2
  return 3
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
  /** B1 v6: only meaningful when `usesHoldFire3()` is true. */
  private holding = false
  private fireBeforeTurn = false
  // 推理道具偏置（默认 0 = 旧行为逐字节不变）
  private pickupBias: number
  private pickupRadius: number
  private enemyClearCells: number
  private c1Weight: number
  private c4Bias: number
  private c4PickupRadius: number
  private c4EnemyRadius: number
  private e1Bias: number
  private skipSteelOnlyShots: boolean
  private breakBricksOnPath: boolean
  private releaseReverseOnIce: boolean
  private suppressUselessFire: boolean
  private suppressLeadMissFire: boolean
  private avoidSteelPath: boolean
  private breakPincer: number
  private attackWhileFrozen: number
  private counterFireFirst: boolean
  private crossfireAwareness: number
  private pickupReachBias: number
  /** 本局规则实际改写动作的次数（诊断读数；只增不减，reset 归零）。 */
  private ruleApplyCount = 0
  private readonly puCenter = new Float64Array(2)

  // A decision is committed (and holds until the next due tick). Only the lazy
  // fallback for callers that never reset()/endFrame() decides on read.
  private committed = false

  constructor(world: World, opts: NNInputOptions = {}) {
    this.world = world
    this.model = loadModel(opts)
    this.K = opts.decisionK ?? 10
    this.gateCfg = createDecisionGateConfig(opts.decisionEvents === true, this.K)
    this.pickupBias = opts.pickupBias ?? 0
    this.pickupRadius = opts.pickupRadius ?? 2
    this.enemyClearCells = opts.enemyClearCells ?? 4
    this.c1Weight = opts.c1Weight ?? 0
    this.c4Bias = opts.c4Bias ?? 0
    this.c4PickupRadius = opts.c4PickupRadius ?? 6
    this.c4EnemyRadius = opts.c4EnemyRadius ?? 5
    this.e1Bias = opts.e1Bias ?? 0
    this.skipSteelOnlyShots = opts.skipSteelOnlyShots === true
    this.breakBricksOnPath = opts.breakBricksOnPath === true
    this.releaseReverseOnIce = opts.releaseReverseOnIce === true
    this.suppressUselessFire = opts.suppressUselessFire === true
    this.suppressLeadMissFire = opts.suppressLeadMissFire === true
    this.avoidSteelPath = opts.avoidSteelPath === true
    this.breakPincer = opts.breakPincer ?? 0
    this.attackWhileFrozen = opts.attackWhileFrozen ?? 0
    this.counterFireFirst = opts.counterFireFirst === true
    this.crossfireAwareness = opts.crossfireAwareness ?? 0
    this.pickupReachBias = opts.pickupReachBias ?? 0
  }

  /** 道具偏置方向（0..3）；不满足「近且无敌人/可走」⇒ -1。 */
  private pickupBiasIdx(): number {
    if (this.pickupBias <= 0) return -1
    const p = this.world.player
    if (!p || !p.alive) return -1
    const d = nearestPickupCenter(this.world, this.puCenter)
    if (d < 0 || d > this.pickupRadius) return -1
    if (nearestEnemyDistPx(this.world) <= this.enemyClearCells * CELL) return -1
    const dx = this.puCenter[0] - (p.x + p.w / 2)
    const dy = this.puCenter[1] - (p.y + p.h / 2)
    const dir: Direction =
      Math.abs(dx) >= Math.abs(dy) ? (dx > 0 ? 'right' : 'left') : dy > 0 ? 'down' : 'up'
    const idx = MOVE4_DECODE.indexOf(dir)
    if (idx < 0) return -1
    return corridorCells(this.world, p, dir, 1) > 0 ? idx : -1
  }

  /** 决策门读数（Δt/事件计数；测试与部署诊断用；只读）。 */
  readDecisionStats(): DecisionReadout {
    return decisionReadout(this.gate)
  }

  /** 本局规则实际改写动作的次数（诊断读数；只读）。 */
  readRuleApplications(): number {
    return this.ruleApplyCount
  }

  /**
   * 本拍实际发射方向（与 `export-eval-game` 的 shotDir 推导同口径）。
   * fire=1 → 已提交朝向；fire=2 → 只有转向会提交时才沿新朝向；null = 本拍无子弹。
   */
  private shotDirFor(fire: number): Direction | null {
    const p = this.world.player
    if (!p) return null
    if (fire === 1) return p.prevMoveDir ?? p.dir
    if (fire !== 2 || !this.moveDir) return null
    return turnWillCommit(this.world, p, this.moveDir, (this.world.frame + 1) * TICK_MS)
      ? this.moveDir
      : null
  }

  /**
   * C1 v1：对四个世界 move 方向按**相对朝向**的威胁特征扣分。
   *
   * 特征来自 `ObsEncoder.extra`（policy-extra 的 57 维，顺序 = 前/后/左/右
   * 相对 `p.dir`）：
   *   · out[0..3]  同轴敌弹数（归一化）
   *   · out[4..7]  该车道最近敌车距离（1.5 = 无敌）
   *   · out[13..16] 最近敌弹 TTI（归一化，1.5 = 无弹）
   *   · out[17..20] 同轴敌车数（归一化）
   *   · out[37..40] 正在瞄准玩家的敌车数（归一化）
   *
   * 权重是 **v1 工程初值**（未被教师读数拟合）；**v1 已在 175 对 A/B 判负
   * （Δ −13.1pp, p=0.0052）**，默认 `c1Weight=0`，不得在未重新预注册 + 新筛选段
   * 的情况下启用。`c1Weight=0` 时完全不进入本函数。
   * 玩家真无敌（shieldTimer>0）时不扣分——policy-extra 的威胁族此时已置零，
   * 这里显式跳过，避免 hitDist/laneE 在无敌期产生无意义的绕路。
   */
  private applyC1Penalty(scores: Float32Array): void {
    const p = this.world.player
    if (!p || !p.alive || (p.shieldTimer ?? 0) > 0) return
    const extra = this.encoder.extra
    const facing = p.dir
    for (let m = 0; m < 4; m++) {
      const r = relIndexOf(facing, MOVE4_DECODE[m])
      let penalty = extra[r] + 0.5 * extra[37 + r] + 0.5 * extra[17 + r]
      const tti = extra[13 + r]
      if (tti < 1) penalty += 1 - tti
      const hit = extra[4 + r]
      if (hit < 1) penalty += 0.5 * (1 - hit)
      scores[m] -= this.c1Weight * penalty
    }
  }

  getMoveDirection(): Direction | null {
    if (!this.committed) this.decide()
    return this.moveDir
  }

  isFiring(): boolean {
    if (!this.committed) this.decide()
    return this.firing
  }

  isHolding(): boolean {
    if (!this.committed) this.decide()
    return this.holding
  }

  isFireBeforeTurn(): boolean {
    if (!this.committed) this.decide()
    return this.fireBeforeTurn
  }

  usesHoldFire3(): boolean {
    return this.model.usesHoldFire3 === true
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
    this.holding = false
    this.fireBeforeTurn = false
    this.ruleApplyCount = 0
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

  /** Greedy move-argmax (v6 0..3 / legacy 0..4) from the latest forward pass. */
  moveArgmax(): number {
    const mv = this.model.moveLogits
    const n = this.model.moveDim ?? mv.length
    let b = 0
    for (let i = 1; i < n; i++) if (mv[i] > mv[b]) b = i
    return b
  }

  /** Greedy hold-argmax (0 advance / 1 turn-only); legacy models have no hold head. */
  holdArgmax(): number {
    const hv = this.model.holdLogits
    if (!hv) return 0
    let b = 0
    for (let i = 1; i < HOLD_DIM; i++) if (hv[i] > hv[b]) b = i
    return b
  }

  /** Greedy fire-argmax (v6 0/1/2, legacy 0 release / 1 hold). */
  fireArgmax(): number {
    const fr = this.model.fireLogits
    const n = this.model.fireDim ?? fr.length
    let b = 0
    for (let i = 1; i < n; i++) if (fr[i] > fr[b]) b = i
    return b
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

    const v6 = this.model.usesHoldFire3 === true
    const masks = computeMasks(w, v6)

    if (v6) {
      // --- B1 v6: hold(2) / move(4) / fire(3) ---
      // F3（2026-11-12）：(move, hold) 联合 argmax。旧实现先 argmax(move) 再掩 hold，
      // 在「hold 头想前进、top-1 move 是墙」时会白掉一拍；联合 argmax 只在
      // hold=0 合法（前方 1 格可走）时参与比较，等价于因子化策略的真 argmax。
      const mv = this.model.moveLogits
      const p = w.player
      // 推理道具偏置：只在「近且无敌人 + 该方向可走」时加分；默认 0 不复制。
      let moveScores = mv
      const biasIdx = this.pickupBiasIdx()
      if (biasIdx >= 0) {
        moveScores = Float32Array.from(mv)
        moveScores[biasIdx] += this.pickupBias
      }
      // C1：威胁车道扣分（默认 0 = 逐字节不变）。
      if (this.c1Weight > 0) {
        if (moveScores === mv) moveScores = Float32Array.from(mv)
        this.applyC1Penalty(moveScores)
      }
      // C4：无敌窗主动换血/冲道具（默认 0 = 逐字节不变）。
      if (this.c4Bias > 0) {
        const idx = c4MoveBias(
          this.world,
          this.world.player,
          this.c4PickupRadius,
          this.c4EnemyRadius,
        )
        if (idx >= 0) {
          if (moveScores === mv) moveScores = Float32Array.from(mv)
          moveScores[idx] += this.c4Bias
        }
      }
      // E1：残敌 BFS 目标偏置（默认 0 = 逐字节不变）。
      if (this.e1Bias > 0) {
        const align = e1MoveBias(this.world, this.world.player)
        if (align) {
          if (moveScores === mv) moveScores = Float32Array.from(mv)
          for (let m = 0; m < 4; m++) moveScores[m] += this.e1Bias * align[m]
        }
      }
      // C3：包夹突围（默认 0 = 逐字节不变）。
      if (this.breakPincer > 0) {
        const idx = pincerBreakoutDir(this.world, p)
        if (idx >= 0) {
          if (moveScores === mv) moveScores = Float32Array.from(mv)
          moveScores[idx] += this.breakPincer
        }
      }
      // C5：冻结期进攻（默认 0 = 逐字节不变）。
      if (this.attackWhileFrozen > 0) {
        const idx = freezeAttackDir(this.world, p)
        if (idx >= 0) {
          if (moveScores === mv) moveScores = Float32Array.from(mv)
          moveScores[idx] += this.attackWhileFrozen
        }
      }
      // C8：交叉火力（默认 0 = 逐字节不变）。
      if (this.crossfireAwareness > 0) {
        const idx = crossfireEscapeDir(this.world, p)
        if (idx >= 0) {
          if (moveScores === mv) moveScores = Float32Array.from(mv)
          moveScores[idx] += this.crossfireAwareness
        }
      }
      // D1：可达道具 ETA≤TTL（默认 0 = 逐字节不变）。
      if (this.pickupReachBias > 0) {
        const idx = reachablePickupDir(this.world, p)
        if (idx >= 0) {
          if (moveScores === mv) moveScores = Float32Array.from(mv)
          moveScores[idx] += this.pickupReachBias
        }
      }
      const hv = this.model.holdLogits
      if (hv && p) {
        let best = -Infinity
        let bestMove = 0
        let bestHold = 0
        for (let m = 0; m < 4; m++) {
          const advanceOk = corridorCells(w, p, MOVE4_DECODE[m], 1) > 0
          for (let h = 0; h < 2; h++) {
            if (h === 0 && !advanceOk) continue
            const s = moveScores[m] + hv[h]
            if (s > best) {
              best = s
              bestMove = m
              bestHold = h
            }
          }
        }
        this.moveDir = decodeMove4(bestMove)
        this.holding = bestHold === 1
      } else {
        // 无 hold 头 / 无玩家：退化为旧行为（move argmax + 掩 advance）。
        let bestMove = 0
        for (let i = 1; i < 4; i++) if (moveScores[i] > moveScores[bestMove]) bestMove = i
        this.moveDir = decodeMove4(bestMove)
        restrictAdvanceMask(w, bestMove, masks)
        this.holding = hv ? masks.hold[0] !== 1 || hv[1] > hv[0] : false
      }
      // B11：钢挡路换向（默认关 = 逐字节不变）。所选方向 1 格被挡且车道首阻弹格
      // 是钢、另有可走方向 ⇒ 改走该方向（推进而不是原地转向）。
      if (this.avoidSteelPath && p && this.moveDir && steelBlockedMove(w, p, this.moveDir)) {
        const alt = alternativeMoveDir(w, p, this.moveDir)
        if (alt) {
          this.moveDir = alt
          this.holding = false
          this.ruleApplyCount++
        }
      }
      const fr = this.model.fireLogits
      // Masked argmax over {0 no shot, 1 fire-before-turn, 2 fire-after-turn}.
      // fire[0] is always legal; 1/2 require the cooldown gate (masks.fire[i]).
      let bestFire = 0
      for (let i = 1; i < 3; i++) {
        if (masks.fire[i] === 1 && fr[i] > fr[bestFire]) bestFire = i
      }
      // 冰面滑行预判（B4）：冰上滑行中请求反向 ⇒ 不真倒车，改为松键保持朝向
      // （复用 God `iceGlideAdjust` 的唯一实现）。after-turn 射击不改方向，跳过。
      if (this.releaseReverseOnIce && bestFire !== 2 && p && !this.holding && this.moveDir) {
        const cx = Math.floor((p.x + p.w / 2) / CELL)
        const cy = Math.floor((p.y + p.h / 2) / CELL)
        const onIce = w.tileMap.get(cx, cy) === 'ice'
        if (onIce && iceGlideAdjust(this.moveDir, true, p.vx, p.vy, ICE_GLIDE_MIN_SPEED) === null) {
          this.moveDir = p.prevMoveDir ?? p.dir
          this.holding = true
        }
      }
      // C7：对射先手（默认关）。策略本拍没开火时，若正前方敌人也正对玩家且
      // fire-ready，沿已提交朝向强制开火（fire=1）。
      if (
        this.counterFireFirst &&
        bestFire === 0 &&
        p &&
        masks.fire[1] === 1 &&
        duelShotReady(w, p)
      ) {
        bestFire = 1
        this.ruleApplyCount++
      }
      // 顶钢空枪门（保守版）：只抑制「沿即将发射的方向、首阻弹格是 steel、
      // 且 lane 内没有可命中敌」的发射。不豁免拦截弹；默认关。
      if (this.skipSteelOnlyShots && bestFire !== 0 && p) {
        const shotDir = this.shotDirFor(bestFire)
        if (shotDir && steelOnlyShot(w, p, shotDir)) bestFire = 0
      }
      // B8 通用废射门 / B10 预测门（默认关；B1 的超集，只测增量）。
      if ((this.suppressUselessFire || this.suppressLeadMissFire) && bestFire !== 0 && p) {
        const shotDir = this.shotDirFor(bestFire)
        if (shotDir) {
          const suppress = this.suppressUselessFire
            ? uselessShot(w, p, shotDir)
            : leadMissShot(w, p, shotDir)
          if (suppress) {
            bestFire = 0
            this.ruleApplyCount++
          }
        }
      }
      // 拆砖免绕路（B2）：策略没开火、且所选方向被砖挡/2 格内有砖 ⇒ 强制拆墙。
      // 只在 fire gate ready 时置位（不制造被引擎拒掉的“空意图”）；基地圈砖不拆。
      if (this.breakBricksOnPath && bestFire === 0 && p && this.moveDir) {
        const dir = this.moveDir
        const wantBrick = this.holding
          ? brickBlocksAdvance(w, p, dir)
          : brickAheadWithin(w, p, dir, 2)
        if (wantBrick && !usefulFire(w, p, dir)) {
          if (turnWillCommit(w, p, dir, (w.frame + 1) * TICK_MS) && masks.fire[2] === 1) {
            bestFire = 2
          } else if (p.prevMoveDir === dir && masks.fire[1] === 1) {
            bestFire = 1
          }
        }
      }
      this.fireBeforeTurn = bestFire === 1
      this.firing = bestFire !== 0
    } else {
      // --- legacy 5-move/2-fire checkpoint (byte-identical path) ---
      const mv = this.model.moveLogits
      let bestMove = 0
      let bestMoveV = mv[0]
      for (let i = 1; i < 5; i++)
        if (mv[i] > bestMoveV) {
          bestMoveV = mv[i]
          bestMove = i
        }
      if (masks.move[bestMove] !== 1) bestMove = 0
      this.moveDir = decodeMove(bestMove)
      const fr = this.model.fireLogits
      this.firing = fr[1] > fr[0] && masks.fire[1] === 1
      this.holding = false
      this.fireBeforeTurn = false
    }

    // v2: item head removed — AI never activates guard/frenzy/rewind.

    this.committed = true
  }
}
