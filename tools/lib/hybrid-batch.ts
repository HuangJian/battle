/**
 * hybrid-batch.ts — 混合跑批调度状态机：共享游标 + 尾部 fan-out 竞速 + 幂等结算。
 *
 * rollout queue v3.7 机制（nn-training/rl/queue.py：tail_fanout_n / tail_fanout_dup /
 * duplicate-settled suppression）的 TS 提取。m1-eval 的本地/远端消费循环通过
 * claim()/settle() 消费本状态机；python 侧机制在 queue.py 已完备，trainer 的
 * clean-eval 经 m1-eval --dist-nodes 间接复用同一语义（能力映射表见
 * docs/goal-nn.progress.md §6）。
 *
 * 语义：
 *   claim() — 游标模式发号 i = next++；游标耗尽且未结算数 ≤ fanoutN 时进入
 *             **竞速模式**：返回最小未结算且在跑副本 < fanoutDup 的任务号
 *             （副本计数 +1），空闲消费者复制尾部任务、先结算者胜——尾部
 *             不再被单个慢消费者拖住。无可发号返回 −1（消费者退出）。
 *   settle(i, r) — 幂等（首个写入者胜，竞速副本后到即弃）；进度只计一次。
 *   whenAll()  — pending 消费者归零后 resolve。
 *
 * 确定性：结果按任务号幂等收敛，竞速不影响任何一局内容（同 task 同结果）。
 */

export class TailRaceBatch {
  readonly total: number
  private readonly fanoutN: number
  private readonly fanoutDup: number
  private next = 0
  private doneCount = 0
  private settled: boolean[] = []
  private readonly inflight = new Map<number, number>()
  private pending = 0
  private resolveAll: () => void = () => {}
  private readonly all: Promise<void>

  constructor(total: number, fanoutN = 4, fanoutDup = 2) {
    this.total = total
    this.fanoutN = fanoutN
    this.fanoutDup = fanoutDup
    this.settled = new Array<boolean>(total).fill(false)
    this.all = new Promise<void>((r) => (this.resolveAll = r))
  }

  /** 消费者进入（cap 个槽各计一次）。 */
  consumer(cap: number): void {
    this.pending += Math.max(1, cap)
  }

  /** 消费者退出；pending 归零即 resolve。 */
  finishConsumer(): void {
    this.pending--
    if (this.pending <= 0) this.resolveAll()
  }

  /**
   * 取下一个要跑的任务号；无可跑任务返回 −1（消费者退出）。
   * @param hasInflightTail 消费者视角是否存在"在跑的尾部任务"——竞速的进入条件
   *        之一（复制有意义的前提是有主副本在跑）。游标未耗尽时忽略。
   */
  claim(hasInflightTail: boolean): number {
    if (this.next < this.total) {
      const i = this.next++
      this.inflight.set(i, (this.inflight.get(i) ?? 0) + 1)
      return i
    }
    if (this.fanoutN <= 0) return -1
    const remaining = this.total - this.doneCount
    if (remaining <= 0 || !hasInflightTail) return -1
    if (remaining > this.fanoutN) return -1
    const i = this.firstUnsettled()
    if (i < 0) return -1
    this.inflight.set(i, (this.inflight.get(i) ?? 0) + 1)
    return i
  }

  /** 最小未结算且副本未满的任务号；无则 −1。 */
  private firstUnsettled(): number {
    for (let i = 0; i < this.total; i++) {
      if (!this.settled[i] && (this.inflight.get(i) ?? 0) < this.fanoutDup) return i
    }
    return -1
  }

  /** 结算（幂等）：首个写入者胜；返回是否为首次结算（账本/进度只计一次）。 */
  settle(i: number): boolean {
    if (i < 0 || i >= this.total || this.settled[i]) return false
    this.settled[i] = true
    this.inflight.delete(i)
    this.doneCount++
    return true
  }

  isSettled(i: number): boolean {
    return this.settled[i] === true
  }

  get done(): number {
    return this.doneCount
  }

  get pendingConsumers(): number {
    return this.pending
  }

  /** 无消费者守护：把全部未结算任务标败收尾，返回标记的任务号（已结算的不动）。 */
  failUnsettled(): number[] {
    const out: number[] = []
    for (let i = 0; i < this.total; i++) {
      if (!this.settled[i]) {
        this.settled[i] = true
        this.doneCount++
        out.push(i)
      }
    }
    this.resolveAll()
    return out
  }

  /** 存在已发号但未结算的任务（竞速进入条件）。 */
  get hasInflight(): boolean {
    for (const c of this.inflight.values()) if (c > 0) return true
    return false
  }

  /** 等待全部消费者退出（pending 归零）。 */
  whenAll(): Promise<void> {
    return this.all
  }
}
