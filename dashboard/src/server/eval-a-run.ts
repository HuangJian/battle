/** eval-a-run.ts — `evalA`（课程 A 层干净评估）的**唯一启动点**。
 *
 *  两个调用方必须完全同源：控制台上的 evalA 按钮，和「导入了训练产物后自动评估」。
 *  它们共享的不仅是命令，还有**互斥键**（`eval:A`）——两处各写一份，就会出现「按钮点下去
 *  说在跑、导入那边不知道」的情形，两个 evalA 同时写同一份 eval_log。这里把 spawn/日志/
 *  退出释放互斥三件事收在一处。
 *
 *  ★ 2026-10-06（用户报障「某 it 的 evalA 跑过后，其它 it 再点按键无反应、要刷新页面」）：
 *  **忙时入队，不再 409 拒**。单槽语义不变——任一时刻恰有一个子进程；互斥键从第一单起跑
 *  持有到**队列排空**（补评/导入侧看到的「忙」照旧 ⇒ 不会并发插单）。变的只是「后到的点击
 *  排到队尾、跑完自动接棒」。**为什么排队住服务端而不是前端重试**：队列是操作状态，
 *  不是画面状态（§2.5 表现层可弃）；前端重试在刷新 / 多标签 / 两个面板（Hero 与指标表）下
 *  会各排一份，服务端这一份才是唯一事实。
 *
 *  返回消息一律「已启动 / 已排队」：evalA 是 detached 长任务（几十秒到几分钟），结果从
 *  `tmp/<课程>/eval_log.jsonl` 回填控制台表格，不在 HTTP 响应里。
 */

import { closeSync, mkdirSync, openSync } from 'fs'
import { spawn } from 'child_process'
import path from 'path'
import { REPO_ROOT } from '../core/paths'
import { warn } from '../core/log'
import { busy } from './actions'
import { resolveRunPython } from './run-python'

/** evalA 正在跑的互斥键（动作层 409 与导入自动评估共用）。 */
export const EVAL_A_BUSY_KEY = 'eval:A'

/** evalA 的日志文件（`tmp/<课程>/evalA.log`；与既有约定同址）。 */
export function evalALogPath(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, 'evalA.log')
}

/** 排队上限：单次评估几十秒–几分钟，8 单已是十几分钟到半小时的积压——再多是误点/脚本刷，
 *  响亮拒（而不是默默吞掉或无限攒着）。 */
export const EVAL_A_QUEUE_CAP = 8

export interface EvalALaunch {
  ok: boolean
  message: string
  pid?: number
  /** 本次调用没有立刻起进程，而是排进了队列（含「同一单已在跑/在队」的去重命中）。 */
  queued?: boolean
  /** 失败类别：busy = 单槽被占；full = 队列已满。路由据此回 409（等一等就有位置），
   *  其余失败（spawn 抛）才是 500。 */
  code?: 'busy' | 'full'
}

/** 启动选项：`baseline` = it0 基线（离线开课自动补跑，见 course-lifecycle）。 */
export interface EvalALaunchOpts {
  /** `--baseline`：python 侧评课程 bc 起点冻结权重 W(0)（`--iter` 恒 0），`ckpt` 可留空。 */
  baseline?: boolean
}

/** 一单（队列的调度单位）。 */
export interface EvalAJob {
  course: string
  ckpt: string
  iter: number
  opts: EvalALaunchOpts
}

/** evalA 一次性进程的 argv（纯函数，便于测试——同 `taskBundleArgs`）。
 *
 *  `ckpt` 空串 = 交给 python 侧按模式取（baseline → 课程 `bc` 起点冻结权重，禁取 live
 *  `out`：out 每轮被覆盖，重启后补派会把新权重读数写进 it0 槽，2026-09-25 实测）：
 *  **空就不传这个 flag**——`--ckpt ''` 到 python 手里 `Path("")` 是 `.`（存在！），
 *  会被当权重算指纹。 */
export function evalAArgs(
  course: string,
  ckpt: string,
  iter: number,
  opts: EvalALaunchOpts = {},
): string[] {
  const argv = [
    path.join(REPO_ROOT, 'nn-training', 'trainer', 'eval_a_once.py'),
    '--course',
    course,
  ]
  if (ckpt) argv.push('--ckpt', ckpt)
  argv.push('--iter', String(iter), '--bun', 'bun')
  if (opts.baseline) argv.push('--baseline')
  return argv
}

/** 真启动（唯一副作用点）：起子进程，返回 pid。`done` **恰被调用一次**（子进程退出，或
 *  启动失败时由调度核心代调）⇒ 队列放行下一单。测试注入替身 ⇒ 不真起 python。 */
export type EvalASpawn = (job: EvalAJob, done: () => void) => { pid?: number }

export interface EvalAQueue {
  /** 入队：立刻能起就起；在跑就排到队尾（同单去重、超上限响亮拒）。 */
  enqueue(job: EvalAJob): EvalALaunch
  /** 只读快照（探针/测试用；`pending` 是副本）。 */
  state(): { active: EvalAJob | null; pending: EvalAJob[] }
}

/** 一单的短标签（消息里用）。 */
function evalALabel(job: EvalAJob): string {
  return job.opts.baseline ? 'it0 基线' : `it${job.iter}`
}

/** 同一单 = 同课同 iter 同 baseline 模式；ckpt 路径不算（它由 iter 派生，同号必同源）。 */
function sameJob(a: EvalAJob, b: EvalAJob): boolean {
  return a.course === b.course && a.iter === b.iter && !!a.opts.baseline === !!b.opts.baseline
}

/** 串行队列（可注入 spawner ⇒ 调度逻辑无头可测）。 */
export function createEvalAQueue(spawnJob: EvalASpawn): EvalAQueue {
  let active: EvalAJob | null = null
  const pending: EvalAJob[] = []

  function start(job: EvalAJob): EvalALaunch {
    active = job
    busy.add(EVAL_A_BUSY_KEY)
    let released = false
    const release = (): void => {
      // 'error' 与 'exit' 都可能到（Node 两种失败形态）——双触发不得多放行一单。
      if (released) return
      released = true
      active = null
      if (pending.length === 0) busy.delete(EVAL_A_BUSY_KEY)
      const next = pending.shift()
      if (next) start(next)
    }
    try {
      const { pid } = spawnJob(job, release)
      return {
        ok: true,
        message:
          `evalA 已启动 ${evalALabel(job)}` +
          `（课程干净评估 → eval_log，完成后读数自动回填；日志 tmp/${job.course}/evalA.log）`,
        pid,
      }
    } catch (e) {
      // 失败也收尾：下一单继续（在队单没有 HTTP 响应可带原因 ⇒ 至少留一行日志）。
      warn(`[evalA] ${evalALabel(job)} 启动失败：${e instanceof Error ? e.message : String(e)}`)
      release()
      return { ok: false, message: `evalA 启动失败：${e instanceof Error ? e.message : String(e)}` }
    }
  }

  return {
    enqueue(job: EvalAJob): EvalALaunch {
      if (active && sameJob(active, job))
        return {
          ok: true,
          queued: true,
          message: `evalA ${evalALabel(job)} 已在运行（不重复启动）`,
        }
      if (pending.some((j) => sameJob(j, job)))
        return {
          ok: true,
          queued: true,
          message: `evalA ${evalALabel(job)} 已在队列（不重复入队）`,
        }
      // 键被**本队列之外**的持有者占着（正常路径不存在；异常/测试路径）：起不了，也不排
      // （不知道它何时释放，排队会永久卡住）。
      if (!active && busy.has(EVAL_A_BUSY_KEY))
        return { ok: false, code: 'busy', message: 'evalA 已在运行（单槽）——等它跑完再点' }
      if (active) {
        if (pending.length >= EVAL_A_QUEUE_CAP) {
          return {
            ok: false,
            code: 'full',
            message: `evalA 队列已满（上限 ${EVAL_A_QUEUE_CAP}）——等前面跑完再点`,
          }
        }
        pending.push(job)
        return {
          ok: true,
          queued: true,
          message:
            `evalA ${evalALabel(job)} 已排队（前面 ${pending.length} 个：` +
            `${evalALabel(active)} 正在跑；跑完自动开始、读数自动回填）`,
        }
      }
      return start(job)
    },
    state: () => ({ active, pending: [...pending] }),
  }
}

/** 真 spawner：detach + 日志重定向；子进程退出即 `done()`（互斥键由队列释放）。 */
function spawnEvalAJob(job: EvalAJob, done: () => void): { pid?: number } {
  const logFile = evalALogPath(job.course)
  const { python, env } = resolveRunPython()
  mkdirSync(path.dirname(logFile), { recursive: true })
  const out = openSync(logFile, 'a')
  const finish = (): void => {
    try {
      closeSync(out)
    } catch {
      /* 已关闭 */
    }
    done()
  }
  try {
    const child = spawn(python, evalAArgs(job.course, job.ckpt, job.iter, job.opts), {
      cwd: path.join(REPO_ROOT, 'nn-training'),
      detached: true,
      stdio: ['ignore', out, out],
      windowsHide: true,
      env: { ...process.env, ...env },
    })
    child.on('exit', finish)
    child.on('error', finish)
    child.unref()
    return { pid: child.pid }
  } catch (e) {
    try {
      closeSync(out)
    } catch {
      /* 已关闭 */
    }
    throw e
  }
}

/** 进程内唯一队列（调度核心的接线在此一处）。 */
const evalAQueue = createEvalAQueue(spawnEvalAJob)

/** 起一次 evalA（忙时入队——见文件头；互斥键在队列排空时释放）。 */
export function launchEvalA(
  course: string,
  ckpt: string,
  iter: number,
  opts: EvalALaunchOpts = {},
): EvalALaunch {
  return evalAQueue.enqueue({ course, ckpt, iter, opts })
}
