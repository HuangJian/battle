/** state-view.ts — /api/state 主视图组装。 */
import { existsSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { ConsoleStateView, MetricsView } from '../../web/view'
import { loadConsoleState } from '../actions'
import { resolveCfTunnel } from '../../stack/specs'
import { readIterMetrics, readPairedReferee } from '../iters'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import { detectPpoQueueStall } from './ppo-queue'
import { readTunnelAbRuns } from './tunnel-ab'
import { getSlowSnapshot } from './snapshot-refresher'
import { isBcCourse } from '../../stack/courses'

export async function buildStateView(courseOverride?: string): Promise<ConsoleStateView> {
  const cfg = loadConfigSafe()
  const state = loadConsoleState()
  const courses = discoverCourses()
  const course = courseOverride || effectiveCourse(state, courses)
  const { components, nodes, localNode, phase, loopComplete, pushTarget } = await getSlowSnapshot(
    cfg,
    course,
  )
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
  return {
    time: new Date().toISOString(),
    course,
    // 首页 BC/RL 区互斥分流（2026-09-14）：isBc = 查看课程是否 *.bc.jsonc 课程。
    isBc: isBcCourse(course),
    activeCourse: state.activeCourse || state.course || course,
    courses,
    components,
    nodes,
    localNode,
    // push 执行面「此刻真的在生效」= 本课 trainer 以 push 模式在跑（模式来自控制台状态，
    // 存活来自组件探测）——徽章据此从「配置指向」切换为「正在用」。
    pushTarget: pushTarget
      ? {
          ...pushTarget,
          active:
            state.trainerPpo === 'push' &&
            components.some((c) => c.key === 'trainingLoop' && c.status === 'running'),
        }
      : null,
    modes: {
      trainerPpo: state.trainerPpo,
      stream: Number(cfg.rl.stream ?? 0),
      doubleBuffer: Number(cfg.rl.double_buffer ?? 0),
      precollectEarly: Number(cfg.rl.precollect_early ?? 0),
      // M1：当前**生效**的隧道选项（per-course 覆盖 > rl.* > 缺省 http2/4）——
      // UI 显示它，避免「以为改了其实没改」。
      cfProtocol: resolveCfTunnel(cfg, course).protocol,
      cfEdgeIp: resolveCfTunnel(cfg, course).edgeIp,
    },
    metrics,
    // M1 隧道 A/B：与课程账本无关（探针结果落 tmp/），故不分课程、纯只读。
    tunnelAb: readTunnelAbRuns(),
    phase,
    cloudHalts: state.cloudHalts ?? {},
    ppoQueueStall,
    loopComplete,
  }
}
