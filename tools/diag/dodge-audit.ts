#!/usr/bin/env bun
/**
 * dodge-audit.ts — §223 dodge-branch idle forensics (protocol §6, DECISIONS §222 后继 ③).
 *
 * Question: in FAILED runs, what is the player doing in the last ≤10 ticks?
 * Specifically the dodge branch: is dodge a moving escape (legitimate) or a
 * standing no-output stall (idle) — and how does dodge compare to other
 * branches on terminal tick?
 *
 * Input: a run-forensics --json sweep (baseline params, same caliber).
 */
export {}
import { DEFAULT_FORENSICS_CORPUS } from '../lib/eval-refs'
const j = JSON.parse(
  await Bun.file(process.argv[2] ?? DEFAULT_FORENSICS_CORPUS).text(),
)
const failures = j.perDifficulty.hard.failures as Array<{
  stageIdx: number
  seed: number
  outcome: string
  failureCause: string
  killerKind: string
  ticks: number
  forensics: {
    lastActions: Array<{ tick: number; branch: string; moveDir: string | null; fire: boolean }>
    events: Array<{ tick: number; type: string; detail: string }>
    playerDeaths: number
    terminal: { playerAlive: boolean; playerLives: number }
  }
}>

const branchCount = new Map<string, number>()
const branchFire = new Map<string, number>()
const branchMove = new Map<string, number>()
const branchNull = new Map<string, number>() // moveDir === null ticks
let dodgeTicks = 0
let dodgeIdleTicks = 0 // dodge + moveDir null
let dodgeFireTicks = 0
let totalTicks = 0

const deathRuns: Array<{
  key: string
  tick: number
  lastBranch: string
  lastBranchCount: number
  actions: number
}> = []
const branchAtDeath = new Map<string, number>()

for (const f of failures) {
  const la = f.forensics.lastActions ?? []
  totalTicks += la.length
  for (const a of la) {
    branchCount.set(a.branch, (branchCount.get(a.branch) ?? 0) + 1)
    if (a.fire) branchFire.set(a.branch, (branchFire.get(a.branch) ?? 0) + 1)
    if (a.moveDir !== null) branchMove.set(a.branch, (branchMove.get(a.branch) ?? 0) + 1)
    else branchNull.set(a.branch, (branchNull.get(a.branch) ?? 0) + 1)
    if (a.branch === 'dodge') {
      dodgeTicks++
      if (a.moveDir === null) dodgeIdleTicks++
      if (a.fire) dodgeFireTicks++
    }
  }
  // Player death runs: lastActions end at the terminal tick; for
  // lives_exhausted the terminal tick IS the final death tick.
  const deaths = (f.forensics.events ?? []).filter((e) => e.type === 'death')
  if (deaths.length > 0) {
    const lastDeath = deaths[deaths.length - 1]
    const last = la[la.length - 1]
    if (last) {
      const lastBranch = last.branch
      branchAtDeath.set(lastBranch, (branchAtDeath.get(lastBranch) ?? 0) + 1)
      const same = la.filter((a) => a.branch === lastBranch).length
      deathRuns.push({
        key: `S${f.stageIdx + 1}s${f.seed}`,
        tick: lastDeath.tick,
        lastBranch,
        lastBranchCount: same,
        actions: la.length,
      })
    }
  }
}

console.log('=== 末 10 tick 分支分布 (518 失败局) ===')
const sorted = [...branchCount.entries()].sort((a, b) => b[1] - a[1])
for (const [b, n] of sorted) {
  const fire = branchFire.get(b) ?? 0
  const move = branchMove.get(b) ?? 0
  const nul = branchNull.get(b) ?? 0
  console.log(
    `${b.padEnd(18)} ${String(n).padStart(5)} (${((n / totalTicks) * 100).toFixed(1)}%)  fire=${String(fire).padStart(4)} move=${String(move).padStart(4)} idle=${String(nul).padStart(4)}`,
  )
}
console.log(`\n=== dodge 细分 ===`)
console.log(
  `dodge ticks: ${dodgeTicks} (${((dodgeTicks / totalTicks) * 100).toFixed(1)}% of terminal ticks)`,
)
console.log(
  `  idle (moveDir null): ${dodgeIdleTicks} (${((dodgeIdleTicks / dodgeTicks) * 100).toFixed(1)}% of dodge)`,
)
console.log(
  `  fire: ${dodgeFireTicks} (${((dodgeFireTicks / dodgeTicks) * 100).toFixed(1)}% of dodge)`,
)

console.log(`\n=== 玩家死亡局的死亡 tick 分支 (${deathRuns.length} 局) ===`)
for (const [b, n] of [...branchAtDeath.entries()].sort((a, b) => b[1] - a[1]))
  console.log(
    `${b.padEnd(18)} ${String(n).padStart(5)} (${((n / deathRuns.length) * 100).toFixed(1)}%)`,
  )
const dodgeDeaths = deathRuns.filter((d) => d.lastBranch === 'dodge')
console.log(
  `dodge 死亡 ${dodgeDeaths.length} 局: ${dodgeDeaths.map((d) => `${d.key}@${d.tick}[${d.lastBranchCount}/${d.actions}]`).join(' ')}`,
)
