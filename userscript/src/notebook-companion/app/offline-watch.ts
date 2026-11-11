/**
 * offline-watch.ts — 离线训练页（`battle.offline.ipynb`）的「训练 cell 真正开始执行」检测。
 *
 * 队列里的 cell 与真正执行的 cell 在 JupyterLab 上可能共用 `jp-mod-running` / `[*]` 外观
 * （`sites/kaggle.ts` 的既有判据只能回答「在执行或排队」），所以这里不靠单点状态变化：
 *   - 目标 cell 的**输出相对上一拍**变化（Jupyter 开始执行会先清空/刷新输出区）；或
 *   - 内核 `busy` 且没有别的 cell 在跑（排队时前一个 cell 还在跑）；或
 *   - **连续 `holdPolls` 拍都在执行、且已等满 `heldMs`**（看门狗兜底，见下）。
 * 证据持续 `holdMs` 才上报一次；cell 回到 idle 后下一轮重新计。只读：不改 DOM、不发命令。
 *
 * ★ 2026-11-11（o2-notax 现场）：上线两天、`offline:cell-start` **一条都没有**，而同提交的
 * `offline:detected` / `stopPpo` 都在（见 `DECISIONS.md §2026-11-09-goalnn-offline-start-report`）。
 * 复查发现判据有三处「静默 fail closed」，而且**没有一处有读数出口**（坏掉时与「没开始训练」同形）：
 *   ① 基线在**已经开始执行之后**才采（第一拍），只认「从这一拍往后输出继续变」——前几秒没输出就永远错过；
 *   ② `busy` 依赖 Jupyter 状态栏（Kaggle 上通常没有 ⇒ `kernelStatus` 恒 `unknown`）；
 *   ③ `target` 缓存永不失效：页面重渲染换了 id ⇒ `cells.find` 永远找不到、永久哑。
 * 本版：① 改成「相对上一拍」的变化；② 增加看门狗（连续 N 拍执行 + 等满 heldMs ⇒ `held`，
 * 信息性事件，宁可晚 1s 也别永远不说）；③ `target` 连续 `missingLimit` 拍找不到就重新识别；
 * ④ 新增 `diag()`，让调用方把「卡在哪一格」写进 op 日志。
 *
 * 找不到目标 cell（源码标记）/ 认不出内核状态且无输出变化 ⇒ 仍按 fail closed 处理，但会留读数。
 */
import type { CellRef } from '../sites/types'

/** `battle.offline.ipynb` 训练入口 cell 的第一行（改参数后点 Run）。 */
export const OFFLINE_CELL_MARKER = '# @title Battle 离线训练'

export interface OfflineSnapshot {
  cells: CellRef[]
  sourceText: (cell: CellRef) => string
  /** cell 正在执行或排队（站点判据）。 */
  executing: (cell: CellRef) => boolean
  outputText: (cell: CellRef) => string
  /** `idle | busy | connecting | unknown`。 */
  kernelStatus: string
}

/** 哪条证据让「开始」成立（写进操作日志，事后能对账）。 */
export type OfflineEvidence = 'output' | 'busy' | 'held'

export interface OfflineStart {
  cell: CellRef
  waitedMs: number
  evidence: OfflineEvidence
}

/** 探测自身的读数（写进 op detail）：没有它，「没上报」就分不清是没跑还是判据卡住。 */
export interface OfflineWatchDiag {
  targetId: string | null
  phase: OfflinePhase
  /** 连续「执行中」的拍数（回到 idle 清零）。 */
  execPolls: number
  kernel: string
  /** 目标 cell 当前输出字符数（`outputText` 读不到时恒 0，正是要看见的那种坏法）。 */
  outLen: number
  /** 除目标外还有几个 cell 在执行（排队计数）。 */
  others: number
  waitedMs: number
}

export type OfflinePhase = 'idle' | 'pending' | 'reported'

export interface OfflineWatch {
  /** 每拍调用；返回一次性的「开始执行」事件。 */
  poll(): OfflineStart | null
  /** 已定位到目标 cell（宿主据此显示「离线训练」选项）。 */
  detected(): CellRef | null
  /** 探测读数（诊断用，只读）。 */
  diag(): OfflineWatchDiag
}

export interface OfflineWatchDeps {
  snapshot: () => OfflineSnapshot
  now?: () => number
  /** 证据要持续多久才算数（默认 1000ms）。 */
  holdMs?: number
  /** 看门狗：连续执行这么久（且拍数够）就认，默认 3000ms。 */
  heldMs?: number
  /** 看门狗要求的最少连续执行拍数（默认 3）。 */
  heldPolls?: number
  /** 目标连续几拍不在 `cells()` 里就丢掉缓存重新识别（默认 3；虚拟化滚出 DOM 不会这么久）。 */
  missingLimit?: number
  /** 每拍最多扫几段源码找标记（默认 12；标记在第 2 段）。 */
  scanLimit?: number
}

export function createOfflineWatch(deps: OfflineWatchDeps): OfflineWatch {
  const now = deps.now ?? (() => Date.now())
  const holdMs = Math.max(0, deps.holdMs ?? 1000)
  const heldMs = Math.max(holdMs, deps.heldMs ?? 3000)
  const heldPolls = Math.max(1, deps.heldPolls ?? 3)
  const missingLimit = Math.max(1, deps.missingLimit ?? 3)
  const scanLimit = Math.max(1, deps.scanLimit ?? 12)
  let target: CellRef | null = null
  let phase: OfflinePhase = 'idle'
  let pendingAt = 0
  /** 上一拍的输出（不是「起点」：起点在执行开始之后才采，会把前几秒的输出吃掉）。 */
  let prevOutput = ''
  let execPolls = 0
  let missingPolls = 0
  let last: OfflineWatchDiag = {
    targetId: null,
    phase: 'idle',
    execPolls: 0,
    kernel: 'unknown',
    outLen: 0,
    others: 0,
    waitedMs: 0,
  }

  const same = (a: CellRef, b: CellRef): boolean => a.id === b.id

  const detect = (snap: OfflineSnapshot): CellRef | null => {
    if (target) return target
    let scanned = 0
    for (const cell of snap.cells) {
      if (cell.kind !== 'code') continue
      if (scanned >= scanLimit) break
      scanned += 1
      if (snap.sourceText(cell).includes(OFFLINE_CELL_MARKER)) {
        target = cell
        return target
      }
    }
    return null
  }

  const reset = (): void => {
    phase = 'idle'
    pendingAt = 0
    prevOutput = ''
    execPolls = 0
  }

  return {
    detected: () => target,
    diag: () => ({ ...last }),
    poll() {
      const snap = deps.snapshot()
      if (!detect(snap)) return null
      const current = snap.cells.find((c) => same(c, target!)) ?? null
      if (!current) {
        // 虚拟化滚出 DOM（正常，几拍就回来）vs 页面重渲染换了 id（要重认）。攒够拍数再重认，
        // 否则滚一次就把「已经报过」的阶段冲掉，同一次运行会上报两次。
        missingPolls += 1
        if (missingPolls >= missingLimit) {
          target = null
          reset()
          missingPolls = 0
        }
        last = { ...last, targetId: target ? target.id : null, phase, execPolls: 0, waitedMs: 0 }
        return null
      }
      missingPolls = 0
      const exec = snap.executing(current)
      const kernel = (snap.kernelStatus || 'unknown').toLowerCase()
      const output = snap.outputText(current)
      const others = snap.cells.filter(
        (c) => c.kind === 'code' && !same(c, current) && snap.executing(c),
      ).length
      last = {
        targetId: current.id,
        phase,
        execPolls,
        kernel,
        outLen: output.length,
        others,
        waitedMs: phase === 'idle' ? 0 : now() - pendingAt,
      }
      if (!exec) {
        reset()
        last = { ...last, phase: 'idle', execPolls: 0, waitedMs: 0 }
        return null
      }
      if (phase === 'reported') return null
      execPolls += 1
      if (phase === 'idle') {
        phase = 'pending'
        pendingAt = now()
        prevOutput = output
        last = { ...last, phase, execPolls }
        return null
      }
      if (kernel === 'connecting') {
        prevOutput = output
        return null
      }
      const changed = output !== prevOutput
      prevOutput = output
      const waited = now() - pendingAt
      last = { ...last, phase, execPolls, waitedMs: waited }
      // busy + 没有别的 cell 在跑 = 它自己就是当前执行者；排队时前一个 cell 仍在跑。
      const busyAlone = kernel === 'busy' && others === 0
      // 看门狗：它自己是**唯一**执行者、连续 N 拍都在执行且等满 heldMs —— Kaggle 上既没有
      // 状态栏、输出也可能读不到（`outputText` 选择器不在 ⇒ 恒空），只靠前两条就是「永远不报」。
      // `others === 0` 是底线：排队时前一个 cell 还在跑，那不是「开始」。仅上报、不下发命令。
      const held = others === 0 && execPolls >= heldPolls && waited >= heldMs
      if (!changed && !busyAlone && !held) return null
      if (waited < holdMs) return null
      phase = 'reported'
      last = { ...last, phase }
      return {
        cell: current,
        waitedMs: waited,
        evidence: changed ? 'output' : busyAlone ? 'busy' : 'held',
      }
    },
  }
}
