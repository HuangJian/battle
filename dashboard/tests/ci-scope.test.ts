import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'

/**
 * `.github/workflows/dashboard.yml` 的**触发范围**不得落后于 dashboard 的真实 import。
 *
 * 背景（2026-09-15）：dashboard 是独立 bun 项目，只读消费仓根的 10 个模块。它原本
 * **没有任何 CI** —— 那些模块被改而三条防线同时失效（pre-commit 只在 staged 含
 * `dashboard/**` 时跑它、根套件 SKIP_RE 排除它、CI 里只有 nn-training.yml）。
 * 新 workflow 按「`dashboard/**` ∪ 它消费的仓根模块」触发；本测试就是让那份清单
 * 不能静默腐烂：**新增一个仓根 import 而没加进 paths ⇒ 这里红**，而它正好在
 * dashboard 门禁（bun run test）里跑。
 *
 * 允许清单比 import 宽（多触发只是浪费），但**不允许**指向不存在的文件（拼错的路径
 * 会永远不触发，等于白写）—— 所以下面还有一条存在性断言。
 */
const DASH = resolve(import.meta.dir, '..')
const ROOT = resolve(DASH, '..')
const WORKFLOW = resolve(ROOT, '.github/workflows/dashboard.yml')

const IMPORT_RE = /from\s+['"]([^'"]+)['"]/g
const EXTS = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']

function walk(dir: string, out: string[]): void {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = resolve(dir, e.name)
    if (e.isDirectory()) {
      if (e.name === 'node_modules' || e.name.startsWith('.')) continue
      walk(p, out)
    } else if (/\.tsx?$/.test(e.name)) {
      out.push(p)
    }
  }
}

/**
 * dashboard 代码 import 的**仓根**模块（仓库相对路径，含扩展名）。
 * 判定依据是解析后的真实位置：落在 `dashboard/` 之外的才算外部依赖。
 */
function repoDependencies(): string[] {
  const files: string[] = []
  for (const sub of ['src', 'tests', 'scripts']) {
    const dir = resolve(DASH, sub)
    if (existsSync(dir) && statSync(dir).isDirectory()) walk(dir, files)
  }

  const deps = new Set<string>()
  for (const file of files) {
    for (const m of readFileSync(file, 'utf8').matchAll(IMPORT_RE)) {
      const spec = m[1]
      if (!spec.startsWith('.')) continue // 只看相对 import
      const base = resolve(dirname(file), spec)
      if (base.startsWith(DASH + '/') && !existsSync(`${base}.ts`)) continue // 仍在 dashboard 内
      const hit = [base, ...EXTS.map((e) => base + e)].find(
        (c) => existsSync(c) && statSync(c).isFile(),
      )
      if (!hit) continue
      if (hit.startsWith(DASH + '/')) continue // dashboard 内部
      if (!hit.startsWith(ROOT + '/')) continue // 仓库之外（node_modules 等）
      deps.add(relative(ROOT, hit).split('\\').join('/'))
    }
  }
  return [...deps].sort()
}

/** workflow 里 `- '...'` 形式的路径模式（两个 paths: 块都在内）。 */
function workflowPatterns(): string[] {
  const text = readFileSync(WORKFLOW, 'utf8')
  const out = new Set<string>()
  for (const line of text.split('\n')) {
    const m = line.match(/^\s+-\s+'([^']+)'\s*$/)
    if (m && (m[1].includes('/') || m[1].startsWith('.'))) out.add(m[1])
  }
  return [...out].sort()
}

function covered(dep: string, patterns: string[]): boolean {
  return patterns.some((p) => {
    if (p === dep) return true
    if (p.endsWith('/**')) return dep.startsWith(p.slice(0, -3) + '/')
    if (p.endsWith('/*')) return dep.startsWith(p.slice(0, -2) + '/')
    return false
  })
}

describe('dashboard CI 触发范围', () => {
  const deps = repoDependencies()
  const patterns = workflowPatterns()

  it('解析出的仓根依赖是可信的、非空的（防假通过）', () => {
    expect(existsSync(WORKFLOW)).toBe(true)
    // 现有 10 个；给未来增长留空间，但不能少到说明解析坏了。
    expect(deps.length).toBeGreaterThanOrEqual(8)
    // 关键依赖必须在（这几条正是「改了会静默打断 dashboard」的那类）。
    expect(deps).toContain('src/config/stages.ts')
    expect(deps).toContain('tools/agent/codehash-files.ts')
    expect(deps).toContain('tools/sim/pack-container.ts')
  })

  it('每个仓根依赖都被 workflow 的 paths 覆盖', () => {
    const missing = deps.filter((d) => !covered(d, patterns))
    expect(missing).toEqual([])
  })

  it('paths 里的 src/tools 条目都真实存在（拼错的路径等于永远不触发）', () => {
    const checked = patterns.filter((p) => /^(src|tools)\//.test(p) && !p.includes('*'))
    expect(checked.length).toBeGreaterThan(0)
    const dangling = checked.filter((p) => !existsSync(resolve(ROOT, p)))
    expect(dangling).toEqual([])
  })
})
