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
import { buildOverview, buildWorkerRegistry, sharedTrainerAlive } from './overview'
import { detectPpoQueueStall } from './ppo-queue'
import { readTunnelAbRuns } from './tunnel-ab'
import { getSlowSnapshot } from './snapshot-refresher'
import { isBcCourse } from '../../stack/courses'

export async function buildStateView(courseOverride?: string): Promise<ConsoleStateView> {
  const cfg = loadConfigSafe()
  const state = loadConsoleState()
  const courses = discoverCourses()
  const course = courseOverride || effectiveCourse(state, courses)
  const { components, nodes, localNode, phase, loopComplete, pushFleet } = await getSlowSnapshot(
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
  // 多课程并行总览 + push worker 登记 + 调度器队列（2026-09-18/R2c-3）：三个面各有一处
  // catch——观测面坏掉（无 hub / 账本不可读 / 只读 CLI 起不来）只该让那一块显空态
  // （视图自带 error 交 UI 显因），不该把整页 /api/state 带崩。
  //
  // 顺序：调度器队列**先算**——「哪几门课在训」的判据就是它的行（调度器存活 ∧ 该课未收官，
  // 2026-09-19 / R3-5：共享 trainer 之后 registry 里不再有每课条目，按课查存活是假事实），
  // 而总览与课程 select 高亮都要这份名单。代价：冷启动时多串一次（队列视图有 10s TTL，
  // 总览/worker 登记有 5s TTL，稳态下两者都在缓存里）。
  const trainerAlive = sharedTrainerAlive()
  const loopQueue = await buildLoopQueueView(trainerAlive).catch(() => null)
  const training = (loopQueue?.rows ?? []).filter((r) => r.training).map((r) => r.course)
  const [overview, workerRegistry] = await Promise.all([
    buildOverview(cfg, courses, course, training).catch(() => null),
    buildWorkerRegistry(cfg, course).catch(() => null),
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
    // push 执行面（机群级）：登记节点 + hub_push（缺省开）+ 逐节点探活——不再按课程键控，
    // 也不再需要「trainer 是否以 push 在跑」这层判断：登记即候选，唯一一条派发路（2026-09-19）。
    pushFleet,
    modes: {
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
