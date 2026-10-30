import { describe, expect, it } from 'bun:test'
import {
  createScheduler,
  OCCURRENCES_KEY,
  TASKS_KEY,
  type Occurrence,
  type SchedulerDeps,
  type SchedulerStore,
  type SchedulerTask,
} from '../src/notebook-companion/app/scheduler'
import type { LocalParts } from '../src/notebook-companion/schedule'

const pad = (n: number): string => String(n).padStart(2, '0')

/** 测试口径 = UTC 墙钟（parts 注入 ⇒ 与时区无关）。 */
function parts(ms: number): LocalParts {
  const d = new Date(ms)
  return {
    date: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
    time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`,
    weekday: d.getUTCDay(),
  }
}

/** 2026-10-30 = 周五。 */
const at = (h: number, m = 0): number => Date.UTC(2026, 9, 30, h, m)
const OCC_KEY = '2026-10-30@09:00'

function memoryStore(): { store: SchedulerStore; data: Map<string, unknown> } {
  const data = new Map<string, unknown>()
  const store: SchedulerStore = {
    getValue: <T>(key: string, fallback: T): T => {
      if (!data.has(key)) return fallback
      return JSON.parse(JSON.stringify(data.get(key))) as T
    },
    setValue: (key, value) => {
      data.set(key, JSON.parse(JSON.stringify(value)))
    },
  }
  return { store, data }
}

interface Harness {
  sched: ReturnType<typeof createScheduler>
  mem: Map<string, unknown>
  runs: SchedulerTask[]
  changes: number
  setNow: (ms: number) => void
  setIdentity: (id: { notebookKey: string; session: string }) => void
  setAfterWait: (fn: (() => void) | null) => void
}

function harness(over: Partial<SchedulerDeps> = {}): Harness {
  const { store, data } = memoryStore()
  let now = at(8)
  let identity = { notebookKey: 'owner/slug', session: 'kaggle-owner-slug-edit-ab12' }
  let afterWait: (() => void) | null = null
  const runs: SchedulerTask[] = []
  const state = { changes: 0 }
  let seq = 0
  const deps: SchedulerDeps = {
    store,
    writer: 'w1',
    now: () => now,
    parts,
    claimSettleMs: 0,
    runGraceMs: 10 * 60_000,
    stopGraceMs: 10 * 60_000,
    identity: () => identity,
    confirm: async () => true,
    run: async (t) => {
      runs.push(t)
      return { ok: true }
    },
    wait: async () => {
      afterWait?.()
    },
    onChange: () => {
      state.changes += 1
    },
    makeId: () => `tid${++seq}`,
    ...over,
  }
  const sched = createScheduler(deps)
  return {
    sched,
    mem: data,
    runs,
    get changes() {
      return state.changes
    },
    setNow: (ms) => {
      now = ms
    },
    setIdentity: (id) => {
      identity = id
    },
    setAfterWait: (fn) => {
      afterWait = fn
    },
  }
}

function occurrences(h: Harness): Record<string, Occurrence> {
  return (h.mem.get(OCCURRENCES_KEY) ?? {}) as Record<string, Occurrence>
}

describe('定时任务：只设「下一次」（一次性）', () => {
  it('add：HH:MM → 今天/明天的 nextAt，落盘；rows 给人话', () => {
    const h = harness()
    const today = h.sched.add({ at: '9:05', action: 'run-all' })
    expect(today.ok).toBe(true)
    if (today.ok) expect(today.task.nextAt).toBe(at(9, 5))
    const tomorrow = h.sched.add({ at: '07:00', action: 'run-all' })
    if (tomorrow.ok) expect(tomorrow.task.nextAt).toBe(at(7) + 86_400_000)

    const row = h.sched.rows()[0]!
    expect(row.text).toBe('09:05 · 运行全部')
    expect(row.next).toBe('今天 09:05')
    expect(h.sched.rows()[1]!.next).toBe('明天 07:00')
    expect((h.mem.get(TASKS_KEY) as SchedulerTask[]).length).toBe(2)
  })

  it('nextDue：最早的可启用任务；停用/空表 ⇒ null（提示区倒计时用）', () => {
    const h = harness()
    expect(h.sched.nextDue()).toBeNull()
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.sched.add({ at: '08:30', action: 'stop-session' })
    expect(h.sched.nextDue()?.action).toBe('stop-session')
    // 停用最早的那条 ⇒ 顺延到下一个
    const early = h.sched.tasks().find((t) => t.action === 'stop-session')!
    h.sched.setEnabled(early.id, false)
    expect(h.sched.nextDue()?.action).toBe('run-all')
  })

  it('add：非法时间 / 动作 / run-cell 缺目标 / 没连上 ⇒ 都不落盘', () => {
    const h = harness()
    expect(h.sched.add({ at: '25:00', action: 'run-all' }).ok).toBe(false)
    expect(h.sched.add({ at: '09:00', action: 'nope' as never }).ok).toBe(false)
    expect(h.sched.add({ at: '09:00', action: 'run-cell' }).ok).toBe(false)
    h.setIdentity({ notebookKey: '', session: '' })
    expect(h.sched.add({ at: '09:00', action: 'run-all' }).ok).toBe(false)
    expect(h.mem.get(TASKS_KEY)).toBeUndefined()
  })

  it('remove / setEnabled 落盘；remove 清掉该任务的 occurrence（历史不留幽灵）', () => {
    const h = harness()
    expect(h.sched.add({ at: '09:00', action: 'run-all' }).ok).toBe(true)
    h.sched.setEnabled('tid1', false)
    expect((h.mem.get(TASKS_KEY) as SchedulerTask[])[0]!.enabled).toBe(false)
    h.sched.setEnabled('tid1', true)
    expect((h.mem.get(TASKS_KEY) as SchedulerTask[])[0]!.enabled).toBe(true)

    h.mem.set(OCCURRENCES_KEY, {
      'tid1|2026-10-30@09:00': { state: 'succeeded', by: 'w1', at: at(9) },
      'keep|2026-10-30@09:00': { state: 'succeeded', by: 'w1', at: at(9) },
    })
    h.sched.remove('tid1')
    expect(h.mem.get(TASKS_KEY)).toEqual([])
    expect(Object.keys(occurrences(h))).toEqual(['keep|2026-10-30@09:00'])
  })
})

describe('定时任务：到点执行一次即消耗（claim + 迟到窗口）', () => {
  it('tick：到点前不跑；到点跑一次后任务被移除、occurrence 留档', async () => {
    const h = harness()
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) - 60_000)
    await h.sched.tick()
    expect(h.runs).toHaveLength(0)

    const before = h.changes
    h.setNow(at(9, 0) + 20_000)
    await h.sched.tick()
    expect(h.runs.map((t) => t.action)).toEqual(['run-all'])
    expect(occurrences(h)[`tid1|${OCC_KEY}`]?.state).toBe('succeeded')
    expect(h.mem.get(TASKS_KEY)).toEqual([]) // 一次性：执行后就不再有待办
    expect(h.changes).toBeGreaterThan(before)

    h.setNow(at(9, 0) + 40_000)
    await h.sched.tick()
    expect(h.runs).toHaveLength(1)
  })

  it('迟到窗口：run-* 10 分钟内照跑；超过 ⇒ skipped(过期) 且任务移除，不补跑', async () => {
    const late = harness()
    late.sched.add({ at: '09:00', action: 'run-all' })
    late.setNow(at(9, 9))
    await late.sched.tick()
    expect(late.runs).toHaveLength(1)

    const tooLate = harness()
    tooLate.sched.add({ at: '09:00', action: 'run-all' })
    tooLate.setNow(at(9, 11))
    await tooLate.sched.tick()
    expect(tooLate.runs).toHaveLength(0)
    const occ = occurrences(tooLate)[`tid1|${OCC_KEY}`]!
    expect(occ.state).toBe('skipped')
    expect(occ.reason).toContain('过期')
    expect(tooLate.mem.get(TASKS_KEY)).toEqual([])
  })

  it('stop/interrupt 用 stopGraceMs（20 分钟窗口内照停）', async () => {
    const stop = harness({ stopGraceMs: 20 * 60_000 })
    stop.sched.add({ at: '09:00', action: 'stop-session' })
    stop.setNow(at(9, 15))
    await stop.sched.tick()
    expect(stop.runs.map((t) => t.action)).toEqual(['stop-session'])
  })

  it('两个页签争同一个 occurrence：沉淀窗里被抢走 ⇒ 本页退让、不执行', async () => {
    const h = harness()
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    h.setAfterWait(() => {
      const occ = occurrences(h)
      for (const key of Object.keys(occ)) occ[key]!.by = 'other-tab'
      h.mem.set(OCCURRENCES_KEY, JSON.parse(JSON.stringify(occ)))
    })
    await h.sched.tick()
    expect(h.runs).toHaveLength(0)
  })

  it('取消 ⇒ skipped(用户取消)，任务移除、不再补跑', async () => {
    const h = harness({ confirm: async () => false })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    expect(h.runs).toHaveLength(0)
    const occ = occurrences(h)[`tid1|${OCC_KEY}`]!
    expect(occ.state).toBe('skipped')
    expect(occ.reason).toBe('用户取消')
    expect(h.mem.get(TASKS_KEY)).toEqual([])
  })

  it('执行失败写 failed + 原因；任务照样移除（不自动重试）', async () => {
    const h = harness({ run: async () => ({ ok: false, reason: '笔记本未连接' }) })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    const occ = occurrences(h)[`tid1|${OCC_KEY}`]!
    expect(occ.state).toBe('failed')
    expect(occ.reason).toBe('笔记本未连接')
    expect(h.mem.get(TASKS_KEY)).toEqual([])
  })

  it('换 notebook / 换会话 ⇒ fail closed（skipped，不执行）', async () => {
    const h = harness()
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.sched.add({ at: '09:00', action: 'stop-session' })
    h.setNow(at(9, 0) + 5_000)
    h.setIdentity({ notebookKey: 'other/notebook', session: 'kaggle-other-edit-zz99' })
    await h.sched.tick()
    expect(h.runs).toHaveLength(0)
    const occ = occurrences(h)
    expect(occ[`tid1|${OCC_KEY}`]?.state).toBe('skipped')
    expect(occ[`tid1|${OCC_KEY}`]?.reason).toContain('notebook')
    expect(occ[`tid2|${OCC_KEY}`]?.state).toBe('skipped')
    expect(occ[`tid2|${OCC_KEY}`]?.reason).toContain('会话已换')
    expect(h.mem.get(TASKS_KEY)).toEqual([])
  })

  it('occurrence 记录只留 7 天（写盘时清理）', async () => {
    const h = harness()
    h.mem.set(OCCURRENCES_KEY, {
      'old|2026-10-01@09:00': { state: 'succeeded', by: 'w0', at: at(9) - 8 * 86_400_000 },
    })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    const keys = Object.keys(occurrences(h))
    expect(keys.some((k) => k.startsWith('old|'))).toBe(false)
    expect(keys).toContain(`tid1|${OCC_KEY}`)
  })
})
