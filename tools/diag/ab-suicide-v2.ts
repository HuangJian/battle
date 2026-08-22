#!/usr/bin/env bun
/**
 * ab-suicide-v2.ts — A/B for the §117/§118 suicide-return variants.
 *
 * §116 (mode 1) triggered the trade on condition ⑤ (a LETHAL bullet hitting
 * the player within 1s). §117 moves the trigger to condition ① (an enemy at a
 * threat point, with a bullet actively flying at the base):
 *
 *   mode 2 — STAND: the player stands still (with a timeout) waiting to die.
 *   mode 3 — CHARGE: the player actively drives at the threat enemy (no
 *            dodging) to die fast and respawn near the base, or kill it first.
 *
 * §118 adds the strict-doom guard (A/B knobs, default OFF): modes 2/3 only
 * commit when the base is genuinely about to fall — baseHp at/below
 * `--base-hp-frac` × baseMaxHp AND the player is farther than `--defend-dist`
 * cells from the base (out of position, cannot return in time). The §117 flip
 * losses committed while the base was full HP with a working defense; the
 * strict guard is the fix attempt.
 *
 * Arms (all other params = DEFAULT_GOD_AI_PARAMS):
 *   A = baseline              (suicideReturnMode = 0)
 *   B = STAND variant         (suicideReturnMode = 2)
 *   C = CHARGE variant        (suicideReturnMode = 3)
 *   D = STAND + strict guard  (mode 2 + suicideReturnBaseHpFrac/DefendDistCells)
 *   E = CHARGE + strict guard (mode 3 + strict)            [--strict only]
 *
 * Reports, per difficulty:
 *   - per-arm outcome mix (stage_clear / base_destroyed / lives_exhausted /
 *     timeout) — the base-defense question is directly Δbase_destroyed;
 *   - per-stage per-arm-vs-A flip counts + seed lists (win = stage_clear);
 *   - suite net flips and Δbase_destroyed for each variant;
 *   - total suicideReturn commit ticks per arm (trigger-rate proxy — 0 means
 *     the variant never fired and a tied result is vacuous).
 *
 * Usage:
 *   bun tools/diag/ab-suicide-v2.ts --difficulty hard,chaos --seeds 120
 *   bun tools/diag/ab-suicide-v2.ts --seeds 60 --mode 3 --difficulty hard
 *   bun tools/diag/ab-suicide-v2.ts --seeds 120 --strict --base-hp-frac 0.5 \
 *       --defend-dist 8 --json out.json
 *   bun tools/diag/ab-suicide-v2.ts --seeds 20 --stages 3,24,34 --json out.json
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask, SimTaskResult } from '../sim/sim-worker'
import { arg, parseSeeds, parseStages } from '../lib/cli'

const difficulties = (arg('difficulty') ?? 'hard,chaos')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
const seeds = parseSeeds(arg('seeds'))
const stageIdxs = parseStages(arg('stages'))
const modes = (arg('mode') ?? '2,3')
  .split(',')
  .map((n) => Number(n.trim()))
  .filter((m) => m === 2 || m === 3)
const strict = process.argv.includes('--strict')
const baseHpFrac = Number(arg('base-hp-frac') ?? '0.5')
const defendDist = Number(arg('defend-dist') ?? '8')
if (strict && (baseHpFrac <= 0 || baseHpFrac > 1 || defendDist <= 0)) {
  console.error(
    `ab-suicide-v2: --strict requires 0 < --base-hp-frac <= 1 and --defend-dist > 0` +
      ` (got baseHpFrac=${baseHpFrac}, defendDist=${defendDist}) — otherwise arms D/E` +
      ` would be inert duplicates of B/C.`,
  )
  process.exit(1)
}
const jsonPath = arg('json')
// 36000 ticks (10 min) matches the §116 forensics corpus 口径 (hard/chaos
// games often exceed 18000 = 5 min — at 18000, ~40% of hard/chaos runs cap
// and base_destroyed is undercounted). Override with --max-ticks.
const maxTicks = Number(arg('max-ticks') ?? '36000')

interface ArmDef {
  id: 'A' | 'B' | 'C' | 'D' | 'E'
  mode: number
  strict: boolean
  label: string
}

const armDefs: ArmDef[] = [{ id: 'A', mode: 0, strict: false, label: 'A' }]
if (modes.includes(2)) armDefs.push({ id: 'B', mode: 2, strict: false, label: 'B(mode2)' })
if (modes.includes(3)) armDefs.push({ id: 'C', mode: 3, strict: false, label: 'C(mode3)' })
if (strict && modes.includes(2))
  armDefs.push({ id: 'D', mode: 2, strict: true, label: 'D(mode2+strict)' })
if (strict && modes.includes(3))
  armDefs.push({ id: 'E', mode: 3, strict: true, label: 'E(mode3+strict)' })

interface ArmMeta {
  difficulty: string
  stageIdx: number
  seed: number
  arm: ArmDef['id']
  mode: number
  strict: boolean
}

function armParams(def: ArmDef): Partial<typeof DEFAULT_GOD_AI_PARAMS> {
  const params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = { suicideReturnMode: def.mode }
  if (def.strict) {
    params.suicideReturnBaseHpFrac = baseHpFrac
    params.suicideReturnDefendDistCells = defendDist
  }
  return params
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
            params: { ...DEFAULT_GOD_AI_PARAMS, ...armParams(def) },
            maxTicks,
            commitCounts: true,
          })
          meta.push({ difficulty, stageIdx, seed, arm: def.id, mode: def.mode, strict: def.strict })
        }
      }
    }
  }

  process.stderr.write(
    `ab-suicide-v2: ${difficulties.join('+')} × ${stageIdxs.length} stages × ${seeds.length} seeds` +
      ` × arms[${armDefs.map((d) => d.id).join('')}] = ${tasks.length} sims (${pool.size} workers)` +
      (strict ? ` — strict(baseHpFrac=${baseHpFrac}, defendDist=${defendDist})` : '') +
      '\n',
  )
  const t0 = Date.now()
  const results = await pool.runBatch(tasks)
  pool.terminate()
  process.stderr.write(
    `ab-suicide-v2: ran ${results.length} sims in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
  )
  report(results, meta)
}

interface ArmAgg {
  clear: number
  baseDestroyed: number
  livesExhausted: number
  timeout: number
  errors: number
  commits: number
  /** Runs where the suicide trade fired at least once (trigger rate). */
  commitRuns: number
}

function freshAgg(): ArmAgg {
  return {
    clear: 0,
    baseDestroyed: 0,
    livesExhausted: 0,
    timeout: 0,
    errors: 0,
    commits: 0,
    commitRuns: 0,
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

  // Per (difficulty, stage): flip seed lists per variant arm (win = stage_clear).
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
    agg.commits += r.suicideReturnCommits ?? 0
    if ((r.suicideReturnCommits ?? 0) > 0) agg.commitRuns++
    // sim-worker reports the raw SimOutcome ('stage_clear' | 'gameover' |
    // 'max_ticks'); base-vs-lives is recovered via `baseAlive`.
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

  // Flip classification — A is always present; find it per (diff, stage, seed).
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
          `  timeout ${a.timeout}  commits ${a.commits} (${a.commitRuns} runs fired)${a.errors ? `  ERRORS ${a.errors}` : ''}`,
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
          strict,
          baseHpFrac,
          defendDist,
          tasks: results.map((r, i) => ({
            ...metaList[i],
            ok: r.ok,
            outcome: r.outcome,
            baseAlive: r.baseAlive,
            suicideReturnCommits: r.suicideReturnCommits ?? 0,
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
