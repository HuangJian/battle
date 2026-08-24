#!/usr/bin/env bun
/**
 * run-forensics.ts — per-run structured autopsy for multi-seed sweeps
 * (DECISIONS §119: 固化策略调试方法论 — beyond win rate, record the details).
 *
 * Every run is executed with `forensics: true`, so each returns a terminal
 * snapshot (player / base / every live enemy / every in-flight enemy bullet),
 * a last-10-ticks action+rule log, and the full death/kill/pickup history.
 * The tool aggregates those across 20/60/120-seed sweeps and prints, per
 * difficulty:
 *
 *   [0] outcome mix — stage_clear / base_destroyed / lives_exhausted /
 *       timeout (+ % of failures), self-inflicted base kills
 *   [1] base-destroyed context — player lives / dist-to-base / HP
 *       (hits it can still take) at the loss instant, enemies + bullets
 *       on the field
 *   [2] lives-exhausted context — player lives / dist-to-base / base HP
 *       (hits it can still take) at the loss instant
 *   [3] history — death/kill/pickup moments + positions (tick percentiles,
 *       cell hotspots), inventory totals, final star level
 *   [4] failure taxonomy — killer mix, worst stages, and for failed runs the
 *       rule distribution of the final 10 action ticks
 *
 * Usage:
 *   bun tools/diag/run-forensics.ts --seeds 120 --difficulty hard,chaos
 *   bun tools/diag/run-forensics.ts --seeds 20 --stages 3,9,17 --trace-wins
 *   bun tools/diag/run-forensics.ts --seeds 120 --set suicideReturnMode=2 \
 *       --json tmp/fx.json
 *
 * Iterative-debugging mode (DECISIONS §120): when a script change invalidates
 * previously collected forensics (e.g. the §120 shot-event off-by-one fix), do
 * NOT re-run the full corpus — re-run only the previously identified failures:
 *
 *   bun tools/diag/run-forensics.ts --from-json tmp/fx-120.json \
 *       --kinds base_destroyed --selfkill --json tmp/fx-selfkill.json
 *
 * `--from-json` takes the (difficulty, stage, seed) combos of the old corpus's
 * failed runs (optionally filtered by `--kinds` failure causes and/or
 * `--selfkill` = only player self-inflicted base kills) and runs exactly those.
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { CELL, BASE_POS } from '../../src/constants'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask } from '../sim/sim-worker'
import type { RunForensics } from '../sim/simulation-runner'
import { parseStageSpec, StageSpecError, runHeader } from '../lib/stage-spec'

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def
}

function parseSeeds(spec: string | undefined): number[] {
  // "1-60" range, "60" count (seeds 1..N), or "1,3,5" list.
  if (!spec) return Array.from({ length: 120 }, (_, i) => i + 1)
  const s = spec.trim()
  if (/^\d+-\d+$/.test(s)) {
    const [lo, hi] = s.split('-').map(Number)
    return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i)
  }
  if (/^\d+$/.test(s)) {
    const n = Number(s)
    return Array.from({ length: n }, (_, i) => i + 1)
  }
  return s.split(',').map(Number)
}

const difficulties = (arg('difficulty') ?? 'hard,chaos')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
const seeds = parseSeeds(arg('seeds'))
const stageSpec = arg('stages') ?? 'all'
let stageIdxs: number[]
try {
  stageIdxs = parseStageSpec(stageSpec, STAGES.length)
} catch (e) {
  console.error(
    e instanceof StageSpecError ? e.message : `run-forensics: invalid --stages: ${stageSpec}`,
  )
  process.exit(1)
}
const maxTicks = Number(arg('max-ticks') ?? '36000')
const jsonPath = arg('json')
const traceWins = process.argv.includes('--trace-wins')
// Iterative-debugging subset mode (DECISIONS §120): re-run only the failure
// combos of an earlier corpus instead of the full stage×seed sweep.
const fromJson = arg('from-json')
const kinds = (arg('kinds') ?? 'base_destroyed,lives_exhausted,timeout')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
const selfkillOnly = process.argv.includes('--selfkill')
let corpusLabel = `${stageIdxs.length} stages × ${seeds.length} seeds`
if (fromJson)
  // §120: the report header must show WHICH subset was re-run — path + the
  // active failure-kind / selfkill filters — so a subset corpus is never
  // mistaken for a full sweep when comparing numbers across runs.
  corpusLabel =
    `subset of ${fromJson.split(/[\\/]/).pop()}` +
    ` (kinds=${kinds.join(',')}${selfkillOnly ? ', selfkill' : ''})`

// Param override for A/B forensics: "--set suicideReturnMode=2,suicideReturnBaseHpFrac=0.5"
const paramOverrides: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}
const setSpec = arg('set')
if (setSpec) {
  for (const pair of setSpec.split(',')) {
    const eq = pair.indexOf('=')
    if (eq <= 0) {
      console.error(`run-forensics: bad --set pair "${pair}" (want key=value)`)
      process.exit(1)
    }
    const k = pair.slice(0, eq)
    const raw = pair.slice(eq + 1)
    const num = Number(raw)
    ;(paramOverrides as Record<string, unknown>)[k] = Number.isFinite(num) ? num : raw
  }
}

interface FxRun {
  difficulty: string
  stageIdx: number
  seed: number
  outcome: string
  failureCause?: string
  failureKillerKind?: string
  ticks: number
  forensics: RunForensics
}

async function main(): Promise<void> {
  const pool = new SimWorkerPool()
  console.error(
    runHeader({
      difficulty: difficulties.join(','),
      stageCount: stageIdxs.length,
      seedCount: seeds.length,
      stageIndex: 0,
      maxTicks,
      params: { ...DEFAULT_GOD_AI_PARAMS, ...paramOverrides },
    }),
  )
  const tasks: SimTask[] = []
  const meta: Array<{ difficulty: string; stageIdx: number; seed: number }> = []
  if (fromJson) {
    // Subset mode: derive (difficulty, stage, seed) from the prior corpus's
    // failed runs. Deterministic sims ⇒ re-running the same combos reproduces
    // the same failures; the point is to re-collect with UPDATED forensics.
    // Malformed-input guard: a bad --from-json path or a non-corpus file must
    // fail with a clear message instead of a cryptic stack trace.
    let prev: {
      perDifficulty: Record<
        string,
        {
          failures?: Array<{
            stageIdx: number
            seed: number
            failureCause?: string
            killerKind?: string
          }>
        }
      >
    }
    try {
      prev = JSON.parse(await Bun.file(fromJson).text())
    } catch (err) {
      console.error(
        `run-forensics: --from-json ${fromJson}: cannot read/parse corpus (${(err as Error).message})`,
      )
      process.exit(1)
    }
    if (!prev || typeof prev !== 'object' || typeof prev.perDifficulty !== 'object') {
      console.error(
        `run-forensics: --from-json ${fromJson}: not a run-forensics corpus (missing perDifficulty)`,
      )
      process.exit(1)
    }
    const kindSet = new Set(kinds)
    for (const d of Object.keys(prev.perDifficulty)) {
      const pd = prev.perDifficulty[d]
      for (const f of pd.failures ?? []) {
        if (f.failureCause && !kindSet.has(f.failureCause)) continue
        if (selfkillOnly && f.killerKind !== 'player') continue
        tasks.push({
          id: tasks.length,
          seed: f.seed,
          stage: STAGES[f.stageIdx],
          difficulty: d,
          params: { ...DEFAULT_GOD_AI_PARAMS, ...paramOverrides },
          maxTicks,
          forensics: true,
        })
        meta.push({ difficulty: d, stageIdx: f.stageIdx, seed: f.seed })
      }
    }
    if (tasks.length === 0) {
      console.error(
        `run-forensics: --from-json ${fromJson} yielded no runs` +
          ` (kinds=${kinds.join(',')}${selfkillOnly ? ', selfkill' : ''})`,
      )
      process.exit(1)
    }
  } else {
    for (const difficulty of difficulties) {
      for (const stageIdx of stageIdxs) {
        for (const seed of seeds) {
          tasks.push({
            id: tasks.length,
            seed,
            stage: STAGES[stageIdx],
            difficulty,
            params: { ...DEFAULT_GOD_AI_PARAMS, ...paramOverrides },
            maxTicks,
            forensics: true,
          })
          meta.push({ difficulty, stageIdx, seed })
        }
      }
    }
  }
  process.stderr.write(
    `run-forensics: ${fromJson ? `subset(${fromJson})` : difficulties.join('+') + ` × ${stageIdxs.length} stages × ${seeds.length} seeds`}` +
      ` = ${tasks.length} runs (${pool.size} workers, maxTicks=${maxTicks})` +
      (Object.keys(paramOverrides).length
        ? `, --set ${Object.entries(paramOverrides)
            .map(([k, v]) => `${k}=${v}`)
            .join(',')}`
        : '') +
      (fromJson ? `, kinds=${kinds.join(',')}${selfkillOnly ? ', selfkill' : ''}` : '') +
      '\n',
  )
  const t0 = Date.now()
  const results = await pool.runBatch(tasks)
  pool.terminate()
  process.stderr.write(
    `run-forensics: ran ${results.length} runs in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
  )

  const runs: FxRun[] = []
  let errors = 0
  for (let i = 0; i < results.length; i++) {
    const r = results[i]
    const m = meta[i]
    if (!r.ok) {
      errors++
      continue
    }
    if (!r.forensics) {
      errors++
      continue
    }
    runs.push({
      difficulty: m.difficulty,
      stageIdx: m.stageIdx,
      seed: m.seed,
      outcome: r.outcome,
      failureCause: r.failureCause,
      failureKillerKind: r.failureKillerKind,
      ticks: r.ticks,
      forensics: r.forensics,
    })
  }
  if (errors) console.error(`  WARNING: ${errors} runs missing forensics/errored`)

  const out: string[] = []
  const perDifficulty = new Map<string, FxRun[]>()
  // In subset mode the difficulty set comes from the meta, not the CLI default.
  const diffSet =
    fromJson && meta.length ? [...new Set(meta.map((m) => m.difficulty))] : difficulties
  for (const d of diffSet)
    perDifficulty.set(
      d,
      runs.filter((r) => r.difficulty === d),
    )
  for (const d of diffSet) out.push(reportDifficulty(d, perDifficulty.get(d) ?? [], corpusLabel))
  out.push(`\n${'='.repeat(78)}`)
  out.push(
    `${runs.length} runs in ${((Date.now() - t0) / 1000).toFixed(1)}s${errors ? ` (${errors} errors)` : ''}`,
  )
  console.log(out.join('\n'))

  if (jsonPath) {
    const payload = {
      generatedAt: new Date().toISOString(),
      difficulties: diffSet,
      stageIdxs: [...new Set(meta.map((m) => m.stageIdx))],
      seedCount: new Set(meta.map((m) => m.seed)).size,
      fromJson: fromJson ?? undefined,
      maxTicks,
      paramOverrides,
      perDifficulty: Object.fromEntries(
        [...perDifficulty.entries()].map(([d, rs]) => [
          d,
          {
            runs: rs.length,
            outcomes: {
              stage_clear: rs.filter((r) => r.outcome === 'stage_clear').length,
              base_destroyed: rs.filter((r) => r.failureCause === 'base_destroyed').length,
              lives_exhausted: rs.filter((r) => r.failureCause === 'lives_exhausted').length,
              timeout: rs.filter((r) => r.outcome === 'max_ticks').length,
            },
            // Full per-run forensics for every non-clear run (定位瓶颈);
            // clears only when --trace-wins.
            failures: rs
              .filter((r) => r.outcome !== 'stage_clear')
              .map((r) => ({
                stageIdx: r.stageIdx,
                seed: r.seed,
                outcome: r.outcome,
                failureCause: r.failureCause,
                killerKind: r.failureKillerKind,
                ticks: r.ticks,
                forensics: r.forensics,
              })),
            wins: traceWins
              ? rs
                  .filter((r) => r.outcome === 'stage_clear')
                  .map((r) => ({
                    stageIdx: r.stageIdx,
                    seed: r.seed,
                    ticks: r.ticks,
                    forensics: r.forensics,
                  }))
              : undefined,
          },
        ]),
      ),
    }
    await Bun.write(jsonPath, JSON.stringify(payload, null, 2))
    console.log(`JSON → ${jsonPath}`)
  }
}

// ============================================================
// Aggregation helpers
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
function summarize(xs: number[]): {
  n: number
  mean: number
  median: number
  p25: number
  p75: number
} {
  const s = [...xs].sort((a, b) => a - b)
  return {
    n: s.length,
    mean: mean(s),
    median: quantile(s, 0.5),
    p25: quantile(s, 0.25),
    p75: quantile(s, 0.75),
  }
}
function fmt(x: number, d = 2): string {
  return Number.isFinite(x) ? x.toFixed(d) : '—'
}
function pct(n: number, d: number): string {
  return fmt(d ? (n / d) * 100 : 0, 1)
}
function histogram(xs: number[], cap = 5): string {
  if (!xs.length) return '—'
  const counts = new Map<number, number>()
  for (const x of xs) {
    const k = Math.min(x, cap)
    counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  return [...counts.keys()]
    .sort((a, b) => a - b)
    .map(
      (k) =>
        `${k === cap && xs.some((x) => x > cap) ? `${k}+` : k}:${counts.get(k)} (${pct(counts.get(k)!, xs.length)})`,
    )
    .join('  ')
}
function tickPcts(runs: FxRun[], type: 'death' | 'kill' | 'pickup'): string {
  // Fraction-of-run percentiles (early/mid/late). Computed per run BEFORE
  // flattening — pairing a flattened event index with a run index would mix
  // events from different runs (a death tick can exceed a shorter run's ticks,
  // producing >100% fractions).
  const fracs: number[] = []
  for (const r of runs) {
    const ticks = r.forensics.events.filter((e) => e.type === type)
    for (const e of ticks) if (r.ticks > 0) fracs.push(e.tick / r.ticks)
  }
  fracs.sort((a, b) => a - b)
  if (!fracs.length) return '—'
  return `p25 ${fmt(quantile(fracs, 0.25) * 100, 0)}%  p50 ${fmt(quantile(fracs, 0.5) * 100, 0)}%  p75 ${fmt(quantile(fracs, 0.75) * 100, 0)}% of run`
}
function hotspots(items: Array<{ x: number; y: number }>, top = 5): string {
  const counts = new Map<string, number>()
  for (const it of items) {
    if (it.x < 0 || it.y < 0) continue
    const cell = `${Math.floor(it.x / 16)},${Math.floor(it.y / 16)}`
    counts.set(cell, (counts.get(cell) ?? 0) + 1)
  }
  if (!counts.size) return '—'
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, top)
    .map(([c, n]) => `(${c})×${n}`)
    .join('  ')
}

function reportDifficulty(difficulty: string, runs: FxRun[], corpusLabel: string): string {
  const out: string[] = []
  const total = runs.length
  if (total === 0) return out.join('\n')
  const clears = runs.filter((r) => r.outcome === 'stage_clear')
  const baseLosses = runs.filter((r) => r.failureCause === 'base_destroyed')
  const livesOut = runs.filter((r) => r.failureCause === 'lives_exhausted')
  const timeouts = runs.filter((r) => r.outcome === 'max_ticks')
  const failures = total - clears.length

  out.push('')
  out.push('='.repeat(78))
  out.push(`DIFFICULTY: ${difficulty}   (${corpusLabel} = ${total} runs)`)
  out.push('='.repeat(78))

  // ---- [0] outcome mix ----
  out.push('\n[0] OUTCOME MIX')
  out.push(
    `    stage_clear      ${clears.length.toString().padStart(5)}  (${fmt((clears.length / total) * 100, 1)}%)`,
  )
  out.push(
    `    base_destroyed   ${baseLosses.length.toString().padStart(5)}  (${fmt((baseLosses.length / total) * 100, 1)}% of runs, ${pct(baseLosses.length, failures)}% of failures)`,
  )
  out.push(
    `    lives_exhausted  ${livesOut.length.toString().padStart(5)}  (${fmt((livesOut.length / total) * 100, 1)}% of runs, ${pct(livesOut.length, failures)}% of failures)`,
  )
  out.push(
    `    timeout          ${timeouts.length.toString().padStart(5)}  (${fmt((timeouts.length / total) * 100, 1)}%)`,
  )
  const selfKills = baseLosses.filter((r) => r.failureKillerKind === 'player').length
  if (selfKills)
    out.push(
      `    SELF-INFLICTED base kills (player's own bullet): ${selfKills} (${fmt((selfKills / Math.max(1, baseLosses.length)) * 100, 1)}% of base losses)`,
    )

  // ---- [0.5] self-inflicted base-kill detail (DECISIONS §120) ----
  // For each run the player destroyed its own base: which decision-chain
  // branch fired the fatal bullet (last player shot of the run — the game ends
  // right after), from where, and how much protection ring was still up.
  const selfKillRuns = baseLosses.filter((r) => r.failureKillerKind === 'player')
  if (selfKillRuns.length) {
    out.push(`\n[0.5] SELF-INFLICTED BASE KILLS  (n=${selfKillRuns.length})`)
    const branchCounts = new Map<string, number>()
    const dists: number[] = []
    const bcx = BASE_POS.col * CELL + CELL
    const bcy = BASE_POS.row * CELL + CELL
    let aimedAtBase = 0
    const dirCounts = new Map<string, number>()
    for (const r of selfKillRuns) {
      const shots = r.forensics.events.filter((e) => e.type === 'shot')
      // The fatal bullet: the last shot aimed INTO the base zone (the game
      // ends right after the base dies, so the newest towardBase shot is the
      // killer); fall back to the last shot when none is aimed (the bullet may
      // have entered through a gap while the recorded dir was off-axis).
      let fatal = null
      for (let i = shots.length - 1; i >= 0; i--) {
        if (shots[i].towardBase) {
          fatal = shots[i]
          break
        }
      }
      fatal ??= shots[shots.length - 1] ?? null
      const branch = fatal?.detail ?? 'no-shot-recorded'
      branchCounts.set(branch, (branchCounts.get(branch) ?? 0) + 1)
      let dist = -1
      if (fatal && fatal.x >= 0 && fatal.y >= 0) {
        dist = Math.round((Math.abs(fatal.x - bcx) + Math.abs(fatal.y - bcy)) / CELL)
        dists.push(dist)
      }
      if (fatal?.dir) dirCounts.set(fatal.dir, (dirCounts.get(fatal.dir) ?? 0) + 1)
      if (fatal?.towardBase) aimedAtBase++
      out.push(
        `    S${String(r.stageIdx + 1).padStart(2)} seed ${String(r.seed).padStart(3)}: ${branch.padEnd(11)}` +
          ` fired@t${String(fatal?.tick ?? '?').padStart(5)}  facing ${(fatal?.dir ?? '?').padEnd(5)}` +
          `${fatal?.towardBase ? '→BASE' : '     '}  player ${dist >= 0 ? `${dist} cells` : '?'} from base` +
          `  wall intact ${r.forensics.terminal.baseWallIntact}`,
      )
    }
    if (dists.length) {
      const ds = [...dists].sort((a, b) => a - b)
      out.push(
        `    fatal-shot dist-to-base: mean ${fmt(mean(dists), 1)}  median ${fmt(quantile(ds, 0.5), 1)}  range ${Math.min(...dists)}..${Math.max(...dists)} cells`,
      )
    }
    out.push(
      `    fatal-shot aimed into base zone: ${aimedAtBase}/${selfKillRuns.length}  facing mix: ` +
        ([...dirCounts.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([d, n]) => `${d} ${n}`)
          .join('  ') || '—'),
    )
    out.push(
      `    fatal-shot branch mix: ` +
        ([...branchCounts.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([b, n]) => `${b} ${n}`)
          .join('  ') || '—'),
    )
  }

  // ---- [1] base-destroyed context ----
  if (baseLosses.length) {
    const livesS = summarize(baseLosses.map((r) => r.forensics.terminal.playerLives))
    const distS = summarize(
      baseLosses
        .filter((r) => r.forensics.terminal.playerDistToBase >= 0)
        .map((r) => r.forensics.terminal.playerDistToBase),
    )
    const hitsS = summarize(baseLosses.map((r) => r.forensics.terminal.playerHitsToDie))
    const enemyN = summarize(baseLosses.map((r) => r.forensics.terminal.enemies.length))
    const bulletN = summarize(baseLosses.map((r) => r.forensics.terminal.enemyBullets.length))
    const etaBullets = baseLosses.map(
      (r) => r.forensics.terminal.enemyBullets.filter((b) => b.etaToPlayer >= 0).length,
    )
    const oneHitBaseBullets = baseLosses.map(
      (r) => r.forensics.terminal.enemyBullets.filter((b) => b.hitsToDieBase <= 1).length,
    )
    out.push(`\n[1] BASE-DESTROYED CONTEXT  (n=${baseLosses.length})`)
    out.push(
      `    player lives      mean ${fmt(livesS.mean, 1)}  median ${fmt(livesS.median, 1)}  distr ${histogram(
        baseLosses.map((r) => r.forensics.terminal.playerLives),
        4,
      )}`,
    )
    const near6 = baseLosses.filter(
      (r) =>
        r.forensics.terminal.playerDistToBase >= 0 && r.forensics.terminal.playerDistToBase <= 6,
    ).length
    const far12 = baseLosses.filter((r) => r.forensics.terminal.playerDistToBase > 12).length
    out.push(
      `    dist to base      mean ${fmt(distS.mean, 1)}  median ${fmt(distS.median, 1)}  ≤6:${pct(near6, baseLosses.length)}%  >12:${pct(far12, baseLosses.length)}%`,
    )
    out.push(
      `    player hits-to-die (vs 100 dmg):  mean ${fmt(hitsS.mean, 1)}  median ${fmt(hitsS.median, 1)}  ≤1:${pct(baseLosses.filter((r) => r.forensics.terminal.playerHitsToDie <= 1).length, baseLosses.length)}%`,
    )
    out.push(
      `    live enemies       mean ${fmt(enemyN.mean, 1)}  median ${fmt(enemyN.median, 0)}  (per-run ${histogram(
        baseLosses.map((r) => r.forensics.terminal.enemies.length),
        4,
      )})`,
    )
    out.push(
      `    enemy bullets      mean ${fmt(bulletN.mean, 1)}  median ${fmt(bulletN.median, 0)}  aligned+closing on player mean ${fmt(mean(etaBullets), 2)}  would-kill-base-in-1-hit mean ${fmt(mean(oneHitBaseBullets), 2)}`,
    )
    const deadAtLoss = baseLosses.filter((r) => !r.forensics.terminal.playerAlive).length
    if (deadAtLoss)
      out.push(
        `    player already dead/respawning at loss: ${deadAtLoss} (${pct(deadAtLoss, baseLosses.length)}%)`,
      )
  }

  // ---- [2] lives-exhausted context ----
  if (livesOut.length) {
    const livesS = summarize(livesOut.map((r) => r.forensics.terminal.playerLives))
    const distS = summarize(
      livesOut
        .filter((r) => r.forensics.terminal.playerDistToBase >= 0)
        .map((r) => r.forensics.terminal.playerDistToBase),
    )
    const baseHitsS = summarize(livesOut.map((r) => r.forensics.terminal.baseHitsToDie))
    const baseHpS = summarize(livesOut.map((r) => r.forensics.terminal.baseHp))
    out.push(`\n[2] LIVES-EXHAUSTED CONTEXT  (n=${livesOut.length})`)
    out.push(`    player lives      mean ${fmt(livesS.mean, 1)}  median ${fmt(livesS.median, 1)}`)
    out.push(`    dist to base      mean ${fmt(distS.mean, 1)}  median ${fmt(distS.median, 1)}`)
    out.push(
      `    base HP           mean ${fmt(baseHpS.mean, 0)}  median ${fmt(baseHpS.median, 0)}  base hits-to-die (vs 100 dmg) mean ${fmt(baseHitsS.mean, 1)}  median ${fmt(baseHitsS.median, 1)}  ≤1:${pct(livesOut.filter((r) => r.forensics.terminal.baseHitsToDie <= 1).length, livesOut.length)}%`,
    )
  }

  // ---- [3] history ----
  const deaths = runs.flatMap((r) => r.forensics.events.filter((e) => e.type === 'death'))
  const kills = runs.flatMap((r) => r.forensics.events.filter((e) => e.type === 'kill'))
  const pickups = runs.flatMap((r) => r.forensics.events.filter((e) => e.type === 'pickup'))
  const deathsPerRun = runs.map((r) => r.forensics.events.filter((e) => e.type === 'death').length)
  const killsPerRun = runs.map((r) => r.forensics.events.filter((e) => e.type === 'kill').length)
  out.push(`\n[3] HISTORY  (all ${total} runs)`)
  out.push(
    `    player deaths     total ${deaths.length}  per-run mean ${fmt(mean(deathsPerRun), 2)}  median ${fmt(
      quantile(
        [...deathsPerRun].sort((a, b) => a - b),
        0.5,
      ),
      1,
    )}`,
  )
  if (deaths.length)
    out.push(
      `      death timing (fraction of run) ${tickPcts(runs, 'death')}`,
      `      death hotspots (cell × count)  ${hotspots(deaths)}`,
    )
  out.push(
    `    player kills      total ${kills.length}  per-run mean ${fmt(mean(killsPerRun), 2)}  median ${fmt(
      quantile(
        [...killsPerRun].sort((a, b) => a - b),
        0.5,
      ),
      1,
    )}`,
  )
  if (kills.length)
    out.push(
      `      kill timing (fraction of run) ${tickPcts(runs, 'kill')}`,
      `      kill hotspots (cell × count)  ${hotspots(kills)}`,
    )
  const puTypes = new Map<string, number>()
  for (const e of pickups) puTypes.set(e.detail, (puTypes.get(e.detail) ?? 0) + 1)
  out.push(
    `    pickups (lifetime, by type)  total ${pickups.length}  per-run mean ${fmt(pickups.length / total, 2)}: ` +
      ([...puTypes.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([t, n]) => `${t} ${n} (×${fmt(n / total, 2)}/run)`)
        .join('  ') || '—'),
  )
  if (pickups.length) out.push(`      pickup timing (fraction of run) ${tickPcts(runs, 'pickup')}`)
  const starLevel = summarize(runs.map((r) => r.forensics.terminal.playerLevel))
  out.push(
    `    final star level  mean ${fmt(starLevel.mean, 2)}  median ${fmt(starLevel.median, 1)}  dist ${histogram(
      runs.map((r) => r.forensics.terminal.playerLevel),
      3,
    )}`,
  )

  // ---- [4] failure taxonomy ----
  const byKiller = new Map<string, number>()
  for (const r of baseLosses)
    byKiller.set(
      r.failureKillerKind ?? 'unknown',
      (byKiller.get(r.failureKillerKind ?? 'unknown') ?? 0) + 1,
    )
  if (byKiller.size)
    out.push(
      `\n[*] KILLER MIX: ` +
        [...byKiller.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => `${k} ${v} (${pct(v, baseLosses.length)})`)
          .join('  '),
    )
  const byStage = new Map<number, number>()
  for (const r of baseLosses) byStage.set(r.stageIdx, (byStage.get(r.stageIdx) ?? 0) + 1)
  const worst = [...byStage.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
  out.push(
    `[*] WORST STAGES (base losses / ${total} runs): ${worst.map(([si, n]) => `S${si + 1}:${n}`).join('  ') || '—'}`,
  )
  // Final-10-ticks rule distribution on failed runs: which decision branches
  // dominated the losing window.
  const failRuns = runs.filter((r) => r.outcome !== 'stage_clear' && r.forensics.lastActions.length)
  if (failRuns.length) {
    const ruleCounts = new Map<string, number>()
    for (const r of failRuns)
      for (const a of r.forensics.lastActions)
        ruleCounts.set(a.branch, (ruleCounts.get(a.branch) ?? 0) + 1)
    out.push(
      `[*] LAST-10-TICK RULES on failures (n=${failRuns.length} runs): ` +
        [...ruleCounts.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([b, n]) => `${b} ${n} (${fmt((n / (failRuns.length * 10)) * 100, 0)}%)`)
          .join('  '),
    )
  }

  return out.join('\n')
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
