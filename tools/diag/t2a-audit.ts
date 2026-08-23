#!/usr/bin/env bun
/**
 * t2a-audit.ts — §227 t2a suicide-guard re-argumentation (protocol §6,
 * §218 后继 ⑤). §223 found: in the final 10 ticks, t2a commits are 72%
 * no-output (442/611) — the player stands still at the enemy's muzzle.
 *
 * This audit replays a stratified sample of base_destroyed runs with the
 * threat ledger and dissects the FINAL 300 ticks: how much of the t2a idle
 * is cooldown-waiting, how much happens with an enemy bullet inbound
 * (nearestThreatEta), and the noOpReason breakdown. Whole-run t2a idle
 * rate is reported as the comparison baseline.
 */
import { STAGES } from '../../src/config/stages'
import { runSimulation } from '../sim/simulation-runner'
import { DEFAULT_FORENSICS_CORPUS } from '../lib/eval-refs'

const j = JSON.parse(
  await Bun.file(process.argv[2] ?? DEFAULT_FORENSICS_CORPUS).text(),
)
const failures = j.perDifficulty.hard.failures as Array<{
  stageIdx: number
  seed: number
  failureCause: string
  ticks: number
}>

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

interface RunStat {
  key: string
  endT2a: number
  endT2aIdle: number
  endIdleCooldown: number
  endIdleThreat: number // idle + enemy bullet inbound (nearestThreatEta <= 40)
  endIdleDeadline: number // idle + eta <= 20 (imminent)
  wholeT2a: number
  wholeT2aIdle: number
  noOpByReason: Record<string, number>
  t2aIdleBeforeDeath: number // final 60 ticks
  playerAliveTicks: number
}

const out: RunStat[] = []
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
  if (!ledger) continue
  const samples = ledger.samples
  const end = Math.max(0, s.ticks - 300)
  const st: RunStat = {
    key: `S${s.stageIdx + 1}s${s.seed}`,
    endT2a: 0,
    endT2aIdle: 0,
    endIdleCooldown: 0,
    endIdleThreat: 0,
    endIdleDeadline: 0,
    wholeT2a: 0,
    wholeT2aIdle: 0,
    noOpByReason: {},
    t2aIdleBeforeDeath: 0,
    playerAliveTicks: 0,
  }
  for (const sm of samples) {
    if (sm.branch === 't2a') {
      st.wholeT2a++
      if (sm.noOpReason !== null) st.wholeT2aIdle++
    }
    if (sm.tick < end) continue
    if (sm.playerLives > 0) st.playerAliveTicks++
    if (sm.branch === 't2a') {
      st.endT2a++
      if (sm.noOpReason !== null) {
        st.endT2aIdle++
        st.noOpByReason[sm.noOpReason] = (st.noOpByReason[sm.noOpReason] ?? 0) + 1
        if (sm.onCooldown) st.endIdleCooldown++
        if (sm.nearestThreatEta <= 40) st.endIdleThreat++
        if (sm.nearestThreatEta <= 20) st.endIdleDeadline++
        if (sm.tick >= s.ticks - 60) st.t2aIdleBeforeDeath++
      }
    }
  }
  out.push(st)
  done++
  if (done % 10 === 0) console.error(`[${done}/${sample.length}]`)
}

const W = out.length
const sum = (f: (o: RunStat) => number) => out.reduce((a, o) => a + f(o), 0)
const endT2a = sum((o) => o.endT2a)
const endIdle = sum((o) => o.endT2aIdle)
console.log(`\n=== t2a suicide-guard audit (${W} base_destroyed runs, final 300 ticks) ===`)
console.log(
  `t2a commits (end): ${endT2a} | idle (no-output): ${endIdle} (${((endIdle / Math.max(1, endT2a)) * 100).toFixed(1)}%)`,
)
console.log(
  `  of idle: onCooldown ${sum((o) => o.endIdleCooldown)} (${((sum((o) => o.endIdleCooldown) / Math.max(1, endIdle)) * 100).toFixed(1)}%)`,
)
console.log(
  `  of idle: enemy bullet inbound eta<=40: ${sum((o) => o.endIdleThreat)} (${((sum((o) => o.endIdleThreat) / Math.max(1, endIdle)) * 100).toFixed(1)}%)`,
)
console.log(
  `  of idle: imminent eta<=20: ${sum((o) => o.endIdleDeadline)} (${((sum((o) => o.endIdleDeadline) / Math.max(1, endIdle)) * 100).toFixed(1)}%)`,
)
console.log(`  of idle: last 60 ticks before destruction: ${sum((o) => o.t2aIdleBeforeDeath)}`)
const noOp: Record<string, number> = {}
for (const o of out)
  for (const [k, v] of Object.entries(o.noOpByReason)) noOp[k] = (noOp[k] ?? 0) + v
console.log('\nnoOpReason breakdown (end-300 window):')
for (const [k, v] of Object.entries(noOp).sort((a, b) => b[1] - a[1]))
  console.log(`  ${k.padEnd(30)} ${v} (${((v / Math.max(1, endIdle)) * 100).toFixed(1)}%)`)
const wholeT2a = sum((o) => o.wholeT2a)
const wholeIdle = sum((o) => o.wholeT2aIdle)
console.log(
  `\nwhole-run baseline: t2a ${wholeT2a} | idle ${wholeIdle} (${((wholeIdle / Math.max(1, wholeT2a)) * 100).toFixed(1)}%)`,
)
const idleDead = out.filter((o) => o.t2aIdleBeforeDeath > 0)
console.log(`runs with t2a idle in final 60 ticks: ${idleDead.length}/${W}`)
console.log('\nper-run (first 12):')
for (const o of out.slice(0, 12))
  console.log(
    `  ${o.key} end-t2a=${o.endT2a} idle=${o.endT2aIdle} cd=${o.endIdleCooldown} threat=${o.endIdleThreat} d60=${o.t2aIdleBeforeDeath}`,
  )
await Bun.write('tmp/t2a-audit.json', JSON.stringify(out, null, 2))
