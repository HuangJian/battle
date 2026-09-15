import { describe, expect, it } from 'bun:test'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'

/**
 * 确定性签名的作用域不变量 —— freeze 门禁触发收窄的**安全前提**（2026-09-15）。
 *
 * `tools/githook/pre-commit` 的 freeze 触发把 `tests/**` 与 `src/assets/**` 排除在外，
 * 理由是「它们不可能移动 det 签名」。豁免本身是省 ~100s/次的好事，但**必须有东西在
 * 检查这个理由**：签名文本由 `tools/probe-det-baseline.sh` 跑
 * `bun tools/diag/per-seed-diff.ts dump` 生成，所以只要有一天有人把探针（或它 import
 * 的东西）接到 `tests/` 或 `src/assets/`，豁免就变成「改测试/改图片绕过签名门禁」的
 * 漏洞——而门禁自己不会发现。
 *
 * 本测试从探针入口算**仓库内相对 import 的传递闭包**，断言闭包与这两个目录零交集。
 * 它也是防「假通过」的：闭包太小或没有 src/ 成员 ⇒ 解析坏了 ⇒ 直接红。
 */
const ROOT = resolve(import.meta.dir, '..')
/** tools/probe-det-baseline.sh 里 `bun tools/diag/per-seed-diff.ts dump ...` 的入口。 */
const ENTRY = 'tools/diag/per-seed-diff.ts'
/** freeze 触发被豁免的两个目录前缀（pre-commit 的 FREEZE_STAGED 里）。 */
const EXEMPT_PREFIXES = ['tests/', 'src/assets/']

const IMPORT_RE = /from\s+['"]([^'"]+)['"]/g
const EXTS = ['.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs']

/** 把仓库内相对 specifier 解析成存在的文件（相对 import 之外的一律忽略）。 */
function resolveSpecifier(fromRel: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null
  const base = resolve(ROOT, dirname(fromRel), spec)
  const candidates = [
    base,
    ...EXTS.map((e) => base + e),
    ...EXTS.map((e) => resolve(base, `index${e}`)),
  ]
  for (const cand of candidates) {
    if (existsSync(cand) && statSync(cand).isFile()) {
      return relative(ROOT, cand).split('\\').join('/')
    }
  }
  return null
}

/** 探针入口的仓库内 import 传递闭包（含入口自身）。 */
function signatureClosure(entry: string): Set<string> {
  const seen = new Set<string>()
  const queue = [entry]
  while (queue.length) {
    const cur = queue.pop()!
    if (seen.has(cur)) continue
    if (!existsSync(resolve(ROOT, cur))) continue
    seen.add(cur)
    const text = readFileSync(resolve(ROOT, cur), 'utf8')
    for (const m of text.matchAll(IMPORT_RE)) {
      const resolved = resolveSpecifier(cur, m[1])
      if (resolved && !seen.has(resolved)) queue.push(resolved)
    }
  }
  return seen
}

describe('freeze 触发豁免 —— 签名闭包不得触及被豁免的目录', () => {
  const closure = signatureClosure(ENTRY)

  it('闭包非空且真的解析到了仓库内文件（防假通过的健全性检查）', () => {
    // 入口 + 它 import 的 src/** 与 tools/lib/** 应有一二十个成员。
    expect(closure.has(ENTRY)).toBe(true)
    expect(closure.size).toBeGreaterThan(8)
    expect([...closure].some((f) => f.startsWith('src/'))).toBe(true)
    expect([...closure].some((f) => f.startsWith('tools/'))).toBe(true)
  })

  it('闭包与 tests/、src/assets/ 零交集（这是 FREEZE_STAGED 豁免成立的充要理由）', () => {
    const offenders = [...closure].filter((f) => EXEMPT_PREFIXES.some((p) => f.startsWith(p)))
    expect(offenders).toEqual([])
  })

  it('闭包里每个成员都会触发 freeze（即：豁免只减掉那两类文件，没有连签名本体一起减掉）', () => {
    const TS_RE = /\.(ts|tsx|js|jsx|mjs|cjs)$/
    const notTriggering = [...closure].filter((f) => !TS_RE.test(f) && !f.startsWith('src/'))
    expect(notTriggering).toEqual([])
  })
})
