/** eval-bc-run.ts — 控制台「BC Epoch 表 evalA」的唯一启动点。
 *
 *  与 RL `evalA` 的形态一致：detached python 子进程 + 服务端按课程互斥 + 日志文件。
 *  真正的评估在 `nn-training/trainer/eval_bc_once.py`，它复用训练内 eval 的同一份
 *  `run_epoch_eval`（hub epoch 快照 → dispatch_bc_eval → bc_eval 账本）。
 *
 *  同一课程同时只允许一个手动 BC eval：eval 会向节点池派发整批任务，两个并发补评会
 *  互相挤占节点；busy 只在子进程退出/启动失败时释放，服务端重启会丢失该键（与 evalA
 *  的既有语义相同，不做额外持久化）。
 */

import { spawn } from 'child_process'
import { closeSync, mkdirSync, openSync } from 'fs'
import path from 'path'
import { loadConfig } from '../core/config'
import { warn } from '../core/log'
import { NN_TRAINING, REPO_ROOT, tmpCourseDir } from '../core/paths'
import { sharedHubUrl } from '../core/slots'
import { busy } from './actions'
import { resolveRunPython } from './run-python'

/** BC 手动 eval 的互斥键（按课程，不与 RL 的 `eval:A` 共用）。 */
export function evalBcBusyKey(course: string): string {
  return `eval:bc:${course}`
}

/** eval 日志（`tmp/<课程>/evalBc.log`）。 */
export function evalBcLogPath(course: string): string {
  return path.join(tmpCourseDir(course), 'evalBc.log')
}

/** 一次性 BC eval 进程的 argv（纯函数，测试不真起 python）。 */
export function evalBcArgs(course: string, it: number, epoch: number, hubUrl: string): string[] {
  const argv = [
    path.join(REPO_ROOT, 'nn-training', 'trainer', 'eval_bc_once.py'),
    '--course',
    course,
    '--it',
    String(it),
    '--epoch',
    String(epoch),
  ]
  if (hubUrl) argv.push('--hub-url', hubUrl)
  return argv
}

export interface EvalBcLaunch {
  ok: boolean
  message: string
  pid?: number
  /** busy = 该课程已有 eval；error = 配置/spawn 失败（路由回 500）。 */
  code?: 'busy' | 'error'
}

/** 该课程是否有手动 BC eval 在跑（`/api/bcEpochs` 回给按钮禁用态）。 */
export function evalBcRunning(course: string): boolean {
  return busy.has(evalBcBusyKey(course))
}

/** 启动一个 `(it, epoch)` 的 BC 干净评估；返回「已启动」，成败由账本/log 决定。 */
export function launchEvalBc(course: string, it: number, epoch: number): EvalBcLaunch {
  const key = evalBcBusyKey(course)
  if (busy.has(key)) {
    return {
      ok: false,
      code: 'busy',
      message: `${course} 已有 BC eval 在跑——同一课程同时只允许一个，请等它结束`,
    }
  }

  let hubUrl = ''
  try {
    const cfg = loadConfig()
    const rl = (cfg.rl ?? {}) as { remote_hub_url?: unknown }
    hubUrl = String(rl.remote_hub_url ?? '').trim() || sharedHubUrl(cfg)
  } catch (e) {
    return {
      ok: false,
      code: 'error',
      message: `读取 rl-config 失败：${e instanceof Error ? e.message : String(e)}`,
    }
  }

  const logFile = evalBcLogPath(course)
  try {
    mkdirSync(path.dirname(logFile), { recursive: true })
  } catch (e) {
    return {
      ok: false,
      code: 'error',
      message: `创建 eval 日志目录失败：${e instanceof Error ? e.message : String(e)}`,
    }
  }

  const { python, env } = resolveRunPython()
  let out: number
  try {
    out = openSync(logFile, 'a')
  } catch (e) {
    return {
      ok: false,
      code: 'error',
      message: `打开 eval 日志失败：${e instanceof Error ? e.message : String(e)}`,
    }
  }

  let child
  try {
    child = spawn(python, evalBcArgs(course, it, epoch, hubUrl), {
      cwd: NN_TRAINING,
      env: { ...process.env, ...env },
      detached: true,
      windowsHide: true,
      stdio: ['ignore', out, out],
    })
  } catch (e) {
    closeSync(out)
    return {
      ok: false,
      code: 'error',
      message: `启动 BC eval 失败：${e instanceof Error ? e.message : String(e)}`,
    }
  }
  closeSync(out)

  busy.add(key)
  let released = false
  const release = (): void => {
    if (released) return
    released = true
    busy.delete(key)
  }
  child.once('error', (e) => {
    release()
    warn(`[evalBc] ${course} it${it} ep${epoch} 启动失败：${e.message}`)
  })
  child.once('exit', (code, signal) => {
    release()
    if (code === 0) {
      warn(`[evalBc] ${course} it${it} ep${epoch} 进程退出（0）；结果看账本 bc_eval`)
    } else {
      warn(
        `[evalBc] ${course} it${it} ep${epoch} 进程退出（code=${code} signal=${signal ?? '—'}）；看 ${logFile}`,
      )
    }
  })
  child.unref()

  return {
    ok: true,
    pid: child.pid,
    message: `BC eval 已启动 it${it} ep${epoch}——成败看 bc_eval 账本（日志 ${logFile}）`,
  }
}
