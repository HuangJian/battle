import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  AGENTS_PATH,
  DETAILS_PATH,
  HARD_LIMIT,
  WARN_LIMIT,
  evaluate,
  measureChars,
} from '../tools/check-agents-budget'

/**
 * `AGENTS.md` 注入预算门禁（`tools/check-agents-budget.ts`，2026-09-23）。
 *
 * 规则本身：文件必须整份短于 harness 的注入截断点（≈9,205 字符，2026-09-10 实测），
 * 否则 §5 之后的规则对 agent 不可见（根因分析见 `docs/agents.details.md` §0.1）。
 * 门禁由 `bun run check` 与 pre-commit 调用；这里钉住**门禁自己不会静默失效**的四件事：
 *   ① 口径是字符不是字节（按字节判会在同一份文件上假红 ~65%，诱人去调大上限）；
 *   ② 文件真的在预算内，且不是靠删规则变短的（下限守卫）；
 *   ③ 超预算判红 / 接近上限告警的行为；
 *   ④ 接线不可漂 —— `bun run check` 与 pre-commit 都调它，且 hook 带文件归因。
 */
const ROOT = resolve(import.meta.dir, '..')
const agentsText = readFileSync(resolve(ROOT, AGENTS_PATH), 'utf8')
const detailsText = readFileSync(resolve(ROOT, DETAILS_PATH), 'utf8')
const pkg = JSON.parse(readFileSync(resolve(ROOT, 'package.json'), 'utf8')) as {
  scripts: Record<string, string>
}
const HOOK = readFileSync(resolve(ROOT, 'tools/githook/pre-commit'), 'utf8')

/** pre-commit 里的预算块（横幅注释到下一个横幅之间）——只在那一块里断言。 */
const HOOK_BLOCK_RE = /# -+ AGENTS\.md[\s\S]*?(?=\n# -+ )/
/** 真实调用（排除注释行里可能出现的示例）。 */
const HOOK_CALL_RE = /^(?!\s*#).*agents-budget/m

describe('AGENTS.md 注入预算', () => {
  it('① 量的是字符而不是字节（CJK 3 字节/字符，按字节判会假红）', () => {
    const chars = measureChars(agentsText)
    const bytes = Buffer.byteLength(agentsText, 'utf8')
    expect(chars).toBeGreaterThan(0) // 防读空文件导致后面全是空断言
    expect(bytes).toBeGreaterThan(chars) // 文件确实含多字节字符（口径之争的前提）
    // 现状就是最好的反例：按字节算这份文件「超预算」⇒ 那样判会诱人去调大 HARD_LIMIT
    expect(bytes).toBeGreaterThan(HARD_LIMIT)
    // CRLF 归一：注入副本不会保留 \r，按工作树 CRLF 计数会每行多算 1
    expect(measureChars(agentsText.replace(/\n/g, '\r\n'))).toBe(chars)
  })

  it('② 当前文件在预算内，且不是靠删规则变短的', () => {
    const { chars, errors } = evaluate(agentsText, detailsText)
    expect(errors).toEqual([]) // 结构（§0–§17）+ 预算都过
    expect(chars).toBeLessThanOrEqual(HARD_LIMIT)
    // 下限守卫：把 AGENTS.md 砍成一小段也能「过预算」，但那不是修法（搬迁才是）
    expect(chars).toBeGreaterThan(4_000)
  })

  it('③ 超预算判红、接近上限只告警（evaluate 行为，含修法指引）', () => {
    const over = evaluate(agentsText + 'x'.repeat(HARD_LIMIT), detailsText)
    expect(over.errors.some((e) => e.includes('硬上限'))).toBe(true)
    expect(over.errors.some((e) => e.includes('不可见'))).toBe(true) // 说清后果，不只是数字

    const near = evaluate(
      // 只在 WARN 与 HARD 之间：章节结构保持完好，于是只有告警没有错误
      agentsText + 'x'.repeat(WARN_LIMIT + 1 - measureChars(agentsText)),
      detailsText,
    )
    expect(near.errors).toEqual([])
    expect(near.warnings.length).toBe(1)
    expect(near.warnings[0]).toContain('搬家')
  })

  it('④ 接线不可漂：check 脚本与 pre-commit 都调用它，且 hook 带文件归因', () => {
    expect(pkg.scripts.check).toContain('tools/check-agents-budget.ts')
    expect(pkg.scripts['budget:check']).toBe('bun tools/check-agents-budget.ts')

    const block = HOOK_BLOCK_RE.exec(HOOK)?.[0] ?? ''
    expect(block).not.toBe('') // 防「横幅被改名 → 断言变空转」的假绿
    expect(HOOK_CALL_RE.test(block)).toBe(true) // 真的调用，不只是注释
    // 归因语义：超预算且 AGENTS.md 在暂存清单里 ⇒ 拦；只在他人未暂存改动里 ⇒ 告警放行
    expect(block).toContain('STAGED_LIST')
    expect(block).toContain('exit 1')
  })
})
