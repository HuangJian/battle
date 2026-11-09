/**
 * offline-watch.test.ts — 离线训练 cell「排队结束 → 真正开始」的判定契约。
 *
 * 关键不变式：排队（前面还有 cell 在跑）不算开始；输出变化或「内核 busy 且没有别的
 * cell 在跑」持续 holdMs 才上报一次；cell 回到 idle 后下一轮重新计。
 */
import { describe, expect, it } from 'bun:test'
import {
  createOfflineWatch,
  OFFLINE_CELL_MARKER,
  type OfflineSnapshot,
} from '../src/notebook-companion/app/offline-watch'
import type { CellRef } from '../src/notebook-companion/sites/types'

const WARM: CellRef = { id: 'warm', index: 0, kind: 'code' }
const TRAIN: CellRef = { id: 'train', index: 1, kind: 'code' }

function harness(withMarker = true) {
  let cells: CellRef[] = [WARM, TRAIN]
  const running = new Set<string>()
  const outputs = new Map<string, string>([[TRAIN.id, 'old']])
  let kernel = 'idle'
  let clock = 0
  const watch = createOfflineWatch({
    now: () => clock,
    holdMs: 1000,
    snapshot: (): OfflineSnapshot => ({
      cells,
      sourceText: (cell) =>
        withMarker && cell.id === TRAIN.id
          ? `${OFFLINE_CELL_MARKER} —— 改参数后点 Run`
          : '# warmup',
      executing: (cell) => running.has(cell.id),
      outputText: (cell) => outputs.get(cell.id) ?? '',
      kernelStatus: kernel,
    }),
  })
  return {
    watch,
    setCells(next: CellRef[]) {
      cells = next
    },
    run(...ids: string[]) {
      running.clear()
      for (const id of ids) running.add(id)
    },
    idle() {
      running.clear()
    },
    output(id: string, text: string) {
      outputs.set(id, text)
    },
    kernel(next: string) {
      kernel = next
    },
    advance(ms: number) {
      clock += ms
    },
  }
}

describe('离线训练 cell：只认真正开始执行', () => {
  it('不是 battle.offline 页面 ⇒ 不检测、不上报', () => {
    const h = harness(false)
    expect(h.watch.poll()).toBeNull()
    expect(h.watch.detected()).toBeNull()
  })

  it('定位标记 cell；第一拍只建基线，不把「已经跑了一半」当开始', () => {
    const h = harness()
    h.run(TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull()
    expect(h.watch.detected()?.id).toBe(TRAIN.id)
    h.advance(2000)
    expect(h.watch.poll()?.cell.id).toBe(TRAIN.id)
  })

  it('排队时前一个 cell 还在跑 ⇒ 不上报；真正拿到内核才上报一次', () => {
    const h = harness()
    h.run(WARM.id, TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull() // 进 pending
    h.advance(2000)
    expect(h.watch.poll()).toBeNull() // 别的 cell 还在跑，且输出没变
    h.run(TRAIN.id) // 前一个 cell 结束，目标成为唯一执行者
    expect(h.watch.poll()?.evidence).toBe('busy')
    expect(h.watch.poll()).toBeNull() // 只报一次
  })

  it('输出变化（Jupyter 开始执行会先清空输出区）⇒ 即使还有排队 cell 也认', () => {
    const h = harness()
    h.run(WARM.id, TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull()
    h.output(TRAIN.id, '')
    h.advance(1500)
    expect(h.watch.poll()?.evidence).toBe('output')
  })

  it('内核 connecting 期间不认；连上后继续', () => {
    const h = harness()
    h.run(TRAIN.id)
    h.kernel('connecting')
    expect(h.watch.poll()).toBeNull()
    h.advance(5000)
    expect(h.watch.poll()).toBeNull()
    h.kernel('busy')
    expect(h.watch.poll()?.evidence).toBe('busy')
  })

  it('cell 回到 idle 后再次执行 ⇒ 第二次上报', () => {
    const h = harness()
    h.run(TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull()
    h.advance(1500)
    expect(h.watch.poll()).not.toBeNull()
    h.idle()
    expect(h.watch.poll()).toBeNull()
    h.run(TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull() // 新一轮 pending
    h.advance(1500)
    expect(h.watch.poll()?.cell.id).toBe(TRAIN.id)
  })

  it('目标 cell 被虚拟化滚出 DOM ⇒ 不判、不重置阶段', () => {
    const h = harness()
    h.run(TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull()
    h.advance(1500)
    h.setCells([WARM]) // 目标不在 cells() 里
    expect(h.watch.poll()).toBeNull()
    h.setCells([WARM, TRAIN])
    expect(h.watch.poll()?.cell.id).toBe(TRAIN.id)
  })
})
