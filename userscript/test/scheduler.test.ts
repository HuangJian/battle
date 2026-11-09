import { describe, expect, it } from 'bun:test'
import {
  countdownLabel,
  createScheduler,
  historyText,
  OCCURRENCES_KEY,
  scheduleStatusText,
  taskBelongsTo,
  TASKS_KEY,
  tasksKeyFor,
  type Occurrence,
  type SchedulerDeps,
  type SchedulerEvent,
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

function memoryStore(seed?: Map<string, unknown>): {
  store: SchedulerStore
  data: Map<string, unknown>
} {
  const data = seed ?? new Map<string, unknown>()
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
  events: SchedulerEvent[]
  /** 本页任务表键（与调度器同口径）。 */
  bucket: () => string
  setNow: (ms: number) => void
  setIdentity: (id: { notebookKey?: string; session?: string; page?: string }) => void
  setAfterWait: (fn: (() => void) | null) => void
}

/** `shared` = 同一个 GM 存储（模拟同浏览器里另一个页签）。 */
function harness(over: Partial<SchedulerDeps> = {}, shared?: Map<string, unknown>): Harness {
  const { store, data } = memoryStore(shared)
  let now = at(8)
  let identity = { notebookKey: 'owner/slug', session: 'kaggle-owner-slug-edit-ab12', page: 'p1' }
  let afterWait: (() => void) | null = null
  const runs: SchedulerTask[] = []
  const events: SchedulerEvent[] = []
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
    onEvent: (e) => events.push(e),
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
    events,
    bucket: () => tasksKeyFor(identity.notebookKey, identity.page),
    get changes() {
      return state.changes
    },
    setNow: (ms) => {
      now = ms
    },
    setIdentity: (id) => {
      identity = { ...identity, ...id }
    },
    setAfterWait: (fn) => {
      afterWait = fn
    },
  }
}

function occurrences(h: Harness): Record<string, Occurrence> {
  return (h.mem.get(OCCURRENCES_KEY) ?? {}) as Record<string, Occurrence>
}

describe('定时状态栏文案（删空后不留「已添加」旧话）', () => {
  it('0 个 ⇒ 没有定时任务；其余给计数', () => {
    expect(scheduleStatusText(0)).toBe('没有定时任务')
    expect(scheduleStatusText(1)).toBe('定时任务 1 个')
    expect(scheduleStatusText(3)).toBe('定时任务 3 个')
  })
})

describe('cell 徽标倒计时文案', () => {
  it('>10 分钟用静态文案；进入窗口变成 M:SS；到点/过期说即将运行', () => {
    expect(countdownLabel(at(9), at(8, 40), '今天 09:00')).toBe('今天 09:00')
    expect(countdownLabel(at(9), at(8, 57), '今天 09:00')).toBe('还有 3:00')
    expect(countdownLabel(at(9), at(9), '今天 09:00')).toBe('即将运行')
    expect(countdownLabel(at(9), at(9, 1), '今天 09:00')).toBe('即将运行')
  })
})

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
    expect((h.mem.get(h.bucket()) as SchedulerTask[]).length).toBe(2)
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

  it('taskBelongsTo：运行类看 notebook；停止/中断看本页会话（另一页签不显示徽标）', () => {
    const ident = { notebookKey: 'a/b', session: 'kaggle-a-b-edit-tab1' }
    const base = { notebookKey: 'a/b', sessionAtCreate: ident.session }
    expect(taskBelongsTo({ ...base, action: 'run-all' }, ident)).toBe(true)
    expect(taskBelongsTo({ ...base, action: 'run-cell', notebookKey: 'other/b' }, ident)).toBe(
      false,
    )
    // 同一 notebook 的另一页签：stop/interrupt 的会话不同 ⇒ 不属于本页
    expect(
      taskBelongsTo(
        { ...base, action: 'stop-session', sessionAtCreate: 'kaggle-a-b-edit-tab2' },
        ident,
      ),
    ).toBe(false)
    expect(taskBelongsTo({ ...base, action: 'interrupt' }, ident)).toBe(true)
    expect(
      taskBelongsTo({ notebookKey: '', sessionAtCreate: '', action: 'stop-session' }, ident),
    ).toBe(false)
  })

  it('add：非法时间 / 动作 / run-cell 缺目标 / 没连上 ⇒ 都不落盘', () => {
    const h = harness()
    expect(h.sched.add({ at: '25:00', action: 'run-all' }).ok).toBe(false)
    expect(h.sched.add({ at: '09:00', action: 'nope' as never }).ok).toBe(false)
    expect(h.sched.add({ at: '09:00', action: 'run-cell' }).ok).toBe(false)
    h.setIdentity({ notebookKey: '', session: '' })
    expect(h.sched.add({ at: '09:00', action: 'run-all' }).ok).toBe(false)
    expect(h.mem.get(h.bucket())).toBeUndefined()
  })

  it('remove / setEnabled 落盘；remove 清掉该任务的 occurrence（历史不留幽灵）', () => {
    const h = harness()
    expect(h.sched.add({ at: '09:00', action: 'run-all' }).ok).toBe(true)
    h.sched.setEnabled('tid1', false)
    expect((h.mem.get(h.bucket()) as SchedulerTask[])[0]!.enabled).toBe(false)
    h.sched.setEnabled('tid1', true)
    expect((h.mem.get(h.bucket()) as SchedulerTask[])[0]!.enabled).toBe(true)

    h.mem.set(OCCURRENCES_KEY, {
      'tid1|2026-10-30@09:00': { state: 'succeeded', by: 'w1', at: at(9) },
      'keep|2026-10-30@09:00': { state: 'succeeded', by: 'w1', at: at(9) },
    })
    h.sched.remove('tid1')
    expect(h.mem.get(h.bucket())).toEqual([])
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
    expect(h.mem.get(h.bucket())).toEqual([]) // 一次性：执行后就不再有待办
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
    expect(tooLate.mem.get(tooLate.bucket())).toEqual([])
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
    expect(h.mem.get(h.bucket())).toEqual([])
  })

  it('执行失败写 failed + 原因；任务照样移除（不自动重试）', async () => {
    const h = harness({ run: async () => ({ ok: false, reason: '笔记本未连接' }) })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    const occ = occurrences(h)[`tid1|${OCC_KEY}`]!
    expect(occ.state).toBe('failed')
    expect(occ.reason).toBe('笔记本未连接')
    expect(h.mem.get(h.bucket())).toEqual([])
  })

  it('换会话（同一页重新跑过 run）⇒ stop/interrupt fail closed（skipped，不执行）', async () => {
    const h = harness()
    h.sched.add({ at: '09:00', action: 'stop-session' })
    h.setNow(at(9, 0) + 5_000)
    h.setIdentity({ session: 'kaggle-owner-slug-edit-newrun' })
    await h.sched.tick()
    expect(h.runs).toHaveLength(0)
    const occ = occurrences(h)
    expect(occ[`tid1|${OCC_KEY}`]?.state).toBe('skipped')
    expect(occ[`tid1|${OCC_KEY}`]?.reason).toContain('会话已换')
    expect(h.mem.get(h.bucket())).toEqual([])
  })

  it('页面隔离：同一个 notebook 的另一个页签看不到也不消耗（2026-11-02 事故）', async () => {
    const a = harness()
    expect(a.sched.add({ at: '09:00', action: 'run-all' }).ok).toBe(true)
    expect(a.sched.add({ at: '09:00', action: 'stop-session' }).ok).toBe(true)

    // 同一份 GM 存储，但页标签不同（同 notebook 的另一页签）
    const b = harness({ writer: 'w2', makeId: () => 'bid-1' }, a.mem)
    b.setIdentity({ page: 'p2', session: 'kaggle-owner-slug-edit-ab12-p2' })
    expect(b.sched.tasks()).toEqual([])
    expect(b.sched.rows()).toEqual([])
    expect(b.sched.nextDue()).toBeNull()

    b.setNow(at(9, 0) + 5_000)
    await b.sched.tick()
    expect(b.runs).toHaveLength(0)
    expect(occurrences(b)).toEqual({}) // 不写 occurrence
    expect(a.sched.tasks()).toHaveLength(2) // 任务原封不动

    // 创建页自己到点：照常执行一次（跨页签的 claim 协议仍在）
    a.setNow(at(9, 0) + 20_000)
    await a.sched.tick()
    expect(a.runs.map((t) => t.action).sort()).toEqual(['run-all', 'stop-session'])
    expect(a.sched.tasks()).toEqual([])
  })

  it('ownerPage 随任务落盘保留；同桶混入别的页任务也不认领', () => {
    const h = harness()
    h.sched.add({ at: '09:00', action: 'run-all' })
    const stored = h.mem.get(h.bucket()) as SchedulerTask[]
    expect(stored[0]!.ownerPage).toBe('p1')

    h.mem.set(h.bucket(), [...stored, { ...stored[0]!, id: 'other-page', ownerPage: 'p2' }])
    expect(h.sched.tasks().map((t) => t.id)).toEqual(['tid1'])
  })

  it('页面隔离：别的 notebook 的页面更看不到、也吃不掉（旧 bug 的回归钉）', async () => {
    const a = harness()
    a.sched.add({ at: '09:00', action: 'run-all' })
    const other = harness({ writer: 'w9' }, a.mem)
    other.setIdentity({
      notebookKey: 'huangjian2026/rollout-358870479',
      session: 'kaggle-huangjian2026-rollout-358870479-1k1la1',
      page: 'k1la1',
    })
    other.setNow(at(9, 0) + 5_000)
    await other.sched.tick()
    expect(other.runs).toHaveLength(0)
    expect(occurrences(other)).toEqual({})
    // 旧实现会在这里记一条 skipped 并把任务删掉 ⇒ “一个定时任务都没执行，也没痕迹”
    expect(a.sched.tasks()).toHaveLength(1)
    expect(occurrences(a)).toEqual({})
  })

  it('旧全局键迁移：只搬本页建的（notebook + 创建会话都对得上），其余原封不动', () => {
    const seed = new Map<string, unknown>()
    const mine = {
      id: 'legacy-mine',
      nextAt: at(9),
      action: 'run-all' as const,
      notebookKey: 'owner/slug',
      sessionAtCreate: 'kaggle-owner-slug-edit-ab12',
      enabled: true,
      createdAt: at(8),
    }
    const otherTab = {
      ...mine,
      id: 'legacy-other-tab',
      sessionAtCreate: 'kaggle-owner-slug-edit-zz9',
    }
    const otherNb = { ...mine, id: 'legacy-other-nb', notebookKey: 'other/notebook' }
    seed.set(TASKS_KEY, [mine, otherTab, otherNb])

    const h = harness({}, seed)
    expect(h.sched.tasks().map((t) => t.id)).toEqual(['legacy-mine'])
    expect(h.sched.tasks()[0]!.ownerPage).toBeUndefined() // 旧数据没有页标签，靠 notebook+会话认领
    const left = h.mem.get(TASKS_KEY) as Array<{ id: string }>
    expect(left.map((t) => t.id)).toEqual(['legacy-other-tab', 'legacy-other-nb'])
  })

  it('调度事件：add / claim / run / skip 都上报（boot 拿它写操作日志）', async () => {
    const h = harness()
    h.sched.add({ at: '09:00', action: 'run-all' })
    expect(h.events.map((e) => e.kind)).toEqual(['add'])
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    expect(h.events.map((e) => e.kind)).toEqual(['add', 'claim', 'run'])
    expect(h.events[2]?.ok).toBe(true)

    const cancel = harness({ confirm: async () => false })
    cancel.sched.add({ at: '09:00', action: 'run-all' })
    cancel.setNow(at(9, 0) + 5_000)
    await cancel.sched.tick()
    expect(cancel.events.map((e) => e.kind)).toEqual(['add', 'claim', 'skip'])
    expect(cancel.events[2]?.reason).toBe('用户取消')
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

describe('可重试失败：窗口内保留任务，再试到成功', () => {
  it('retry ⇒ waiting + retry 事件，任务保留；下一拍成功 ⇒ 消耗', async () => {
    let attempt = 0
    const h = harness({
      run: async (t) => {
        attempt += 1
        h.runs.push(t)
        return attempt === 1 ? { ok: false, retry: true, reason: '笔记本未就绪' } : { ok: true }
      },
    })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    expect(h.runs).toHaveLength(1)
    expect(occurrences(h)[`tid1|${OCC_KEY}`]).toMatchObject({
      state: 'waiting',
      reason: '笔记本未就绪',
    })
    expect(h.sched.tasks()).toHaveLength(1)
    expect(h.events.map((e) => e.kind)).toEqual(['add', 'claim', 'retry'])

    // 会话起来了（13:24 那种情况）：下一拍直接重试，不再确认/claim 一遍
    h.setNow(at(9, 0) + 25_000)
    await h.sched.tick()
    expect(h.runs).toHaveLength(2)
    expect(occurrences(h)[`tid1|${OCC_KEY}`]?.state).toBe('succeeded')
    expect(h.events.map((e) => e.kind)).toEqual(['add', 'claim', 'retry', 'retry', 'run'])
    expect(h.sched.tasks()).toEqual([])
  })

  it('重试不重复弹确认（确认只发生一次）', async () => {
    let confirms = 0
    let attempts = 0
    const h = harness({
      confirm: async () => {
        confirms += 1
        return true
      },
      run: async () => {
        attempts += 1
        return { ok: false, retry: true, reason: '会话未就绪' }
      },
    })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    h.setNow(at(9, 0) + 25_000)
    await h.sched.tick()
    expect(confirms).toBe(1)
    expect(attempts).toBe(2)
  })

  it('重试超出迟到窗口 ⇒ skipped（写原因）并消耗，不无限重试', async () => {
    let attempts = 0
    const h = harness({
      run: async () => {
        attempts += 1
        return { ok: false, retry: true, reason: '会话未就绪' }
      },
    })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    expect(h.sched.tasks()).toHaveLength(1)

    h.setNow(at(9, 11))
    await h.sched.tick()
    const occ = occurrences(h)[`tid1|${OCC_KEY}`]!
    expect(occ.state).toBe('skipped')
    expect(occ.reason).toContain('重试')
    expect(attempts).toBe(1)
    expect(h.sched.tasks()).toEqual([])
  })

  it('不可重试的失败照旧立刻 failed + 消耗（不把真错误拖成重试）', async () => {
    const h = harness({ run: async () => ({ ok: false, reason: '指纹不符' }) })
    h.sched.add({ at: '09:00', action: 'run-all' })
    h.setNow(at(9, 0) + 5_000)
    await h.sched.tick()
    expect(occurrences(h)[`tid1|${OCC_KEY}`]?.state).toBe('failed')
    expect(h.sched.tasks()).toEqual([])
  })

  it('等待期间刷新页面（writer 变、页标签不变）⇒ 新页面接管重试，不丢任务', async () => {
    let attempt = 0
    const first = harness({
      run: async (t) => {
        attempt += 1
        first.runs.push(t)
        return { ok: false, retry: true, reason: '笔记本未就绪' }
      },
    })
    first.sched.add({ at: '09:00', action: 'run-all' })
    first.setNow(at(9, 0) + 5_000)
    await first.sched.tick()
    expect(occurrences(first)[`tid1|${OCC_KEY}`]?.state).toBe('waiting')

    const second = harness(
      {
        writer: 'w2',
        run: async (t) => {
          second.runs.push(t)
          return { ok: true }
        },
      },
      first.mem,
    )
    second.setNow(at(9, 0) + 25_000)
    await second.sched.tick()
    expect(second.runs).toHaveLength(1)
    expect(occurrences(second)[`tid1|${OCC_KEY}`]).toMatchObject({
      state: 'succeeded',
      by: 'w2',
    })
    expect(second.sched.tasks()).toEqual([])
  })
})

describe('执行历史文案', () => {
  it('停会话 · 关运行时：写明运行时文件已删除（事后能从历史里认出来）', () => {
    expect(
      historyText('stop-session', {
        ok: true,
        detail: '已断开并释放运行时（Colab 删掉了运行时文件）',
      }),
    ).toBe('停会话 · 关运行时（运行时文件已删除）')
  })

  it('其它停会话/失败 ⇒ 回到动作名，不编细节', () => {
    expect(
      historyText('stop-session', {
        ok: true,
        detail: '已关闭全部 1 个内核（Running Sessions 已无内核）',
      }),
    ).toBe('停会话 · 关闭内核')
    expect(historyText('stop-session', { ok: true, detail: 'stopped' })).toBe('停会话')
    expect(historyText('stop-session', { ok: false, detail: 'x' })).toBe('停会话')
    expect(historyText('run-all', { ok: true })).toBe('运行全部')
  })
})
