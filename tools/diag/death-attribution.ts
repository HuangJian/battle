#!/usr/bin/env bun
/**
 * death-attribution.ts — M0 第一交付物（plan/God-AI-Redesign-v2 §6）.
 *
 * 回答 "God AI 在 X 难度最常见的死法是什么"：
 *   1. 跑 (stage × seed) 网格，telemetry: true
 *   2. 聚合每次玩家死亡的 {killerTier, killerKind, branch} 三元组
 *   3. 输出 top 死亡模式 + 逐关死亡表 + 归因 JSON
 *
 * 数据来源：SimResult.telemetry.deaths（M0 新增的逐死亡事件，含凶手
 * 坦克 id → kind/AI 层级、死亡时 think() 分支、死亡位置与距基地距离）。
 *
 * 用法：
 *   bun tools/diag/death-attribution.ts --difficulty hard --seeds 20
 *   bun tools/diag/death-attribution.ts --difficulty chaos --stages 20-34 --json tmp/deaths.json
 */
import { writeFileSync } from 'node:fs'
import { STAGES } from '../../src/config/stages'
import { runSimulation } from '../sim/simulation-runner'
import type { PlayerDeath } from '../sim/simulation-runner'

const MAX_TICKS = 18000

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : fallback
}

function parseStages(spec: string | undefined): number[] {
  if (!spec) return STAGES.map((_, i) => i)
  const out: number[] = []
  for (const part of spec.split(',')) {
    const m = /^(\d+)-(\d+)$/.exec(part.trim())
    if (m) {
      for (let i = Number(m[1]); i <= Number(m[2]); i++) out.push(i)
    } else {
      const n = Number(part.trim())
      if (Number.isInteger(n) && n >= 0 && n < STAGES.length) out.push(n)
    }
  }
  return out
}

interface DeathRow extends PlayerDeath {
  stage: string
  stageIndex: number
  seed: number
}

interface CellOutcome {
  stageIndex: number
  stageName: string
  seed: number
  outcome: string
  ticks: number
}

function main(): void {
  const difficulty = arg('difficulty', 'hard')!
  const seedCount = Number(arg('seeds', '20'))
  const stageIdxs = parseStages(arg('stages'))
  const jsonOut = arg('json')

  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const cells: CellOutcome[] = []
  const rows: DeathRow[] = []
  let deathRuns = 0

  for (const si of stageIdxs) {
    for (const seed of seeds) {
      // NOTE: no stageIndex — official 口径 (DECISIONS §97/§101). Passing it
      // scales killScore by levelFactor(stageIndex), shifting the
      // dropOnScoreMilestone power-up drops and diverging the RNG stream from
      // the gate / eval-suite shape (proven 2026-08-03: Twin Spires chaos
      // 8/20 vs 11/20 for stageIndex 16 vs 0). Death attribution must match
      // the official evaluation shape or its branch fractions are artifacts.
      const r = runSimulation({
        seed,
        stage: STAGES[si],
        difficulty,
        maxTicks: MAX_TICKS,
        sampleInterval: MAX_TICKS,
        telemetry: true,
      })
      cells.push({
        stageIndex: si,
        stageName: STAGES[si].name,
        seed,
        outcome: r.outcome,
        ticks: r.ticks,
      })
      const ds = r.telemetry?.deaths ?? []
      if (ds.length > 0) deathRuns++
      for (const d of ds) rows.push({ ...d, stage: STAGES[si].name, stageIndex: si, seed })
    }
  }

  const totalRuns = cells.length
  const wins = cells.filter((c) => c.outcome === 'stage_clear').length
  const winRate = totalRuns > 0 ? wins / totalRuns : 0

  // ---- Aggregates ----
  const count = (fn: (r: DeathRow) => string | undefined): Array<[string, number]> => {
    const m = new Map<string, number>()
    for (const r of rows) {
      const k = fn(r) ?? '(unknown)'
      m.set(k, (m.get(k) ?? 0) + 1)
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }

  const byBranch = count((r) => r.branch)
  const byTier = count((r) => r.killerTier)
  const byKind = count((r) => r.killerKind)
  const byTierBranch = count((r) => `${r.killerTier ?? 'unknown'} × ${r.branch}`)

  // Per-stage summary
  const byStage = new Map<string, { runs: number; wins: number; deaths: number; distSum: number }>()
  for (const c of cells) {
    const s = byStage.get(c.stageName) ?? { runs: 0, wins: 0, deaths: 0, distSum: 0 }
    s.runs++
    if (c.outcome === 'stage_clear') s.wins++
    byStage.set(c.stageName, s)
  }
  for (const r of rows) {
    const s = byStage.get(r.stage)
    if (s) {
      s.deaths++
      s.distSum += r.distToBase
    }
  }
  const stageTable = [...byStage.entries()]
    .map(([name, s]) => ({
      name,
      winRate: s.wins / s.runs,
      deathsPerRun: s.deaths / s.runs,
      avgDistToBase: s.deaths > 0 ? s.distSum / s.deaths : NaN,
    }))
    .sort((a, b) => a.winRate - b.winRate)

  // ---- Report ----
  const pct = (x: number): string => `${(x * 100).toFixed(0)}%`
  const bar = '─'.repeat(78)
  console.log(`\n${bar}`)
  console.log(
    `死亡归因 · difficulty=${difficulty} · ${stageIdxs.length} 关 × ${seeds.length} seeds = ${totalRuns} 局`,
  )
  console.log(`${bar}`)
  console.log(`  过关率      ${pct(winRate)} (${wins}/${totalRuns})`)
  console.log(
    `  玩家总死亡  ${rows.length} (${deathRuns}/${totalRuns} 局有死亡，${(rows.length / totalRuns).toFixed(2)} 次/局)`,
  )

  const printTop = (title: string, list: Array<[string, number]>): void => {
    console.log(`\n  ${title}`)
    const total = list.reduce((a, [, n]) => a + n, 0)
    for (const [k, n] of list.slice(0, 8)) {
      console.log(
        `    ${(k + ':').padEnd(22)} ${n.toString().padStart(5)}  ${pct(n / total).padStart(4)}`,
      )
    }
  }
  printTop('死亡时 think() 分支 (branch)', byBranch)
  printTop('凶手 AI 层级 (killerTier)', byTier)
  printTop('凶手坦克类型 (killerKind)', byKind)
  printTop('层级 × 分支 (top combos)', byTierBranch)

  console.log(`\n  逐关死亡表 (按过关率升序, top 12):`)
  console.log(
    `    ${'stage'.padEnd(22)}${'win'.padStart(7)}${'deaths/run'.padStart(11)}${'avgDist'.padStart(9)}`,
  )
  for (const s of stageTable.slice(0, 12)) {
    console.log(
      `    ${s.name.slice(0, 21).padEnd(22)}${pct(s.winRate).padStart(7)}${s.deathsPerRun.toFixed(2).padStart(11)}${(Number.isNaN(s.avgDistToBase) ? 'n/a' : s.avgDistToBase.toFixed(1)).padStart(9)}`,
    )
  }

  if (jsonOut) {
    writeFileSync(
      jsonOut,
      JSON.stringify(
        {
          generatedAt: new Date().toISOString(),
          config: { difficulty, stageIdxs, seeds: seeds.length, maxTicks: MAX_TICKS },
          summary: { totalRuns, wins, winRate, deaths: rows.length, deathRuns },
          byBranch,
          byTier,
          byKind,
          byTierBranch,
          stages: stageTable,
          cells,
          deaths: rows,
        },
        null,
        2,
      ),
    )
    console.log(`\nWrote ${jsonOut}`)
  }
}

await main()
