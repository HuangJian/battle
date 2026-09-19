/** start.ts — 组件启动（含 BC 循环启动与 run_bc 锁检查）。 */
import { appendFileSync, existsSync, mkdirSync, readFileSync, statSync } from 'fs'
import path from 'path'
import { loadConfig, validateCourseArg } from '../../core/config'
import { httpOk, pidAlive, waitUntil } from '../../core/net'
import { tmpLogsDir } from '../../core/paths'
import { launchSpec } from '../../core/proc'
import { clearAnyComponent, saveAnyComponent, scopeOf } from '../../core/registry'
import { monitorTouch } from '../../core/reload-touch'
import {
  lockName,
  lockPathFor,
  sharedHubUrl,
  sharedHubPort,
  slotOf,
  slotPort,
  validateCourseName,
} from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import { resolveVenvPython } from '../../core/venv'
import { type CourseMachineKnobs, writeCourseMachineKnobs } from '../../stack/course-knobs'
import { isBcCourse, seedWeightsFromBc } from '../../stack/courses'
import {
  hubServerHealthy,
  selfNodeHealthy,
  stepCloudflared,
  stepHubServer,
  stepSelfNode,
  supersedeLegacyInstances,
} from '../../stack/hub'
import { startLocalWorker } from '../../stack/local-worker'
import { startLocalWorkerServer } from '../../stack/push'
import { TRAINER_SERVE_ENTRY, trainerServeSpec, workerServeSpec } from '../../stack/specs'
import { entryOf, markCloudHaltRecovered } from './cloud-halt'
import { ConsoleState } from './console-state'
import { restoreCourseModesNote } from './course-mode'
import { COMPONENT_LABELS, runRlLockHolder, tailLines } from './labels'
import { ActionError, ActionResult, busyKey, done, guard, release } from './result'

// ────────────────────────── 组件启动 ──────────────────────────

export interface StartCtx {
  course: string
  /** trainer 模式（pull/push → 云端 worker；local → 本机独立 localWorker 的 pull 模式）。
   *
   *  ★ 共享 trainer 时代它是**每课**的传输裁决：进程级只有一份命令行，故它落成
   *  `rl-config → courses.<课>.remote_transport`（+ local 时的本机 hub 地址），由
   *  `prepareCourseForSharedTrainer` 写、python `apply_course_machine_overrides` 施加。 */
  trainerPpo: ConsoleState['trainerPpo']
  /** 显式 REMOTE_PUSH_NODE（仅冒烟/本机伪 GPU 预演；真实 Push 走 rl-config gpu_push 节点，
   *  不注入 env——env 会强制 remote_token，覆盖用户填写的 authKey）。
   *
   *  ★ 共享 trainer 不再消费它（`trainerServeSpec` 不给 `REMOTE_PUSH_NODE`）：push 执行面
   *  由 `stack/push-config.configurePushEndpoint` 写进 rl-config（节点表 + 本课 push_node_url），
   *  由训练侧自己解析。保留字段是因为预演（`train-smoke.ts`）仍用它把伪节点注入 env。 */
  pushNodeUrl?: string
  /** T7：远端连败是否 opt-in 降级本机 PPO（默认 false = ABORT）。 */
  remoteDegrade?: boolean
}

/** run_bc 单实例锁持有人（BC 课程；与 runRlLockHolder 同语义，锁名 run_bc）。 */
export function runBcLockHolder(course = ''): number | null {
  const lockPath = lockPathFor(validateCourseName(course), 'run_bc')
  if (!existsSync(lockPath)) return null
  try {
    const holder = Number.parseInt((readFileSync(lockPath, 'utf-8').split('|')[0] ?? '').trim(), 10)
    return Number.isInteger(holder) && holder > 0 && pidAlive(holder) ? holder : null
  } catch {
    return null
  }
}

/** **共享 trainer 的单实例锁持有人**（`nn-training/.run_cluster.lock`，2026-09-19 / R3-5）。
 *
 *  一个进程服务**所有**课程 ⇒ 双开就是两套调度器抢同一批 traj（按课锁拦不住这一类：
 *  两套调度器可以各跑一半课程，每门课都恰好只有一个跑者）。python 侧 `--serve` 自己也会
 *  响亮拒启；这里是控制台的**前置**检查——拦在 spawn 之前，报错落在动作返回值里而不是日志里。 */
export function runClusterLockHolder(): number | null {
  const lockPath = lockPathFor('', 'run_cluster')
  if (!existsSync(lockPath)) return null
  try {
    const holder = Number.parseInt((readFileSync(lockPath, 'utf-8').split('|')[0] ?? '').trim(), 10)
    return Number.isInteger(holder) && holder > 0 && pidAlive(holder) ? holder : null
  } catch {
    return null
  }
}

/** 启动共享 trainer 前的**课程准备**：返回施加了机器侧旋钮的 config + 人读说明。
 *
 *  三件事各解决一个具体的坑（每一件都是「不做就会静默地不对」的那类）：
 *
 *   ① **权重播种**（RL 才有；BC 无 warm-start）：`tmp/<课>/weights.json` 不在则从 BC 种子复制。
 *      共享 trainer 不接受每课的权重参数，它按 traj 目录自己找（与 `run_rl.py` 同约定）。
 *   ② **发现事实**：`tmp/<课>/training_log.jsonl` 必须存在。训练侧的课程表**就是**这个文件
 *      （`rl/loop_plan.discover_courses` 与控制台 `discoverCourses` 同一判据），而共享 trainer 是
 *      「先起进程、后加课」的模型 —— 不建它，这门新课永远不会被发现（症状极难查：进程活着、
 *      队列正常、就是这门课一轮都不跑）。空账本 = 合法状态（第 1 轮从头开始）。
 *   ③ **机器侧旋钮**：`courses.<课>.{remote_transport,remote_hub_url,remote_degrade_after}`。
 *      单进程没有「这门课的 flag」这一说（命令行只有一份），故「这门课怎么连云」住 rl-config；
 *      不写它的后果很具体：`auto` 会按残留的 `push_node_url` 把 job 推给云机，而操作员以为
 *      自己选的是 pull/local（2026-09-17 事故：云机 pull 会话被过期节点劫走 → 530 三连败 →
 *      GATE ABORT，而云机 worker 其实正在正常 pull）。
 *
 *  local 模式 = 本机独立 worker 的 pull 模式：`remote_transport=pull` + `remote_hub_url=<本机 hub>`
 *  ——与旧 per-course spec 的 `--remote-transport pull --remote-hub-url <本机 hub>` 逐字段同义。
 *  **不动 `rl.remote_hub_url`**（刻意）：那个键是「pull preset 的隧道 URL」的家，写本机 hub
 *  进去会把它悄悄改成打本机 hub；per-course 覆盖正好只作用于这一门课。 */
export function prepareCourseForSharedTrainer(
  cfg: RlConfig,
  course: string,
  ctx: StartCtx,
): { cfg: RlConfig; notes: string[] } {
  const notes: string[] = []
  const bc = isBcCourse(course)
  // 课程 traj 根：生产下就是仓根 `tmp/`（与训练侧同布局），单测用 BCITY_TMP_LOGS_DIR 重定向
  const trajRoot = tmpLogsDir()
  if (!bc) {
    const weightsPath = path.join(trajRoot, course, 'weights.json')
    if (!existsSync(weightsPath)) {
      seedWeightsFromBc(course, weightsPath) // 缺文件即抛 → 调用方响亮失败（不静默跑新权）
      notes.push(`已播种初始权重 tmp/${course}/weights.json`)
    }
  }
  const traj = path.join(trajRoot, course)
  mkdirSync(traj, { recursive: true })
  const ledger = path.join(traj, 'training_log.jsonl')
  if (!existsSync(ledger)) {
    appendFileSync(ledger, '')
    notes.push('已建课程账本 training_log.jsonl（共享 trainer 的课程发现判据）')
  }
  const knobs: CourseMachineKnobs = {}
  if (ctx.trainerPpo === 'push') knobs.remoteTransport = 'push'
  else if (ctx.trainerPpo === 'pull') knobs.remoteTransport = 'pull'
  else {
    knobs.remoteTransport = 'pull'
    knobs.remoteHubUrl = sharedHubUrl(cfg)
  }
  if (ctx.remoteDegrade !== undefined) knobs.remoteDegradeAfter = ctx.remoteDegrade ? 3 : 0
  const w = writeCourseMachineKnobs(cfg, course, knobs)
  // 旋钮说明放**第一条**：它是操作员最需要确认的一件事（「我选的模式真的落盘了吗」），
  // 也是结果详情/预设详情的首行
  const knobNote =
    `本课（${course}）机器侧传输 = ${knobs.remoteTransport}` +
    `${knobs.remoteHubUrl ? ` @ ${knobs.remoteHubUrl}` : ''}` +
    (w.changed ? '（已写入 rl-config courses.<课>）' : '（rl-config 已是该值，未重写）') +
    '；其余并行课程各按自己的配置'
  return { cfg: w.cfg, notes: [knobNote, ...notes] }
}

/** **启动共享 trainer**（`run_rl_cluster.py --serve`）——一个进程服务所有课程（R3-5）。
 *
 *  为什么不再是「每课一个 `run_rl.py --course`」：用户口径「hubserver/trainingloop/selfNode/
 *  cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程」。R2d 已造好单进程驱动者
 *  （按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 / 暂停恢复），R3-4 又让同一个进程能带 BC 课，
 *  而 BC 与 RL **共用 `trainingLoop` 这一个角色键** —— 两个进程并存是旧形状。
 *
 *  **不给 `--courses`（发现模式）**：课程 = 「`tmp/<课>/training_log.jsonl` 存在」这个文件系统
 *  事实（与 hub `--discover` 同一原则），所以「先起 trainer、后加课」不需要重启进程；
 *  代价是控制台得替**这门课**把账本文件建出来（见 prepare ②）。
 *
 *  ⚠ 行为语义（操作员必须知道）：**停止 trainer = 停掉所有课程的训练**。停单门课用调度器卡片
 *  的「暂停」（控制文件，只影响调度、队列与账本一个字不动）。
 *
 *  BC 课与 RL 课走**同一条路**（R3-4 起 `--serve` 按课程种类选引擎/粒度/指针）：BC 不再有
 *  单独的 `run_bc.py` 启动分支。 */
async function startSharedTrainer(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
  ctx: StartCtx,
): Promise<ActionResult> {
  const course = ctx.course
  const bc = isBcCourse(course)
  // 槽恒 `''`：共享实例不属于任何单门课（`entryOf` 内部走 scopeOf，读面自动同源）
  const prev = entryOf('trainingLoop')
  if (pidAlive(prev?.pid)) {
    // 幂等早退，但**先把本课意图落盘**：单进程的传输裁决住 rl-config（命令行只有一份），
    // 早退前不写它，操作员的「给这门课换成 push」就成了静默无效的动作。
    // 同时账本/权重那两件也照样做（新课的账本不建出来，发现扫拍永远看不见它）。
    //
    // ★ 准备失败**不改事实**：trainer 在跑、其它并行课程照常。若让它冒泡成
    // `startComponent` 的通用失败（"TrainingLoop (trainer) 启动失败: …"），操作员会以为
    // trainer 没起来，于是去停/重启它 —— 而停共享 trainer = 停掉**所有**课程的训练
    // （本文件顶部那条 ⚠）。故这里自己接住：消息以「已在运行」开头，失败只说本课。
    const running = `共享 trainer 已在运行 (PID ${prev!.pid})——一个进程服务所有课程（含 ${course}）`
    const knobHint =
      '★ 机器侧旋钮在**开课时**施加（python `apply_course_machine_overrides`）：' +
      '已经开课的课程要等它重开才换传输——暂停该课 → 重启共享 trainer（或等引擎驱逐重开）'
    let prep: { cfg: RlConfig; notes: string[] } | null = null
    let prepErr = ''
    try {
      prep = prepareCourseForSharedTrainer(cfg, course, ctx)
    } catch (e) {
      prepErr = e instanceof Error ? e.message : String(e)
    }
    if (prepErr)
      return done(false, `${running}；但**本课**（${course}）没准备好：${prepErr}`, [
        '★ trainer 本身没问题，其它课程不受影响——**别停它**（停共享 trainer = 停掉所有并行课程的训练）；' +
          '停单门课用调度器卡片的「暂停」。',
        '★ 修好根因后重按「启动」即可补上本课准备（幂等：已在运行不会重复起进程）。',
        knobHint,
      ])
    return done(true, running, [...prep!.notes, knobHint])
  }
  const clusterHolder = runClusterLockHolder()
  if (clusterHolder)
    return done(
      false,
      `共享 trainer 单实例锁被 PID ${clusterHolder} 持有` +
        `（${path.join('nn-training', lockName('', 'run_cluster'))}）——一个进程服务所有课程，` +
        '先停掉它（或删除该锁文件）',
    )
  // 本课的去重锁：serve 开课时会按课取锁（RL=run_rl / BC=run_bc），别的持有者会让它**响亮拒开
  // 这门课**。在这里拦下来，操作员就从动作结果里看到原因，不用去日志里找。
  const kind = bc ? 'run_bc' : 'run_rl'
  const holder = bc ? runBcLockHolder(course) : runRlLockHolder(course)
  if (holder)
    return done(
      false,
      `${kind} 锁被 PID ${holder} 持有（${path.join('nn-training', lockName(course, kind))}）` +
        '——先停止在跑的那一份训练（或删除该锁文件）',
    )
  const prep = prepareCourseForSharedTrainer(cfg, course, ctx)
  // 旧形状（每课一个 trainer 进程）**显式换代接管**：存活的会被停掉并清账，死条目的账也清
  // （与 hub/隧道同规，见 stack/hub.ts::supersedeLegacyInstances）。
  const superseded = await supersedeLegacyInstances('trainingLoop')
  const spec = trainerServeSpec(prep.cfg, venv)
  const baseline = (() => {
    try {
      return statSync(spec.log).size
    } catch {
      return 0
    }
  })()
  const r = launchSpec(spec)
  saveAnyComponent('trainingLoop', '', {
    pid: r.pid,
    course: '',
    slot: 0,
    entry: TRAINER_SERVE_ENTRY,
    // 「模式」对共享 trainer 是**每课**的（见 prepare ③）：这里记的是最近一次启动所用的模式，
    // 每课的真实裁决在 `courses.<课>.remote_transport`。
    mode: ctx.trainerPpo,
    log: spec.log,
  })
  monitorTouch()
  const detail = [
    ...prep.notes,
    ...(superseded.length > 0 ? [`旧形状 trainer 已换代接管：${superseded.join(', ')}`] : []),
  ]
  await waitUntil(
    async () => {
      if (!pidAlive(r.pid)) return true
      try {
        return statSync(spec.log).size > baseline
      } catch {
        return false
      }
    },
    20000,
    500,
  )
  if (!pidAlive(r.pid)) {
    // 启动即退出不许静默：往共享日志追加失败标记（含尾日志）后再清账，否则只剩本次响应里的
    // 临时 tail，刷新即丢（2026-09-08 vk1 事故的同一条规矩）。
    try {
      appendFileSync(
        spec.log,
        `\n[console] ${new Date().toISOString()} 共享 trainer 启动即退出 (PID ${r.pid})——` +
          `启动失败，原因见上方日志尾段：\n` +
          tailLines(spec.log)
            .map((l) => `  | ${l}`)
            .join('\n') +
          '\n',
        'utf-8',
      )
    } catch {
      /* best-effort */
    }
    clearAnyComponent('trainingLoop', '')
    return done(false, `共享 trainer 启动即退出 (PID ${r.pid})`, [
      ...detail,
      ...tailLines(spec.log),
    ])
  }
  // §386：trainer 重启 = 停机条件消失 → 自动恢复停机状态。只解**本课**（共享 hub 上达令按课程
  // 下发：无课程 = 全课程，会把并行训练的其它课一起解停）。
  const rec = await markCloudHaltRecovered(prep.cfg, 'trainer 已重启（停机条件消失）', course)
  return done(
    true,
    `共享 trainer 已启动 (PID ${r.pid})——一个进程服务所有课程` +
      (rec.ok && rec.message.includes('解除') ? '；本课云端停机状态已自动恢复' : ''),
    detail,
  )
}

/** 启动单个组件（已在运行 = 幂等成功；依赖缺失 = ActionError/失败结果）。 */
export async function startComponent(key: Component, ctx: StartCtx): Promise<ActionResult> {
  // 键必须与 finally 释放的键同源（2026-09-14 事故：guard 用按课键、release 用旧无课键
  // ⇒ 按课键永不释放 ⇒ 该课程组件的启动/停止/冒烟永久 409）。槽位归一（共享组件恒 `''`）
  // 也是同一件事的两半：归一前后两个键会让 guard 与 release 不同源。
  const bk = busyKey('start', key, scopeOf(key, ctx.course))
  guard(bk)
  try {
    const cfg = loadConfig()
    const venv = resolveVenvPython()

    switch (key) {
      case 'selfNode': {
        if (await selfNodeHealthy(cfg))
          return done(true, `self-node 已在运行 (port ${cfg.rl.agent_port})`)
        await stepSelfNode(cfg)
        return done(true, 'self-node 已启动')
      }
      case 'hubServer': {
        // 共享 hub（2026-09-18）：**不需要 course**——一个进程服务所有并行课程，课程表
        // 由它自己从盘上发现。以前这里要求课程，是因为 hub 是「每课一份」。
        const hubPort = sharedHubPort(cfg)
        // R3-2：hub 的每课模式是 **volatile**（重启回启动参数）⇒ 每次确认 hub 在跑之后
        // 都回灌控制台的离线/在线意图。两个分支都回灌：hub 也可能是被别处（手敲命令、
        // 别的终端）拉起来的，那时「已运行」这条早退路径同样需要把意图接回去。
        if (await hubServerHealthy(cfg)) {
          const note = await restoreCourseModesNote(cfg)
          return done(true, `hub-server 已在运行 (port ${hubPort})${note ? `；${note}` : ''}`)
        }
        await stepHubServer(cfg)
        const note = await restoreCourseModesNote(cfg)
        return done(
          true,
          `hub-server 已启动 (port ${hubPort}；服务所有课程)${note ? `；${note}` : ''}`,
        )
      }
      case 'cloudflared': {
        // 共享单隧道（指向共享 hub 端口；一条隧道服务所有课程）。
        const url = await stepCloudflared(cfg, false)
        return done(true, `隧道已就绪: ${url}`)
      }
      case 'localWorker': {
        if (!ctx.course) throw new ActionError('local-worker 需要 course（先在顶部设置课程）')
        validateCourseArg(ctx.course)
        const prev = entryOf('localWorker', ctx.course)
        if (prev?.pid && pidAlive(prev.pid))
          return done(true, `local-worker 已在运行 (PID ${prev.pid})`)
        const r = await startLocalWorker({ course: ctx.course, cfg, venv })
        return done(
          r.ready,
          r.ready
            ? `local-worker 已启动 (PID ${r.pid}, poll ${sharedHubUrl(cfg)})`
            : `local-worker 启动即退出 (PID ${r.pid})`,
          r.tail,
        )
      }
      case 'workerServe': {
        const course = ctx.course || 'smoke'
        const prev = entryOf('workerServe', course)
        // 幂等（与其它组件同规，2026-09-15）：已在运行则不动它。push 预设回落本机时
        // 也会把 workerServe 排进顺序（复用路径下它本来就活着）——旧实现无条件 kill+重起，
        // 会把正在跑 PPO job 的 server 当场打死。
        if (prev?.pid && pidAlive(prev.pid))
          return done(true, `本机伪 GPU 节点已在运行 (PID ${prev.pid}, ${prev.url ?? ''})`)
        // 端口级兜底：未登记但在服务的 worker_server 同样不抢端口（不重起、不 bind 失败）。
        const pushUrl0 = `http://127.0.0.1:${slotPort(cfg, course, 'push')}`
        if (await httpOk(`${pushUrl0}/ping`, cfg.rl.remote_token, 3000))
          return done(true, `本机 worker_server 已在服务 (${pushUrl0})`)
        const { pushUrl, servePid } = await startLocalWorkerServer({ course, cfg, venv })
        saveAnyComponent('workerServe', course, {
          pid: servePid,
          url: pushUrl,
          entry: 'nn-training/remote_worker_serve.py',
          course,
          slot: slotOf(cfg, course),
          log: workerServeSpec(cfg, venv, course).log,
        })
        monitorTouch()
        return done(true, `本机伪 GPU 节点就绪: ${pushUrl}`)
      }
      case 'trainingLoop': {
        // 共享 trainer（R3-5）：**一个进程服务所有课程**，BC 与 RL 走同一条路。
        // 课程在这里的作用是「开哪门课」（种子权重 / 账本发现 / 机器侧旋钮），不是「起哪个进程」。
        if (!ctx.course)
          throw new ActionError(
            'trainer 需要 course（先在顶部设置课程）——进程是共享的一份，但「开哪门课」由课程决定',
          )
        validateCourseArg(ctx.course)
        return await startSharedTrainer(cfg, venv, ctx)
      }
    }
  } catch (e) {
    if (e instanceof ActionError) throw e
    return done(false, `${COMPONENT_LABELS[key]} 启动失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(bk)
  }
}
