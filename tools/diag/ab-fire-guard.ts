#!/usr/bin/env bun
/**
 * ab-fire-guard.ts — A/B for the §121 t2a/aggressive self-fire base guard.
 *
 * §120 forensics (32 self-kill runs across hard/chaos 120-seed, t2a 81%):
 * the scan's two ±8px offset lines catch an enemy up to ~25px off the
 * bullet's 6px center path and report scan.enemy CLOSER than the base
 * eagle — the §74 dual-offset guard then allows fire, but the bullet misses
 * the off-line enemy and continues into the base (hard S6 s43: killer shot
 * x=200, enemy body x∈[206,238], bullet [197,203] passed beside it).
 *
 * The guard (selfFireBaseGuard) walks the bullet's actual CENTER line to the
 * base and suppresses the stop-and-aim fire / shouldFireInDir fire when it
 * would reach the eagle (ring brick/steel stops; tanks deliberately ignored —
 * they can dodge away before the bullet arrives):
 *
 *   A = baseline (selfFireBaseGuard = 0)
 *   B = strict   (selfFireBaseGuard = 1) — tanks ignored, suppress whenever
 *       the terrain line reaches the base
 *   C = lenient  (selfFireBaseGuard = 2) — only suppress when NO enemy body
 *       overlaps the 6px corridor (keeps point-blank overlap kills)
 *
 * Reports, per difficulty:
 *   - per-arm outcome mix (stage_clear / base_destroyed / lives_exhausted /
 *     timeout) — the base-defense question is directly Δbase_destroyed;
 *   - per-stage per-arm-vs-A flip counts + seed lists (win = stage_clear);
 *   - suite net flips and Δbase_destroyed for each variant;
 *   - total selfFireGuardBlocks per arm (trigger-rate proxy — 0 means the
 *     arm never diverged and a tied result is vacuous).
 *
 * Usage:
 *   bun tools/diag/ab-fire-guard.ts --difficulty hard,chaos --seeds 120
 *   bun tools/diag/ab-fire-guard.ts --seeds 20 --stages 3,24,34 --json out.json
 *   bun tools/diag/ab-fire-guard.ts --seeds 120 --mode 1 --difficulty hard
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask, SimTaskResult } from '../sim/sim-worker'
import { arg, parseSeeds, parseStages } from '../lib/cli'

const seeds = parseSeeds(arg('seeds'))
const stageIdxs = parseStages(arg('stages'))

const difficulties = (arg('difficulty') ?? 'hard,chaos')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
const modes = (arg('mode') ?? '1,2')
  .split(',')
  .map((n) => Number(n.trim()))
  .filter((m) => m === 1 || m === 2)
const jsonPath = arg('json')
// 36000 ticks (10 min) — matches the §116/§119 corpus 口径.
const maxTicks = Number(arg('max-ticks') ?? '36000')

interface ArmDef {
  id: 'A' | 'B' | 'C'
  guard: number
  label: string
}

const armDefs: ArmDef[] = [{ id: 'A', guard: 0, label: 'A' }]
if (modes.includes(1)) armDefs.push({ id: 'B', guard: 1, label: 'B(strict1)' })
if (modes.includes(2)) armDefs.push({ id: 'C', guard: 2, label: 'C(lenient2)' })

interface ArmMeta {
  difficulty: string
  stageIdx: number
  seed: number
  arm: ArmDef['id']
  guard: number
}

async function main(): Promise<void> {
  const pool = new SimWorkerPool()
  const tasks: SimTask[] = []
  const meta: ArmMeta[] = []

  for (const difficulty of difficulties) {
    for (const stageIdx of stageIdxs) {
      for (const seed of seeds) {
        for (const def of armDefs) {
          tasks.push({
            id: tasks.length,
            seed,
            stage: STAGES[stageIdx],
            difficulty,
            params: {
              ...DEFAULT_GOD_AI_PARAMS,
              selfFireBaseGuard: def.guard,
            },
            maxTicks,
            commitCounts: true,
          })
          meta.push({ difficulty, stageIdx, seed, arm: def.id, guard: def.guard })
        }
      }
    }
  }

  process.stderr.write(
    `ab-fire-guard: ${difficulties.join('+')} × ${stageIdxs.length} stages × ${seeds.length} seeds` +
      ` × arms[${armDefs.map((d) => d.id).join('')}] = ${tasks.length} sims (${pool.size} workers)\n`,
  )
  const t0 = Date.now()
  const results = await pool.runBatch(tasks)
  pool.terminate()
  process.stderr.write(
    `ab-fire-guard: ran ${results.length} sims in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
  )
  report(results, meta)
}

interface ArmAgg {
  clear: number
  baseDestroyed: number
  livesExhausted: number
  timeout: number
  errors: number
  guardBlocks: number
  /** Runs where the guard blocked a base-line shot at least once. */
  guardBlockRuns: number
}

function freshAgg(): ArmAgg {
  return {
    clear: 0,
    baseDestroyed: 0,
    livesExhausted: 0,
    timeout: 0,
    errors: 0,
    guardBlocks: 0,
    guardBlockRuns: 0,
  }
}

function report(results: SimTaskResult[], metaList: ArmMeta[]): void {
  const armIds = armDefs.map((d) => d.id)
  // results[i] corresponds to meta[i] (pool re-orders by task id = push order).
  const byDiff = new Map<string, Record<string, ArmAgg>>()
  for (const d of difficulties) {
    const m: Record<string, ArmAgg> = {}
    for (const id of armIds) m[id] = freshAgg()
    byDiff.set(d, m)
  }

  const flips = new Map<
    string,
    Map<number, Record<string, { toWin: number[]; toLose: number[] }>>
  >()

  const key = (d: string, si: number) => `${d}|${si}`

  for (let i = 0; i < results.length; i++) {
    const r = results[i]
    const m = metaList[i]
    const agg = byDiff.get(m.difficulty)![m.arm]
    if (!r.ok) {
      agg.errors++
      continue
    }
    agg.guardBlocks += r.selfFireGuardBlocks ?? 0
    if ((r.selfFireGuardBlocks ?? 0) > 0) agg.guardBlockRuns++
    switch (r.outcome) {
      case 'stage_clear':
        agg.clear++
        break
      case 'gameover':
        if (r.baseAlive) agg.livesExhausted++
        else agg.baseDestroyed++
        break
      default:
        agg.timeout++
    }
  }

  const aByKey = new Map<string, boolean>()
  for (let i = 0; i < results.length; i++) {
    const m = metaList[i]
    if (m.arm !== 'A') continue
    const r = results[i]
    aByKey.set(`${key(m.difficulty, m.stageIdx)}|${m.seed}`, r.ok && r.outcome === 'stage_clear')
  }
  for (let i = 0; i < results.length; i++) {
    const m = metaList[i]
    if (m.arm === 'A') continue
    const r = results[i]
    if (!r.ok) continue
    let byStage = flips.get(key(m.difficulty, m.stageIdx))
    if (!byStage) {
      byStage = new Map()
      flips.set(key(m.difficulty, m.stageIdx), byStage)
    }
    let f = byStage.get(m.stageIdx)
    if (!f) {
      f = {}
      for (const id of armIds) if (id !== 'A') f[id] = { toWin: [], toLose: [] }
      byStage.set(m.stageIdx, f)
    }
    const aWin = aByKey.get(`${key(m.difficulty, m.stageIdx)}|${m.seed}`) ?? false
    const armWin = r.outcome === 'stage_clear'
    const entry = f[m.arm]
    if (aWin === armWin) continue
    if (armWin && !aWin) entry.toWin.push(m.seed)
    else entry.toLose.push(m.seed)
  }

  const out: string[] = []
  for (const d of difficulties) {
    const agg = byDiff.get(d)!
    const total =
      agg.A.clear + agg.A.baseDestroyed + agg.A.livesExhausted + agg.A.timeout + agg.A.errors
    out.push('')
    out.push('='.repeat(78))
    out.push(
      `DIFFICULTY: ${d}   (${stageIdxs.length} stages × ${seeds.length} seeds = ${total} runs/arm)`,
    )
    out.push('='.repeat(78))

    const row = (name: string, a: ArmAgg): void => {
      const n = a.clear + a.baseDestroyed + a.livesExhausted + a.timeout + a.errors
      out.push(
        `  ${name.padEnd(14)} clear ${String(a.clear).padStart(5)} (${((a.clear / Math.max(1, n)) * 100).toFixed(1)}%)` +
          `  base_destroyed ${String(a.baseDestroyed).padStart(5)} (${((a.baseDestroyed / Math.max(1, n)) * 100).toFixed(1)}%)` +
          `  lives_exhausted ${String(a.livesExhausted).padStart(4)} (${((a.livesExhausted / Math.max(1, n)) * 100).toFixed(1)}%)` +
          `  timeout ${a.timeout}  guardBlocks ${a.guardBlocks} (${a.guardBlockRuns} runs fired)${a.errors ? `  ERRORS ${a.errors}` : ''}`,
      )
    }
    out.push('  OUTCOME MIX (win = stage_clear; base defense = lower base_destroyed)')
    for (const def of armDefs) {
      row(def.label, agg[def.id])
      if (def.id !== 'A') {
        const a = agg[def.id]
        out.push(
          `    Δ vs A: clear ${a.clear - agg.A.clear}  base_destroyed ${a.baseDestroyed - agg.A.baseDestroyed}`,
        )
      }
    }

    const net: Record<string, number> = {}
    const toWinTotal: Record<string, number> = {}
    const toLoseTotal: Record<string, number> = {}
    for (const id of armIds) {
      if (id !== 'A') {
        net[id] = 0
        toWinTotal[id] = 0
        toLoseTotal[id] = 0
      }
    }
    for (const si of stageIdxs) {
      const byStage = flips.get(key(d, si))
      if (!byStage) continue
      const f = byStage.get(si)
      if (!f) continue
      for (const def of armDefs) {
        if (def.id === 'A') continue
        const entry = f[def.id]
        net[def.id] += entry.toWin.length - entry.toLose.length
        toWinTotal[def.id] += entry.toWin.length
        toLoseTotal[def.id] += entry.toLose.length
        out.push(
          `  S${si + 1} ${STAGES[si].name.padEnd(16)} ${def.id}: win ${entry.toWin.length} lose ${entry.toLose.length}`,
        )
        if (entry.toWin.length)
          out.push(`      FLIP-TO-WIN  ${def.id} seeds: ${entry.toWin.join(', ')}`)
        if (entry.toLose.length)
          out.push(`      FLIP-TO-LOSE ${def.id} seeds: ${entry.toLose.join(', ')}`)
      }
    }
    for (const def of armDefs) {
      if (def.id === 'A') continue
      out.push(
        `  [${d}] ${def.label} SUITE: net ${net[def.id] >= 0 ? '+' : ''}${net[def.id]} flips` +
          ` (to-win ${toWinTotal[def.id]}, to-lose ${toLoseTotal[def.id]})`,
      )
    }
  }
  out.push('')
  out.push(
    'Next: per-seed-diff dump/diff on FLIP-TO-LOSE seeds, then decision-probe at the first divergence tick.',
  )
  console.log(out.join('\n'))

  if (jsonPath) {
    Bun.write(
      jsonPath,
      JSON.stringify(
        {
          generatedAt: new Date().toISOString(),
          difficulties,
          stageIdxs,
          seeds,
          modes,
          tasks: results.map((r, i) => ({
            ...metaList[i],
            ok: r.ok,
            outcome: r.outcome,
            baseAlive: r.baseAlive,
            selfFireGuardBlocks: r.selfFireGuardBlocks ?? 0,
          })),
        },
        null,
        2,
      ),
    ).then(() => console.log(`JSON → ${jsonPath}`))
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
