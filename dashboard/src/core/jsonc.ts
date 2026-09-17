/** jsonc.ts — JSONC 子集解析（`//` 行注释 + 尾逗号）。
 *
 *  **权威实现是 python 侧 `nn-training/rl/jsonc.py`**（训练栈读同一批课程/账本文件）；
 *  本文件是它的逐条移植，两侧语义必须一致——改任一侧都要同步改另一侧。
 *
 *  语义（与 python 版逐条对齐）：
 *   - `//` 注释**在任意位置**都被剥离（行首、**行内**都算）；
 *   - **字符串字面量内的 `//` 原样保留**（跟踪反斜杠转义的奇偶，URL 一类值不被吃掉）；
 *   - 剥离注释时**保留换行** ⇒ JSON 报错的行号不漂移（定位准确）；
 *   - **不支持块注释**（`/*` 开头那种）：遇到按普通字符处理，随后 `JSON.parse` 会响亮
 *     报错，而不是静默误剥（块注释与行注释的嵌套歧义被整体消除）；
 *   - 尾逗号（`, }` / `, ]`，仅字符串外）删除——JSONC 惯例兜底。
 *
 *  为什么单独成模块（2026-09-17 事故）：`stack/courses.ts` 与 `server/api/curriculum.ts`
 *  各自手搓过一版「只剥整行 `//`」的弱实现。`curricula/x1-rebirth-a2.jsonc` 里
 *  `"lr": 0.0005, // …` 这种**行内**注释让弱实现抛 SyntaxError，`resolveCourseBc`
 *  于是静默回退 legacy 硬编码路径，最终报出「初始权重缺失且 BC 产物不存在:
 *  tmp/ep60/…」——错误信息指向完全无关的文件，真因（课程文件读不了）被藏起来。
 *  **同一解析器只留这一份，别再手搓。**
 */

import { readFileSync } from 'fs'

/** 剥离 `//` 行注释；字符串内的 `//` 原样保留（保留换行，行号不漂移）。 */
export function stripComments(src: string): string {
  const out: string[] = []
  const n = src.length
  let i = 0
  let inStr = false
  let quote = ''
  let esc = false
  while (i < n) {
    const c = src[i]!
    if (inStr) {
      out.push(c)
      if (esc) esc = false
      else if (c === '\\') esc = true
      else if (c === quote) inStr = false
      i += 1
      continue
    }
    // 与 python 版一致：单引号也当字符串起始（JSON 不允许，随后 JSON.parse 自会报错；
    // 保留它只为两侧行为逐条相同）
    if (c === '"' || c === "'") {
      inStr = true
      quote = c
      out.push(c)
      i += 1
      continue
    }
    if (c === '/' && i + 1 < n && src[i + 1] === '/') {
      // 吃到行尾（**不吞换行**：i 停在 '\n'，下一轮走进通用分支把它吐出）
      while (i < n && src[i] !== '\n') i += 1
      continue
    }
    out.push(c)
    i += 1
  }
  return out.join('')
}

/** 删除字符串外的尾逗号（`, }` / `, ]`）；非尾逗号原样保留。 */
export function dropTrailingCommas(src: string): string {
  const out: string[] = []
  const n = src.length
  let i = 0
  let inStr = false
  let esc = false
  while (i < n) {
    const c = src[i]!
    if (inStr) {
      out.push(c)
      if (esc) esc = false
      else if (c === '\\') esc = true
      else if (c === '"') inStr = false
      i += 1
      continue
    }
    if (c === '"') {
      inStr = true
      out.push(c)
      i += 1
      continue
    }
    if (c === ',') {
      let j = i + 1
      while (j < n && ' \t\r\n'.includes(src[j]!)) j += 1
      if (j < n && (src[j] === '}' || src[j] === ']')) {
        i += 1 // 尾逗号：丢弃
        continue
      }
    }
    out.push(c)
    i += 1
  }
  return out.join('')
}

/** JSONC 文本 → 值（`stripComments` + 去尾逗号 + `JSON.parse`）。语法错误**抛**，不吞。 */
export function parseJsonc(src: string): unknown {
  return JSON.parse(dropTrailingCommas(stripComments(src)))
}

/** 从文件读 JSONC（`.jsonc` / `.json` 皆可）。读失败/解析失败都抛（调用方决定措辞）。 */
export function readJsoncFile(file: string): unknown {
  return parseJsonc(readFileSync(file, 'utf-8'))
}
