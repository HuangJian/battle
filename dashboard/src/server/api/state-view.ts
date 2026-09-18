/** state-view.ts — /api/state 主视图组装。 */
import { existsSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { ConsoleStateView, MetricsView } from '../../web/view'
import { loadConsoleState } from '../actions'
import { resolveCfTunnel, resolveRolloutSrc, resolveSlim } from '../../stack/specs'
import { readIterMetrics, readPairedReferee } from '../iters'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import { buildLoopQueueView } from './loop-queue'
import { buildOverview, buildWorkerRegistry, trainingCourses } from './overview'
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
  const training = trainingCourses()
  // 多课程并行总览 + push worker 登记 + 调度器队列（2026-09-18/R2c-3）：三个面各有一处
  // catch——观测面坏掉（无 hub / 账本不可读 / 只读 CLI 起不来）只该让那一块显空态
  // （视图自带 error 交 UI 显因），不该把整页 /api/state 带崩。
  const [overview, workerRegistry, loopQueue] = await Promise.all([
    buildOverview(cfg, courses, course).catch(() => null),
    buildWorkerRegistry(cfg, course).catch(() => null),
    buildLoopQueueView(training).catch(() => null),
  ])
  return {
    time: new Date().toISOString(),
    course,
    // 首页 BC/RL 区互斥分流（2026-09-14）：isBc = 查看课程是否 *.bc.jsonc 课程。
    isBc: isBcCourse(course),
    activeCourse: state.activeCourse || state.course || course,
    courses,
    // 在训课程（registry trainingLoop 存活）：课程 select 的多课高亮与总览的「在训」列同源。
    trainingCourses: training,
    overview,
    workerRegistry,
    loopQueue,
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
      // M1：当前**生效**的隧道选项（单隧道 ⇒ 只有 rl.* > 缺省 http2/4）——
      // UI 显示它，避免「以为改了其实没改」。
      cfProtocol: resolveCfTunnel(cfg).protocol,
      cfEdgeIp: resolveCfTunnel(cfg).edgeIp,
      // M2：协议瘦身开关的当前**生效**值（per-course > rl.* > 缺省 on）——
      // UI 显示它，避免「以为改了其实没改」。
      slim: resolveSlim(cfg, course),
      // M3：rollout 执行位置的当前**生效**值（per-course > rl.* > 缺省 local）。
      rolloutSrc: resolveRolloutSrc(cfg, course),
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
