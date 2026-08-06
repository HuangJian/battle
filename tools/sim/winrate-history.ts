/**
 * winrate-history.ts — shared snapshot format + loader for the win-rate sweep history.
 *
 * A "snapshot" is a frozen copy of one `sweep-winrate.ts` run (`results.json`)
 * plus provenance metadata (label, save time, git commit). Snapshots live as flat
 * JSON files in a history directory (default `reports/winrate/history/`) so that
 * `sweep-winrate.ts` can diff the current run against any subset of them.
 *
 * Written by  tools/sim/snapshot-winrate.ts
 * Read by     tools/sim/sweep-winrate.ts
 */
import { execSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'

export const DEFAULT_HISTORY_DIR = 'reports/winrate/history'
export const DEFAULT_RESULTS_PATH = 'reports/winrate/results.json'

export interface GitInfo {
  commit: string
  subject: string
  dirty: boolean
}

export interface SnapshotDifficulty {
  name: string
  totalRuns: number
  winRate: number
  avgKills: number
  baseDestroyedRate: number
  baseDestroyedAmongLosses: number
  gameovers: number
  timeouts: number
}

export interface SnapshotStage {
  index: number
  name: string
  winRate: number
  avgKills: number
  baseDestroyedRate: number
}

/** Shape of `results.json` emitted by sweep-winrate.ts. */
export interface WinrateResults {
  scope: {
    difficulties: string[]
    stageCount: number
    seeds: number[]
    seedsCount: number
    maxTicks: number
  }
  generatedAt: string
  perDifficulty: SnapshotDifficulty[]
  perStage: Array<{ name: string; stages: SnapshotStage[] }>
}

/** A `results.json` archived under the history directory. */
export interface WinrateSnapshot extends WinrateResults {
  id: string
  label: string
  savedAt: string
  git?: GitInfo
}

/** Best-effort git provenance; returns undefined outside a repo. */
export function gitInfo(): GitInfo | undefined {
  const run = (cmd: string) =>
    execSync(cmd, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
  try {
    return {
      commit: run('git rev-parse --short HEAD').trim(),
      subject: run('git log -1 --pretty=%s').trim(),
      dirty: run('git status --porcelain').trim().length > 0,
    }
  } catch {
    return undefined
  }
}

/** Filesystem-safe slug for snapshot ids. */
export function slug(s: string): string {
  return (
    s
      .trim()
      .replace(/[^\w\u4e00-\u9fa5.-]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || 'run'
  )
}

/** `2026-08-06_0638` — sortable, filename-safe. */
export function stampNow(d = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

/** Load every snapshot in `dir`, oldest first. Corrupt files are skipped with a warning. */
export function loadSnapshots(dir: string): WinrateSnapshot[] {
  if (!existsSync(dir)) return []
  const out: WinrateSnapshot[] = []
  for (const file of readdirSync(dir)) {
    if (!file.endsWith('.json')) continue
    const path = `${dir}/${file}`
    try {
      const s = JSON.parse(readFileSync(path, 'utf8')) as WinrateSnapshot
      if (!s || !Array.isArray(s.perDifficulty) || !Array.isArray(s.perStage)) {
        process.stderr.write(`[history] skip ${file}: not a sweep result\n`)
        continue
      }
      if (!s.id) s.id = file.replace(/\.json$/, '')
      if (!s.label) s.label = s.id
      if (!s.savedAt) s.savedAt = s.generatedAt ?? ''
      out.push(s)
    } catch (e) {
      process.stderr.write(`[history] skip ${file}: ${(e as Error).message}\n`)
    }
  }
  // Chronological by when the sweep *ran* (not when it was archived), so importing
  // an older results.json still lands in the right place on the timeline.
  const key = (s: WinrateSnapshot) => s.generatedAt || s.savedAt
  return out.sort((a, b) => key(a).localeCompare(key(b)))
}
