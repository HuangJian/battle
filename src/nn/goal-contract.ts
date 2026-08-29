/**
 * goal-contract.ts — 目标契约（plan/Goal-Space-Policy-Rebuild.md §6 / 任务卡 T8）。
 *
 * 契约 = "去哪 + 为什么 + 承诺多久"。网络热图只产 cellIndex（纯空间），不产"理由"
 * （评审 A2 §6.1.1）⇒ premise 分两层：
 *   ① predicates — 通用动态谓词，对所有契约统一适用、每 tick 可求值；任一为假 ⇒ E1。
 *      （与"选了哪一格"无关：基地存续 / 玩家可操作 / 关卡进行中。）
 *   ② label — 静态/准静态地图条件的可读归因标签（如 central-breach / brick-dense），
 *      只供仪表与失效归因，**不参与 E1 判定**（随地图静态成立 ⇒ 作 E1 判据是死代码）。
 *
 * 失效条款（§6.2，触发**重新评估**非立即放弃；定序固定 E3 > E1 > E5 > E4 > E2）：
 *   E3 静态地形不可达（cached reach-mask 该格 ≡ −Infinity —— 只认静态地形，
 *      动态敌坦占位不触发，§6.6；防抖回归测试见 tests/nn/goal-contract.test.ts）
 *   E1 前提不成立（premise.predicates 任一为假，纯谓词不过网）
 *   E5 安全位移超限（累计被 dodge 位移 > N tick，N 初值 60，§6.5.1）
 *   E4 超出承诺期（tick − bornTick > T —— 慢心跳处理，这里只做判定）
 *   E2 价值跳变（**T8-min 关闭**：value 头在 PPO 前不可信，§6.7 两期化；
 *      T8-full 启用 = 状态价值跳变触发重评估，§6.8，不比较目标）
 *
 * 确定性：全部谓词为 World 纯读；零 world.rng 消费；零分配（谓词数组为模块级常量）。
 */
import { BASE_POS } from '../constants'
import type { World } from '../game/World'
import type { Cell } from '../utils/grid-search'
import { detectCentralBreachRisk } from '../ai/god/stage-adapt'

/** 契约（§6.1 接口；targetCell/K 已随 target/K 头被砍，不得回加，§8.3.0b）。 */
export interface GoalContract {
  /** 去哪（坦克顶点；过可达性掩码 + §9.4.0 的 25×25 合法顶点域）。 */
  cell: Cell
  premise: Premise
  /** 最长承诺 tick。 */
  T: number
  /** 预计到达 tick（pathfind 距离；可满足性校验 travelEst ≤ T）。 */
  travelEst: number
  bornTick: number
}

/** E1 用：通用动态谓词（§6.1.1 层①）。 */
export type PremisePredicate = (world: World, contract: GoalContract) => boolean

export interface Premise {
  predicates: PremisePredicate[]
  /** 归因标签（§6.1.1 层②，不参与 E1）。 */
  label: string
}

/** E5 阈值 N 初值（§6.5.1）：被 dodge 累计推离 1 秒说明这条路走不通。 */
export const E5_DODGE_TICK_LIMIT = 60

/** 失效条款 id（定序 E3 > E1 > E5 > E4 > E2，§6.2）。 */
export type ClauseId = 'E1' | 'E3' | 'E4' | 'E5' | 'E2'

/** evaluateContract 的调用侧上下文（执行器维护的逐 tick 状态）。 */
export interface ContractEvalContext {
  /** 当前 tick（world.frame）。 */
  tick: number
  /** 本契约累计被 dodge 位移的 tick 数（执行器累计；E5 判据）。 */
  dodgeTicks: number
  /** 缓存 reach-mask 在契约格上是否 ≡ −Infinity（执行器按 tileMap.revision 刷新；E3 判据）。 */
  goalMaskedOut: boolean
}

/**
 * 契约失效评估（§6.2 定序 E3 > E1 > E5 > E4；E2 在 T8-min 关闭）。
 * 返回 null = 契约仍然有效。
 */
export function evaluateContract(
  contract: GoalContract,
  world: World,
  ctx: ContractEvalContext,
): ClauseId | null {
  if (ctx.goalMaskedOut) return 'E3'
  for (let i = 0; i < contract.premise.predicates.length; i++) {
    if (!contract.premise.predicates[i](world, contract)) return 'E1'
  }
  if (ctx.dodgeTicks > E5_DODGE_TICK_LIMIT) return 'E5'
  if (ctx.tick - contract.bornTick > contract.T) return 'E4'
  return null
}

// ---- 通用动态谓词（§6.1.1 层①；模块级常量，零分配）----

/** 基地未被摧毁。 */
function baseIntact(world: World): boolean {
  return !world.tileMap.isBaseDestroyed()
}

/**
 * 玩家可操作。计划文本的"冰冻/失控"在本代码库的对应物：本作 freeze 道具冻结的是
 * **敌人**（world.freezeTimer，是交战理由而非前提失效）；玩家侧真正的"不可控"是
 * 阵亡/等待重生（InputLike 无法执行任何导航）⇒ 谓词判存活。
 */
function playerOperable(world: World): boolean {
  const p = world.player
  return p !== null && p.alive && p.spawnTimer <= 0
}

/** 关卡仍在进行（stageclear/gameover 后契约无意义）。 */
function stageInProgress(world: World): boolean {
  return world.state === 'playing'
}

const DEFAULT_PREMISE_PREDICATES: PremisePredicate[] = [baseIntact, playerOperable, stageInProgress]

/**
 * 默认 premise 工厂：层① 谓词取上面的通用三项；层② label 按 §6.5 现成纯函数归因
 * （中路无钢 → 'central-breach'，否则按砖密度档位记 'brick-dense' | 'open'）。
 * label 只在选定时求值一次（准静态属性）。
 */
export function makeDefaultPremise(world: World): Premise {
  let label = 'open'
  if (detectCentralBreachRisk(world)) label = 'central-breach'
  else {
    // 砖密度档位（§6.5 原型二；26×26 扫一遍，仅选定时调用）
    let bricks = 0
    for (let r = 0; r < world.tileMap.grid.length; r++) {
      const grow = world.tileMap.grid[r]
      for (let c = 0; c < grow.length; c++) if (grow[c] === 'brick') bricks++
    }
    if (bricks >= 140) label = 'brick-dense' // ≈ 85.7% 分位（§1.1 原型二阈值）
  }
  return { predicates: DEFAULT_PREMISE_PREDICATES, label }
}

/**
 * 契约构造（含 §6.1.1 可满足性校验：travelEst ≤ T，否则拒绝 → 返回 null，
 * 调用方继续用上一契约或走回退）。
 */
export function makeGoalContract(
  cell: Cell,
  world: World,
  tick: number,
  T: number,
  travelEst: number,
  force = false,
): GoalContract | null {
  if (!force && travelEst > T) return null
  return { cell, premise: makeDefaultPremise(world), T, travelEst, bornTick: tick }
}

/** 基地环格判定（countBaseWall 同款 8 格保护圈）——dodge 位移计数的辅助。 */
export function isBaseRingCellCol(col: number, row: number): boolean {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  if (row === br - 1 && col >= bc - 1 && col <= bc + 2) return true
  if (row >= br && row <= br + 1 && (col === bc - 1 || col === bc + 2)) return true
  return false
}
