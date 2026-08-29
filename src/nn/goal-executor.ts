/**
 * goal-executor.ts — Goal-Space 执行器（plan/Goal-Space-Policy-Rebuild.md §7 / 任务卡 T8.5）。
 *
 * 分层仲裁（§7，单一裁决者，禁止优先级级联）：
 *   L0 dodge —— 硬约束：复用 God-AI 全链的 reflex 层（_lastBranch === 'dodge' 检测），
 *              只"位移"不"取消"目标（契约保持，E5 累计 dodge 位移 tick）。
 *   L1 目标层 —— NN：selectGoal argmax（§9.4.1 推理前强制 mask；RL 采样经 goalPick 回调）。
 *   L2 路径层 —— findPath（默认 PathConstraints；walk 失败再 breakBrick，P3.1 同款），
 *              逐格消费步进（Navigator followPath 同构：进新格 shift，堵死取替代方向）。
 *   L3 开火层 —— FireControl.shouldFireInDirImpl（物理层原样，§9.2）+ 凿墙开火；
 *              engage 头的"L3 交战意愿门控"在 T9 全量期接线（canary 期不开）。
 *
 * 心跳调度（§6.3）：契约 E4（超承诺期 = 慢心跳 T tick）与 E1/E3/E5 纯谓词事件驱动触发
 * 重选；E2 关闭（T8-min，value 头 PPO 前不可信 §6.7）。前向按需，不逐 tick。
 *
 * 有序回退（§9.4.1）：selectGoal −1 ⇒ 原地停射 → 邻域最近可达格 → God-AI 兜底（§9.2.1
 * 旁路：候选链原样保留，仅作为兜底/教师/对照，不删除）。
 *
 * 可满足性（§6.1.1）：travelEst（carve-aware A* 步数 × 23 tick/格 + 8×k tick）> T 的
 * 候选拒绝；top-K（K=6）按 heat+mask 降序找第一个可满足者；全不可满足 ⇒ 提交 argmax
 * （有目标优于站着不动，telemetry 记 unsatisfiable 供 T9a 校准 T）。
 *
 * 确定性：argmax 模式零 RNG 消费（god 兜底链走 god 实例内部 RNG，§47 与 world 解耦）；
 * 同 seed 逐字节一致（单测断言）。零每 tick 堆分配（AGENTS §14：路径/掩码/注入全部
 * 常驻缓冲；reselect 的 telemetry 追加是低频路径，不在热路径）。
 */
import type { Direction } from '../constants'
import { CELL, GRID } from '../constants'
import type { InputLike } from '../game/Input'
import type { World } from '../game/World'
import type { RNG } from '../utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../ai/GodAIInput'
import { findPath } from '../ai/god/pathfind'
import { shouldFireInDirImpl } from '../ai/god/FireControl'
import { ObsEncoder } from './obs-encoder'
import { buildGoalModelFromText, type GoalModelLike } from './infer'
import { writeGoalInject, GOAL_INJECT_DIM } from './goal-inject'
import {
  evaluateContract,
  makeGoalContract,
  type ClauseId,
  type GoalContract,
} from './goal-contract'
import { ReachMasker, selectGoal, GOAL_MASK_LAMBDA_DEFAULT } from '../ai/goal/reach-mask'

/** 行军 tick/格（pathfind §3.3(c)：Player ≈ 23 ticks/cell）。 */
const MARCH_TICKS_PER_CELL = 23
/** 凿砖 tick/块（§9.4 carve 代价 ≈ 8×k）。 */
const CARVE_TICKS_PER_BRICK = 8
/** 可满足性扫描的 top-K（§6.1.1 实现决策）。 */
const SATISFIABLE_TOP_K = 6
/** L2 路径 replan 间隔（tick；Navigator replanInterval 同量级）。 */
const PATH_REPLAN_TICKS = 60
/** 重选失败（无契约可提交）后的重试冷却（tick）——防 E3/全遮情形逐 tick 重前向抖动。 */
const RESELECT_BACKOFF_TICKS = 30

export interface GoalExecutorOptions {
  weightsText?: string
  godParams?: GodAIParams
  /** 内部 God-AI 决策 RNG（§47）。缺省 = world.rng（不推荐）。 */
  rng?: RNG
  /** 承诺期 T = 慢心跳 ticks（§11.7 T=H 起步；E4 在 bornTick + T 触发重选）。默认 240。 */
  promiseTicks?: number
  /** carve 代价系数 λ（§9.4.2）。默认 0.5。 */
  lambda?: number
  /**
   * T7.2 RL 采样回调：在每次重选 tick 调用（collector 侧持模型做前向 + 676/169 路采样），
   * 返回目标格 idx（行主序）；executor 只做契约/路径/注入态维护。设此回调时
   * weightsText 可省略（executor 不构建模型）。
   */
  goalPick?: (obs: Uint8Array, scalars: Float32Array, inject: Float32Array, tick: number) => number
  /**
   * 诊断模式（executor-ceiling 对照，§T9a 门失败归因）：不跑网络，每心跳直接采用
   * God-AI 全链的导航目标格作为 goal —— "目标选择 = God-AI 口径"时执行器本身的
   * 上限测量。与网络模式互斥；设置时 weightsText 可省略。
   */
  followGodNav?: boolean
  /** T0-goal 遥测：记录每次重选（低频，仅显式开启时）。 */
  recordTrace?: boolean
}

/** 重选遥测行（T0-goal；clause='init' 为首次选格，'unsat' = top-K 全不可满足回退 argmax）。 */
export interface GoalReselect {
  tick: number
  cell: number
  clause: ClauseId | 'init'
  outcome: 'ok' | 'unsat' | 'fallback'
}

export class GoalExecutor implements InputLike {
  private world: World
  private model: GoalModelLike | null
  private encoder = new ObsEncoder()
  private god: GodAIInput
  private masker = new ReachMasker()
  private maskRevision = -1
  private maskStartValid = false
  // top-K 扫描复用缓冲（reselect 低频路径，但避免每次分配，AGENTS §14.2）
  private readonly topIdx = new Int32Array(SATISFIABLE_TOP_K)
  private readonly topVal = new Float64Array(SATISFIABLE_TOP_K)
  private promiseTicks: number
  private lambda: number
  private goalPick: GoalExecutorOptions['goalPick']
  private recordTrace: boolean

  // 契约与计数
  private contract: GoalContract | null = null
  private dodgeTicks = 0
  private switches = 0

  // inject 态（§8.1.1）
  private prevGoalRow = -1
  private prevGoalCol = -1
  private arrived = false
  /** 本目标追击累计起点（goal 变更才重置；bornTick 是承诺期起点，E4 续约会重置）。 */
  private pursueSince = 0
  private readonly injectBuf = new Float32Array(GOAL_INJECT_DIM)

  // L2 路径缓存
  private pathDirs: Direction[] = []
  private pathCell: { col: number; row: number } | null = null
  private pathReplanTimer = 0
  private pathRevision = -1

  private thought = false
  private moveDir: Direction | null = null
  private firing = false
  private nextReselectTick = 0

  /** T0-goal 遥测（recordTrace 时填充）。 */
  readonly reselectTrace: GoalReselect[] = []

  private followGodNav: boolean

  constructor(world: World, opts: GoalExecutorOptions) {
    this.world = world
    this.goalPick = opts.goalPick
    this.followGodNav = opts.followGodNav === true
    this.model =
      opts.goalPick || this.followGodNav ? null : buildGoalModelFromText(opts.weightsText ?? '')
    this.god = new GodAIInput(world, opts.godParams ?? { ...DEFAULT_GOD_AI_PARAMS }, opts.rng)
    this.promiseTicks = opts.promiseTicks ?? 240
    this.lambda = opts.lambda ?? GOAL_MASK_LAMBDA_DEFAULT
    this.recordTrace = opts.recordTrace === true
  }

  // ---- InputLike ----

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
    this.god.endFrame()
  }

  reset(): void {
    this.thought = false
    this.contract = null
    this.dodgeTicks = 0
    this.switches = 0
    this.pursueSince = 0
    this.prevGoalRow = -1
    this.prevGoalCol = -1
    this.arrived = false
    this.pathDirs = []
    this.pathCell = null
    this.pathReplanTimer = 0
    this.nextReselectTick = 0
    this.reselectTrace.length = 0
    this.god.reset()
  }

  // ---- 决策主流程 ----

  private playerCell(): { col: number; row: number } {
    const p = this.world.player
    const col = Math.max(0, Math.min(GRID - 1, Math.round((p ? p.x : 0) / CELL)))
    const row = Math.max(0, Math.min(GRID - 1, Math.round((p ? p.y : 0) / CELL)))
    return { col, row }
  }

  private decide(): void {
    this.thought = true
    const w = this.world
    const f = w.frame

    // reflex 保底：God-AI 全链每 tick 先跑（dodge/survive 硬代码层）。
    this.god.getMoveDirection()
    this.god.isFiring()

    // L0 dodge 硬约束：位移不取消目标（E5 累计，契约保持）。
    if (this.god._lastBranch === 'dodge' && this.god._moveDir) {
      this.dodgeTicks++
      this.moveDir = this.god._moveDir
      this.firing = this.god._fire
      this.tickContract(f)
      return
    }

    this.tickContract(f)
    if (this.contract) {
      this.followContract()
      return
    }
    // 无契约（重选彻底失败）⇒ God-AI 兜底（§9.2.1 旁路）。
    this.moveDir = this.god._moveDir
    this.firing = this.god._fire
  }

  /** 当前契约格 idx（无契约 = -1）。遥测/测试用。 */
  get currentGoalCell(): number {
    return this.contract ? this.goalIdx() : -1
  }

  /** 契约逐 tick 评估（纯谓词，便宜）+ 失效触发重选。 */
  private tickContract(f: number): void {
    if (!this.contract) {
      // 上次重选失败（全遮/无路）：冷却重试，冷却期走 God-AI 兜底（decide 尾部）。
      if (f >= this.nextReselectTick) this.reselect(f, 'init')
      return
    }
    // E3 判据：缓存 mask 在契约格 ≡ −Infinity（只在地形 revision 变化时重算，§6.6）。
    const maskedOut =
      this.ensureMask() && this.masker.mask(this.lambda)[this.goalIdx()] === -Infinity
    const clause = evaluateContract(this.contract, this.world, {
      tick: f,
      dodgeTicks: this.dodgeTicks,
      goalMaskedOut: maskedOut,
    })
    if (clause) this.reselect(f, clause)
  }

  private goalIdx(): number {
    const c = this.contract!
    return c.cell.row * GRID + c.cell.col
  }

  /** 地形 revision 变化时重算 mask；返回 start 是否有效（无效 = 全图不可达）。 */
  private ensureMask(): boolean {
    const tm = this.world.tileMap
    if (this.maskRevision !== tm.revision) {
      this.maskRevision = tm.revision
      const pc = this.playerCell()
      this.maskStartValid = this.masker.compute(tm, pc.col, pc.row)
    }
    return this.maskStartValid
  }

  /** L1 重选（事件驱动 / E4 心跳）。 */
  private reselect(f: number, clause: ClauseId | 'init'): void {
    const w = this.world
    this.encoder.encode(w)
    // inject §8.1.1：duration 用 pursueSince 差（追击累计，E4 同格续约不清零）。
    const duration = this.contract ? f - this.pursueSince : 0
    const inject = writeGoalInject(
      this.injectBuf,
      this.prevGoalRow,
      this.prevGoalCol,
      duration,
      this.switches,
      this.arrived,
    )

    const maskReady = this.ensureMask()
    let idx = -1
    if (this.followGodNav) {
      // 诊断：God-AI 导航目标（_navCache 未命中时投影）；过顶点合法性即可。
      let tc: number
      let tr: number
      if (this.god._navCacheValid) {
        tc = this.god._navTargetCol
        tr = this.god._navTargetRow
      } else {
        const pc0 = this.playerCell()
        const d = this.god._moveDir
        const dx = d === 'left' ? -1 : d === 'right' ? 1 : 0
        const dy = d === 'up' ? -1 : d === 'down' ? 1 : 0
        tc = pc0.col + dx * 4
        tr = pc0.row + dy * 4
      }
      tc = Math.max(0, Math.min(GRID - 1, tc))
      tr = Math.max(0, Math.min(GRID - 1, tr))
      idx = tr * GRID + tc
    } else if (this.goalPick) {
      idx = this.goalPick(this.encoder.obs, this.encoder.scalars, inject, f)
    } else if (this.model) {
      this.model.goalForward(this.encoder.obs, this.encoder.scalars, inject)
      idx = selectGoal(this.model.goalHeatmap, this.masker.mask(this.lambda))
    }
    void maskReady

    // 有序回退（§9.4.1）：argmax 全遮 ⇒ 原地停射 → 邻域最近可达格 → God-AI 兜底。
    if (idx < 0) {
      const near = this.nearestReachableIdx()
      if (near >= 0) {
        this.commitContract(near, f, clause, 'ok')
        return
      }
      this.contract = null
      this.moveDir = null // 原地停射；下一层由 decide 的 God-AI 兜底接管
      this.firing = false
      this.nextReselectTick = f + RESELECT_BACKOFF_TICKS
      if (this.recordTrace)
        this.reselectTrace.push({ tick: f, cell: -1, clause, outcome: 'fallback' })
      return
    }

    // 可满足性（§6.1.1）：top-K 找第一个 travelEst ≤ T；全不可满足 ⇒ 提交 argmax。
    const chosen = this.satisfiableIdx(idx)
    if (chosen === -1) {
      this.commitContract(idx, f, clause, 'unsat')
      return
    }
    this.commitContract(chosen, f, clause, 'ok')
  }

  /** 提交契约 + 维护 inject 态 + 重置 L2 路径。 */
  private commitContract(
    idx: number,
    f: number,
    clause: ClauseId | 'init',
    outcome: GoalReselect['outcome'],
  ): void {
    const col = idx % GRID
    const row = (idx - col) / GRID
    const travelEst = this.estimateTravelTicks(col, row)
    // 'unsat'（top-K 全不可满足）强制提交 argmax：有目标优于站着不动（telemetry 可见）。
    // RL 采样模式同样强制：采样动作必须落契约（rollout 的 dt/inject 记账依赖它）。
    const force = outcome === 'unsat' || this.goalPick !== undefined
    const contract = makeGoalContract(
      { col, row },
      this.world,
      f,
      this.promiseTicks,
      travelEst,
      force,
    )
    if (contract) {
      const sameGoal =
        this.prevGoalRow === row && this.prevGoalCol === col && this.contract !== null
      if (!sameGoal) {
        this.switches++
        this.dodgeTicks = 0
        this.pursueSince = f
        this.pathDirs = []
        this.pathCell = null
        this.pathReplanTimer = 0
      }
      // 同格续约（E4 心跳确认同一目标）：bornTick 重置 ⇒ 承诺期重新起算（E4 不抖动）；
      // pursueSince 保留 ⇒ inject duration 连续增长；dodgeTicks 重置 = "自上次重承诺起
      // 累计"口径（否则 E5 同格续约会逐 tick 重触发）。
      this.dodgeTicks = 0
      this.prevGoalRow = row
      this.prevGoalCol = col
      this.arrived = false
      this.contract = contract
    } else if (this.contract && clause === 'E4') {
      // E4 续约被可满足性拒绝（当前目标 travelEst > T）：推迟下一心跳（bornTick = f），
      // 契约保持 —— 否则旧 bornTick 使 E4 每 tick 重触发（抖动）。
      this.contract.bornTick = f
    } else if (!this.contract) {
      // 无契约且提交失败：进冷却，防止逐 tick 重前向。
      this.nextReselectTick = f + RESELECT_BACKOFF_TICKS
    }
    if (this.recordTrace) this.reselectTrace.push({ tick: f, cell: idx, clause, outcome })
  }

  /**
   * top-K 可达性扫描（2026-08-29 修订）：按 heat+mask 降序找第一个**可达**格
   * （travelEst 有限 ⇒ 掩码有限）；不可达格跳过。T 不再过滤远距目标（见
   * makeGoalContract 修订注——T 是重评估节奏，不是移动拴绳）。
   */
  private satisfiableIdx(argmaxIdx: number): number {
    if (!this.model) return argmaxIdx // RL/诊断模式：采样/导航动作直接提交
    const heat = this.model.goalHeatmap
    const mask = this.masker.mask(this.lambda)
    const topIdx = this.topIdx
    const topVal = this.topVal
    topIdx.fill(-1)
    topVal.fill(-Infinity)
    // 单遍插入维持降序（K=6 常数小；reselect 低频路径，无分配）
    for (let i = 0; i < 676; i++) {
      const m = mask[i]
      if (m === -Infinity) continue
      const v = heat[i] + m
      if (v <= topVal[SATISFIABLE_TOP_K - 1]) continue
      let j = SATISFIABLE_TOP_K - 1
      while (j > 0 && topVal[j - 1] < v) {
        topVal[j] = topVal[j - 1]
        topIdx[j] = topIdx[j - 1]
        j--
      }
      topVal[j] = v
      topIdx[j] = i
    }
    for (let i = 0; i < SATISFIABLE_TOP_K; i++) {
      const idx = topIdx[i]
      if (idx < 0) break
      const col = idx % GRID
      const row = (idx - col) / GRID
      if (Number.isFinite(this.estimateTravelTicks(col, row))) return idx
    }
    return -1
  }

  /** travelEst = A* 步数 × 23 + k × 8（§6.1.1 粗估即可，K 是预算不是预测）。 */
  private estimateTravelTicks(col: number, row: number): number {
    const tm = this.world.tileMap
    const pc = this.playerCell()
    const walk = findPath(tm, pc, { col, row })
    if (walk) return walk.length * MARCH_TICKS_PER_CELL
    const carve = findPath(tm, pc, { col, row }, { breakBrick: true })
    const k = this.masker.k[row * GRID + col]
    const kCost = k === 65535 ? CARVE_TICKS_PER_BRICK * 8 : k * CARVE_TICKS_PER_BRICK
    if (carve) return carve.length * MARCH_TICKS_PER_CELL + kCost
    return Infinity // 不可达（makeGoalContract 的 isFinite 检查依赖此哨兵）
  }

  /** 邻域最近可达格（回退第 2 步）：min (k, 距离) 的有限 k 格。 */
  private nearestReachableIdx(): number {
    this.ensureMask()
    const k = this.masker.k
    const pc = this.playerCell()
    let best = -1
    let bestK = Number.MAX_SAFE_INTEGER
    let bestD = Number.MAX_SAFE_INTEGER
    for (let r = 0; r < GRID - 1; r++) {
      for (let c = 0; c < GRID - 1; c++) {
        const ki = k[r * GRID + c]
        if (ki === 65535) continue
        const d = Math.abs(c - pc.col) + Math.abs(r - pc.row)
        if (ki < bestK || (ki === bestK && d < bestD)) {
          bestK = ki
          bestD = d
          best = r * GRID + c
        }
      }
    }
    return best
  }

  // ---- L2 路径跟随 ----

  private followContract(): void {
    const w = this.world
    const p = w.player
    const c = this.contract!
    const pc = this.playerCell()
    this.arrived = pc.col === c.cell.col && pc.row === c.cell.row

    // 进新格消费一步（Navigator followPath 同构）。
    if (
      this.pathDirs.length > 0 &&
      (!this.pathCell || this.pathCell.col !== pc.col || this.pathCell.row !== pc.row)
    ) {
      this.pathDirs.shift()
      this.pathCell = { col: pc.col, row: pc.row }
    }

    // replan：路径耗尽 / 地形变更 / 定时器。
    this.pathReplanTimer--
    const revisionChanged = this.pathRevision !== w.tileMap.revision
    if (this.pathDirs.length === 0 || revisionChanged || this.pathReplanTimer <= 0) {
      this.replanPath(pc)
      this.pathRevision = w.tileMap.revision
      this.pathReplanTimer = PATH_REPLAN_TICKS
      this.pathCell = { col: pc.col, row: pc.row }
    }

    let dir: Direction | null = null
    if (this.arrived) {
      dir = null // 在位：保持朝向
    } else if (this.pathDirs.length > 0) {
      dir = this.pathDirs[0]
    }

    if (dir && p) {
      const pcx = p.x + p.w / 2
      const pcy = p.y + p.h / 2
      const canMove = this.god.canMoveDir(p, dir)
      const canBreak = !canMove && this.god.canMoveOrBreak(p, dir)
      if (!canMove && !canBreak) {
        // 堵死（钢/水/坦）⇒ 取替代方向（不含反向），全堵 ⇒ 原地。
        const alt = this.alternativeDir(p, dir)
        dir = alt
      }
      this.moveDir = dir
      // L3-min：凿墙开火（路径方向被砖挡）+ 顺路交战判定（FireControl 物理层原样）。
      this.firing =
        dir !== null && (canBreak || shouldFireInDirImpl(this.god, pcx, pcy, dir, !this.arrived))
    } else {
      this.moveDir = dir
      this.firing =
        p !== null &&
        shouldFireInDirImpl(this.god, p.x + p.w / 2, p.y + p.h / 2, p ? p.dir : 'up', false)
    }
  }

  private replanPath(pc: { col: number; row: number }): void {
    const c = this.contract!
    const tm = this.world.tileMap
    // walk 优先（corridors），失败再 breakBrick（P3.1 dig 同款）。
    const walk = findPath(tm, pc, c.cell)
    if (walk && walk.length > 0) {
      this.pathDirs = walk
      return
    }
    const carve = findPath(tm, pc, c.cell, { breakBrick: true })
    this.pathDirs = carve && carve.length > 0 ? carve : []
  }

  private alternativeDir(p: NonNullable<World['player']>, preferred: Direction): Direction | null {
    const dirs: Direction[] = ['up', 'down', 'left', 'right']
    const opp =
      preferred === 'up'
        ? 'down'
        : preferred === 'down'
          ? 'up'
          : preferred === 'left'
            ? 'right'
            : 'left'
    for (let i = 0; i < dirs.length; i++) {
      const d = dirs[i]
      if (d === opp) continue
      if (this.god.canMoveDir(p, d)) return d
    }
    return null
  }
}
