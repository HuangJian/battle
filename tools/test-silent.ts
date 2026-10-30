/**
 * Silent test runner for Battle City Web.
 *
 * Purpose: run the tests that local changes can affect, and emit *only* the
 * failing-test logs (so an LLM/CI step does not burn tokens on the full,
 * passing output). Passing runs produce a one-line summary; failing runs
 * re-run each failing test individually to capture just its error detail.
 *
 * Copied from another project; adapted to this single-package repo:
 *  - relies on `bun test` (the project's test runner) instead of vitest
 *  - discovers the changed file set from git; **代码改动一律跑全套**（见下）
 *  - keeps the bun-compatible failure parsers (file header, `(fail)` lines,
 *    and the `N pass` / `M fail` summary)
 *
 * **为什么不再「按 basename 窄跑」（2026-09-15）**：旧版把改动文件按 basename 映射到
 * 同名测试、命中就只跑那几个。命中方向是**欠采样**，实测：
 *   src/config/stages.ts     → 命中 1 个测试，但 50 个测试直接 import 它
 *   src/config/difficulty.ts → 命中 1 个测试，但 39 个
 *   src/config/combat.ts     → 命中 3 个，但 13 个
 * 即：改 `stages.ts` 只跑 `stages.test.ts` 就能让门禁变绿，而另外 49 个依赖它的测试
 * 一个没跑；反倒是「映射为空 → fallback 全量」更安全。代价也几乎不存在：非 heavy
 * 全量实测 ~6s，只跑「import tools 的 39 个文件」~5s —— 为 1s 留一个静默漏测面是坏
 * 交易（AGENTS §13 simple beats clever）。仍然保留的省法是**跳过无关改动**（纯文档/
 * 课程配置、只改 dashboard），那不是窄跑。
 *
 * Usage:
 *   bun tools/test-silent.ts                 # 代码改动 → 全量（非 heavy）；无关改动 → 跳过
 *   bun tools/test-silent.ts --heavy         # also run heavy gate/integration sims
 *   bun tools/test-silent.ts -- fileA.test.ts fileB.test.ts   # explicit files
 *   bun tools/test-silent.ts --changed[=<ref>]  # 只跑 import 图受影响的文件（开发回路快道）
 *
 * 重负载 gate（见 `HEAVY_TESTS` 的判据注释）默认排除。**2026-10-26 重测**：名单里只剩
 * `godai-score-gate`，而它自 2026-10-19 起是 `test.skip`（单跑 237ms = 地板的 0.01×），
 * 豁免已不省任何时间（留着无害：它只把一仃 no-op 挡在快跑外）。同次重测：套件 247 文件
 * / 8 worker 地板 14.4s / 实测 15–17s —— 要提速只能改仿真计算，不是调度旋钮。
 * 显式跑全量用 `bun test` 或 `bun run test --heavy`。
 */
import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { relative, resolve } from 'node:path'
import { physicalCores } from './lib/cores'
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
/**
 * 不参与根套件的路径前缀。
 *
 * `dashboard/` 是**独立 bun 项目**（自带 package.json / bun.lock / node_modules，
 * 见 DECISIONS §2026-09-14-goalnn-dashboard-project · 全文 → docs/nn/console.md §11），它的测试由自己的门禁跑
 * （`cd dashboard && bun run test`，pre-commit 里的 dashboard 门禁块）。根套件若
 * 继续枚举它，就会把「两份 node_modules 互不重叠」重新耦合回去 —— 根门禁将反过来
 * 依赖 dashboard 的安装状态。镜像关系：根 tsconfig 的 include 同样不含 dashboard。
 *
 * `userscript/` 同理（2026-10-29，plan/notebook-userscript.plan.md §4.1）：第二个独立
 * bun 项目（自带 package.json / bun.lock / node_modules），根套件不跑它的测试；它的门禁
 * 由 `cd userscript && bun run check` 与 pre-commit 的 userscript 块承担。
 */
const SKIP_RE = /^(tmp|node_modules|dist|\.git|dashboard|userscript)([\\/]|$)/

/**
 * 重负载 gate —— 默认排除，`--heavy` 显式带上。
 *
 * **判据不是「慢」，而是「值不值得为它牺牲覆盖率」**：只有当某个文件**单独运行的
 * 墙钟 ≥ 非 heavy 全量套件自身的墙钟**时才排除它 —— 即它一个就抵得上整份套件。
 * 低于这条线时，`--parallel`（16 worker 起）会把它填进既有的尾巴里，剔除收益
 * ~等于它自己的运行时间（实测落在 0.2–1.1s 的噪声带内），却白送一个静默盲区：
 * 这类文件被改坏时，本地 hook 是**唯一**会跑它的自动化路径（CI 无根套件 workflow）。
 *
 * 2026-09-15 实测（16 vCPU Linux，`bun tools/measure-suite.ts`，地板 = 179 files 跑完 6.1s）：
 *   godai-score-gate       11.7–13.1s  (2.0×)      ← 唯一达标：带上它 6.1s → 18.6s（3.0×）
 *   nn/intent-rl-rollout    3.8s / 1.7s (0.3–0.6×) ← 次慢但仍在 1× 地板下（跳跑实测只省 ~0.6s，噪声内）
 *   calibration            0.66–0.71s  (0.11×)     ← **不达标**：2026-09-15 移出名单（旧注释记 ~2.5s 已过期——
 *                                                   该测试自己写着「full sweep 走 CLI」= tools/eval/calibrate.ts）
 * 两处旧注释（godai-score-gate ~19.5s / calibration ~2.5s）都测错了，这就是为什么判据必须是
 * **可复核的测量**而不是一个写死的秒数：改名单前先跑 `bun tools/measure-suite.ts`，它会直接
 * 打出每个条目的达标判定。名单本身（名字必须存在）由 tests/test-silent-scope.test.ts 钉住。
 */
export const HEAVY_TESTS = new Set<string>([
  'godai-score-gate', // 11.7–13.1s: worker-pool score gate, 3 difficulties × 35 stages × 10 seeds (§233)
])

/**
 * 路径 → HEAVY_TESTS 口径的名字（basename 去掉 `.test`/`.spec`）。
 * 单一口径来源：过滤（isHeavyFile）、名单校验（staleHeavyEntries）、剖面工具
 * （tools/measure-suite.ts）都走这里，免得三处各自 strip 而悄悄漂移。
 */
export function heavyName(path: string): string {
  return baseName(path).replace(/\.(test|spec)$/, '')
}

export function isHeavyFile(path: string): boolean {
  return HEAVY_TESTS.has(heavyName(path))
}

/**
 * HEAVY_TESTS 里匹配不到任何现存测试文件的名字（重命名/删除残留）。
 *
 * 抽成导出函数是为了让 `tests/test-silent-scope.test.ts` 把「名单必须有效」钉成**硬断言**：
 * 下面的内联 ⚠ 提示会挂在绿行摘要上，实际没人会读，所以提示本身拦不住名单腐烂
 * （2026-09-14 已有先例：`god-ai-gate` 重命名后残留，见 docs/god-ai-tuning.progress.md §234）。
 */
export function staleHeavyEntries(allTests: string[]): string[] {
  const known = new Set(allTests.map(heavyName))
  return [...HEAVY_TESTS].filter((h) => !known.has(h))
}

/**
 * 「无测试影响」的改动类型：纯文档 / notebook / 课程配置 / gitignore。
 *
 * 这些文件不参与任何 TS 或 Python 代码路径，改它们不可能让测试变红。若仍走
 * fallback 全量，等于每次写文档都白烧一整轮套件（并会撞上 spawn 真实 CLI 的慢
 * 测试 —— 2026-09-08 `tests/training-train.test.ts` 因此触发 ensureVenv() 联网装
 * torch，40s+ 且 exit 4）。
 *
 * 只在**全部**改动都属于这些类型时才跳过；只要混进一个代码文件就照常跑。
 * 逃逸阀：`BATTLE_TEST_FORCE_ALL=1` 强制跑全量。
 */
const TEST_INERT_RE = [/\.md$/i, /\.ipynb$/i, /\.jsonc$/i, /(^|\/)\.gitignore$/]

function isTestInertFile(rel: string): boolean {
  return TEST_INERT_RE.some((re) => re.test(rel))
}

/**
 * 根套件是否应该跑这个测试文件（见 SKIP_RE 注释）。
 *
 * 抽成导出函数是为了可测 —— 这条排除一旦失灵，根门禁就会反向依赖 dashboard 的
 * 安装状态，"两份 node_modules 互不重叠" 静默回退。
 */
export function isRootSuiteTestPath(rel: string): boolean {
  const p = rel.split('\\').join('/')
  return TEST_RE.test(p) && !SKIP_RE.test(p)
}

/** 独立 bun 项目（根套件不覆盖；各自的门禁跑自己的测试）。 */
const INDEPENDENT_PROJECTS = ['dashboard', 'userscript'] as const

/**
 * 改动**全部**落在同一组独立 bun 项目下的哪些项目（`[]` = 不是独立项目专属改动）。
 *
 * 空集不算（没改动≠只改独立项目）；混入任何根文件 ⇒ `[]`（仍要跑根套件）。
 */
export function independentProjects(changed: string[]): string[] {
  if (changed.length === 0) return []
  const hits = new Set<string>()
  for (const f of changed) {
    const top = f.split('\\').join('/').split('/')[0] ?? ''
    if (!(INDEPENDENT_PROJECTS as readonly string[]).includes(top)) return []
    hits.add(top)
  }
  return [...hits].sort()
}

/** 改动是否全部落在独立 bun 项目下（dashboard / userscript）。 */
export function isIndependentOnly(changed: string[]): boolean {
  return independentProjects(changed).length > 0
}

/**
 * 改动是否**全部**落在 `dashboard/` 下。
 *
 * 保留旧名：只认 dashboard（userscript 专属改动不等于 dashboard-only）。
 */
export function isDashboardOnly(changed: string[]): boolean {
  const projects = independentProjects(changed)
  return projects.length === 1 && projects[0] === 'dashboard'
}

/** Enumerate every root-suite test file (repo-relative, forward slashes). */
export function allTestFiles(cwd: string): string[] {
  const collect = (raw: string[]): string[] => raw.filter(isRootSuiteTestPath)
  try {
    const out = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard'], {
      windowsHide: true,
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
  /** If true, include heavy gate/integration sim tests that are skipped by default. */
  heavy?: boolean
  /**
   * 只跑「受本次改动影响的」测试文件（`bun test --changed[=ref]`，Bun ≥ 1.4）。
   *
   * 选择走 bun 的真实 **import 图**，比旧的 basename 映射精确得多（那个方向是欠采样，
   * 见文件头）。但它仍**只用于开发回路**：运行时读的文件（夹具 / 语料 / 权重）不在
   * import 图里，门禁与提交前的判定必须保持全量 —— 见 DECISIONS
   * §2026-10-26-goalnn-bun-native-sweep。
   */
  changed?: boolean | string
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

  // 0. `--changed` 快道：文件选择交给 bun 的 import 图，本文件不再自己枚举。
  const changedFlag =
    opts.changed === true
      ? '--changed'
      : typeof opts.changed === 'string' && opts.changed !== ''
        ? `--changed=${opts.changed}`
        : null

  // 1. Decide which test files to run.
  let files: string[] = []
  let mode: string
  let advisory = ''
  if (changedFlag) {
    const ref = changedFlag.slice('--changed'.length).replace(/^=/, '')
    mode = `changed (${ref === '' ? 'vs HEAD' : ref})`
  } else if (opts.files && opts.files.length) {
    files = opts.files
    mode = 'explicit'
  } else {
    const allTests = allTestFiles(cwd)
    const changed = gitChangedFiles(cwd)
    // §1.4 guard: a HEAVY_TESTS entry that matches no existing test file means
    // the exclusion list went stale (heavy test renamed/deleted) — the heavy
    // file would silently start running in every fast scoped pass. Enforced for
    // real by tests/test-silent-scope.test.ts; this line is just the visible hint.
    const staleHeavy = staleHeavyEntries(allTests)
    if (staleHeavy.length > 0) {
      advisory += ` ⚠ HEAVY_TESTS stale (no matching test file): ${staleHeavy.join(', ')}`
    }
    const projects = independentProjects(changed)
    if (projects.length > 0 && !process.env.BATTLE_TEST_FORCE_ALL) {
      // 独立 bun 项目专属改动：根套件与它们无关（见 SKIP_RE 注释），而且**绝不能**走
      // fallback 全量 —— 一个只改 dashboard/userscript 的提交会白烧一整轮根套件，撞上
      // 那些 spawn 真实 CLI 的慢测试。它们的门禁由自己承担（pre-commit 已挂）。
      const names = projects.map((p) => `${p}/`).join(' + ')
      return {
        ok: true,
        summary: `no relevant tests (${projects.join('+')}-only)`,
        detail:
          `${label}: ${changed.length} changed file(s) are all under ${names} — separate\n` +
          `  bun project(s) the root suite does not cover.\n` +
          `  Run their gates instead:  cd dashboard && bun run test  ·  cd userscript && bun run check\n` +
          `  Set BATTLE_TEST_FORCE_ALL=1 to run the root suite anyway.\n`,
      }
    } else if (
      changed.length > 0 &&
      changed.every(isTestInertFile) &&
      !process.env.BATTLE_TEST_FORCE_ALL
    ) {
      // 文档/notebook/课程配置改动 → 不 fallback 全量（见 isTestInertFile 注释）。
      return {
        ok: true,
        summary: 'no relevant tests (docs/config only)',
        detail:
          `${label}: ${changed.length} changed file(s) are docs / notebook / course-config ` +
          `only (md, ipynb, jsonc, .gitignore) — no TS or Python code path affected.\n` +
          `  Set BATTLE_TEST_FORCE_ALL=1 to run the full suite anyway.\n`,
      }
    } else {
      // 代码改动 → 全量（不再按 basename 窄跑：命中的方向是欠采样，理由见文件头）。
      files = allTests
      mode = changed.length ? `all (${changed.length} changed → full)` : 'all (clean tree)'
    }
  }

  // By default, exclude heavy gate/acceptance sims (see HEAVY_TESTS for the
  // criterion and the measured justification of each entry). They still run via
  // `bun test` or when `--heavy` is passed. Heavy tests are never filtered when
  // the caller passed explicit files (user intent).
  let skippedHeavy = 0
  if (!opts.heavy && !changedFlag && !(opts.files && opts.files.length)) {
    const before = files.length
    files = files.filter((f) => !isHeavyFile(f))
    skippedHeavy = before - files.length
  }
  if (skippedHeavy > 0) {
    mode += `, skipped ${skippedHeavy} heavy gate/integration test(s) (use --heavy)`
  }

  // Drop files that no longer exist (e.g. a deleted test file still in git diff).
  files = files.map((f) => f.replace(/\\/g, '/')).filter((f) => existsSync(resolve(cwd, f)))

  if (!changedFlag && files.length === 0) {
    return { ok: true, summary: 'no tests to run', detail: `${label}: no test files resolved\n` }
  }

  // 2. Run the selected tests once. `--parallel=<物理核数> --timeout=50000` are mandatory
  // (AGENTS §5): without `--parallel` all files share one process and
  // cross-file module state leaks surface as order-dependent failures.
  // worker 数显式给物理核数（2026-10-03）：裸 `--parallel` 的缺省 = 逻辑核（含超线程），
  // 16 worker 会把 Windows 提交上限顶穿 ⇒ worker 崩 + 连环 abort 的假红（见 tools/lib/cores.ts）。
  // 门禁口径不变：并行度 = 物理核、超时 50s、dashboard/ 由它自己的门禁跑。
  const gateArgs = [`--parallel=${physicalCores()}`, '--timeout=50000']
  const first = await spawnCapture(
    'bun',
    changedFlag
      ? ['test', changedFlag, ...gateArgs, '--path-ignore-patterns=dashboard/**']
      : ['test', ...gateArgs, ...files],
    cwd,
    timeoutMs,
  )
  // bun 把 `--changed: N changed file(s), running K/M test files` 打在输出里 —— 直接用它
  // 当选择面证据（不重新实现一遍 git/import 图推断）。
  if (changedFlag) {
    const sel = /(\d+) changed files?, running (\d+)\/(\d+) test files/.exec(first.output)
    if (sel) mode = `changed (${sel[1]} file → ${sel[2]}/${sel[3]} tests)`
  }
  let summary = extractSummary(first.output)
  const failures = parseFailures(first.output)
  if (changedFlag && first.code === 0 && summary === 'unknown') summary = '0 tests'

  if (first.code === 0 && failures.length === 0) {
    return { ok: true, summary: `${summary} [${mode}]${advisory}`, detail: '' }
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

// Direct CLI invocation: `bun tools/test-silent.ts [--heavy] [-- file ...]`.
const isMain: boolean = (import.meta as { main?: boolean }).main === true
if (isMain) {
  const argv = process.argv.slice(2)
  const heavy = argv.includes('--heavy')
  // `--changed` 可带 ref（`--changed=HEAD~1`）；`--` 只是分隔符，`bun run` 会把它剥掉。
  const changedArg = argv.find((a) => a === '--changed' || a.startsWith('--changed='))
  const changed =
    changedArg === undefined
      ? false
      : changedArg.slice('--changed'.length).replace(/^=/, '') || true
  // Collect non-flag positional args as explicit files. `--` is only a separator;
  // `bun run` strips it when invoked as `bun run test -- file`, so we must not
  // rely on it being present.
  const explicit = argv.filter(
    (a) => a !== '--' && a !== '--heavy' && a !== '--changed' && !a.startsWith('--changed='),
  )
  const result = await runSilentTest({
    cwd: CWD,
    label: 'local tests',
    files: explicit,
    heavy,
    changed,
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
