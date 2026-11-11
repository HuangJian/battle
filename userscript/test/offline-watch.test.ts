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
      // 按**段号**认标记（不是 id）：重渲染换 id 的用例靠这个还能重新识别。
      sourceText: (cell) =>
        withMarker && cell.index === TRAIN.index
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

  it('★ Kaggle 形态：认得执行、但内核状态与输出都读不到 ⇒ 看门狗仍上报（2026-11-11 现场）', () => {
    // 现场：`offline:detected` 在、`offline:cell-start` 两天 0 条。Kaggle 没有 Jupyter 状态栏
    // （kernel 恒 unknown），输出选择器也不一定命中（outLen 恒 0）——只靠前两条证据永远不响。
    const h = harness()
    h.output(TRAIN.id, '')
    h.run(TRAIN.id)
    h.kernel('unknown')
    expect(h.watch.poll()).toBeNull() // 进 pending
    h.advance(1000)
    expect(h.watch.poll()).toBeNull() // 第 2 拍：拍数还不够
    h.advance(1000)
    expect(h.watch.poll()).toBeNull() // 第 3 拍：拍数够，但还没等满 heldMs(3000)
    h.advance(1000)
    expect(h.watch.poll()?.evidence).toBe('held')
  })

  it('输出「相对上一拍」变化即算（不要求从起点累计变）', () => {
    // 旧版基线在**已经开始执行之后**才采：前几秒没输出就永远错过。新版只比相邻两拍。
    const h = harness()
    h.output(TRAIN.id, '')
    h.run(TRAIN.id)
    h.kernel('unknown')
    expect(h.watch.poll()).toBeNull() // pending，基线 = ''
    h.output(TRAIN.id, '[offline] course=o2-notax\n')
    h.advance(1200)
    expect(h.watch.poll()?.evidence).toBe('output')
  })

  it('排队（别的 cell 一直在跑、无输出、无内核读数）⇒ 看门狗也不报', () => {
    const h = harness()
    h.output(TRAIN.id, '')
    h.run(WARM.id, TRAIN.id)
    h.kernel('unknown')
    expect(h.watch.poll()).toBeNull()
    for (let i = 0; i < 6; i += 1) {
      h.advance(1000)
      expect(h.watch.poll()).toBeNull()
    }
  })

  it('diag() 把「卡在哪一格」变成读数', () => {
    const h = harness()
    h.output(TRAIN.id, '')
    h.run(WARM.id, TRAIN.id)
    h.kernel('unknown')
    expect(h.watch.poll()).toBeNull()
    h.advance(500)
    expect(h.watch.poll()).toBeNull()
    const d = h.watch.diag()
    expect(d).toMatchObject({
      targetId: TRAIN.id,
      phase: 'pending',
      kernel: 'unknown',
      outLen: 0,
      others: 1,
    })
    expect(d.execPolls).toBeGreaterThanOrEqual(1)
  })

  it('目标 cell 连续 3 拍不在 cells() 里 ⇒ 丢掉缓存重新识别（页面重渲染换了 id 也不会永久哑）', () => {
    const h = harness()
    h.run(TRAIN.id)
    h.kernel('busy')
    expect(h.watch.poll()).toBeNull()
    const renamed: CellRef = { id: 'train-v2', index: 1, kind: 'code' }
    h.setCells([WARM, renamed])
    for (let i = 0; i < 3; i += 1) expect(h.watch.poll()).toBeNull()
    expect(h.watch.diag().targetId).toBeNull()
    h.run(renamed.id)
    expect(h.watch.poll()).toBeNull() // 重认后的第一拍：重新建基线
    h.advance(1500)
    expect(h.watch.poll()?.cell.id).toBe(renamed.id)
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
