/**
 * agent-bench.ts —— 从 PC 侧驱动一个远端 sampler-agent 跑 N 局 rollout（真实 HTTP 协议，非本地直跑）。
 *
 * 用途：回答「某台节点在真实训练路径下每局/吞吐各是多少」——与 `export-rl-rollout.ts` 本机直跑相比，
 * 这里多走 HTTP + 常驻池（`PERSIST_SERVE_ENTRIES`）+ 并发 worker，是训练栈的真实形态。
 *
 * 协议（对齐 `nn-training/dist_common.py::fetch_task`）：
 *   · 鉴权：`Authorization: Bearer <agent.auth 内容>`
 *   · 权重：`POST /v1/weights`（body = **gzip** 的权重 JSON，`x-weights-sha256` = **未压缩**字节的 sha256，
 *     `x-iter-id`），返回 204 kept / 200 purged；`wver` 参数就用这个 sha。
 *   · 任务：`GET /v1/task?iterId&wver&stage&seed&maxTicks&difficulty[&livesOverride]` —— 缺省 sync，
 *     跑完在同一连接回整包（200）；204/202 时走 `/v1/result` 轮询；503 busy 重试。
 *
 * 用法：
 *   bun tools/perf/agent-bench.ts --url http://192.168.0.95:8443 --key <k> --weights <w.json> \
 *     --games 100 --concurrency 8 --stage 0 --seed-start 0 --max-ticks 12900 --lives-override 1
 *
 * 注意：**每个 (iterId, stage, seed) 只能领一次**（agent 结果缓存按 taskKey，重复会被秒回）。
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { unpackContainer } from '../sim/pack-container'

function opt(name: string, dflt: string): string {
  const i = process.argv.indexOf(name)
  return i >= 0 && process.argv[i + 1] !== undefined ? process.argv[i + 1]! : dflt
}

const url = opt('--url', 'http://127.0.0.1:8443').replace(/\/$/, '')
const key = opt('--key', '')
const weightsPath = opt('--weights', '')
const games = parseInt(opt('--games', '100'), 10)
const conc = parseInt(opt('--concurrency', '8'), 10)
const stage = parseInt(opt('--stage', '0'), 10)
const seedStart = parseInt(opt('--seed-start', '0'), 10)
const maxTicks = parseInt(opt('--max-ticks', '12900'), 10)
const difficulty = opt('--difficulty', 'hard')
const lives = opt('--lives-override', '1')
const iterId = opt('--iter', `bench-${Date.now()}`)
const label = opt('--label', 'bench')
const auth = { authorization: `Bearer ${key}` }

async function status(): Promise<Record<string, unknown>> {
  const r = await fetch(`${url}/v1/status`, { headers: auth })
  return (await r.json()) as Record<string, unknown>
}

/** 上传权重，返回 wver（= 未压缩字节的 sha256）。 */
async function pushWeights(file: string): Promise<string> {
  const raw = readFileSync(file)
  const sha = createHash('sha256').update(raw).digest('hex')
  const gz = Bun.gzipSync(raw)
  const t0 = performance.now()
  const r = await fetch(`${url}/v1/weights`, {
    method: 'POST',
    headers: {
      ...auth,
      'content-type': 'application/octet-stream',
      'x-weights-sha256': sha,
      'x-iter-id': iterId,
      'x-kind': 'rollout',
    },
    body: gz,
  })
  const body = await r.text()
  console.log(
    `[${label}] weights POST ${r.status} (${((performance.now() - t0) / 1000).toFixed(2)}s, ` +
      `raw=${(raw.length / 1024).toFixed(0)}KB gz=${(gz.length / 1024).toFixed(0)}KB) wver=${sha.slice(0, 12)}… ${body.trim()}`,
  )
  if (r.status !== 200 && r.status !== 204)
    throw new Error(`weights upload failed: ${r.status} ${body}`)
  return sha
}

/** 跑一局（含 503 重试 / 202 轮询）；返回墙钟 ms 与整包字节。 */
async function runGame(
  wver: string,
  seed: number,
): Promise<{
  ms: number
  bytes: number
  retries: number
  manifest: Record<string, unknown> | null
}> {
  const qs = new URLSearchParams({
    iterId,
    wver,
    stage: String(stage),
    seed: String(seed),
    maxTicks: String(maxTicks),
    difficulty,
  })
  if (lives !== '') qs.set('livesOverride', lives)

  const t0 = performance.now()
  let retries = 0
  for (;;) {
    const r = await fetch(`${url}/v1/task?${qs}`, { headers: auth })
    if (r.status === 503) {
      retries++
      if (retries > 60) throw new Error('agent busy for too long')
      await new Promise((s) => setTimeout(s, 500))
      continue
    }
    let buf = Buffer.from(await r.arrayBuffer())
    if (r.status === 202) {
      // 异步路径：轮询 /v1/result
      const rq = new URLSearchParams({ iterId, stage: String(stage), seed: String(seed) })
      for (;;) {
        await new Promise((s) => setTimeout(s, 200))
        const rr = await fetch(`${url}/v1/result?${rq}`, { headers: auth })
        if (rr.status === 200) {
          buf = Buffer.from(await rr.arrayBuffer())
          break
        }
        if (rr.status !== 202)
          throw new Error(`result poll failed: ${rr.status} ${await rr.text()}`)
      }
    } else if (r.status !== 200) {
      throw new Error(`task failed: ${r.status} ${buf.toString('utf8', 0, 200)}`)
    }
    // sync 路径的保活 chunk 是前导空格（trainer 在 gunzip 前 strip）
    let off = 0
    while (off < buf.length && buf[off] === 0x20) off++
    buf = buf.subarray(off)
    let manifest: Record<string, unknown> | null = null
    try {
      manifest = unpackContainer(buf).manifest as Record<string, unknown>
    } catch {
      manifest = null
    }
    return { ms: performance.now() - t0, bytes: buf.length, retries, manifest }
  }
}

const before = await status()
console.log(
  `[${label}] agent: cpus=${before.cpus} workers=${before.workers} persist=${before.persist} ` +
    `engine=${before.rolloutEngine} codeHash=${String(before.codeHash).slice(0, 12)}… version=${before.agentVersion}`,
)
const wver = weightsPath ? await pushWeights(weightsPath) : opt('--wver', '')
if (!wver) throw new Error('need --weights or --wver')

console.log(`[${label}] driving ${games} games, concurrency=${conc}, iterId=${iterId}`)
const t0 = performance.now()
const rows: {
  seed: number
  ms: number
  bytes: number
  retries: number
  manifest: Record<string, unknown> | null
}[] = []
let next = 0
let done = 0
async function worker(): Promise<void> {
  for (;;) {
    const i = next++
    if (i >= games) return
    const r = await runGame(wver, seedStart + i)
    rows.push({ seed: seedStart + i, ...r })
    done++
    if (done % 10 === 0)
      console.log(
        `[${label}]  ${done}/${games} games  wall=${((performance.now() - t0) / 1000).toFixed(1)}s`,
      )
  }
}
await Promise.all(Array.from({ length: conc }, () => worker()))
const wallMs = performance.now() - t0

const ms = rows.map((r) => r.ms).sort((a, b) => a - b)
const q = (p: number): number => ms[Math.min(ms.length - 1, Math.floor(p * ms.length))]!
const sum = ms.reduce((a, b) => a + b, 0)
const totalTicks = rows.reduce((a, r) => a + (Number(r.manifest?.ticks) || 0), 0)
const fails = rows.filter((r) => !r.manifest).length
const retries = rows.reduce((a, r) => a + r.retries, 0)
const after = await status()

console.log(`\n=== [${label}] 结果 ===`)
console.log(`games=${rows.length} 并发=${conc} 总墙钟=${(wallMs / 1000).toFixed(2)}s`)
console.log(
  `吞吐 = ${((rows.length * 1000) / wallMs).toFixed(2)} 局/s = ${((rows.length * 3600 * 1000) / wallMs).toFixed(0)} 局/h`,
)
console.log(
  `单局延迟 ms: mean=${(sum / ms.length).toFixed(0)} p50=${q(0.5).toFixed(0)} p90=${q(0.9).toFixed(0)} max=${ms[ms.length - 1]!.toFixed(0)}`,
)
console.log(
  `总 tick=${totalTicks} ⇒ ${(wallMs / Math.max(1, totalTicks)).toFixed(2)} ms/tick ｜ 整包均字节=${Math.round(rows.reduce((a, r) => a + r.bytes, 0) / rows.length / 1024)}KB`,
)
console.log(`unpack 失败=${fails} ｜ 503 重试=${retries}`)
console.log(
  `agent gamesDoneTotal: ${before.gamesDoneTotal} → ${after.gamesDoneTotal} ｜ cacheHits: ${before.cacheHits} → ${after.cacheHits}`,
)
