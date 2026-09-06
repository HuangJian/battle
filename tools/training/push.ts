/** push.ts — push 模式（HUB 推架构，DECISIONS §340 补充 4）：HUB 只做普通出站
 *  HTTPS，cloudflared/服务端在 GPU 机器侧。本地职责：
 *    - 基建最小集：self-node（rollout 采集）+ TrainingLoop（--ppo remote）；
 *      hub-server/tunnel 仅在 --with-hub 时拉起（纯 push 不需要本机入站端点）。
 *    - 冒烟（启动门禁）：本机伪 GPU 节点 remote_worker_serve（与真 Kaggle 同一套
 *      run_job 代码路径；echo 模式不跑 PPO）+ REMOTE_PUSH_NODE 注入，预演走真
 *      推送链路（发布→推送→echo→落位→作废，不跑真 PPO）。
 *    - 全流程模式（非冒烟）：直接启动 TrainingLoop（GPU 节点来自 rl-config
 *      nodes[].gpu_push=true，即 Kaggle 侧 worker_server 隧道 URL）。
 */

import path from 'path'
import { LOG_DIR, NN_TRAINING } from './paths'
import { httpOk, killPid, waitUntil } from './net'
import { loadRegistry } from './registry'
import { launchSpec } from './proc'
import { fail, info, log, ok } from './log'
import { pySentinels } from './sentinels'
import type { ProcSpec } from './types'

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

/** 本机伪 GPU 节点（remote_worker_serve）步骤：起服务 + 等 /ping 就绪。 */
export async function startLocalWorkerServer(ctx: PushSmokeContext): Promise<PushSmokeResult> {
  const pushPort = ctx.hubPort + 2
  const pushUrl = `http://127.0.0.1:${pushPort}`
  const serveLog = path.join(LOG_DIR, 'remote-worker-serve.log')
  log('启动本机伪 GPU 节点 (remote_worker_serve，模拟 Kaggle 侧 worker_server)...')
  const spec: ProcSpec = {
    key: 'workerServe',
    name: 'worker_server (本机伪 GPU 节点)',
    cmd: [
      ctx.venv.python,
      '-u',
      '-m',
      'remote_worker_serve',
      '--port',
      String(pushPort),
      '--token',
      ctx.cfgToken,
      '--work',
      'tmp/remote-worker-serve',
    ],
    cwd: NN_TRAINING,
    env: { PYTHONPATH: `${ctx.venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
    log: serveLog,
    healthy: () => httpOk(`${pushUrl}/ping`, ctx.cfgToken, 3000),
    sentinels: pySentinels(
      'nn-training/remote_worker_serve.py',
      'nn-training/remote/worker_server.py',
    ),
  }
  const r = launchSpec(spec)
  const serveUp = await waitUntil(() => httpOk(`${pushUrl}/ping`, ctx.cfgToken, 3000), 20000, 500)
  if (!serveUp) {
    fail('本机 worker_server 20s 未就绪——见 remote-worker-serve.log')
    throw new Error('本机 push 节点未就绪')
  }
  ok(`本机 push 节点就绪: ${pushUrl}（模拟 Kaggle 侧 worker_server）`)
  return { pushUrl, servePid: r.pid }
}

/** 冒烟期注入：把伪节点 URL 写入 spec env（TrainingLoop 用）。 */
export function pushSmokeEnv(pushUrl: string): Record<string, string> {
  return { REMOTE_PUSH_NODE: pushUrl }
}

/** 预演收尾：停掉本机伪节点（冒烟专用进程，不入长跑账本——由调用方决定登记与否）。 */
export async function stopLocalWorkerServer(servePid: number): Promise<void> {
  if (servePid) {
    await killPid(servePid)
    info('已停止本机 worker_server（冒烟用）')
  }
}

/** push 模式的 TrainingLoop 已在运行检查（避免干扰在途 job）。 */
export function trainingLoopRunning(): boolean {
  return !!loadRegistry().trainingLoop
}
