/**
 * nav-stall-audit.ts — 量化「长时间 navigate 不接战」。
 *
 * 采集：runSimulation({ navStallAudit: true, navStallMinTicks }) 返回连续
 * `navigate` ≥ N 拍的段，每段带最近敌距离、贴脸拍数、开火/射击/命中/击杀、
 * 移动距离、基地血量变化。只读观测（15 局 parity 验证 outcome/ticks/kills 一致）。
 *
 * Usage
 *   bun tools/diag/nav-stall-audit.ts                      hard 35×60，段阈值 300
 *   bun tools/diag/nav-stall-audit.ts --min-ticks 600      只统计更长段
 *   bun tools/diag/nav-stall-audit.ts --seeds 20 --breakdown
 *   bun tools/diag/nav-stall-audit.ts --json tmp/nav-stall.json
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { runSimulation } from '../sim/simulation-runner'
import type { NavStallSegment } from '../sim/simulation-runner'
import { arg, flag, parseSeeds, parseStages, parseParamSets } from '../lib/cli'
import { writeFileSync } from 'node:fs'

const seeds = parseSeeds(arg('seeds', '60')!, 60)
const stageIdxs = parseStages(arg('stages'))
const difficulty = arg('difficulty', 'hard')!
const maxTicks = Number(arg('max-ticks', '18000'))
const minTicks = Number(arg('min-ticks', '300'))
const jsonOut = arg('json')
const breakdown = flag('breakdown')
const overrides = parseParamSets(DEFAULT_GOD_AI_PARAMS)
const params = { ...DEFAULT_GOD_AI_PARAMS, ...overrides }

interface Row {
  stageIndex: number
  name: string
  runs: number
  wins: number
  ticks: number
  stallTicks: number
  segments: number
  closeSegments: number // near5 ≥ 50% of segment
  closeTicks: number
  kills: number
  stallKills: number
  segLens: number[]
  minDists: number[]
  fireTicks: number
  freezeTicks: number
  shots: number
  hits: number
}

const rows: Row[] = []
let allRuns = 0
let allWins = 0
let allTicks = 0
let allStallTicks = 0
let allKills = 0
let allStallKills = 0
let runsWithStall = 0
let fullSegs: NavStallSegment[] = []
const t0 = performance.now()

for (const si of stageIdxs) {
  const stage = STAGES[si]
  const row: Row = {
    stageIndex: si,
    name: stage.name,
    runs: 0,
    wins: 0,
    ticks: 0,
    stallTicks: 0,
    segments: 0,
    closeSegments: 0,
    closeTicks: 0,
    kills: 0,
    stallKills: 0,
    segLens: [],
    minDists: [],
    fireTicks: 0,
    freezeTicks: 0,
    shots: 0,
    hits: 0,
  }
  for (const seed of seeds) {
    const r = runSimulation({
      seed,
      stage,
      stageIndex: si,
      difficulty,
      godAIParams: params,
      maxTicks,
      sampleInterval: maxTicks,
      collectMetrics: false,
      navStallAudit: true,
      navStallMinTicks: minTicks,
    })
    const segs = r.navStalls ?? []
    row.runs++
    allRuns++
    row.ticks += r.ticks
    allTicks += r.ticks
    row.kills += r.finalState.killCount
    allKills += r.finalState.killCount
    if (r.outcome === 'stage_clear') {
      row.wins++
      allWins++
    }
    if (segs.length) runsWithStall++
    for (const s of segs) {
      row.segments++
      allStallTicks += s.ticks
      row.stallTicks += s.ticks
      row.stallKills += s.kills
      allStallKills += s.kills
      row.fireTicks += s.fireTicks
      row.freezeTicks += s.freezeTicks
      row.shots += s.playerShots
      row.hits += s.enemyHits
      row.segLens.push(s.ticks)
      if (s.minEnemyDist >= 0) row.minDists.push(s.minEnemyDist)
      if (s.ticks > 0 && s.near5Ticks / s.ticks >= 0.5) {
        row.closeSegments++
        row.closeTicks += s.ticks
      }
      fullSegs.push(s)
    }
  }
  rows.push(row)
  if (rows.length % 5 === 0) {
    process.stderr.write(
      `  … ${rows.length * seeds.length}/${stageIdxs.length * seeds.length} runs (${((performance.now() - t0) / 1000).toFixed(0)}s)\n`,
    )
  }
}

const med = (xs: number[]) => {
  const a = [...xs].sort((x, y) => x - y)
  return a.length ? a[a.length >> 1] : 0
}
const pct = (n: number, d: number) => (d === 0 ? '—' : ((n / d) * 100).toFixed(2) + '%')

const closeSegs = fullSegs.filter((s) => s.ticks > 0 && s.near5Ticks / s.ticks >= 0.5)
const stallKillRate = allStallKills / Math.max(1, allStallTicks)
const outsideRate = (allKills - allStallKills) / Math.max(1, allTicks - allStallTicks)

const L: string[] = []
L.push('')
L.push(
  `NAV-STALL AUDIT · ${difficulty} · ${stageIdxs.length} stages × ${seeds.length} seeds = ${allRuns} runs · 段阈值 ${minTicks} tick · maxTicks ${maxTicks}`,
)
L.push(
  `wall ${((performance.now() - t0) / 1000).toFixed(0)}s · win ${pct(allWins, allRuns)} · mean ticks ${(allTicks / allRuns).toFixed(0)}`,
)
L.push('')
L.push('— 规模 —')
L.push(
  `navigate 长段 ${fullSegs.length} 个 · 出现在 ${runsWithStall}/${allRuns} 局（${pct(runsWithStall, allRuns)}）· ` +
    `段长中位 ${med(fullSegs.map((s) => s.ticks))} 拍 · 总停滞 ${allStallTicks} 拍（${pct(allStallTicks, allTicks)} 的仿真 tick）`,
)
L.push(
  `其中「贴身空转」段（≥50% 拍有敌人 ≤5 格）${closeSegs.length} 个（${pct(closeSegs.length, fullSegs.length)}）· ` +
    `占 ${closeSegs.reduce((a, s) => a + s.ticks, 0)} 拍`,
)
L.push(
  `冻结相关性：段内有冻结的 tick ${fullSegs.reduce((a, s) => a + s.freezeTicks, 0)}（${pct(
    fullSegs.reduce((a, s) => a + s.freezeTicks, 0),
    allStallTicks,
  )} 的停滞拍）· ` +
    `含冻结的段 ${fullSegs.filter((s) => s.freezeTicks > 0).length} 个（${pct(fullSegs.filter((s) => s.freezeTicks > 0).length, fullSegs.length)}）`,
)
L.push('')
L.push('— NAV INTENT（长段的目的地与原因）—')
const BASE_COL = 12
const BASE_ROW = 24
const targetKind = (s2: NavStallSegment): string => {
  if (s2.targetCol < 0) return 'none'
  const d = Math.abs(s2.targetCol - BASE_COL) + Math.abs(s2.targetRow - BASE_ROW)
  return d <= 4 ? 'base-area' : 'field'
}
const byReason = new Map<string, NavStallSegment[]>()
for (const s2 of fullSegs) {
  const arr = byReason.get(s2.reason) ?? []
  arr.push(s2)
  byReason.set(s2.reason, arr)
}
L.push(
  'reason        segs   ticks    len   tgtDist(start→end)  reached  tgtChg  kills/1k  base-area',
)
for (const [reason, arr] of [...byReason.entries()].sort((a, b) => b[1].length - a[1].length)) {
  const ticks = arr.reduce((a, s2) => a + s2.ticks, 0)
  const kills = arr.reduce((a, s2) => a + s2.kills, 0)
  const reached = arr.filter((s2) => s2.reachedTarget).length
  const nearBase = arr.filter((s2) => targetKind(s2) === 'base-area').length
  L.push(
    `${reason.padEnd(12)} ${String(arr.length).padStart(5)} ${String(ticks).padStart(8)} ` +
      `${String(med(arr.map((s2) => s2.ticks))).padStart(6)}   ` +
      `${String(med(arr.map((s2) => s2.targetDistStart))).padStart(3)} → ${String(med(arr.map((s2) => s2.targetDistEnd))).padStart(3)} 格`.padEnd(
        20,
      ) +
      ` ${pct(reached, arr.length).padStart(7)}  ${String(med(arr.map((s2) => s2.targetChanges))).padStart(5)} ` +
      ` ${((kills / Math.max(1, ticks)) * 1000).toFixed(1).padStart(8)}  ${pct(nearBase, arr.length)}`,
  )
}
L.push('')
L.push('— 段内行为 —')
L.push(
  `开火拍 ${med(fullSegs.map((s) => s.fireTicks))} / 段（中位）· 射击 ${med(fullSegs.map((s) => s.playerShots))} · 命中 ${med(fullSegs.map((s) => s.enemyHits))} · 击杀 ${med(fullSegs.map((s) => s.kills))}`,
)
L.push(
  `段内最近敌距中位 ${med(fullSegs.map((s) => s.minEnemyDist))} 格 · 移动中位 ${med(fullSegs.map((s) => s.travelCells))} 格 · ` +
    `基地掉血中位 ${med(fullSegs.map((s) => s.baseHpStart - s.baseHpEnd))}`,
)
L.push(
  `击杀率：停滞段内 ${(stallKillRate * 1000).toFixed(2)} 杀/千拍 vs 段外 ${(outsideRate * 1000).toFixed(2)} 杀/千拍（${(stallKillRate / outsideRate || 0).toFixed(2)}×）`,
)
L.push(
  `贴身空转段：开火拍中位 ${med(closeSegs.map((s) => s.fireTicks))} · 射击 ${med(closeSegs.map((s) => s.playerShots))} · ` +
    `命中 ${med(closeSegs.map((s) => s.enemyHits))} · 击杀 ${med(closeSegs.map((s) => s.kills))} · ` +
    `移动 ${med(closeSegs.map((s) => s.travelCells))} 格`,
)
L.push(
  `  → 贴身空转总时长 ${closeSegs.reduce((a, s) => a + s.ticks, 0)} 拍 · 其中击杀 ${closeSegs.reduce((a, s) => a + s.kills, 0)}`,
)
L.push('')

if (breakdown) {
  L.push('— PER-STAGE —')
  L.push('stage                     stall%  segs/run  close%  kills/1k(stall)')
  for (const r of rows) {
    L.push(
      `${`${r.stageIndex + 1}. ${r.name}`.padEnd(24)} ${((r.stallTicks / r.ticks) * 100).toFixed(1).padStart(6)}% ` +
        `${(r.segments / r.runs).toFixed(2).padStart(8)} ` +
        `${((r.closeTicks / Math.max(1, r.stallTicks)) * 100).toFixed(0).padStart(6)}% ` +
        `${((r.stallKills / Math.max(1, r.stallTicks)) * 1000).toFixed(1).padStart(16)}`,
    )
  }
  L.push('')
}

console.log(L.join('\n'))

if (jsonOut) {
  writeFileSync(
    jsonOut,
    JSON.stringify(
      {
        meta: {
          difficulty,
          runs: allRuns,
          minTicks,
          maxTicks,
          stages: stageIdxs.length,
          seeds: seeds.length,
          overrides,
        },
        stages: rows.map((r) => ({ ...r, segLens: undefined, minDists: undefined })),
        segments: fullSegs,
      },
      null,
      0,
    ),
  )
  console.log(`json → ${jsonOut}`)
}
