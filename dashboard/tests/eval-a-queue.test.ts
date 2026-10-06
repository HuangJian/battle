/**
 * eval-a-queue.test.ts — evalA **串行队列**（2026-10-06 用户报障）。
 *
 *  报障：「evalA 在某 it 执行过后，其它 it 再点 evalA 按键无反应，需要刷新页面后才能点」。
 *  根因：单槽互斥把后到的点击一律 409 拒掉（`evalA 已在运行`），一单几十秒–几分钟里
 *  连点其它 it 全部落空。期望（用户口径）：**连点多个 it 的 evalA ⇒ 依次执行（串行）**。
 *
 *  分层：`src/server/eval-a-run.ts`——`createEvalAQueue`（调度核心，可注入 spawner）+
 *  `launchEvalA`（唯一启动点，生产 wiring）。一律不真起 python：替身里手动调 `done()`
 *  表示「子进程退出」，`queue.state()` 读快照；互斥键契约（补评/导入侧看「忙」）同文件断言。
 */

import { afterEach, beforeEach, describe, expect, it } from 'bun:test'
import { busy } from '../src/server/actions'
import {
  EVAL_A_BUSY_KEY,
  EVAL_A_QUEUE_CAP,
  createEvalAQueue,
  type EvalAJob,
} from '../src/server/eval-a-run'

/** 一单（缺省非 baseline：每单由 ckpt + iter 唯一确定）。 */
function job(iter: number, course = 'c4-dodge'): EvalAJob {
  return { course, ckpt: `nn-training/weights/${course}.it${iter}.json`, iter, opts: {} }
}

/** 替身 spawner：记录起过谁、留出「子进程退出」的把手。 */
function makeQueue(): {
  queue: ReturnType<typeof createEvalAQueue>
  started: EvalAJob[]
  exit: Array<() => void>
} {
  const started: EvalAJob[] = []
  const exit: Array<() => void> = []
  const queue = createEvalAQueue((j, done) => {
    started.push(j)
    exit.push(done)
    return { pid: 1000 + started.length }
  })
  return { queue, started, exit }
}

beforeEach(() => {
  busy.delete(EVAL_A_BUSY_KEY)
})
afterEach(() => {
  busy.delete(EVAL_A_BUSY_KEY)
})

describe('evalA 串行队列（createEvalAQueue）', () => {
  it('空闲 ⇒ 立刻启动（不进队列）；互斥键在第一单起跑时置位', () => {
    const { queue, started } = makeQueue()
    const r = queue.enqueue(job(11))
    expect(r.ok).toBe(true)
    expect(r.queued).toBeUndefined()
    expect(typeof r.pid).toBe('number')
    expect(started.map((j) => j.iter)).toEqual([11])
    expect(busy.has(EVAL_A_BUSY_KEY)).toBe(true)
    expect(queue.state().active?.iter).toBe(11)
    expect(queue.state().pending).toEqual([])
  })

  it('★ 连点多个 it：后到的排队，前一单退出后自动接棒；排空才释放互斥键', () => {
    const { queue, started, exit } = makeQueue()
    expect(queue.enqueue(job(11)).ok).toBe(true)
    const r2 = queue.enqueue(job(12))
    const r3 = queue.enqueue(job(13))
    expect([r2.ok, r2.queued, r3.ok, r3.queued]).toEqual([true, true, true, true])
    expect(r2.message).toContain('已排队')
    expect(r3.message).toContain('前面 2 个')
    // 同一时刻只跑一单：it12/it13 只在队里等
    expect(started.map((j) => j.iter)).toEqual([11])
    expect(queue.state().pending.map((j) => j.iter)).toEqual([12, 13])

    exit[0]() // it11 子进程退出
    expect(started.map((j) => j.iter)).toEqual([11, 12])
    expect(queue.state().active?.iter).toBe(12)
    // 队列未空 ⇒ 键仍占（补评/导入侧照旧看到「忙」，不会并发插单）
    expect(busy.has(EVAL_A_BUSY_KEY)).toBe(true)

    exit[1]()
    expect(started.map((j) => j.iter)).toEqual([11, 12, 13])
    exit[2]()
    expect(queue.state().active).toBe(null)
    expect(queue.state().pending).toEqual([])
    // ★ 排空 ⇒ 释放：不再需要刷新页面，下一轮点击立即可起
    expect(busy.has(EVAL_A_BUSY_KEY)).toBe(false)
  })

  it('去重：同一 (课, iter) 在跑/在队 ⇒ 不重复启动、不重复入队（ckpt 路径不同算同一单）', () => {
    const { queue, started, exit } = makeQueue()
    queue.enqueue(job(11))
    const dupActive = queue.enqueue(job(11))
    expect(dupActive.ok).toBe(true)
    expect(dupActive.queued).toBe(true)
    expect(dupActive.message).toContain('已在运行')

    queue.enqueue(job(12))
    const dupPending = queue.enqueue({ ...job(12), ckpt: '别的/权重路径.json' })
    expect(dupPending.ok).toBe(true)
    expect(dupPending.message).toContain('已在队列')

    expect(started.map((j) => j.iter)).toEqual([11])
    expect(queue.state().pending.map((j) => j.iter)).toEqual([12])
    exit[0]()
    exit[1]()
  })

  it('不同课的同号 iter 不是同一单（按课程键控）', () => {
    const { queue, started, exit } = makeQueue()
    queue.enqueue(job(11, 'c4-dodge'))
    const other = queue.enqueue(job(11, 'x21-psh-b'))
    expect(other.ok).toBe(true)
    expect(other.queued).toBe(true)
    expect(queue.state().pending.map((j) => j.course)).toEqual(['x21-psh-b'])
    expect(started.map((j) => j.iter)).toEqual([11])
    exit[0]()
    exit[1]()
  })

  it(`队列满（上限 ${EVAL_A_QUEUE_CAP}）⇒ 响亮拒（code=full）且不挤掉已有单`, () => {
    const { queue, started } = makeQueue()
    queue.enqueue(job(11))
    for (let i = 0; i < EVAL_A_QUEUE_CAP; i++) {
      expect(queue.enqueue(job(100 + i)).ok).toBe(true)
    }
    const r = queue.enqueue(job(999))
    expect(r.ok).toBe(false)
    expect(r.code).toBe('full')
    expect(r.message).toContain('队列已满')
    expect(queue.state().pending.length).toBe(EVAL_A_QUEUE_CAP)
    expect(started.map((j) => j.iter)).toEqual([11])
  })

  it("子进程 'error' 与 'exit' 双触发只放行一单（迟到事件不得多起一单）", () => {
    const { queue, started, exit } = makeQueue()
    queue.enqueue(job(11))
    queue.enqueue(job(12))
    exit[0]()
    exit[0]() // 第二次（error/exit 都可能到）——不得把 it12 顶掉再起一单
    expect(started.map((j) => j.iter)).toEqual([11, 12])
    exit[1]()
  })

  it('启动失败（spawn 抛）⇒ ok:false 且队列继续消化下一单', () => {
    const started: EvalAJob[] = []
    const exit: Array<() => void> = []
    const queue = createEvalAQueue((j, done) => {
      if (j.iter === 12) throw new Error('python 起不来')
      started.push(j)
      exit.push(done)
      return { pid: started.length }
    })
    queue.enqueue(job(11))
    queue.enqueue(job(12))
    queue.enqueue(job(13))
    exit[0]() // it11 退出 ⇒ 起 it12 ⇒ 抛 ⇒ it13 自动接棒（不卡死队列）
    expect(started.map((j) => j.iter)).toEqual([11, 13])
    expect(busy.has(EVAL_A_BUSY_KEY)).toBe(true)
    exit[1]()
    expect(busy.has(EVAL_A_BUSY_KEY)).toBe(false)
  })

  it('单槽被外部持有（不是本队列在跑）⇒ 拒（队列不接管不知道何时释放的占用）', () => {
    busy.add(EVAL_A_BUSY_KEY)
    const { queue, started } = makeQueue()
    const r = queue.enqueue(job(11))
    expect(r.ok).toBe(false)
    expect(r.code).toBe('busy')
    expect(r.message).toContain('已在运行')
    expect(started).toEqual([])
  })
})
