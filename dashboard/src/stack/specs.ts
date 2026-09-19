/** specs.ts — 受管组件 ProcSpec 构造（唯一事实来源）。

 *  hub.ts 的启动步骤、控制台动作层、控制台监督器（变更检测重启）三处都需要
 *  "某个组件该怎么 spawn"——过去这份知识散在三处（DECISIONS §349 起集中于此）。
 *  监督器从账本元数据（entry.course / entry.metrics / entry.log）+ 当前 rl-config
 *  重建 spec，因此重启永远用最新配置与最新哨兵。
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import path from 'path'
import { CONFIG_PATH, LOG_DIR, NN_TRAINING, REPO_ROOT } from '../core/paths'
import { httpOk, pidAlive, portListen } from '../core/net'
import { entryForCourse, loadRegistry } from '../core/registry'
import { agentSentinels, pySentinels } from '../core/sentinels'
import { sharedHubUrl, sharedHubPort, sharedTunnelMetricsPort, slotPort } from '../core/slots'
import { portOwnedBy } from '../core/proc'
import { resolveVenvPython } from '../core/venv'
import { COMPONENT_KILL_TREE, normalizeRaceMode } from '../core/types'
import type {
  CfEdgeIp,
  CfProtocol,
  ProcSpec,
  RegistryEntry,
  RlConfig,
  RolloutSrcMode,
  SlimMode,
} from '../core/types'

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

/** 共享 hub-server spec：**一个进程服务所有并行课程**（2026-09-18 用户指令）。
 *
 *  课程表为什么不在这里给（`--course`）：训练侧把 job 发布到 `<repo>/tmp/<course>/remote-jobs`
 *  就是「这门课在跑」的**文件系统事实**（hub 与 trainer 共享同一块盘），`--discover` 让 hub
 *  自己扫出来。于是「控制台先起 hub、后开第二门课」不需要注册、不需要重启，也不会出现
 *  「漏注册 ⇒ 那门课永久饿死，而表面看起来训练一切正常」。
 *
 *  端口 = hub 基数端口本身（`sharedHubPort`）：共享之后 hub 不再按课程占端口。
 *  `--traj-root` **必须绝对**（hub 的 cwd 是控制台进程的 cwd，相对路径会指到 dashboard/tmp
 *  去），故用 REPO_ROOT 拼。 */
export function hubServerSpec(cfg: RlConfig): ProcSpec {
  const port = sharedHubPort(cfg)
  return {
    key: 'hubServer',
    name: 'hub-server (共享：服务所有课程)',
    course: '',
    cmd: [
      resolveVenvPython().python,
      '-u',
      '-m',
      'remote.hub_server',
      '--port',
      String(port),
      '--token',
      cfg.rl.remote_token,
      '--traj-root',
      path.join(REPO_ROOT, 'tmp'),
      '--discover',
      // 竞速广播（2026-09-17）：auto（缺省）= 本 hub 的 worker 全都只服务这一个 hub 时
      // 广播最新 job；on/off 为运维强制。值先归一化——hub-server 的 argparse choices
      // 对未知值直接退出，写错配置不能让整个 hub 起不来。
      '--race',
      normalizeRaceMode(cfg.rl.race_mode),
      // hub 中介 push 派发（2026-09-18）：`rl.hub_push` 打开时，hub 按队列顺序把 job 推给
      // **登记在册**的 GPU worker（登记表 = rl-config 的 `gpu_push` 节点，控制台的 worker
      // 登记入口回写的正是它 ⇒ 必须显式指向仓库那份 rl-config，而不是 per-course 目录）。
      // 默认关：不打开连探活线程都不起，行为与改造前逐字节一致。
      ...(cfg.rl.hub_push ? ['--push', '--push-config', CONFIG_PATH] : []),
    ],
    // cwd 钉死 REPO_ROOT：入口是包路径（`-m remote.hub_server`）靠 PYTHONPATH，
    // 而它内部的默认路径/日志相对 cwd；控制台以 dashboard/ 为 cwd 启动时不能漂。
    cwd: REPO_ROOT,
    env: { PYTHONPATH: NN_TRAINING },
    // 共享实例 ⇒ 日志也唯一（不再 per-course；与 api 侧 resolver 两半同步）
    log: path.join(LOG_DIR, 'hub-server.out'),
    healthy: () => httpOk(`http://127.0.0.1:${port}/ping`, cfg.rl.remote_token),
    // 就绪归属：旧僵尸 hub 可能替新进程答 /ping（新实例被双监听守卫拒绝后秒退），
    // 那样账本会记新 pid 而实际服务的是旧进程（2026-09-17 事故相位）。
    ownsResource: (pid) => portOwnedBy(pid, port),
    sentinels: pySentinels(HUB_SERVER_ENTRY),
  }
}

// ────────────────────────── cloudflared ──────────────────────────

/** 隧道选项解析（M1，plan/remote-wire-remediation §3.3）：rl.* > 缺省（http2 / 4）。
 *  缺省刻意选 http2/4——国内 ISP 对 QUIC(UDP/443) 的 QoS 降质是实测病灶；`auto` = 不传旗标，
 *  逐字节回到旧行为。
 *
 *  **2026-09-18 收敛为单隧道后不再有 per-course 覆盖**：`courses.<课>.cf_protocol` 是
 *  「每课一条隧道」时代的旋钮，一条隧道服务所有课程时它没有意义（哪一门说了算？）——
 *  按 rl.* 全局配置走，读旧配置不报错（那两项留在 courses 块里，不再被读）。 */
export function resolveCfTunnel(cfg: RlConfig): { protocol: CfProtocol; edgeIp: CfEdgeIp } {
  const protocol = (cfg.rl.cf_protocol ?? 'http2') as CfProtocol
  const edgeIp = (cfg.rl.cf_edge_ip ?? '4') as CfEdgeIp
  return { protocol, edgeIp }
}

/** 协议瘦身开关（M2）解析：per-course > rl.* > 缺省 `'on'`。
 *
 *  缺省为什么是 on：python 侧 `--remote-slim` 默认 `_d("slim", 1)`（开着才是今天的
 *  线上行为）；这里如果缺省 off，控制台会在**没改过配置**的课上谎报「瘦身关」。
 *  与 `resolveCfTunnel` 同形：UI 只认 `'on'|'off'`，rl-config 只认 1/0（`slimToCfg`）。 */
export function resolveSlim(cfg: RlConfig, course = ''): SlimMode {
  const cc = course ? cfg.courses?.[course] : undefined
  const raw = cc?.slim ?? cfg.rl.slim
  return Number(raw ?? 1) === 0 ? 'off' : 'on'
}

/** `SlimMode` → rl-config 能读的数值（**唯一**换算入口；写字符串会让训练启动报错）。 */
export function slimToCfg(mode: SlimMode): 0 | 1 {
  return mode === 'off' ? 0 : 1
}

/** rollout 执行位置（M3）解析：per-course > rl.* > 缺省 `'local'`。
 *
 *  缺省为什么是 local：python 侧 `--rollout-src` 缺省 `auto`，而 `_rollout_source()`
 *  在 rl-config 没有该键时一律返回 `local`（历史行为）——这里若缺省成别的值，控制台
 *  就会在**没改过配置**的课上谎报「本轮上云」。
 *  与 `resolveCfTunnel`/`resolveSlim` 同形，但**无域换算**：两侧都是同字面量字符串。 */
export function resolveRolloutSrc(cfg: RlConfig, course = ''): RolloutSrcMode {
  const cc = course ? cfg.courses?.[course] : undefined
  const raw = cc?.rollout_src ?? cfg.rl.rollout_src
  return raw === 'node' || raw === 'auto' ? raw : 'local'
}

/** cloudflared 隧道旗标（唯一来源）——cloudflaredSpec 与 hub.ts 的 spawn 共用，
 *  杜绝「两处 spawn 漂移」（仓库的「两半同步」约定）。`auto` 不传对应旗标。 */
export function cfTunnelArgs(cfg: RlConfig): string[] {
  const { protocol, edgeIp } = resolveCfTunnel(cfg)
  return [
    ...(protocol === 'auto' ? [] : ['--protocol', protocol]),
    ...(edgeIp === 'auto' ? [] : ['--edge-ip-version', edgeIp]),
  ]
}

/** 共享**单**隧道 spec：指向共享 hub 端口，一条隧道服务所有课程。
 *
 *  （旧形状是「每课一条隧道 + 每课 metrics 端口 + 同槽位接管」——共享 hub 之后这些全部
 *  失去意义：隧道里跑的是同一个 hub 的连接，多开一条只是多一份出网状态。） */
export function cloudflaredSpec(cfg: RlConfig, entry?: RegistryEntry): ProcSpec {
  const cfBin = resolveCloudflaredBin()
  const metricsPort = entry?.metrics ?? sharedTunnelMetricsPort(cfg)
  const cfLog = entry?.log ?? path.join(LOG_DIR, `cloudflared-${Date.now()}.log`)
  return {
    key: 'cloudflared',
    name: 'cloudflared (共享：单隧道)',
    course: '',
    cmd: [
      cfBin ?? 'cloudflared',
      'tunnel',
      '--url',
      `http://localhost:${sharedHubPort(cfg)}`,
      '--metrics',
      `127.0.0.1:${metricsPort}`,
      '--logfile',
      cfLog,
      // M1：隧道协议/边缘 IP（缺省 http2/4；auto = 不传旗标回到旧行为）。
      ...cfTunnelArgs(cfg),
    ],
    log: cfLog,
    // edge 连接注册以本地 metrics /ready 为准（不依赖出网；hub→CF 劣化不判死）
    healthy: () => httpOk(`http://127.0.0.1:${metricsPort}/ready`, '', 3000),
    // 就绪归属：metrics 端口既是 bind 目标又是探测目标，被旧僵尸占着时它的 200 会被
    // 当成新隧道的就绪（URL 读新日志、连接状态读旧 metrics）。
    ownsResource: (pid) => portOwnedBy(pid, metricsPort),
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
  // 共享 hub（2026-09-18）：本机 worker 与本课之外的所有课共用一个作业中枢——
  // 它领到哪门课的 job 就干哪门课的活（job 自带课程快照，结果按 job_id 回家）。
  const hubUrl = sharedHubUrl(cfg)
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
    // 就绪归属：push 端口上的旧 worker_server（僵尸）也会答 /ping，而新实例拿不到
    // 端口实例锁时会**响亮拒启**（这是有意的，不回收在跑 PPO job 的 worker）——
    // 不核归属就会把「旧实例在服务」记成「重启成功」。
    ownsResource: (pid) => portOwnedBy(pid, pushPort),
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
      // 2026-09-17：**pull preset 同样钉死** —— 它原本不传，于是 run_bc 落回
      // `--remote-transport auto`（=「config 里本课 gpu_push 节点 > hub」）；
      // rl-config 里留着一条**过期 quick-tunnel URL** 的 gpu_push 节点时，
      // 「云机 pull」会静默改走 push → HTTP 530 三连败 → GATE ABORT 停腿，
      // 而云机 worker 其实正在正常 pull（实测 hub 日志同时有 push 530 与 pull 200）。
      // 用户选 pull 的语义就是 pull，不该被某条残留节点悄悄改道。
      ...(s.ppo === 'local' || s.ppo === 'pull' ? ['--remote-transport', 'pull'] : []),
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

/** **共享 trainer**（`run_rl_cluster.py --serve`）——2026-09-19 / R3-5：一个进程服务所有课程。
 *
 *  为什么不是每课一个 `run_rl.py --course <课>`：用户口径「trainingloop 也只需要开一个进程就能
 *  支持所有并行课程」，且 R2d 已经造好单进程驱动者（按课锁 / 按课日志镜像 / 引擎池 / 故障隔离 /
 *  暂停恢复），R3-4 又让同一个进程能带 BC 课——而 BC 与 RL **共用 `trainingLoop` 这一个角色键**。
 *
 *  **不给 `--course`（发现模式）**：课程 = 「`<traj-root>/<课>/training_log.jsonl` 存在」这个文件
 *  系统事实（与 hub 的 `--discover` 同一原则）。控制台先起 trainer、后加课不需要重启，也不会出现
 *  「漏注册 ⇒ 那门课永久饿死而表面一切正常」。一门课都没有也照常运行（队列空着等）。
 *
 *  **不给每課 CLI 旋钮**：单进程没有「这门课的 flag」这一说——它住在机器侧覆盖
 *  `rl-config → courses.<课>.{remote_transport, remote_hub_url, remote_degrade_after, gate_halt_mode}`
 *  （serve 的 `apply_course_machine_overrides`）；`ppo=remote` 是**全进程同一个**，故走 argv。
 *
 *  日志：stdout 落共享 `trainer-cluster.log`；**每课仍有自己的镜像**（serve 的行路由，
 *  路径 = 该课 traj 下的 `training-loop.log`）⇒ 组件卡的「日志增长」就绪判定与 `/log/trainingLoop`
 *  页按课程读，与收敛前同一个文件。
 */
export const TRAINER_SERVE_ENTRY = 'nn-training/run_rl_cluster.py'

export function trainerServeSpec(cfg: RlConfig, venv: { python: string }): ProcSpec {
  void cfg // 机器侧旋钮住 rl-config，由 python 开课时施加（不在命令行上）
  return {
    key: 'trainingLoop',
    name: 'trainer (共享：服务所有课程)',
    course: '',
    cmd: [
      venv.python,
      '-u',
      // 绝对路径：cwd 是 REPO_ROOT，但入口写成相对路径会让哨兵/账本匹配不上（与旧 spec 同规）。
      path.join(REPO_ROOT, TRAINER_SERVE_ENTRY),
      '--serve',
      // traj 根必须绝对（hub 同一个坑：相对路径会指到控制台 cwd）
      '--traj-root',
      path.join(REPO_ROOT, 'tmp'),
      // PPO 在云端 GPU（控制台起的训练一律 remote）
      '--ppo',
      'remote',
      // 控制文件（暂停意图）：控制台写、训练侧每拍读——显式给绝对路径，不靠 cwd
      '--control-file',
      path.join(REPO_ROOT, 'tmp', 'loop-control.json'),
      // 进程级单实例锁（一个进程服务所有课程 ⇒ 双开 = 两套调度器抢同一批 traj）
      '--cluster-lock',
      path.join(NN_TRAINING, '.run_cluster.lock'),
    ],
    cwd: REPO_ROOT,
    env: { PYTHONPATH: NN_TRAINING },
    log: path.join(LOG_DIR, 'trainer-cluster.log'),
    healthy: async () => pidAlive(entryForCourse(loadRegistry(), 'trainingLoop', '')?.pid),
    sentinels: pySentinels(TRAINER_SERVE_ENTRY),
  }
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
      // 2026-09-17：**pull preset 同样钉死**（同因，见 BC 分支的详细注释）：
      // 云机 pull 会话被一条残留/过期的 gpu_push 节点劫走 → push 530 三连败 →
      // GATE ABORT，而云机 worker 其实正在正常 pull 并已把 result 200 回传。
      ...(s.ppo === 'local' || s.ppo === 'pull' ? ['--remote-transport', 'pull'] : []),
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
