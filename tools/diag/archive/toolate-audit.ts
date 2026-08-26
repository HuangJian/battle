#!/usr/bin/env bun
/**
 * toolate-audit.ts — §225 "too late" defense-structure forensics (protocol §6,
 * §218 后继 ④).
 *
 * Question: in base_destroyed failures, what is the player doing between the
 * FIRST base HP drop and the base's destruction? Is the loss "too late" —
 * the player was absent/stationary/dead — or did the defense actively fail?
 *
 * Input: a run-forensics --json sweep (baseline); a stratified sample of
 * base_destroyed runs is replayed with threatLedger to get the per-tick
 * baseHp curve + player position + branch.
 */
import { STAGES } from '../../../src/config/stages'
import { runSimulation } from '../../sim/simulation-runner'
import { DEFAULT_FORENSICS_CORPUS } from '../../lib/eval-refs'

const j = JSON.parse(await Bun.file(process.argv[2] ?? DEFAULT_FORENSICS_CORPUS).text())
const failures = j.perDifficulty.hard.failures as Array<{
  stageIdx: number
  seed: number
  failureCause: string
  ticks: number
}>

// Stratified sample: 40 base_destroyed runs spread over the failure-tick
// range (fast / mid / slow burns) and over stages.
const bd = failures.filter((f) => f.failureCause === 'base_destroyed')
const bdSorted = [...bd].sort((a, b) => a.ticks - b.ticks)
const N = 40
const sample: Array<{ stageIdx: number; seed: number; ticks: number }> = []
for (let i = 0; i < N; i++) {
  const idx = Math.floor((i / N) * (bdSorted.length - 1))
  const f = bdSorted[idx]
  if (!sample.some((s) => s.stageIdx === f.stageIdx && s.seed === f.seed))
    sample.push({ stageIdx: f.stageIdx, seed: f.seed, ticks: f.ticks })
}

const BASE_COL = 13 // col 12-13, center col
const BASE_ROW = 25 // rows 24-25, center row

interface WindowStat {
  key: string
  ticks: number
  firstDamage: number
  window: number
  playerDeadAt: number // tick of last player death before destruction; -1 none
  deathBeforeDestroy: boolean
  branchCounts: Record<string, number>
  sentryTicks: number
  absentTicks: number // player > 8 cells from base center
  stationaryTicks: number // noOpReason non-null
  baseThreatTicks: number
  nearTicks: number // player <= 2 cells from base center
}

const out: WindowStat[] = []
let done = 0
for (const s of sample) {
  const r = runSimulation({
    seed: s.seed,
    stage: STAGES[s.stageIdx],
    stageIndex: 0,
    difficulty: 'hard',
    maxTicks: 36000,
    threatLedger: true,
  })
  const ledger = r.ledger
  if (!ledger) {
    console.error(`no ledger for S${s.stageIdx + 1}s${s.seed}`)
    continue
  }
  const samples = ledger.samples
  let firstDamage = -1
  for (const sm of samples) {
    if (sm.baseHp < ledger.baseMaxHp) {
      firstDamage = sm.tick
      break
    }
  }
  if (firstDamage < 0) {
    // No base damage in ledger — skip (should not happen for base_destroyed)
    done++
    continue
  }
  const branchCounts: Record<string, number> = {}
  let sentryTicks = 0
  let absentTicks = 0
  let stationaryTicks = 0
  let baseThreatTicks = 0
  let nearTicks = 0
  let lastDeathTick = -1
  let prevLives = samples[0]?.playerLives ?? 3
  for (const sm of samples) {
    if (sm.tick < firstDamage) {
      if (sm.playerLives < prevLives) lastDeathTick = sm.tick
      prevLives = sm.playerLives
      continue
    }
    branchCounts[sm.branch] = (branchCounts[sm.branch] ?? 0) + 1
    if (sm.branch === 'baseLaneSentry') sentryTicks++
    const d = Math.abs(sm.playerCell.col - BASE_COL) + Math.abs(sm.playerCell.row - BASE_ROW)
    if (d > 8) absentTicks++
    if (d <= 2) nearTicks++
    if (sm.noOpReason !== null) stationaryTicks++
    if (sm.baseThreatNow) baseThreatTicks++
    if (sm.playerLives < prevLives) lastDeathTick = sm.tick
    prevLives = sm.playerLives
  }
  out.push({
    key: `S${s.stageIdx + 1}s${s.seed}`,
    ticks: s.ticks,
    firstDamage,
    window: s.ticks - firstDamage,
    playerDeadAt: lastDeathTick,
    deathBeforeDestroy: lastDeathTick >= 0 && lastDeathTick > firstDamage,
    branchCounts,
    sentryTicks,
    absentTicks,
    stationaryTicks,
    baseThreatTicks,
    nearTicks,
  })
  done++
  if (done % 10 === 0) console.error(`[${done}/${sample.length}]`)
}

// ---------------------------------------------------------------- report
const W = out.length
console.log(`\n=== "too late" window audit (${W} base_destroyed runs, stratified ${N}) ===`)
const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b)
  return s[Math.floor(s.length / 2)]
}
console.log(
  `first-damage tick: median ${median(out.map((o) => o.firstDamage))} (min ${Math.min(...out.map((o) => o.firstDamage))}, max ${Math.max(...out.map((o) => o.firstDamage))})`,
)
console.log(`window (first damage → destruction): median ${median(out.map((o) => o.window))} ticks`)
const deathBefore = out.filter((o) => o.deathBeforeDestroy)
console.log(
  `player died BEFORE destruction: ${deathBefore.length}/${W} (${((deathBefore.length / W) * 100).toFixed(1)}%)`,
)
const absent = out.filter((o) => o.absentTicks > o.window * 0.5)
console.log(
  `player ABSENT (>8 cells) for >50% of the window: ${absent.length}/${W} (${((absent.length / W) * 100).toFixed(1)}%)`,
)
const stat = out.filter((o) => o.stationaryTicks > o.window * 0.5)
console.log(
  `stationary no-output for >50% of the window: ${stat.length}/${W} (${((stat.length / W) * 100).toFixed(1)}%)`,
)
const sentried = out.filter((o) => o.sentryTicks > 0)
console.log(
  `baseLaneSentry engaged: ${sentried.length}/${W} (${((sentried.length / W) * 100).toFixed(1)}%) | median sentry ticks ${median(sentried.map((o) => o.sentryTicks))}`,
)
const near = out.filter((o) => o.nearTicks > 0)
console.log(`player near base (<=2 cells) at some point in window: ${near.length}/${W}`)
// branch mix over the window (aggregate)
const agg: Record<string, number> = {}
for (const o of out) for (const [b, n] of Object.entries(o.branchCounts)) agg[b] = (agg[b] ?? 0) + n
console.log(`\nwindow branch mix (all ${W} runs):`)
for (const [b, n] of Object.entries(agg).sort((a, b) => b[1] - a[1]))
  console.log(
    `  ${b.padEnd(18)} ${n} (${((n / Object.values(agg).reduce((x, y) => x + y, 0)) * 100).toFixed(1)}%)`,
  )
console.log('\nper-run detail (first 15):')
for (const o of out.slice(0, 15)) {
  const top = Object.entries(o.branchCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([b, n]) => `${b}:${n}`)
    .join(' ')
  console.log(
    `  ${o.key} f@${o.firstDamage} win=${o.window} dead=${o.playerDeadAt >= 0} abs=${o.absentTicks} stat=${o.stationaryTicks} sentry=${o.sentryTicks} [${top}]`,
  )
}
await Bun.write('tmp/toolate-audit.json', JSON.stringify(out, null, 2))
