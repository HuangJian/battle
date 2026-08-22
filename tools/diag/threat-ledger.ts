#!/usr/bin/env bun
/**
 * threat-ledger.ts — M0 failure-attribution sweep (plan/
 * God-AI-Hard-Breakthrough-Implementation.md §4: Phase 0 建立失败归因基线).
 *
 * Runs a stage×seed sweep with BOTH forensics and the event-driven threat
 * ledger ON, classifies every failed run into the §4.2 families, and prints:
 *
 *   [0] outcome mix + family mix (per difficulty)
 *   [1] family × stage worst-stages table
 *   [2] per-tick ledger reports for representative failures (--report N per
 *       family, or --report-runs "S34:1,S34:2" to pick specific combos)
 *
 * Usage:
 *   bun tools/diag/threat-ledger.ts --seeds 60 --difficulty hard --json tmp/ledger-hard.json
 *   bun tools/diag/threat-ledger.ts --seeds 60 --difficulty hard --report 2
 *   bun tools/diag/threat-ledger.ts --from-json tmp/ledger-hard.json --report 1
 *
 * `--from-json` re-runs only the previously collected combos (same combos are
 * deterministic → same failures) for iteration on the classifier itself —
 * it needs the ledger samples, so it re-runs WITH the ledger ON.
 */
import { STAGES } from '../../src/config/stages'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { SimWorkerPool } from '../sim/sim-pool'
import type { SimTask } from '../sim/sim-worker'
import type { ThreatLedgerRun } from '../sim/simulation-runner'
import { parseStageSpec, StageSpecError, runHeader } from '../lib/stage-spec'
import { arg, parseSeeds } from '../lib/cli'
import {
  classifyFailure,
  aggregateClassifications,
  FAILURE_CLASS_NAMES,
  type Classification,
  type FailureClass,
} from './failure-classifier'

const difficulties = (arg('difficulty') ?? 'hard')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
const seeds = parseSeeds(arg('seeds'), 60)
const stageSpec = arg('stages') ?? 'all'
let stageIdxs: number[]
try {
  stageIdxs = parseStageSpec(stageSpec, STAGES.length)
} catch (e) {
  console.error(
    e instanceof StageSpecError ? e.message : `threat-ledger: invalid --stages: ${stageSpec}`,
  )
  process.exit(1)
}
const maxTicks = Number(arg('max-ticks') ?? '36000')
const jsonPath = arg('json')
const reportPerFamily = Number(arg('report') ?? '0')
const fromJson = arg('from-json')
const reportRuns = (arg('report-runs') ?? '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)

interface LedgerRun {
  difficulty: string
  stageIdx: number
  seed: number
  outcome: string
  failureCause?: string
  failureKillerKind?: string
  ticks: number
  ledger: ThreatLedgerRun
  classification: Classification
}

function classify(
  difficulty: string,
  stageIdx: number,
  seed: number,
  outcome: string,
  failureCause: string | undefined,
  ticks: number,
  ledger: ThreatLedgerRun,
): LedgerRun {
  return {
    difficulty,
    stageIdx,
    seed,
    outcome,
    failureCause,
    failureKillerKind: undefined,
    ticks,
    ledger,
    classification: classifyFailure(ledger, failureCause),
  }
}

/**
 * Compact form persisted to JSON: the report path reads live in-memory
 * ledgers, and --from-json only re-derives the run combos, so the corpus can
 * drop the per-enemy ETAs and long keys (a 2100-run hard sweep otherwise
 * balloons past 400MB).
 */
function compactLedger(r: ThreatLedgerRun): unknown {
  return {
    outcome: r.outcome,
    failureCause: r.failureCause,
    tick: r.tick,
    baseMaxHp: r.baseMaxHp,
    samples: r.samples.map((s) => ({
      t: s.tick,
      hp: s.baseHp,
      ring: s.intactRing,
      pc: [s.playerCell.col, s.playerCell.row],
      pd: s.playerDir,
      pl: s.playerLives,
      b: s.branch,
      cd: s.onCooldown,
      live: s.liveEnemies,
      thr: s.baseThreatNow,
      eta: s.nearestThreatEta,
      pe: s.playerEtaToBestIntercept,
      slack: s.threatSlack,
      noOp: s.noOpReason,
      enemies: s.enemies.map((e) => ({
        k: e.kind,
        i: e.id,
        c: [e.cell.col, e.cell.row],
        d: e.dir,
        s: e.canShootBase,
        b: e.canBreachRing,
      })),
    })),
  }
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
      params: DEFAULT_GOD_AI_PARAMS,
    }),
  )
  const tasks: SimTask[] = []
  const meta: Array<{ difficulty: string; stageIdx: number; seed: number }> = []

  const runCombo = (difficulty: string, stageIdx: number, seed: number) => {
    tasks.push({
      id: tasks.length,
      seed,
      stage: STAGES[stageIdx],
      difficulty,
      params: { ...DEFAULT_GOD_AI_PARAMS },
      maxTicks,
      forensics: true,
      threatLedger: true,
    })
    meta.push({ difficulty, stageIdx, seed })
  }

  if (fromJson) {
    let prev: {
      runs?: Array<{ difficulty: string; stageIdx: number; seed: number }>
    }
    try {
      prev = JSON.parse(await Bun.file(fromJson).text())
    } catch (err) {
      console.error(
        `threat-ledger: --from-json ${fromJson}: cannot read/parse corpus (${(err as Error).message})`,
      )
      process.exit(1)
    }
    for (const r of prev.runs ?? []) runCombo(r.difficulty, r.stageIdx, r.seed)
    if (tasks.length === 0) {
      console.error(`threat-ledger: --from-json ${fromJson} yielded no runs`)
      process.exit(1)
    }
  } else {
    for (const difficulty of difficulties)
      for (const stageIdx of stageIdxs)
        for (const seed of seeds) runCombo(difficulty, stageIdx, seed)
  }

  process.stderr.write(
    `threat-ledger: ${tasks.length} runs (${pool.size} workers, maxTicks=${maxTicks})` +
      (fromJson ? `, subset of ${fromJson}` : '') +
      '\n',
  )
  const t0 = Date.now()
  const results = await pool.runBatch(tasks)
  pool.terminate()
  process.stderr.write(
    `threat-ledger: ran ${results.length} runs in ${((Date.now() - t0) / 1000).toFixed(1)}s\n`,
  )

  const runs: LedgerRun[] = []
  let errors = 0
  for (let i = 0; i < results.length; i++) {
    const r = results[i]
    const m = meta[i]
    if (!r.ok || !r.ledger) {
      errors++
      continue
    }
    runs.push(
      classify(m.difficulty, m.stageIdx, m.seed, r.outcome, r.failureCause, r.ticks, r.ledger),
    )
  }
  if (errors) console.error(`  WARNING: ${errors} runs missing ledger/errored`)

  const out: string[] = []
  const perDifficulty = new Map<string, LedgerRun[]>()
  const diffSet = [...new Set(meta.map((m) => m.difficulty))]
  for (const d of diffSet)
    perDifficulty.set(
      d,
      runs.filter((r) => r.difficulty === d),
    )

  for (const d of diffSet) reportDifficulty(out, d, perDifficulty.get(d) ?? [])

  // ---- per-tick reports ----
  if (reportPerFamily > 0) {
    for (const d of diffSet) {
      const rs = perDifficulty.get(d) ?? []
      const byFamily = new Map<FailureClass, LedgerRun[]>()
      for (const r of rs) {
        if (r.outcome === 'stage_clear') continue
        const list = byFamily.get(r.classification.primary) ?? []
        list.push(r)
        byFamily.set(r.classification.primary, list)
      }
      for (const [family, list] of [...byFamily.entries()].sort(
        (a, b) => b[1].length - a[1].length,
      )) {
        void family
        const picked = list.slice(0, reportPerFamily)
        for (const r of picked) {
          out.push(`\n${'─'.repeat(78)}`)
          out.push(
            `PER-TICK REPORT  ${d} S${r.stageIdx + 1} seed ${r.seed}  [${FAILURE_CLASS_NAMES[r.classification.primary]}${r.classification.secondary.length ? ` + ${r.classification.secondary.map((c) => FAILURE_CLASS_NAMES[c]).join(',')}` : ''}]  (${r.outcome}, t=${r.ticks})`,
          )
          for (const e of r.classification.evidence) out.push(`  ! ${e}`)
          out.push('  tick  baseHp ring live  thrEta  slack  cooldown noOp  playerCell -> branch')
          for (const s of r.ledger.samples) {
            const threats = s.enemies
              .filter((e) => e.canShootBase || e.canBreachRing)
              .map(
                (e) =>
                  `${e.kind}#${e.id}(${e.canShootBase ? 'B' : 'R'})@${e.cell.col},${e.cell.row}`,
              )
              .join(' ')
            out.push(
              `  ${String(s.tick).padStart(6)}  ${String(s.baseHp).padStart(3)}   ${String(s.intactRing).padStart(3)}   ${String(s.liveEnemies).padStart(2)}  ${String(s.nearestThreatEta).padStart(5)}  ${String(s.threatSlack).padStart(6)}  ${s.onCooldown ? ' cd' : '   '} ${s.noOpReason ? ` ${s.noOpReason}` : '    '}  (${s.playerCell.col},${s.playerCell.row}) -> ${s.branch}${threats ? `  THREATS: ${threats}` : ''}`,
            )
          }
        }
      }
    }
  }

  // ---- specific-run reports (--report-runs S34:1,S34:2) ----
  if (reportRuns.length) {
    for (const spec of reportRuns) {
      const m = /^S?(\d+):(\d+)$/i.exec(spec)
      if (!m) continue
      const stageIdx = Number(m[1]) - 1
      const seed = Number(m[2])
      const r = runs.find((x) => x.stageIdx === stageIdx && x.seed === seed)
      if (!r) {
        out.push(`\nreport-runs: S${stageIdx + 1} seed ${seed} not in corpus`)
        continue
      }
      out.push(`\n${'─'.repeat(78)}`)
      out.push(
        `PER-TICK REPORT  ${r.difficulty} S${r.stageIdx + 1} seed ${r.seed}  [${FAILURE_CLASS_NAMES[r.classification.primary]}]  (${r.outcome}, t=${r.ticks})`,
      )
      for (const e of r.classification.evidence) out.push(`  ! ${e}`)
      out.push('  tick  baseHp ring live  thrEta  slack  cooldown noOp  playerCell -> branch')
      for (const s of r.ledger.samples) {
        const threats = s.enemies
          .filter((e) => e.canShootBase || e.canBreachRing)
          .map(
            (e) => `${e.kind}#${e.id}(${e.canShootBase ? 'B' : 'R'})@${e.cell.col},${e.cell.row}`,
          )
          .join(' ')
        out.push(
          `  ${String(s.tick).padStart(6)}  ${String(s.baseHp).padStart(3)}   ${String(s.intactRing).padStart(3)}   ${String(s.liveEnemies).padStart(2)}  ${String(s.nearestThreatEta).padStart(5)}  ${String(s.threatSlack).padStart(6)}  ${s.onCooldown ? ' cd' : '   '} ${s.noOpReason ? ` ${s.noOpReason}` : '    '}  (${s.playerCell.col},${s.playerCell.row}) -> ${s.branch}${threats ? `  THREATS: ${threats}` : ''}`,
        )
      }
    }
  }

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
      runs: runs.map((r) => ({
        difficulty: r.difficulty,
        stageIdx: r.stageIdx,
        seed: r.seed,
        outcome: r.outcome,
        failureCause: r.failureCause,
        ticks: r.ticks,
        classification: {
          primary: r.classification.primary,
          secondary: r.classification.secondary,
          evidence: r.classification.evidence,
        },
        ledger: r.outcome === 'stage_clear' ? undefined : compactLedger(r.ledger),
      })),
    }
    await Bun.write(jsonPath, JSON.stringify(payload))
    console.log(`JSON → ${jsonPath}`)
  }
}

function reportDifficulty(out: string[], difficulty: string, runs: LedgerRun[]): void {
  const total = runs.length
  if (total === 0) return
  const clears = runs.filter((r) => r.outcome === 'stage_clear')
  const failures = runs.filter((r) => r.outcome !== 'stage_clear')
  const baseLosses = failures.filter((r) => r.failureCause === 'base_destroyed')

  out.push('')
  out.push('='.repeat(78))
  out.push(`DIFFICULTY: ${difficulty}   (${total} runs)`)
  out.push('='.repeat(78))
  out.push(
    `    stage_clear      ${clears.length.toString().padStart(5)}  (${((clears.length / total) * 100).toFixed(1)}%)`,
  )
  out.push(
    `    failures         ${failures.length.toString().padStart(5)}  (base_destroyed ${baseLosses.length}, lives_exhausted ${failures.filter((r) => r.failureCause === 'lives_exhausted').length}, timeout ${failures.filter((r) => r.outcome === 'max_ticks').length})`,
  )

  if (failures.length) {
    const fam = aggregateClassifications(
      failures.map((r) => ({
        key: `${r.difficulty}S${r.stageIdx + 1}#${r.seed}`,
        classification: r.classification,
      })),
    )
    out.push(`\n[*] FAILURE FAMILIES (primary class, n=${failures.length}):`)
    for (const [cls, n] of [...fam.entries()].sort((a, b) => b[1] - a[1])) {
      const list = failures.filter((r) => r.classification.primary === cls)
      const stages = [...new Set(list.map((r) => `S${r.stageIdx + 1}`))]
      const sec = new Map<FailureClass, number>()
      for (const r of list)
        for (const s of r.classification.secondary) sec.set(s, (sec.get(s) ?? 0) + 1)
      out.push(
        `    ${cls.padEnd(22)} ${String(n).padStart(4)}  (${((n / failures.length) * 100).toFixed(1)}%)` +
          `  stages: ${stages.slice(0, 10).join(',')}${stages.length > 10 ? '…' : ''}` +
          (sec.size
            ? `  secondary: ${[...sec.entries()].map(([s, c]) => `${s}×${c}`).join(' ')}`
            : ''),
      )
    }
    // base_destroyed sub-split (the plan's target family).
    const baseFam = aggregateClassifications(
      baseLosses.map((r) => ({
        key: `${r.difficulty}S${r.stageIdx + 1}#${r.seed}`,
        classification: r.classification,
      })),
    )
    out.push(`\n[*] BASE_DESTROYED ONLY (n=${baseLosses.length}):`)
    for (const [cls, n] of [...baseFam.entries()].sort((a, b) => b[1] - a[1])) {
      out.push(
        `    ${cls.padEnd(22)} ${String(n).padStart(4)}  (${((n / baseLosses.length) * 100).toFixed(1)}%)`,
      )
    }
  }

  // Worst stages by base losses with family mix.
  const byStage = new Map<number, LedgerRun[]>()
  for (const r of baseLosses) {
    const list = byStage.get(r.stageIdx) ?? []
    list.push(r)
    byStage.set(r.stageIdx, list)
  }
  const worst = [...byStage.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 8)
  if (worst.length) {
    out.push(`\n[*] WORST STAGES (base losses):`)
    for (const [si, list] of worst) {
      const fam = aggregateClassifications(
        list.map((r) => ({ key: `${r.seed}`, classification: r.classification })),
      )
      out.push(
        `    S${si + 1}: ${list.length}/${total / byStage.size} runs  ` +
          [...fam.entries()].map(([c, n]) => `${FAILURE_CLASS_NAMES[c]}×${n}`).join(' '),
      )
    }
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
