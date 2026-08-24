/**
 * tools/dist/sampler-agent.ts — 分布式采样节点（bun 零依赖常驻服务）
 *
 * 协议（plan/distributed-rollout.md v3.3）：
 *   POST /v1/weights  每轮一次；x-weights-sha256 与缓存不同 → 原子切换并清空结果缓存，
 *                     相同 → 幂等不动（relaunch 续跑不误清本批数据）。
 *   GET  /v1/task     ?iterId&wver&stage&seed&maxTicks&difficulty —— 同步跑一局并随响应
 *                     返回 gzip(JSON {report, files:{name:base64}})；采样期间每 30s 推
 *                     chunked 心跳字节防中间设备空闲回收。结果缓存 LRU：同键重入直接回放。
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
  'a_item.npy',
  'lp_move.npy',
  'lp_fire.npy',
  'lp_item.npy',
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
): Promise<Buffer> {
  if (!weights) throw new Error('no weights cached')
  const wfile = weights.file
  const seq = ++gameSeq
  const gameDir = path.join(WORK_DIR, `game-${process.pid}-${seq}`)
  fs.mkdirSync(gameDir, { recursive: true })
  const t0 = Date.now()
  try {
    const args = [
      'tools/sim/export-rl-rollout.ts',
      '--weights',
      wfile,
      '--out',
      gameDir,
      '--stages',
      String(stage),
      '--seeds',
      String(seed),
      '--max-ticks',
      String(maxTicks),
      '--difficulty',
      difficulty,
      '--wver',
      weights.sha,
      '--node-label',
      `bun-${process.pid}`,
    ]
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
    if (rc !== 0) throw new Error(`export-rl-rollout exited ${rc}: ${tail}`)

    const shardDir = path.join(gameDir, `rl_s${stage}_seed${seed}`)
    const files: Record<string, string> = {}
    for (const name of SHARD_FILES) {
      const p = path.join(shardDir, name)
      if (!fs.existsSync(p)) throw new Error(`missing shard file ${name}`)
      files[name] = fs.readFileSync(p).toString('base64')
    }
    const reportPath = path.join(gameDir, '_rl_report.json')
    const report = JSON.parse(fs.readFileSync(reportPath, 'utf8')) as Record<string, unknown>
    report.elapsedSec = +((Date.now() - t0) / 1000).toFixed(1)
    // 权威回显：trainer 校验器按标量 stage/seed 对账（export 摘要里是 stages/seeds 列表）
    report.stage = stage
    report.seed = seed

    return packContainer(report, files)
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
    if (oldFile && oldFile !== wfile) fs.rmSync(oldFile, { force: true })
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
        runGame(stage, seed, maxTicks, difficulty)
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
