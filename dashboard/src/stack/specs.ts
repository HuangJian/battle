/** specs.ts — 受管组件 ProcSpec 构造（唯一事实来源）。

 *  hub.ts 的启动步骤、控制台动作层、控制台监督器（变更检测重启）三处都需要
 *  "某个组件该怎么 spawn"——过去这份知识散在三处（DECISIONS §349 起集中于此）。
 *  监督器从账本元数据（entry.course / entry.metrics / entry.log）+ 当前 rl-config
 *  重建 spec，因此重启永远用最新配置与最新哨兵。
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { LOG_DIR, NN_TRAINING, REPO_ROOT } from '../core/paths'
import { httpOk, pidAlive, portListen } from '../core/net'
import { entryForCourse, loadRegistry } from '../core/registry'
import { agentSentinels, pySentinels } from '../core/sentinels'
import { slotPort } from '../core/slots'
import { resolveVenvPython } from '../core/venv'
import type { ProcSpec, RegistryEntry, RlConfig } from '../core/types'

/** 课程日志目录（per-course；无课程走 `nocourse`——与旧单课路径同构）。 */
export function courseLogDir(course: string): string {
  return path.join(LOG_DIR, course || 'nocourse')
}

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
    course: '',
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
  const port = slotPort(cfg, course, 'hub')
  return {
    key: 'hubServer',
    name: 'hub-server',
    course,
    cmd: [
      resolveVenvPython().python,
      '-u',
      '-m',
      'remote.hub_server',
      '--port',
      String(port),
      '--token',
      cfg.rl.remote_token,
      '--job-root',
      path.join(trajDir, 'remote-jobs'),
      '--jsonl',
      path.join(trajDir, 'training_log.jsonl'),
    ],
    env: { PYTHONPATH: NN_TRAINING },
    // 日志 per-course（M6：spec 侧 + api.ts resolver 两半同步）
    log: path.join(courseLogDir(course), 'hub-server.out'),
    healthy: () => httpOk(`http://127.0.0.1:${port}/ping`, cfg.rl.remote_token),
    sentinels: pySentinels(HUB_SERVER_ENTRY),
  }
}

// ────────────────────────── cloudflared ──────────────────────────

export function cloudflaredSpec(cfg: RlConfig, entry?: RegistryEntry): ProcSpec {
  const cfBin = resolveCloudflaredBin()
  const slot = entry?.slot ?? 0
  const course = entry?.course ?? ''
  const metricsPort = entry?.metrics ?? slotPort(cfg, slot, 'metrics')
  const cfLog = entry?.log ?? path.join(LOG_DIR, `cloudflared-${Date.now()}.log`)
  return {
    key: 'cloudflared',
    name: 'cloudflared',
    course,
    cmd: [
      cfBin ?? 'cloudflared',
      'tunnel',
      '--url',
      `http://localhost:${slotPort(cfg, slot, 'hub')}`,
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
  course = '',
): ProcSpec {
  const pushPort = slotPort(cfg, course, 'push')
  const pushUrl = `http://127.0.0.1:${pushPort}`
  // work 目录 per-course（Q9：硬编码单值在双课冒烟时会让两个伪节点互相踩 payload）；
  // 无课程沿用旧路径（默认行为零变化）。
  const workDir = course ? `tmp/remote-worker-serve-${course}` : 'tmp/remote-worker-serve'
  return {
    key: 'workerServe',
    name: 'worker_server (本机伪 GPU 节点)',
    course,
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
      workDir,
    ],
    cwd: NN_TRAINING,
    env: { PYTHONPATH: `${venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
    log: path.join(course ? courseLogDir(course) : LOG_DIR, 'remote-worker-serve.log'),
    healthy: () => httpOk(`${pushUrl}/ping`, cfg.rl.remote_token, 3000),
    sentinels: pySentinels(WORKER_SERVE_ENTRY, 'nn-training/remote/worker_server.py'),
  }
}

// ────────────────────────── BcLoop（BC 编排器，2026-09-13） ──────────────────────────

export const BC_LOOP_ENTRY = 'nn-training/run_bc.py'

export interface BcLoopSpecOpts {
  course: string
  /** 远程：发布到 per-course hub（pull preset）/ 直推 push 节点（push preset，
   *  run_bc 读 REMOTE_PUSH_NODE/push_node_url）。local preset → --local（本机 torch）。 */
  ppo?: 'pull' | 'push' | 'local' | 'remote'
  /** 冒烟：尺寸压缩真一轮，落位即作废（不覆盖 out、不归档、账本零污染）。 */
  smoke?: boolean
  /** 冒烟/push 注入：REMOTE_PUSH_NODE（本机伪 GPU 节点 URL）。 */
  pushNodeUrl?: string
  venv: { python: string; sitePackages: string }
}

/** BC 课程编排器 spec：复用 trainingLoop 组件键（registry/监督/停止全链零改动），
 *  仅 cmd/哨兵分叉；日志沿用 training-loop.log（api.ts 组件表解析无需感知）。 */
export function bcLoopSpec(cfg: RlConfig, s: BcLoopSpecOpts): ProcSpec {
  void cfg
  const trainLog = path.join(LOG_DIR, s.course || 'nocourse', 'training-loop.log')
  return {
    key: 'trainingLoop',
    name: 'BcLoop (trainer)',
    course: s.course,
    cmd: [
      s.venv.python,
      '-u',
      path.join(REPO_ROOT, BC_LOOP_ENTRY),
      '--course',
      s.course,
      ...(s.ppo === 'local' ? ['--local'] : ['--remote']),
      ...(s.smoke ? ['--smoke'] : []),
    ],
    env: {
      PYTHONPATH: `${s.venv.sitePackages}${path.delimiter}${NN_TRAINING}`,
      ...(s.pushNodeUrl ? { REMOTE_PUSH_NODE: s.pushNodeUrl } : {}),
    },
    log: trainLog,
    healthy: async () => pidAlive(entryForCourse(loadRegistry(), 'trainingLoop', s.course)?.pid),
    sentinels: pySentinels(
      BC_LOOP_ENTRY,
      'nn-training/rl/bc_config.py',
      'nn-training/rl/bc_dispatch.py',
      'nn-training/remote/protocol.py',
      'nn-training/remote/worker.py',
      'nn-training/remote/hub_client.py',
    ),
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
  /** 门禁触发时的动作：halt = 下发云端停机达令（默认）；notify = 只提示不停机。 */
  gateHaltMode?: GateHaltMode
}

/**
 * 门禁动作模式（2026-09-13）：`halt` = 下发 cloud halt（历史默认）；
 * `notify` = 只记录 gate_verdict + 控制台横幅，**停掉云机这件事不做**。
 *
 * 为什么是文件而不是纯启动参数：G4(plateau) 的 REMEDIATE 每 5 轮就复现一次，
 * 历史上 c6-pickup3 / c6-bonus 就是被它反复杀掉云端 PPO worker（6 次 / 10 次）。
 * 操作员在训练途中改主意必须能热切，不能重启一轮（重启 = 丢进度）。
 */
export type GateHaltMode = 'halt' | 'notify'

/**
 * 标志文件路径：`<traj>/gate-halt-mode.txt`。
 * Python 侧 `rl/loop_guards.py::_gate_halt_mode` 每轮门判定读它（优先于启动参数）。
 * traj 在课程里恒写作 `tmp/<name>`，故这里按 course 拼即可与 Python 对齐。
 */
export function gateHaltModePath(course: string): string {
  return path.join(LOG_DIR, course || 'nocourse', 'gate-halt-mode.txt')
}

export function readGateHaltMode(course: string): GateHaltMode {
  try {
    const v = readFileSync(gateHaltModePath(course), 'utf8').trim().toLowerCase()
    if (v === 'notify' || v === 'halt') return v
  } catch {
    /* 无文件/不可读 = 用默认 */
  }
  return 'halt'
}

export function writeGateHaltMode(course: string, mode: GateHaltMode): GateHaltMode {
  const p = gateHaltModePath(course)
  mkdirSync(path.dirname(p), { recursive: true })
  writeFileSync(p, `${mode}\n`, 'utf8')
  return mode
}

export function trainingLoopSpec(cfg: RlConfig, s: TrainingLoopSpecOpts): ProcSpec {
  void cfg
  const trainLog = path.join(LOG_DIR, s.course || 'nocourse', 'training-loop.log')
  return {
    key: 'trainingLoop',
    name: 'TrainingLoop',
    course: s.course,
    cmd: [
      s.venv.python,
      '-u',
      // TRAINING_LOOP_ENTRY 是仓库相对路径（哨兵/账本用）——绝对路径从仓库根拼，
      // 不能再 join(NN_TRAINING)（会把 nn-training 前缀翻倍，python 直接打不开文件）。
      path.join(REPO_ROOT, TRAINING_LOOP_ENTRY),
      '--course',
      s.course,
      ...(s.ppo === 'local' ? [] : ['--ppo', 'remote']),
      ...(s.smoke ? ['--smoke'] : []),
      ...(s.gateHaltMode ? ['--gate-halt-mode', s.gateHaltMode] : []),
    ],
    env: {
      PYTHONPATH: `${s.venv.sitePackages}${path.delimiter}${NN_TRAINING}`,
      ...(s.pushNodeUrl ? { REMOTE_PUSH_NODE: s.pushNodeUrl } : {}),
    },
    log: trainLog,
    // 账本 pid 即真相（saveAnyComponent 在 spawn 后立即回灌新 pid）；严格按课取（R2）。
    healthy: async () => pidAlive(entryForCourse(loadRegistry(), 'trainingLoop', s.course)?.pid),
    sentinels: pySentinels(TRAINING_LOOP_ENTRY),
  }
}
