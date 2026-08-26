#!/usr/bin/env bun
/**
 * export-intent-labels.ts — M0b 机械意图 tagger + 可学习性探针语料导出器
 * (plan/Intent-Policy-NN-Plan.md §3.6 / §5.1 / 预注册 #19/#28)。
 *
 * 用【临时规则版标签器】（= vocab.ts 的 19 候选→8 意图映射机械实现，一次性、
 * 不进正式 tagger）全量重跑逐 tick 打标；共享分段实现（segmentIntents——与 M1
 * 正式 tagger 同一模块，四件套 P0-2 探针同步④）展开成逐帧意图标签。
 *
 * 并行：WorkerPool（tools/lib/worker-pool.ts）按物理核扇出，每 job 一局；
 * 结果按任务 id 归并 = 串行逐字典序（池的确定性注记），ghost 表与并行度无关。
 *
 * 输出：
 *   shards/<sNN-seedN-difficulty>/  obs.npy (N,14,26,26,u1) / scalars.npy
 *                                   intent.npy (N,u1) / frame.npy (N,f8)
 *                                   manifest.json（含段序列）
 *   ghost.json                      类分布幽灵表（自然分布口径）+ ±5 tick 翻转
 *
 * 采样方案（预注册 #10 初值）：均匀 replan 网格（--grid-period 默认 30）
 * ∪ 全部意图转移边界帧（段首）。obs 只在采样帧编码。
 *
 * 用法：
 *   bun tools/sim/export-intent-labels.ts --stages 1 --seeds 1 --out tmp/intent-smoke
 *   bun tools/sim/export-intent-labels.ts --stages all --seeds 1-60 --difficulty hard --out tmp/intent-probe-hard
 */
import { WorkerPool, defaultWorkerCount } from '../lib/worker-pool'
import { STAGES } from '../../src/config/stages'
import { INTENT_IDS } from '../../src/ai/intent/vocab'
import { writeFileSync, mkdirSync } from 'fs'
import type { TaggerAggregate, TaggerJob, TaggerPayload } from './export-intent-labels-worker'

const EXPORTER_VERSION = '0.2.0'

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : def
}
function flag(name: string): boolean {
  return process.argv.includes(`--${name}`)
}
function parseRange(spec: string): number[] {
  if (spec.includes('-')) {
    const [a, b] = spec.split('-').map(Number)
    return Array.from({ length: b - a + 1 }, (_, i) => a + i)
  }
  return [Number(spec)]
}

async function main(): Promise<void> {
  const stagesSpec = arg('stages', 'all')!
  const seedsSpec = arg('seeds', '1-10')!
  const difficulty = arg('difficulty', 'hard')!
  const outRoot = arg('out', 'tmp/intent-probe')!
  const maxTicks = Number(arg('max-ticks', '36000'))
  const gridPeriod = Number(arg('grid-period', '30'))
  const workers = Number(arg('workers', String(defaultWorkerCount())))

  let stageIdxs: number[]
  if (stagesSpec === 'all') {
    stageIdxs = STAGES.map((_, i) => i)
  } else if (stagesSpec.includes(',')) {
    stageIdxs = stagesSpec.split(',').map((s) => Number(s) - 1)
  } else {
    stageIdxs = parseRange(stagesSpec).map((n) => n - 1)
  }
  const seeds = parseRange(seedsSpec)

  const shardDir = `${outRoot}/shards`
  mkdirSync(shardDir, { recursive: true })

  const jobs: TaggerJob[] = []
  for (const si of stageIdxs) for (const seed of seeds) jobs.push({ id: jobs.length, si, seed })

  console.error(
    `[intent-tagger] ${stageIdxs.length} stages × ${seeds.length} seeds = ${jobs.length} games · ` +
      `difficulty=${difficulty} gridPeriod=${gridPeriod} workers=${workers}`,
  )

  const payload: Omit<TaggerPayload, 'jobs'> = {
    difficulty,
    maxTicks,
    gridPeriod,
    shardDir,
    force: flag('force'),
  }

  const t0 = Date.now()
  const pool = new WorkerPool<TaggerPayload, TaggerAggregate>(
    new URL('./export-intent-labels-worker.ts', import.meta.url).href,
    workers,
    'intent-tagger',
  )
  const tasks: TaggerPayload[] = jobs.map((j) => ({ ...payload, jobs: [j] }))
  let lastPct = -1
  const aggs = await pool.runBatch(tasks, (done) => {
    const pct = Math.floor((done / jobs.length) * 100)
    if (pct !== lastPct && pct % 5 === 0) {
      lastPct = pct
      console.error(`[intent-tagger] ${done}/${jobs.length} (${pct}%)`)
    }
  })
  pool.terminate()

  // ---- 按 id 序归并（= 串行字典序，确定性）----
  const merged = {
    windows: {} as Record<string, number>,
    windowCount: 0,
    frames: 0,
    flipFrames: 0,
    flipComparable: 0,
  }
  for (const id of INTENT_IDS) merged.windows[id] = 0
  const outcomes: Record<string, number> = {}
  for (const a of aggs) {
    for (const [k, v] of Object.entries(a.windows)) merged.windows[k] = (merged.windows[k] ?? 0) + v
    merged.windowCount += a.windowCount
    merged.frames += a.ticks
    merged.flipFrames += a.flipFrames
    merged.flipComparable += a.flipComparable
    outcomes[a.outcome] = (outcomes[a.outcome] ?? 0) + 1
  }

  const ghost = {
    exporterVersion: EXPORTER_VERSION,
    difficulty,
    stages: stagesSpec,
    seeds: seedsSpec,
    gridPeriod,
    naturalWindows: merged.windows,
    totalWindows: merged.windowCount,
    totalFrames: merged.frames,
    avgWindowTicks: merged.windowCount > 0 ? merged.frames / merged.windowCount : 0,
    flipRatioPlusMinus5: merged.flipComparable > 0 ? merged.flipFrames / merged.flipComparable : 0,
    outcomes,
    wallSeconds: ((Date.now() - t0) / 1000).toFixed(1),
    workers,
    preregistered: {
      debounceN: 4,
      minWindowsPerClass: 200,
      verdictBasis: 'natural distribution (P1-5k3 #28)',
    },
  }
  writeFileSync(`${outRoot}/ghost.json`, JSON.stringify(ghost, null, 2))
  console.log(JSON.stringify(ghost, null, 2))
}

await main()
