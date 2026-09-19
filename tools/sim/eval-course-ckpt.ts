#!/usr/bin/env bun
/**
 * eval-course-ckpt.ts — greedy evaluation of NN-policy checkpoint(s) on a
 * curriculum course's custom stages (2000+), headless & parallel.
 *
 * Unlike m1-eval.ts (built-in STAGES only), this evaluates the checkpoint on
 * `--course <name|path>.jsonc` custom stages — e.g. the p1-onset stages —
 * through the same masked-argmax deployment evaluator as the RL eval loop
 * (export-eval-game.runEvalOne, pure v7 scoring). Rows carry the full hit
 * accounting (kills / enemyHits 击中 / playerHits+playerDamageTaken 被击中),
 * the fields the RL metrics (reward_library METRICS 30-dim) track.
 *
 * Determinism & parallelism: each game is a pure function of (weights bytes,
 * stageLocal, seed) — fresh World, own seeded RNG, decodeStageGrid spawn
 * variant picked by seed hash. Jobs are split round-robin across
 * `runChunkedWorkers` (physical-core cap, defaultWorkerCount) so parallel ==
 * serial; JSONL rows are emitted in submission order (stable aggregation).
 *
 * Distributed (2026-09-19, same hybrid path as m1-eval auto-dist): when
 * `nn-training/rl-config.json` has enabled nodes (or `--dist-nodes <path>`),
 * games fan out to sampler-agent `mode=eval` + custom `stageJson` (stage id
 * 2000+stageLocal), while local workers keep a share. Node gate =
 * evalSupport ∧ stageJsonSupport ∧ bun major.minor ∧ codeHash (rollout 同源).
 * Force local with `--no-dist`. `--policy nn-goal` stays local-only.
 *
 * Usage:
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset \
 *       --weights tmp/p1-bc-smoke/weights.json.ckpt.3 --games 100
 *   bun tools/sim/eval-course-ckpt.ts --course nn-training/curricula/p1-onset.jsonc \
 *       --weights a.json --weights b.json --games 50 --workers 8 \
 *       --out tmp/p1-eval-rows.jsonl
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset --policy god --games 100 \
 *       # true God-AI (DEFAULT_GOD_AI_PARAMS, RNG 同 simulation-runner，无需 --weights)
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset --weights w.json \
 *       --dist-nodes nn-training/rl-config.json --out tmp/x.jsonl
 *
 * Output: one JSON row per game (JSONL) to --out or stdout; human summary per
 * checkpoint to stderr.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs'
import { createHash } from 'node:crypto'
import { gzipSync } from 'node:zlib'
import { dirname } from 'path'
import { splitRoundRobin, defaultWorkerCount } from '../lib/worker-pool'
import { flag } from '../lib/cli'
import { TailRaceBatch } from '../lib/hybrid-batch'
import { unpackContainer } from './pack-container'
import { computeCodeHash } from '../agent/codehash-files'
import type { EvalCourseWorkerPayload, EvalCourseRow } from './eval-course-ckpt-worker'

const WORKER_URL = new URL('./eval-course-ckpt-worker.ts', import.meta.url).href
const CURRICULA_DIR = 'nn-training/curricula'
const DEFAULT_DIST_CFG = 'nn-training/rl-config.json'
/** sampler-agent 对 stageJson 的硬上限（query 串 + 校验）。 */
const STAGE_JSON_MAX = 16384

interface CourseJson {
  stages: Array<Record<string, unknown> & { name?: string }>
  difficulty?: string
  max_ticks?: number
  player?: { lives?: number; level?: number }
}

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

/** All --weights values (repeatable). */
function argAll(name: string): string[] {
  const out: string[] = []
  for (let i = 0; i < process.argv.length; i++) {
    if (process.argv[i] === `--${name}` && i + 1 < process.argv.length)
      out.push(process.argv[i + 1])
  }
  return out
}

/**
 * 解析一个 `--weights` 值：支持可选 `label=path` 形式（I4 晋级门 runner 用它对腿
 * 命名），缺省 label = 路径文件名（既有裸路径调用逐字不变）。label 侧不得含路径
 * 分隔符，避免把文件名里带 `=` 的裸路径误拆。
 */
export function parseWeightSpec(spec: string): { path: string; label: string } {
  const eq = spec.indexOf('=')
  if (eq > 0) {
    const label = spec.slice(0, eq)
    const p = spec.slice(eq + 1)
    if (p && !label.includes('/') && !label.includes('\\')) return { path: p, label }
  }
  return { path: spec, label: spec.split(/[\\/]/).pop() ?? spec }
}

/**
 * 去尾逗号（`,]` / `,}`，含跨行）：逐字符扫描，字符串内原样保留（转义感知）。
 * 背景：Python 侧 rl/jsonc.py 容忍尾逗号，课程文件（c6-pickup 起）普遍带尾逗号
 * （oxfmt `trailingComma: all` 还会主动加）；本函数让 TS 侧与 Python 同口径，
 * 否则探针读课程文件直接崩（2026-09-12 实测）。只删 `]`/`}` 前的逗号，中部逗号不动。
 */
export function stripTrailingCommas(text: string): string {
  let out = ''
  let i = 0
  let inStr = false
  while (i < text.length) {
    const c = text[i]
    if (inStr) {
      out += c
      if (c === '\\') {
        out += text[i + 1] ?? ''
        i += 2
        continue
      }
      if (c === '"') inStr = false
      i++
      continue
    }
    if (c === '"') {
      inStr = true
      out += c
      i++
      continue
    }
    if (c === ',') {
      let j = i + 1
      while (j < text.length && /\s/.test(text[j] ?? '')) j++
      const n = text[j] ?? ''
      if (n === ']' || n === '}') {
        i++
        continue
      }
    }
    out += c
    i++
  }
  return out
}

/** 课程文件完整管线：去注释 → 去尾逗号 → JSON.parse 可直读。 */
export function parseCourseJsonc(text: string): unknown {
  return JSON.parse(stripTrailingCommas(stripJsonc(text)))
}

/** Strip // and block comments from JSONC, respecting strings (course files). */
export function stripJsonc(text: string): string {
  let out = ''
  let i = 0
  let inStr = false
  while (i < text.length) {
    const c = text[i]
    const n = text[i + 1]
    if (inStr) {
      out += c
      if (c === '\\') {
        out += text[i + 1] ?? ''
        i += 2
        continue
      }
      if (c === '"') inStr = false
      i++
      continue
    }
    if (c === '"') {
      inStr = true
      out += c
      i++
      continue
    }
    if (c === '/' && n === '/') {
      while (i < text.length && text[i] !== '\n') i++
      continue
    }
    if (c === '/' && n === '*') {
      i += 2
      while (i < text.length && !(text[i] === '*' && text[i + 1] === '/')) i++
      i += 2
      continue
    }
    out += c
    i++
  }
  return out
}

function resolveCourse(nameOrPath: string): string {
  if (nameOrPath.endsWith('.jsonc') || nameOrPath.endsWith('.json')) return nameOrPath
  return `${CURRICULA_DIR}/${nameOrPath}.jsonc`
}

function parseRangeInt(spec: string | undefined, fallback: number): number {
  if (spec === undefined) return fallback
  const n = parseInt(spec, 10)
  return Number.isInteger(n) && n > 0 ? n : fallback
}

// ---------------- dist 纯函数（可单测；不碰网络） ----------------

export interface CourseJob {
  id: number
  weightIdx: number
  label: string
  stageLocal: number
  seed: number
  /** 自定义关 stage id：与 worker / batch_eval 同命名空间（2000+）。 */
  stageId: number
}

/**
 * 课程局任务展开：stageLocal = g % nStages；seed = seed0 + floor(g / nStages)。
 * 与本地 worker 路径同一映射（docs 中已固定的探针口径）。
 */
export function buildCourseJobs(
  weights: Array<{ path: string; label: string }>,
  games: number,
  seed0: number,
  nStages: number,
): CourseJob[] {
  const jobs: CourseJob[] = []
  if (nStages <= 0 || games <= 0) return jobs
  for (let wi = 0; wi < weights.length; wi++) {
    for (let g = 0; g < games; g++) {
      const stageLocal = g % nStages
      jobs.push({
        id: wi * games + g,
        weightIdx: wi,
        label: weights[wi].label,
        stageLocal,
        seed: seed0 + Math.floor(g / nStages),
        stageId: 2000 + stageLocal,
      })
    }
  }
  return jobs
}

/** bun 版本 major.minor（节点门，与 Python `mm()` 同式）。 */
export function bunMajorMinor(v: string): string {
  return String(v).split('.').slice(0, 2).join('.')
}

export interface RemoteTaskUrlOpts {
  baseUrl: string
  iterId: string
  wver: string
  kind: string
  stageId: number
  seed: number
  maxTicks: number
  difficulty: string
  policy: string
  stageJson: string
  livesOverride: number
  playerLevel: number
}

/** 构造 sampler-agent `/v1/task` 查询串（mode=eval + 课程自定义关 stageJson）。 */
export function buildRemoteTaskUrl(o: RemoteTaskUrlOpts): string {
  const p = new URLSearchParams()
  p.set('iterId', o.iterId)
  p.set('wver', o.wver)
  p.set('stage', String(o.stageId))
  p.set('seed', String(o.seed))
  p.set('maxTicks', String(o.maxTicks))
  p.set('difficulty', o.difficulty)
  p.set('mode', 'eval')
  p.set('kind', o.kind)
  if (o.policy && o.policy !== 'nn') p.set('policy', o.policy)
  if (o.stageJson) p.set('stageJson', o.stageJson)
  p.set('livesOverride', String(o.livesOverride))
  p.set('playerLevel', String(o.playerLevel))
  return `${o.baseUrl.replace(/\/$/, '')}/v1/task?${p.toString()}`
}

/** 节点 pack manifest → eval-course-ckpt JSONL 行（Phase0 字段由 export-eval-game 顶层携带）。 */
export function manifestToCourseRow(
  m: Record<string, unknown>,
  task: CourseJob,
  stageName: string,
): EvalCourseRow {
  const num = (k: string): number => (typeof m[k] === 'number' ? (m[k] as number) : 0)
  const bool = (k: string): boolean => m[k] === true
  const numArr = (k: string, len: number): number[] => {
    const v = m[k]
    if (Array.isArray(v) && v.every((x) => typeof x === 'number')) return v as number[]
    return Array.from({ length: len }, () => 0)
  }
  const strOrNull = (k: string): string | null => {
    const v = m[k]
    return typeof v === 'string' ? v : null
  }
  const killerKinds = ((): (string | null)[] => {
    const v = m.killerKinds
    if (!Array.isArray(v)) return []
    return v.map((x) => (typeof x === 'string' ? x : null))
  })()
  return {
    label: task.label,
    id: task.id,
    stageId: task.stageId,
    stageName,
    seed: task.seed,
    outcome: String(m.outcome ?? 'error'),
    win: bool('win'),
    cleared: bool('cleared'),
    ticks: num('ticks'),
    kills: num('kills'),
    enemyHits: num('enemyHits'),
    playerHits: num('playerHits'),
    playerDamageTaken: num('playerDamageTaken'),
    playerShots: num('playerShots'),
    powerUpsCollected: num('powerUpsCollected'),
    score: num('score'),
    hitsByKind: numArr('hitsByKind', 4),
    killsByKind: numArr('killsByKind', 4),
    exposureByKind: numArr('exposureByKind', 4),
    firstHitKind: strOrNull('firstHitKind'),
    firstKillKind: strOrNull('firstKillKind'),
    killOrder: Array.isArray(m.killOrder) ? (m.killOrder as string[]) : [],
    killerKinds,
  }
}

export interface DistNodeCfg {
  id: string
  url: string
  authKey?: string
  concurrency?: number
  enabled?: boolean
}

export interface DistPing {
  codeHash?: string
  bunVersion?: string
  evalSupport?: boolean
  stageJsonSupport?: boolean
  cpus?: number
}

/** 节点门：enabled 外层已滤；此处判 ping 能力位 + bun + codeHash。返回拒绝原因或 null。 */
export function nodeGateReason(
  ping: DistPing | null,
  localBunMM: string,
  localCodeHash: string,
): string | null {
  if (!ping) return 'ping failed'
  if (!ping.evalSupport) return 'lacks evalSupport'
  if (!ping.stageJsonSupport) return 'lacks stageJsonSupport'
  if (bunMajorMinor(ping.bunVersion ?? '?') !== localBunMM) return 'bun version mismatch'
  if (localCodeHash && ping.codeHash && ping.codeHash !== localCodeHash)
    return `codeHash mismatch (node ${String(ping.codeHash).slice(0, 12)}… local ${localCodeHash.slice(0, 12)}…)`
  return null
}

interface LabelAgg {
  label: string
  games: number
  wins: number
  cleared: number
  /** 单关训练场景下 `win`(stage_clear) 与 `cleared`(歼灭) 等价，统一算「过关」。
   *  敌人全灭后若场上还有道具，游戏进 BONUS TIME 窗口（≈600 tick）才 stage_clear，
   *  而 max_ticks 可能在窗口结束前截断 ⇒ outcome=max_ticks 但实际已歼灭。
   *  （2026-09-12 用户裁定：单关场景不区分二者；道具跨关累积的增益只在多关训练时才存在。） */
  passed: number
  outcomes: Record<string, number>
  kills: number
  enemyHits: number
  playerHits: number
  playerDamageTaken: number
  playerShots: number
  ticks: number
}

function summarize(rows: EvalCourseRow[], outPath: string | undefined, t0: number): void {
  const agg = new Map<string, LabelAgg>()
  for (const r of rows) {
    let a = agg.get(r.label)
    if (!a) {
      a = {
        label: r.label,
        games: 0,
        wins: 0,
        cleared: 0,
        passed: 0,
        outcomes: {},
        kills: 0,
        enemyHits: 0,
        playerHits: 0,
        playerDamageTaken: 0,
        playerShots: 0,
        ticks: 0,
      }
      agg.set(r.label, a)
    }
    a.games++
    if (r.win) a.wins++
    if (r.cleared) a.cleared++
    if (r.win || r.cleared) a.passed++
    a.outcomes[r.outcome] = (a.outcomes[r.outcome] ?? 0) + 1
    a.kills += r.kills
    a.enemyHits += r.enemyHits
    a.playerHits += r.playerHits
    a.playerDamageTaken += r.playerDamageTaken
    a.playerShots += r.playerShots
    a.ticks += r.ticks
  }
  const el = ((Date.now() - t0) / 1000).toFixed(1)
  process.stderr.write(
    `\n[eval-course-ckpt] ${rows.length} games in ${el}s (${(rows.length / Number(el) || 0).toFixed(1)} games/s)\n`,
  )
  process.stderr.write(
    `${'label'.padEnd(28)} pass   kills  hit(敌) beHit(玩家) dmg     shots  avgTicks  max_ticks gameover\n`,
  )
  for (const a of agg.values()) {
    process.stderr.write(
      `${a.label.padEnd(28)} ${`${a.passed}/${a.games}`.padEnd(6)} ${String(a.kills).padEnd(6)} ` +
        `${String(a.enemyHits).padEnd(7)} ${String(a.playerHits).padEnd(10)} ` +
        `${String(a.playerDamageTaken).padEnd(7)} ${String(a.playerShots).padEnd(6)} ` +
        `${Math.round(a.ticks / Math.max(1, a.games))
          .toString()
          .padEnd(9)} ` +
        `${String(a.outcomes['max_ticks'] ?? 0).padEnd(9)} ${a.outcomes['gameover'] ?? 0}\n`,
    )
  }
  process.stderr.write(
    // `pass` = win ∪ cleared（单关场景二者等价，见 LabelAgg.passed 注释）；
    // win/cleared 的原始计数仍逐局落在 JSONL 里，需要细分时可离线重算。
    `[eval-course-ckpt] pass rate (= win ∪ cleared) per checkpoint above; ` +
      `full JSONL ${outPath ? `-> ${outPath}` : 'on stdout'}\n`,
  )
}

function emitRows(rows: EvalCourseRow[], outPath: string | undefined): void {
  const lines = rows.map((r) => JSON.stringify(r))
  if (outPath) {
    mkdirSync(dirname(outPath), { recursive: true })
    writeFileSync(outPath, lines.join('\n') + '\n')
  } else {
    for (const l of lines) console.log(l)
  }
}

/**
 * Chunk-per-worker runner (mirrors tools/lib/worker-pool runChunkedWorkers but
 * surfaces per-chunk errors instead of resolving empty). Each chunk payload is
 * posted to its own short-lived worker; the worker answers once with
 * { results, error? }.
 */
async function runChunks(
  chunks: EvalCourseWorkerPayload[],
): Promise<Array<{ results: EvalCourseRow[]; error?: string }>> {
  const settled = await Promise.all(
    chunks.map(
      (payload) =>
        new Promise<{ results: EvalCourseRow[]; error?: string }>((resolve, reject) => {
          const w = new Worker(WORKER_URL)
          w.addEventListener('message', (ev: MessageEvent) => {
            const d = ev.data as { results: EvalCourseRow[]; error?: string }
            resolve({ results: d.results ?? [], error: d.error })
            w.terminate()
          })
          w.addEventListener('error', (err: unknown) => {
            w.terminate()
            reject(new Error((err as ErrorEvent)?.message ?? String(err)))
          })
          w.postMessage(payload)
        }),
    ),
  )
  return settled
}

/** 持久 worker 串行跑一个 payload（hybrid 本地槽）。 */
function runWorkerOnce(
  w: Worker,
  payload: EvalCourseWorkerPayload,
): Promise<{ results: EvalCourseRow[]; error?: string }> {
  return new Promise((resolve, reject) => {
    const onMsg = (ev: MessageEvent): void => {
      w.removeEventListener('message', onMsg)
      w.removeEventListener('error', onErr)
      const d = ev.data as { results: EvalCourseRow[]; error?: string }
      resolve({ results: d.results ?? [], error: d.error })
    }
    const onErr = (err: unknown): void => {
      w.removeEventListener('message', onMsg)
      w.removeEventListener('error', onErr)
      reject(new Error((err as ErrorEvent)?.message ?? String(err)))
    }
    w.addEventListener('message', onMsg)
    w.addEventListener('error', onErr)
    w.postMessage(payload)
  })
}

interface WeightSlot {
  path: string
  label: string
  bytes: Buffer
  sha: string
  kind: 'rollout' | 'none'
}

async function uploadWeightsToNode(
  node: DistNodeCfg,
  slot: WeightSlot,
  iterId: string,
): Promise<void> {
  const resp = await fetch(`${node.url.replace(/\/$/, '')}/v1/weights`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${node.authKey ?? ''}`,
      'Content-Type': 'application/octet-stream',
      'x-weights-sha256': slot.sha,
      'x-iter-id': iterId,
      'x-kind': slot.kind,
    },
    body: gzipSync(slot.bytes),
    signal: AbortSignal.timeout(120_000),
  })
  if (resp.status !== 200 && resp.status !== 204)
    throw new Error(`${node.id} weights upload HTTP ${resp.status}`)
}

interface HybridCtx {
  jobs: CourseJob[]
  stagePayloads: Array<{ name: string; json: string }>
  weightsSlots: WeightSlot[]
  difficulty: string
  maxTicks: number
  lives: number
  level: number
  policy: string
  iterId: string
  localWorkers: number
  distCfgPath: string
}

async function runHybrid(ctx: HybridCtx): Promise<{ rows: EvalCourseRow[]; errors: string[] }> {
  const cfgRaw = JSON.parse(readFileSync(ctx.distCfgPath, 'utf8')) as { nodes?: DistNodeCfg[] }
  const nodes = (cfgRaw.nodes ?? []).filter((n) => n.enabled !== false && n.url)
  const localBunMM = bunMajorMinor(process.versions.bun ?? Bun.version ?? '?')
  const localCodeHash = computeCodeHash()
  const rows: EvalCourseRow[] = []
  const errors: string[] = []
  const total = ctx.jobs.length
  const batch = new TailRaceBatch(total)
  const results: Array<EvalCourseRow | null> = Array.from({ length: total }, () => null)
  let done = 0
  const t0 = Date.now()
  const step = Math.max(1, Math.ceil(total / 20))
  let lastPrint = 0

  const settle = (job: CourseJob, row: EvalCourseRow | null, err?: string): void => {
    if (!batch.settle(job.id)) return
    if (row) {
      results[job.id] = row
      rows.push(row)
    } else if (err) {
      errors.push(err)
    }
    done++
    if (done - lastPrint >= step || done === total) {
      lastPrint = done
      const el = (Date.now() - t0) / 1000
      process.stderr.write(
        `[eval-course-ckpt] hybrid progress ${done}/${total} (${((done / total) * 100).toFixed(0)}%) elapsed ${el.toFixed(0)}s\n`,
      )
    }
  }

  // ---- 节点门 + 权重下发 ----
  const alive: DistNodeCfg[] = []
  for (const n of nodes) {
    const nid = n.id || n.url
    let ping: DistPing | null = null
    try {
      const r = await fetch(`${n.url.replace(/\/$/, '')}/v1/ping`, {
        headers: { Authorization: `Bearer ${n.authKey ?? ''}` },
        signal: AbortSignal.timeout(5000),
      })
      if (r.status === 200) ping = (await r.json()) as DistPing
    } catch {
      ping = null
    }
    const why = nodeGateReason(ping, localBunMM, localCodeHash)
    if (why) {
      process.stderr.write(`[eval-course-ckpt] node ${nid}: ${why} — skipped\n`)
      continue
    }
    alive.push(n)
  }

  const nodesOk: DistNodeCfg[] = []
  if (alive.length > 0) {
    for (const n of alive) {
      let ok = true
      for (const slot of ctx.weightsSlots) {
        try {
          await uploadWeightsToNode(n, slot, ctx.iterId)
        } catch (e) {
          process.stderr.write(
            `[eval-course-ckpt] ${n.id || n.url}: weights upload failed (${(e as Error).message})\n`,
          )
          ok = false
          break
        }
      }
      if (ok) nodesOk.push(n)
    }
  }

  const remoteCap = nodesOk.reduce((s, n) => s + Math.max(1, n.concurrency ?? 4), 0)
  const localCap = Math.max(0, ctx.localWorkers)
  process.stderr.write(
    `[eval-course-ckpt] hybrid dispatch: ${nodesOk.length} nodes (${remoteCap} slots) + local ${localCap}, ${total} games, codeHash=${localCodeHash.slice(0, 12)}…\n`,
  )
  if (nodesOk.length === 0 && localCap <= 0)
    throw new Error('no eval-capable node and no local worker')

  const stageNameOf = (stageLocal: number): string =>
    ctx.stagePayloads[stageLocal]?.name ?? 'custom'

  const payloadFor = (job: CourseJob): EvalCourseWorkerPayload => {
    const slot = ctx.weightsSlots[job.weightIdx]
    return {
      weightsPath: slot.path,
      label: slot.label,
      policy: ctx.policy,
      difficulty: ctx.difficulty,
      maxTicks: ctx.maxTicks,
      lives: ctx.lives,
      level: ctx.level,
      stages: ctx.stagePayloads,
      jobs: [{ id: job.id, stageLocal: job.stageLocal, seed: job.seed }],
    }
  }

  // ---- 远端 fetch-loop ----
  const RETRY_BACKOFF = [3, 5, 8, 12, 18, 26, 38, 60]
  const spawnNode = (node: DistNodeCfg): void => {
    const cap = Math.max(1, node.concurrency ?? 4)
    const nid = node.id || node.url
    batch.consumer(cap)
    for (let s = 0; s < cap; s++) {
      ;(async (): Promise<void> => {
        try {
          for (;;) {
            const i = batch.claim(batch.hasInflight)
            if (i < 0 || i >= total) return
            const job = ctx.jobs[i]
            const slot = ctx.weightsSlots[job.weightIdx]
            const stageJson = ctx.stagePayloads[job.stageLocal]?.json ?? ''
            const url = buildRemoteTaskUrl({
              baseUrl: node.url,
              iterId: ctx.iterId,
              wver: slot.sha,
              kind: slot.kind,
              stageId: job.stageId,
              seed: job.seed,
              maxTicks: ctx.maxTicks,
              difficulty: ctx.difficulty,
              policy: ctx.policy,
              stageJson,
              livesOverride: ctx.lives,
              playerLevel: ctx.level,
            })
            let ok = false
            let lastErr = ''
            for (let attempt = 0; attempt < RETRY_BACKOFF.length && !ok; attempt++) {
              try {
                const resp = await fetch(url, {
                  headers: { Authorization: `Bearer ${node.authKey ?? ''}` },
                  signal: AbortSignal.timeout((ctx.maxTicks / 20 + 120) * 1000),
                })
                if (resp.status === 409) {
                  lastErr = `${nid}: wver not cached (409)`
                  break
                }
                if (resp.status !== 200) {
                  lastErr = `${nid}: HTTP ${resp.status}`
                  const retryAfter = Number(resp.headers.get('retry-after') ?? '')
                  await new Promise((r) =>
                    setTimeout(
                      r,
                      ((retryAfter > 0 ? retryAfter : RETRY_BACKOFF[attempt]) || 5) * 1000,
                    ),
                  )
                  continue
                }
                const { manifest } = unpackContainer(Buffer.from(await resp.arrayBuffer()))
                const row = manifestToCourseRow(
                  manifest as Record<string, unknown>,
                  job,
                  stageNameOf(job.stageLocal),
                )
                settle(job, row)
                ok = true
              } catch (e) {
                lastErr = `${nid}: ${(e as Error).message}`
                if (attempt + 1 < RETRY_BACKOFF.length)
                  await new Promise((r) => setTimeout(r, RETRY_BACKOFF[attempt] * 1000))
              }
            }
            if (!ok)
              settle(
                job,
                null,
                `${job.label}#${job.id} s${job.stageId}/seed${job.seed} ${lastErr || 'remote failed'}`,
              )
          }
        } finally {
          batch.finishConsumer()
        }
      })()
    }
  }

  // ---- 本地 worker-loop ----
  const spawnLocal = (workerId: number): void => {
    batch.consumer(1)
    void (async (): Promise<void> => {
      const w = new Worker(WORKER_URL)
      try {
        for (;;) {
          const i = batch.claim(batch.hasInflight)
          if (i < 0 || i >= total) return
          const job = ctx.jobs[i]
          try {
            const d = await runWorkerOnce(w, payloadFor(job))
            if (d.error) settle(job, null, d.error)
            else
              settle(
                job,
                d.results[0] ?? null,
                d.results[0] ? undefined : `empty result #${job.id}`,
              )
          } catch (e) {
            settle(job, null, `local#${workerId} ${(e as Error).message}`)
          }
        }
      } finally {
        w.terminate()
        batch.finishConsumer()
      }
    })()
  }

  for (let li = 0; li < localCap; li++) spawnLocal(li)
  for (const n of nodesOk) spawnNode(n)

  // 无消费者守护
  setTimeout(() => {
    if (batch.pendingConsumers <= 0 && done < total) {
      const failed = batch.failUnsettled()
      process.stderr.write(
        `[eval-course-ckpt] no consumer available — failing ${failed.length} remaining tasks\n`,
      )
      for (const id of failed) {
        const job = ctx.jobs[id]
        if (job && !results[id]) errors.push(`${job.label}#${job.id} unsettled (no consumer)`)
      }
    }
  }, 2500)

  await batch.whenAll()
  // 保序输出：按 id 回填（竞速不影响内容）
  const ordered: EvalCourseRow[] = []
  for (const r of results) if (r) ordered.push(r)
  return { rows: ordered, errors }
}

async function main(): Promise<void> {
  const courseArg = arg('course')
  if (!courseArg) {
    console.error('[eval-course-ckpt] --course <name|path> required')
    process.exit(2)
  }
  const policy = arg('policy') ?? 'nn'
  if (policy !== 'nn' && policy !== 'god' && policy !== 'nn-goal') {
    console.error(`[eval-course-ckpt] unknown --policy '${policy}' (nn|god|nn-goal)`)
    process.exit(2)
  }
  // goal 层测试：nn-goal 的外部目标源（god|heuristic）+ 软偏置强度，透传 env/payload。
  if (policy === 'nn-goal') {
    const src = arg('goal-source') ?? 'god'
    if (src !== 'god' && src !== 'heuristic') {
      console.error(`[eval-course-ckpt] --goal-source must be 'god'|'heuristic'`)
      process.exit(2)
    }
    process.env.GOAL_SOURCE = src
    const bias = arg('goal-bias')
    if (bias !== undefined) {
      const b = Number(bias)
      if (!Number.isFinite(b) || b <= 0) {
        console.error(`[eval-course-ckpt] --goal-bias must be a positive number`)
        process.exit(2)
      }
      process.env.GOAL_BIAS = String(b)
    }
  }
  const weightPaths = argAll('weights')
  if ((policy === 'nn' || policy === 'nn-goal') && weightPaths.length === 0) {
    console.error(
      `[eval-course-ckpt] --weights <file> required for --policy ${policy} (repeatable)`,
    )
    process.exit(2)
  }
  const weights = policy === 'god' ? [{ path: '', label: 'god' }] : weightPaths.map(parseWeightSpec)
  const games = parseRangeInt(arg('games'), 100)
  const seed0 = parseRangeInt(arg('seed0'), 0)
  const workersArg = parseInt(arg('workers') ?? '0', 10)
  const workers = workersArg > 0 ? workersArg : defaultWorkerCount()
  const outPath = arg('out')

  const course = parseCourseJsonc(readFileSync(resolveCourse(courseArg), 'utf8')) as CourseJson
  const stages = course.stages
  if (!Array.isArray(stages) || stages.length === 0) {
    console.error(`[eval-course-ckpt] course has no custom stages: ${courseArg}`)
    process.exit(2)
  }
  const difficulty = course.difficulty ?? 'hard'
  const maxTicks = course.max_ticks ?? 36000
  const lives = course.player?.lives ?? 3
  const level = course.player?.level ?? 0
  const stagePayloads = stages.map((s) => ({ name: s.name ?? 'custom', json: JSON.stringify(s) }))

  // ---- dist 开关（与 m1-eval auto-dist 同规）----
  // 布尔存在位用 flag()（位置无关）：arg() 取「下一个 token」，把 --no-dist 写在
  // 命令行末尾时取到 undefined → 静默忽略（2026-09-19 实测，与 m1-eval 同规加固）。
  const noDist = flag('no-dist')
  let distCfgPath = arg('dist-nodes') ?? ''
  if (!distCfgPath && !noDist) {
    try {
      if (
        existsSync(DEFAULT_DIST_CFG) &&
        readFileSync(DEFAULT_DIST_CFG, 'utf8').includes('"nodes"')
      )
        distCfgPath = DEFAULT_DIST_CFG
    } catch {
      /* 纯本地 */
    }
  }
  // nn-goal：GOAL_* 未进 agent 协议，强制本地。
  let distPolicy = policy
  if (policy === 'nn-goal') {
    if (distCfgPath)
      process.stderr.write(
        `[eval-course-ckpt] policy nn-goal is not dispatchable — running local only\n`,
      )
    distCfgPath = ''
    distPolicy = 'nn-goal'
  }
  const distLocalArg = parseInt(arg('dist-local') ?? String(workers), 10)
  const distLocal = Number.isFinite(distLocalArg) && distLocalArg >= 0 ? distLocalArg : workers
  const iterId = arg('iter-id') ?? `evalcourse-${Date.now()}`

  const oversized = stagePayloads.filter((s) => s.json.length > STAGE_JSON_MAX)
  if (distCfgPath && oversized.length > 0) {
    process.stderr.write(
      `[eval-course-ckpt] ${oversized.length} stageJson > ${STAGE_JSON_MAX}B — dist disabled (local only)\n`,
    )
    distCfgPath = ''
  }

  process.stderr.write(
    `[eval-course-ckpt] course=${courseArg} stages=${stages.length} games/weights=${games} ` +
      `policy=${policy} weights=${weights.length} workers=${workers} difficulty=${difficulty} ` +
      `max_ticks=${maxTicks} lives=${lives} level=${level}` +
      (distCfgPath ? ` dist=${distCfgPath} distLocal=${distLocal}` : ' dist=local') +
      `\n`,
  )

  const t0 = Date.now()

  // ---- 混合分派路径 ----
  if (distCfgPath) {
    const weightsSlots: WeightSlot[] = weights.map((w) => {
      if (policy === 'god' || !w.path) {
        const bytes = Buffer.from('{}')
        return {
          path: w.path,
          label: w.label,
          bytes,
          sha: createHash('sha256').update(bytes).digest('hex'),
          kind: 'none' as const,
        }
      }
      const bytes = readFileSync(w.path)
      return {
        path: w.path,
        label: w.label,
        bytes,
        sha: createHash('sha256').update(bytes).digest('hex'),
        kind: 'rollout' as const,
      }
    })
    const jobs = buildCourseJobs(weights, games, seed0, stages.length)
    let hybridErrors: string[] = []
    let rows: EvalCourseRow[] = []
    try {
      const r = await runHybrid({
        jobs,
        stagePayloads,
        weightsSlots,
        difficulty,
        maxTicks,
        lives,
        level,
        policy: distPolicy === 'nn-goal' ? 'nn' : distPolicy,
        iterId,
        localWorkers: distLocal,
        distCfgPath,
      })
      rows = r.rows
      hybridErrors = r.errors
    } catch (e) {
      process.stderr.write(
        `[eval-course-ckpt] hybrid failed (${(e as Error).message}) — falling back to local\n`,
      )
      distCfgPath = ''
    }
    if (distCfgPath) {
      if (hybridErrors.length > 0) {
        for (const e of hybridErrors) process.stderr.write(`[eval-course-ckpt] ${e}\n`)
      }
      if (rows.length !== jobs.length) {
        process.stderr.write(
          `[eval-course-ckpt] incomplete hybrid results ${rows.length}/${jobs.length} — exit 1\n`,
        )
        // 仍写出已得行，便于断点/诊断
        emitRows(
          rows.sort((a, b) => a.id - b.id),
          outPath,
        )
        summarize(rows, outPath, t0)
        process.exit(1)
      }
      rows.sort((a, b) => a.id - b.id)
      emitRows(rows, outPath)
      summarize(rows, outPath, t0)
      return
    }
  }

  // ---- 纯本地 chunked 路径（旧行为）----
  const jobsPerWeight: Array<Array<{ id: number; stageLocal: number; seed: number }>> = weights.map(
    (_, wi) => {
      const jobs: Array<{ id: number; stageLocal: number; seed: number }> = []
      for (let g = 0; g < games; g++) {
        jobs.push({
          id: wi * games + g,
          stageLocal: g % stages.length,
          seed: seed0 + Math.floor(g / stages.length),
        })
      }
      return jobs
    },
  )

  // One chunk per (weight, round-robin job slice) — one fresh worker per chunk,
  // each returns { results } once. Rows are ordered by id after concat.
  const chunks: EvalCourseWorkerPayload[] = []
  for (let wi = 0; wi < weights.length; wi++) {
    const slices = splitRoundRobin(jobsPerWeight[wi], workers)
    for (const slice of slices) {
      if (slice.length === 0) continue
      chunks.push({
        weightsPath: weights[wi].path,
        label: weights[wi].label,
        policy,
        goalSource: policy === 'nn-goal' ? (process.env.GOAL_SOURCE ?? 'god') : undefined,
        goalBias: policy === 'nn-goal' ? (process.env.GOAL_BIAS ?? undefined) : undefined,
        difficulty,
        maxTicks,
        lives,
        level,
        stages: stagePayloads,
        jobs: slice,
      })
    }
  }

  const chunkResults = await runChunks(chunks)
  const errors = chunkResults.filter((r) => r.error)
  if (errors.length > 0) {
    for (const e of errors) process.stderr.write(`[eval-course-ckpt] ${e.error}\n`)
    process.exit(1)
  }
  const rows = chunkResults.flatMap((r) => r.results).sort((a, b) => a.id - b.id)
  emitRows(rows, outPath)
  summarize(rows, outPath, t0)
}

if (import.meta.main) await main()
