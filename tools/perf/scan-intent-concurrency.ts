/**
 * scan-intent-concurrency.ts — 找 intent-exec 批跑的最优并行度（内存带宽争抢权衡）。
 *
 * 背景（用户 2026-08-27）：16 worker 全并行时 IntentNet forward 被 CPU/内存带宽争抢
 * 从 41ms 放大到 ~150ms。本工具在同一子集（可配置 stages×seeds）上跑不同 --fixed-workers
 * 的 m1-eval，比较 wall-time 与单位时间吞吐，找总吞吐峰值并行度。
 *
 * 用法：bun tools/perf/scan-intent-concurrency.ts [--stages 1-5] [--seeds 1-30] [--workers 8,12,16]
 * 注意：须在无其它大任务时跑（本工具自身串行跑各档，避免自相争抢污染测量）。
 */
import { spawnSync } from 'node:child_process'

const arg = (name: string, fb: string) => {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fb
}
const stages = arg('stages', '1-5')
const seeds = arg('seeds', '1-30')
const workersList = (arg('workers', '16,12,8') ?? '16,12,8').split(',').map(Number)

const rows: Array<{ w: number; sec: number; games: number; wps: number }> = []
for (const w of workersList) {
  const t0 = Date.now()
  const r = spawnSync(
    'bun',
    [
      'tools/sim/m1-eval.ts',
      '--stages',
      stages,
      '--seeds',
      seeds,
      '--difficulty',
      'hard',
      '--policy',
      'intent-exec',
      '--intent-weights',
      'tmp/intent-weights-Bp.json',
      '--fixed-workers',
      String(w),
      '--out',
      `tmp/m1_scan_w${w}.html`,
    ],
    { stdio: 'pipe', encoding: 'utf8' },
  )
  const sec = (Date.now() - t0) / 1000
  const out = r.stderr + '\n' + r.stdout
  const win = out.match(/WIN RATE[^\n]*/) ?? []
  const games = stages.includes('-')
    ? (Number(stages.split('-')[1]) - Number(stages.split('-')[0]) + 1) *
      (seeds.includes('-') ? Number(seeds.split('-')[1]) - Number(seeds.split('-')[0]) + 1 : 1)
    : 1 * (seeds.includes('-') ? Number(seeds.split('-')[1]) - Number(seeds.split('-')[0]) + 1 : 1)
  rows.push({ w, sec, games, wps: games / sec })
  console.log(`[scan] w=${w}: ${sec.toFixed(1)}s  ${win[0] ?? ''}`)
}
console.log('---')
rows.sort((a, b) => b.wps - a.wps)
for (const r of rows) {
  console.log(
    `w=${r.w}: ${r.sec.toFixed(1)}s  ${r.games}/${r.sec.toFixed(1)}s = ${r.wps.toFixed(2)} 局/s  (vs best ${((r.wps / rows[0].wps) * 100).toFixed(0)}%)`,
  )
}
console.log(`最优并行度: w=${rows[0].w}`)
