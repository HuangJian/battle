/** state-view.ts — /api/state 主视图组装。 */
import { existsSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { ConsoleStateView, MetricsView } from '../../web/view'
import { loadConsoleState } from '../actions'
import { readIterMetrics, readPairedReferee } from '../iters'
import { loadConfigSafe } from './config'
import { getCourseOverviews } from './course-overview'
import { discoverCourses, effectiveCourse } from './courses'
import { detectPpoQueueStall } from './ppo-queue'
import { getSlowSnapshot } from './snapshot-refresher'

export async function buildStateView(courseOverride?: string): Promise<ConsoleStateView> {
  const cfg = loadConfigSafe()
  const state = loadConsoleState()
  const courses = discoverCourses()
  const course = courseOverride || effectiveCourse(state, courses)
  const { components, nodes, localNode, phase, loopComplete } = await getSlowSnapshot(cfg, course)
  let metrics: MetricsView = { available: false, iters: [] }
  if (course && existsSync(path.join(REPO_ROOT, 'tmp', course, 'training_log.jsonl'))) {
    try {
      const trajDir = path.join(REPO_ROOT, 'tmp', course)
      metrics = {
        available: true,
        iters: readIterMetrics(trajDir).rows,
        // 配对裁判：同趟 eval_log 扫描的副产品，纯读，失败即 null 不阻断 state。
        pairedReferee: readPairedReferee(trajDir),
      }
    } catch (e) {
      metrics = { available: false, iters: [], error: e instanceof Error ? e.message : String(e) }
    }
  }
  const ppoQueueStall = course
    ? detectPpoQueueStall(path.join(REPO_ROOT, 'tmp', course, 'remote-jobs'))
    : null
  // 同屏多课总览（P5-W2）：单课时不出（不制造无效面板）。
  const courseOverviews =
    courses.length > 1 ? await getCourseOverviews(cfg, courses, state.cloudHalts ?? {}) : []
  return {
    time: new Date().toISOString(),
    course,
    activeCourse: state.activeCourse || state.course || course,
    courses,
    courseOverviews,
    components,
    nodes,
    localNode,
    modes: {
      trainerPpo: state.trainerPpo,
      stream: Number(cfg.rl.stream ?? 0),
      doubleBuffer: Number(cfg.rl.double_buffer ?? 0),
      precollectEarly: Number(cfg.rl.precollect_early ?? 0),
    },
    metrics,
    phase,
    cloudHalts: state.cloudHalts ?? {},
    ppoQueueStall,
    loopComplete,
  }
}
