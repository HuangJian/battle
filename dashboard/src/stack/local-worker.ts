/** local-worker.ts — 本机 PPO worker（`remote_worker --poll <共享 hub>`）的启动步骤。
 *
 *  为什么独立成模块：本机 PPO 从 "run_rl 进程内 `--ppo local`" 拆成**独立受管进程**
 *  （2026-09-15，与云端 worker 同一份代码/同一套协议），于是它像 hubServer 一样需要
 *  「spawn → 登记账本 → 等就绪 → 由监督器接管变更检测」。spec 构造集中在 specs.ts
 *  （控制台启动步骤与监督器重启共用），本层只做编排（与 push.ts 同分层）。
 *
 *  ★ 2026-09-19：**一个进程服务所有课程**（用户口径：「localWorker 也不应绑定课程，它和云端
 *  worker 一样，只与 hub 通信（pull/push），领到任务后直接执行，完成后回传结果」）。
 *  `/jobs/next` 从来不看课程——job 由 hub 按队列分发、manifest 自带课程快照、结果按 job_id
 *  回家。故本层不再接受 course 作为编排输入，与 trainer（R3-5）同一形状：共享槽 + 换代接管。
 */

import { readFileSync } from 'fs'
import { fail, log, ok, warn } from '../core/log'
import { httpOk, pidAlive, sleep } from '../core/net'
import { launchSpec } from '../core/proc'
import { saveAnyComponent } from '../core/registry'
import { monitorTouch } from '../core/reload-touch'
import { sharedHubUrl } from '../core/slots'
import type { RlConfig } from '../core/types'
import { supersedeLegacyInstances } from './hub'
import { LOCAL_WORKER_ENTRY, localWorkerSpec } from './specs'

export interface LocalWorkerCtx {
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
  /** 被换代接管（停掉并清账）的旧形状每课实例所属课程。 */
  superseded: string[]
}

/** 日志尾（本模块不从 server/actions 借 tailLines——那会把 stack 反向依赖到 actions）。 */
function logTail(p: string, n = 6): string[] {
  try {
    return readFileSync(p, 'utf-8').split('\n').filter(Boolean).slice(-n)
  } catch {
    return []
  }
}

// ★ 2026-09-19 删掉了 `coursesInLocalMode`：它是「local 模式」的配套判据（离开 local 时只在
//   最后一门 local 课才停共享 worker）。local 模式本身已随「课程与 worker 节点正交」退场 ——
//   本机 worker 就是一张独立的共享卡片，起它就参与领活，没有任何课程声明「我在本机跑」。
//   停它 = 本机不再执行**任何**课程的 PPO job（卡片上的停止语义有说明）。

/** 启动本机 PPO worker：换代接管旧形状实例 + spawn + 登记账本 + 就绪窗口判活。
 *
 *  就绪判定 = **存活**（它是出站轮询者，没有任何 HTTP 端点可探）：worker 启动后即进入
 *  轮询循环，唯一的启动失败形态是进程立刻退出（venv/import/token 参数问题）。故给 1.5s
 *  窗口再判一次 pidAlive——秒退就响亮失败（否则组件卡会显示「运行中」，实际什么都没跑）。
 *  hub 未就绪只 WARN 不拦截：worker 会一直重试轮询（与云端 worker 断线重连同语义），
 *  先起 worker 后起 hub 也能自愈。 */
export async function startLocalWorker(ctx: LocalWorkerCtx): Promise<LocalWorkerResult> {
  const spec = localWorkerSpec(ctx.cfg, ctx.venv)
  // 共享 hub（2026-09-18）：本机 worker 轮询的是那一个作业中枢，与课程无关。
  const hubUrl = sharedHubUrl(ctx.cfg)
  if (!(await httpOk(`${hubUrl}/ping`, ctx.cfg.rl.remote_token, 3000))) {
    warn(`本机 hub-server 未就绪（${hubUrl}）——worker 会持续轮询重试；建议先启动 hub-server`)
  }
  // 换代接管（必须在 spawn 前）：旧形状是「每课一个 worker」，而它们与共享实例服务的是
  // **同一个角色**（同一份 hub 队列里的 job）——多份进程并存就是互相抢活，还会让每课一条
  // 日志把「谁在干活」彻底打散。
  const superseded = await supersedeLegacyInstances('localWorker')
  if (superseded.length > 0) ok(`已接管旧形状的每课本机 worker（原属 ${superseded.join('、')}）`)
  log(`启动本机 PPO worker (remote_worker --poll ${hubUrl})...`)
  const r = launchSpec(spec)
  // 登记固定走 `''` 槽（共享实例不属于任何单门课；槽归一化唯一归宿
  // = core/registry.ts::scopeOf）。
  saveAnyComponent('localWorker', '', {
    pid: r.pid,
    course: '',
    slot: 0,
    entry: LOCAL_WORKER_ENTRY,
    url: hubUrl,
    log: spec.log,
    // 启动时刻：接管对账（server.ts::reconcileWatch）判「跑的是不是旧码」靠它。
    startedAt: Date.now(),
  })
  monitorTouch()
  await sleep(1500)
  const ready = pidAlive(r.pid)
  if (!ready) {
    fail(`本机 PPO worker 启动即退出 (PID ${r.pid})——见 ${spec.log}`)
    return { pid: r.pid, log: spec.log, ready, tail: logTail(spec.log), superseded }
  }
  ok(`本机 PPO worker 已启动 (PID ${r.pid}, poll ${hubUrl})`)
  return { pid: r.pid, log: spec.log, ready, tail: [], superseded }
}
