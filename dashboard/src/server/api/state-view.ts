/** state-view.ts — /api/state 主视图组装。 */
import { existsSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { ConsoleStateView, MetricsView } from '../../web/view'
import { courseEnableMarkerPath, loadConsoleState, readCourseModes } from '../actions'
import { resolveCfTunnel, resolveRolloutSrc, resolveSlim } from '../../stack/specs'
import { readIterMetrics, readPairedReferee } from '../iters'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import { buildLoopQueueView } from './loop-queue'
import { buildOverview, buildWorkerRegistry, getHubAdmin, sharedTrainerAlive } from './overview'
import { detectPpoQueueStall } from './ppo-queue'
import { readTunnelAbRuns } from './tunnel-ab'
import { getSlowSnapshot } from './snapshot-refresher'
import { isBcCourse } from '../../stack/courses'

export async function buildStateView(courseOverride?: string): Promise<ConsoleStateView> {
  const cfg = loadConfigSafe()
  const state = loadConsoleState()
  const courses = discoverCourses()
  const course = courseOverride || effectiveCourse(state, courses)
  // 机群级两笔冷探测互不依赖，**并行起跑**：慢快照里的节点 ping（~1.5s）与 hub 观测面
  // （`buildOverview`/`buildWorkerRegistry` 里的 ~1.2s）。串行时它们是相加的——冷启动/
  // 动作后的第一帧实测 2.8s → 并行后 ~1.5s（2026-09-22）。下面三处 await 同一个
  // 单飞 promise（缓存键同为全局），不会多探一次。
  const hubProbe = getHubAdmin(cfg, course)
  void hubProbe.catch(() => undefined) // 真 await 在下面；这里只防「无人接手」的 rejection
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
  // ★2026-09-22（离线课）：离线（`rollout_src=run`，整段上云）课的 PPO job **不经 hub 队列
  // 认领**——执行方是 bundle kernel（自跑 plan、产物走 /offline/artifact 回传）。排队暂停检测
  // 对它是**结构性误报**（job 永远没人领 ≠ worker 断连），故离线课关闭这条红条告警。
  const offlineRollout = course ? resolveRolloutSrc(cfg, course) === 'run' : false
  const ppoQueueStall =
    course && !offlineRollout
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
  // 「在训课程」= **已开课**的课程（`tmp/<课>/training-enabled.txt`）。
  //
  // ★ 判据换过两次，两次都是事故驱动的（2026-09-20）：
  //   ① 旧口径「共享 trainer 在跑 ∧ 队列未收官」——它回答的是「进程在不在推进」，
  //      而不是「哪几门课被放进了课程表」；进程没跑时已开课的课会从顶上消失（而它明明
  //      在课程表里，一起进程就该跑）；
  //   ② 更早的「有账本 = 在训」——tmp/ 下每门历史课都有账本，**一启动共享 trainer 就
  //      把 21 门历史课一起拉起来跑**（用户报障原话：「界面显示一堆课程正在训练」）。
  // 开课标记是训练侧（`loop_plan.enabled_courses`）与 hub（`_course_dir_live`）用的**同一个**
  // 闸：控制台只是把同一份事实读出来上屏（不再自己推算一份，两份必然漂开）。
  const training = courses.filter((c) => existsSync(courseEnableMarkerPath(c)))
  // 课程生命周期事实（2026-09-20：进程与课程解耦后，顶部「训练」入口与在训 pill 行的判据）。
  // 两个判据各自只有**一个**事实源，不在这里发明第三份：
  //  · enabled = **开课标记**（`training-enabled.txt`）——训练侧 `enabled_courses` 与 hub
  //    `_course_dir_live` 的同一个闸。「有账本」**不是**在训判据（每门历史课都有账本：
  //    拿它当判据的后果就是一启动共享 trainer 就把 21 门历史课拉起来跑）；
  //  · paused = 暂停意图（与调度器卡片的「暂停」共用同一份契约）。
  const courseLifecycle = course
    ? {
        enabled: training.includes(course),
        paused: (loopQueue?.rows ?? []).some((r) => r.course === course && r.pausedIntent),
      }
    : null
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
    // 在训（= **已开课**）课程：课程 select 的多课高亮、顶部 pill 行、总览的「在训」列
    // 与门禁动作开关**同源**——一处判据修三次才会三处各说各话，故只在这里算一次。
    trainingCourses: training,
    overview,
    // 每课 hub 派发**意图**（控制台那份，权威）：hub 的 mode 是 volatile（重启回启动参数），
    // 而这份由「切离线/切换成在线」与「离线开课」写入、起 hub 时回灌。UI 拿它跟 `overview`
    // 里的 hub 事实比对 ⇒ 「意图未生效」可见（2026-09-23：回灌抢在发现之前 400，一门课
    // 静默留在 online，面板却显示「在训/切离线」，操作员直到今天才发现）。
    courseModeIntents: readCourseModes(),
    workerRegistry,
    loopQueue,
    courseLifecycle,
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
