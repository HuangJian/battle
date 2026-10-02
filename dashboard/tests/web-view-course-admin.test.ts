/**
 * web-view-course-admin.test.ts — 课程管理页（`/courses`）的纯函数：全课表 + 封存区行构建。
 *
 * 分层：src/web/view/course-admin.ts（纯函数、零 IO）
 *
 * 这一页的分工是「把总览课程矩阵筛掉的那些课摊开」——历史课占着盘、已停课该不该封存、
 * `curricula/` 里声明了却没落盘的课在哪。故用例盯四件事：
 *   ① 三态分类（在训 / 已停 / 仅课程文件）+ 封存单独一段（封存课**不进活体表**）；
 *   ② 排序（在训按 it 新→旧；已停按最后写入新→旧——最久没动的排最后，那批正是该封存的）；
 *   ③ 状态/等待列**复用**矩阵词表（不在这一页发明第二种说法）；
 *   ④ 动作可用性（在训课给封存键但说清会被拒；无活体工作区不给；已封存不给）。
 */

import { describe, expect, it } from 'bun:test'
import {
  type ArchivedCourseView,
  type CourseFactView,
  archiveOp,
  buildCourseAdmin,
  courseAdminSummary,
  lastWriteCell,
} from '../src/web/view'
import type { CourseOverviewRow, ParallelOverviewView } from '../src/web/view/course-overview'
import type { LoopQueueRow, LoopQueueView } from '../src/web/view/loop-queue'

const NOW = Math.floor(Date.parse('2026-09-27T12:00:00') / 1000)

function queueRow(course: string, over: Partial<LoopQueueRow> = {}): LoopQueueRow {
  return {
    course,
    kind: 'rl',
    training: false,
    it: 7,
    state: 'ready',
    current: '',
    pending: [],
    inflight: [],
    facts: {
      iterations: 7,
      lastVerdict: null,
      trainSecTotal: 0,
      softRemediateCount: 0,
      klStreak: 0,
      gamesSettled: 0,
      gamesPlanned: 0,
    },
    waiting: { kind: 'idle', text: '' },
    pausedIntent: false,
    pauseApplied: false,
    ...over,
  }
}

function queue(rows: LoopQueueRow[]): LoopQueueView {
  return {
    blockedCourses: [],
    pools: {},
    rows,
    trainingCount: rows.filter((r) => r.training).length,
  }
}

/** hub 侧视图（`mergeCourseRows` 只吃 `rows` / `hubOnline`；其余是机群级读数）。 */
function hub(rows: Array<Partial<CourseOverviewRow> & { course: string }>): ParallelOverviewView {
  const full: CourseOverviewRow[] = rows.map((r) => ({
    training: false,
    iter: null,
    offline: false,
    hubSeen: true,
    queuePending: 0,
    inflight: 0,
    offlineRounds: 0,
    offlineLastIter: null,
    offlineLastMtime: 0,
    frozen: [],
    halt: false,
    inflightDetail: [],
    stuckSec: null,
    peeked: null,
    nextJob: null,
    ...r,
  }))
  return {
    hubUrl: 'http://127.0.0.1:8787',
    hubOnline: true,
    activeCourses: full.length,
    activeWorkers: 1,
    halt: false,
    recentDispatch: full[0]?.course ?? null,
    offline: full.filter((r) => r.offline).map((r) => r.course),
    rows: full,
    offlineProgress: null,
  }
}

function fact(course: string, over: Partial<CourseFactView> = {}): CourseFactView {
  return { course, tmp: true, enabled: false, declared: true, lastWriteMs: null, ...over }
}

function manifest(course: string, over: Partial<ArchivedCourseView> = {}): ArchivedCourseView {
  return {
    course,
    archivedAt: '2026-09-26 10:00:00',
    form: 'C',
    parent: '',
    itRange: [0, 40],
    finalIt: 40,
    keyIters: [0, 40],
    shardsKept: false,
    codec: 'gzip',
    verdict: '',
    bytesTotal: 20_000_000,
    bytesRawTotal: 300_000_000,
    filesTotal: 120,
    weights: [{ it: 40, src: 'archive', path: `nn-training/weights/${course}/x.it40.1.json` }],
    reads: { evalLog: 'eval_log.jsonl.gz', trainLog: 'training_log.jsonl.gz' },
    ...over,
  }
}

/** 一次构建（各用例只改自己关心的那几个输入）。 */
function build(
  input: Partial<Parameters<typeof buildCourseAdmin>[0]> = {},
): ReturnType<typeof buildCourseAdmin> {
  return buildCourseAdmin({
    courses: [],
    training: [],
    facts: null,
    overview: null,
    queue: null,
    archived: null,
    viewing: '',
    nowSec: NOW,
    ...input,
  })
}

describe('course-admin · 行集与分类', () => {
  it('三态分类：开课标记在 = 在训；有活体无标记 = 已停；只有课程文件 = 未落盘', () => {
    const v = build({
      training: ['t'],
      courses: ['t', 's', 'd'],
      facts: [fact('t', { enabled: true }), fact('s'), fact('d', { tmp: false })],
    })
    const kinds = new Map(v.rows.map((r) => [r.course, r.kind]))
    expect(kinds.get('t')).toBe('enabled')
    expect(kinds.get('s')).toBe('stopped')
    expect(kinds.get('d')).toBe('declared')
    expect(v.counts).toEqual({ enabled: 1, stopped: 1, declared: 1, archived: 0 })
  })

  it('封存课**不进活体表**（即使它还在 courses 里）：只出现在封存段', () => {
    const v = build({
      courses: ['live', 'gone'],
      facts: [fact('live'), fact('gone')],
      archived: [manifest('gone')],
    })
    expect(v.rows.map((r) => r.course)).toEqual(['live'])
    expect(v.archived.map((r) => r.course)).toEqual(['gone'])
    expect(v.archived[0]!.kind).toBe('archived')
    expect(v.archived[0]!.archive!.finalIt).toBe(40)
    expect(v.counts.archived).toBe(1)
  })

  it('旧视图（无 courseFacts）退化：按 courses/training 推断，不编「未落盘」', () => {
    const v = build({ courses: ['a', 'b'], training: ['a'] })
    expect(v.rows.map((r) => r.kind)).toEqual(['enabled', 'stopped'])
  })

  it('两侧（hub/队列）登记过、但 courses 里没有的课也上屏（不静默丢课）', () => {
    const v = build({
      courses: ['a'],
      facts: [fact('a')],
      queue: queue([queueRow('a'), queueRow('zombie')]),
      overview: hub([{ course: 'a' }]),
    })
    expect(v.rows.map((r) => r.course).sort()).toEqual(['a', 'zombie'])
  })

  it('排序：已开课（it 新→旧）在前，已停按最后写入新→旧，未落盘垫底', () => {
    const v = build({
      training: ['t1', 't2'],
      courses: ['t1', 't2', 's-new', 's-old', 'd'],
      facts: [
        fact('t1', { enabled: true, lastWriteMs: NOW * 1000 - 1000 }),
        fact('t2', { enabled: true, lastWriteMs: NOW * 1000 }),
        fact('s-new', { lastWriteMs: NOW * 1000 - 60_000 }),
        fact('s-old', { lastWriteMs: NOW * 1000 - 30 * 86_400_000 }),
        fact('d', { tmp: false }),
      ],
      queue: queue([
        queueRow('t1', { training: true, it: 3 }),
        queueRow('t2', { training: true, it: 11 }),
      ]),
      overview: hub([
        { course: 't1', training: true, iter: 3 },
        { course: 't2', training: true, iter: 11 },
      ]),
    })
    expect(v.rows.map((r) => r.course)).toEqual(['t2', 't1', 's-new', 's-old', 'd'])
  })
})

describe('course-admin · 列口径（复用矩阵词表）', () => {
  it('状态/在等什么与矩阵同源：hub 标离线 → 「离线（只收回传）」+ 云机等待口径', () => {
    const v = build({
      courses: ['c'],
      facts: [fact('c')],
      queue: queue([queueRow('c')]),
      overview: hub([{ course: 'c', offline: true }]),
    })
    const r = v.rows[0]!
    expect(r.status.text).toBe('离线（只收回传）')
    expect(r.waiting.text).toBe('离线（只收回传）')
    expect(r.canToggleMode).toBe(true)
  })

  it('两侧都没有它 → 「未在训」+ 「不知道」的等待占位（不是「无待办」）', () => {
    const v = build({ courses: ['c'], facts: [fact('c')] })
    const r = v.rows[0]!
    expect(r.status.text).toBe('未在训')
    expect(r.waiting.text).toBe('—')
    expect(r.canToggleMode).toBe(false)
  })

  it('最后写入：无活体是 `—`（不是「刚刚」）；有活体给相对时间', () => {
    const v = build({
      courses: ['live', 'dead'],
      facts: [
        fact('live', { lastWriteMs: NOW * 1000 - 2 * 3600_000 }),
        fact('dead', { tmp: false }),
      ],
    })
    const by = new Map(v.rows.map((r) => [r.course, r]))
    expect(lastWriteCell(by.get('live')!, NOW).text).toBe('2h 前')
    expect(lastWriteCell(by.get('dead')!, NOW).text).toBe('—')
  })
})

describe('course-admin · 封存动作可用性', () => {
  it('在训课照常给键（服务端会拒），title 说清「先停课」', () => {
    const v = build({
      training: ['t'],
      courses: ['t'],
      facts: [fact('t', { enabled: true })],
    })
    const op = archiveOp(v.rows[0]!)!
    expect(op.label).toBe('封存')
    expect(op.title).toContain('停课')
    expect(op.title).toContain('会被拒')
  })

  it('无活体工作区（仅课程文件）不给键；已封存也不给', () => {
    const v = build({
      courses: ['d'],
      facts: [fact('d', { tmp: false })],
      archived: [manifest('gone')],
    })
    expect(archiveOp(v.rows[0]!)).toBeNull()
    expect(archiveOp(v.archived[0]!)).toBeNull()
  })

  it('已停且有活体：给键，title 写清「先预演、确认后才真删」', () => {
    const v = build({ courses: ['s'], facts: [fact('s')] })
    const op = archiveOp(v.rows[0]!)!
    expect(op.title).toContain('--dry-run')
    expect(op.title).toContain('archive/courses/s/')
    // ★ 「先停课 → 再封存」正好落进新鲜度闸（停课只删 marker、不杀在飞那轮）⇒ 这一行的
    //   title 必须自己说出来，否则正规流程的下一步会被当成故障（plan §1.3-1）。
    expect(op.title).toContain('会被拒')
    expect(op.title).toContain('静止')
  })

  it('汇总行三个数 + 封存数（表头用的就是它，不另写一份）', () => {
    const v = build({
      training: ['t'],
      courses: ['t', 's', 'd'],
      facts: [fact('t', { enabled: true }), fact('s'), fact('d', { tmp: false })],
      archived: [manifest('gone')],
    })
    expect(courseAdminSummary(v)).toBe('已开课 1 · 已停 1 · 未落盘 1 · 已封存 1')
  })
})
