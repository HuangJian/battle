/**
 * eval-refs.ts — the single loader for tools/eval/eval-refs.json
 * (refactor.trae.md §2.6: was duplicated in eval-suite.ts and
 * optimize-godai.ts with drift-prone path math).
 *
 * Pure module: reads one JSON file, no RNG, no params mutation.
 */
import { readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import type { StageRefs } from '../eval/godai-score'

/** Per-stage references, keyed by stage name. */
export type RefsFile = Record<string, StageRefs>

export const EVAL_REFS_FILE = join(import.meta.dir, '../eval/eval-refs.json')

/** Load per-stage calibrated references; empty object when absent/corrupt. */
export function loadEvalRefs(): RefsFile {
  if (!existsSync(EVAL_REFS_FILE)) return {}
  try {
    return JSON.parse(readFileSync(EVAL_REFS_FILE, 'utf8')).stages ?? {}
  } catch {
    return {}
  }
}

/**
 * Default forensics corpus produced by `tools/diag/run-forensics.ts`
 * (open-test protocol baseline). Shared by the audit tools that consume it.
 */
export const DEFAULT_FORENSICS_CORPUS = 'tmp/open-test-forensics-baseline.json'

/** Back-compat alias used by optimize-godai.ts consumers. */
export function loadStageRefs(): RefsFile {
  return loadEvalRefs()
}
