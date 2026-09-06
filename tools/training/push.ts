/** push.ts — 本机伪 GPU 节点（remote_worker_serve）启动步骤（DECISIONS §340 补充 4）。
 *  控制台 workerServe 组件与冒烟预演共用；TrainingLoop 推送注入经 specs.ts 的
 *  pushNodeUrl 参数。*/

import { httpOk, waitUntil } from './net'
import { launchSpec } from './proc'
import { fail, log, ok } from './log'
import { workerServeSpec } from './specs'

export interface PushSmokeContext {
  course: string
  cfgToken: string
  hubPort: number
  /** 已就绪的 venv 解析结果。 */
  venv: { python: string; sitePackages: string }
}

export interface PushSmokeResult {
  /** 本机伪 GPU 节点 URL（注入 REMOTE_PUSH_NODE）。 */
  pushUrl: string
  /** worker_server PID（预演后停止）。 */
  servePid: number
}

/** 本机伪 GPU 节点（remote_worker_serve）步骤：起服务 + 等 /ping 就绪（spec 构造
 *  集中在 specs.ts，控制台 workerServe 组件与此共用）。 */
export async function startLocalWorkerServer(ctx: PushSmokeContext): Promise<PushSmokeResult> {
  const pushPort = ctx.hubPort + 2
  const pushUrl = `http://127.0.0.1:${pushPort}`
  log('启动本机伪 GPU 节点 (remote_worker_serve，模拟 Kaggle 侧 worker_server)...')
  const spec = workerServeSpec(
    {
      version: 1,
      nodes: [],
      rl: { hub_port: ctx.hubPort, agent_port: 0, remote_token: ctx.cfgToken },
    },
    ctx.venv,
  )
  const r = launchSpec(spec)
  const serveUp = await waitUntil(() => httpOk(`${pushUrl}/ping`, ctx.cfgToken, 3000), 20000, 500)
  if (!serveUp) {
    fail('本机 worker_server 20s 未就绪——见 remote-worker-serve.log')
    throw new Error('本机 push 节点未就绪')
  }
  ok(`本机 push 节点就绪: ${pushUrl}（模拟 Kaggle 侧 worker_server）`)
  return { pushUrl, servePid: r.pid }
}

/** 预演收尾：停掉本机伪节点（冒烟专用进程，不入长跑账本——由调用方决定登记与否）。 */
export async function stopLocalWorkerServer(servePid: number): Promise<void> {
  if (servePid) {
    const { killPid } = await import('./net')
    await killPid(servePid)
  }
}
