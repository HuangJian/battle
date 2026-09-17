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
import { COMPONENT_KILL_TREE } from '../core/types'
import type { CfEdgeIp, CfProtocol, ProcSpec, RegistryEntry, RlConfig } from '../core/types'

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
    // 入口是仓库相对路径（bun run tools/agent/sampler-agent.ts）—— cwd 必须钉死
    // REPO_ROOT，否则控制台以 dashboard/ 为 cwd 启动时 bun 解析不到入口，直接
    // Module not found 退出（2026-09-14）。
    cwd: REPO_ROOT,
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

/** 隧道选项解析（M1，plan/remote-wire-remediation §3.3）：per-course 覆盖 >
 *  rl.* > 缺省（http2 / 4）。缺省刻意选 http2/4——国内 ISP 对 QUIC(UDP/443) 的
 *  QoS 降质是实测病灶；`auto` = 不传旗标，逐字节回到旧行为。 */
export function resolveCfTunnel(
  cfg: RlConfig,
  course = '',
): { protocol: CfProtocol; edgeIp: CfEdgeIp } {
  const cc = course ? cfg.courses?.[course] : undefined
  const protocol = (cc?.cf_protocol ?? cfg.rl.cf_protocol ?? 'http2') as CfProtocol
  const edgeIp = (cc?.cf_edge_ip ?? cfg.rl.cf_edge_ip ?? '4') as CfEdgeIp
  return { protocol, edgeIp }
}

/** cloudflared 隧道旗标（唯一来源）——cloudflaredSpec 与 hub.ts 的 spawn 共用，
 *  杜绝「两处 spawn 漂移」（仓库的「两半同步」约定）。`auto` 不传对应旗标。 */
export function cfTunnelArgs(cfg: RlConfig, course = ''): string[] {
  const { protocol, edgeIp } = resolveCfTunnel(cfg, course)
  return [
    ...(protocol === 'auto' ? [] : ['--protocol', protocol]),
    ...(edgeIp === 'auto' ? [] : ['--edge-ip-version', edgeIp]),
  ]
}

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
      // M1：隧道协议/边缘 IP（缺省 http2/4；auto = 不传旗标回到旧行为）。
      ...cfTunnelArgs(cfg, course),
    ],
    log: cfLog,
    // edge 连接注册以本地 metrics /ready 为准（不依赖出网；hub→CF 劣化不判死）
    healthy: () => httpOk(`http://127.0.0.1:${metricsPort}/ready`, '', 3000),
    sentinels: pySentinels(),
  }
}

// ────────────────────────── localWorker（本机独立 PPO worker，2026-09-15） ──────────────────────────

/** localWorker 的入口 = 云端 worker 的同一个入口（`python -m remote_worker` 薄包装）。 */
export const LOCAL_WORKER_ENTRY = 'nn-training/remote_worker.py'

/** 本机 PPO worker（pull 模式）：`remote_worker --poll 本课 hub`。
 *
 *  与云端 worker **同一份代码/同一套协议**（租约/心跳/幂等重拉/热替换退出码 86 +
 *  内部监督器重拉），差别只有 `--poll` 指向本机 hub、`--device cpu`。控制台只负责
 *  启停（与其它受管组件同规：账本 + 变更检测重启 + 整树停止）。
 *
 *  语义注意：push（worker_server）不在这里——push 模式的执行面就是既有 `workerServe`
 *  组件（同一台机器两个模式各占半边，不重复实现）。 */
export function localWorkerSpec(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
  course = '',
): ProcSpec {
  const hubUrl = `http://127.0.0.1:${slotPort(cfg, course, 'hub')}`
  // torch 线程：0/缺省 = torch 默认（云端 worker 同语义）；配了 rl.torch_threads 就透传——
  // 本机 worker 与 rollout 子进程抢核，这时它是唯一能限核的旋钮。
  const threads = Math.round(Number(cfg.rl?.torch_threads ?? 0) || 0)
  // work 目录 per-course（与 workerServe 同规：双课同机时两个 worker 的 job 目录/payload
  // 归档不得互相踩）；无课程沿用旧路径。--out 由 python 侧按仓库根解析（worker main）。
  return {
    key: 'localWorker',
    name: 'local-worker (本机 PPO worker)',
    course,
    cmd: [
      venv.python,
      '-u',
      '-m',
      'remote_worker',
      '--poll',
      hubUrl,
      '--token',
      cfg.rl.remote_token,
      '--out',
      course ? `tmp/local-worker-${course}` : 'tmp/local-worker',
      '--device',
      'cpu',
      ...(threads > 0 ? ['--threads', String(threads)] : []),
    ],
    cwd: NN_TRAINING,
    env: { PYTHONPATH: `${venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
    log: path.join(course ? courseLogDir(course) : LOG_DIR, 'local-worker.log'),
    // 无 HTTP 端点可探（它是出站轮询者）——存活即健康，与 trainingLoop 同口径。
    healthy: async () => pidAlive(entryForCourse(loadRegistry(), 'localWorker', course)?.pid),
    // 入口 + 实际执行链（remote/worker.py 是全部逻辑、protocol.py 是线路格式）：
    // 手工哨兵补足 codehash-files.txt 之外的依赖面（漏报 = worker 用旧协议跑新 job）。
    sentinels: pySentinels(
      LOCAL_WORKER_ENTRY,
      'nn-training/remote/worker.py',
      'nn-training/remote/protocol.py',
    ),
    // 整树停止：父 supervise_worker + 子 worker_loop（判定唯一来源 core/types.ts，
    // stop / 全部停止 / 监督重启三处共用）
    killTree: COMPONENT_KILL_TREE.has('localWorker'),
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
   *  run_bc 读 REMOTE_PUSH_NODE/push_node_url）。local preset（2026-09-15 起）
   *  = 本机独立 localWorker pull 模式：仍走 hub，但传输钉死 pull 并指名本机 hub。 */
  ppo?: 'pull' | 'push' | 'local' | 'remote'
  /** 冒烟：尺寸压缩真一轮，落位即作废（不覆盖 out、不归档、账本零污染）。 */
  smoke?: boolean
  /** 冒烟/push 注入：REMOTE_PUSH_NODE（本机伪 GPU 节点 URL）。 */
  pushNodeUrl?: string
  /** pull 目标 hub（local preset 注入本机 hub；其余模式缺省=读 rl-config remote_hubs）。 */
  hubUrl?: string
  venv: { python: string; sitePackages: string }
}

/** BC 课程编排器 spec：复用 trainingLoop 组件键（registry/监督/停止全链零改动），
 *  仅 cmd/哨兵分叉；日志沿用 training-loop.log（api.ts 组件表解析无需感知）。 */
export function bcLoopSpec(cfg: RlConfig, s: BcLoopSpecOpts): ProcSpec {
  void cfg
  const trainLog = path.join(LOG_DIR, s.course || 'nocourse', 'training-loop.log')
  const hubFlags = s.hubUrl ? ['--remote-hub-url', s.hubUrl] : []
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
      '--remote',
      // local preset = 本机独立 worker（pull）：传输钉死 pull，否则 run_bc 的
      // 「push（env/config）> hub」优先级会把 job 推去云机，本机 worker 永远领不到活。
      ...(s.ppo === 'local' ? ['--remote-transport', 'pull'] : []),
      ...hubFlags,
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
  /** PPO 执行面：pull/push → --ppo remote（云端 worker）；local（2026-09-15 起）
   *  = 本机独立 localWorker 的 pull 模式（--ppo remote --remote-transport pull
   *  --remote-hub-url 本机 hub）——进程内 PPO 不再是控制台可选项。 */
  ppo?: 'pull' | 'push' | 'local' | 'remote'
  /** 冒烟预演：--smoke（作废本轮、账本零污染）。 */
  smoke?: boolean
  /** 冒烟注入：REMOTE_PUSH_NODE（本机伪 GPU 节点 URL）。 */
  pushNodeUrl?: string
  /** pull 目标 hub（local preset 注入本机 hub；其余模式缺省=读 rl-config remote_hubs）。 */
  hubUrl?: string
  venv: { python: string; sitePackages: string }
  /** 门禁触发时的动作：halt = 下发云端停机达令（默认）；notify = 只提示不停机。 */
  gateHaltMode?: GateHaltMode
  /**
   * 远端 PPO 连败是否降级到本机进程内 PPO（T7，2026-09-15）。
   * **默认 false** → `--remote-degrade-after 0`（连败 3 次 ABORT 停腿，不静默切本机）。
   * true → `--remote-degrade-after 3`（显式 opt-in；降级前 Python 会懒加载本机栈）。
   */
  remoteDegrade?: boolean
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
  const hubFlags = s.hubUrl ? ['--remote-hub-url', s.hubUrl] : []
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
      '--ppo',
      'remote',
      // local preset = 本机独立 worker pull 模式。**必须钉死 pull**：_remote_ppo 的
      // 传输优先级是「config 里本课 gpu_push 节点 > hub」，某课用 push 跑过一次后
      // `courses.<课>.push_node_url` 就留在 rl-config 里——不钉死就会把 job 推给云机，
      // 本机 localWorker 永远空转（且看起来「训练正常」）。
      ...(s.ppo === 'local' ? ['--remote-transport', 'pull'] : []),
      ...hubFlags,
      ...(s.smoke ? ['--smoke'] : []),
      ...(s.gateHaltMode ? ['--gate-halt-mode', s.gateHaltMode] : []),
      // T7：默认不自动降级本机。opt-in 时才给 N>0。
      '--remote-degrade-after',
      String(s.remoteDegrade ? 3 : 0),
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
