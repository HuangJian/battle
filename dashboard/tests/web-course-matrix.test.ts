/**
 * web-course-matrix.test.ts — 课程矩阵的合并纯函数（src/web/view/course-matrix.ts）
 *
 * 分层：src/web/view/course-matrix.ts（无 IO，纯函数）
 *
 * 为什么这个文件必须存在（docs/dashboard-redesign.md §6 P2）：合并两张表是**唯一会丢信息**的
 * 重构动作——分开时「hub 的表」与「训练侧的表」各自完整，一旦 join 错（漏掉只在单侧出现的
 * 课、把缺数据渲染成 0、把「不知道」当成「没有」），缺陷的表现是**屏幕上少一行**，而不是报错。
 * 所以三条主用例钉的是三种最容易悄悄错掉的局面：
 *
 *  1. **单侧缺失**：只在 hub 表里 / 只在训练侧 —— 都必须出行，且缺的那几列是 `—` + 说明原因。
 *  2. **状态冲突**：hub 说在派发、训练侧没进程（反之亦然）—— 合并前被漏掉的信号，必须上状态列。
 *  3. **离线段过期**：1 小时没新产物 —— 离线课唯一的「死」信号。
 *
 * 另有一条**回归闸**：hub 无应答时不得把 `hubSeen === false` 读成「hub 不认这门课」。
 * 合并前的总览卡犯过这个错（表头写着「hub 无应答」，行里却叫人去 `--course` 重启 hub）。
 */

import { describe, expect, it } from 'bun:test'
import type { CourseOverviewRow, LoopQueueView, ParallelOverviewView } from '../src/web/view'
import {
  CELL_UNKNOWN,
  HUB_DOWN_TITLE,
  OFFLINE_STALE_SEC,
  QUEUE_DOWN_TITLE,
  matrixBadgeTone,
  matrixConflict,
  matrixDotTone,
  matrixFoot,
  matrixMeta,
  matrixStatus,
  mergeCourseRows,
  parseLoopQueue,
  pauseOp,
  queueCell,
  segmentCell,
  waitingCell,
  withPausedFacts,
  withTraining,
} from '../src/web/view'

// ────────────────────────── 夹具 ──────────────────────────

/** hub 侧一行（形状与 `buildCourseRows` 的产物一致）。 */
function ovRow(patch: Partial<CourseOverviewRow> & { course: string }): CourseOverviewRow {
  return {
    training: false,
    iter: null,
    offline: false,
    hubSeen: true,
    queuePending: 0,
    inflight: 0,
    offlineRounds: 0,
    offlineLastIter: null,
    offlineLastMtime: 0,
    ...patch,
  }
}

/** 训练侧视图：走**真实解析路径**（不手拼 LoopQueueRow），保证与 python 输出的形状同源。 */
function lqView(courses: Record<string, unknown>[], training: string[], pools = {}): LoopQueueView {
  const v = parseLoopQueue({ courses, pools })!
  return withTraining(v, training)
}

/** hub 侧整页视图（默认：在线、有应答）。 */
function ovView(
  rows: CourseOverviewRow[],
  patch: Partial<ParallelOverviewView> = {},
): ParallelOverviewView {
  return {
    hubUrl: 'http://127.0.0.1:18787',
    hubOnline: true,
    raceActive: false,
    activeCourses: 2,
    activeWorkers: 3,
    halt: false,
    recentDispatch: 'c4',
    offlineProgress: null,
    rows,
    ...patch,
  }
}

/** 训练侧一行（默认：在训、RL、报 37 轮、下一步 ppo）。 */
function lqRow(patch: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    course: 'c4',
    it: 37,
    state: 'ready',
    current: 'ppo',
    pending: ['ppo'],
    inflight: [],
    facts: {},
    waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' },
    ...patch,
  }
}

const NOW = 1_700_000_000

// ────────────────────────── ① 单侧缺失 ──────────────────────────

describe('mergeCourseRows：outer join 不丢行（单侧缺失）', () => {
  it('只在 hub 表里 / 只在训练侧 —— 两种都出行，且顺序稳定（hub 侧序在前）', () => {
    const rows = mergeCourseRows({
      overview: ovView([ovRow({ course: 'hub-only', hubSeen: true })], {
        activeCourses: 1,
        activeWorkers: 1,
        recentDispatch: null,
      }),
      queue: lqView([lqRow({ course: 'trainer-only' })], ['trainer-only']),
      viewing: '',
      nowSec: NOW,
    })
    expect(rows.map((r) => r.course)).toEqual(['hub-only', 'trainer-only'])
    // 各自缺的那一半是 null（面板据此渲染 `—` + 说明，而不是编数字）
    expect(rows[0]!.lq).toBeNull()
    expect(rows[1]!.ov).toBeNull()
  })

  it('hub 侧缺列：队列列是 `—` + 「hub 无应答」，**不是** 0', () => {
    const cell = queueCell(ovRow({ course: 'c4', queuePending: 5, inflight: 2 }), false)
    expect(cell.text).toBe(CELL_UNKNOWN)
    expect(cell.title).toBe(HUB_DOWN_TITLE)
    expect(cell.text).not.toContain('0')
  })

  it('训练侧缺列：「在等什么」是 `—` + 读失败原因（说「不可知」，不说「本轮无待办」）', () => {
    const cell = waitingCell(null)
    expect(cell.text).toBe(CELL_UNKNOWN)
    expect(cell.title).toBe(QUEUE_DOWN_TITLE)
    expect(cell.title).toContain('不可知')
    // 单元格本身不能是一句「无待办」式的肯定陈述（那是**说不知道没事干**）
    expect(cell.text).not.toContain('待办')
  })

  it('两侧同一门课只出一行（join 而不是去重后拼接）', () => {
    const rows = mergeCourseRows({
      overview: ovView([ovRow({ course: 'c4', training: true })], {
        hubUrl: null,
        hubOnline: false,
        activeCourses: 0,
        activeWorkers: 0,
        recentDispatch: null,
      }),
      queue: lqView([lqRow({ course: 'c4' })], ['c4']),
      viewing: 'c4',
      nowSec: NOW,
    })
    expect(rows).toHaveLength(1)
    expect(rows[0]!.ov).not.toBeNull()
    expect(rows[0]!.lq).not.toBeNull()
    expect(rows[0]!.viewing).toBe(true)
  })
})

// ────────────────────────── ② 状态冲突 ──────────────────────────

describe('状态列：两侧判据打架时上屏（合并前漏掉的信号）', () => {
  it('hub 在线且注册了它、训练侧没进程 ⇒ `hub 已注册 · 无进程`（warn）——job 堆着没人消费', () => {
    const ov = ovRow({ course: 'c4', hubSeen: true, training: false })
    const st = matrixStatus(ov, null, true)
    expect(st.text).toBe('hub 已注册 · 无进程')
    expect(st.tone).toBe('warn')
    expect(matrixConflict(ov, null, true)).toBe('hub-no-process')
  })

  it('在训但 hub 没注册 ⇒ `在训 · hub 未注册`（warn）——rollout 白跑、PPO 永不派发', () => {
    const ov = ovRow({ course: 'c4', hubSeen: false, training: true })
    const st = matrixStatus(ov, null, true)
    expect(st.text).toBe('在训 · hub 未注册')
    expect(st.tone).toBe('warn')
    expect(matrixConflict(ov, null, true)).toBe('hub-unregistered')
  })

  it('★ 回归闸：hub 无应答时**不得**把「读不到课程表」读成「hub 未注册」', () => {
    // 合并前的总览卡就是这个错：hubOnline=false ⇒ 队列整个读不到 ⇒ hubSeen 恒 false，
    // 于是每个在训课程都被贴上「在训·hub 未注册」+「以 --course 重启 hub」的假诊断，
    // 而同一张卡的表头正写着「hub 无应答」。
    const ov = ovRow({ course: 'c4', hubSeen: false, training: true })
    const st = matrixStatus(ov, null, false)
    expect(st.text).toBe('在训') // 只知道在训，不知道 hub 认不认它
    expect(st.title).not.toContain('--course')
    expect(matrixConflict(ov, null, false)).toBeNull()
    // 同理：hub 无应答时不报「已注册 · 无进程」（那是「hub 认识它」的肯定判断）
    const idle = matrixStatus(ovRow({ course: 'c5', hubSeen: false }), null, false)
    expect(idle.text).toBe('未在训')
    expect(matrixConflict(ovRow({ course: 'c5', hubSeen: false }), null, false)).toBeNull()
  })

  it('状态优先级：离线（只收回传）压过「在训」——它是 hub 的一种模式，不是故障', () => {
    const st = matrixStatus(ovRow({ course: 'c4', offline: true, training: true }), null, true)
    expect(st.text).toBe('离线（只收回传）')
    expect(st.tone).toBe('info') // 不涂成黄/红：离线是合法模式
  })

  it('五态各是一个不同的说法（文案两两不同；语义档映射到点/徽章词表）', () => {
    const states = [
      matrixStatus(ovRow({ course: 'a', training: true, hubSeen: true }), null, true),
      matrixStatus(ovRow({ course: 'b', training: true, hubSeen: false }), null, true),
      matrixStatus(ovRow({ course: 'c', offline: true }), null, true),
      matrixStatus(ovRow({ course: 'd', hubSeen: true }), null, true),
      matrixStatus(ovRow({ course: 'e', hubSeen: false }), null, true),
    ].map((s) => s.text)
    expect(new Set(states).size).toBe(5)
    expect(states).toEqual([
      '在训',
      '在训 · hub 未注册',
      '离线（只收回传）',
      'hub 已注册 · 无进程',
      '未在训',
    ])
    // 语义档 → 原语词表（点四档 / 徽章五档），不允许矩阵自己长一套类名
    expect(matrixDotTone('ok')).toBe('ok')
    expect(matrixDotTone('info')).toBe('off')
    expect(matrixBadgeTone('ok')).toBe('g')
    expect(matrixBadgeTone('warn')).toBe('y')
    expect(matrixBadgeTone('info')).toBe('a')
    expect(matrixBadgeTone('off')).toBe('gray')
  })
})

// ────────────────────────── ③ 离线段过期 ──────────────────────────

describe('段内列：离线课唯一的「死」信号', () => {
  const offlineRow = (ageSec: number) =>
    ovRow({
      course: 'c5',
      offline: true,
      offlineRounds: 3,
      offlineLastIter: 9,
      offlineLastMtime: NOW - ageSec,
    })

  it('未离线 / 没回传过 → 不给单元格（那一行的状态徽标已经说了）', () => {
    expect(segmentCell(ovRow({ course: 'c4' }), NOW)).toBeNull()
    expect(segmentCell(ovRow({ course: 'c4', offline: true }), NOW)).toBeNull()
    expect(segmentCell(null, NOW)).toBeNull()
  })

  it('1 分钟前 = 在跑（不醒目）；2 小时前 = 醒目并点名「可能挂了」', () => {
    const fresh = segmentCell(offlineRow(60), NOW)!
    expect(fresh.text).toBe('段内 3 轮 · 最近 1m 前')
    expect(fresh.warn).toBe(false)
    expect(fresh.title).not.toContain('可能挂了')

    const stale = segmentCell(offlineRow(2 * OFFLINE_STALE_SEC), NOW)!
    expect(stale.warn).toBe(true)
    expect(stale.title).toContain('可能挂了')
    expect(stale.title).toContain(`${OFFLINE_STALE_SEC / 60} 分钟`)
  })

  it('红线边界：恰好 1 小时不算过期（判据是「超过」），1 小时零 1 秒算', () => {
    expect(segmentCell(offlineRow(OFFLINE_STALE_SEC), NOW)!.warn).toBe(false)
    expect(segmentCell(offlineRow(OFFLINE_STALE_SEC + 1), NOW)!.warn).toBe(true)
  })

  it('mtime=0（还没有产物）→ 相对时间显示 `—`，不显示 1970 年前', () => {
    const cell = segmentCell(ovRow({ course: 'c5', offline: true, offlineRounds: 2 }), NOW)!
    expect(cell.text).toContain('最近 —')
    expect(cell.warn).toBe(false)
  })
})

// ────────────────────────── 单元格来源与指针 ──────────────────────────

describe('单元格来源：指针优先训练侧，且说清来自哪一侧', () => {
  const merged = (ov: CourseOverviewRow | null, lq: Record<string, unknown> | null) =>
    mergeCourseRows({
      overview: ovView(ov ? [ov] : [], {
        hubUrl: null,
        hubOnline: false,
        activeCourses: 0,
        activeWorkers: 0,
        recentDispatch: null,
      }),
      queue: lq ? lqView([lq], [String(lq.course)]) : null,
      viewing: '',
      nowSec: NOW,
    })[0]!

  it('两侧都有指针 → 取训练侧的（它是「下一轮要跑的 it」），并标注来源', () => {
    const r = merged(ovRow({ course: 'c4', iter: 41 }), lqRow({ course: 'c4', it: 37 }))
    expect(r.iter).toBe(37)
    expect(r.iterSource).toBe('queue')
  })

  it('只有 hub 侧账本尾行 → 用它并标注来源（不是 `—`）', () => {
    const r = merged(ovRow({ course: 'c4', iter: 41 }), null)
    expect(r.iter).toBe(41)
    expect(r.iterSource).toBe('ledger')
  })

  it('两侧都没有 → null（面板渲染 `—`；**不是** it0）', () => {
    const r = merged(ovRow({ course: 'c4', iter: null }), null)
    expect(r.iter).toBeNull()
    expect(r.iterSource).toBeNull()
  })

  it('切离线开关的能力边界：只在 hub 在线 ∧ hub 认识它时给（否则点下去一定 400）', () => {
    expect(merged(ovRow({ course: 'c4', hubSeen: true }), lqRow()).canToggleMode).toBe(false) // hub 离线
    const online = mergeCourseRows({
      overview: ovView([
        ovRow({ course: 'c4', hubSeen: true }),
        ovRow({ course: 'c5', hubSeen: false }),
      ]),
      queue: null,
      viewing: '',
      nowSec: NOW,
    })
    expect(online[0]!.canToggleMode).toBe(true)
    expect(online[1]!.canToggleMode).toBe(false)
  })

  it('暂停开关的事实徽标：已暂停 / 待生效 / 恢复中 三种，且不回落到「暂停」态', () => {
    const build = (intent: string[], applied: string[]) => {
      const v = parseLoopQueue({ courses: [lqRow()], pools: {} })!
      return withPausedFacts(withTraining(v, ['c4']), intent, applied)
    }
    expect(pauseOp(build([], []).rows[0]!)!.badge).toBeNull()
    expect(pauseOp(build(['c4'], ['c4']).rows[0]!)!.badge).toEqual({ text: '已暂停', tone: 'a' })
    expect(pauseOp(build(['c4'], []).rows[0]!)!.badge).toEqual({ text: '待生效', tone: 'y' })
    expect(pauseOp(build([], ['c4']).rows[0]!)!.badge).toEqual({ text: '恢复中', tone: 'y' })
    // 训练侧整行缺失 ⇒ 没有暂停开关
    expect(pauseOp(null)).toBeNull()
  })
})

// ────────────────────────── 表头 / 页脚 ──────────────────────────

describe('表头元信息与页脚：两侧各自缺什么都要说出来', () => {
  const ov = (patch: Partial<ParallelOverviewView> = {}) => ovView([], patch)

  it('hub 正常：地址/在派发/worker/游标都在；竞速与停机只在置位时出现', () => {
    const texts = matrixMeta({ overview: ov(), queue: null }).map((m) => m.text)
    expect(texts).toContain('hub 127.0.0.1:18787')
    expect(texts).toContain('在派发 2 / worker 3')
    expect(texts).toContain('最近派发 c4')
    expect(texts.some((t) => t.includes('竞速'))).toBe(false)
    expect(texts.some((t) => t.includes('停机'))).toBe(false)
    const on = matrixMeta({ overview: ov({ raceActive: true, halt: true }), queue: null })
    expect(on.map((m) => m.text)).toContain('竞速')
    expect(on.map((m) => m.text)).toContain('停机中')
  })

  it('hub 无应答 / 视图未读 → 各说各的原因，且不冒充「0 在派发」', () => {
    const down = matrixMeta({ overview: ov({ hubUrl: null, hubOnline: false }), queue: null })
    expect(down.map((m) => m.text)).toContain('hub 无应答')
    expect(down.some((m) => m.text.includes('在派发'))).toBe(false)
    const missing = matrixMeta({ overview: null, queue: null })
    expect(missing[0]!.text).toBe('hub 视图未读')
    expect(missing.map((m) => m.title)).toContain(HUB_DOWN_TITLE)
  })

  it('训练侧：在训 N/M、等回传课数、排队等资源、池票占用、上一拍读失败', () => {
    const base = lqView(
      [
        lqRow({ course: 'a', waiting: { kind: 'inflight', text: '等回传' } }),
        lqRow({ course: 'b' }),
      ],
      ['a'],
      { local_ppo: { held: 1, capacity: 1 } },
    )
    const meta = matrixMeta({
      overview: null,
      queue: { ...base, blockedCourses: ['b'], error: 'boom' },
    })
    const texts = meta.map((m) => m.text)
    expect(texts).toContain('在训 1/2')
    expect(texts).toContain('1 课等回传') // 只有「在训」的等回传才算
    expect(texts).toContain('排队等资源：b')
    expect(texts).toContain('local_ppo 1/1')
    expect(texts).toContain('上一拍读失败（显示缓存）')
    expect(meta.find((m) => m.text === '上一拍读失败（显示缓存）')!.title).toBe('boom')
  })

  it('页脚：有课在等外部时点名它；排队时说明容量 1 的语义；训练侧缺席时说明只有 hub 侧事实', () => {
    const rows = mergeCourseRows({
      overview: null,
      queue: lqView(
        [
          lqRow({
            course: 'a',
            inflight: [{ phase: 'ppo', round: '37', jid: 'job-abcdef123456', dispatch: 'push' }],
          }),
        ],
        ['a'],
      ),
      viewing: '',
      nowSec: NOW,
    })
    const q = lqView([lqRow({ course: 'a' })], ['a'])
    expect(matrixFoot({ queue: q, rows })).toContain('a 正在等 ppo@37')
    expect(matrixFoot({ queue: q, rows })).toContain('jid=job-abcdef12')
    expect(matrixFoot({ queue: { ...q, blockedCourses: ['a'] }, rows })).toContain('容量 1')
    // 训练侧整块缺席 = 行里根本没有 lq（与 panel 的调用形状一致：queue 与 rows 同源）
    const hubOnly = mergeCourseRows({
      overview: ovView([ovRow({ course: 'c4' })]),
      queue: null,
      viewing: '',
      nowSec: NOW,
    })
    expect(matrixFoot({ queue: null, rows: hubOnly })).toContain('只有 hub 侧事实')
  })
})
