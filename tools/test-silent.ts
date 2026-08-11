/**
 * Silent test runner for Battle City Web.
 *
 * Purpose: run only the tests related to local changes, and emit *only* the
 * failing-test logs (so an LLM/CI step does not burn tokens on the full,
 * passing output). Passing runs produce a one-line summary; failing runs
 * re-run each failing test individually to capture just its error detail.
 *
 * Copied from another project; adapted to this single-package repo:
 *  - relies on `bun test` (the project's test runner) instead of vitest
 *  - discovers the changed file set from git and maps it to test files
 *  - keeps the bun-compatible failure parsers (file header, `(fail)` lines,
 *    and the `N pass` / `M fail` summary)
 *
 * Usage:
 *   bun tools/test-silent.ts                 # auto-scope to local changes
 *   bun tools/test-silent.ts --strict        # exit clean (no tests) if nothing maps
 *   bun tools/test-silent.ts --heavy         # also run heavy gate/integration sims
 *   bun tools/test-silent.ts -- fileA.test.ts fileB.test.ts   # explicit files
 *
 * Heavy gate/acceptance tests (those that run hundreds–thousands of full-game
 * simulations, e.g. the God-AI 1400-game gates) are EXCLUDED by default — they
 * take minutes and defeat the runner's token/time-saving purpose. Run them
 * deliberately via `bun test` or `bun run test --heavy`.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { relative, resolve } from 'node:path'
import { CWD, gitChangedFiles, spawnCapture } from './runner'

export interface TestResult {
  ok: boolean
  summary: string
  detail: string
}

interface Failure {
  file: string
  testName: string
}

const TEST_RE = /\.test\.(ts|tsx|js|jsx)$/
const SKIP_RE = /^(tmp|node_modules|dist|\.git)([\\/]|$)/

/**
 * Heavy "gate"/acceptance tests that run full-game simulations (hundreds–thousands
 * of sims) and take minutes. Excluded from the fast scoped runner by default;
 * run them deliberately with `bun test` or `bun run test --heavy`. Keep this list
 * in sync with the measured wall-time of the suite (see per-file profiling).
 */
const HEAVY_TESTS = new Set<string>([
  'god-ai-gate', // ~26s: unified worker-pool gate, 3 difficulties × 35 stages × 20 seeds
  'calibration', // ~2.5s: CMA-ES calibration sweep
])

function isHeavyFile(path: string): boolean {
  // baseName keeps the `.test`/`.spec` infix (needed for src→test mapping), so
  // strip it here to match the HEAVY_TESTS basenames.
  const base = baseName(path).replace(/\.(test|spec)$/, '')
  if (HEAVY_TESTS.has(base)) return true
  return false
}

/** Enumerate every test file in the repo (repo-relative, forward slashes). */
function allTestFiles(cwd: string): string[] {
  const collect = (raw: string[]): string[] => {
    const out: string[] = []
    for (const f of raw) {
      if (!TEST_RE.test(f)) continue
      if (SKIP_RE.test(f)) continue
      out.push(f)
    }
    return out
  }
  try {
    const out = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard'], {
      cwd,
      encoding: 'utf8',
    })
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean)
    return collect(out)
  } catch {
    // Fallback when git is unavailable (e.g. corrupted HEAD): walk the tree.
    const out: string[] = []
    for (const d of ['tests', 'src', 'tools']) {
      const dir = resolve(cwd, d)
      if (existsSync(dir)) walk(dir, out)
    }
    return out.map((abs) => relative(cwd, abs).split('\\').join('/'))
  }
}

function walk(dir: string, out: string[]): void {
  let entries
  try {
    entries = readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }
  for (const e of entries) {
    const p = resolve(dir, e.name)
    if (e.isDirectory()) {
      if (SKIP_RE.test(e.name)) continue
      walk(p, out)
    } else if (TEST_RE.test(e.name)) {
      out.push(p)
    }
  }
}

/**
 * Map changed files to the test files that should run.
 *  - a changed test file is included directly
 *  - a changed source file is matched to tests by basename, including common
 *    suffixes/prefixes this repo uses (e.g. World.ts → world-snapshot.test.ts)
 */
function mapToTests(changed: string[], allTests: string[]): string[] {
  const byBase = new Map<string, string[]>()
  for (const t of allTests) {
    const base = baseName(t)
    if (!byBase.has(base)) byBase.set(base, [])
    byBase.get(base)!.push(t)
  }

  const out = new Set<string>()
  for (const f of changed) {
    if (TEST_RE.test(f)) {
      out.add(f)
      continue
    }
    const srcBase = baseName(f)
    if (!srcBase) continue
    for (const [testBase, files] of byBase) {
      if (
        testBase === srcBase ||
        testBase.startsWith(srcBase + '-') ||
        testBase.startsWith(srcBase + '.') ||
        testBase.endsWith('-' + srcBase) ||
        testBase.endsWith('.' + srcBase)
      ) {
        for (const tf of files) out.add(tf)
      }
    }
  }
  return [...out]
}

function baseName(path: string): string {
  const noExt = path.replace(/\.(ts|tsx|js|jsx|mjs|cjs)$/, '')
  const last = noExt.split(/[\\/]/).pop() ?? noExt
  return last.toLowerCase()
}

function parseFailures(output: string): Failure[] {
  const lines = output.split('\n')
  // A file header is a bare `path:` line at column 0. We must NOT match stack
  // frames like `      at <anonymous> (D:/.../file.test.ts:3:39)` — those also
  // contain `.test.ts:` but have leading whitespace, parens, and trailing text.
  const fileRe = /^([^\s(]+\.test\.[tj]sx?):\s*$/
  const failRe = /^\(fail\)\s+(.+?)\s+\[[\d.]+ms\]/
  const failures: Failure[] = []
  let currentFile = ''

  for (const line of lines) {
    const t = line.trim()
    if (/^\d+ pass/.test(t) || /\d+ fail/.test(t)) break
    const fm = t.match(fileRe)
    if (fm) {
      currentFile = fm[1]
      continue
    }
    const xm = t.match(failRe)
    if (xm && currentFile) {
      const full = xm[1]
      const testName = full.includes('>') ? full.split('>').pop()!.trim() : full
      failures.push({ file: currentFile, testName })
    }
  }
  return failures
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function extractSummary(output: string): string {
  const passMatch = output.match(/(\d+)\s+pass/)
  const failMatch = output.match(/(\d+)\s+fail/)
  const skipMatch = output.match(/(\d+)\s+skip/)
  const parts: string[] = []
  if (passMatch) parts.push(`${passMatch[1]} pass`)
  if (failMatch) parts.push(`${failMatch[1]} fail`)
  if (skipMatch) parts.push(`${skipMatch[1]} skip`)
  return parts.join(', ') || 'unknown'
}

export interface SilentTestOptions {
  cwd: string
  label?: string
  /** Explicit test files to run; overrides git-based discovery. */
  files?: string[]
  /** If true, run nothing (and report clean) when no tests map to changes. */
  strict?: boolean
  /** If true, include heavy gate/integration sim tests that are skipped by default. */
  heavy?: boolean
  timeoutMs?: number
}

/** Run `bun test` silently; on failure, re-run each failing test to capture detail. */
export async function runSilentTest(
  arg: string | SilentTestOptions,
  labelArg = '',
): Promise<TestResult> {
  const opts: SilentTestOptions = typeof arg === 'string' ? { cwd: arg, label: labelArg } : arg
  const cwd = opts.cwd
  const label = opts.label ?? 'tests'
  const timeoutMs = opts.timeoutMs ?? 0

  // 1. Decide which test files to run.
  let files: string[]
  let mode: string
  if (opts.files && opts.files.length) {
    files = opts.files
    mode = 'explicit'
  } else {
    const allTests = allTestFiles(cwd)
    const changed = gitChangedFiles(cwd)
    const mapped = mapToTests(changed, allTests)
    if (mapped.length) {
      files = mapped
      mode = `changed (${changed.length} changed → ${mapped.length} test file(s))`
    } else if (opts.strict) {
      return {
        ok: true,
        summary: 'no relevant tests',
        detail: `${label}: no tests map to local changes (strict mode)\n`,
      }
    } else {
      files = allTests
      mode = changed.length
        ? `fallback:all (${changed.length} changed file(s) mapped to no tests)`
        : 'all (clean tree)'
    }
  }

  // By default, exclude heavy gate/integration acceptance sims (e.g. the God-AI
  // 1400-game gates) — they take minutes and defeat the runner's token/time-saving
  // purpose. They still run via `bun test` or when `--heavy` is passed. Heavy
  // tests are never filtered when the caller passed explicit files (user intent).
  let skippedHeavy = 0
  if (!opts.heavy && !(opts.files && opts.files.length)) {
    const before = files.length
    files = files.filter((f) => !isHeavyFile(f))
    skippedHeavy = before - files.length
  }
  if (skippedHeavy > 0) {
    mode += `, skipped ${skippedHeavy} heavy gate/integration test(s) (use --heavy)`
  }

  // Drop files that no longer exist (e.g. a deleted test file still in git diff).
  files = files.map((f) => f.replace(/\\/g, '/')).filter((f) => existsSync(resolve(cwd, f)))

  if (files.length === 0) {
    return { ok: true, summary: 'no tests to run', detail: `${label}: no test files resolved\n` }
  }

  // 2. Run the selected tests once.
  const first = await spawnCapture('bun', ['test', ...files], cwd, timeoutMs)
  const summary = extractSummary(first.output)
  const failures = parseFailures(first.output)

  if (first.code === 0 && failures.length === 0) {
    return { ok: true, summary: `${summary} [${mode}]`, detail: '' }
  }

  // 3. On failure, re-run each failing test individually to isolate its detail.
  let detail = `${label} [${mode}]:\n${summary}\n`
  if (failures.length === 0) {
    // Process exited non-zero but produced no parseable failures (crash/syntax error).
    detail += '\n--- raw output ---\n' + first.output + '\n'
    return { ok: false, summary, detail }
  }
  for (const f of failures) {
    const fileArg = f.file.replace(/\\/g, '/')
    detail += `\n--- ${f.testName} (${fileArg}) ---\n`
    const r = await spawnCapture(
      'bun',
      ['test', '-t', escapeRegex(f.testName), fileArg],
      cwd,
      timeoutMs,
    )
    detail += r.output + '\n'
  }
  return { ok: false, summary, detail }
}

// Direct CLI invocation: `bun tools/test-silent.ts [--strict] [-- file ...]`.
const isMain: boolean = (import.meta as { main?: boolean }).main === true
if (isMain) {
  const argv = process.argv.slice(2)
  const strict = argv.includes('--strict')
  const heavy = argv.includes('--heavy')
  // Collect non-flag positional args as explicit files. `--` is only a separator;
  // `bun run` strips it when invoked as `bun run test -- file`, so we must not
  // rely on it being present.
  const explicit = argv.filter((a) => a !== '--' && a !== '--strict' && a !== '--heavy')
  const result = await runSilentTest({
    cwd: CWD,
    label: 'local tests',
    files: explicit,
    strict,
    heavy,
  })
  if (result.ok) {
    console.log(`✓ ${result.summary}`)
    process.exit(0)
  } else {
    console.log(`✗ ${result.summary}`)
    console.log(result.detail)
    process.exit(1)
  }
}
