/**
 * export-counterfactual-goals.ts — T6 反事实标注管线（plan/Goal-Space-Policy-Rebuild.md
 * Part III §11 / 任务卡 T6；试点 350 局口径）。
 *
 * 流水线（§11.1，**不是模仿**）：
 *   1. God-AI 驱动整局 → 只取状态分布（每 replan tick 记 obs）
 *   2. 丢弃 God-AI 的选择（其导航目标仅作为候选之一保底，§11.2）
 *   3. 枚举 K≈12 候选目标格（§11.2 + §11.4 确定性截断）
 *   4. 各候选分支 rollout H tick（cloneWorld 分叉，§T6.1b；每分支一次 clone/restore，
 *      严禁每 tick 克隆 —— §11.9.4 实测 0.3% vs 32% 的分界线）
 *   5. 窗口级打分 → 软目标（trainer 侧 p_i ∝ exp((s_i − λ·k_i)/τ)，§11.5/§9.4.3）
 *   6. engage 标签（§8.3.2：max(s) − s(当前格) > ε=0）
 *
 * 分支执行策略（§T6.1(a)，钉死）：GoalExecutor 强制目标模式（goalPick 回调恒返回候选格）
 *   —— 与部署同一条 L2/L3 代码路径（pathfind 默认约束 + FireControl 开火 + dodge 硬约束），
 *   候选间比较只反映目标选择。开火 = FireControl 规则（God-AI-fire-conditioned，
 *   §11.3.1 ⇒ manifest 记 firePolicy 版本）。
 *
 * shard（每局一个目录；λ 不入库，k_i 必存 —— §9.4.3）：
 *   obs (N,14,26,26) u1 | scalars (N,19) f4
 *   cand_cell (N,K) u2（padding 65535）| cand_k (N,K) u2 | cand_s (N,K) f4
 *   engage (N) u1 | manifest.json（H/K/replan/firePolicy/难度/开销统计）
 *
 * 确定性：分支 RNG 全部由 world 快照派生；同 (stage,seed) 双跑逐字节一致（单测断言）。
 *
 * Usage:
 *   bun tools/sim/export-counterfactual-goals.ts --out tmp/cf-goals-pilot \
 *       --stages 0-34 --seeds 1-10 --window 120 --k 12
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, GRID, CELL, BASE_POS } from '../../src/constants'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { GoalExecutor } from '../../src/nn/goal-executor'
import { ReachMasker } from '../../src/ai/goal/reach-mask'
import { basePressure } from '../../src/nn/intent-rl-reward'
import { ObsEncoder } from '../../src/nn/obs-encoder'
import { writeGoalInject, GOAL_INJECT_DIM } from '../../src/nn/goal-inject'
import {
  computeBaseGuardAnchorImpl,
  getDefaultDefensePositionImpl,
} from '../../src/ai/god/StrategyPlanner'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { RNG } from '../../src/utils/RNG'
import { writeNpy } from '../../src/nn/npy'
import { writeFileSync, mkdirSync } from 'node:fs'
import type { Tank } from '../../src/types'

const MAX_TICKS = 36000
const UNREACH = 65535

/** 窗口打分权重（§11.3；分量原始值入库，权重可 trainer 侧重推导）。 */
const W_KILL = 4.0
const W_DEATH = -5.0
const W_WALL = -3.0
const W_PRESSURE = 10.0
const W_ENEMIES = 2.0

export interface CfDecision {
  tick: number
  candidates: number[] // cell idx（含 padding 前的实际数 ≤ K）
  ks: number[]
  scores: number[]
  engage: number
}

export interface CfGameResult {
  decisions: CfDecision[]
  obs: Uint8Array[] // 每决策点的 obs 拷贝
  scalars: Float32Array[]
  injects: Float32Array[] // 每决策点的 §8.1.1 自馈注入态
  outcome: string
  ticks: number
  /** 候选集覆盖率诊断（§11.4）：被截掉的数量统计 */
  truncated: number
  totalCandidates: number
}

/** 玩家顶点格（playerCellImpl 同口径：Math.round(x/CELL)，clamp 到顶点域）。 */
function playerVertex(world: World): { col: number; row: number } {
  const p = world.player
  const col = Math.max(0, Math.min(GRID - 1, Math.round((p ? p.x : 0) / CELL)))
  const row = Math.max(0, Math.min(GRID - 1, Math.round((p ? p.y : 0) / CELL)))
  return { col, row }
}

/** 直线可视性（Bresenham 逐格，砖/钢/水阻断）。 */
function hasLos(
  world: World,
  from: { col: number; row: number },
  to: { col: number; row: number },
): boolean {
  const dr = to.row - from.row
  const dc = to.col - from.col
  const steps = Math.max(Math.abs(dr), Math.abs(dc))
  if (steps === 0) return true
  for (let i = 1; i < steps; i++) {
    const r = from.row + Math.round((dr * i) / steps)
    const c = from.col + Math.round((dc * i) / steps)
    const t = world.tileMap.get(c, r)
    if (t === 'brick' || t === 'steel' || t === 'water' || t === 'base') return false
  }
  return true
}

function enemyCell(e: Tank): { col: number; row: number } {
  return { col: Math.round(e.x / CELL), row: Math.round(e.y / CELL) }
}

function dirDelta(dir: string): { dc: number; dr: number } {
  return dir === 'up'
    ? { dc: 0, dr: -1 }
    : dir === 'down'
      ? { dc: 0, dr: 1 }
      : dir === 'left'
        ? { dc: -1, dr: 0 }
        : { dc: 1, dr: 0 }
}

/**
 * 候选生成器（§11.2 + §11.4 确定性截断）。
 * 恒保留 2 席：God-AI 导航目标（保底）+ 当前格（"什么都不做"基线）；
 * 其余按 score = −dist(godTarget) + LOS + (1−basePressure) 降序取 top-(K−2)，
 * 平局取索引最小。全部候选过顶点合法性 + 可达性（k ≠ UNREACH）过滤。
 */
export function generateCandidates(
  world: World,
  god: GodAIInput,
  masker: ReachMasker,
  K: number,
): { cells: number[]; truncated: number; total: number } {
  const pc = playerVertex(world)
  const startValid = masker.compute(world.tileMap, pc.col, pc.row)
  const k = masker.k
  const seen = new Set<number>()
  const reserved: number[] = []
  const pool: Array<{ cell: number; score: number }> = []
  let total = 0

  const push = (col: number, row: number, reserved_: boolean): void => {
    if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return
    const idx = row * GRID + col
    total++
    if (!startValid || k[idx] === UNREACH) return
    if (seen.has(idx)) return
    seen.add(idx)
    if (reserved_) reserved.push(idx)
    else pool.push({ cell: idx, score: 0 })
  }

  // ① God-AI 导航目标（保底；_navCache 未命中时用移动方向投影 4 格）。
  {
    let tc: number
    let tr: number
    if (god._navCacheValid) {
      tc = god._navTargetCol
      tr = god._navTargetRow
    } else {
      const d = dirDelta(god._moveDir ?? 'up')
      tc = pc.col + d.dc * 4
      tr = pc.row + d.dr * 4
    }
    push(tc, tr, true)
  }
  // ② 当前格（基线）。
  push(pc.col, pc.row, true)

  // ③ 敌人后方 1–2 格（§11.2 缺陷③直击；≤4 敌 × 2 深度）。
  const tanks = world.allTanks
  const enemies: Tank[] = []
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && !t.isPlayer && t.spawnTimer <= 0) enemies.push(t)
  }
  enemies.sort((a, b) => {
    const ca = enemyCell(a)
    const cb = enemyCell(b)
    const da = Math.abs(ca.col - pc.col) + Math.abs(ca.row - pc.row)
    const db = Math.abs(cb.col - pc.col) + Math.abs(cb.row - pc.row)
    return da - db || a.id - b.id
  })
  for (let i = 0; i < Math.min(4, enemies.length); i++) {
    const e = enemies[i]
    const ec = enemyCell(e)
    const d = dirDelta(e.dir)
    for (const depth of [1, 2]) {
      push(ec.col - d.dc * depth, ec.row - d.dr * depth, false)
    }
  }

  // ④ 挡路砖墙对位格：玩家 Chebyshev 6 内、基地侧半平面的砖，取朝向玩家的邻格。
  {
    const found: Array<{ col: number; row: number; d: number }> = []
    for (let r = Math.max(0, pc.row - 6); r <= Math.min(GRID - 1, pc.row + 6); r++) {
      for (let c = Math.max(0, pc.col - 6); c <= Math.min(GRID - 1, pc.col + 6); c++) {
        if (world.tileMap.get(c, r) !== 'brick') continue
        // 基地侧半平面（朝向基地的行/列方向优先）
        const towardBase =
          (r - pc.row) * (BASE_POS.row - pc.row) + (c - pc.col) * (BASE_POS.col - pc.col)
        if (towardBase < 0) continue
        const d = Math.abs(c - pc.col) + Math.abs(r - pc.row)
        found.push({ col: c, row: r, d })
      }
    }
    found.sort((a, b) => a.d - b.d || a.row * GRID + a.col - (b.row * GRID + b.col))
    for (const b of found.slice(0, 2)) {
      // 朝玩家的邻格（坦克站位于砖前开火）
      const dc = Math.sign(pc.col - b.col)
      const dr = Math.sign(pc.row - b.row)
      if (Math.abs(b.row - pc.row) >= Math.abs(b.col - pc.col)) push(b.col, b.row + dr, false)
      else push(b.col + dc, b.row, false)
    }
  }

  // ⑤ 基地防御锚点（§9.2 蒸馏为候选）。
  {
    const anchor = computeBaseGuardAnchorImpl(god)
    if (anchor) push(anchor.col, anchor.row, false)
    const def = getDefaultDefensePositionImpl(god)
    push(def.col, def.row, false)
  }

  // ⑥ 路径中点（折返/截断；player→base 走行距离场路径长 ≥6 才取）。
  {
    const mid = pathMidpoint(world, pc)
    if (mid) push(mid.col, mid.row, false)
  }

  // top-(K−2) 确定性排序（§11.4）：−dist(godTarget) + LOS + (1−basePressure)，等权。
  const godTarget = reserved[0]
  const gtc = godTarget >= 0 ? godTarget % GRID : pc.col
  const gtr = godTarget >= 0 ? (godTarget - gtc) / GRID : pc.row
  const P = basePressure(world)
  for (const cand of pool) {
    const cc = cand.cell % GRID
    const cr = (cand.cell - cc) / GRID
    const dist = Math.abs(cc - gtc) + Math.abs(cr - gtr)
    const los = hasLos(world, pc, { col: cc, row: cr }) ? 1 : 0
    cand.score = -dist + los + (1 - P)
  }
  pool.sort((a, b) => b.score - a.score || a.cell - b.cell)
  const topK = pool.slice(0, Math.max(0, K - 2))
  const truncated = pool.length - topK.length
  return { cells: [...reserved, ...topK.map((c) => c.cell)].slice(0, K), truncated, total }
}

function pathMidpoint(
  world: World,
  pc: { col: number; row: number },
): { col: number; row: number } | null {
  // 轻量 BFS 走行距离场到基地，取路径中点格（不做完整 A*，工具内可接受）。
  const N = GRID * GRID
  const dist = new Int32Array(N).fill(-1)
  const queue = new Int32Array(N)
  let qh = 0
  let qt = 0
  const src = pc.row * GRID + pc.col
  const dstCol = BASE_POS.col
  const dstRow = BASE_POS.row
  dist[src] = 0
  queue[qt++] = src
  const grid = world.tileMap.grid
  const DX = [0, 0, -1, 1]
  const DY = [-1, 1, 0, 0]
  while (qh < qt) {
    const cur = queue[qh++]
    const cc = cur % GRID
    const cr = (cur - cc) / GRID
    if (cc === dstCol && cr === dstRow) break
    for (let s = 0; s < 4; s++) {
      const nc = cc + DX[s]
      const nr = cr + DY[s]
      if (nc < 0 || nc + 1 >= GRID || nr < 0 || nr + 1 >= GRID) continue
      const ni = nr * GRID + nc
      if (dist[ni] >= 0) continue
      let blocked = false
      for (let dr = 0; dr <= 1 && !blocked; dr++) {
        for (let dc = 0; dc <= 1 && !blocked; dc++) {
          const t = grid[nr + dr][nc + dc]
          if (t === 'steel' || t === 'water' || t === 'base') blocked = true
        }
      }
      if (blocked) continue
      dist[ni] = dist[cur] + 1
      queue[qt++] = ni
    }
  }
  const dDst = dist[dstRow * GRID + dstCol]
  if (dDst < 6) return null
  // 从基地反向走半程，收集中点格
  let cur = dstRow * GRID + dstCol
  let remaining = Math.floor(dDst / 2)
  while (remaining > 0) {
    const cc = cur % GRID
    const cr = (cur - cc) / GRID
    let next = -1
    for (let s = 0; s < 4 && next < 0; s++) {
      const nc = cc + DX[s]
      const nr = cr + DY[s]
      if (nc < 0 || nr < 0 || nc >= GRID || nr >= GRID) continue
      const ni = nr * GRID + nc
      if (dist[ni] === dist[cur] - 1) next = ni
    }
    if (next < 0) return null
    cur = next
    remaining--
  }
  const col = cur % GRID
  const row = (cur - col) / GRID
  return col + 1 < GRID && row + 1 < GRID ? { col, row } : null
}

/** 单局反事实标注（§11.1 流水线）。 */
export function runCounterfactualGame(
  stageIdx: number,
  stage: unknown,
  seed: number,
  difficulty: string,
  opts: { replan?: number; window?: number; K?: number; maxTicks?: number } = {},
): CfGameResult {
  const replan = opts.replan ?? 30
  const H = opts.window ?? 120
  const K = opts.K ?? 12
  const maxTicks = opts.maxTicks ?? MAX_TICKS

  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const god = new GodAIInput(
    world,
    { ...DEFAULT_GOD_AI_PARAMS },
    new RNG((seed ^ 0x9e3779b9) >>> 0),
  )
  const sim = new Simulation(world, god as unknown as GodAIInput)
  world.loadStageData(stage as never, stageIdx)
  god.reset()

  const decisions: CfDecision[] = []
  const obsList: Uint8Array[] = []
  const scalarList: Float32Array[] = []
  const injectList: Float32Array[] = []
  // §8.1.1 自馈注入态：prevGoal = 上一决策点的 argmax 候选（部署时执行器即此语义）。
  let prevGoalRow = -1
  let prevGoalCol = -1
  let prevGoalTick = 0
  let switches = 0
  let prevArrived = false
  let truncatedTotal = 0
  let totalCands = 0
  let t = 0
  let outcome = 'timeout'

  while (t < maxTicks) {
    sim.tick()
    god.endFrame()
    t++
    if (world.state !== 'playing') {
      outcome =
        world.state === 'gameover'
          ? world.tileMap.isBaseDestroyed()
            ? 'base_destroyed'
            : 'lives_exhausted'
          : 'stage_clear'
      break
    }
    // 决策点：replan tick + 玩家存活。
    if (t % replan !== 0) continue
    const p = world.player
    if (!p || !p.alive) continue

    const masker = new ReachMasker()
    const cands = generateCandidates(world, god, masker, K)
    if (cands.cells.length === 0) continue
    truncatedTotal += cands.truncated
    totalCands += cands.cells.length + cands.truncated

    // 编码 obs/scalars（决策点状态分布）。
    const encoder = new ObsEncoder()
    encoder.encode(world)
    const obsCopy = encoder.obs.slice()
    const scalarsCopy = encoder.scalars.slice()
    // inject：duration = 距上一决策的 tick 差；arrived = 当前格距 prevGoal Chebyshev ≤1。
    const pcHere = playerVertex(world)
    const dur = decisions.length > 0 ? t - prevGoalTick : 0
    let arrivedNow = false
    if (prevGoalCol >= 0) {
      arrivedNow =
        Math.max(Math.abs(pcHere.col - prevGoalCol), Math.abs(pcHere.row - prevGoalRow)) <= 1
    }
    const inject = writeGoalInject(
      new Float32Array(GOAL_INJECT_DIM),
      prevGoalRow,
      prevGoalCol,
      dur,
      switches,
      arrivedNow,
    )

    // 分支 rollout（§T6.1b：每分支一次 clone/restore）。
    const snap = cloneWorld(world)
    const scores: number[] = []
    const enemies0 = countEnemies(world)
    for (const cell of cands.cells) {
      restoreWorld(world, snap)
      const branch = new GoalExecutor(world, {
        rng: new RNG((seed ^ 0x5bf03635 ^ cell) >>> 0),
        promiseTicks: H + 1, // 分支内不触发 E4 重选；E1/E3/E5 触发时 goalPick 恒返回同格
        goalPick: () => cell,
      })
      sim.input = branch as unknown as GodAIInput
      branch.reset()
      // 分支前基线
      const phi0 = basePressure(world)
      let kills = 0
      let deaths = 0
      let wallLoss = 0
      let wallNow = countBaseWall(world)
      let h = 0
      while (h < H) {
        sim.tick()
        branch.endFrame()
        h++
        for (const e of world.consumeEvents()) {
          if (e.type === 'tank_destroyed') {
            if (e.by === 'player') kills++
            if ((e.tank as Tank | undefined)?.isPlayer) deaths++
          } else if (e.type === 'terrain_destroyed') {
            // 环墙损失在任何分支都计（候选比较口径一致）
          }
        }
        const w = countBaseWall(world)
        if (w < wallNow) {
          wallLoss += wallNow - w
          wallNow = w
        }
        if (world.state !== 'playing') break
      }
      const phi1 = basePressure(world)
      const enemies1 = countEnemies(world)
      scores.push(
        W_KILL * kills +
          W_DEATH * deaths +
          W_WALL * wallLoss +
          W_PRESSURE * (phi0 - phi1) +
          W_ENEMIES * (enemies0 - enemies1),
      )
    }
    restoreWorld(world, snap)
    sim.input = god as unknown as GodAIInput

    // 自馈态推进：prevGoal ← 本决策 argmax 候选（软目标的峰）。
    {
      let bestJ = 0
      for (let j = 1; j < cands.cells.length; j++) {
        if (scores[j] > scores[bestJ]) bestJ = j
      }
      const argCell = cands.cells[bestJ]
      const ac = argCell % GRID
      const ar = (argCell - ac) / GRID
      if (ac !== prevGoalCol || ar !== prevGoalRow) {
        switches++
        prevGoalCol = ac
        prevGoalRow = ar
        prevGoalTick = t
      }
      prevArrived = arrivedNow
    }

    // engage 标签（§8.3.2）：max(s) − s(当前格) > ε=0。当前格必在候选集（§11.2）。
    const pcNow = playerVertex(world)
    const curIdx = pcNow.row * GRID + pcNow.col
    const curPos = cands.cells.indexOf(curIdx)
    const curScore = curPos >= 0 ? scores[curPos] : -Infinity
    const maxScore = Math.max(...scores)
    const engage = maxScore - curScore > 0 ? 1 : 0

    // 候选 k 值（§9.4.3：shard 必存 k_i）。
    const ks = cands.cells.map((cell) => masker.k[cell])

    decisions.push({ tick: t, candidates: cands.cells, ks, scores, engage })
    obsList.push(obsCopy)
    scalarList.push(scalarsCopy)
    injectList.push(inject)
    void prevArrived
  }

  return {
    decisions,
    obs: obsList,
    scalars: scalarList,
    injects: injectList,
    outcome,
    ticks: t,
    truncated: truncatedTotal,
    totalCandidates: totalCands,
  }
}

function countBaseWall(world: World): number {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  let n = 0
  for (let col = bc - 1; col <= bc + 2; col++) {
    const t = world.tileMap.get(col, br - 1)
    if (t === 'brick' || t === 'steel') n++
  }
  for (let row = br; row <= br + 1; row++) {
    for (const col of [bc - 1, bc + 2]) {
      const t = world.tileMap.get(col, row)
      if (t === 'brick' || t === 'steel') n++
    }
  }
  return n
}

function countEnemies(world: World): number {
  let n = 0
  const tanks = world.allTanks
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (t.alive && !t.isPlayer && t.spawnTimer <= 0) n++
  }
  return n
}

// ---------------------------------------------------------------- CLI

function parseRange(s: string): number[] {
  const out: number[] = []
  for (const part of s.split(',')) {
    if (part.includes('-')) {
      const [a, b] = part.split('-').map((x) => parseInt(x, 10))
      for (let i = a; i <= b; i++) out.push(i)
    } else out.push(parseInt(part, 10))
  }
  return out
}

function main(): void {
  const t0 = Date.now()
  const args = process.argv.slice(2)
  let outDir = 'tmp/cf-goals'
  let difficulty = 'hard'
  let stagesStr = '0-34'
  let seedsStr = '1-10'
  let window = 120
  let K = 12
  let replan = 30
  let maxTicks = MAX_TICKS
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--out') outDir = args[++i]
    else if (args[i] === '--difficulty') difficulty = args[++i]
    else if (args[i] === '--stages') stagesStr = args[++i]
    else if (args[i] === '--seeds') seedsStr = args[++i]
    else if (args[i] === '--window') window = parseInt(args[++i], 10)
    else if (args[i] === '--k') K = parseInt(args[++i], 10)
    else if (args[i] === '--replan') replan = parseInt(args[++i], 10)
    else if (args[i] === '--max-ticks') maxTicks = parseInt(args[++i], 10)
  }
  const stages = parseRange(stagesStr)
  const seeds = parseRange(seedsStr)
  mkdirSync(outDir, { recursive: true })

  const lines: string[] = []
  let totalDecisions = 0
  let truncatedTotal = 0
  let totalCands = 0
  let games = 0
  for (const si of stages) {
    const stage = STAGES[si]
    if (!stage) continue
    for (const seed of seeds) {
      const res = runCounterfactualGame(si, stage, seed, difficulty, {
        replan,
        window,
        K,
        maxTicks,
      })
      games++
      totalDecisions += res.decisions.length
      truncatedTotal += res.truncated
      totalCands += res.totalCandidates
      const dir = `${outDir}/cf_s${si}_seed${seed}`
      mkdirSync(dir, { recursive: true })
      writeCfShard(dir, res, K, { difficulty, stage: si, seed, window, K, replan })
      lines.push(
        `[OK] s${si} seed${seed} decisions=${res.decisions.length} outcome=${res.outcome} ticks=${res.ticks}`,
      )
      if (games % 10 === 0) {
        const elapsed = (Date.now() - t0) / 1000
        console.error(
          `[cf] ${games} games, ${totalDecisions} decisions, ${elapsed.toFixed(0)}s (${(elapsed / games).toFixed(2)} s/game)`,
        )
      }
    }
  }

  const coverage = totalCands > 0 ? (1 - truncatedTotal / totalCands).toFixed(3) : 'n/a'
  const summary = {
    collector: 'CF-GOAL',
    firePolicy: 'firecontrol-l3-min', // §11.3.1：标注所用开火策略版本
    difficulty,
    stages: stagesStr,
    seeds: seedsStr,
    window,
    K,
    replan,
    games,
    decisions: totalDecisions,
    candidateCoverage: coverage, // §11.4 验收统计
    elapsedSec: +((Date.now() - t0) / 1000).toFixed(1),
  }
  writeFileSync(`${outDir}/_cf_report.json`, JSON.stringify(summary, null, 2))
  console.log(lines.join('\n'))
  console.log(`\n=== CF-GOAL labeling ===`)
  console.log(`games=${games} decisions=${totalDecisions} candidateCoverage=${coverage}`)
  console.log(`window=${window} K=${K} replan=${replan} (firePolicy firecontrol-l3-min)`)
  console.log(`shards under: ${outDir}  (consume with train_goal_bc.py)`)
}

function writeCfShard(
  dir: string,
  res: CfGameResult,
  K: number,
  meta: Record<string, unknown>,
): void {
  const N = res.decisions.length
  if (N === 0) return
  const obs = new Uint8Array(N * 14 * 26 * 26)
  const scalars = new Float32Array(N * 19)
  const injects = new Float32Array(N * GOAL_INJECT_DIM)
  const cells = new Uint16Array(N * K).fill(UNREACH)
  const ks = new Uint16Array(N * K).fill(UNREACH)
  const ss = new Float32Array(N * K).fill(0)
  const engage = new Uint8Array(N)
  for (let i = 0; i < N; i++) {
    const d = res.decisions[i]
    obs.set(res.obs[i], i * 14 * 26 * 26)
    scalars.set(res.scalars[i], i * 19)
    injects.set(res.injects[i], i * GOAL_INJECT_DIM)
    for (let j = 0; j < d.candidates.length && j < K; j++) {
      cells[i * K + j] = d.candidates[j]
      ks[i * K + j] = d.ks[j]
      ss[i * K + j] = d.scores[j]
    }
    engage[i] = d.engage
  }
  writeNpy(`${dir}/obs.npy`, obs, [N, 14, 26, 26], 'u1')
  writeNpy(`${dir}/scalars.npy`, scalars, [N, 19], 'f4')
  writeNpy(`${dir}/inject.npy`, injects, [N, GOAL_INJECT_DIM], 'f4')
  writeNpy(`${dir}/cand_cell.npy`, cells, [N, K], 'u2')
  writeNpy(`${dir}/cand_k.npy`, ks, [N, K], 'u2')
  writeNpy(`${dir}/cand_s.npy`, ss, [N, K], 'f4')
  writeNpy(`${dir}/engage.npy`, engage, [N], 'u1')
  writeFileSync(`${dir}/manifest.json`, JSON.stringify({ ...meta, nDecisions: N }, null, 2))
}

if (import.meta.main) main()
