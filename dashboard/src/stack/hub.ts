/** hub.ts — 基建组件启动步骤 + 冒烟门禁 + Kaggle 交互预演。职责拆分自
 *  tools/hub-start.ts（DECISIONS §339/§340 行为不变）；组件 spec 构造统一在
 *  specs.ts（DECISIONS §349），本层只做编排（检查→spawn→等就绪）。
 */

import {
  existsSync,
  mkdirSync,
  openSync,
  closeSync,
  readSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
} from 'fs'
import path from 'path'
import { LOG_DIR, fmtStamp } from '../core/paths'
import { httpOk, killPid, pidAlive, portListen, waitUntil } from '../core/net'
import {
  entryForCourse,
  loadRegistry,
  saveAnyComponent,
  saveComponent,
  clearAnyComponent,
} from '../core/registry'
import { launchSpec, portOwnedBy, portOwnerPids, spawnBg } from '../core/proc'
import { writeRemoteHubUrl } from '../core/config'
import { fail, info, log, ok, warn } from '../core/log'
import { monitorTouch } from '../core/reload-touch'
import {
  HUB_SERVER_ENTRY,
  SELF_NODE_ENTRY,
  TRAINING_LOOP_ENTRY,
  hubServerSpec,
  resolveCloudflaredBin as specsResolveCloudflaredBin,
  selfNodeSpec,
  trainingLoopSpec,
} from './specs'
import { slotOf, slotPort } from '../core/slots'
import { seedWeightsFromBc } from './courses'
import type { RlConfig } from '../core/types'

// ──────────────────────────────────────────────────── cloudflared 辅助 ──────────────────────────

function extractCfUrls(logPath: string): string[] {
  try {
    return readFileSync(logPath, 'utf-8').match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/g) ?? []
  } catch {
    return []
  }
}

/** cloudflared 本地 metrics /ready：200 = edge 连接已注册（隧道建立）。
 *  纯本机检查——用它判定"隧道死活"，避免 hub 出网劣化造成假阴性。 */
async function tunnelEdgeReady(metricsPort: number | undefined): Promise<boolean> {
  if (!metricsPort) return false
  return httpOk(`http://127.0.0.1:${metricsPort}/ready`, '', 3000)
}

// ────────────────────────── 健康谓词 ──────────────────────────

export async function selfNodeHealthy(cfg: RlConfig): Promise<boolean> {
  if (!(await portListen(cfg.rl.agent_port))) return false
  const selfKey = cfg.nodes.find((n) => n.id === 'self')?.authKey ?? ''
  return httpOk(`http://127.0.0.1:${cfg.rl.agent_port}/v1/ping`, selfKey)
}

export async function hubServerHealthy(cfg: RlConfig, course = ''): Promise<boolean> {
  const port = slotPort(cfg, course, 'hub')
  if (!(await portListen(port))) return false
  return httpOk(`http://127.0.0.1:${port}/ping`, cfg.rl.remote_token)
}

// ────────────────────────── 端口回收（2026-09-17 事故修复） ──────────────────────────

/** reclaimPort 的可注入依赖（测试用；默认走 OS 进程表 / 真实 kill / 真实探测）。 */
export interface ReclaimPortIO {
  ownerPids?: (port: number) => number[]
  kill?: (pid: number) => Promise<unknown>
  listening?: (port: number) => Promise<boolean>
}

/** 杀掉端口上的幸存占用者，等端口真正释放；返回被回收的 PID 清单。
 *
 *  **为什么必须有这一层**（2026-09-17 事故：hub-server 重启死锁，用户「手动重启失败」）：
 *  组件健康检查失败 ⇒ 控制台「启动」spawn 新实例，但**旧实例还活着占着端口**
 *  （孤儿 / 登记丢失 / 控制台重启竞态留下的僵尸）⇒ python 侧的双监听守卫
 *  （`remote/_port_guard.ensure_port_free`）拒绝启动 ⇒ 新进程秒退 ⇒ 控制台报
 *  「启动即退出」。而那个幸存者可能正处于**只有重启才能清除**的状态——hub-server 的
 *  D9 闭锁（127.0.0.1 连续 5 次鉴权失败封 3600s，封禁只住进程内存）就是典型：
 *  重启是唯一的解药，而重启恰好被它自己占着的端口挡死 = 死锁，只能手动杀进程。
 *
 *  **调用契约：只在健康检查已判定组件不可用时调用**（`stepSelfNode`/`stepHubServer`
 *  都在这之后）。健康且可用的组件在上层就被短路复用了，端口绝不会被回收。
 *  与 cloudflared 的 `supersedeSlotTunnels` 同族：同一资源（同槽 = 同端口）只允许
 *  一个活实例，换代时先清口再起。
 */
export async function reclaimPort(port: number, io: ReclaimPortIO = {}): Promise<number[]> {
  // 永不回收 console 自己（它可能正好是这个端口上的某个客户端）。
  // 清单 = 「决定回收」的占用者（不因单次 kill 抛错就漏记——已死/权限不足同样要
  // 计入，且必须等端口真正释放）。
  const struck = (io.ownerPids ?? portOwnerPids)(port).filter((pid) => pid !== process.pid)
  for (const pid of struck) {
    warn(
      `端口 ${port} 被幸存进程 (PID ${pid}) 占用——先回收再启动` +
        '（否则新实例会被双监听守卫拒绝，控制台只会看到「启动即退出」）',
    )
    try {
      await (io.kill ?? killPid)(pid)
    } catch {
      /* 已死/权限不足：交给下面的释放探测判定 */
    }
  }
  if (struck.length > 0) {
    // 端口释放以探测为准，不做固定等待。
    await waitUntil(async () => !(await (io.listening ?? portListen)(port)), 5000, 200)
  }
  return struck
}

// 端口归属判定住 `core/proc.ts::portOwnedBy`（唯一实现，spec 与启动步骤共用）——
// 本文件不再自带一份，避免又一次「同名两份实现」的漂移。

// ────────────────────────── 组件步骤 ──────────────────────────

/** self-node（sampler-agent）步骤。 */
export async function stepSelfNode(cfg: RlConfig): Promise<void> {
  log('检查 self-node (sampler-agent)...')
  if (await selfNodeHealthy(cfg)) {
    ok(`self-node 已在运行 (port ${cfg.rl.agent_port})`)
    return
  }
  log('启动 self-node...')
  // 端口回收（必须在 spawn 前，契约见 reclaimPort）：健康检查已失败 ⇒ 端口上的
  // 幸存者不可用，先清口再起，否则新实例撞 EADDRINUSE 秒退。
  await reclaimPort(cfg.rl.agent_port)
  const spec = selfNodeSpec(cfg)
  const r = launchSpec(spec)
  saveComponent('selfNode', { pid: r.pid, entry: SELF_NODE_ENTRY })
  monitorTouch()
  if (await waitUntil(() => selfNodeHealthy(cfg), 30000)) {
    ok(`self-node 启动成功 (port ${cfg.rl.agent_port}, PID ${r.pid})`)
  } else {
    fail('self-node 启动失败（30s 内健康检查未通过，见 tmp/sampler-agent.log）')
    throw new Error('self-node 启动失败')
  }
}

/** jobRoot（tmp/<course>/remote-jobs）→ 课程名。 */
function courseOf(jobRoot: string): string {
  return path.basename(path.dirname(jobRoot))
}

/** hub-server（python remote.hub_server）步骤。 */
export async function stepHubServer(cfg: RlConfig, jobRoot: string): Promise<void> {
  const course = courseOf(jobRoot)
  const port = slotPort(cfg, course, 'hub')
  log('检查 hub-server...')
  if (await hubServerHealthy(cfg, course)) {
    ok(`hub-server 已在运行 (port ${port})`)
    return
  }
  mkdirSync(jobRoot, { recursive: true })
  // 端口回收（必须在 spawn 前，2026-09-17 事故）：健康检查已失败 ⇒ 端口上的幸存者
  // （孤儿 / 登记丢失 / 处于 D9 内存封禁态）不可用，而 python 侧的双监听守卫
  // （remote/_port_guard.ensure_port_free）会拒绝新实例——不回收就是重启被自己的
  // 守卫挡死：新进程秒退，控制台报「启动即退出」，只能手动杀进程。
  await reclaimPort(port)
  log('启动 hub-server...')
  const spec = hubServerSpec(cfg, course)
  const r = launchSpec(spec)
  saveAnyComponent('hubServer', course, {
    pid: r.pid,
    entry: HUB_SERVER_ENTRY,
    course,
    slot: slotOf(cfg, course),
    jobRoot,
    log: spec.log,
    url: `http://127.0.0.1:${port}`,
  })
  monitorTouch()
  // Python 冷启动（import 链）可达 10s+，以 /ping 探测为准，上限 45s
  if (await waitUntil(() => hubServerHealthy(cfg, course), 45000)) {
    ok(`hub-server 启动成功 (port ${port}, PID ${r.pid})`)
  } else {
    fail(`hub-server 启动失败（45s 内未就绪，见 ${spec.log}）`)
    throw new Error('hub-server 启动失败')
  }
}

/** 同槽位隧道接管（2026-09-14 事故修复）：槽位是端口独占单位（同 slot = 同 hub 目标），
 *  绝不允许两门课程的隧道同时占同一槽。某课程停训后残留的存活隧道（如 c6-chip）会
 *  长期占死该槽 metrics 端口——新课程隧道 `--metrics` bind 失败即退（"Only one usage
 *  of each socket address"）→ 控制台「启动失败」。启动新隧道前，先杀掉并清账同槽位
 *  其它课程的存活隧道，保证「启动即就绪」。
 *
 *  返回被接管课程的清单（日志/测试断言用）；异槽位/死 pid/同课自身条目一律不动。 */
export async function supersedeSlotTunnels(course: string, slot: number): Promise<string[]> {
  const struck: string[] = []
  const reg = loadRegistry()
  for (const [owner, ent] of Object.entries(reg.cloudflareds ?? {})) {
    if (owner === course) continue
    if ((ent.slot ?? 0) !== slot) continue
    if (!pidAlive(ent.pid)) continue
    warn(
      `slot ${slot} 的隧道由课程 ${owner} 占用（PID ${ent.pid}）——先停止旧隧道，为 ${course} 接管`,
    )
    await killPid(ent.pid)
    clearAnyComponent('cloudflared', owner)
    struck.push(owner)
  }
  return struck
}

/** cloudflared tunnel 步骤（3 次申请重试；URL 以日志输出为触发）。
 *  course 决定登记归属（P1b 按课程键控）；隧道自身的 per-course 端口/URL 是 P3。 */
export async function stepCloudflared(
  cfg: RlConfig,
  noTunnel = false,
  course = '',
): Promise<string> {
  log('检查 cloudflared tunnel...')
  if (noTunnel) {
    info('已指定 --no-tunnel——跳过隧道（Kaggle 路径本轮不验证）')
    return ''
  }
  const prev = entryForCourse(loadRegistry(), 'cloudflared', course)
  const cfBin = specsResolveCloudflaredBin()
  if (!cfBin) {
    fail('cloudflared 不在 PATH 中——Kaggle 无法接入（安装 cloudflared，或显式 --no-tunnel 跳过）')
    throw new Error('cloudflared 未安装')
  }

  // 已有登记的隧道：edge 就绪（本地 /ready）即复用；穿隧道 ping 失败可能是
  // hub 出网劣化——只有 edge 未注册才重启
  if (prev && pidAlive(prev.pid) && prev.url) {
    const url = prev.url
    const edgeReady = await tunnelEdgeReady(prev.metrics)
    if (edgeReady || (await httpOk(`${url}/ping`, cfg.rl.remote_token, 10000))) {
      if (edgeReady) ok(`cloudflared 已在运行（edge 在线）: ${url}`)
      else ok(`cloudflared 已在运行: ${url}`)
      return url
    }
    warn('cloudflared 进程存在但 edge 未连接，重启中...')
    await killPid(prev.pid)
  }

  // 隧道 per-course（P3）：槽位取自 rl-config courses 块（未配置 → 0 = 旧单课行为）；
  // 每课独立 cloudflared 进程，各自指向本课 hub 端口。
  const slot = slotOf(cfg, course)
  const metricsPort = slotPort(cfg, slot, 'metrics')
  const hubPort = slotPort(cfg, slot, 'hub')
  // 同槽位接管（2026-09-14 事故）：先杀其它课程残留占同一槽的存活隧道，避免本课隧道
  // `--metrics` bind 失败即退。监督器只重启被哨兵变化的活进程，杀掉 + 清账后就无复活。
  const superseded = await supersedeSlotTunnels(course, slot)
  if (superseded.length > 0) ok(`已接管 slot ${slot} 的隧道（原属 ${superseded.join('、')}）`)
  // 端口回收（必须在 spawn 前，2026-09-17 同族修复）：cloudflared 是**第三方二进制**，
  // 没法在它内部装实例锁（hub/worker 是 python 自己拿 `O_CREAT|O_EXCL`），所以控制台侧的
  // 回收就是它唯一的一道闸。supersede 只看得见**账本里**的同槽隧道，挡不住孤儿/登记丢失/
  // 控制台重启竞态留下的幸存者；而 metrics 端口既是 `--metrics` 的 bind 目标、又是后面
  // `/ready` 的探测目标，被幸存者占着会同时造成「新隧道 bind 失败」与「就绪读到别人的隧道」。
  const reclaimed = await reclaimPort(metricsPort)
  if (reclaimed.length > 0) ok(`已回收 metrics 端口 ${metricsPort} 的幸存占用者`)
  let url: string | null = null
  let procPid = 0
  let cfLog = ''
  for (let attempt = 1; attempt <= 3; attempt++) {
    log(`启动 cloudflared tunnel（第 ${attempt}/3 次尝试）...`)
    cfLog = path.join(LOG_DIR, `cloudflared-${fmtStamp()}-a${attempt}.log`)
    const r = spawnBg(
      [
        cfBin,
        'tunnel',
        '--url',
        `http://localhost:${hubPort}`,
        '--metrics',
        `127.0.0.1:${metricsPort}`,
        '--logfile',
        cfLog,
      ],
      { log: cfLog },
    )
    procPid = r.pid
    saveAnyComponent('cloudflared', course, {
      pid: r.pid,
      log: cfLog,
      metrics: metricsPort,
      slot,
      course,
    })
    monitorTouch()

    // URL 以 cloudflared 日志输出为触发（取最后一个），单次上限 45s
    await waitUntil(
      async () => {
        const urls = extractCfUrls(cfLog)
        if (urls.length > 0) {
          url = urls[urls.length - 1]
          return true
        }
        if (!pidAlive(procPid)) return true // 提前退出，外层报错
        return false
      },
      45000,
      1000,
    )
    if (url) break
    fail(`第 ${attempt}/3 次尝试未获取 URL（quick Tunnel 申请超时/进程退出）— 见 ${cfLog}`)
    if (pidAlive(procPid)) await killPid(procPid)
    if (attempt < 3) await Bun.sleep(3000)
  }

  if (!url || !procPid) {
    fail(
      'cloudflared tunnel URL 获取失败（3 次尝试均未通过）——判定启动失败；基础设施保留，稍后重跑即可复用',
    )
    throw new Error('cloudflared tunnel URL 获取失败')
  }

  saveAnyComponent('cloudflared', course, {
    pid: procPid,
    url,
    log: cfLog,
    metrics: metricsPort,
    slot,
    course,
  })

  // 隧道死活以本地 /ready 为准（不依赖出网）；穿隧道 ping 失败只降级为警告。
  // 归属前置（2026-09-17）：必须确认 metrics 端口是**本进程**持有的，否则旧僵尸答的 200
  // 会被当成新隧道的就绪（见 core/proc.ts::portOwnedBy）。
  const edgeReady = await waitUntil(
    async () => (await portOwnedBy(procPid, metricsPort)) && (await tunnelEdgeReady(metricsPort)),
    20000,
    500,
  )
  if (!edgeReady) {
    fail(
      'cloudflared edge 连接未注册（20s）或 metrics 端口非本隧道持有——隧道未建立，' +
        '判定启动失败；基础设施保留，稍后重跑本脚本即可复用',
    )
    throw new Error('cloudflared edge 未注册')
  }
  const pingOk = await httpOk(`${url}/ping`, cfg.rl.remote_token, 8000)
  if (pingOk) ok(`cloudflared tunnel 已就绪: ${url}`)
  else
    warn(
      '隧道 edge 在线，但 hub 出网探测未通过（hub→CF 劣化）——Kaggle 入站路径不受影响，继续（预演阶段实测连通性）',
    )

  writeRemoteHubUrl(url, course)
  return url
}

// ────────────────────────── 冒烟门禁 ──────────────────────────

/** 节点可用性检测（并行 ping，报告 codeHash/cpus/agent 版本）。 */
export async function stepNodesCheck(cfg: RlConfig): Promise<void> {
  log('检查 rollout 节点可用性...')
  const enabled = cfg.nodes.filter((n) => n.enabled)
  const outcomes = await Promise.all(
    enabled.map(async (node) => {
      try {
        const resp = await fetch(`${node.url}/v1/ping`, {
          headers: { Authorization: `Bearer ${node.authKey}` },
          signal: AbortSignal.timeout(5000),
        })
        if (resp.status === 200) {
          const body = (await resp.json()) as Record<string, unknown>
          const ch = String(body.codeHash ?? '').slice(0, 12)
          ok(`${node.id} 在线: codeHash=${ch}... cpus=${body.cpus} agent=${body.agentVersion}`)
          return true
        } else if (resp.status === 401) {
          warn(`${node.id} 返回 401（authKey 可能不匹配）`)
        } else {
          warn(`${node.id} 不可达 (HTTP ${resp.status})`)
        }
      } catch {
        warn(`${node.id} 不可达（连接失败）`)
      }
      return false
    }),
  )
  if (outcomes.every(Boolean)) ok('所有 rollout 节点就绪')
  else warn('部分节点不可用，local 模式兜底')
}

/** 基础设施冒烟：hub 本地可达 + 隧道可达 + code.zip 可下载（硬门）。 */
export async function stepSmokeTest(
  cfg: RlConfig,
  cfUrl: string | null,
  noTunnel = false,
  course = '',
): Promise<void> {
  log('运行基础设施冒烟测试...')
  const hubOk = await httpOk(
    `http://127.0.0.1:${slotPort(cfg, course, 'hub')}/ping`,
    cfg.rl.remote_token,
  )
  if (!hubOk) {
    fail('hub-server 不可达')
    throw new Error('hub-server 不可达')
  }
  ok('hub-server 本地可达')

  if (noTunnel) {
    info('已指定 --no-tunnel——跳过隧道/code.zip 检查（Kaggle 路径本轮不验证）')
    return
  }
  if (!cfUrl) {
    fail('隧道 URL 缺失——Kaggle 无法接入')
    throw new Error('隧道 URL 缺失')
  }

  // 隧道判定分两级：穿隧道 ping 失败但 edge 在线 = hub 出网劣化 → 警告继续；
  // edge 未建立 = 隧道真死 → 硬失败。code.zip 走同一出网路径，ping 失败时跳过。
  const pingOk = await httpOk(`${cfUrl}/ping`, cfg.rl.remote_token, 10000)
  const edgeReady = pingOk
    ? true
    : await tunnelEdgeReady(entryForCourse(loadRegistry(), 'cloudflared', course)?.metrics)
  if (!pingOk && !edgeReady) {
    fail('cloudflared tunnel 不可达（edge 未建立）——Kaggle 无法连接')
    throw new Error('cloudflared tunnel 不可达')
  }
  if (pingOk) ok(`cloudflared tunnel 可达: ${cfUrl}`)
  else warn('隧道 edge 在线，但 hub 出网探测未通过——Kaggle 入站不受影响（code.zip 检查跳过）')
  if (!pingOk) return

  try {
    const resp = await fetch(`${cfUrl}/code`, {
      headers: { Authorization: `Bearer ${cfg.rl.remote_token}` },
      signal: AbortSignal.timeout(10000),
    })
    if (resp.status !== 200) {
      fail(`code.zip 不可下载（HTTP ${resp.status}）——Kaggle 初始化会失败`)
      throw new Error('code.zip 不可下载')
    }
    const len = resp.headers.get('content-length') ?? '0'
    ok(`code.zip 可下载 (${len} bytes) — Kaggle 可接入`)
  } catch (e) {
    if (e instanceof Error && e.message === 'code.zip 不可下载') throw e
    fail('code.zip 下载失败——Kaggle 初始化会失败')
    throw new Error('code.zip 下载失败')
  }
}

// ────────────────────────── TrainingLoop ──────────────────────────

/** 打印 logPath 中 offset 之后的新增行（尾部 lines 行）。 */
export function printLogTail(logPath: string, offset = 0, lines = 5): void {
  try {
    const size = statSync(logPath).size
    if (size <= offset) return
    const fh = openSync(logPath, 'r')
    const buf = Buffer.alloc(size - offset)
    readSync(fh, buf, 0, buf.length, offset)
    closeSync(fh)
    const tail = buf.toString('utf-8').split('\n').filter(Boolean).slice(-lines)
    for (const line of tail) info(line)
  } catch {
    /* log may not exist yet */
  }
}

/** 下架陈旧 pending job：TrainingLoop 不在运行时，队列里所有未完成 job 都来自
 *  已死运行——真 Kaggle worker 会白白烧 GPU 租约去领它们。删除 payload 容器文件
 *  使其不可被领取（hub_server.claimable_job_ids 以 find_payload 的存在性判定）；
 *  账本 job_pending 保留真实历史。 */
export function drainStaleJobs(jobRoot: string, jsonlPath: string): void {
  const pending = new Set<string>()
  const completed = new Set<string>()
  try {
    for (const line of readFileSync(jsonlPath, 'utf-8').split('\n')) {
      if (!line.trim()) continue
      try {
        const e = JSON.parse(line) as { event?: string; job_id?: string }
        if (e.event === 'job_pending' && e.job_id) pending.add(e.job_id)
        if (e.event === 'job_completed' && e.job_id) completed.add(e.job_id)
      } catch {
        /* skip bad line */
      }
    }
  } catch {
    /* no ledger yet */
  }
  let n = 0
  for (const jid of pending) {
    if (completed.has(jid)) continue
    const dir = path.join(jobRoot, jid)
    try {
      // 按前缀扫描而非硬编码单一名字：容器名由 nn-training/remote/protocol.py 的
      // PAYLOAD_NAME 决定（zip/deflate → tar.xz(3)，2026-09-10），硬编码 'payload.zip'
      // 的话 unlinkSync 恒抛 → 被下面的 catch 吞掉 → 陈旧 job 永不下架、真 worker
      // 白烧租约（正是本函数要防的事）。扫描可免疫后续再次改名。
      let removed = false
      for (const name of readdirSync(dir)) {
        if (!name.startsWith('payload.')) continue
        unlinkSync(path.join(dir, name))
        removed = true
      }
      if (removed) n++
    } catch {
      /* already gone */
    }
  }
  if (n > 0) info(`已下架 ${n} 个陈旧 pending job（来自已结束的运行，避免真 worker 空烧租约）`)
  else info('无陈旧 pending job')
}

export interface TrainingLoopSpec {
  course: string
  weightsPath: string
  jobRoot: string
  jsonlPath: string
  smoke: boolean
  /** PPO 模式：remote=pull/push 远程结算（默认）；local=本机 CPU PPO。 */
  ppo?: 'local' | 'remote'
  /** push 模式冒烟：本机伪 GPU 节点（worker_server）URL，注入 REMOTE_PUSH_NODE。 */
  pushNodeUrl?: string
  /** 已就绪的 venv 解析结果（复用，避免重复解析）。 */
  venv: { python: string; sitePackages: string }
}

/** TrainingLoop 步骤（新启动返回 true；已在运行返回 false）。 */
export async function stepTrainingLoop(cfg: RlConfig, s: TrainingLoopSpec): Promise<boolean> {
  log('检查 TrainingLoop...')
  const prevTl = entryForCourse(loadRegistry(), 'trainingLoop', s.course)
  if (pidAlive(prevTl?.pid)) {
    ok(`TrainingLoop 已在运行 (PID ${prevTl!.pid}, course=${s.course})`)
    return false
  }
  drainStaleJobs(s.jobRoot, s.jsonlPath)

  // 确保初始权重存在（课程 BC 种子唯一复制点经 seedWeightsFromBc；缺文件抛错 fail loud）。
  if (!existsSync(s.weightsPath)) {
    seedWeightsFromBc(s.course, s.weightsPath)
    ok(`初始权重已播种到 ${s.weightsPath}`)
  }

  log(`启动 TrainingLoop (course=${s.course})...`)
  const trainLog = path.join(LOG_DIR, s.course, 'training-loop.log')
  mkdirSync(path.dirname(trainLog), { recursive: true })

  // 日志基线 = spawn 前的文件大小：就绪判定与尾部打印只看本次启动的产出。
  let baseline = 0
  try {
    baseline = statSync(trainLog).size
  } catch {
    /* first run */
  }

  const spec = trainingLoopSpec(cfg, {
    course: s.course,
    ppo: s.ppo,
    smoke: s.smoke,
    pushNodeUrl: s.pushNodeUrl,
    venv: s.venv,
  })
  const r = launchSpec(spec)
  saveAnyComponent('trainingLoop', s.course, {
    pid: r.pid,
    course: s.course,
    slot: 0,
    entry: TRAINING_LOOP_ENTRY,
    mode: s.ppo ?? 'remote',
    pushNodeUrl: s.pushNodeUrl,
    log: trainLog,
  })
  monitorTouch()

  // 就绪以进程存活 + 本次启动的日志产出为触发（上限 20s），无固定等待。
  // 另加 fail-fast：进程秒退且日志含 python "can't open file"（路径错/入口错）时
  // 立即抛错——否则预演/等待逻辑会空烧整个超时窗口等一个永远不来的输出。
  const hasOutput = await waitUntil(
    async () => {
      if (!pidAlive(r.pid)) return true
      try {
        return statSync(trainLog).size > baseline
      } catch {
        return false
      }
    },
    20000,
    500,
  )

  if (!pidAlive(r.pid)) {
    fail(`TrainingLoop 启动失败（PID ${r.pid} 已退出，见 ${trainLog}）`)
    printLogTail(trainLog, baseline)
    const tail = tailText(trainLog, baseline)
    if (tail.includes("can't open file")) {
      throw new Error(
        `TrainingLoop 入口文件打不开（cmd[2]=${spec.cmd[2]}）——检查 specs.ts 路径拼接: ${tail.split('\n').find((l) => l.includes("can't open file")) ?? ''}`,
      )
    }
    throw new Error('TrainingLoop 启动失败')
  }
  ok(`TrainingLoop 已启动 (PID ${r.pid})`)
  if (!hasOutput) {
    warn('TrainingLoop 进程存活但 20s 内未产生日志输出（继续观察）')
  } else {
    printLogTail(trainLog, baseline)
  }
  return true
}

/** baseline 之后的日志文本（字节偏移起读；无文件返回空串）。 */
function tailText(logPath: string, offset: number): string {
  try {
    const b = readFileSync(logPath)
    return b.toString('utf-8', Math.min(offset, b.length))
  } catch {
    return ''
  }
}

// ────────────────────────── BC 冒烟预演（BcLoop --smoke 专属，2026-09-13） ──────────────────────────

/** BC 冒烟预演：真 BC 课程 run_bc.py --smoke（REMOTE_PUSH_NODE=本机伪 GPU 节点）
 *  走全链 —— 语料采集（1 局 God-AI）→ 发布 kind=bc job → 伪节点**真 BC 训练**
 *  1 epoch → 回传落位 → 落位即作废退出（不覆盖 out、不归档、账本零污染）。
 *  三里程碑：published job → weights landed → BC SMOKE PASS + 进程退出。 */
export async function stepBcSmokeRehearsal(course: string, tlPid: number): Promise<void> {
  log('── BC 冒烟预演（BcLoop：语料采集 → kind=bc job → 伪 GPU 节点真 BC 训练 → 作废）──')
  const trainLog = path.join(LOG_DIR, course, 'training-loop.log')
  let baseline = 0
  try {
    baseline = statSync(trainLog).size
  } catch {
    /* 新课程 */
  }
  const tailSince = (): string => tailText(trainLog, baseline)

  // 1) 语料采集 + 发布（BC 采集 1 局 God-AI 为秒级；180s 预算含节点升级波）
  const published = await waitUntil(async () => tailSince().includes('published job'), 180000, 2000)
  if (!published) {
    fail('BcLoop 180s 内未发布 bc job——见 training-loop.log（语料采集/节点可用性）')
    printLogTail(trainLog, baseline)
    throw new Error('BC 预演失败：job 未发布')
  }
  ok('BcLoop 已采集语料并发布真 bc job')

  // 2) 等 BC 结果回传 + verify_and_land_bc 落位（伪节点含 torch 导入 + 1 epoch）
  const landed = await waitUntil(async () => tailSince().includes('weights landed'), 300000, 1000)
  if (!landed) {
    fail('300s 内未见 weights landed——推送/BC 训练/回传链路有断点')
    printLogTail(trainLog, baseline)
    throw new Error('BC 预演失败：结果未落位')
  }
  ok('BC 权重回传落位（push → 云端 BC 分支 → verify_and_land_bc 全通过）')

  // 3) 作废退出确认：BC SMOKE PASS 且进程干净退出
  const passed = await waitUntil(
    async () => tailSince().includes('BC SMOKE PASS') && !pidAlive(tlPid),
    120000,
    1000,
  )
  if (!passed) {
    fail('未确认 BC SMOKE PASS 退出（--smoke 应在落位后作废退出）')
    printLogTail(trainLog, baseline)
    throw new Error('BC 预演失败：作废退出未确认')
  }
  ok('BcLoop 已作废 smoke 轮并干净退出（不覆盖 out、不归档、账本零污染）')
}

// ────────────────────────── Kaggle 交互预演（--smoke-only 专属） ──────────────────────────

/** Kaggle 交互预演：真课程 TrainingLoop（--smoke）发布 job 后，用与 Kaggle notebook
 *  完全相同的 remote_worker 代码路径（--echo 冒烟回显，不跑 PPO）穿隧道完成
 *  claim→payload→code→result 一整趟，TrainingLoop 三重校验落位后识别 smoke 标记
 *  作废本轮并干净退出——it 不前进、账本零污染（DECISIONS §340）。 */
export async function stepKaggleRehearsal(course: string, tlPid: number): Promise<void> {
  log('── Kaggle 交互预演（push 模式：trainer 推送 → 本机伪 GPU 节点 echo）──')
  const trainLog = path.join(LOG_DIR, course, 'training-loop.log')

  // 日志基线 = 预演开始时刻（字节偏移），就绪判定只看本次产出。
  let baseline = 0
  try {
    baseline = statSync(trainLog).size
  } catch {
    /* 新课程 */
  }
  const tailSince = (): string => {
    try {
      const b = readFileSync(trainLog)
      return b.toString('utf-8', Math.min(baseline, b.length))
    } catch {
      return ''
    }
  }

  // 1) 等 TrainingLoop 发布 job 并推送（输出触发；真实 rollout 收集需 ~20-60s）
  const published = await waitUntil(async () => tailSince().includes('published job'), 180000, 2000)
  if (!published) {
    fail('TrainingLoop 180s 内未发布 job——见 training-loop.log')
    throw new Error('Kaggle 预演失败：job 未发布')
  }
  ok('TrainingLoop 已发布真 job 并推送')

  // 2) 等 push 节点回显结果被三重校验落位
  const landed = await waitUntil(async () => tailSince().includes('weights landed'), 120000, 1000)
  if (!landed) {
    fail('120s 内未见 weights landed——push/echo/落位链路有断点')
    printLogTail(trainLog, baseline)
    throw new Error('Kaggle 预演失败：结果未落位')
  }
  ok('三重校验落位（HUB 推 → worker_server echo 回显 → verify_and_land 全通过）')

  // 作废确认双信号（满足其一即可）：日志出现作废标记，或 --smoke 进程干净退出
  //（ALL DONE）。旧判据只认"作废标记 && 进程已退"，而 p4 系课程的作废文案
  // 是 GBK 乱码化中文 + 退出路径带 "ALL DONE"，180s 预演实测会假阴性。
  const voidMark = (): boolean =>
    tailSince().includes('冒烟回显已作废') ||
    tailSince().includes('result.smoke') ||
    tailSince().includes('ALL DONE')
  const voided = await waitUntil(
    async () =>
      (voidMark() && !pidAlive(tlPid)) || (voidMark() && tailSince().includes('ALL DONE')),
    120000,
    1000,
  )
  if (!voided) {
    fail('未确认作废退出（--smoke 应在作废本轮后退出）')
    printLogTail(trainLog, baseline)
    throw new Error('Kaggle 预演失败：作废退出未确认')
  }
  ok('TrainingLoop 已作废本轮并干净退出（it 不前进、无 iteration 事件，真训练零污染）')
}
