#!/usr/bin/env bun
/**
 * export-intent-replays.ts — 用指定意图 NN 权重跑指定关卡并落盘 .replay 文件，
 * 供 ReplayBrowser 在浏览器里回放观看（plan/God-AI-Replay-Visualization §4.1）。
 *
 * 与 m1-eval 的 intent-exec 同一条代码路径（runSimulation { policy:'intent-exec' }），
 * 只多了 record:true → InputRecorder 录制 → writeReplayFile 序列化。
 *
 * 用法：
 *   bun tools/sim/export-intent-replays.ts --stages 14 --seeds 1-10 \
 *       --weights tmp/intent-rl/weights.json --out-dir replays-intent
 */
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { STAGES } from '../../src/config/stages'
import { runSimulation } from './simulation-runner'
import { writeReplayFile } from './replay-writer'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  const hit = i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : undefined
  return hit ?? fallback
}

async function main(): Promise<void> {
  const stageSpecs = (arg('stages', '14') ?? '14').split(',')
  const seedSpec = arg('seeds', '1-10')!
  const weights = arg('weights', 'tmp/intent-rl/weights.json')!
  const outDir = arg('out-dir', 'replays-intent')!
  const maxTicks = parseInt(arg('max-ticks', '12000')!, 10)
  const difficulty = arg('difficulty', 'hard')!

  const stages = stageSpecs.map((s) => {
    const idx = parseInt(s, 10) - 1 // CLI 1-based；内部 0-based
    if (!STAGES[idx]) throw new Error(`invalid --stages ${s} (1..${STAGES.length})`)
    return { index: idx, data: STAGES[idx], name: STAGES[idx].name }
  })
  let seeds: number[]
  if (seedSpec.includes('-')) {
    const [a, b] = seedSpec.split('-').map(Number)
    seeds = []
    for (let n = a; n <= b; n++) seeds.push(n)
  } else {
    seeds = seedSpec.split(',').map(Number)
  }

  // graphviz 无；纯记录 + 概览
  const rows: Array<{
    seed: number
    outcome: string
    kills: number
    ticks: number
    path: string | null
  }> = []
  for (const sg of stages) {
    for (const seed of seeds) {
      const res = runSimulation({
        stageIndex: sg.index,
        stage: sg.data,
        difficulty,
        seed,
        policy: 'intent-exec',
        intentWeightsDir: weights,
        maxTicks,
        record: true,
        collectMetrics: false,
        collectEvents: false,
        telemetry: true,
      })
      const path = await writeReplayFile({
        result: res,
        dir: outDir,
        stageIndex: sg.index,
        stageName: sg.name,
      })
      rows.push({
        seed,
        outcome: res.outcome,
        kills: res.finalState.killCount,
        ticks: res.ticks,
        path,
      })
      console.log(
        `s${sg.index + 1} ${sg.name} seed=${seed} ${res.outcome} kills=${res.finalState.killCount}` +
          ` ticks=${res.ticks}${path ? ` -> ${path}` : ' (no replay)'}`,
      )
    }
  }

  const header = ['seed', 'outcome', 'kills', 'ticks', 'file']
  writeFileSync(
    join(outDir, 'README.md'),
    `# intent NN replays (${difficulty}) — ${stages.map((s) => `Stage ${s.index + 1} ${s.name}`).join(', ')}\n\n` +
      `weights: ${weights}\n\n| ${header.join(' | ')} |\n| ${header.map(() => '---').join(' | ')} |\n` +
      rows
        .map(
          (r) =>
            `| ${r.seed} | ${r.outcome} | ${r.kills} | ${r.ticks} | ${r.path ? r.path.split('/').pop() : '—'} |`,
        )
        .join('\n') +
      '\n',
  )
  console.log(
    `summary -> ${join(outDir, 'README.md')} (${rows.length} runs, best=${rows.some((r) => r.outcome === 'stage_clear') ? 'has clear' : 'no clear'})`,
  )
}

if (import.meta.main) {
  await main()
}

export {}
