/**
 * goal-mask.ts — 「StudentNet + goal 硬掩码」测试实现（goal-nn-action 1a 语义）。
 *
 * 目标：验证"外部战略目标约束能否抬升 goal-less 执行器平台"（c5 上 nn 47% vs 人类 ~100%）。
 * 机制：执行器（per-tick StudentNet）不动；外部目标源（God-AI 导航目标 / 手写启发式）
 * 每承诺期选一个目的地；BFS 距离掩码**禁止背离目的地**的 move（硬 mask，1/0，非软偏置）。
 * 只禁不禁劝：执行器保留全部战斗自由度，仅不能"跑离目标"。
 *
 * 确定性：纯 World 只读 + 零 RNG 消费；同 (world, dest) 输出逐位一致。
 * 测试用途：仅评估（eval-course-ckpt --policy nn-goal），不进训练热路径。
 */
import { GRID, CELL, BASE_POS } from '../constants'
import type { World } from '../game/World'
import type { GodAIInput } from '../ai/GodAIInput'

export type GoalSource = 'god' | 'heuristic'

/** 可步行地形（与 GoalExecutor.corruptTarget 同口径：empty/forest/ice）。 */
function walkable(t: string): boolean {
  return t === 'empty' || t === 'forest' || t === 'ice'
}

function cellOfPx(px: number, py: number): { col: number; row: number } {
  const col = Math.max(0, Math.min(GRID - 1, Math.round(px / CELL)))
  const row = Math.max(0, Math.min(GRID - 1, Math.round(py / CELL)))
  return { col, row }
}

/**
 * BFS 距离场（26×26 = 676 格，eval 期一次 BFS 可接受）。目标格不可达 → 全 -1。
 * 返回值：row*GRID+col → 到目的地的步数（-1 = 不可达）。
 */
export function bfsDistance(world: World, dc: number, dr: number): Int16Array {
  const dist = new Int16Array(GRID * GRID).fill(-1)
  const tm = world.tileMap
  const goalIdx = dr * GRID + dc
  if (dc < 0 || dc >= GRID || dr < 0 || dr >= GRID || !walkable(tm.get(dc, dr))) return dist
  dist[goalIdx] = 0
  const queue: number[] = [goalIdx]
  let head = 0
  while (head < queue.length) {
    const idx = queue[head++]
    const c = idx % GRID
    const r = (idx / GRID) | 0
    const nd = dist[idx] + 1
    // up / down / left / right
    if (r > 0) {
      const n = idx - GRID
      if (dist[n] === -1 && walkable(tm.get(c, r - 1))) {
        dist[n] = nd
        queue.push(n)
      }
    }
    if (r < GRID - 1) {
      const n = idx + GRID
      if (dist[n] === -1 && walkable(tm.get(c, r + 1))) {
        dist[n] = nd
        queue.push(n)
      }
    }
    if (c > 0) {
      const n = idx - 1
      if (dist[n] === -1 && walkable(tm.get(c - 1, r))) {
        dist[n] = nd
        queue.push(n)
      }
    }
    if (c < GRID - 1) {
      const n = idx + 1
      if (dist[n] === -1 && walkable(tm.get(c + 1, r))) {
        dist[n] = nd
        queue.push(n)
      }
    }
  }
  return dist
}

/**
 * goal 硬掩码：length 5 [stay, up, down, left, right]，1 = 允许 / 0 = 禁止。
 * 规则：stay 恒允许；方向 d 允许 ⇔ 走一步后到目的地的距离**不增**（禁背离目标）。
 * 玩家不可操作 / 目的地不可达 ⇒ 全 1（回退执行器本能，mask 不干预）。
 */
export function goalMoveMask(world: World, dc: number, dr: number): number[] {
  const p = world.player
  if (!p || !p.alive) return [1, 1, 1, 1, 1]
  const pc = cellOfPx(p.x, p.y)
  const dist = bfsDistance(world, dc, dr)
  const cur = dist[pc.row * GRID + pc.col]
  if (cur === -1) return [1, 1, 1, 1, 1]
  const mask: number[] = [1, 0, 0, 0, 0]
  // [up, down, left, right]
  const dirs: ReadonlyArray<readonly [number, number]> = [
    [0, -1],
    [0, 1],
    [-1, 0],
    [1, 0],
  ]
  for (let i = 0; i < 4; i++) {
    const nc = pc.col + dirs[i][0]
    const nr = pc.row + dirs[i][1]
    if (nc < 0 || nc >= GRID || nr < 0 || nr >= GRID) continue
    const nd = dist[nr * GRID + nc]
    if (nd !== -1 && nd <= cur) mask[i + 1] = 1
  }
  return mask
}

/**
 * goal 软偏置（1b-posthoc）：length 5 [stay, up, down, left, right]，取值 {-1,0,1}。
 * +1 = 该方向缩短到目标距离；−1 = 背离；0 = 持平/玩家不可操作/目标不可达（不干预）。
 * 用法：logits[i] += β · align[i]（诱导不禁止，执行器机动保留）。
 */
export function goalMoveBias(world: World, dc: number, dr: number): number[] {
  const p = world.player
  if (!p || !p.alive) return [0, 0, 0, 0, 0]
  const pc = cellOfPx(p.x, p.y)
  const dist = bfsDistance(world, dc, dr)
  const cur = dist[pc.row * GRID + pc.col]
  if (cur === -1) return [0, 0, 0, 0, 0]
  const align: number[] = [0, 0, 0, 0, 0]
  const dirs: ReadonlyArray<readonly [number, number]> = [
    [0, -1],
    [0, 1],
    [-1, 0],
    [1, 0],
  ]
  for (let i = 0; i < 4; i++) {
    const nc = pc.col + dirs[i][0]
    const nr = pc.row + dirs[i][1]
    if (nc < 0 || nc >= GRID || nr < 0 || nr >= GRID) continue
    const nd = dist[nr * GRID + nc]
    if (nd === -1) continue
    align[i + 1] = nd < cur ? 1 : nd > cur ? -1 : 0
  }
  return align
}

/** 目标源 A：God-AI 当前导航目标（幻影点修复后只消费真实 nav 目标）。 */
export function pickGodGoal(god: GodAIInput): { col: number; row: number } | null {
  if (!(god as { _navCacheValid?: boolean })._navCacheValid) return null
  const g = god as unknown as { _navTargetCol: number; _navTargetRow: number }
  return {
    col: Math.max(0, Math.min(GRID - 1, g._navTargetCol)),
    row: Math.max(0, Math.min(GRID - 1, g._navTargetRow)),
  }
}

/** 目标源 B：手写启发式——血低撤退到基地上方；否则追最近存活敌；无敌则回基地位。 */
export function pickHeuristicGoal(world: World): { col: number; row: number } {
  const p = world.player
  const pc = p ? cellOfPx(p.x, p.y) : { col: BASE_POS.col, row: BASE_POS.row - 2 }
  // 1) 血量 < 40% ⇒ 撤退（基地上方 2 格，退路短）。
  if (p && p.hp < (p.maxHp || 1) * 0.4) {
    return { col: BASE_POS.col, row: Math.max(0, BASE_POS.row - 2) }
  }
  // 2) 追最近存活敌。
  let best: { col: number; row: number; d: number } | null = null
  for (let i = 0; i < world.tanks.length; i++) {
    const t = world.tanks[i]
    if (!t.alive || t.isPlayer || t.allegiance !== 'enemy') continue
    const tc = Math.max(0, Math.min(GRID - 1, Math.round(t.x / CELL)))
    const tr = Math.max(0, Math.min(GRID - 1, Math.round(t.y / CELL)))
    const d = Math.abs(tc - pc.col) + Math.abs(tr - pc.row)
    if (!best || d < best.d) best = { col: tc, row: tr, d }
  }
  if (best) return { col: best.col, row: best.row }
  // 3) 场清 → 基地上方。
  return { col: BASE_POS.col, row: Math.max(0, BASE_POS.row - 2) }
}

/**
 * goal 转向器：承诺期（promiseTicks）持有同一目标，到期/失效重选。
 * god 源在导航缓存失效时回退启发式（不产生幻影目标）。
 */
export class GoalSteering {
  readonly source: GoalSource
  private god: GodAIInput | null
  private promiseTicks: number
  private cell: { col: number; row: number } | null = null
  private bornTick = -1

  constructor(source: GoalSource, god: GodAIInput | null, promiseTicks = 240) {
    this.source = source
    this.god = god
    this.promiseTicks = promiseTicks
  }

  get currentGoal(): { col: number; row: number } | null {
    return this.cell
  }

  /** 每 tick 调用：过期/无目标则重选，返回 length-5 硬掩码。 */
  maskForTick(world: World, tick: number): number[] {
    if (!this.cell || tick - this.bornTick > this.promiseTicks) {
      this.reselect(world, tick)
    }
    if (!this.cell) return [1, 1, 1, 1, 1]
    return goalMoveMask(world, this.cell.col, this.cell.row)
  }

  private reselect(world: World, tick: number): void {
    if (this.source === 'god' && this.god) {
      const g = pickGodGoal(this.god)
      if (g) {
        this.cell = g
        this.bornTick = tick
        return
      }
    }
    this.cell = pickHeuristicGoal(world)
    this.bornTick = tick
  }
}
