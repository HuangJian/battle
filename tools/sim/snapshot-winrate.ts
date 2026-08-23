#!/usr/bin/env bun
/**
 * snapshot-winrate.ts — freeze the current sweep result as a history snapshot.
 *
 * Copies `reports/winrate/results.json` into the history directory with a
 * timestamped id plus provenance (label + git commit). `sweep-winrate.ts` then
 * reads that directory and renders win-rate deltas against any subset of them.
 *
 * Usage:
 *   bun tools/sim/snapshot-winrate.ts --label "§149 baseline"
 *   bun tools/sim/snapshot-winrate.ts --list
 *   bun tools/sim/snapshot-winrate.ts --from reports/winrate/results.json --dir reports/winrate/history
 *
 * The label is optional — it defaults to the current git commit subject.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'

import { arg } from '../lib/cli'
import {
  DEFAULT_HISTORY_DIR,
  DEFAULT_RESULTS_PATH,
  gitInfo,
  loadSnapshots,
  slug,
  stampNow,
  type WinrateResults,
  type WinrateSnapshot,
} from './winrate-history'

function flag(name: string): boolean {
  return process.argv.includes(`--${name}`)
}

const fromPath = arg('from', DEFAULT_RESULTS_PATH)!
const dir = arg('dir', DEFAULT_HISTORY_DIR)!

if (flag('list')) {
  const snaps = loadSnapshots(dir)
  if (snaps.length === 0) {
    console.log(`(no snapshots in ${dir})`)
  } else {
    console.log(`${snaps.length} snapshot(s) in ${dir}:\n`)
    for (const s of snaps) {
      const rates = s.perDifficulty.map((d) => `${d.name}=${d.winRate.toFixed(1)}%`).join(' ')
      const git = s.git ? `${s.git.commit}${s.git.dirty ? '+dirty' : ''}` : '-'
      console.log(`  ${s.id}\n    label=${s.label}  git=${git}\n    ${rates}`)
    }
  }
  process.exit(0)
}

if (!existsSync(fromPath)) {
  process.stderr.write(
    `✗ ${fromPath} not found. Run the sweep first:\n` +
      `  bun tools/sim/sweep-winrate.ts --difficulties classic,hard,chaos --seeds 1-60\n`,
  )
  process.exit(1)
}

const results = JSON.parse(readFileSync(fromPath, 'utf8')) as WinrateResults
if (!Array.isArray(results.perDifficulty) || !Array.isArray(results.perStage)) {
  process.stderr.write(`✗ ${fromPath} is not a sweep result (missing perDifficulty/perStage).\n`)
  process.exit(1)
}

const git = gitInfo()
const label = arg('label') ?? (git ? `${git.commit} ${git.subject}` : 'unlabeled')
// Stamp the id with when the sweep *ran*, so re-snapshotting the same
// results.json is idempotent and the filename sorts chronologically.
const ranAt = results.generatedAt ? new Date(results.generatedAt) : new Date()
const id = `${stampNow(ranAt)}__${slug(arg('label') ?? git?.commit ?? 'run')}`

const snapshot: WinrateSnapshot = {
  ...results,
  id,
  label,
  savedAt: new Date().toISOString(),
  ...(git ? { git } : {}),
}

mkdirSync(dir, { recursive: true })
const outPath = `${dir}/${id}.json`
writeFileSync(outPath, JSON.stringify(snapshot, null, 2))

const rates = snapshot.perDifficulty.map((d) => `${d.name}=${d.winRate.toFixed(1)}%`).join('  ')
console.log(`✓ snapshot saved: ${outPath}`)
console.log(`  label: ${label}`)
console.log(`  scope: ${results.scope.stageCount} stages × ${results.scope.seedsCount} seeds`)
console.log(`  ${rates}`)
console.log(`\nRe-run the sweep to see deltas against it:`)
console.log(`  bun tools/sim/sweep-winrate.ts --difficulties classic,hard,chaos --seeds 1-60`)
