/**
 * reach-mask.ts — goal 动作空间的带代价可达性掩码（plan/Goal-Space-Policy-Rebuild.md
 * §9.4 / §9.4.0 / T3 卡评审 G8+a2 规格）。
 *
 * 动作空间 = 坦克左上顶点 (row, col)。GRID=26、TANK 占 2×2 ⇒ 合法顶点 [0,24]² = 625 个；
 * 第 25 行/列共 51 格是物理上永远不可作为顶点的越界格 ⇒ **永久硬遮 −Infinity**（§9.4.0）。
 *
 * 三层掩码（§9.4）：
 *   blocked   钢/基地/水/越界顶点          → −Infinity（绝对不可达）
 *   walk      纯行走可达                    → 0
 *   carve     需凿 k 块砖                    → −λ·k（带代价，不硬遮 —— 保留"凿开就到"的格）
 *
 * k 的定义：从玩家当前顶点到目标顶点、4 向逐格移动（2×2 足印只检查前缘 2 格，
 * 与 pathfind §130 同一技巧），路径上必须击毁的砖格总数（含目标顶点足印内的砖）。
 * Dijkstra 字典序最小化 (k, steps)：k 为主准则（mask 只用 k），steps 为确定性 tie-break。
 *
 * 池化规格（评审 a2 / AGENTS §14）：常驻实例，构造时一次性预分配
 * dist / closed / kArr / mask / heap 全部 TypedArray；每次 compute 只覆写，零堆分配。
 * 备忘：同 (tileMap.revision, start) 直接复用 k-field（E3 只认静态地形 ⇒ 可达性准静态，
 * §6.6 连带收益）。消费方各持有自己的实例（执行器 / 采集器 / 标注管线），不做模块级
 * 单例 —— findPath 的模块缓冲不可重入，多消费者共用会互踩。
 *
 * 确定性：同 revision + 同 start ⇒ 同一 mask（单测断言）；零 world.rng 消费。
 */
import { GRID } from '../../constants'
import type { TileMap } from '../../game/TileMap'

/** λ 初值（§9.4.2）：每需凿 1 块砖，扣 0.5 热图 logit。T9a 后可扫 {0.25, 0.5, 1.0}。 */
export const GOAL_MASK_LAMBDA_DEFAULT = 0.5

/** Dijkstra 堆容量：676 顶点 × 4 边的 lazy 插入上界（含冗余）。 */
const HEAP_CAP = 4096

/** steps 主循环上限（packed cost = k*4096 + steps 需 steps < 4096 保 f32 精确）。 */
const STEPS_CAP = 4095

/** 无效顶点（越界/不可达）在 kArr 里的哨兵值。 */
export const REACH_UNREACHABLE = 65535

/** 前缘 2 格相对偏移（列, 行）——与 pathfind §130 的 EDGE_DC/EDGE_DR 表同源语义。
 *  0 up → 新行 nr；1 down → 新行 nr+1；2 left → 新列 nc；3 right → 新列 nc+1。 */
const EDGE_C0 = [0, 0, 0, 1]
const EDGE_R0 = [0, 1, 0, 0]
const EDGE_C1 = [1, 1, 0, 1]
const EDGE_R1 = [0, 1, 1, 1]
const STEP_DC = [0, 0, -1, 1]
const STEP_DR = [-1, 1, 0, 0]

/**
 * 静态顶点判定（E3 专用，不跑 Dijkstra）：顶点越界（r==25‖c==25 的 51 格）或
 * 2×2 足印含钢/基地/水 ⇒ 静态不可达。**砖不算**——carve 格是可达的（§9.4 带代价层），
 * 把砖写进 E3 会误触发高频重选（§6.6 明令禁止）。
 */
export function isVertexStaticallyBlocked(tileMap: TileMap, col: number, row: number): boolean {
  if (col < 0 || col + 1 >= GRID || row < 0 || row + 1 >= GRID) return true
  const grid = tileMap.grid
  for (let dr = 0; dr <= 1; dr++) {
    const grow = grid[row + dr]
    for (let dc = 0; dc <= 1; dc++) {
      const t = grow[col + dc]
      if (t === 'steel' || t === 'base' || t === 'water') return true
    }
  }
  return false
}

export class ReachMasker {
  private readonly kArr = new Uint16Array(GRID * GRID)
  private readonly maskBuf = new Float32Array(GRID * GRID)
  private readonly dist = new Float32Array(GRID * GRID)
  private readonly closed = new Uint8Array(GRID * GRID)
  private readonly heapNode = new Int32Array(HEAP_CAP)
  private readonly heapCost = new Float32Array(HEAP_CAP)
  private heapSize = 0

  private memoRevision = -1
  private memoStart = -1 // row * GRID + col（-1 = 未缓存；无效起点 = -2）
  private memoStartValid = false
  private memoLambda = NaN

  constructor() {
    this.kArr.fill(REACH_UNREACHABLE)
    this.maskBuf.fill(-Infinity)
  }

  /** 上次 compute 的 k-field 只读视图（REACH_UNREACHABLE = 不可达/越界）。 */
  get k(): Uint16Array {
    return this.kArr
  }

  /**
   * 重建 k-field（同 revision+start 命中备忘则直接复用）。
   * @returns start 顶点是否有效（false = 足印被钢/基地/水堵死或越界 ⇒ 全图不可达）。
   */
  compute(tileMap: TileMap, startCol: number, startRow: number): boolean {
    // 先验证再定 memo key（无效起点统一 key=-2，避免负坐标 key 与合法 key 碰撞）。
    const startInvalid =
      startCol < 0 ||
      startCol + 1 >= GRID ||
      startRow < 0 ||
      startRow + 1 >= GRID ||
      isVertexStaticallyBlocked(tileMap, startCol, startRow)
    const start = startInvalid ? -2 : startRow * GRID + startCol
    if (this.memoRevision === tileMap.revision && this.memoStart === start) {
      return this.memoStartValid
    }
    this.memoRevision = tileMap.revision
    this.memoStart = start
    this.memoLambda = NaN // k-field 变了，mask 缓存失效
    const kArr = this.kArr

    // 无效起点：全图不可达（含 51 越界格），返回 false。selectGoal 将回退（§9.4.1）。
    if (startInvalid) {
      kArr.fill(REACH_UNREACHABLE)
      this.memoStartValid = false
      return false
    }
    this.memoStartValid = true

    // ---- Dijkstra（字典序 (k, steps)，packed = k*4096 + steps，f32 精确域内）----
    const dist = this.dist
    const closed = this.closed
    dist.fill(Infinity)
    closed.fill(0)
    this.heapSize = 0
    dist[start] = 0
    this.push(start, 0)

    const grid = tileMap.grid
    while (this.heapSize > 0) {
      const node = this.pop()
      if (closed[node] !== 0) continue
      closed[node] = 1
      const packed = dist[node]
      const steps = packed % 4096
      const k = (packed - steps) / 4096
      if (steps >= STEPS_CAP) continue // 路径长度饱和（k 已主序，安全剪枝）
      const col = node % GRID
      const row = (node - col) / GRID
      for (let s = 0; s < 4; s++) {
        const nc = col + STEP_DC[s]
        const nr = row + STEP_DR[s]
        if (nc < 0 || nc + 1 >= GRID || nr < 0 || nr + 1 >= GRID) continue
        // 前缘 2 格：本步新进入的子块（另一侧 2 格与当前足印共享，已验证）。
        const c0 = nc + EDGE_C0[s]
        const r0 = nr + EDGE_R0[s]
        const c1 = nc + EDGE_C1[s]
        const r1 = nr + EDGE_R1[s]
        const t0 = grid[r0][c0]
        const t1 = grid[r1][c1]
        let bk = k
        let blockedEdge = false
        // 逐格判型：brick 可凿（k+1），steel/base/water 绝对阻断。
        if (t0 === 'brick') bk++
        else if (t0 === 'steel' || t0 === 'base' || t0 === 'water') blockedEdge = true
        if (t1 === 'brick') bk++
        else if (t1 === 'steel' || t1 === 'base' || t1 === 'water') blockedEdge = true
        if (blockedEdge) continue
        const nk = nr * GRID + nc
        if (closed[nk] !== 0) continue
        const np = bk * 4096 + (steps + 1)
        if (np < dist[nk]) {
          dist[nk] = np
          this.push(nk, np)
        }
      }
    }

    for (let i = 0; i < kArr.length; i++) {
      kArr[i] = closed[i] !== 0 ? (dist[i] - (dist[i] % 4096)) / 4096 : REACH_UNREACHABLE
    }
    return true
  }

  /**
   * 掩码视图：walk=0、carve=−λ·k、blocked=−Infinity、51 越界格=−Infinity。
   * λ 变化只重着色（不重跑 Dijkstra）。返回只读缓冲——调用方不得持有跨 compute 引用。
   */
  mask(lambda: number = GOAL_MASK_LAMBDA_DEFAULT): Float32Array {
    if (this.memoLambda === lambda) return this.maskBuf
    this.memoLambda = lambda
    const kArr = this.kArr
    const mask = this.maskBuf
    for (let i = 0; i < mask.length; i++) {
      const k = kArr[i]
      // k=0 特判：避免 -lambda*0 = -0（Object.is(-0,0)=false 会破坏确定性断言）。
      mask[i] = k === REACH_UNREACHABLE ? -Infinity : k === 0 ? 0 : -lambda * k
    }
    return mask
  }

  // ---- 二叉最小堆（lazy deletion；packed cost 相同按 node 索引 tie-break 保确定性）----
  private push(node: number, cost: number): void {
    let i = this.heapSize++
    this.heapNode[i] = node
    this.heapCost[i] = cost
    while (i > 0) {
      const p = (i - 1) >> 1
      if (this.less(i, p)) {
        this.swap(i, p)
        i = p
      } else break
    }
  }

  private pop(): number {
    const top = this.heapNode[0]
    this.heapSize--
    if (this.heapSize > 0) {
      this.heapNode[0] = this.heapNode[this.heapSize]
      this.heapCost[0] = this.heapCost[this.heapSize]
      let i = 0
      for (;;) {
        const l = 2 * i + 1
        const r = l + 1
        let m = i
        if (l < this.heapSize && this.less(l, m)) m = l
        if (r < this.heapSize && this.less(r, m)) m = r
        if (m === i) break
        this.swap(i, m)
        i = m
      }
    }
    return top
  }

  private less(a: number, b: number): boolean {
    const ca = this.heapCost[a]
    const cb = this.heapCost[b]
    if (ca !== cb) return ca < cb
    return this.heapNode[a] < this.heapNode[b]
  }

  private swap(a: number, b: number): void {
    const n = this.heapNode[a]
    this.heapNode[a] = this.heapNode[b]
    this.heapNode[b] = n
    const c = this.heapCost[a]
    this.heapCost[a] = this.heapCost[b]
    this.heapCost[b] = c
  }
}

/**
 * selectGoal（§9.4.1）：推理前强制 mask 的选格算法。训练与推理共用同一实现。
 *   argmax(heat + mask)，mask=−Infinity 硬遮、carve 格 −λ·k 扣分不遮死；
 *   平局取索引最小（行主序，§9.4.2 —— T8 单测显式断言防漂移）。
 * 全遮时返回 −1（调用方走有序回退：原地停射 → 邻域最近可达格 → God-AI 兜底）。
 */
export function selectGoal(heat: Float32Array, mask: Float32Array): number {
  let best = -1
  let bestV = -Infinity
  for (let i = 0; i < 676; i++) {
    const m = mask[i]
    if (m === -Infinity) continue
    const v = heat[i] + m
    if (v > bestV) {
      bestV = v
      best = i
    }
  }
  return best
}
