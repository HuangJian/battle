/** local-worker.ts — 本机独立 PPO worker（`remote_worker --poll 本课 hub`）的启动步骤。
 *
 *  为什么独立成模块：本机 PPO 从 "run_rl 进程内 `--ppo local`" 拆成**独立受管进程**
 *  （2026-09-15，与云端 worker 同一份代码/同一套协议），于是它像 hubServer 一样需要
 *  「spawn → 登记账本 → 等就绪 → 由监督器接管变更检测」。spec 构造集中在 specs.ts
 *  （控制台启动步骤与监督器重启共用），本层只做编排（与 push.ts 同分层）。
 */

import { readFileSync } from 'fs'
import { fail, log, ok, warn } from '../core/log'
import { httpOk, pidAlive, sleep } from '../core/net'
import { launchSpec } from '../core/proc'
import { saveAnyComponent } from '../core/registry'
import { monitorTouch } from '../core/reload-touch'
import { slotOf, slotPort } from '../core/slots'
import type { RlConfig } from '../core/types'
import { LOCAL_WORKER_ENTRY, localWorkerSpec } from './specs'

export interface LocalWorkerCtx {
  /** 归属课程（job 队列 = 该课 hub，work 目录 per-course）。 */
  course: string
  /** 真实 rl-config（hub 端口/token/torch_threads 都在里面）。 */
  cfg: RlConfig
  /** 已就绪的 venv 解析结果。 */
  venv: { python: string; sitePackages: string }
}

export interface LocalWorkerResult {
  pid: number
  log: string
  /** false = 进程在就绪窗口内即退出（启动失败，调用方按失败上报并展示日志尾）。 */
  ready: boolean
  /** 就绪窗口内进程退出时的日志尾（失败证据；正常启动为空数组）。 */
  tail: string[]
}

/** 日志尾（本模块不从 server/actions 借 tailLines——那会把 stack 反向依赖到 actions）。 */
function logTail(p: string, n = 6): string[] {
  try {
    return readFileSync(p, 'utf-8').split('\n').filter(Boolean).slice(-n)
  } catch {
    return []
  }
}

/** 启动本机 PPO worker：spawn + 登记账本 + 就绪窗口判活。
 *
 *  就绪判定 = **存活**（它是出站轮询者，没有任何 HTTP 端点可探）：worker 启动后即进入
 *  轮询循环，唯一的启动失败形态是进程立刻退出（venv/import/token 参数问题）。故给 1.5s
 *  窗口再判一次 pidAlive——秒退就响亮失败（否则组件卡会显示「运行中」，实际什么都没跑）。
 *  hub 未就绪只 WARN 不拦截：worker 会一直重试轮询（与云端 worker 断线重连同语义），
 *  先起 worker 后起 hub 也能自愈。 */
export async function startLocalWorker(ctx: LocalWorkerCtx): Promise<LocalWorkerResult> {
  const spec = localWorkerSpec(ctx.cfg, ctx.venv, ctx.course)
  const hubUrl = `http://127.0.0.1:${slotPort(ctx.cfg, ctx.course, 'hub')}`
  if (!(await httpOk(`${hubUrl}/ping`, ctx.cfg.rl.remote_token, 3000))) {
    warn(`本机 hub-server 未就绪（${hubUrl}）——worker 会持续轮询重试；建议先启动 hub-server`)
  }
  log(`启动本机 PPO worker (remote_worker --poll ${hubUrl})...`)
  const r = launchSpec(spec)
  saveAnyComponent('localWorker', ctx.course, {
    pid: r.pid,
    course: ctx.course,
    slot: slotOf(ctx.cfg, ctx.course),
    entry: LOCAL_WORKER_ENTRY,
    url: hubUrl,
    log: spec.log,
  })
  monitorTouch()
  await sleep(1500)
  const ready = pidAlive(r.pid)
  if (!ready) {
    fail(`本机 PPO worker 启动即退出 (PID ${r.pid})——见 ${spec.log}`)
    return { pid: r.pid, log: spec.log, ready, tail: logTail(spec.log) }
  }
  ok(`本机 PPO worker 已启动 (PID ${r.pid}, poll ${hubUrl})`)
  return { pid: r.pid, log: spec.log, ready, tail: [] }
}
