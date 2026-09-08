#!/usr/bin/env bun
/**
 * DECISIONS.md 决策治理校验（plan/decisions-governance.md P3，2026-09-08 落地）
 *
 * 四条校验：
 *  1. 编号集合不变式 —— 编号 multiset 与 tools/decisions-baseline.json 完全一致：
 *     任一编号消失或数量减少（误删历史契约，外部引用断链）=> fail；
 *     任一编号数量多于基线（新撞号）=> fail。
 *  2. 越界告警 —— 单条正文 > 40 行（明显该搬 progress 文档了）=> 告警（不 fail）。
 *  3. 新条目格式 —— 不在基线中的新编号必须形如 §YYYY-MM-DD-<branch>-<slug> => 否则 fail。
 *  4. 头部可解析 —— 无法提取编号的 `## ` 行 => 告警。
 *
 * 用法：
 *   bun tools/check-decisions.ts              # 校验（默认）
 *   bun tools/check-decisions.ts --write-baseline  # 以当前 DECISIONS.md 重写基线
 *   bun tools/check-decisions.ts --quiet      # 只输出错误/失败，不输出提示
 */
import { resolve } from 'node:path'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'

/** 日期 ID：§2026-09-08-<branch>-<slug>（branch 去连字符，英文小写连字符 3-4 词） */
const DATE_ID_RE = /^\d{4}-\d{2}-\d{2}-[a-z0-9-]+$/
/**
 * 标题内条目标识提取（顺序重要）：日期 ID 优先（可带「（日期，来源）」后缀），
 * 其次旧契约 ID（§340 / §193-A / §114.1 / §90b / §75）。
 */
const HEADER_KEY_RE = /^(\d{4}-\d{2}-\d{2}-[a-z0-9-]+|\d+(?:\.\d+)?(?:[A-Za-z]|-[A-Za-z0-9]+)?)/
/** 单条正文行数上限（超过=该搬 progress 文档） */
const BODY_LINE_LIMIT = 40

/** 仓库根 = 调用目录（bun run check 从仓库根执行；被别的目录调用会先失败在存在性守卫） */
const ROOT = process.cwd()
const DECISIONS_PATH = resolve(ROOT, 'DECISIONS.md')
const BASELINE_PATH = resolve(ROOT, 'tools', 'decisions-baseline.json')

interface Baseline {
  version: number
  capturedAt: string
  lineCount: number
  /** 编号 -> 出现次数（§293/§354/§355/§361 等已知撞号以出现次数 >1 记录） */
  entries: Record<string, number>
}

/** 从一行 `## ` 提取条目编号；解析不出返回 null */
function headerKey(line: string): string | null {
  const rest = line.replace(/^##\s*/, '').replace(/^§/, '')
  const m = rest.match(HEADER_KEY_RE)
  return m ? m[1] : null
}

function parseDecisions(text: string): {
  counts: Map<string, number>
  overlong: string[]
  unparsed: string[]
} {
  const counts = new Map<string, number>()
  const overlong: string[] = []
  const unparsed: string[] = []
  const lines = text.split(/\r?\n/)

  let currentKey: string | null = null
  let bodyLen = 0
  for (const line of lines) {
    const isHeader = /^##\s/.test(line)
    if (isHeader) {
      const key = headerKey(line)
      if (key) {
        currentKey = key
        counts.set(key, (counts.get(key) ?? 0) + 1)
        bodyLen = 0
      } else {
        currentKey = null
        unparsed.push(line.slice(0, 80))
      }
      continue
    }
    if (line.trim() !== '') bodyLen += 1
    if (currentKey && bodyLen > BODY_LINE_LIMIT) {
      overlong.push(currentKey)
      bodyLen = 0 // 每个 key 只记一次
    }
  }
  return { counts, overlong, unparsed }
}

function loadBaseline(): Baseline | null {
  try {
    if (!existsSync(BASELINE_PATH)) return null
    return JSON.parse(readFileSync(BASELINE_PATH, 'utf8')) as Baseline
  } catch {
    return null
  }
}

function main() {
  const quiet = Bun.argv.includes('--quiet')
  const writeBaseline = Bun.argv.includes('--write-baseline')

  if (!existsSync(DECISIONS_PATH)) {
    console.error('[check-decisions] DECISIONS.md 不存在——请从仓库根目录运行（如 bun run check）')
    process.exit(1)
  }
  const decisionsText = readFileSync(DECISIONS_PATH, 'utf8')
  const { counts, overlong, unparsed } = parseDecisions(decisionsText)

  if (writeBaseline) {
    const entries: Record<string, number> = {}
    for (const [k, n] of [...counts.entries()].sort((a, b) =>
      a[0].localeCompare(b[0], 'en', { numeric: true }),
    )) {
      entries[k] = n
    }
    const baseline: Baseline = {
      version: 1,
      capturedAt: new Date().toISOString(),
      lineCount: decisionsText.split(/\r?\n/).length,
      entries,
    }
    writeFileSync(BASELINE_PATH, `${JSON.stringify(baseline, null, 2)}\n`)
    console.log(
      `baseline written: ${Object.keys(entries).length} distinct ids, ${decisionsText.split(/\r?\n/).length} lines`,
    )
    return
  }

  const baseline = loadBaseline()
  let failed = false

  if (!baseline) {
    console.error(
      '[check-decisions] MISSING baseline — 先运行: bun tools/check-decisions.ts --write-baseline',
    )
    process.exit(1)
  }

  // 1. 编号集合不变式 + 新撞号
  const baseEntries = baseline.entries
  const added: string[] = []
  const missing: string[] = []
  const newDups: string[] = []
  const allKeys = new Set<string>([...Object.keys(baseEntries), ...counts.keys()])
  for (const key of allKeys) {
    const before = baseEntries[key] ?? 0
    const now = counts.get(key) ?? 0
    if (now === 0) missing.push(key)
    else if (now > before) {
      if (before === 0) added.push(key)
      else newDups.push(`${key} (基线 ${before} → 现在 ${now})`)
    }
  }
  if (missing.length) {
    failed = true
    console.error(`[check-decisions] FAIL 编号丢失（外部引用断链）: ${missing.join(', ')}`)
  }
  if (newDups.length) {
    failed = true
    console.error(`[check-decisions] FAIL 新撞号: ${newDups.join(', ')}`)
  }

  // 3. 新条目格式
  const badFormat: string[] = []
  for (const key of added) {
    if (!DATE_ID_RE.test(key)) badFormat.push(key)
  }
  if (badFormat.length) {
    failed = true
    console.error(
      `[check-decisions] FAIL 新条目未用日期 ID（应形如 §YYYY-MM-DD-<branch>-<slug>）: ${badFormat.join(', ')}`,
    )
  }

  // 2. 越界告警（不 fail）
  if (overlong.length)
    console.warn(
      `[check-decisions] warn 正文超 ${BODY_LINE_LIMIT} 行（该搬 progress 文档）: ${[...new Set(overlong)].join(', ')}`,
    )
  if (unparsed.length)
    console.warn(`[check-decisions] warn 无法解析编号的 ## 行: ${unparsed.join(' | ')}`)

  if (added.length && !badFormat.length && !quiet) {
    console.log(`[check-decisions] ok 新条目（日期 ID）: ${added.join(', ')}`)
  }
  if (!quiet) {
    console.info(
      `[check-decisions] ${counts.size} distinct ids / ${[...counts.values()].reduce((a, b) => a + b, 0)} entries / ${decisionsText.split(/\r?\n/).length} lines`,
    )
  }

  if (failed) process.exit(1)
  console.log('[check-decisions] ok')
}

main()
