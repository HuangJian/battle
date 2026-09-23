#!/usr/bin/env bun
/**
 * AGENTS.md 注入预算门禁 —— 防「规则文件静静长回不可读的长度」（2026-09-23）
 *
 * 背景：harness 注入 `AGENTS.md` 时会**截断**。2026-09-10 实测（详见
 * `docs/agents.details.md` §0.1）：旧文件 35,407 字符，注入副本只到 ≈26% ⇒ 切口落在
 * **9,205 字符、文件内 §4 中间**，于是 §5 之后（硬规则 / 测试 / 门禁 / 资产 / 性能 /
 * 长任务纪律 / Windows 拼接）**整段对 agent 不可见**——「规矩被读一半」才是规则不被
 * 遵守的根因，不是规则质量。2026-09-23 把文件压到 ≤9.0K（规则一条未删，解释搬进
 * `docs/agents.details.md` 同号 §）；本门禁让那次瘦身**不会随时间回退**。
 *
 * 两条不变量：
 *  1. **预算** —— `AGENTS.md` 的字符数 ≤ `HARD_LIMIT`，超了直接红（否则 §5 之后再次读不到）；
 *     接近 `WARN_LIMIT` 时告警：再有新规则就该**搬家**而不是继续追加。
 *     ⚠ 量的是**字符**不是字节（CJK 1 字符 = 3 字节）——按字节判会在同一份文件上假红 ~65%。
 *  2. **结构** —— 两文件都必须含 §0–§17（AGENTS 用 `## N.`，details 用 `## §N`）。
 *     丢一节不是「少说一句」，是**外部引用断链 + 规则静默消失**，所以是错误不是告警。
 *
 * 用法：
 *   bun tools/check-agents-budget.ts            # 校验（`bun run check` 与 pre-commit 都调它）
 *   bun tools/check-agents-budget.ts --verbose  # 另打印每节字符分布（找该搬哪一节）
 *   bun tools/check-agents-budget.ts --quiet    # 只在有问题时输出
 */
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * 硬上限（字符）。= 实测注入截断点 ≈9,205（35,407 的 26%）向下取整到百位，
 * 留 ~5 字符容差；**不要**为了塞进一条新规则而调大它 —— 截断点不由我们决定。
 */
export const HARD_LIMIT = 9_200
/** 预警线：余量 ≤100 字符时提醒「下一条规则要搬家，不要追加」。 */
export const WARN_LIMIT = 9_100
/** 结构契约的上界节号（§0–§17）。 */
export const SECTION_MAX = 17
export const AGENTS_PATH = 'AGENTS.md'
export const DETAILS_PATH = 'docs/agents.details.md'
/** 报错时指向的规则解释 */
const DETAILS_DOC = `${DETAILS_PATH} §0.1`

export interface BudgetResult {
  /** 规范化换行后按**字符**计的正文长度 */
  chars: number
  hardLimit: number
  warnLimit: number
  errors: string[]
  warnings: string[]
}

/**
 * 字符口径的度量：先把 CRLF 归一成 LF 再数。
 * 注入的副本不会保留 `\r`，若按工作树的 CRLF 计数，Windows 检出会比实际多算一节
 * 的字符数（每行 +1）——那种假红会诱使人去调 `HARD_LIMIT`，正好毁掉这个门禁。
 */
export function measureChars(text: string): number {
  return text.replace(/\r\n/g, '\n').length
}

/** 某节（`## ` 标题）在文件里的字符数；用于 `--verbose` 指出该搬哪一节。 */
export function sectionChars(
  text: string,
  headingPrefix: string,
): Array<{ section: string; chars: number }> {
  const normalized = text.replace(/\r\n/g, '\n')
  const lines = normalized.split('\n')
  const out: Array<{ section: string; chars: number }> = []
  let current: { section: string; chars: number } | null = null
  for (const line of lines) {
    if (line.startsWith(headingPrefix)) {
      current = { section: line.replace(/^#+\s*/, '').slice(0, 42), chars: 0 }
      out.push(current)
      continue
    }
    if (current) current.chars += line.length + 1
  }
  return out
}

/** 缺失的节号（AGENTS `## N.` / details `## §N`）。 */
export function missingSections(text: string, headingPrefix: string): number[] {
  const missing: number[] = []
  for (let n = 0; n <= SECTION_MAX; n++) {
    const re = new RegExp(`^${headingPrefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}${n}\\b`, 'm')
    if (!re.test(text)) missing.push(n)
  }
  return missing
}

/** 纯函数：给两份正文，返回预算 + 结构的判定结果（CLI 与单测共用同一口径）。 */
export function evaluate(agents: string, details: string): BudgetResult {
  const errors: string[] = []
  const warnings: string[] = []
  const chars = measureChars(agents)

  if (chars === 0) errors.push(`${AGENTS_PATH} 为空（读错文件？）`)
  if (chars > HARD_LIMIT) {
    errors.push(
      `${AGENTS_PATH} ${chars} 字符 > 硬上限 ${HARD_LIMIT}` +
        `（注入截断点 ≈9,205 字符；超了 ⇒ §5 之后的规则再次对 agent 不可见）`,
    )
  } else if (chars > WARN_LIMIT) {
    warnings.push(
      `${AGENTS_PATH} ${chars} 字符，距硬上限 ${HARD_LIMIT} 只剩 ${HARD_LIMIT - chars} 字符` +
        ` —— 下一条新规则请**搬家**（解释进 ${DETAILS_PATH} 同号 §）而不是追加`,
    )
  }

  const agentsMissing = missingSections(agents, '## ')
  if (agentsMissing.length > 0) {
    errors.push(
      `${AGENTS_PATH} 缺节：${agentsMissing.map((n) => `§${n}`).join(' ')}（规则不得静默消失）`,
    )
  }
  const detailsMissing = missingSections(details, '## §')
  if (detailsMissing.length > 0) {
    errors.push(
      `${DETAILS_PATH} 缺节：${detailsMissing.map((n) => `§${n}`).join(' ')}` +
        `（编号是两文件共享契约，AGENTS 的每个 § 都要有解释落点）`,
    )
  }

  return { chars, hardLimit: HARD_LIMIT, warnLimit: WARN_LIMIT, errors, warnings }
}

const HOW_TO_FIX = [
  '修法（不要靠抠字眼，要搬家）：',
  `  · 解释 / 事故史 / 配方 / 枚举 → ${DETAILS_DOC} 的 Relocation contract：` +
    '指令留 AGENTS，缘由与配方进 docs/agents.details.md 同号 §',
  '  · 新增规则前先在 AGENTS 里找一段可搬走的内容腾出空间（旧版全文见 git ccf49ff^）',
  `  · 看清哪一节最肥：bun tools/check-agents-budget.ts --verbose`,
  '  · **不许**调大 HARD_LIMIT —— 截断点由 harness 决定，不由本仓库决定',
].join('\n')

function main(): number {
  const argv = process.argv.slice(2)
  const quiet = argv.includes('--quiet')
  const verbose = argv.includes('--verbose')
  const root = process.cwd()
  const agentsPath = resolve(root, AGENTS_PATH)
  const detailsPath = resolve(root, DETAILS_PATH)

  for (const p of [agentsPath, detailsPath]) {
    if (!existsSync(p)) {
      console.error(`[agents-budget] ${p} 不存在——请从仓库根目录运行（如 bun run check）`)
      return 1
    }
  }

  const agents = readFileSync(agentsPath, 'utf8')
  const details = readFileSync(detailsPath, 'utf8')
  const result = evaluate(agents, details)

  if (verbose) {
    console.log('[agents-budget] AGENTS.md 每节字符数（找最肥的一节搬走）：')
    for (const s of sectionChars(agents, '## ')) {
      console.log(`  ${String(s.chars).padStart(5)}  ${s.section}`)
    }
  }

  for (const w of result.warnings) console.warn(`[agents-budget] warn ${w}`)
  if (result.errors.length > 0) {
    for (const e of result.errors) console.error(`[agents-budget] FAIL ${e}`)
    console.error(HOW_TO_FIX)
    return 1
  }
  if (!quiet) {
    console.log(
      `[agents-budget] ok ${AGENTS_PATH} ${result.chars}/${result.hardLimit} 字符` +
        `（注入截断点 ≈9,205；结构 §0–§${SECTION_MAX} 齐）`,
    )
  }
  return 0
}

if (import.meta.main) {
  process.exit(main())
}
