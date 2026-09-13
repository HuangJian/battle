/** course-overview.ts — 同屏多课总览（跨课程聚合视图）。 */
import path from 'path'
import { pidAlive } from '../../core/net'
import { REPO_ROOT } from '../../core/paths'
import { entryForCourse, loadRegistry } from '../../core/registry'
import { SLOT_COUNT } from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import {
  type CourseOverview,
  type CourseOverviewComponent,
  type PhaseInfo,
  parsePhaseFromLog,
} from '../../web/view'
import { readIterMetrics } from '../iters'
import { readLogTail, resolveComponentLog } from './logs'
import { detectPpoQueueStall } from './ppo-queue'
import { SNAPSHOT_REFRESH_MS } from './snapshot-cache'

// ────────────────────────── 同屏多课总览（P5-W2） ──────────────────────────

/** 总览列的组件（selfNode 全局单例不入——它不属任何课程）。 */
const OVERVIEW_COMPONENTS: Component[] = ['hubServer', 'cloudflared', 'workerServe', 'trainingLoop']

export const courseOverviewCache = new Map<string, { at: number; rows: CourseOverview[] }>()

/** 同屏多课总览：每课一行（组件进程状态 + 阶段 + 最近一轮 + halt/排队徽标）。
 *
 *  与单课详情的取舍：这里**只读账本**判定进程状态（不发健康探测），避免 N 课 × 每拍探测
 *  把探测预算打爆——健康探针仍只在选中课程的详情卡里做。阶段取训练循环日志尾（与 Hero
 *  同口径）。结果随慢快照同拍缓存；课程上限 = 槽位数（plan §0.4：常态 2 课、架构 4 槽）。
 */
export function getCourseOverviews(
  cfg: RlConfig,
  courses: string[],
  cloudHalts: Record<string, { status: 'halted' | 'recovered'; reason: string }> = {},
): CourseOverview[] {
  const names = courses.slice(0, SLOT_COUNT)
  if (names.length === 0) return [] // 无课：不碰账本/日志
  const key = names.join('\n')
  const now = Date.now()
  const ent = courseOverviewCache.get(key)
  if (ent && now - ent.at < SNAPSHOT_REFRESH_MS) return ent.rows
  const reg = loadRegistry()
  const rows: CourseOverview[] = names.map((c) => {
    const components: CourseOverviewComponent[] = OVERVIEW_COMPONENTS.map((k) => {
      // fail-closed：按 (key, course) 精确取条目，不用旧键兜底（避免 A 课显示 B 课状态）
      const e = entryForCourse(reg, k, c)
      const status: CourseOverviewComponent['status'] = e
        ? pidAlive(e.pid)
          ? 'running'
          : 'exited'
        : 'stopped'
      return { key: k, status, pid: e?.pid ?? null }
    })
    let last: CourseOverview['last'] = null
    let iters = 0
    try {
      const rr = readIterMetrics(path.join(REPO_ROOT, 'tmp', c)).rows
      iters = rr.length
      const r = rr[rr.length - 1]
      if (r) {
        last = {
          iter: r.iter,
          winRate: r.winRate,
          rolloutSec: r.rolloutSec,
          ppoSec: r.ppoSec,
          halted: r.halted,
        }
      }
    } catch {
      /* 无日志/损坏 → last=null（该课从未训练） */
    }
    let phase: PhaseInfo = { phase: 'idle', sinceMs: null, iter: null }
    try {
      const logRel = resolveComponentLog('trainingLoop', cfg, c)
      if (logRel) phase = parsePhaseFromLog(readLogTail(logRel, 40).lines)
    } catch {
      /* 日志不可读 → idle */
    }
    const halt = cloudHalts[c]
    return {
      course: c,
      components,
      phase,
      last,
      iters,
      cloudHalt: halt ? { status: halt.status, reason: halt.reason } : null,
      ppoQueueStall: detectPpoQueueStall(path.join(REPO_ROOT, 'tmp', c, 'remote-jobs')),
    }
  })
  courseOverviewCache.set(key, { at: Date.now(), rows })
  return rows
}
