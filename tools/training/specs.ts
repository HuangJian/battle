/** specs.ts — 受管组件 ProcSpec 构造（唯一事实来源）。

 *  hub.ts 的启动步骤、控制台动作层、控制台监督器（变更检测重启）三处都需要
 *  "某个组件该怎么 spawn"——过去这份知识散在三处（DECISIONS §349 起集中于此）。
 *  监督器从账本元数据（entry.course / entry.metrics / entry.log）+ 当前 rl-config
 *  重建 spec，因此重启永远用最新配置与最新哨兵。
 */

import { existsSync } from 'fs'
import path from 'path'
import { LOG_DIR, NN_TRAINING, REPO_ROOT } from './paths'
import { httpOk, pidAlive, portListen } from './net'
import { loadRegistry } from './registry'
import { agentSentinels, pySentinels } from './sentinels'
import { resolveVenvPython } from './venv'
import type { ProcSpec, RegistryEntry, RlConfig } from './types'

/** cloudflared 真身路径（Chocolatey 的 bin\cloudflared.exe 是 shim——另起真身子进程、
 *  不透传 stdio 句柄、被杀留孤儿；优先直取 lib\<name>\tools\ 真身 exe）。 */
export function resolveCloudflaredBin(): string | null {
  const found = Bun.which('cloudflared')
  if (!found) return null
  const real = path.resolve(
    path.dirname(path.dirname(found)),
    'lib',
    'cloudflared',
    'tools',
    'cloudflared.exe',
  )
  if (path.basename(found).toLowerCase().endsWith('.exe') && existsSync(real)) return real
  return found
}

// ────────────────────────── self-node ──────────────────────────

export const SELF_NODE_ENTRY = 'tools/agent/sampler-agent.ts'

export function selfNodeSpec(cfg: RlConfig): ProcSpec {
  const selfKey = cfg.nodes.find((n) => n.id === 'self')?.authKey ?? ''
  return {
    key: 'selfNode',
    name: 'self-node',
    cmd: [process.execPath, 'run', SELF_NODE_ENTRY, '--port', String(cfg.rl.agent_port)],
    log: path.join(LOG_DIR, 'sampler-agent.log'),
    healthy: async () =>
      (await portListen(cfg.rl.agent_port)) &&
      (await httpOk(`http://127.0.0.1:${cfg.rl.agent_port}/v1/ping`, selfKey)),
    sentinels: agentSentinels(SELF_NODE_ENTRY),
  }
}

// ────────────────────────── hub-server ──────────────────────────

export const HUB_SERVER_ENTRY = 'nn-training/remote/hub_server.py'

export function hubServerSpec(cfg: RlConfig, course: string): ProcSpec {
  const trajDir = path.join(REPO_ROOT, 'tmp', course || 'nocourse')
  return {
    key: 'hubServer',
    name: 'hub-server',
    cmd: [
      resolveVenvPython().python,
      '-u',
      '-m',
      'remote.hub_server',
      '--port',
      String(cfg.rl.hub_port),
      '--token',
      cfg.rl.remote_token,
      '--job-root',
      path.join(trajDir, 'remote-jobs'),
      '--jsonl',
      path.join(trajDir, 'training_log.jsonl'),
    ],
    env: { PYTHONPATH: NN_TRAINING },
    log: path.join(LOG_DIR, 'hub-server.out'),
    healthy: () => httpOk(`http://127.0.0.1:${cfg.rl.hub_port}/ping`, cfg.rl.remote_token),
    sentinels: pySentinels(HUB_SERVER_ENTRY),
  }
}

// ────────────────────────── cloudflared ──────────────────────────

export function cloudflaredSpec(cfg: RlConfig, entry?: RegistryEntry): ProcSpec {
  const cfBin = resolveCloudflaredBin()
  const metricsPort = entry?.metrics ?? cfg.rl.hub_port + 1
  const cfLog = entry?.log ?? path.join(LOG_DIR, `cloudflared-${Date.now()}.log`)
  return {
    key: 'cloudflared',
    name: 'cloudflared',
    cmd: [
      cfBin ?? 'cloudflared',
      'tunnel',
      '--url',
      `http://localhost:${cfg.rl.hub_port}`,
      '--metrics',
      `127.0.0.1:${metricsPort}`,
      '--logfile',
      cfLog,
    ],
    log: cfLog,
    // edge 连接注册以本地 metrics /ready 为准（不依赖出网；hub→CF 劣化不判死）
    healthy: () => httpOk(`http://127.0.0.1:${metricsPort}/ready`, '', 3000),
    sentinels: pySentinels(),
  }
}

// ────────────────────────── worker_server（本机伪 GPU 节点） ──────────────────────────

export const WORKER_SERVE_ENTRY = 'nn-training/remote_worker_serve.py'

export function workerServeSpec(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
): ProcSpec {
  const pushPort = cfg.rl.hub_port + 2
  const pushUrl = `http://127.0.0.1:${pushPort}`
  return {
    key: 'workerServe',
    name: 'worker_server (本机伪 GPU 节点)',
    cmd: [
      venv.python,
      '-u',
      '-m',
      'remote_worker_serve',
      '--port',
      String(pushPort),
      '--token',
      cfg.rl.remote_token,
      '--work',
      'tmp/remote-worker-serve',
    ],
    cwd: NN_TRAINING,
    env: { PYTHONPATH: `${venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
    log: path.join(LOG_DIR, 'remote-worker-serve.log'),
    healthy: () => httpOk(`${pushUrl}/ping`, cfg.rl.remote_token, 3000),
    sentinels: pySentinels(WORKER_SERVE_ENTRY, 'nn-training/remote/worker_server.py'),
  }
}

// ────────────────────────── TrainingLoop ──────────────────────────

export const TRAINING_LOOP_ENTRY = 'nn-training/run_rl.py'

export interface TrainingLoopSpecOpts {
  course: string
  /** PPO 模式：local → 不带 --ppo；pull/push/remote → --ppo remote。 */
  ppo?: 'pull' | 'push' | 'local' | 'remote'
  /** 冒烟预演：--smoke（作废本轮、账本零污染）。 */
  smoke?: boolean
  /** 冒烟注入：REMOTE_PUSH_NODE（本机伪 GPU 节点 URL）。 */
  pushNodeUrl?: string
  venv: { python: string; sitePackages: string }
}

export function trainingLoopSpec(cfg: RlConfig, s: TrainingLoopSpecOpts): ProcSpec {
  void cfg
  const trainLog = path.join(LOG_DIR, s.course || 'nocourse', 'training-loop.log')
  return {
    key: 'trainingLoop',
    name: 'TrainingLoop',
    cmd: [
      s.venv.python,
      '-u',
      path.join(NN_TRAINING, TRAINING_LOOP_ENTRY),
      '--course',
      s.course,
      ...(s.ppo === 'local' ? [] : ['--ppo', 'remote']),
      ...(s.smoke ? ['--smoke'] : []),
    ],
    env: {
      PYTHONPATH: `${s.venv.sitePackages}${path.delimiter}${NN_TRAINING}`,
      ...(s.pushNodeUrl ? { REMOTE_PUSH_NODE: s.pushNodeUrl } : {}),
    },
    log: trainLog,
    // 账本 pid 即真相（saveComponent 在 spawn 后立即回灌新 pid）
    healthy: async () => pidAlive(loadRegistry().trainingLoop?.pid),
    sentinels: pySentinels(TRAINING_LOOP_ENTRY),
  }
}
