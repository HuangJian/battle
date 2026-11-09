/**
 * offline-watch.ts — 离线训练页（`battle.offline.ipynb`）的「训练 cell 真正开始执行」检测。
 *
 * 队列里的 cell 与真正执行的 cell 在 JupyterLab 上可能共用 `jp-mod-running` / `[*]` 外观
 * （`sites/kaggle.ts` 的既有判据只能回答「在执行或排队」），所以这里不靠单点状态变化：
 *   - 目标 cell 有输出变化（Jupyter 开始执行会先清空/刷新输出区）；或
 *   - 内核 `busy` 且没有别的 cell 在跑（排队时前一个 cell 还在跑）。
 * 证据持续 `holdMs` 才上报一次；cell 回到 idle 后下一轮重新计。只读：不改 DOM、不发命令。
 *
 * 找不到目标 cell（源码标记）/ 认不出内核状态且无输出变化 ⇒ 不报（fail closed）。
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

export interface OfflineStart {
  cell: CellRef
  waitedMs: number
  /** 哪条证据让它成立（写进操作日志，事后能对账）。 */
  evidence: 'output' | 'busy'
}

export interface OfflineWatch {
  /** 每拍调用；返回一次性的「开始执行」事件。 */
  poll(): OfflineStart | null
  /** 已定位到目标 cell（宿主据此显示「离线训练」选项）。 */
  detected(): CellRef | null
}

export interface OfflineWatchDeps {
  snapshot: () => OfflineSnapshot
  now?: () => number
  /** 证据要持续多久才算数（默认 1000ms）。 */
  holdMs?: number
  /** 每拍最多扫几段源码找标记（默认 12；标记在第 2 段）。 */
  scanLimit?: number
}

type Phase = 'idle' | 'pending' | 'reported'

export function createOfflineWatch(deps: OfflineWatchDeps): OfflineWatch {
  const now = deps.now ?? (() => Date.now())
  const holdMs = Math.max(0, deps.holdMs ?? 1000)
  const scanLimit = Math.max(1, deps.scanLimit ?? 12)
  let target: CellRef | null = null
  let phase: Phase = 'idle'
  let pendingAt = 0
  let baseline = ''

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

  return {
    detected: () => target,
    poll() {
      const snap = deps.snapshot()
      if (!detect(snap)) return null
      const current = snap.cells.find((c) => same(c, target!)) ?? null
      // 虚拟化把目标滚出 DOM：读不到状态 ⇒ 这拍不判（保留阶段，别把一次运行重置掉）
      if (!current) return null
      const exec = snap.executing(current)
      const kernel = (snap.kernelStatus || 'unknown').toLowerCase()
      if (!exec) {
        phase = 'idle'
        pendingAt = 0
        baseline = ''
        return null
      }
      if (phase === 'reported') return null
      const output = snap.outputText(current)
      if (phase === 'idle') {
        phase = 'pending'
        pendingAt = now()
        baseline = output
        return null
      }
      if (kernel === 'connecting') return null
      const others = snap.cells.some(
        (c) => c.kind === 'code' && !same(c, current) && snap.executing(c),
      )
      const changed = output !== baseline
      // busy + 没有别的 cell 在跑 = 它自己就是当前执行者；排队时前一个 cell 仍在跑。
      const busyAlone = kernel === 'busy' && !others
      if (!changed && !busyAlone) return null
      const waited = now() - pendingAt
      if (waited < holdMs) return null
      phase = 'reported'
      return { cell: current, waitedMs: waited, evidence: changed ? 'output' : 'busy' }
    },
  }
}
