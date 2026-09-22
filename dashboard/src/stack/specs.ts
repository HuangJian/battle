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
import { sharedHubUrl, sharedHubPort, sharedTunnelMetricsPort } from '../core/slots'
import { portOwnedBy } from '../core/proc'
import { resolveVenvPython } from '../core/venv'
import { COMPONENT_KILL_TREE } from '../core/types'
import type {
  CfEdgeIp,
  CfProtocol,
  ProcSpec,
  RegistryEntry,
  RlConfig,
  RolloutSrcMode,
  SlimMode,
  TrainMode,
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
/** 训练模式 → 课程级 rl-config 键（**唯一推导点**；2026-09-19 离线训练模式）。
 *
 *  `offline` ⇒ `{rollout_src:'run', run_iters:-1}`：两个键缺一不可——
 *    · `run` 是**声明**（本机不跑 rollout，整段交给云机）；
 *    · `run_iters:-1` 是**段长**（-1 = 直到课程末尾，与 `--export-bundle` 同口径）。
 *    只写 `run` 而不给段长在训练侧是配置错误（`_run_segment_iters` 返回 0 = 关，
 *    于是本轮静默退化成在**本机**采样 —— 那正是最难查的那类分叉）。
 *  `online` ⇒ `{rolloutSrc: 选中的源, runIters: null}`，其中 `null` = **要求删除**该课
 *    的 `run_iters`/`rollout_src` 覆盖（不删就会「切回在线了但还在整段上云」）。
 *
 *  为什么放这里：模式与 rollout 源是**两个域**（一个用户口径、一个 python 字面量），
 *  换算只此一处，弹窗/preset/测试共用。
 */
export function trainModeKnobs(
  mode: TrainMode,
  rolloutSrc: RolloutSrcMode,
): { rolloutSrc: RolloutSrcMode; runIters: number | null } {
  if (mode === 'offline') return { rolloutSrc: 'run', runIters: -1 }
  // 在线不接受 `run`：`run` 是离线模式的产物，留在在线档位里就是自相矛盾的状态。
  return { rolloutSrc: rolloutSrc === 'run' ? 'local' : rolloutSrc, runIters: null }
}

export function resolveRolloutSrc(cfg: RlConfig, course = ''): RolloutSrcMode {
  const cc = course ? cfg.courses?.[course] : undefined
  const raw = cc?.rollout_src ?? cfg.rl.rollout_src
  // `run`（离线训练模式；2026-09-19）也是合法值——漏掉它 = 离线课在 UI 上显示成 `local`，
  // 而那正是「云机在跑」与「本机在跑」看起来一样的那类静默分叉。域与 python
  // `rl/loop_steps.py::ROLLOUT_SRCS` 同源（有测试对账）。
  return raw === 'node' || raw === 'run' || raw === 'auto' ? raw : 'local'
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

/** 本机 PPO worker（pull 模式）：`remote_worker --poll <共享 hub>`。
 *
 *  与云端 worker **同一份代码/同一套协议**（租约/心跳/幂等重拉/热替换退出码 86 +
 *  内部监督器重拉），差别只有 `--poll` 指向本机 hub、`--device cpu`。控制台只负责
 *  启停（与其它受管组件同规：账本 + 变更检测重启 + 整树停止）。
 *
 *  ★ **一个进程服务所有课程**（2026-09-19，用户口径：「它和云端 worker 一样，只与 hub 通信，
 *  领到任务后直接执行，完成后回传结果」）：hub 的取活面从来不看课程——job 由 hub 按队列
 *  分发、manifest 自带课程快照、结果按 job_id 回家。故 spec 与**课程无关**（`course: ''`、
 *  单一 work 目录与日志）；「这门课的 worker」这个归属只存在于旧账本的每课条目里（启动时
 *  被换代接管收掉）。
 *
 *  语义注意：push（worker_server）不在这里——push 模式的执行面是**云机** worker_server
 *  （经隧道，控制台不拉起本机伪节点；后者只服务冒烟预演，见 `stack/push.ts`）。 */
export function localWorkerSpec(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
): ProcSpec {
  // 共享 hub（2026-09-18）：一个作业中枢服务所有课程。
  const hubUrl = sharedHubUrl(cfg)
  // torch 线程：0/缺省 = torch 默认（云端 worker 同语义）；配了 rl.torch_threads 就透传——
  // 本机 worker 与 rollout 子进程抢核，这时它是唯一能限核的旋钮。
  const threads = Math.round(Number(cfg.rl?.torch_threads ?? 0) || 0)
  // work 目录唯一（不再 per-course）：一个进程串行干所有课的活，job 目录/payload 按 job_id
  // 归档，故不存在互踩。--out 由 python 侧按仓库根解析（worker main）。
  return {
    key: 'localWorker',
    name: 'local-worker (本机 PPO worker)',
    course: '',
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
      'tmp/local-worker',
      '--device',
      'cpu',
      ...(threads > 0 ? ['--threads', String(threads)] : []),
    ],
    cwd: NN_TRAINING,
    env: { PYTHONPATH: `${venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
    log: path.join(LOG_DIR, 'local-worker.log'),
    // 无 HTTP 端点可探（它是出站轮询者）——存活即健康，与 trainingLoop 同口径。
    // 槽恒 `''`（共享实例不属于任何单门课；归一唯一归宿 = registry.scopeOf）。
    healthy: async () => pidAlive(entryForCourse(loadRegistry(), 'localWorker', '')?.pid),
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

// ★ 本机伪 GPU 节点（`remote_worker_serve`）**没有 ProcSpec**（2026-09-19 用户指令）：
//   它不是受管组件，只服务 trainingLoop 冒烟预演，由预演自起自停（`stack/push.ts`）。

// ────────────────────────── BcLoop（BC 编排器，2026-09-13） ──────────────────────────

export const BC_LOOP_ENTRY = 'nn-training/run_bc.py'

export interface BcLoopSpecOpts {
  course: string
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
      // ★ 2026-09-19：**不再钉死传输**（`--remote-transport` 由训练侧 auto 裁决）。旧的
      // 「local/pull 一律钉 pull」是为了防残留 gpu_push 节点把活劫去云机；现在课程与
      // worker 节点正交、控制台也不再写 per-course 传输键，残留的那种条目已在启动时由
      // `pruneLegacyCourseKnobs` 清掉。冒烟预演则靠 env `REMOTE_PUSH_NODE` 定方向。
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
  /** 冒烟预演：--smoke（作废本轮、账本零污染）。 */
  smoke?: boolean
  /** 冒烟注入：REMOTE_PUSH_NODE（本机伪 GPU 节点 URL）。 */
  pushNodeUrl?: string
  /** pull 目标 hub（local preset 注入本机 hub；其余模式缺省=读 rl-config remote_hubs）。 */
  hubUrl?: string
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
 *  `rl-config → courses.<课>.{gate_halt_mode}`（serve 的 `apply_course_machine_overrides`）。
 *  ★ 2026-09-21（§3）：backend 不再是旋钮（`--ppo` 已删）——PPO 恒为「发布到 hub 队列 +
 *  等 worker 认领」，故命令行上**一个 PPO 相关的旗标都没有**。
 *
 *  日志：stdout 落共享 `trainer-cluster.log`；**每课仍有自己的镜像**（serve 的行路由，
 *  路径 = 该课 traj 下的 `training-loop.log`）⇒ 组件卡的「日志增长」就绪判定与 `/log/trainingLoop`
 *  页按课程读，与收敛前同一个文件。
 */
export const TRAINER_SERVE_ENTRY = 'nn-training/run_rl_cluster.py'

export function trainerServeSpec(
  cfg: RlConfig,
  venv: { python: string; sitePackages: string },
): ProcSpec {
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
      // ★ §3（2026-09-21）：`--ppo` 已删除 —— PPO 恒为「发布到 hub 队列 + 等 worker 认领」，
      //   训练侧没有「跑在哪」这个参数；想要本机算，起本机 worker 组件（同一认领协议）。
      // 控制文件（暂停意图）：控制台写、训练侧每拍读——显式给绝对路径，不靠 cwd
      '--control-file',
      path.join(REPO_ROOT, 'tmp', 'loop-control.json'),
      // 进程级单实例锁（一个进程服务所有课程 ⇒ 双开 = 两套调度器抢同一批 traj）
      '--cluster-lock',
      path.join(NN_TRAINING, '.run_cluster.lock'),
    ],
    cwd: REPO_ROOT,
    // ★ venv 的 **site-packages** 必须挂在 PYTHONPATH 上：`venv.python` 是 uv 跳板的真身
    //  （基础解释器），它自己不认 venv 的包（`pyvenv.cfg` 无 executable 时
    //  `resolveVenvPython` 只能从 `home` 取基础解释器；`include-system-site-packages=false`）
    //  ⇒ 漏了这一项就是 `ModuleNotFoundError: No module named 'pydantic'`（2026-09-20 线上实测：
    //  trainer 连续两次「启动即退出」，而 hub/其余组件无恙——它们要么只用 stdlib，要么本就带了
    //  这一项）。与 localWorker / trainingLoop / BC spec 同规。
    env: { PYTHONPATH: `${venv.sitePackages}${path.delimiter}${NN_TRAINING}` },
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
      // ★ 2026-09-19：**不再钉死传输**（同 BC 分支）：执行面由 `rl.hub_push` + 登记节点 +
      // hub 队列裁决；`--remote-transport` 一律交回训练侧 auto。冒烟预演靠 env
      // `REMOTE_PUSH_NODE` 定方向。
      ...hubFlags,
      ...(s.smoke ? ['--smoke'] : []),
      ...(s.gateHaltMode ? ['--gate-halt-mode', s.gateHaltMode] : []),
      // ★ §3：`--remote-degrade-after` 已删除（单一 PPO 路径无「就地降级本机」档）。
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
