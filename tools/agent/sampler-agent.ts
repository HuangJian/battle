/**
 * tools/dist/sampler-agent.ts — 分布式采样节点（bun 零依赖常驻服务）
 *
 * 协议（plan/distributed-rollout.md v3.6）：
 *   POST /v1/weights  每轮一次；x-weights-sha256 与缓存不同 → 原子切换并清空结果缓存，
 *                     相同 → 幂等不动（relaunch 续跑不误清本批数据）。
 *   GET  /v1/task     ?iterId&wver&stage&seed&maxTicks&difficulty
 *                     — 同步模式（缺省，v3.5- 兼容）：跑完一局流式回包（20s 心跳防空闲回收）；
 *                     — 异步模式（x-async:1，v3.6）：202+token 立即返回、后台执行，
 *                       trainer 轮询 GET /v1/result 取包（同 key 幂等）。
 *                     结果缓存 LRU：同键重入直接回放。
 *   GET  /v1/result   异步取包：200 容器 | 202 在跑 | 500 局失败（一次性消费）| 404 过期。
 *   GET  /v1/status   健康快照（巡检用）。
 *   GET  /v1/ping     存活 + codeHash + bunVersion + agentVersion + cpus。
 *
 * 仅用 bun/node 内建（Bun.serve / node:child_process / node:zlib / node:crypto /
 * node:fs），零 npm 第三方依赖、无构建步骤（bun 直跑 TS 源码）。Bun.serve 若出现
 * 稳定性问题，允许降级为等价的 node:http 实现（协议不变）。
 *
 * 启动：bun tools/dist/sampler-agent.ts --port 8443 [--workers N] [--cache-mb 2048]
 * 首启生成随机 authKey 写同目录 agent.auth 并打印一次；运维复制到 dist-nodes.json。
 */
import { spawn, spawnSync } from 'node:child_process'
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto'
import { gunzipSync, gzipSync } from 'node:zlib'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const REPO_ROOT = path.resolve(import.meta.dir, '..', '..')
const AGENT_AUTH_PATH = path.join(import.meta.dir, 'agent.auth')
const WORK_DIR = path.join(REPO_ROOT, 'tmp', 'dist-agent')
export const SHARD_FILES = [
  'obs.npy',
  'scalars.npy',
  'a_move.npy',
  'a_fire.npy',
  'lp_move.npy',
  'lp_fire.npy',
  'value.npy',
  'reward.npy',
  'done.npy',
  'mask.npy',
] as const

// ---------------- CLI ----------------
const CPUS = os.cpus().length
let port = 8443
let workers = CPUS
let cacheMaxBytes = 2048 * 1024 * 1024
let cacheMaxItems = 32
{
  const argv = process.argv.slice(2)
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--port') port = parseInt(argv[++i], 10)
    else if (a === '--workers') workers = Math.max(1, parseInt(argv[++i], 10))
    else if (a === '--cache-mb') cacheMaxBytes = Math.max(1, parseInt(argv[++i], 10)) * 1024 * 1024
    else if (a === '--max-cache-items') cacheMaxItems = Math.max(1, parseInt(argv[++i], 10))
  }
}

// ---------------- codeHash（与 nn-training/dist_common.py 逐字节一致的双语契约） ----------------
/** 对 entries（posix 相对路径 + 内容）按路径字典序，依次喂 sha256(path)+sha256(content)。 */
export function computeCodeHashFromFiles(entries: { relPath: string; content: Buffer }[]): string {
  const sorted = [...entries].sort((a, b) =>
    a.relPath < b.relPath ? -1 : a.relPath > b.relPath ? 1 : 0,
  )
  const h = createHash('sha256')
  for (const e of sorted) {
    h.update(e.relPath.replace(/\\/g, '/'))
    h.update(createHash('sha256').update(e.content).digest())
  }
  return h.digest('hex')
}

function collectCodeHashEntries(): { relPath: string; content: Buffer }[] {
  const out: { relPath: string; content: Buffer }[] = []
  const nnRoot = path.join(REPO_ROOT, 'src', 'nn')
  const walk = (dir: string): void => {
    if (!fs.existsSync(dir)) return
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name)
      if (fs.statSync(p).isDirectory()) walk(p)
      else out.push({ relPath: path.relative(REPO_ROOT, p), content: fs.readFileSync(p) })
    }
  }
  walk(nnRoot)
  const rollout = path.join(REPO_ROOT, 'tools', 'sim', 'export-rl-rollout.ts')
  if (fs.existsSync(rollout))
    out.push({ relPath: path.relative(REPO_ROOT, rollout), content: fs.readFileSync(rollout) })
  return out
}

export function computeCodeHash(): string {
  return computeCodeHashFromFiles(collectCodeHashEntries())
}

function gitShortHash(): string {
  try {
    const r = spawnSync('git', ['rev-parse', '--short', 'HEAD'], {
      cwd: REPO_ROOT,
      encoding: 'utf8',
    })
    return r.status === 0 ? r.stdout.trim() : 'unknown'
  } catch {
    return 'unknown'
  }
}

// ---------------- authKey ----------------
function loadOrCreateAuthKey(): string {
  if (fs.existsSync(AGENT_AUTH_PATH)) {
    const key = fs.readFileSync(AGENT_AUTH_PATH, 'utf8').trim()
    if (key) return key
  }
  const key = randomBytes(32).toString('base64url')
  fs.writeFileSync(AGENT_AUTH_PATH, key + '\n', { mode: 0o600 })
  console.log(
    `[sampler-agent] authKey generated -> ${AGENT_AUTH_PATH}\n[sampler-agent] authKey=${key}`,
  )
  return key
}

function safeEqual(a: string, b: string): boolean {
  const ba = Buffer.from(a)
  const bb = Buffer.from(b)
  return ba.length === bb.length && timingSafeEqual(ba, bb)
}

// ---------------- state ----------------
interface CachedResult {
  buf: Buffer
  bytes: number
}
const resultCache = new Map<string, CachedResult>()
let cacheBytes = 0
let cacheHits = 0
let cacheEvicted = 0
let rejectedCount = 0
let lastError = ''
let gamesDoneTotal = 0
const gamesDoneByIter = new Map<string, number>()
const inflight = new Map<string, { stage: number; seed: number; startedAt: number }>()
// v3.6 异步模式：已完成但失败的任务登记表（/v1/result 消费一次即删）。
// 权重切换清场时与 resultCache 一并清空——跨轮生命周期一致。
const failedTasks = new Map<string, string>()
let activeWorkers = 0
const startedAt = Date.now()

interface WeightsState {
  sha: string
  iterId: string
  file: string
}
let weights: WeightsState | null = null
const AUTH_KEY = loadOrCreateAuthKey()
fs.mkdirSync(WORK_DIR, { recursive: true })

function lruPut(key: string, buf: Buffer): void {
  const prev = resultCache.get(key)
  if (prev) {
    cacheBytes -= prev.bytes
    resultCache.delete(key)
  }
  resultCache.set(key, { buf, bytes: buf.length })
  cacheBytes += buf.length
  while ((resultCache.size > cacheMaxItems || cacheBytes > cacheMaxBytes) && resultCache.size > 0) {
    const oldest = resultCache.keys().next().value as string | undefined
    if (oldest === undefined) break
    const v = resultCache.get(oldest)
    if (v) cacheBytes -= v.bytes
    resultCache.delete(oldest)
    cacheEvicted++
  }
}

function diskFreeMB(): number | null {
  type StatFsLike = { bavail: number; bsize: number }
  const f = fs as unknown as { statfsSync?: (p: string) => StatFsLike }
  try {
    if (typeof f.statfsSync !== 'function') return null
    const s = f.statfsSync.call(fs, WORK_DIR)
    return Math.floor((s.bavail * s.bsize) / (1024 * 1024))
  } catch {
    return null
  }
}

/** 权重文件保留清扫：只留最新 KEEP 份；被在飞评估局占用的尽力跳过。 */
const WEIGHT_FILES_KEEP = 4

function sweepWeightFiles(): void {
  try {
    const files = fs
      .readdirSync(WORK_DIR)
      .filter((f) => /^weights-[0-9a-f]{16}\.json$/.test(f))
      .map((f) => {
        const p = path.join(WORK_DIR, f)
        return { p, m: fs.statSync(p).mtimeMs }
      })
      .sort((a, b) => b.m - a.m)
    for (const x of files.slice(WEIGHT_FILES_KEEP)) {
      try {
        fs.rmSync(x.p, { force: true })
      } catch {
        /* busy — next sweep */
      }
    }
  } catch {
    /* best effort */
  }
}

// ---------------- game execution ----------------
let gameSeq = 0

/** 结果容器：gzip(JSON {manifest, files:{name:base64}})——TS/Python 双语契约，见 dist_common.py。 */
export function packContainer(
  report: Record<string, unknown>,
  files: Record<string, string>,
): Buffer {
  return gzipSync(Buffer.from(JSON.stringify({ manifest: report, files })))
}

export function unpackContainer(buf: Buffer): {
  manifest: Record<string, unknown>
  files: Record<string, string>
} {
  return JSON.parse(gunzipSync(buf).toString('utf8'))
}

async function runGame(
  stage: number,
  seed: number,
  maxTicks: number,
  difficulty: string,
  mode: string,
): Promise<Buffer> {
  if (!weights) throw new Error('no weights cached')
  const wfile = weights.file
  // 干净评估走独立贪心 runner（不在 codeHash 集内，见 export-eval-game.ts 头注释）；
  // 只产 _eval_report.json，无 npy shards。
  const isEval = mode === 'eval'
  const seq = ++gameSeq
  const gameDir = path.join(WORK_DIR, `game-${process.pid}-${seq}`)
  fs.mkdirSync(gameDir, { recursive: true })
  try {
    const args = [
      isEval ? 'tools/sim/export-eval-game.ts' : 'tools/sim/export-rl-rollout.ts',
      '--weights',
      wfile,
      '--out',
      gameDir,
    ]
    if (isEval) {
      // export-eval-game.ts 用单数形式 --stage/--seed
      args.push('--stage', String(stage), '--seed', String(seed))
    } else {
      args.push('--stages', String(stage), '--seeds', String(seed))
    }
    args.push(
      '--max-ticks',
      String(maxTicks),
      '--difficulty',
      difficulty,
      '--wver',
      weights.sha,
      '--node-label',
      `bun-${process.pid}`,
      // v3.6：容器在子进程内组装（BCV2，tools/sim/pack-container.ts）——base64+gzip+JSON
      // 拼装与仿真并行，不再阻塞 agent 主线程（8 workers 串行打包曾是吞吐瓶颈）。
      '--pack',
      path.join(gameDir, '_result.pack'),
    )
    const child = spawn(process.execPath, args, {
      cwd: REPO_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let tail = ''
    const cap = (chunk: Buffer): void => {
      tail = (tail + chunk.toString('utf8')).slice(-4000)
    }
    child.stdout.on('data', cap)
    child.stderr.on('data', cap)
    const rc = await new Promise<number>((resolve, reject) => {
      child.on('error', reject)
      child.on('close', (code) => resolve(code ?? -1))
    })
    const scriptName = isEval ? 'export-eval-game' : 'export-rl-rollout'
    if (rc !== 0) throw new Error(`${scriptName} exited ${rc}: ${tail}`)

    // 子进程已把结果打成 BCV2 容器（manifest 含 stage/seed/mode/elapsedSec 溯源戳），
    // 主线程只做一次顺序读——不再读 12 个 shard + base64 + gzip。
    const packFile = path.join(gameDir, '_result.pack')
    if (!fs.existsSync(packFile)) {
      throw new Error(`${scriptName} produced no result pack: ${tail}`)
    }
    return fs.readFileSync(packFile)
  } finally {
    fs.rmSync(gameDir, { recursive: true, force: true })
  }
}

function jsonResponse(obj: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

// ---------------- HTTP handler ----------------
/**
 * v3.6 异步模式的后台执行器：与同步流式路径共用 runGame/缓存/计数器，
 * 但不挂任何 HTTP 流——局完成（或失败）后结果落在 resultCache/failedTasks 里，
 * 由 trainer 轮询 /v1/result 取走。同 key 重复提交幂等（不重复 spawn）。
 */
function beginTask(
  key: string,
  iterId: string,
  stage: number,
  seed: number,
  maxTicks: number,
  difficulty: string,
  mode: string,
): void {
  activeWorkers++
  inflight.set(key, { stage, seed, startedAt: Date.now() })
  runGame(stage, seed, maxTicks, difficulty, mode)
    .then((buf) => {
      lruPut(key, buf)
      gamesDoneTotal++
      gamesDoneByIter.set(iterId, (gamesDoneByIter.get(iterId) ?? 0) + 1)
    })
    .catch((e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e)
      failedTasks.set(key, msg.slice(0, 300))
      lastError = `${new Date().toISOString()} s${stage}/seed${seed}: ${msg}`
      console.error(`[sampler-agent] task failed: ${lastError}`)
    })
    .finally(() => {
      activeWorkers--
      inflight.delete(key)
    })
}

async function handle(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const auth = req.headers.get('authorization') ?? ''
  const token = auth.startsWith('Bearer ') ? auth.slice(7) : ''
  if (!token || !safeEqual(token, AUTH_KEY)) return jsonResponse({ error: 'unauthorized' }, 401)

  if (req.method === 'POST' && url.pathname === '/v1/weights') {
    const declared = req.headers.get('content-length') ?? '0'
    if (parseInt(declared, 10) > 64 * 1024 * 1024)
      return jsonResponse({ error: 'payload too large' }, 413)
    const claimedSha = req.headers.get('x-weights-sha256') ?? ''
    const iterId = req.headers.get('x-iter-id') ?? ''
    if (!claimedSha || !iterId)
      return jsonResponse({ error: 'missing x-weights-sha256/x-iter-id' }, 400)
    const raw = Buffer.from(await req.arrayBuffer())
    let weightsBytes: Buffer
    try {
      weightsBytes = gunzipSync(raw)
    } catch {
      return jsonResponse({ error: 'body is not valid gzip' }, 400)
    }
    const actualSha = createHash('sha256').update(weightsBytes).digest('hex')
    if (actualSha !== claimedSha) {
      rejectedCount++
      return jsonResponse({ error: `sha mismatch: header=${claimedSha} actual=${actualSha}` }, 400)
    }
    if (weights && weights.sha === actualSha) return jsonResponse({ ok: true, cache: 'kept' }, 204)
    // 原子切换：先备好新权重文件，再换状态、清结果缓存（跨轮生命周期显式化，v3.3）
    const wfile = path.join(WORK_DIR, `weights-${actualSha.slice(0, 16)}.json`)
    fs.writeFileSync(wfile, weightsBytes)
    const oldFile = weights?.file
    weights = { sha: actualSha, iterId, file: wfile }
    cacheEvicted += resultCache.size
    cacheBytes = 0
    resultCache.clear()
    failedTasks.clear()
    // 尽力而为删除：干净评估局可能在飞、子进程尚持旧权重文件句柄（Windows EBUSY）。
    // 删不掉的留给下方 retention 清扫，绝不让切换失败（那会拖垮整轮权重分发）。
    if (oldFile && oldFile !== wfile) {
      try {
        fs.rmSync(oldFile, { force: true })
      } catch {
        /* in-flight eval game holds the handle — swept by retention below */
      }
    }
    sweepWeightFiles()
    console.log(
      `[sampler-agent] weights switched -> ${actualSha.slice(0, 12)}… (result cache purged)`,
    )
    // 状态变更触发（清场）：带 JSON body，返回 200（204 不应带 body，HTTP 语义）
    return jsonResponse({ ok: true, cache: 'purged' }, 200)
  }

  if (req.method === 'GET' && url.pathname === '/v1/task') {
    const iterId = url.searchParams.get('iterId') ?? ''
    const wver = url.searchParams.get('wver') ?? ''
    const stage = parseInt(url.searchParams.get('stage') ?? '', 10)
    const seed = parseInt(url.searchParams.get('seed') ?? '', 10)
    const maxTicks = parseInt(url.searchParams.get('maxTicks') ?? '', 10)
    const difficulty = url.searchParams.get('difficulty') ?? 'hard'
    // mode=eval → 干净评估局（贪心、无 shards）；缺省 rollout。旧 trainer 不发此参数。
    const mode = url.searchParams.get('mode') ?? 'rollout'
    if (
      !iterId ||
      !wver ||
      !Number.isInteger(stage) ||
      !Number.isInteger(seed) ||
      !Number.isInteger(maxTicks)
    )
      return jsonResponse({ error: 'missing/invalid query params' }, 400)
    if (!weights || weights.sha !== wver)
      return jsonResponse({ error: 'wver not cached here' }, 409)
    const free = diskFreeMB()
    if (free !== null && free < 2048) return jsonResponse({ error: `low disk: ${free}MB` }, 503)
    if (activeWorkers >= workers)
      return jsonResponse({ error: 'busy' }, 503, { 'Retry-After': '5' })

    const key = `${iterId}:${stage}:${seed}`
    const cached = resultCache.get(key)
    if (cached) {
      cacheHits++
      // LRU touch
      resultCache.delete(key)
      resultCache.set(key, cached)
      return new Response(new Uint8Array(cached.buf), {
        headers: { 'Content-Type': 'application/octet-stream' },
      })
    }

    // v3.6 异步模式（plan/distributed-rollout.md §4.2）：带 x-async:1 的提交立即返回
    // 202+token，游戏后台执行，trainer 轮询 /v1/result 取包。同 key 在跑 → 幂等回同一
    // token（不重复 spawn）；旧 trainer 不发此头，走下方同步流式路径，行为不变。
    if (req.headers.get('x-async') === '1') {
      if (inflight.has(key)) return jsonResponse({ status: 'running', token: key }, 202)
      if (activeWorkers >= workers)
        return jsonResponse({ error: 'busy' }, 503, { 'Retry-After': '5' })
      beginTask(key, iterId, stage, seed, maxTicks, difficulty, mode)
      return jsonResponse({ status: 'accepted', token: key }, 202)
    }

    activeWorkers++
    inflight.set(key, { stage, seed, startedAt: Date.now() })
    // 保活流式响应：单局可能长达 ~480s，而 Bun.serve idleTimeout 上限仅 255s。若连接全程静默，
    // server 回收连接 → trainer 端报 "Remote end closed"。用合法 chunk 字节(' '空格)每 20s 发一次
    // 保活，防 server 空闲回收；单局完成后追加 gzip payload 并结束。trainer 在 gunzip 前 strip 空格
    // （见 dist_common.fetch_task）。注意不能用非法 chunk(如 ':\n')——那会让 urllib 丢弃整个 body。
    const enc = new TextEncoder()
    let hb: ReturnType<typeof setInterval> | undefined
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        hb = setInterval(() => {
          try {
            controller.enqueue(enc.encode(' '))
          } catch {
            /* client gone */
          }
        }, 20_000)
        runGame(stage, seed, maxTicks, difficulty, mode)
          .then((buf) => {
            if (hb) clearInterval(hb)
            lruPut(key, buf)
            gamesDoneTotal++
            gamesDoneByIter.set(iterId, (gamesDoneByIter.get(iterId) ?? 0) + 1)
            try {
              controller.enqueue(new Uint8Array(buf))
              controller.close()
            } catch {
              /* client gone */
            }
          })
          .catch((e: unknown) => {
            if (hb) clearInterval(hb)
            lastError = `${new Date().toISOString()} s${stage}/seed${seed}: ${e instanceof Error ? e.message : String(e)}`
            console.error(`[sampler-agent] task failed: ${lastError}`)
            controller.error(e instanceof Error ? e : new Error(String(e)))
          })
          .finally(() => {
            activeWorkers--
            inflight.delete(key)
          })
      },
      cancel() {
        if (hb) clearInterval(hb)
      },
    })
    return new Response(stream, { headers: { 'Content-Type': 'application/octet-stream' } })
  }

  // v3.6 异步模式取包端点：200=容器就绪 | 202=仍在跑 | 500=局失败（登记一次性消费）
  // | 404=无此任务（重启/跨轮清场后过期，trainer 应重新提交）。
  if (req.method === 'GET' && url.pathname === '/v1/result') {
    const iterId = url.searchParams.get('iterId') ?? ''
    const stage = parseInt(url.searchParams.get('stage') ?? '', 10)
    const seed = parseInt(url.searchParams.get('seed') ?? '', 10)
    if (!iterId || !Number.isInteger(stage) || !Number.isInteger(seed))
      return jsonResponse({ error: 'missing/invalid query params' }, 400)
    const key = `${iterId}:${stage}:${seed}`
    const cached = resultCache.get(key)
    if (cached) {
      resultCache.delete(key)
      resultCache.set(key, cached)
      return new Response(new Uint8Array(cached.buf), {
        headers: { 'Content-Type': 'application/octet-stream' },
      })
    }
    const failed = failedTasks.get(key)
    if (failed !== undefined) {
      failedTasks.delete(key)
      return jsonResponse({ error: failed }, 500)
    }
    const running = inflight.get(key)
    if (running)
      return jsonResponse(
        { status: 'running', elapsedSec: +((Date.now() - running.startedAt) / 1000).toFixed(1) },
        202,
      )
    return jsonResponse({ error: 'unknown task (expired/purged/restart)' }, 404)
  }

  if (req.method === 'GET' && url.pathname === '/v1/status') {
    return jsonResponse({
      nodeId: `bun-${process.pid}`,
      uptimeSec: Math.floor((Date.now() - startedAt) / 1000),
      codeHash: computeCodeHash(),
      bunVersion: Bun.version,
      agentVersion: gitShortHash(),
      cpus: CPUS,
      workers,
      gamesDoneTotal,
      gamesDoneByIter: Object.fromEntries(gamesDoneByIter),
      inflight: [...inflight.entries()].map(([k, v]) => ({
        key: k,
        ...v,
        elapsedSec: +((Date.now() - v.startedAt) / 1000).toFixed(1),
      })),
      lastError,
      recentFailed: failedTasks.size,
      diskFreeMB: diskFreeMB(),
      cacheHits,
      cacheEvicted,
      rejectedCount,
      resultCache: { items: resultCache.size, bytes: cacheBytes },
    })
  }

  if (req.method === 'GET' && url.pathname === '/v1/ping') {
    return jsonResponse({
      ok: true,
      codeHash: computeCodeHash(),
      bunVersion: Bun.version,
      agentVersion: gitShortHash(),
      cpus: CPUS,
      // 能力声明：trainer 据此把节点纳入干净评估分发（旧 agent 无此字段 → 自动跳过）
      evalSupport: true,
    })
  }

  return jsonResponse({ error: 'not found' }, 404)
}

// ---------------- entry ----------------
if (import.meta.main) {
  const showHelp = process.argv.includes('--help')
  if (process.argv.includes('--print-code-hash')) {
    console.log(computeCodeHash())
    process.exit(0)
  }
  if (showHelp) {
    console.log(
      'usage: bun tools/dist/sampler-agent.ts --port 8443 [--workers N] [--cache-mb 2048] [--max-cache-items 32] [--print-code-hash]',
    )
    process.exit(0)
  }
  console.log(
    `[sampler-agent] listening on 0.0.0.0:${port} workers=${workers} cache=${cacheMaxBytes >> 20}MB/${cacheMaxItems} ` +
      `codeHash=${computeCodeHash().slice(0, 12)}… agentVersion=${gitShortHash()} cpus=${CPUS}`,
  )
  Bun.serve({
    port,
    // Bun.serve 的 idleTimeout 上限 255s，而单局最长 ~480s——仅靠它不足以阻止长静默 task 连接被回收。
    // 因此设 idleTimeout=255(允许的最大值) + task 响应流式的"保活 chunk"（每 20s 发一个空格字节），
    // 双重保证等待中的 task 连接不被 server 空闲回收（否则 trainer 端报 Remote end closed）。
    // trainer 在 gunzip 前 strip 掉这些空格字节（见 dist_common.fetch_task）。
    idleTimeout: 255,
    fetch: (req) =>
      handle(req).catch((e: unknown) => {
        lastError = `${new Date().toISOString()} handler: ${e instanceof Error ? e.message : String(e)}`
        return jsonResponse({ error: 'internal error', detail: lastError }, 500)
      }),
  })
}
