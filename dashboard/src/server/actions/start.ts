/** start.ts — 组件启动（含 BC 循环启动与 run_bc 锁检查）。 */
import { appendFileSync, existsSync, readFileSync, statSync } from 'fs'
import path from 'path'
import { loadConfig, validateCourseArg } from '../../core/config'
import { httpOk, pidAlive, waitUntil } from '../../core/net'
import { LOG_DIR, REPO_ROOT } from '../../core/paths'
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
import { isBcCourse, seedWeightsFromBc } from '../../stack/courses'
import {
  hubServerHealthy,
  selfNodeHealthy,
  stepCloudflared,
  stepHubServer,
  stepSelfNode,
} from '../../stack/hub'
import { startLocalWorker } from '../../stack/local-worker'
import { startLocalWorkerServer } from '../../stack/push'
import {
  BC_LOOP_ENTRY,
  bcLoopSpec,
  TRAINING_LOOP_ENTRY,
  trainingLoopSpec,
  workerServeSpec,
} from '../../stack/specs'
import { entryOf, markCloudHaltRecovered } from './cloud-halt'
import { ConsoleState } from './console-state'
import { restoreCourseModesNote } from './course-mode'
import { COMPONENT_LABELS, runRlLockHolder, tailLines } from './labels'
import { ActionError, ActionResult, busyKey, done, guard, release } from './result'

// ────────────────────────── 组件启动 ──────────────────────────

export interface StartCtx {
  course: string
  /** trainer 模式（pull/push → 云端 worker；local → 本机独立 localWorker 的 pull 模式）。 */
  trainerPpo: ConsoleState['trainerPpo']
  /** 显式 REMOTE_PUSH_NODE（仅冒烟/本机伪 GPU；真实 Push 走 rl-config gpu_push 节点，
   *  不注入 env——env 会强制 remote_token，覆盖用户填写的 authKey）。 */
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

/** 启动 BC 编排器（run_bc.py）：BC 课程的 trainingLoop 分支——无 BC 种子权重播种
 *  （BC 无 warm-start）、锁名 run_bc、spec = bcLoopSpec（REMOTE_PUSH_NODE 注入
 *  courses.<课>.push_node_url，smoke 由动作层 --smoke 预演覆盖）。 */
async function startBcLoop(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
  course: string,
  trainerPpo: ConsoleState['trainerPpo'],
): Promise<ActionResult> {
  const prev = entryOf('trainingLoop', course)
  if (pidAlive(prev?.pid)) return done(false, `BcLoop 已在运行 (PID ${prev!.pid})——先停止再启动`)
  const holder = runBcLockHolder(course)
  if (holder)
    return done(
      false,
      `run_bc 锁被 PID ${holder} 持有（${path.join('nn-training', lockName(course, 'run_bc'))}）` +
        '——先停止在跑训练（或删除该锁文件）',
    )
  const trainLog = path.join(LOG_DIR, course, 'training-loop.log')
  const baseline = (() => {
    try {
      return statSync(trainLog).size
    } catch {
      return 0
    }
  })()
  const spec = bcLoopSpec(cfg, {
    course,
    ppo: trainerPpo,
    pushNodeUrl: cfg.courses?.[course]?.push_node_url,
    hubUrl: localHubUrl(cfg, course, trainerPpo),
    venv,
  })
  const r = launchSpec(spec)
  saveAnyComponent('trainingLoop', course, {
    pid: r.pid,
    course,
    slot: slotOf(cfg, course),
    entry: BC_LOOP_ENTRY,
    mode: trainerPpo,
    pushNodeUrl: cfg.courses?.[course]?.push_node_url,
    log: trainLog,
  })
  monitorTouch()
  await waitUntil(
    async () => {
      if (!pidAlive(r.pid)) return true
      try {
        return statSync(trainLog).size > baseline
      } catch {
        return false
      }
    },
    20000,
    500,
  )
  if (!pidAlive(r.pid)) {
    try {
      appendFileSync(
        trainLog,
        `\n[console] ${new Date().toISOString()} BcLoop 启动即退出 (PID ${r.pid})——` +
          `启动失败，原因见上方日志尾段：\n` +
          tailLines(trainLog)
            .map((l) => `  | ${l}`)
            .join('\n') +
          '\n',
        'utf-8',
      )
    } catch {
      /* best-effort */
    }
    clearAnyComponent('trainingLoop', course)
    return done(false, `BcLoop 启动即退出 (PID ${r.pid})`, tailLines(trainLog))
  }
  return done(true, `BcLoop 已启动 (PID ${r.pid}, ppo=${trainerPpo})`)
}

/** local 模式（本机独立 worker）下 trainer 的 pull 目标 = 本机 hub（槽位算术取端口）。
 *
 *  **不改 rl-config 的 remote_hubs**（刻意）：那一个键同时是「pull preset 的隧道 URL」的
 *  家，而 stepCloudflared 复用已有隧道时**不会重写**它——本机 hub 写进去就会把 pull
 *  preset 悄悄改成打本机 hub。显式 --remote-hub-url 压过配置（run_rl 既有语义），
 *  既不动配置也能保证本机 worker 是唯一执行面。 */
function localHubUrl(
  cfg: RlConfig,
  course: string,
  mode: ConsoleState['trainerPpo'],
): string | undefined {
  void course // 共享 hub：地址与课程无关
  return mode === 'local' ? sharedHubUrl(cfg) : undefined
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
        if (!ctx.course) throw new ActionError('trainer 需要 course（先在顶部设置课程）')
        validateCourseArg(ctx.course)
        // BC 课程 → run_bc.py 编排器（无 BC 种子播种；run_bc 锁）
        if (isBcCourse(ctx.course)) return await startBcLoop(cfg, venv, ctx.course, ctx.trainerPpo)
        const prevTl = entryOf('trainingLoop', ctx.course)
        if (pidAlive(prevTl?.pid))
          return done(false, `TrainingLoop 已在运行 (PID ${prevTl!.pid})——先停止再启动`)
        const holder = runRlLockHolder(ctx.course)
        if (holder)
          return done(
            false,
            `run_rl 锁被 PID ${holder} 持有（${path.join('nn-training', lockName(ctx.course, 'run_rl'))}）` +
              '——先停止在跑训练（或删除该锁文件）',
          )

        const trajDir = path.join(REPO_ROOT, 'tmp', ctx.course)
        const weightsPath = path.join(trajDir, 'weights.json')
        if (!existsSync(weightsPath)) {
          try {
            seedWeightsFromBc(ctx.course, weightsPath)
          } catch (e) {
            return done(false, e instanceof Error ? e.message : String(e))
          }
        }
        const trainLog = path.join(LOG_DIR, ctx.course, 'training-loop.log')
        const baseline = (() => {
          try {
            return statSync(trainLog).size
          } catch {
            return 0
          }
        })()
        const spec = trainingLoopSpec(cfg, {
          course: ctx.course,
          ppo: ctx.trainerPpo,
          // 真实 Push：不注入 REMOTE_PUSH_NODE（见 StartCtx.pushNodeUrl）。
          // Python 从 rl-config nodes[].gpu_push 读 URL+authKey（控制台 configurePush 已写回）。
          pushNodeUrl: ctx.pushNodeUrl,
          // local 模式：指名本机 hub（worker 是独立进程，训练器只负责发布+等待）
          hubUrl: localHubUrl(cfg, ctx.course, ctx.trainerPpo),
          venv,
          remoteDegrade: ctx.remoteDegrade,
        })
        const r = launchSpec(spec)
        saveAnyComponent('trainingLoop', ctx.course, {
          pid: r.pid,
          course: ctx.course,
          slot: slotOf(cfg, ctx.course),
          entry: TRAINING_LOOP_ENTRY,
          mode: ctx.trainerPpo,
          pushNodeUrl: ctx.pushNodeUrl ?? cfg.courses?.[ctx.course]?.push_node_url,
          remoteDegrade: !!ctx.remoteDegrade,
          log: trainLog,
        })
        monitorTouch()
        await waitUntil(
          async () => {
            if (!pidAlive(r.pid)) return true
            try {
              return statSync(trainLog).size > baseline
            } catch {
              return false
            }
          },
          20000,
          500,
        )
        if (!pidAlive(r.pid)) {
          // §380：启动即退出不许静默——往日志文件追加失败标记（含尾日志）后再清账，
          // 否则只剩 startComponent 响应里的临时 tail，刷新即丢（2026-09-08 vk1 事故）。
          try {
            appendFileSync(
              trainLog,
              `\n[console] ${new Date().toISOString()} ${COMPONENT_LABELS[key]} 启动即退出` +
                ` (PID ${r.pid})——启动失败，原因见上方日志尾段：\n` +
                tailLines(trainLog)
                  .map((l) => `  | ${l}`)
                  .join('\n') +
                '\n',
              'utf-8',
            )
          } catch {
            /* best-effort */
          }
          clearAnyComponent('trainingLoop', ctx.course)
          return done(false, `TrainingLoop 启动即退出 (PID ${r.pid})`, tailLines(trainLog))
        }
        // §386：TrainingLoop 重启 = 停机条件消失 → 自动恢复停机状态（hub resume + recovered）。
        // 只解**本课**的停机态（共享 hub 上达令是按课程下发的：无课程 = 全课程，那会把
        // 并行训练的其它课程一起解停）
        const rec = await markCloudHaltRecovered(
          cfg,
          'TrainingLoop 已重启（停机条件消失）',
          ctx.course,
        )
        return done(
          true,
          rec.ok && rec.message.includes('解除')
            ? `TrainingLoop 已启动 (PID ${r.pid}, ppo=${ctx.trainerPpo})；云端停机状态已自动恢复`
            : `TrainingLoop 已启动 (PID ${r.pid}, ppo=${ctx.trainerPpo})`,
        )
      }
    }
  } catch (e) {
    if (e instanceof ActionError) throw e
    return done(false, `${COMPONENT_LABELS[key]} 启动失败: ${e instanceof Error ? e.message : e}`)
  } finally {
    release(bk)
  }
}
