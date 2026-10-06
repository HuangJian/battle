/**
 * web-course-status.test.ts — 首页课程区**单一派生**（plan/offline-online-status-switch §3.10 / P1-10）。
 *
 * 三轮补审 R4-a…R4-g 的四件事在这里钉住：
 *   ① **同一派生**：pill 与矩阵状态列的话出自 `courseStatus` 一个输出——假收官/暂停两例
 *      不得再互相矛盾（旧形状：离线课 pill「已收官」而矩阵「在训」；暂停课 pill「已暂停」
 *      而矩阵仍「在训」）；
 *   ② **未知 ≠ 否定**：hub 探针失败（保旧值）只降级 `stale` 标注、不把词跌回训练侧或编一个
 *      「hub 不认它」；没有旧事实时是「不知道」，不是「未在训」的肯定判断；
 *   ③ **滞回**：来源可用性 false→true 不改词（只动标注）；
 *   ④ **来源透明**：每词带 `source`（marker/registry/intent/hub/loop），`stale` 指向读面失败。
 *
 * 纯函数层（src/web/view/course-status.ts + course-matrix.ts 的接线），无 IO。
 */

import { describe, expect, it } from 'bun:test'
import type {
  CourseOverviewRow,
  LoopQueueRow,
  LoopQueueView,
  ParallelOverviewView,
} from '../src/web/view'
import {
  coursePills,
  courseStatus,
  matrixMeta,
  matrixStatus,
  mergeCourseRows,
  parseLoopQueue,
  withTraining,
} from '../src/web/view'

// ────────────────────────── 夹具 ──────────────────────────

/** 训练侧一行（默认：在训、RL、下一步 ready）。 */
function lqRow(patch: Partial<LoopQueueRow> & { course: string }): LoopQueueRow {
  return {
    kind: 'rl',
    training: true,
    it: 37,
    state: 'running',
    current: 'ppo',
    pending: ['ppo'],
    inflight: [],
    facts: {
      iterations: 37,
      lastVerdict: null,
      trainSecTotal: 0,
      softRemediateCount: 0,
      klStreak: 0,
      gamesSettled: 0,
      gamesPlanned: 0,
    },
    waiting: { kind: 'ready', text: '无外部等待，下一步 ppo' },
    openable: { ok: true, reason: '' },
    pausedIntent: false,
    pauseApplied: false,
    ...patch,
  }
}

/** hub 侧一行（默认：hub 认得、在线、无离线段）。 */
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
    frozen: [],
    halt: false,
    inflightDetail: [],
    stuckSec: null,
    peeked: null,
    nextJob: null,
    authority: null,
    pinned: null,
    lease: null,
    ...patch,
  }
}

function ovView(
  rows: CourseOverviewRow[],
  patch: Partial<ParallelOverviewView> = {},
): ParallelOverviewView {
  return {
    hubUrl: 'http://127.0.0.1:18787',
    hubOnline: true,
    activeCourses: rows.length,
    activeWorkers: 0,
    halt: false,
    recentDispatch: null,
    offline: rows.filter((r) => r.offline).map((r) => r.course),
    offlineProgress: null,
    rows,
    ...patch,
  }
}

function lqView(rows: Record<string, unknown>[], training: string[]): LoopQueueView {
  return withTraining(parseLoopQueue({ courses: rows, pools: {} })!, training)
}

// ────────────────────────── ① 同一派生 ──────────────────────────

describe('courseStatus：pill 与矩阵状态列同源（R4-a）', () => {
  it('★ 假收官不再出现：离线接管的课在两屏都不是「已收官」', () => {
    // P1-1 之后训练侧对离线课发的是 waiting(offline)（不是 done）——旧形状的「本机收官、
    // 云机在跑」在修好后不再出现；本用例钉的就是这个真形状：pill 与矩阵都在离线族里。
    const live = lqRow({
      course: 'off',
      state: 'waiting',
      training: false,
      waiting: { kind: 'offline', text: '离线课由云机取任务包接手（切回在线自动恢复）' },
    })
    const ov = ovRow({ course: 'off', offline: true, training: true })
    const pill = coursePills({
      courses: ['off'],
      rows: [live],
      trainerRunning: false,
      overview: ovView([ov]),
    })[0]!
    expect(pill.status).toBe('等云机')
    expect(pill.status).not.toBe('已收官')
    expect(matrixStatus(ov, live, true).text).toBe('离线（只收回传）')
    // 派生层唯一答案：两边同一个 kind（离线族）——矩阵词与 pill 词各自渲染，语义同源。
    const st = courseStatus({ course: 'off', lq: live, ov, hubOnline: true, trainerRunning: false })
    expect(st.kind).toBe('offline-waiting')
  })

  it('★2026-10-06：账本尾行 run_complete ⇒ pill 与矩阵都「已收官」（读面 ready / hub 不认都不动终态）', () => {
    // 事故：收官停车态只进了告警坞（「✅ 训练已完成」），pill/矩阵仍按只读读面说「推进中」——
    // 同一屏两个 widget 互相矛盾。终态事实单点进 `courseStatus`，两个渲染器同时对齐。
    const lq = lqRow({ course: 'p2', state: 'ready' })
    // hub 不认这门课（hubSeen=false）：停车态不得被冲突档升成「在训 · hub 未注册」。
    const ov = ovRow({ course: 'p2', training: true, hubSeen: false })
    const done = {
      at: '2026-10-06 10:39:48',
      reason: '正常收官（it16/150），本地停采、云机已停机',
      iters: 150,
    }
    const pill = coursePills({
      courses: ['p2'],
      rows: [lq],
      trainerRunning: true,
      overview: ovView([ov]),
      loopCompletes: { p2: done },
    })[0]!
    expect(pill.status).toBe('已收官')
    expect(matrixStatus(ov, lq, true, { loopComplete: done }).text).toBe('已收官')
  })

  it('★ 暂停：pill 与矩阵同词「已暂停」（此前矩阵状态列仍说「在训」）', () => {
    const lq = lqRow({ course: 'p', pausedIntent: true, pauseApplied: true })
    const ov = ovRow({ course: 'p', training: true })
    const pill = coursePills({
      courses: ['p'],
      rows: [lq],
      trainerRunning: true,
      overview: ovView([ov]),
    })[0]!
    expect(pill.status).toBe('已暂停')
    expect(matrixStatus(ov, lq, true).text).toBe('已暂停')
  })

  it('★ 收官（真）：pill 与矩阵同词「已收官」，且筛选把行仍留（开课标记在）', () => {
    const lq = lqRow({ course: 'd', state: 'done', training: false })
    const ov = ovRow({ course: 'd', training: true })
    const pill = coursePills({
      courses: ['d'],
      rows: [lq],
      trainerRunning: true,
      overview: ovView([ov]),
    })[0]!
    expect(pill.status).toBe('已收官')
    expect(matrixStatus(ov, lq, true).text).toBe('已收官')
    // 上屏判据 = 任一侧说在训（开课标记在 ⇒ 行不消失；词照实说已收官）
    const rows = mergeCourseRows({
      overview: ovView([ov]),
      queue: lqView(
        [
          {
            course: 'd',
            it: 41,
            state: 'done',
            current: '',
            pending: [],
            inflight: [],
            facts: {},
            waiting: { kind: 'idle', text: '已跑满 it40/40' },
          },
        ],
        [],
      ),
      viewing: '',
      nowSec: 0,
    })
    expect(rows).toHaveLength(1)
    expect(rows[0]!.status.text).toBe('已收官')
  })

  it('★ pinned 三态徽标（P1-6）：固定在线 / 固定离线 / 自动；旧 hub 未上报 ⇒ 不画（不猜）', () => {
    const build = (patch: Partial<CourseOverviewRow>): ReturnType<typeof mergeCourseRows>[number] =>
      mergeCourseRows({
        overview: ovView([ovRow({ course: 'c', training: true, ...patch })]),
        queue: null,
        viewing: '',
        nowSec: 0,
      })[0]!
    expect(build({ authority: 'pinned_online', pinned: true }).pinBadge?.text).toBe('固定在线')
    expect(build({ authority: 'pinned_offline', pinned: true }).pinBadge?.text).toBe('固定离线')
    expect(build({ authority: 'auto', pinned: false }).pinBadge?.text).toBe('自动')
    expect(build({ authority: null, pinned: null }).pinBadge).toBeNull()
  })

  it('★ 租约徽标（P1-6）：stale ⇒ 「可接管」；墓碑 ⇒ 「已撤租」；新鲜租约不画', () => {
    const build = (patch: Partial<CourseOverviewRow>): ReturnType<typeof mergeCourseRows>[number] =>
      mergeCourseRows({
        overview: ovView([ovRow({ course: 'c', training: true, ...patch })]),
        queue: null,
        viewing: '',
        nowSec: 0,
      })[0]!
    expect(
      build({
        lease: { workerId: 'tpu-1', silentSec: 512, stale: true, revoked: false, expiresIn: 388 },
      }).leaseBadge?.text,
    ).toBe('可接管')
    expect(
      build({
        lease: { workerId: 'tpu-1', silentSec: 3, stale: false, revoked: true, expiresIn: 800 },
      }).leaseBadge?.text,
    ).toBe('已撤租')
    expect(
      build({
        lease: { workerId: 'tpu-1', silentSec: 3, stale: false, revoked: false, expiresIn: 800 },
      }).leaseBadge,
    ).toBeNull()
  })
})

// ────────────────────────── ② 未知 ≠ 否定 ──────────────────────────

describe('courseStatus：读面失败只降级标注（R4-b/g）', () => {
  it('★ hub 探针失败（保旧值）⇒ 词不变 + `stale` 上屏；不得跌成「未在训 / hub 不认」', () => {
    const lq = lqRow({ course: 'off' })
    const ov = ovRow({ course: 'off', offline: true, training: true, offlineRounds: 3 })
    const stale = { since: 1_700_000_000_000, reason: 'ECONNREFUSED 127.0.0.1:18787' }
    const st = courseStatus({
      course: 'off',
      lq,
      ov,
      hubOnline: false,
      trainerRunning: true,
      overviewStale: stale,
    })
    expect(st.text).toBe('回传中') // 上一拍的真实仍在——不换词
    expect(st.stale).toEqual(stale)
    expect(st.source).toBe('hub')
    const ms = matrixStatus(ov, lq, false, { overviewStale: stale })
    expect(ms.text).toBe('离线（只收回传）')
  })

  it('★ 没有旧事实 ⇒ 是「不知道」而不是「未在训」的肯定判断（不编冲突档）', () => {
    const st = courseStatus({
      course: 'ghost',
      lq: null,
      ov: null,
      hubOnline: false,
      trainerRunning: false,
    })
    expect(st.conflict).toBeNull()
    expect(st.kind).toBe('not-training')
    // 表头：hub 无应答 + 失败原因在 title（不是「hub 不认这门课」）
    const ov = ovView([], { hubOnline: false, hubUrl: null, stale: { since: 1, reason: 'boom' } })
    const meta = matrixMeta({ overview: ov, queue: null })
    expect(meta.map((m) => m.text)).toContain('hub 无应答')
    expect(meta.find((m) => m.text === 'hub 无应答')!.title).toContain('boom')
  })

  it('hub 读失败但**旧值仍在显示** ⇒ 表头点名「上一拍读失败（显示缓存）」', () => {
    const ov = ovView([], { stale: { since: 1_700_000_000_000, reason: 'ECONNREFUSED' } })
    const texts = matrixMeta({ overview: ov, queue: null }).map((m) => m.text)
    expect(texts).toContain('hub 上一拍读失败（显示缓存）')
  })

  it('训练侧读失败保旧行（`queueStale`）：词仍来自旧行，只加标注', () => {
    const lq = lqRow({ course: 'c', waiting: { kind: 'collect', text: '采集中：已落 12 局' } })
    const st = courseStatus({
      course: 'c',
      lq,
      ov: null,
      hubOnline: false,
      trainerRunning: true,
      queueStale: { since: 1, reason: '只读调度器视图超时（>20s）' },
    })
    expect(st.text).toBe('采集中')
    expect(st.stale).not.toBeNull()
    expect(st.source).toBe('loop')
  })
})

// ────────────────────────── ③ 滞回 ──────────────────────────

describe('courseStatus：来源可用性变化不改词（R4-b/c/d 的滞回）', () => {
  it('hubOnline false→true（旧事实在手）：词完全一致，只有 `stale` 标注变', () => {
    const lq = lqRow({ course: 'off' })
    const ov = ovRow({ course: 'off', offline: true, training: true, offlineRounds: 2 })
    const a = courseStatus({
      course: 'off',
      lq,
      ov,
      hubOnline: false,
      trainerRunning: true,
      overviewStale: { since: 1, reason: 'x' },
    })
    const b = courseStatus({ course: 'off', lq, ov, hubOnline: true, trainerRunning: true })
    expect(a.text).toBe(b.text)
    expect(a.kind).toBe(b.kind)
    expect(a.stale).not.toBeNull()
    expect(b.stale).toBeNull()
  })

  it('pill 与矩阵都不因 hub 掉线而改变词（后台驱动的那次探测失败只动 chip）', () => {
    const ov = ovView([ovRow({ course: 'off', offline: true, training: true, offlineRounds: 1 })])
    const rows = [lqRow({ course: 'off' })]
    const before = coursePills({ courses: ['off'], rows, trainerRunning: true, overview: ov })[0]!
    const after = coursePills({
      courses: ['off'],
      rows,
      trainerRunning: true,
      overview: { ...ov, hubOnline: false, stale: { since: 1, reason: 'boom' } },
    })[0]!
    expect(after.status).toBe(before.status)
    expect(before.status).toBe('回传中')
  })
})
