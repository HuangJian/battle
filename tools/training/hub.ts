/** hub.ts — 基建组件启动步骤 + 冒烟门禁 + Kaggle 交互预演。职责拆分自
 *  tools/hub-start.ts（DECISIONS §339/§340 行为不变）；组件 spec 构造统一在
 *  specs.ts（DECISIONS §349），本层只做编排（检查→spawn→等就绪）。
 */

import {
  copyFileSync,
  existsSync,
  mkdirSync,
  openSync,
  closeSync,
  readSync,
  readFileSync,
  statSync,
  unlinkSync,
} from 'fs'
import path from 'path'
import { LOG_DIR, REPO_ROOT, fmtStamp } from './paths'
import { httpOk, killPid, pidAlive, portListen, waitUntil } from './net'
import { loadRegistry, saveComponent } from './registry'
import { launchSpec, spawnBg } from './proc'
import { writeRemoteHubUrl } from './config'
import { fail, info, log, ok, warn } from './log'
import { monitorTouch } from './reload-touch'
import {
  HUB_SERVER_ENTRY,
  SELF_NODE_ENTRY,
  TRAINING_LOOP_ENTRY,
  hubServerSpec,
  resolveCloudflaredBin as specsResolveCloudflaredBin,
  selfNodeSpec,
  trainingLoopSpec,
} from './specs'
import type { RlConfig } from './types'

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

export async function hubServerHealthy(cfg: RlConfig): Promise<boolean> {
  if (!(await portListen(cfg.rl.hub_port))) return false
  return httpOk(`http://127.0.0.1:${cfg.rl.hub_port}/ping`, cfg.rl.remote_token)
}

// ────────────────────────── 组件步骤 ──────────────────────────

/** self-node（sampler-agent）步骤。 */
export async function stepSelfNode(cfg: RlConfig): Promise<void> {
  log('检查 self-node (sampler-agent)...')
  if (await selfNodeHealthy(cfg)) {
    ok(`self-node 已在运行 (port ${cfg.rl.agent_port})`)
    return
  }
  log('启动 self-node...')
  const spec = selfNodeSpec(cfg)
  const r = launchSpec(spec)
  saveComponent('selfNode', { pid: r.pid, entry: SELF_NODE_ENTRY })
  monitorTouch()
  if (await waitUntil(() => selfNodeHealthy(cfg), 30000)) {
    ok(`self-node 启动成功 (port ${cfg.rl.agent_port}, PID ${r.pid})`)
  } else {
    fail('self-node 启动失败（30s 内健康检查未通过，见 nn-training/tmp/sampler-agent.log）')
    throw new Error('self-node 启动失败')
  }
}

/** jobRoot（tmp/<course>/remote-jobs）→ 课程名。 */
function courseOf(jobRoot: string): string {
  return path.basename(path.dirname(jobRoot))
}

/** hub-server（python remote.hub_server）步骤。 */
export async function stepHubServer(cfg: RlConfig, jobRoot: string): Promise<void> {
  log('检查 hub-server...')
  if (await hubServerHealthy(cfg)) {
    ok(`hub-server 已在运行 (port ${cfg.rl.hub_port})`)
    return
  }
  mkdirSync(jobRoot, { recursive: true })
  log('启动 hub-server...')
  const spec = hubServerSpec(cfg, courseOf(jobRoot))
  const r = launchSpec(spec)
  saveComponent('hubServer', { pid: r.pid, entry: HUB_SERVER_ENTRY })
  monitorTouch()
  // Python 冷启动（import 链）可达 10s+，以 /ping 探测为准，上限 45s
  if (await waitUntil(() => hubServerHealthy(cfg), 45000)) {
    ok(`hub-server 启动成功 (port ${cfg.rl.hub_port}, PID ${r.pid})`)
  } else {
    fail('hub-server 启动失败（45s 内未就绪，见 nn-training/tmp/hub-server.out）')
    throw new Error('hub-server 启动失败')
  }
}

/** cloudflared tunnel 步骤（3 次申请重试；URL 以日志输出为触发）。 */
export async function stepCloudflared(cfg: RlConfig, noTunnel = false): Promise<string> {
  log('检查 cloudflared tunnel...')
  if (noTunnel) {
    info('已指定 --no-tunnel——跳过隧道（Kaggle 路径本轮不验证）')
    return ''
  }
  const reg = loadRegistry()
  const cfBin = specsResolveCloudflaredBin()
  if (!cfBin) {
    fail('cloudflared 不在 PATH 中——Kaggle 无法接入（安装 cloudflared，或显式 --no-tunnel 跳过）')
    throw new Error('cloudflared 未安装')
  }

  // 已有登记的隧道：edge 就绪（本地 /ready）即复用；穿隧道 ping 失败可能是
  // hub 出网劣化——只有 edge 未注册才重启
  if (reg.cloudflared && pidAlive(reg.cloudflared.pid) && reg.cloudflared.url) {
    const url = reg.cloudflared.url
    const edgeReady = await tunnelEdgeReady(reg.cloudflared.metrics)
    if (edgeReady || (await httpOk(`${url}/ping`, cfg.rl.remote_token, 10000))) {
      if (edgeReady) ok(`cloudflared 已在运行（edge 在线）: ${url}`)
      else ok(`cloudflared 已在运行: ${url}`)
      return url
    }
    warn('cloudflared 进程存在但 edge 未连接，重启中...')
    await killPid(reg.cloudflared.pid)
  }

  const metricsPort = cfg.rl.hub_port + 1
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
        `http://localhost:${cfg.rl.hub_port}`,
        '--metrics',
        `127.0.0.1:${metricsPort}`,
        '--logfile',
        cfLog,
      ],
      { log: cfLog },
    )
    procPid = r.pid
    saveComponent('cloudflared', { pid: r.pid, log: cfLog, metrics: metricsPort })
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

  saveComponent('cloudflared', { pid: procPid, url, log: cfLog, metrics: metricsPort })

  // 隧道死活以本地 /ready 为准（不依赖出网）；穿隧道 ping 失败只降级为警告。
  const edgeReady = await waitUntil(() => tunnelEdgeReady(metricsPort), 20000, 500)
  if (!edgeReady) {
    fail(
      'cloudflared edge 连接未注册（20s）——隧道未建立，判定启动失败；基础设施保留，稍后重跑本脚本即可复用',
    )
    throw new Error('cloudflared edge 未注册')
  }
  const pingOk = await httpOk(`${url}/ping`, cfg.rl.remote_token, 8000)
  if (pingOk) ok(`cloudflared tunnel 已就绪: ${url}`)
  else
    warn(
      '隧道 edge 在线，但 hub 出网探测未通过（hub→CF 劣化）——Kaggle 入站路径不受影响，继续（预演阶段实测连通性）',
    )

  writeRemoteHubUrl(url)
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
): Promise<void> {
  log('运行基础设施冒烟测试...')
  const hubOk = await httpOk(`http://127.0.0.1:${cfg.rl.hub_port}/ping`, cfg.rl.remote_token)
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
  const edgeReady = pingOk ? true : await tunnelEdgeReady(loadRegistry().cloudflared?.metrics)
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
 *  已死运行——真 Kaggle worker 会白白烧 GPU 租约去领它们。删除 payload.zip 使
 *  其不可被领取；账本 job_pending 保留真实历史。 */
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
    try {
      unlinkSync(path.join(jobRoot, jid, 'payload.zip'))
      n++
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
  const reg = loadRegistry()
  if (pidAlive(reg.trainingLoop?.pid)) {
    ok(`TrainingLoop 已在运行 (PID ${reg.trainingLoop!.pid})`)
    return false
  }
  drainStaleJobs(s.jobRoot, s.jsonlPath)

  // 确保初始权重存在（缺省从 BC 产物复制）
  if (!existsSync(s.weightsPath)) {
    const bcPath = path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
    mkdirSync(path.dirname(s.weightsPath), { recursive: true })
    if (existsSync(bcPath)) {
      copyFileSync(bcPath, s.weightsPath)
      ok(`初始权重已复制到 ${s.weightsPath}`)
    } else {
      warn(`初始权重文件不存在: ${bcPath}`)
    }
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
  saveComponent('trainingLoop', { pid: r.pid, course: s.course, entry: TRAINING_LOOP_ENTRY })
  monitorTouch()

  // 就绪以进程存活 + 本次启动的日志产出为触发（上限 20s），无固定等待
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
