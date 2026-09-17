/** eval-a-run.ts — `evalA`（课程 A 层干净评估）的**唯一启动点**。
 *
 *  两个调用方必须完全同源：控制台上的 evalA 按钮，和「导入了训练产物后自动评估」。
 *  它们共享的不仅是命令，还有**互斥键**（`eval:A`）——两处各写一份，就会出现「按钮点下去
 *  说在跑、导入那边不知道」的情形，两个 evalA 同时写同一份 eval_log。这里把 spawn/日志/
 *  退出释放互斥三件事收在一处。
 *
 *  返回消息一律「已启动」：evalA 是 detached 长任务（几十秒到几分钟），结果从
 *  `tmp/<课程>/eval_log.jsonl` 回填控制台表格，不在 HTTP 响应里。
 */

import { closeSync, mkdirSync, openSync } from 'fs'
import { spawn } from 'child_process'
import path from 'path'
import { REPO_ROOT } from '../core/paths'
import { busy } from './actions'
import { resolveRunPython } from './run-python'

/** evalA 正在跑的互斥键（动作层 409 与导入自动评估共用）。 */
export const EVAL_A_BUSY_KEY = 'eval:A'

/** evalA 的日志文件（`tmp/<课程>/evalA.log`；与既有约定同址）。 */
export function evalALogPath(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, 'evalA.log')
}

export interface EvalALaunch {
  ok: boolean
  message: string
  pid?: number
}

/** 起一次 evalA（detach；互斥键在子进程退出时释放）。 */
export function launchEvalA(course: string, ckpt: string, iter: number): EvalALaunch {
  if (busy.has(EVAL_A_BUSY_KEY)) return { ok: false, message: 'evalA 已在运行' }
  busy.add(EVAL_A_BUSY_KEY)
  const logFile = evalALogPath(course)
  const { python, env } = resolveRunPython()
  try {
    mkdirSync(path.dirname(logFile), { recursive: true })
    const out = openSync(logFile, 'a')
    const child = spawn(
      python,
      [
        path.join(REPO_ROOT, 'nn-training', 'rl', 'eval_a_once.py'),
        '--course',
        course,
        '--ckpt',
        ckpt,
        '--iter',
        String(iter),
        '--bun',
        'bun',
      ],
      {
        cwd: path.join(REPO_ROOT, 'nn-training'),
        detached: true,
        stdio: ['ignore', out, out],
        windowsHide: true,
        env: { ...process.env, ...env },
      },
    )
    const release = () => {
      busy.delete(EVAL_A_BUSY_KEY)
      try {
        closeSync(out)
      } catch {
        /* 已关闭 */
      }
    }
    child.on('exit', release)
    child.on('error', release)
    child.unref()
    return {
      ok: true,
      message: `evalA 已启动 it${iter}（课程干净评估 → eval_log；日志 tmp/${course}/evalA.log）`,
      pid: child.pid,
    }
  } catch (e) {
    busy.delete(EVAL_A_BUSY_KEY)
    return { ok: false, message: `evalA 启动失败：${e instanceof Error ? e.message : String(e)}` }
  }
}
