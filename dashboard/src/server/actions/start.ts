/** start.ts — 组件启动（**只起进程**；课程生命周期见 `course-lifecycle.ts`）。 */
import { appendFileSync, existsSync, readFileSync, statSync } from 'fs'
import path from 'path'
import { loadConfig } from '../../core/config'
import { pidAlive, waitUntil } from '../../core/net'
import { launchSpec } from '../../core/proc'
import { clearAnyComponent, saveAnyComponent, scopeOf } from '../../core/registry'
import { monitorTouch } from '../../core/reload-touch'
import {
  lockName,
  lockPathFor,
  sharedHubUrl,
  sharedHubPort,
  validateCourseName,
} from '../../core/slots'
import type { Component, RlConfig } from '../../core/types'
import { resolveVenvPython } from '../../core/venv'
import {
  hubServerHealthy,
  selfNodeHealthy,
  stepCloudflared,
  stepHubServer,
  stepSelfNode,
  supersedeLegacyInstances,
} from '../../stack/hub'
import { startLocalWorker } from '../../stack/local-worker'
import { TRAINER_SERVE_ENTRY, trainerServeSpec } from '../../stack/specs'
import { entryOf, markCloudHaltRecovered } from './cloud-halt'
import { restoreCourseModesNote } from './course-mode'
import { COMPONENT_LABELS, tailLines } from './labels'
import { ActionError, ActionResult, busyKey, done, guard, release } from './result'

// ────────────────────────── 组件启动 ──────────────────────────

export interface StartCtx {
  course: string
  /** 显式 REMOTE_PUSH_NODE（仅冒烟预演：把本机伪节点注入 env；真实执行面走
   *  `rl.hub_push` + 登记节点，由训练侧自己解析）。
   *
   *  ★ 共享 trainer 不消费它（`trainerServeSpec` 不给 `REMOTE_PUSH_NODE`）；保留字段是因为
   *  预演（`train-smoke.ts`）仍用它把伪节点注入 env。 */
  pushNodeUrl?: string
  /** T7：远端连败是否 opt-in 降级本机 PPO（默认 false = ABORT）。
   *
   *  ★ 2026-09-20：**共享 trainer 已不消费它** —— 它是**课程级**旋钮（落
   *  `courses.<课>.remote_degrade_after`），随「开课」走（`course-lifecycle.ts`）。
   *  保留字段是因为预演路径（`train-smoke.ts`）仍按旧形状传它。 */
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

/** **启动共享 trainer**（`run_rl_cluster.py --serve`）——一个进程服务所有课程（R3-5）。
 *
 *  为什么不再是「每课一个 `run_rl.py --course`」：用户口径「hubserver/trainingloop/selfNode/
 *  cloudflared 都只需要开一个进程，就能同时支持所有并行训练课程」。R2d 已造好单进程驱动者
 *  （按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 / 暂停恢复），R3-4 又让同一个进程能带 BC 课，
 *  而 BC 与 RL **共用 `trainingLoop` 这一个角色键** —— 两个进程并存是旧形状。
 *
 *  **不给 `--courses`（发现模式）**：课程 = 「`tmp/<课>/training_log.jsonl` 存在」这个文件系统
 *  事实（与 hub `--discover` 同一原则），所以「先起 trainer、后加课」不需要重启进程。
 *
 *  ★ **本步骤不碰课程**（2026-09-20 用户指令：「服务进程启动不应与课程绑定。进程启动时不要
 *  自动开启课程训练」）：课程准备（播权重 / 建账本 + `remote-jobs/` / 写课程旋钮 / 置 hub 模式）
 *  整体迁到 `actions/course-lifecycle.ts::openCourse`。之前它挂在这里的代价是**时序错位**：
 *  置 hub 模式发生在课程还不存在（hub 扫盘才发现课程）时，hub 回
 *  `需要合法 course（[]）`——2026-09-20 那条「启动训练中断于 trainingLoop」的实测根因之一。
 *
 *  ⚠ 行为语义（操作员必须知道）：**停止 trainer = 停掉所有课程的训练**。停单门课用「开课/停课」
 *  入口（停课 = 暂停意图 + 该课 hub 置离线；队列与账本一个字不动）。
 *
 *  BC 课与 RL 课走**同一条路**（R3-4 起 `--serve` 按课程种类选引擎/粒度/指针）：BC 不再有
 *  单独的 `run_bc.py` 启动分支。 */
async function startSharedTrainer(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
): Promise<ActionResult> {
  // 槽恒 `''`：共享实例不属于任何单门课（`entryOf` 内部走 scopeOf，读面自动同源）
  const prev = entryOf('trainingLoop')
  if (pidAlive(prev?.pid))
    return done(true, `共享 trainer 已在运行 (PID ${prev!.pid})——一个进程服务所有课程`, [
      '★ 「开哪门课」用课程入口（顶部课程选择旁的 开课/停课）：进程与课程是两件事。',
    ])
  const clusterHolder = runClusterLockHolder()
  if (clusterHolder)
    return done(
      false,
      `共享 trainer 单实例锁被 PID ${clusterHolder} 持有` +
        `（${path.join('nn-training', lockName('', 'run_cluster'))}）——一个进程服务所有课程，` +
        '先停掉它（或删除该锁文件）',
    )
  // 旧形状（每课一个 trainer 进程）**显式换代接管**：存活的会被停掉并清账，死条目的账也清
  // （与 hub/隧道同规，见 stack/hub.ts::supersedeLegacyInstances）。
  const superseded = await supersedeLegacyInstances('trainingLoop')
  const spec = trainerServeSpec(cfg, venv)
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
    log: spec.log,
    // 启动时刻：接管对账（server.ts::reconcileWatch）判「跑的是不是旧码」靠它。
    startedAt: Date.now(),
  })
  monitorTouch()
  const detail =
    superseded.length > 0 ? [`旧形状 trainer 已换代接管：${superseded.join(', ')}`] : []
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
  // §386：trainer 重启 = 停机条件消失 → 自动恢复停机状态。
  // **无课程 = 全课程**（刻意）：重启的是**共享** trainer，它是所有课程共同的停机条件——
  // 被停掉的正是整个调度器，只解「当前查看的那门」会让其它课替它背锅（旧形状按课启动时
  // 才需要按课解停，那时一个进程只服务一门课）。
  const rec = await markCloudHaltRecovered(cfg, 'trainer 已重启（停机条件消失）')
  return done(
    true,
    `共享 trainer 已启动 (PID ${r.pid})——一个进程服务所有课程` +
      (rec.ok && rec.message.includes('解除') ? '；云端停机状态已自动恢复' : ''),
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
        // 共享本机 worker（2026-09-19）：**一个进程服务所有课程**——它领到哪门课的 job 就干哪门
        // 课的活（job 自带课程快照，结果按 job_id 回家）。故这里**不要求 course**：
        // 「开哪门课」对本组件不是一个概念（旧形状的每课实例由 startLocalWorker 换代接管收掉）。
        const prev = entryOf('localWorker')
        if (pidAlive(prev?.pid))
          return done(true, `local-worker 已在运行 (PID ${prev!.pid})——一个进程服务所有课程`)
        const r = await startLocalWorker({ cfg, venv })
        const taken =
          r.superseded.length > 0
            ? [`旧形状的每课 worker 已换代接管：${r.superseded.join(', ')}`]
            : []
        return done(
          r.ready,
          r.ready
            ? `local-worker 已启动 (PID ${r.pid}, poll ${sharedHubUrl(cfg)}；服务所有课程)`
            : `local-worker 启动即退出 (PID ${r.pid})`,
          [...r.tail, ...taken],
        )
      }
      case 'trainingLoop': {
        // 共享 trainer（R3-5）：**一个进程服务所有课程**，BC 与 RL 走同一条路。
        // ★ **不需要课程**（2026-09-20）：「开哪门课」是独立入口的事
        // （`course-lifecycle.ts::{openCourse,stopCourse}`），本步骤只起进程——
        // 启动阶段不再有任何按课程的副作用（旧形状把账本/权重/旋钮/hub 模式挂在这里，
        // 于是「起进程」被迫先选一门课，置 hub 模式还会撞上「hub 还不认识这门课」）。
        return await startSharedTrainer(cfg, venv)
      }
    }
  } catch (e) {
    if (e instanceof ActionError) throw e
    return done(false, `${COMPONENT_LABELS[key]} 启动失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(bk)
  }
}
