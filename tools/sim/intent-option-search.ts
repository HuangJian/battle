/**
 * intent-option-search.ts — P′-2a2 修饰维度 headroom 探针（零训练测量仪器）。
 *
 * 与 `src/nn/intent-option-probe.ts`（E2′ 纯委托）同构，唯一区别是执行命令：
 * 每 `searchEvery` 拍在 oracle 意图上**只搜索修饰维度**（option 固定 = oracle 全链意图），
 * 每个候选跑 horizon 拍真模拟，用 T6 反事实窗口分（`window-score.ts`，与
 * P′-2a-B / T6 语料同一实现）打分取 argmax。
 *
 * 累积消融档（`modifierLevel`，预注册见 plan/hier-options-prereg.v1.json `p2a2`）：
 *   0 = 纯委托（等价 intent-option，plumbing 对账用）
 *   1 = + targetKind（none / enemy 最近 / powerup 最近 / anchor 基地外一格）
 *   2 = + target（enemySlotOrder 1..4 显式 targetId；powerup 第 2 近格）
 *   3 = + pathMode（enemy 目标加 break；detour 在 E2′ 与 direct 同径，不单列）
 *   4 = + param（standoff 0/3/6 格；仅 enemy/anchor）
 *
 * 语义与 `intent-search-input.ts` 对齐：新命令从下一拍生效（commit 时点相同）；
 * 分支起点 = 同一 tick 的 `cloneWorld` + `captureDecisionState`，每候选独立 restore
 * （CRN：世界流共享快照，executor 流同种子）。
 *
 * 事件队列：`restoreWorld` 会 `events.clear()`，搜索前的 pending events 收尾原样放回。
 * 确定性：无 `Math.random()`；exec RNG 与 intent-option-probe 的 exec 同种子。
 */
import { BASE_POS, CELL, type Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'
import type { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { RNG } from '../../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../../src/ai/GodAIInput'
import { currentIntent } from '../../src/ai/intent/tagger'
import {
  ACTIVATION_MATRIX,
  INTENT_IDS,
  enemySlotOrder,
  type IntentId,
} from '../../src/ai/intent/vocab'
import type { NNOptionCommand, NNOptionTarget } from '../../src/ai/god/NNOption'
import { basePressure } from '../../src/nn/intent-rl-reward'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { countBaseWall } from './rl-reward'
import { counterfactualWindowScore, countEnemies } from './window-score'

export type ModifierLevel = 0 | 1 | 2 | 3 | 4

export interface IntentOptionSearchOptions {
  godParams?: GodAIParams
  /** 基础 seed（派生两个独立 RNG：oracle/exec 互不污染；exec 与 intent-option 同源）。 */
  seed?: number
  /** 搜索/承诺周期（tick）。默认 120。 */
  searchEvery?: number
  /** 候选分支 rollout 视界（tick）。默认 = searchEvery。 */
  horizon?: number
  /** 累积消融档。默认 4（全修饰）。 */
  modifierLevel?: ModifierLevel
}

/** 搜索读数（只读诊断）。 */
export interface IntentOptionSearchStats {
  searches: number
  branchTicks: number
  /** argmax 选中非委托命令里各维度出现的次数。 */
  chosen: { targetKind: number; target: number; pathMode: number; param: number }
}

const NN_OPTION_SET: ReadonlySet<string> = new Set(['nnOption'])

/** 基地 2×2 外侧的 6 个候选锚点（ring 外一格；按上/左/右顺序作 tie-break）。 */
function baseAnchorCells(): NNOptionTarget[] {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  return [
    { col: bc, row: br - 2 },
    { col: bc + 1, row: br - 2 },
    { col: bc - 2, row: br },
    { col: bc - 2, row: br + 1 },
    { col: bc + 3, row: br },
    { col: bc + 3, row: br + 1 },
  ]
}

/** 坦克可站立格（与 God-AI StrategyPlanner 同口径：brick/steel/water/base 不可站）。 */
function standable(world: World, col: number, row: number): boolean {
  const t = world.tileMap.get(col, row)
  return t !== 'brick' && t !== 'steel' && t !== 'water' && t !== 'base'
}

function playerCellOf(world: World): NNOptionTarget {
  const p = world.player
  if (!p) return { col: BASE_POS.col, row: BASE_POS.row - 2 }
  return { col: Math.floor((p.x + p.w / 2) / CELL), row: Math.floor((p.y + p.h / 2) / CELL) }
}

/** 最近的基地锚点（ring 外一格、可站立）；没有可站格 ⇒ null（该维度无候选）。 */
function nearestBaseAnchor(world: World): NNOptionTarget | null {
  const pc = playerCellOf(world)
  let best: NNOptionTarget | null = null
  let bestD = Infinity
  for (const c of baseAnchorCells()) {
    if (!standable(world, c.col, c.row)) continue
    const d = Math.abs(c.col - pc.col) + Math.abs(c.row - pc.row)
    if (d < bestD) {
      bestD = d
      best = c
    }
  }
  return best
}

/** 活道具格（中心口径与 NNOption.resolveTarget 一致），按距玩家升序。 */
function powerUpCellsByDistance(world: World): NNOptionTarget[] {
  const pc = playerCellOf(world)
  const out: Array<NNOptionTarget & { d: number }> = []
  for (const pu of world.powerUps) {
    if (!pu.alive) continue
    const col = Math.round((pu.x + pu.w / 2) / CELL)
    const row = Math.round((pu.y + pu.h / 2) / CELL)
    out.push({ col, row, d: Math.abs(col - pc.col) + Math.abs(row - pc.row) })
  }
  if (out.length > 1) out.sort((a, b) => a.d - b.d)
  return out.map((c) => ({ col: c.col, row: c.row }))
}

function cmdKey(c: NNOptionCommand): string {
  const t = c.target ? `${c.target.col},${c.target.row}` : ''
  return `${c.targetKind}|${c.targetId ?? ''}|${t}|${c.pathMode}|${c.param}`
}

/**
 * 候选命令集（纯函数；决策时调用，非热路径）。
 * 控制组（纯委托）恒为第 0 个；其余按 level 累积追加并去重。
 */
export function buildModifierCandidates(
  world: World,
  intent: IntentId,
  level: ModifierLevel,
): NNOptionCommand[] {
  const control: NNOptionCommand = {
    option: intent,
    targetKind: 'none',
    target: null,
    pathMode: 'direct',
    param: 0,
  }
  const cmds: NNOptionCommand[] = [control]
  if (level <= 0) return cmds

  const mask = ACTIVATION_MATRIX[intent]
  const allowEnemy = mask.enemy === 1
  // 计划 §4.1 的 targetKindMask：PICKUP 是 HUNT/HOLD_LANE/PICKUP 下的 powerup 目标。
  const allowPowerup = intent === 'HUNT' || intent === 'HOLD_LANE' || intent === 'PICKUP'
  const allowAnchor = mask.anchor === 1
  const seen = new Set<string>([cmdKey(control)])
  const push = (c: NNOptionCommand): void => {
    const k = cmdKey(c)
    if (seen.has(k)) return
    seen.add(k)
    cmds.push(c)
  }
  const base = (
    targetKind: NNOptionCommand['targetKind'],
    extra: Partial<NNOptionCommand> = {},
  ): NNOptionCommand => ({ ...control, targetKind, ...extra })

  // ---- L1: targetKind ----
  if (allowEnemy) push(base('enemy'))
  if (allowPowerup) push(base('powerup'))
  const anchor = allowAnchor ? nearestBaseAnchor(world) : null
  if (anchor) push(base('anchor', { target: anchor }))

  // ---- L2: target（显式槽 / 第 2 近道具） ----
  if (level >= 2) {
    if (allowEnemy) {
      const slots = enemySlotOrder(world)
      for (let i = 0; i < slots.length; i++) push(base('enemy', { targetId: slots[i].id }))
    }
    if (allowPowerup) {
      const pus = powerUpCellsByDistance(world)
      if (pus.length >= 2) push(base('powerup', { target: pus[1] }))
    }
  }

  // ---- L3: pathMode（break 只对 enemy；anchor/powerup 不做 L0 环拆） ----
  if (level >= 3) {
    const enemyCmds = cmds.filter((c) => c.targetKind === 'enemy')
    for (const c of enemyCmds) push({ ...c, pathMode: 'break' })
  }

  // ---- L4: param（standoff；仅 enemy/anchor） ----
  if (level >= 4) {
    const eligible = cmds.filter((c) => c.targetKind === 'enemy' || c.targetKind === 'anchor')
    for (const c of eligible) {
      push({ ...c, param: 1 })
      push({ ...c, param: 2 })
    }
  }

  return cmds
}

/**
 * IntentOptionSearch — InputLike。选择 = 修饰维度搜索 argmax，执行 = E2′ `nn-option`；
 * option 固定为 oracle 全链意图（与 intent-option 同源）。
 */
export class IntentOptionSearch implements InputLike {
  private world: World
  private oracle: GodAIInput
  private exec: GodAIInput
  private branchSim: Simulation
  private searchEvery: number
  private horizon: number
  private modifierLevel: ModifierLevel

  private thought = false
  private moveDir: Direction | null = null
  private firing = false

  /** 每搜索窗的 oracle 意图 id（INTENT_IDS 下标）。 */
  readonly intentTrace: number[] = []
  private searches = 0
  private branchTicks = 0
  private chosen = { targetKind: 0, target: 0, pathMode: 0, param: 0 }

  constructor(world: World, opts: IntentOptionSearchOptions = {}) {
    this.world = world
    const params = opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }
    const base = opts.seed ?? (Date.now() & 0x7fffffff) >>> 0
    // 与 IntentOptionProbe 同源：oracle 用 base，exec 用 base^0x9e3779b9。
    this.oracle = new GodAIInput(world, { ...params }, new RNG(base))
    this.exec = new GodAIInput(world, { ...params }, new RNG((base ^ 0x9e3779b9) >>> 0))
    this.branchSim = new Simulation(world, this.exec)
    this.searchEvery = Math.max(1, opts.searchEvery ?? 120)
    this.horizon = Math.max(1, opts.horizon ?? this.searchEvery)
    this.modifierLevel = opts.modifierLevel ?? 4
  }

  getMoveDirection(): Direction | null {
    if (!this.thought) this.decide()
    return this.moveDir
  }

  isFiring(): boolean {
    if (!this.thought) this.decide()
    return this.firing
  }

  wasItemPressed(): false {
    return false // AI 不使用主动道具
  }

  endFrame(): void {
    this.thought = false
    this.oracle.endFrame()
    this.exec.endFrame()
  }

  reset(): void {
    this.thought = false
    this.intentTrace.length = 0
    this.searches = 0
    this.branchTicks = 0
    this.chosen = { targetKind: 0, target: 0, pathMode: 0, param: 0 }
    this.oracle.reset()
    this.exec.reset()
    this.exec._candidateOverride = null
    this.exec._optionCmd = null
  }

  readSearchStats(): IntentOptionSearchStats {
    return { searches: this.searches, branchTicks: this.branchTicks, chosen: { ...this.chosen } }
  }

  /** 每 tick：oracle 全链（意图源）+ E2′ exec 链；搜索窗只对修饰维度做 H 拍反事实。 */
  private decide(): void {
    this.thought = true
    const f = this.world.frame

    this.oracle.getMoveDirection()
    this.oracle.isFiring()
    this.exec.getMoveDirection()
    this.exec.isFiring()

    if (f % this.searchEvery === 0) this.search()

    this.moveDir = this.exec._moveDir
    this.firing = this.exec._fire
  }

  private search(): void {
    const w = this.world
    const intent = currentIntent(this.oracle)
    if (!intent) return

    const cmds = buildModifierCandidates(w, intent, this.modifierLevel)
    let best = 0
    if (cmds.length > 1) {
      const pending = w.events.items.length > 0 ? [...w.events.items] : null
      let bestScore = -Infinity
      for (let i = 0; i < cmds.length; i++) {
        const snap = cloneWorld(w)
        const st = this.exec.captureDecisionState()
        this.exec._optionCmd = cmds[i]
        this.exec._candidateOverride = NN_OPTION_SET

        const phi0 = basePressure(w)
        const enemies0 = countEnemies(w)
        const kills0 = w.killCount
        const lives0 = w.lives
        let wallNow = countBaseWall(w)
        let wallLoss = 0

        for (let h = 0; h < this.horizon; h++) {
          this.branchSim.tick()
          this.exec.endFrame()
          this.branchTicks++
          const wall = countBaseWall(w)
          if (wall < wallNow) {
            wallLoss += wallNow - wall
            wallNow = wall
          }
          if (w.state !== 'playing') break
        }

        const score = counterfactualWindowScore(
          w.killCount - kills0,
          Math.max(0, lives0 - w.lives),
          wallLoss,
          phi0 - basePressure(w),
          enemies0 - countEnemies(w),
        )

        restoreWorld(w, snap)
        this.exec.restoreDecisionState(st)
        if (score > bestScore) {
          bestScore = score
          best = i
        }
      }
      // restoreWorld 清了事件队列：把搜索前的 pending events 原样放回（telemetry 不丢）。
      if (pending) for (const e of pending) w.pushEvent(e)
    }

    const chosen = cmds[best]
    this.exec._optionCmd = chosen
    this.exec._candidateOverride = NN_OPTION_SET
    this.intentTrace.push(INTENT_IDS.indexOf(intent))
    this.searches++

    if (chosen.targetKind !== 'none') this.chosen.targetKind++
    if (chosen.targetId != null || (chosen.targetKind === 'powerup' && chosen.target != null)) {
      this.chosen.target++
    }
    if (chosen.pathMode === 'break') this.chosen.pathMode++
    if (chosen.param > 0) this.chosen.param++
  }
}
