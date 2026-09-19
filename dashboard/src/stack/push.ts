/** push.ts — 冒烟预演用的**本机伪 GPU 节点**（`remote_worker_serve`）：自起自停。
 *
 *  ★ 它**不是受管组件**（2026-09-19 用户指令：「workerServe 伪节点直接从 dashboard 去掉，
 *  它只是用于 trainingloop 冒烟测试，用户只关心冒烟是否通过，不会手动去开启/停止伪节点」）：
 *  没有卡片、没有账本条目、没有日志页入口、没有端口兜底清场、没有变更检测哨兵。
 *  生命周期就是一个 `try/finally`：预演起来 → 跑完（或失败）即杀。
 *
 *  **真 push 执行面是云机 worker_server**（经隧道，用户填 endpoint 或复用 config 里 ping 通的
 *  `gpu_push` 节点）——控制台不再提供「本机伪节点当执行面」的启动途径（旧形状里那条
 *  「无可用 gpu_push → 回落本机 workerServe」的路径在 2026-09-15 就已被改成响亮报错，
 *  伪节点随之只剩冒烟这一个用途）。
 *
 *  训练侧的推送目标注入仍走 `REMOTE_PUSH_NODE`（指向这里的临时 URL）——预演要证明的正是
 *  「真课程发布 job → 推送 → 执行 → 回传 → 落位」这条链路本身。
 */

import path from 'path'
import { LOG_DIR, NN_TRAINING } from '../core/paths'
import { httpOk, killPid, waitUntil } from '../core/net'
import { spawnBg } from '../core/proc'
import { fail, log, ok } from '../core/log'
import { slotPort } from '../core/slots'
import { courseLogDir } from './specs'
import type { RlConfig } from '../core/types'

export interface PushSmokeContext {
  course: string
  /** 真实 rl-config（槽位算术与非课程键都在里面；不再合成半份 cfg）。 */
  cfg: RlConfig
  /** 已就绪的 venv 解析结果。 */
  venv: { python: string; sitePackages: string }
}

export interface PushSmokeResult {
  /** 本机伪 GPU 节点 URL（注入 REMOTE_PUSH_NODE）。 */
  pushUrl: string
  /** worker_server PID（预演后停止）。 */
  servePid: number
}

/** 起本机伪 GPU 节点 + 等 `/ping` 就绪。
 *
 *  端口恒取该课槽位的 push 端口（与课程 `push_node_url` 的槽位算术同源）；work 目录
 *  per-course（硬编码单值在双课同冒时会让两个伪节点互相踩 payload）。 */
export async function startLocalWorkerServer(ctx: PushSmokeContext): Promise<PushSmokeResult> {
  const pushPort = slotPort(ctx.cfg, ctx.course, 'push')
  const pushUrl = `http://127.0.0.1:${pushPort}`
  log('启动本机伪 GPU 节点 (remote_worker_serve，模拟 Kaggle 侧 worker_server)...')
  const workDir = ctx.course ? `tmp/remote-worker-serve-${ctx.course}` : 'tmp/remote-worker-serve'
  const r = spawnBg(
    [
      ctx.venv.python,
      '-u',
      '-m',
      'remote_worker_serve',
      '--port',
      String(pushPort),
      '--token',
      ctx.cfg.rl.remote_token,
      '--work',
      workDir,
    ],
    {
      cwd: NN_TRAINING,
      env: { PYTHONPATH: `${ctx.venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
      log: path.join(ctx.course ? courseLogDir(ctx.course) : LOG_DIR, 'remote-worker-serve.log'),
    },
  )
  const serveUp = await waitUntil(
    () => httpOk(`${pushUrl}/ping`, ctx.cfg.rl.remote_token, 3000),
    20000,
    500,
  )
  if (!serveUp) {
    fail('本机 worker_server 20s 未就绪——见 remote-worker-serve.log')
    await killPid(r.pid)
    throw new Error('本机 push 节点未就绪')
  }
  ok(`本机 push 节点就绪: ${pushUrl}（模拟 Kaggle 侧 worker_server）`)
  return { pushUrl, servePid: r.pid }
}

/** 预演收尾：停掉本机伪节点（冒烟专用一次性进程，不入账本）。 */
export async function stopLocalWorkerServer(servePid: number): Promise<void> {
  if (servePid) await killPid(servePid)
}
