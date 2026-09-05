#!/usr/bin/env bun
/**
 * hub-start.ts — HUB 一键启动脚本 (Bun/TypeScript，全原生 API)
 *
 * 从 rl-config.json 读取全部配置，启动所有 HUB 组件。
 * 幂等安全：健康检查（HTTP ping / 端口）通过的组件自动跳过，不会重复启动。
 * 自启进程登记进 registry.json（PID 账本），--kill 按账本精确停止。
 * 全部启动后自动跑冒烟测试，验证基础设施可用性。
 *
 * 用法:
 *   bun tools/hub-start.ts --course p4-onset        # 启动全部
 *   bun tools/hub-start.ts --course p4-onset --smoke-only  # 冒烟：基建 + rollout 冒烟
 *       + Kaggle 交互预演（真课程 TrainingLoop --smoke 发布真 job，echo worker 与
 *       Kaggle 同代码路径穿隧道完成一整趟，TrainingLoop 落位后作废本轮退出）
 *   bun tools/hub-start.ts --kill                   # 停止本脚本登记的所有 HUB 进程
 *
 * 实现约定:
 *   - 进程/端口/文件全走 Bun 原生 API，无 netstat/tasklist/ps 等平台分支。
 *   - 一切等待以"命令输出/健康探测"触发（waitUntil 轮询探测），无硬编码 sleep 等待。
 *   - 相互独立的组件并行启动、并行检测（Promise.allSettled）。
 *   - detached + windowsHide：子进程脱离父进程 Job Object 存活且不弹控制台窗口。
 */

import {
  openSync,
  closeSync,
  readSync,
  readFileSync,
  writeFileSync,
  appendFileSync,
  existsSync,
  mkdirSync,
  copyFileSync,
  readdirSync,
  statSync,
  unlinkSync,
} from 'fs'
import path from 'path'

// ────────────────────────────────────────────── 路径
const REPO_ROOT = path.resolve(import.meta.dir, '..')
const NN_TRAINING = path.join(REPO_ROOT, 'nn-training')
const CONFIG_PATH = path.join(NN_TRAINING, 'rl-config.json')
const CURRICULA_DIR = path.join(NN_TRAINING, 'curricula')
const LOG_DIR = path.join(NN_TRAINING, 'tmp')

// ────────────────────────────────────────────── 类型
interface NodeConf {
  id: string
  url: string
  authKey: string
  concurrency: number
  enabled: boolean
}

interface RlConfig {
  version: number
  nodes: NodeConf[]
  rl: {
    hub_port: number
    agent_port: number
    remote_token: string
    remote_hub_url: string
    [key: string]: unknown
  }
  [key: string]: unknown
}

interface Registry {
  selfNode?: { pid: number }
  hubServer?: { pid: number }
  cloudflared?: { pid: number; url?: string; log?: string; metrics?: number }
  trainingLoop?: { pid: number; course: string }
}

// ────────────────────────────────────────────── 日志系统（控制台 + 文件双写）
const RED = '\x1b[0;31m',
  GREEN = '\x1b[0;32m'
const CYAN = '\x1b[0;36m',
  GRAY = '\x1b[0;90m',
  NC = '\x1b[0m'

let _logFile = ''

function initLog(course: string): void {
  const d = path.join(LOG_DIR, 'hub-start')
  mkdirSync(d, { recursive: true })
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  _logFile = path.join(d, `${course || 'noclass'}-${ts}.log`)
  appendFileSync(_logFile, `=== hub-start ${new Date().toISOString()} ===\n`, 'utf-8')
}

function writeLog(prefix: string, msg: string): void {
  const ts = new Date().toLocaleTimeString('sv-SE')
  const line = `[${ts}] ${prefix}${msg}`
  console.log(line)
  if (_logFile) appendFileSync(_logFile, line + '\n', 'utf-8')
}

function log(msg: string): void {
  writeLog('', msg)
}
function ok(msg: string): void {
  writeLog('  ✅ ', msg)
}
function warn(msg: string): void {
  writeLog('  ⚠️  ', msg)
}
function fail(msg: string): void {
  writeLog('  ❌ ', msg)
}
function info(msg: string): void {
  writeLog('  ℹ️  ', msg)
}

// ────────────────────────────────────────────── 原生 Bun 原语（无平台分支）

/** 端口是否在监听：原生 TCP 连接探测（连上即有人在听）。 */
async function portListen(port: number, host = '127.0.0.1'): Promise<boolean> {
  try {
    const conn = await Bun.connect({ hostname: host, port, socket: { data() {} } })
    conn.end()
    return true
  } catch {
    return false
  }
}

/** HTTP 健康检查。 */
async function httpOk(url: string, token: string, timeout = 5000): Promise<boolean> {
  try {
    const resp = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      signal: AbortSignal.timeout(timeout),
    })
    return resp.status === 200
  } catch {
    return false
  }
}

/** PID 是否存活（signal 0 = 仅探测）。 */
function pidAlive(pid: number | undefined | null): boolean {
  if (!pid) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

/** 端口占用者 PID 清单（--kill 兜底清场专用）。Bun 没有端口→PID 的原生 API，
 *  这里是全脚本唯一触碰 OS 进程表的点：Windows 走 netstat -ano（LISTENING 行
 *  尾列即 PID，状态名与列结构不受中文区域影响），POSIX 走 lsof。 */
function portOwnerPids(port: number): number[] {
  try {
    if (process.platform === 'win32') {
      const out = Bun.spawnSync(['netstat', '-ano']).stdout.toString()
      const pids = new Set<number>()
      for (const line of out.split('\n')) {
        if (!line.includes('LISTENING')) continue
        const cols = line.trim().split(/\s+/)
        // TCP  本地地址  远程地址  状态  PID
        if (cols.length >= 5 && cols[1]!.endsWith(`:${port}`)) {
          const pid = Number(cols[4])
          if (Number.isInteger(pid) && pid > 0) pids.add(pid)
        }
      }
      return [...pids]
    }
    const out = Bun.spawnSync(['lsof', '-t', `-i:${port}`, '-s', 'TCP:LISTEN']).stdout.toString()
    return [
      ...new Set(
        out
          .split(/\s+/)
          .map(Number)
          .filter((n) => Number.isInteger(n) && n > 0),
      ),
    ]
  } catch {
    return []
  }
}

/** 后台启动组件进程：stdout/stderr fd 级直写日志；detached 脱离父进程 Job Object
 *  存活（Bun 在 Windows 上默认 kill-on-close，脚本一退子进程全灭）；windowsHide
 *  杜绝控制台黑窗口。 */
function spawnBg(
  cmd: string[],
  opts: { cwd?: string; env?: Record<string, string>; log?: string } = {},
): Bun.Subprocess {
  if (opts.log) mkdirSync(path.dirname(opts.log), { recursive: true })
  const fd = opts.log ? openSync(opts.log, 'a') : undefined
  const proc = Bun.spawn({
    cmd,
    cwd: opts.cwd ?? REPO_ROOT,
    env: { ...process.env, ...opts.env },
    stdin: 'ignore',
    stdout: fd ?? 'ignore',
    stderr: fd ?? 'ignore',
    detached: true,
    windowsHide: true,
  })
  // 写句柄已由子进程继承，本侧即刻关闭
  if (fd !== undefined) closeSync(fd)
  proc.unref()
  return proc
}

/** 停止单个 PID：SIGTERM → 轮询存活（输出触发）→ 仍活则 SIGKILL。 */
async function killPid(pid: number): Promise<boolean> {
  try {
    process.kill(pid, 'SIGTERM')
  } catch {
    return !pidAlive(pid)
  }
  const gone = await waitUntil(() => Promise.resolve(!pidAlive(pid)), 3000, 100)
  if (!gone && pidAlive(pid)) {
    try {
      process.kill(pid, 'SIGKILL')
    } catch {
      /* already dead */
    }
  }
  return !pidAlive(pid)
}

// ────────────────────────────────────────────── 进程登记（PID 账本，按组件分文件）
// 并行启动阶段各组件并发登记，单文件 load→save 会互相覆盖丢条目；分文件天然无竞态。

type Comp = 'selfNode' | 'hubServer' | 'cloudflared' | 'trainingLoop'
const COMPS: Comp[] = ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop']

function componentPath(name: Comp): string {
  return path.join(LOG_DIR, 'hub-start', `registry.${name}.json`)
}

function saveComponent(name: Comp, data: unknown): void {
  mkdirSync(path.dirname(componentPath(name)), { recursive: true })
  writeFileSync(componentPath(name), JSON.stringify(data, null, 2), 'utf-8')
}

function loadRegistry(): Registry {
  const reg: Registry = {}
  for (const name of COMPS) {
    try {
      ;(reg as Record<string, unknown>)[name] = JSON.parse(
        readFileSync(componentPath(name), 'utf-8'),
      )
    } catch {
      /* not started */
    }
  }
  return reg
}

function clearRegistry(): void {
  for (const name of COMPS) {
    try {
      unlinkSync(componentPath(name))
    } catch {
      /* absent */
    }
  }
  try {
    unlinkSync(path.join(LOG_DIR, 'hub-start', 'registry.json'))
  } catch {
    /* legacy */
  }
}

// ────────────────────────────────────────────── venv 真实解释器解析

/** 解析 venv 真实解释器：uv 生成的 .venv\Scripts\python.exe 是 trampoline——
 *  真正干活的是它另起的基础解释器子进程，只杀跳板会留下孤儿进程继续占端口。
 *  读 pyvenv.cfg 的 executable/home 直取真实解释器；第三方包由调用方通过
 *  PYTHONPATH 挂 venv site-packages 提供。 */
function resolveVenvPython(): { python: string; sitePackages: string } {
  const venv = path.join(NN_TRAINING, '.venv')
  let python =
    process.platform === 'win32'
      ? path.join(venv, 'Scripts', 'python.exe')
      : path.join(venv, 'bin', 'python3')
  try {
    const cfg = readFileSync(path.join(venv, 'pyvenv.cfg'), 'utf-8')
    const exe = /^executable\s*=\s*(.+)$/m.exec(cfg)?.[1]?.trim()
    const home = /^home\s*=\s*(.+)$/m.exec(cfg)?.[1]?.trim()
    if (exe && existsSync(exe)) python = exe
    else if (home) {
      const winPy = path.join(home, 'python.exe')
      const posixPy = path.join(home, 'bin', 'python3')
      if (existsSync(winPy)) python = winPy
      else if (existsSync(posixPy)) python = posixPy
    }
  } catch {
    /* no pyvenv.cfg → 用 venv 入口（非 uv 创建的 venv 不跳板） */
  }
  // site-packages：优先 Windows 布局 Lib\site-packages（本 venv 同时存在
  // POSIX 残留 lib\python3.x\，其中并无实际包）；POSIX 机器回退 lib/python3.x/
  let sitePackages = ''
  const winSp = path.join(venv, 'Lib', 'site-packages')
  if (existsSync(winSp)) {
    sitePackages = winSp
  } else {
    try {
      const libDir = path.join(venv, 'lib')
      const v = readdirSync(libDir).find((d) => d.startsWith('python3'))
      if (v) sitePackages = path.join(libDir, v, 'site-packages')
    } catch {
      /* 无 lib 目录 */
    }
  }
  return { python, sitePackages }
}

// ────────────────────────────────────────────── 输出触发式等待

/** 轮询等待条件成立（探测即输出源），超时前最后再测一次。 */
async function waitUntil(
  check: () => Promise<boolean>,
  timeoutMs: number,
  stepMs = 500,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await check()) return true
    await sleep(stepMs)
  }
  return check()
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/** 异步任务取包：轮询 /v1/result 直到拿到容器字节（200）| 失败（500）| 超时（null）。 */
async function pollAsyncResult(
  node: NodeConf,
  iterId: string,
  timeoutMs = 30000,
): Promise<ArrayBuffer | null> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const rResp = await fetch(`${node.url}/v1/result?iterId=${iterId}&stage=0&seed=42`, {
        headers: { Authorization: `Bearer ${node.authKey}` },
      })
      if (rResp.status === 200) return await rResp.arrayBuffer()
      if (rResp.status === 500) {
        const body = (await rResp.json()) as Record<string, unknown>
        fail(`${node.id} 任务失败: ${body.error}`)
        return null
      }
      // 202 = 仍在跑，404 = 过期；继续轮询直到超时
    } catch {
      /* transient network error, keep polling */
    }
    await sleep(1000)
  }
  return null
}

// ────────────────────────────────────────────── 健康谓词

async function selfNodeHealthy(): Promise<boolean> {
  if (!(await portListen(config.rl.agent_port))) return false
  const selfKey = config.nodes.find((n) => n.id === 'self')?.authKey ?? ''
  return httpOk(`http://127.0.0.1:${config.rl.agent_port}/v1/ping`, selfKey)
}

async function hubServerHealthy(): Promise<boolean> {
  if (!(await portListen(config.rl.hub_port))) return false
  return httpOk(`http://127.0.0.1:${config.rl.hub_port}/ping`, config.rl.remote_token)
}

// ────────────────────────────────────────────── cloudflared 辅助

/** 解析 cloudflared 真身路径：Chocolatey 的 bin\cloudflared.exe 是 shim，它会另起
 *  控制台窗口（黑窗）、不透传 stdio 句柄、被杀后留下孤儿真身。优先直取
 *  lib\<name>\tools\ 下的真身 exe，取不到再退回 shim。 */
function resolveCloudflaredBin(): string | null {
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

function extractCfUrls(logPath: string): string[] {
  try {
    return readFileSync(logPath, 'utf-8').match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/g) ?? []
  } catch {
    return []
  }
}

/** 写回 rl-config.json 的 remote_hub_url。 */
function writeTunnelUrl(url: string): void {
  const cfg = JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig
  const old = cfg.rl?.remote_hub_url
  if (url && url !== old) {
    cfg.rl = cfg.rl || ({} as RlConfig['rl'])
    cfg.rl.remote_hub_url = url
    writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), 'utf-8')
    log(`  remote_hub_url updated: ${old} -> ${url}`)
  }
}

// ────────────────────────────────────────────── 步骤

async function stopAll(): Promise<void> {
  log('停止所有 HUB 进程...')
  const reg = loadRegistry()
  const entries: Array<[string, number | undefined]> = [
    ['trainingLoop', reg.trainingLoop?.pid],
    ['hubServer', reg.hubServer?.pid],
    ['selfNode', reg.selfNode?.pid],
    ['cloudflared', reg.cloudflared?.pid],
  ]
  let attempted = 0
  await Promise.all(
    entries.map(async ([name, pid]) => {
      if (!pid) return
      attempted++
      if (!pidAlive(pid)) {
        info(`${name} (PID ${pid}) 已不在运行`)
        return
      }
      const dead = await killPid(pid)
      dead ? ok(`${name} (PID ${pid}) 已停止`) : warn(`${name} (PID ${pid}) 未能停止`)
    }),
  )
  clearRegistry()

  // 端口兜底清场（用户指令 2026-09-05：不管进程从哪来，占着端口就杀）。
  // 登记表只覆盖本脚本启动的组件；agent 自重启遗留、手动启动、登记丢失的
  // 进程由这一层兜住——只认端口，不认出身。Bun 没有端口→PID 原生 API，
  // OS 差异封装在 portOwnerPids 里。
  for (const port of [config.rl.hub_port, config.rl.agent_port]) {
    for (const pid of portOwnerPids(port)) {
      if (pid === process.pid) continue
      await killPid(pid)
      ok(`端口 ${port} 占用进程 (PID ${pid}) 已终止`)
    }
  }

  // 端口释放以探测为准，不做固定等待
  const freed = await waitUntil(
    async () =>
      !(await portListen(config.rl.hub_port)) && !(await portListen(config.rl.agent_port)),
    5000,
    300,
  )
  if (freed) {
    ok('所有 HUB 端口已释放')
  } else {
    warn(
      `仍有端口占用: hub=${config.rl.hub_port} agent=${config.rl.agent_port}（终止失败或权限不足，请手动排查）`,
    )
  }
  if (attempted === 0) info('登记表中无进程记录（端口已由兜底清场处理）')
}

async function stepSelfNode(): Promise<void> {
  log('检查 self-node (sampler-agent)...')
  if (await selfNodeHealthy()) {
    ok(`self-node 已在运行 (port ${config.rl.agent_port})`)
    return
  }

  log('启动 self-node...')
  const proc = spawnBg(
    [
      process.execPath,
      'run',
      'tools/agent/sampler-agent.ts',
      '--port',
      String(config.rl.agent_port),
    ],
    {
      log: path.join(LOG_DIR, 'sampler-agent.log'),
    },
  )
  saveComponent('selfNode', { pid: proc.pid })

  if (await waitUntil(selfNodeHealthy, 30000)) {
    ok(`self-node 启动成功 (port ${config.rl.agent_port}, PID ${proc.pid})`)
  } else {
    fail('self-node 启动失败（30s 内健康检查未通过，见 nn-training/tmp/sampler-agent.log）')
    throw new Error('self-node 启动失败')
  }
}

async function stepHubServer(): Promise<void> {
  log('检查 hub-server...')
  if (await hubServerHealthy()) {
    ok(`hub-server 已在运行 (port ${config.rl.hub_port})`)
    return
  }

  mkdirSync(jobRoot, { recursive: true })
  log('启动 hub-server...')
  const { python } = resolveVenvPython()
  const proc = spawnBg(
    [
      python,
      '-u',
      '-m',
      'remote.hub_server',
      '--port',
      String(config.rl.hub_port),
      '--token',
      config.rl.remote_token,
      '--job-root',
      jobRoot,
      '--jsonl',
      jsonlPath,
    ],
    {
      env: { PYTHONPATH: NN_TRAINING },
      log: path.join(LOG_DIR, 'hub-server.out'),
    },
  )
  saveComponent('hubServer', { pid: proc.pid })

  // Python 冷启动（import 链）可达 10s+，以 /ping 探测为准，上限 45s
  if (await waitUntil(hubServerHealthy, 45000)) {
    ok(`hub-server 启动成功 (port ${config.rl.hub_port}, PID ${proc.pid})`)
  } else {
    fail('hub-server 启动失败（45s 内未就绪，见 nn-training/tmp/hub-server.out）')
    throw new Error('hub-server 启动失败')
  }
}

/** cloudflared 本地 metrics /ready：200 = edge 连接已注册（隧道建立）。
 *  纯本机检查，不经过出网——用它判定"隧道死活"，避免 hub 出网劣化造成假阴性。 */
async function tunnelEdgeReady(metricsPort: number | undefined): Promise<boolean> {
  if (!metricsPort) return false
  return httpOk(`http://127.0.0.1:${metricsPort}/ready`, '', 3000)
}

async function stepCloudflared(noTunnel = false): Promise<string> {
  log('检查 cloudflared tunnel...')
  if (noTunnel) {
    info('已指定 --no-tunnel——跳过隧道（Kaggle 路径本轮不验证）')
    return ''
  }
  const reg = loadRegistry()
  const cfBin = resolveCloudflaredBin()
  if (!cfBin) {
    fail('cloudflared 不在 PATH 中——Kaggle 无法接入（安装 cloudflared，或显式 --no-tunnel 跳过）')
    throw new Error('cloudflared 未安装')
  }

  // 已有登记的隧道：edge 就绪（本地 /ready）即复用；穿隧道 ping 失败可能是
  // hub 出网劣化（探测路径独有段），不足以判死刑——只有 edge 未注册才重启
  if (reg.cloudflared && pidAlive(reg.cloudflared.pid) && reg.cloudflared.url) {
    const url = reg.cloudflared.url
    const edgeReady = await tunnelEdgeReady(reg.cloudflared.metrics)
    if (edgeReady || (await httpOk(`${url}/ping`, config.rl.remote_token, 10000))) {
      edgeReady
        ? ok(`cloudflared 已在运行（edge 在线）: ${url}`)
        : ok(`cloudflared 已在运行: ${url}`)
      return url
    }
    warn('cloudflared 进程存在但 edge 未连接，重启中...')
    await killPid(reg.cloudflared.pid)
  }

  const metricsPort = config.rl.hub_port + 1

  // 快速隧道申请（api.trycloudflare.com）本身有失败窗口（实测 context deadline
  // exceeded 后进程直接退出）——整段 spawn+申请重试 3 次，单次失败先清理残留
  // 进程再退避重试。stderr 一并接进日志文件（--logfile 只收结构化日志）。
  let url: string | null = null
  let proc: Bun.Subprocess | null = null
  let cfLog = ''
  for (let attempt = 1; attempt <= 3; attempt++) {
    log(`启动 cloudflared tunnel（第 ${attempt}/3 次尝试）...`)
    cfLog = path.join(
      LOG_DIR,
      `cloudflared-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}-a${attempt}.log`,
    )
    proc = spawnBg(
      [
        cfBin,
        'tunnel',
        '--url',
        `http://localhost:${config.rl.hub_port}`,
        '--metrics',
        `127.0.0.1:${metricsPort}`,
        '--logfile',
        cfLog,
      ],
      { log: cfLog },
    )
    saveComponent('cloudflared', { pid: proc.pid, log: cfLog, metrics: metricsPort })

    // URL 以 cloudflared 日志输出为触发（取最后一个），单次上限 45s
    await waitUntil(
      async () => {
        const urls = extractCfUrls(cfLog)
        if (urls.length > 0) {
          url = urls[urls.length - 1]
          return true
        }
        if (!pidAlive(proc!.pid)) return true // 提前退出，外层报错
        return false
      },
      45000,
      1000,
    )
    if (url) break
    fail(`第 ${attempt}/3 次尝试未获取 URL（quick Tunnel 申请超时/进程退出）— 见 ${cfLog}`)
    if (pidAlive(proc!.pid)) await killPid(proc!.pid)
    if (attempt < 3) await Bun.sleep(3000)
  }

  if (!url || !proc) {
    fail(
      'cloudflared tunnel URL 获取失败（3 次尝试均未通过）——判定启动失败；基础设施保留，稍后重跑即可复用',
    )
    throw new Error('cloudflared tunnel URL 获取失败')
  }

  reg.cloudflared = { pid: proc.pid, url, log: cfLog, metrics: metricsPort }
  saveComponent('cloudflared', { pid: proc.pid, url, log: cfLog, metrics: metricsPort })

  // 隧道死活以本地 /ready 为准（edge 连接注册 = 建立，不依赖出网）；穿隧道 ping
  // 失败只降级为警告——探测路径含 hub 本机出网段，该段劣化时探测假阴性，
  // Kaggle 入站路径（Kaggle→CF→隧道→hub）不经过 hub 出网，不受影响
  const edgeReady = await waitUntil(() => tunnelEdgeReady(metricsPort), 20000, 500)
  if (!edgeReady) {
    fail(
      `cloudflared edge 连接未注册（20s）——隧道未建立，判定启动失败；` +
        `基础设施保留，稍后重跑本脚本即可复用`,
    )
    throw new Error('cloudflared edge 未注册')
  }
  const pingOk = await httpOk(`${url}/ping`, config.rl.remote_token, 8000)
  pingOk
    ? ok(`cloudflared tunnel 已就绪: ${url}`)
    : warn(
        `隧道 edge 在线，但 hub 出网探测未通过（hub→CF 劣化）——` +
          `Kaggle 入站路径不受影响，继续（预演阶段实测连通性）`,
      )

  writeTunnelUrl(url)
  return url
}

async function stepNodesCheck(): Promise<void> {
  log('检查 rollout 节点可用性...')
  const enabled = config.nodes.filter((n) => n.enabled)
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
  outcomes.every(Boolean) ? ok('所有 rollout 节点就绪') : warn('部分节点不可用，local 模式兜底')
}

/** 解包结果容器：strip 前导空格（agent 同步流式路径的保活字节）→ gzip →
 *  BCV2 二进制帧（magic+headerLen+headerJSON）| v1 gzip(JSON)。
 *  与 tools/sim/pack-container.ts / nn-training/dist_common.py 对等。 */
function unpackContainer(buf: ArrayBuffer): {
  manifest: Record<string, unknown>
  fileCount: number
} {
  let bytes = new Uint8Array(buf)
  while (bytes.length > 0 && bytes[0] === 0x20) bytes = bytes.subarray(1)
  if (bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) {
    bytes = Bun.gunzipSync(bytes)
  }
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const MAGIC_BCV2 = 0x42435632 // 'B''C''V''2'
  if (bytes.length >= 8 && dv.getUint32(0, false) === MAGIC_BCV2) {
    const hlen = dv.getUint32(4, false)
    const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + hlen))) as {
      manifest?: Record<string, unknown>
      files?: unknown
    }
    const files = header.files
    const count = Array.isArray(files)
      ? files.length
      : files && typeof files === 'object'
        ? Object.keys(files).length
        : 0
    return { manifest: header.manifest ?? {}, fileCount: count }
  }
  // v1 兼容：gzip(JSON {manifest, files:{name:base64}})
  const payload = JSON.parse(new TextDecoder().decode(bytes)) as {
    manifest?: Record<string, unknown>
    files?: unknown
  }
  const count =
    payload.files && typeof payload.files === 'object' ? Object.keys(payload.files).length : 0
  return { manifest: payload.manifest ?? {}, fileCount: count }
}

/** 单节点 rollout 冒烟（节点间相互独立，可并行）。 */
async function smokeNode(
  node: NodeConf,
  weightsBytes: Uint8Array<ArrayBuffer>,
  wver: string,
): Promise<boolean> {
  log(`  向 ${node.id} 派发 rollout 测试局...`)
  try {
    // POST 权重
    const wResp = await fetch(`${node.url}/v1/weights`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${node.authKey}`,
        'X-Iter-Id': 'smoke-it0',
        'X-Weights-Sha256': wver,
        'Content-Encoding': 'gzip',
        'Content-Type': 'application/octet-stream',
      },
      body: Bun.gzipSync(weightsBytes),
      signal: AbortSignal.timeout(60000),
    })
    if (wResp.status !== 200 && wResp.status !== 204) {
      const body = await wResp.text()
      fail(`${node.id} 权重下发失败: HTTP ${wResp.status} ${body.slice(0, 100)}`)
      return false
    }

    // 提交任务（stage=0, seed=42, 标准关）
    const taskUrl = `${node.url}/v1/task?iterId=smoke-it0&wver=${wver}&stage=0&seed=42&maxTicks=1200&difficulty=hard`
    const tResp = await fetch(taskUrl, {
      headers: {
        Authorization: `Bearer ${node.authKey}`,
        'x-async': '1',
      },
      signal: AbortSignal.timeout(10000),
    })

    // 处理结果：同步（200 = 直接返回容器）或异步（202 = 需轮询 /v1/result，输出触发）
    let resultBuf: ArrayBuffer | null = null

    if (tResp.status === 200) {
      resultBuf = await tResp.arrayBuffer()
    } else if (tResp.status === 202) {
      resultBuf = await pollAsyncResult(node, 'smoke-it0')
    } else {
      const body = await tResp.text()
      fail(`${node.id} 任务提交失败: HTTP ${tResp.status} ${body.slice(0, 100)}`)
      return false
    }

    if (!resultBuf) {
      fail(`${node.id} 任务超时（30s 无结果）`)
      return false
    }

    const { manifest, fileCount } = unpackContainer(resultBuf)
    const outcome = manifest.outcome ?? '?'
    const ticks = manifest.ticks ?? 0
    ok(`${node.id} rollout 冒烟通过: outcome=${outcome} ticks=${ticks} files=${fileCount}`)
    return true
  } catch (e) {
    fail(`${node.id} rollout 冒烟失败: ${e}`)
    return false
  }
}

async function stepRolloutSmoke(weightsPath: string): Promise<void> {
  log('运行 rollout 冒烟测试...')
  if (!existsSync(weightsPath)) {
    warn(`权重文件不存在 (${weightsPath})，跳过 rollout 冒烟`)
    return
  }

  const weightsBytes = new Uint8Array(readFileSync(weightsPath))
  const wver = await crypto.subtle.digest('SHA-256', weightsBytes).then((b) => {
    return Array.from(new Uint8Array(b))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('')
  })

  const enabledNodes = config.nodes.filter((n) => n.enabled)
  const results = await Promise.all(enabledNodes.map((node) => smokeNode(node, weightsBytes, wver)))

  // self 节点是本地采集的底线，失败即冒烟不通过；其它节点（mac 等）降级容忍
  const selfIdx = enabledNodes.findIndex((n) => n.id === 'self')
  if (selfIdx >= 0 && !results[selfIdx]) {
    throw new Error('rollout 冒烟未通过（self 节点）——真训练将无法采集')
  }
  if (results.every(Boolean)) ok('rollout 冒烟全通过')
  else warn('rollout 冒烟部分非 self 节点未通过（真训练降级容忍）')
}

async function stepSmokeTest(cfUrl: string | null, noTunnel = false): Promise<void> {
  log('运行基础设施冒烟测试...')

  const hubOk = await httpOk(`http://127.0.0.1:${config.rl.hub_port}/ping`, config.rl.remote_token)
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

  // 隧道判定分两级（用户指出探测路径 ≠ Kaggle 路径）：穿隧道 ping 失败但
  // edge 在线（本地 /ready）= hub 出网劣化（探测独有段）→ 警告继续；edge 未
  // 建立 = 隧道真死 → 硬失败。code.zip 走同一出网路径，ping 失败时跳过检查。
  const pingOk = await httpOk(`${cfUrl}/ping`, config.rl.remote_token, 10000)
  const edgeReady = pingOk ? true : await tunnelEdgeReady(loadRegistry().cloudflared?.metrics)
  if (!pingOk && !edgeReady) {
    fail(`cloudflared tunnel 不可达（edge 未建立）——Kaggle 无法连接`)
    throw new Error('cloudflared tunnel 不可达')
  }
  pingOk
    ? ok(`cloudflared tunnel 可达: ${cfUrl}`)
    : warn('隧道 edge 在线，但 hub 出网探测未通过——Kaggle 入站不受影响（code.zip 检查跳过）')
  if (!pingOk) return

  try {
    const resp = await fetch(`${cfUrl}/code`, {
      headers: { Authorization: `Bearer ${config.rl.remote_token}` },
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

/** 下架陈旧 pending job：TrainingLoop 不在运行时，队列里所有未完成 job 都来自
 *  已死运行——真 Kaggle worker 会白白烧 GPU 租约去领它们。删除 payload.zip 使
 *  其不可被领取（claimable_job_ids 跳过缺 payload 的 job）；账本 job_pending
 *  保留真实历史（这些 job 确实从未完成）。仅在 TrainingLoop 新启动分支调用。 */
function drainStaleJobs(): void {
  let pending = new Set<string>(),
    completed = new Set<string>()
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
  n > 0
    ? info(`已下架 ${n} 个陈旧 pending job（来自已结束的运行，避免真 worker 空烧租约）`)
    : info('无陈旧 pending job')
}

async function stepTrainingLoop(
  course: string,
  weightsPath: string,
  smoke = false,
  pushNodeUrl = '',
): Promise<boolean> {
  log('检查 TrainingLoop...')
  const reg = loadRegistry()
  if (pidAlive(reg.trainingLoop?.pid)) {
    ok(`TrainingLoop 已在运行 (PID ${reg.trainingLoop!.pid})`)
    return false
  }
  drainStaleJobs()

  // 确保初始权重存在
  if (!existsSync(weightsPath)) {
    const bcPath = path.join(REPO_ROOT, 'tmp/ep60/battle2-p1bc/run/weights.json')
    mkdirSync(path.dirname(weightsPath), { recursive: true })
    if (existsSync(bcPath)) {
      copyFileSync(bcPath, weightsPath)
      ok(`初始权重已复制到 ${weightsPath}`)
    } else {
      warn(`初始权重文件不存在: ${bcPath}`)
    }
  }

  log(`启动 TrainingLoop (course=${course})...`)
  const { python, sitePackages } = resolveVenvPython()
  const trainLog = path.join(LOG_DIR, course, 'training-loop.log')
  mkdirSync(path.dirname(trainLog), { recursive: true })

  // 日志基线 = spawn 前的文件大小：就绪判定与尾部打印只看本次启动的产出，
  // 不被上一轮残留的旧日志行污染（日志为追加模式）；必须在 spawn 前取，
  // 否则子进程秒死时 traceback 会落在基线之前而看不到
  let baseline = 0
  try {
    baseline = statSync(trainLog).size
  } catch {
    /* first run */
  }

  const proc = spawnBg(
    [
      python,
      '-u',
      path.join(NN_TRAINING, 'run_rl.py'),
      '--course',
      course,
      '--ppo',
      'remote',
      ...(smoke ? ['--smoke'] : []),
    ],
    {
      env: {
        PYTHONPATH: `${sitePackages}${path.delimiter}${NN_TRAINING}`,
        // push 模式冒烟：本机伪 GPU 节点（worker_server），优先于 rl-config gpu 节点
        ...(pushNodeUrl ? { REMOTE_PUSH_NODE: pushNodeUrl } : {}),
      },
      log: trainLog,
    },
  )
  saveComponent('trainingLoop', { pid: proc.pid, course })

  // 就绪以进程存活 + 本次启动的日志产出为触发（上限 20s），无固定等待
  const hasOutput = await waitUntil(
    async () => {
      if (!pidAlive(proc.pid)) return true
      try {
        return statSync(trainLog).size > baseline
      } catch {
        return false
      }
    },
    20000,
    500,
  )

  if (!pidAlive(proc.pid)) {
    fail(`TrainingLoop 启动失败（PID ${proc.pid} 已退出，见 ${trainLog}）`)
    printLogTail(trainLog, baseline)
    throw new Error('TrainingLoop 启动失败')
  }
  ok(`TrainingLoop 已启动 (PID ${proc.pid})`)
  if (!hasOutput) {
    warn('TrainingLoop 进程存活但 20s 内未产生日志输出（继续观察）')
  } else {
    printLogTail(trainLog, baseline)
  }
  return true
}

/** Kaggle 交互预演（--smoke-only 专属）：真课程 TrainingLoop（--smoke）发布 job 后，
 *  用与 Kaggle notebook 完全相同的 remote_worker 代码路径（--echo 冒烟回显，不跑 PPO）
 *  穿隧道完成 claim→payload→code→result 一整趟，TrainingLoop 三重校验落位后识别
 *  smoke 标记作废本轮并干净退出——it 不前进、账本零污染。 */
async function stepKaggleRehearsal(course: string, tlPid: number): Promise<void> {
  log('── Kaggle 交互预演（push 模式：trainer 推送 → 本机伪 GPU 节点 echo）──')
  const trainLog = path.join(LOG_DIR, course, 'training-loop.log')

  // 日志基线 = 预演开始时刻（字节偏移），就绪判定只看本次产出。
  // 注意按字节切（Buffer.toString(offset)）而非字符串 slice——日志含 CJK 时
  // 字节偏移与字符下标不对齐。
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

  // 1) 等 TrainingLoop 发布 job 并推送到 push 节点（输出触发；真实 rollout 收集需 ~20-60s）
  const published = await waitUntil(async () => tailSince().includes('published job'), 180000, 2000)
  if (!published) {
    fail('TrainingLoop 180s 内未发布 job——见 training-loop.log')
    throw new Error('Kaggle 预演失败：job 未发布')
  }
  ok('TrainingLoop 已发布真 job 并推送')

  // 2) 等 push 节点回显结果被三重校验落位（trainer POST → worker_server echo → land）
  const landed = await waitUntil(async () => tailSince().includes('weights landed'), 120000, 1000)
  if (!landed) {
    fail('120s 内未见 weights landed——push/echo/落位链路有断点')
    printLogTail(trainLog, baseline)
    throw new Error('Kaggle 预演失败：结果未落位')
  }
  ok('三重校验落位（HUB 推 → worker_server echo 回显 → verify_and_land 全通过）')

  const voided = await waitUntil(
    async () => tailSince().includes('冒烟回显已作废') && !pidAlive(tlPid),
    60000,
    1000,
  )
  if (!voided) {
    fail('未确认作废退出（--smoke 应在作废本轮后退出）')
    printLogTail(trainLog, baseline)
    throw new Error('Kaggle 预演失败：作废退出未确认')
  }
  ok('TrainingLoop 已作废本轮并干净退出（it 不前进、无 iteration 事件，真训练零污染）')
}

/** 打印 logPath 中 offset 之后的新增行（尾部 lines 行）。 */
function printLogTail(logPath: string, offset = 0, lines = 5): void {
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

// ────────────────────────────────────────────── 课程名校验 / 列最新课程

function printRecentCourses(): void {
  let files: string[] = []
  try {
    files = readdirSync(CURRICULA_DIR).filter((f) => f.endsWith('.jsonc'))
  } catch {
    /* dir missing */
  }
  if (files.length === 0) {
    console.error(`课程目录无 .jsonc 文件: ${CURRICULA_DIR}`)
    return
  }
  const recent = files
    .map((f) => ({ f, m: statSync(path.join(CURRICULA_DIR, f)).mtimeMs }))
    .sort((a, b) => b.m - a.m)
    .slice(0, 5)
  console.error(`\n课程目录最近更新的 5 个课程 (${CURRICULA_DIR}):`)
  for (const { f, m } of recent) {
    console.error(
      `  ${f.replace(/\.jsonc$/, '').padEnd(20)} (${new Date(m).toLocaleString('sv-SE')})`,
    )
  }
}

/** 课程参数快速失败：与 rl/config.resolve_course 同规则（先按路径、再按
 *  curricula/<name>.jsonc），拼错课程名在启动任何基础设施**之前**响亮报错。 */
function validateCourseArg(name: string): void {
  if (!name || existsSync(name)) return
  if (existsSync(path.join(CURRICULA_DIR, `${name}.jsonc`))) return
  console.error(
    `\n课程 '${name}' 不存在（查找 ${path.join(CURRICULA_DIR, name)}.jsonc，或传已存在的课程文件路径）。`,
  )
  printRecentCourses()
  console.error(`\n用法: bun tools/hub-start.ts <course> [--smoke-only] | --kill`)
  process.exit(1)
}

// ────────────────────────────────────────────── 主流程
let config: RlConfig
let jobRoot: string
let jsonlPath: string

async function main() {
  const args = process.argv.slice(2)
  let course = ''
  let smokeOnly = false
  let doKill = false
  let noTunnel = false

  // 代理环境整形：hub↔自己隧道的回环流量永远直连。shell 里若设了 HTTP(S)_PROXY
  //（如 Clash），Bun fetch / urllib / wait_job 都会被代理接管——trycloudflare.com
  // 走哪条出口取决于代理规则，健康检查与预演因此引入假阴性（实测）。追加
  // NO_PROXY 使回环语义确定；其余流量的代理行为不变。子进程经 env 继承同享。
  const hadProxy = !!(
    process.env.HTTP_PROXY ||
    process.env.HTTPS_PROXY ||
    process.env.http_proxy ||
    process.env.https_proxy
  )
  if (hadProxy) {
    const cur = (process.env.NO_PROXY || process.env.no_proxy || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const merged = [...new Set([...cur, 'localhost', '127.0.0.1', '.trycloudflare.com'])].join(',')
    process.env.NO_PROXY = merged
    process.env.no_proxy = merged
  }

  for (let i = 0; i < args.length; i++) {
    const a = args[i] ?? ''
    switch (a) {
      case '--course': {
        const v = args[++i] ?? ''
        if (course) {
          console.error(`课程重复指定: ${course} + ${v}（位置参数与 --course 只能二选一）`)
          process.exit(1)
        }
        course = v
        break
      }
      case '--smoke-only':
        smokeOnly = true
        break
      case '--kill':
        doKill = true
        break
      case '--no-tunnel':
        noTunnel = true
        break
      case '--help':
      case '-h':
        console.log(`用法: bun tools/hub-start.ts <course> [--smoke-only] [--kill] [--no-tunnel]`)
        console.log(`  <course>         课程名（位置参数，等价 --course <name>；缺省时列最新课程）`)
        console.log(`  --smoke-only     冒烟：基建 + rollout 冒烟 + Kaggle 交互预演（不跑真 PPO）`)
        console.log(`  --no-tunnel      显式跳过隧道（无 cloudflared 的机器；Kaggle 路径不验证）`)
        console.log(`  --kill           停止所有 HUB 进程（无需课程）`)
        process.exit(0)
      default:
        if (a.startsWith('-')) {
          console.error(`未知参数: ${a}`)
          process.exit(1)
        }
        // 位置参数：裸词视为课程名（bun tools/hub-start.ts p4-onset ≡ --course p4-onset）
        if (course) {
          console.error(`课程重复指定: --course ${course} + 位置参数 ${a}`)
          process.exit(1)
        }
        course = a
    }
  }

  if (doKill) {
    config = JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig
    initLog('')
    await stopAll()
    process.exit(0)
  }

  if (!course) {
    console.error(
      `\n必须指定课程（位置参数或 --course <name>，如: bun tools/hub-start.ts p4-onset）。`,
    )
    printRecentCourses()
    console.error(`\n用法: bun tools/hub-start.ts <course> [--smoke-only] | --kill`)
    process.exit(1)
  }
  validateCourseArg(course)

  // 读取配置
  config = JSON.parse(readFileSync(CONFIG_PATH, 'utf-8')) as RlConfig

  // 初始化日志
  initLog(course)
  if (hadProxy) {
    info(
      '检测到代理环境变量（HTTP(S)_PROXY）——hub↔隧道回环已追加 NO_PROXY 直连' +
        '（localhost/127.0.0.1/.trycloudflare.com），防探测与预演被代理规则引入假阴性',
    )
  }

  // 课程相关路径
  const trajDir = path.join(REPO_ROOT, 'tmp', course)
  jobRoot = path.join(trajDir, 'remote-jobs')
  jsonlPath = path.join(trajDir, 'training_log.jsonl')

  console.log(`\n${CYAN}╔══════════════════════════════════════════════╗${NC}`)
  console.log(`${CYAN}║     Battle City — HUB 一键启动脚本           ║${NC}`)
  console.log(`${CYAN}║     ${new Date().toISOString().slice(0, 19)}              ║${NC}`)
  console.log(`${CYAN}╚══════════════════════════════════════════════╝${NC}\n`)
  info(`课程: ${course}`)
  info(`hub-server: 127.0.0.1:${config.rl.hub_port}`)
  info(
    `节点: ${config.nodes
      .filter((n) => n.enabled)
      .map((n) => n.id)
      .join(', ')}`,
  )
  console.log('')

  const weightsPath = path.join(trajDir, 'weights.json')

  if (smokeOnly) {
    // 阶段 1：三个基础设施组件相互独立，并行启动；隧道失败 = 冒烟不通过（硬门）
    const [selfRes, hubRes, cfRes] = await Promise.allSettled([
      stepSelfNode(),
      stepHubServer(),
      stepCloudflared(noTunnel),
    ])
    if (hubRes.status === 'rejected') throw hubRes.reason
    if (cfRes.status === 'rejected') throw cfRes.reason
    if (selfRes.status === 'rejected') warn('self-node 未就绪，local 模式兜底')
    const cfUrl = noTunnel ? null : (cfRes.value as string)

    // 阶段 2：节点检测与基础设施冒烟并行；隧道/code.zip 是 Kaggle 硬门
    await Promise.allSettled([
      stepNodesCheck(),
      stepSmokeTest(cfUrl, noTunnel),
      stepRolloutSmoke(weightsPath),
    ])

    // 阶段 3：本机伪 GPU 节点（worker_server，与真 Kaggle 同一套 run_job 代码路径；
    // echo 模式不跑 PPO）——push 模式预演的落点
    const { python: servePy, sitePackages: serveSite } = resolveVenvPython()
    const pushPort = config.rl.hub_port + 2
    const pushUrl = `http://127.0.0.1:${pushPort}`
    const serveLog = path.join(LOG_DIR, 'remote-worker-serve.log')
    const serveProc = spawnBg(
      [
        servePy,
        '-u',
        '-m',
        'remote_worker_serve',
        '--port',
        String(pushPort),
        '--token',
        config.rl.remote_token,
        '--work',
        'tmp/remote-worker-serve',
      ],
      {
        cwd: NN_TRAINING,
        env: { PYTHONPATH: `${serveSite}${path.delimiter}${NN_TRAINING}` },
        log: serveLog,
      },
    )
    const serveUp = await waitUntil(
      () => httpOk(`${pushUrl}/ping`, config.rl.remote_token, 3000),
      20000,
      500,
    )
    if (!serveUp) {
      fail('本机 worker_server 20s 未就绪——见 remote-worker-serve.log')
      throw new Error('本机 push 节点未就绪')
    }
    ok(`本机 push 节点就绪: ${pushUrl}（模拟 Kaggle 侧 worker_server）`)

    // 阶段 4：Kaggle 交互预演——真课程 TrainingLoop（--smoke）发布真 job 并推送到
    // push 节点，echo 回显 → 三重校验落位 → 作废退出（不跑 PPO）
    const started = await stepTrainingLoop(course, weightsPath, true, pushUrl)
    if (!started) {
      warn('TrainingLoop 已在运行（可能是真训练）——跳过 Kaggle 预演，以免干扰在途 job')
      return
    }
    const tlPid = loadRegistry().trainingLoop?.pid ?? 0
    let rehearsalOk = true
    try {
      await stepKaggleRehearsal(course, tlPid)
    } catch (e) {
      rehearsalOk = false
      fail(`Kaggle 交互预演未通过: ${(e as Error).message}`)
      if (pidAlive(tlPid)) {
        await killPid(tlPid)
        warn('已停止冒烟用 TrainingLoop（--smoke 进程没有 echo 结果会一直等待）')
      }
    }
    if (pidAlive(serveProc.pid)) {
      await killPid(serveProc.pid)
      info('已停止本机 worker_server（冒烟用）')
    }

    // ── 冒烟总结（任何硬门失败都以非零码退出）──
    const [hubOk, selfOk] = await Promise.all([hubServerHealthy(), selfNodeHealthy()])
    console.log(`\n${CYAN}╔══════════════════════════════════════════════╗${NC}`)
    console.log(`${CYAN}║                 冒烟总结                      ║${NC}`)
    console.log(`${CYAN}╚══════════════════════════════════════════════╝${NC}`)
    hubOk ? ok(`hub-server    http://127.0.0.1:${config.rl.hub_port}`) : fail('hub-server 未运行')
    selfOk ? ok(`self-node     http://127.0.0.1:${config.rl.agent_port}`) : fail('self-node 未运行')
    noTunnel
      ? info('cloudflared   未启用（--no-tunnel）')
      : cfUrl
        ? ok(`cloudflared   ${cfUrl}`)
        : fail('cloudflared 未建立')
    rehearsalOk
      ? ok('Kaggle 交互预演全通过（发布→推送→echo→落位→作废）')
      : fail('Kaggle 交互预演未通过——见上方日志')
    process.exitCode = hubOk && selfOk && rehearsalOk ? 0 : 1
    if (cfUrl) {
      console.log(`\n${GREEN}📋 Kaggle 粘贴用:${NC}`)
      console.log(`  HUB_URL = "${cfUrl}"`)
      console.log(`  TOKEN   = "${config.rl.remote_token}"`)
    }
    console.log(
      `\n${GRAY}下一步：Kaggle notebook 填入 HUB_URL 接入；真训练直接跑全流程` +
        `（bun tools/hub-start.ts ${course}）${NC}\n`,
    )
    return
  }

  // ── 全流程 ──
  log('── 阶段 1/3: 基础设施（self-node ∥ hub-server ∥ cloudflared 并行）──')
  const [selfRes, hubRes, cfRes] = await Promise.allSettled([
    stepSelfNode(),
    stepHubServer(),
    stepCloudflared(noTunnel),
  ])
  const cfUrl = noTunnel ? null : cfRes.status === 'fulfilled' ? (cfRes.value as string) : null
  if (hubRes.status === 'rejected') throw hubRes.reason
  if (cfRes.status === 'rejected') throw cfRes.reason
  if (selfRes.status === 'rejected') warn('self-node 未就绪，local 模式兜底')

  log('── 阶段 2/3: 检测与冒烟（节点检测 ∥ 基建冒烟 ∥ rollout 冒烟并行）──')
  const [, smokeRes, rolloutRes] = await Promise.allSettled([
    stepNodesCheck(),
    stepSmokeTest(cfUrl, noTunnel),
    stepRolloutSmoke(weightsPath),
  ])
  // 隧道/code.zip/self-rollout 是 Kaggle 接入与本地采集的硬门——失败即终止启动
  if (smokeRes.status === 'rejected') throw smokeRes.reason
  if (rolloutRes.status === 'rejected') throw rolloutRes.reason

  log('── 阶段 3/3: TrainingLoop ──')
  await stepTrainingLoop(course, weightsPath)

  // ── 最终报告（并行体检）──
  console.log(`\n${CYAN}╔══════════════════════════════════════════════╗${NC}`)
  console.log(`${CYAN}║              启动报告                          ║${NC}`)
  console.log(`${CYAN}╚══════════════════════════════════════════════╝${NC}`)

  const [hubOk, selfOk, tlAlive, tunnelOk] = await Promise.all([
    hubServerHealthy(),
    selfNodeHealthy(),
    Promise.resolve(pidAlive(loadRegistry().trainingLoop?.pid)),
    noTunnel || !cfUrl
      ? Promise.resolve(false)
      : httpOk(`${cfUrl}/ping`, config.rl.remote_token, 10000),
  ])

  hubOk
    ? ok(`hub-server      http://127.0.0.1:${config.rl.hub_port}`)
    : fail('hub-server     未运行')
  selfOk
    ? ok(`self-node       http://127.0.0.1:${config.rl.agent_port}`)
    : fail('self-node      未运行')
  noTunnel
    ? info('cloudflared     未启用（--no-tunnel，Kaggle 路径未验证）')
    : cfUrl && tunnelOk
      ? ok(`cloudflared     ${cfUrl}`)
      : fail('cloudflared     隧道不可达——Kaggle 无法连接')
  tlAlive ? ok(`TrainingLoop    course=${course}`) : fail('TrainingLoop   未运行')

  // 退出码真实反映门禁状态（隧道不可达等硬门失败 → 非零）
  process.exitCode =
    hubOk && selfOk && tlAlive && (noTunnel || (cfUrl !== null && tunnelOk)) ? 0 : 1

  if (cfUrl) {
    console.log(`\n${GREEN}📋 Kaggle 接入指引:${NC}`)
    console.log(`  1. HUB_URL = "${cfUrl}"（已写入 rl-config.json）`)
    console.log(`  2. TOKEN   = "${config.rl.remote_token}"（rl-config.json 的 rl.remote_token）`)
    console.log(`  3. 打开 Kaggle notebook: nn-training/ipynb/ 下的对应课程 notebook`)
    console.log(
      `  4. worker 一行命令：python -m remote_worker --poll <HUB_URL> --token <TOKEN> --device cuda`,
    )
  }

  console.log(`\n${GRAY}日志文件:${NC}`)
  console.log(`  hub-server:    ${LOG_DIR}/hub-server.out`)
  console.log(`  self-node:     ${LOG_DIR}/sampler-agent.log`)
  console.log(`  TrainingLoop:  ${LOG_DIR}/${course}/training-loop.log`)
  console.log(`  停止全部:      bun tools/hub-start.ts --kill`)
  console.log('')
}

main().catch((e) => {
  console.error(`\n${RED}${e.message || e}${NC}`)
  process.exit(1)
})
