/**
 * base-loss-forensics.ts — "did 自杀秒回 (§116) have anything to bite on?"
 *
 * Sweeps the full 35-stage corpus across N seeds per difficulty and, for every
 * run that ends with the base destroyed, reports the six quantities requested:
 *
 *   1. base-destroyed rate
 *   2. player lives at the moment of base loss
 *   3. player distance to base at that moment
 *   4. player HP (hits it could still absorb) when the killing bullet was fired
 *   5. enemy bullets threatening the player at that fire tick
 *   6. enemy bullets able to hit the player within 1 s of that fire tick
 *
 * Usage:
 *   bun tools/diag/base-loss-forensics.ts --seeds 120 --difficulty hard,chaos
 *   bun tools/diag/base-loss-forensics.ts --seeds 20 --stages 3,9,17 --json out.json
 */
import { STAGES } from '../../src/config/stages'
import { defaultWorkerCount } from '../sim/sim-pool'
import type { BaseLossRecord, RunResult } from './base-loss-run'
import type { ForensicTask, ForensicTaskResult } from './base-loss-worker'

const WORKER_URL = new URL('./base-loss-worker.ts', import.meta.url).href

class ForensicPool {
  private workers: Worker[] = []
  readonly size: number
  constructor(size: number = defaultWorkerCount()) {
    this.size = Math.max(1, size)
    for (let i = 0; i < this.size; i++) this.workers.push(new Worker(WORKER_URL))
  }
  runBatch(
    tasks: ForensicTask[],
    onProgress?: (done: number) => void,
  ): Promise<ForensicTaskResult[]> {
    if (tasks.length === 0) return Promise.resolve([])
    return new Promise((resolve, reject) => {
      const results: ForensicTaskResult[] = Array.from({ length: tasks.length })
      let next = 0
      let done = 0
      const dispatch = (w: Worker): void => {
        if (next >= tasks.length) return
        w.postMessage(tasks[next++])
      }
      for (const w of this.workers) {
        w.onmessage = (ev: MessageEvent<ForensicTaskResult>) => {
          results[ev.data.id] = ev.data
          done++
          onProgress?.(done)
          if (done === tasks.length) resolve(results)
          else dispatch(w)
        }
        w.onerror = (err) => reject(new Error(`base-loss-worker failed: ${err.message ?? err}`))
      }
      for (const w of this.workers) dispatch(w)
    })
  }
  terminate(): void {
    for (const w of this.workers) w.terminate()
    this.workers = []
  }
}

// ============================================================
// Stats helpers
// ============================================================

function mean(xs: number[]): number {
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN
}
function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN
  const pos = (sorted.length - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}
interface Summary {
  n: number
  mean: number
  p25: number
  median: number
  p75: number
  min: number
  max: number
}
function summarize(xs: number[]): Summary {
  const s = [...xs].sort((a, b) => a - b)
  return {
    n: s.length,
    mean: mean(s),
    p25: quantile(s, 0.25),
    median: quantile(s, 0.5),
    p75: quantile(s, 0.75),
    min: s.length ? s[0] : NaN,
    max: s.length ? s[s.length - 1] : NaN,
  }
}
function fmt(x: number, d = 2): string {
  return Number.isFinite(x) ? x.toFixed(d) : '—'
}
function histogram(xs: number[], cap = 6): string {
  const counts = new Map<number, number>()
  for (const x of xs) {
    const k = Math.min(x, cap)
    counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  const keys = [...counts.keys()].sort((a, b) => a - b)
  return keys
    .map((k) => {
      const pct = ((counts.get(k)! / xs.length) * 100).toFixed(1)
      return `${k === cap && xs.some((x) => x > cap) ? `${k}+` : k}:${counts.get(k)} (${pct}%)`
    })
    .join('  ')
}

// ============================================================
// Reporting
// ============================================================

function reportDifficulty(
  difficulty: string,
  runs: RunResult[],
  seedCount: number,
  stageCount: number,
): string {
  const out: string[] = []
  const total = runs.length
  const losses = runs
    .filter((r) => r.outcome === 'base_destroyed')
    .map((r) => r.loss!) as BaseLossRecord[]
  const clears = runs.filter((r) => r.outcome === 'stage_clear').length
  const livesOut = runs.filter((r) => r.outcome === 'lives_exhausted').length
  const timeouts = runs.filter((r) => r.outcome === 'timeout').length

  out.push(`\n${'='.repeat(78)}`)
  out.push(
    `DIFFICULTY: ${difficulty}   (${stageCount} stages × ${seedCount} seeds = ${total} runs)`,
  )
  out.push('='.repeat(78))

  // ---- metric 1 ----
  const lossRate = (losses.length / total) * 100
  const failures = total - clears
  out.push(`\n[1] OUTCOME MIX`)
  out.push(
    `    stage_clear      ${clears.toString().padStart(5)}  (${fmt((clears / total) * 100, 1)}%)`,
  )
  out.push(
    `    base_destroyed   ${losses.length.toString().padStart(5)}  (${fmt(lossRate, 1)}% of all runs` +
      `, ${fmt(failures ? (losses.length / failures) * 100 : 0, 1)}% of failures)`,
  )
  out.push(
    `    lives_exhausted  ${livesOut.toString().padStart(5)}  (${fmt((livesOut / total) * 100, 1)}%)`,
  )
  out.push(
    `    timeout          ${timeouts.toString().padStart(5)}  (${fmt((timeouts / total) * 100, 1)}%)`,
  )

  if (losses.length === 0) {
    out.push('\n    no base losses — nothing further to report.')
    return out.join('\n')
  }

  const withFire = losses.filter((l) => l.fire) as (BaseLossRecord & {
    fire: NonNullable<BaseLossRecord['fire']>
  })[]
  const noFire = losses.length - withFire.length
  const selfKills = losses.filter((l) => l.killerKind === 'player').length
  out.push(
    `    ...of which SELF-INFLICTED (player's own bullet): ${selfKills}` +
      ` (${fmt((selfKills / Math.max(1, losses.length)) * 100, 1)}% of base losses,` +
      ` ${fmt((selfKills / total) * 100, 2)}% of all runs)`,
  )

  // ---- metric 2 ----
  const lives = summarize(losses.map((l) => l.livesAtLoss))
  out.push(`\n[2] PLAYER LIVES AT BASE LOSS   (n=${lives.n})`)
  out.push(
    `    mean ${fmt(lives.mean)}   median ${fmt(lives.median, 1)}   p25 ${fmt(lives.p25, 1)}   p75 ${fmt(lives.p75, 1)}   range ${lives.min}..${lives.max}`,
  )
  out.push(`    distribution: ${histogram(losses.map((l) => l.livesAtLoss))}`)
  const ge2 = losses.filter((l) => l.livesAtLoss >= 2).length
  out.push(
    `    lives >= 2 (§116 minLives gate): ${ge2}/${losses.length} (${fmt((ge2 / losses.length) * 100, 1)}%)`,
  )

  // ---- metric 3 ----
  const dist = summarize(
    losses.filter((l) => l.distToBaseAtLoss >= 0).map((l) => l.distToBaseAtLoss),
  )
  out.push(`\n[3] PLAYER DISTANCE TO BASE AT LOSS  (Manhattan cells, n=${dist.n})`)
  out.push(
    `    mean ${fmt(dist.mean)}   median ${fmt(dist.median, 1)}   p25 ${fmt(dist.p25, 1)}   p75 ${fmt(dist.p75, 1)}   range ${dist.min}..${dist.max}`,
  )
  const near = losses.filter((l) => l.distToBaseAtLoss >= 0 && l.distToBaseAtLoss <= 6).length
  const far = losses.filter((l) => l.distToBaseAtLoss > 12).length
  out.push(
    `    within 6 cells: ${near} (${fmt((near / dist.n) * 100, 1)}%)   beyond 12 cells: ${far} (${fmt((far / dist.n) * 100, 1)}%)`,
  )
  const deadAtLoss = losses.filter((l) => !l.playerAliveAtLoss).length
  out.push(
    `    player already dead/respawning at the loss instant: ${deadAtLoss} (${fmt((deadAtLoss / losses.length) * 100, 1)}%)`,
  )

  if (withFire.length === 0) {
    out.push(
      `\n    killing-bullet fire tick unresolved for all ${losses.length} losses — metrics 4-6 unavailable.`,
    )
    return out.join('\n')
  }
  out.push(
    `\n    killing-bullet muzzle tick resolved for ${withFire.length}/${losses.length} losses` +
      (noFire ? ` (${noFire} unresolved — bullet predates event capture)` : ''),
  )

  // ---- metric 4 ----
  const alive = withFire.filter((l) => l.fire.playerAlive)
  const hp = summarize(alive.map((l) => l.fire.hp))
  const hits = summarize(alive.map((l) => l.fire.hitsRemaining))
  out.push(
    `\n[4] PLAYER HP WHEN THE KILLING BULLET WAS FIRED  (n=${alive.length} alive of ${withFire.length})`,
  )
  out.push(
    `    HP pool      mean ${fmt(hp.mean, 1)}   median ${fmt(hp.median, 1)}   range ${fmt(hp.min, 0)}..${fmt(hp.max, 0)}`,
  )
  out.push(
    `    hits it can still take (vs basic dmg 100):  mean ${fmt(hits.mean)}   median ${fmt(hits.median, 1)}   distribution ${histogram(
      alive.map((l) => l.fire.hitsRemaining),
      4,
    )}`,
  )
  const oneHit = alive.filter((l) => l.fire.hitsRemaining <= 1).length
  out.push(
    `    one hit from death (hits<=1): ${oneHit}/${alive.length} (${fmt((oneHit / Math.max(1, alive.length)) * 100, 1)}%)`,
  )
  const deadAtFire = withFire.length - alive.length
  out.push(
    `    player dead/respawning at fire tick: ${deadAtFire} (${fmt((deadAtFire / withFire.length) * 100, 1)}%)`,
  )
  const flight = summarize(withFire.map((l) => l.fire.flightTicks))
  out.push(
    `    muzzle→base flight time: mean ${fmt(flight.mean, 1)} ticks (${fmt(flight.mean / 60, 2)}s), median ${fmt(flight.median, 0)}, max ${fmt(flight.max, 0)}`,
  )

  // ---- metric 5 ----
  const thAll = summarize(alive.map((l) => l.fire.threats.all))
  const thLethal = summarize(alive.map((l) => l.fire.threats.lethal))
  out.push(`\n[5] ENEMY BULLETS THREATENING THE PLAYER AT THE FIRE TICK  (n=${alive.length})`)
  out.push(
    `    aligned + closing        mean ${fmt(thAll.mean)}   median ${fmt(thAll.median, 1)}   max ${fmt(thAll.max, 0)}`,
  )
  out.push(
    `      distribution: ${histogram(
      alive.map((l) => l.fire.threats.all),
      4,
    )}`,
  )
  out.push(
    `    of which LETHAL          mean ${fmt(thLethal.mean)}   median ${fmt(thLethal.median, 1)}   max ${fmt(thLethal.max, 0)}`,
  )
  const zeroThreat = alive.filter((l) => l.fire.threats.all === 0).length
  out.push(
    `    ZERO bullets threatening the player: ${zeroThreat}/${alive.length} (${fmt((zeroThreat / Math.max(1, alive.length)) * 100, 1)}%)`,
  )
  const killAlsoThreat = alive.filter((l) => l.fire.killBulletThreatensPlayer).length
  out.push(
    `    the base-killing bullet itself was also aimed through the player: ${killAlsoThreat} (${fmt((killAlsoThreat / Math.max(1, alive.length)) * 100, 1)}%)`,
  )

  // ---- metric 6 ----
  const eta = summarize(alive.map((l) => l.fire.threats.eta60))
  const etaLethal = summarize(alive.map((l) => l.fire.threats.eta60Lethal))
  const obs = summarize(alive.map((l) => l.fire.observedDistinct))
  const obsLethal = summarize(alive.map((l) => l.fire.observedDistinctLethal))
  out.push(
    `\n[6] ENEMY BULLETS ABLE TO HIT THE PLAYER WITHIN 1 s OF THAT SHOT  (n=${alive.length})`,
  )
  out.push(`    (a) predictive — projected at the fire tick, ETA <= 60 ticks`)
  out.push(
    `        any     mean ${fmt(eta.mean)}   median ${fmt(eta.median, 1)}   max ${fmt(eta.max, 0)}   distribution ${histogram(
      alive.map((l) => l.fire.threats.eta60),
      4,
    )}`,
  )
  out.push(
    `        lethal  mean ${fmt(etaLethal.mean)}   median ${fmt(etaLethal.median, 1)}   max ${fmt(etaLethal.max, 0)}   distribution ${histogram(
      alive.map((l) => l.fire.threats.eta60Lethal),
      4,
    )}`,
  )
  out.push(
    `    (b) observed — distinct bullets that actually threatened the player during [F, F+60]`,
  )
  out.push(
    `        any     mean ${fmt(obs.mean)}   median ${fmt(obs.median, 1)}   max ${fmt(obs.max, 0)}   distribution ${histogram(
      alive.map((l) => l.fire.observedDistinct),
      4,
    )}`,
  )
  out.push(
    `        lethal  mean ${fmt(obsLethal.mean)}   median ${fmt(obsLethal.median, 1)}   max ${fmt(obsLethal.max, 0)}`,
  )
  const truncated = alive.filter((l) => l.fire.observedTicks < 60).length
  out.push(
    `        window truncated by run end: ${truncated}/${alive.length} (mean observed ${fmt(mean(alive.map((l) => l.fire.observedTicks)), 1)} of 60 ticks)`,
  )
  const diedInWindow = alive.filter((l) => l.fire.playerDiedInWindow).length
  out.push(
    `        player actually died inside the window: ${diedInWindow} (${fmt((diedInWindow / Math.max(1, alive.length)) * 100, 1)}%)`,
  )

  // ---- §116 trigger-eligibility cross-check ----
  out.push(`\n[*] §116 SUICIDE-RETURN ELIGIBILITY AT THE DECISIVE MOMENT`)
  const c3 = alive.filter((l) => l.fire.lives >= 2)
  const c5 = alive.filter((l) => l.fire.threats.eta60Lethal > 0)
  const c35 = alive.filter((l) => l.fire.lives >= 2 && l.fire.threats.eta60Lethal > 0)
  const c35far = c35.filter((l) => l.fire.distToBase > 6)
  out.push(
    `    cond3 lives>=2                       ${c3.length}/${alive.length} (${fmt((c3.length / Math.max(1, alive.length)) * 100, 1)}%)`,
  )
  out.push(
    `    cond5 lethal bullet inbound <=1s     ${c5.length}/${alive.length} (${fmt((c5.length / Math.max(1, alive.length)) * 100, 1)}%)`,
  )
  out.push(
    `    cond3 AND cond5                      ${c35.length}/${alive.length} (${fmt((c35.length / Math.max(1, alive.length)) * 100, 1)}%)`,
  )
  out.push(
    `    ... and player >6 cells from base    ${c35far.length}/${alive.length} (${fmt((c35far.length / Math.max(1, alive.length)) * 100, 1)}%)`,
  )
  out.push(
    `    → upper bound on base losses §116 could even in principle contest: ${fmt((c35.length / losses.length) * 100, 1)}% of losses` +
      ` = ${fmt((c35.length / total) * 100, 2)}% of all runs`,
  )

  // ---- killer mix + worst stages ----
  const byKiller = new Map<string, number>()
  for (const l of losses) byKiller.set(l.killerKind, (byKiller.get(l.killerKind) ?? 0) + 1)
  out.push(
    `\n[*] KILLER MIX: ` +
      [...byKiller.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => `${k} ${v} (${fmt((v / losses.length) * 100, 1)}%)`)
        .join('  '),
  )
  const byStage = new Map<number, number>()
  for (const l of losses) byStage.set(l.stageIndex, (byStage.get(l.stageIndex) ?? 0) + 1)
  const worst = [...byStage.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  out.push(
    `[*] WORST STAGES (base losses / ${seedCount} seeds): ` +
      worst.map(([si, n]) => `S${si + 1}:${n}`).join('  '),
  )

  return out.join('\n')
}

// ============================================================
// Main
// ============================================================

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def
}

async function main(): Promise<void> {
  const seedCount = Number(arg('seeds', '120'))
  const seeds = Array.from({ length: seedCount }, (_, i) => i + 1)
  const difficulties = (arg('difficulty', 'hard,chaos') as string).split(',').map((s) => s.trim())
  const stageIdxs = arg('stages')
    ? arg('stages')!
        .split(',')
        .map((s) => Number(s.trim()) - 1)
        .filter((i) => i >= 0 && i < STAGES.length)
    : STAGES.map((_, i) => i)
  const maxTicks = Number(arg('max-ticks', '36000'))
  const jsonPath = arg('json')

  const pool = new ForensicPool()
  const totalRuns = stageIdxs.length * seeds.length * difficulties.length
  console.log(
    `base-loss forensics · ${difficulties.join('+')} · ${stageIdxs.length} stages × ${seeds.length} seeds` +
      ` = ${totalRuns} runs · ${pool.size} workers`,
  )

  const t0 = performance.now()
  const byDifficulty = new Map<string, RunResult[]>()
  let globalDone = 0

  for (const difficulty of difficulties) {
    const tasks: ForensicTask[] = []
    let id = 0
    for (const si of stageIdxs) {
      for (const seed of seeds) {
        tasks.push({ id: id++, seed, stage: STAGES[si], stageIndex: si, difficulty, maxTicks })
      }
    }
    let lastPct = -1
    const results = await pool.runBatch(tasks, (done) => {
      const pct = Math.floor(((globalDone + done) / totalRuns) * 100)
      if (pct !== lastPct && pct % 5 === 0) {
        lastPct = pct
        const el = (performance.now() - t0) / 1000
        process.stderr.write(
          `\r  ${pct}%  (${globalDone + done}/${totalRuns})  ${el.toFixed(0)}s   `,
        )
      }
    })
    globalDone += tasks.length
    const failed = results.filter((r) => !r.ok)
    if (failed.length)
      console.error(`\n  WARNING: ${failed.length} runs errored: ${failed[0].error}`)
    byDifficulty.set(
      difficulty,
      results.filter((r) => r.ok && r.result).map((r) => r.result!),
    )
  }
  pool.terminate()
  process.stderr.write('\r' + ' '.repeat(60) + '\r')

  const elapsed = (performance.now() - t0) / 1000
  const report: string[] = []
  for (const d of difficulties) {
    report.push(reportDifficulty(d, byDifficulty.get(d) ?? [], seeds.length, stageIdxs.length))
  }
  report.push(`\n${'='.repeat(78)}`)
  report.push(`${totalRuns} runs in ${elapsed.toFixed(1)}s`)
  console.log(report.join('\n'))

  if (jsonPath) {
    const payload = {
      generatedAt: new Date().toISOString(),
      seeds: seeds.length,
      stages: stageIdxs.length,
      difficulties,
      runs: Object.fromEntries(
        [...byDifficulty.entries()].map(([d, rs]) => [
          d,
          {
            total: rs.length,
            outcomes: {
              stage_clear: rs.filter((r) => r.outcome === 'stage_clear').length,
              base_destroyed: rs.filter((r) => r.outcome === 'base_destroyed').length,
              lives_exhausted: rs.filter((r) => r.outcome === 'lives_exhausted').length,
              timeout: rs.filter((r) => r.outcome === 'timeout').length,
            },
            losses: rs.filter((r) => r.loss).map((r) => r.loss),
          },
        ]),
      ),
    }
    await Bun.write(jsonPath, JSON.stringify(payload, null, 2))
    console.log(`\nJSON → ${jsonPath}`)
  }
}

main()
