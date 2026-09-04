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
 * the fields the RL metrics (reward_library METRICS 21-dim) track.
 *
 * Determinism & parallelism: each game is a pure function of (weights bytes,
 * stageLocal, seed) — fresh World, own seeded RNG, decodeStageGrid spawn
 * variant picked by seed hash. Jobs are split round-robin across
 * `runChunkedWorkers` (physical-core cap, defaultWorkerCount) so parallel ==
 * serial; JSONL rows are emitted in submission order (stable aggregation).
 *
 * Usage:
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset \
 *       --weights tmp/p1-bc-smoke/weights.json.ckpt.3 --games 100
 *   bun tools/sim/eval-course-ckpt.ts --course nn-training/curricula/p1-onset.jsonc \
 *       --weights a.json --weights b.json --games 50 --workers 8 \
 *       --out tmp/p1-eval-rows.jsonl
 *   bun tools/sim/eval-course-ckpt.ts --course p1-onset --policy god --games 100 \
 *       # true God-AI (DEFAULT_GOD_AI_PARAMS, RNG 同 simulation-runner，无需 --weights)
 *
 * Output: one JSON row per game (JSONL) to --out or stdout; human summary per
 * checkpoint to stderr.
 */
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { dirname } from 'path'
import { splitRoundRobin, defaultWorkerCount } from '../lib/worker-pool'
import type { EvalCourseWorkerPayload, EvalCourseRow } from './eval-course-ckpt-worker'

const WORKER_URL = new URL('./eval-course-ckpt-worker.ts', import.meta.url).href
const CURRICULA_DIR = 'nn-training/curricula'

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

/** Strip // and block comments from JSONC, respecting strings (course files). */
function stripJsonc(text: string): string {
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

interface LabelAgg {
  label: string
  games: number
  wins: number
  cleared: number
  outcomes: Record<string, number>
  kills: number
  enemyHits: number
  playerHits: number
  playerDamageTaken: number
  playerShots: number
  ticks: number
}

async function main(): Promise<void> {
  const courseArg = arg('course')
  if (!courseArg) {
    console.error('[eval-course-ckpt] --course <name|path> required')
    process.exit(2)
  }
  const policy = arg('policy') ?? 'nn'
  if (policy !== 'nn' && policy !== 'god') {
    console.error(`[eval-course-ckpt] unknown --policy '${policy}' (nn|god)`)
    process.exit(2)
  }
  const weightPaths = argAll('weights')
  if (policy === 'nn' && weightPaths.length === 0) {
    console.error('[eval-course-ckpt] --weights <file> required for --policy nn (repeatable)')
    process.exit(2)
  }
  const weights =
    policy === 'god'
      ? [{ path: '', label: 'god' }]
      : weightPaths.map((p) => ({ path: p, label: p.split(/[\\/]/).pop() ?? p }))
  const games = parseRangeInt(arg('games'), 100)
  const seed0 = parseRangeInt(arg('seed0'), 0)
  const workersArg = parseInt(arg('workers') ?? '0', 10)
  const workers = workersArg > 0 ? workersArg : defaultWorkerCount()
  const outPath = arg('out')

  const course = JSON.parse(
    stripJsonc(readFileSync(resolveCourse(courseArg), 'utf8')),
  ) as CourseJson
  const stages = course.stages
  if (!Array.isArray(stages) || stages.length === 0) {
    console.error(`[eval-course-ckpt] course has no custom stages: ${courseArg}`)
    process.exit(2)
  }
  const difficulty = course.difficulty ?? 'hard'
  const maxTicks = course.max_ticks ?? 36000
  const lives = course.player?.lives ?? 3
  const level = course.player?.level ?? 0

  process.stderr.write(
    `[eval-course-ckpt] course=${courseArg} stages=${stages.length} games/weights=${games} ` +
      `policy=${policy} weights=${weights.length} workers=${workers} difficulty=${difficulty} ` +
      `max_ticks=${maxTicks} lives=${lives} level=${level}\n`,
  )

  // jobs[weight][g]: stageLocal = g % nStages ; seed = seed0 + floor(g / nStages)
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

  const stagePayloads = stages.map((s) => ({ name: s.name ?? 'custom', json: JSON.stringify(s) }))

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
        difficulty,
        maxTicks,
        lives,
        level,
        stages: stagePayloads,
        jobs: slice,
      })
    }
  }

  const t0 = Date.now()
  const chunkResults = await runChunks(chunks)
  const errors = chunkResults.filter((r) => r.error)
  if (errors.length > 0) {
    for (const e of errors) process.stderr.write(`[eval-course-ckpt] ${e.error}\n`)
    process.exit(1)
  }
  const rows = chunkResults.flatMap((r) => r.results).sort((a, b) => a.id - b.id)
  const lines = rows.map((r) => JSON.stringify(r))
  if (outPath) {
    mkdirSync(dirname(outPath), { recursive: true })
    writeFileSync(outPath, lines.join('\n') + '\n')
  } else {
    for (const l of lines) console.log(l)
  }

  // ---- per-label summary ----
  const agg = new Map<string, LabelAgg>()
  for (const r of rows) {
    let a = agg.get(r.label)
    if (!a) {
      a = {
        label: r.label,
        games: 0,
        wins: 0,
        cleared: 0,
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
    `${'label'.padEnd(28)} win    kills  hit(敌) beHit(玩家) dmg     shots  avgTicks  max_ticks gameover\n`,
  )
  for (const a of agg.values()) {
    process.stderr.write(
      `${a.label.padEnd(28)} ${`${a.wins}/${a.games}`.padEnd(6)} ${String(a.kills).padEnd(6)} ` +
        `${String(a.enemyHits).padEnd(7)} ${String(a.playerHits).padEnd(10)} ` +
        `${String(a.playerDamageTaken).padEnd(7)} ${String(a.playerShots).padEnd(6)} ` +
        `${Math.round(a.ticks / Math.max(1, a.games))
          .toString()
          .padEnd(9)} ` +
        `${String(a.outcomes['max_ticks'] ?? 0).padEnd(9)} ${a.outcomes['gameover'] ?? 0}\n`,
    )
  }
  process.stderr.write(
    `[eval-course-ckpt] win rate per checkpoint above; full JSONL ${outPath ? `-> ${outPath}` : 'on stdout'}\n`,
  )
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

await main()
