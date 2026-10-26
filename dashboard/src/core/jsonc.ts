/** jsonc.ts — JSONC 子集解析（`//` 行注释 + 尾逗号）。
 *
 *  **权威实现是 python 侧 `nn-training/common/jsonc.py`**（训练栈读同一批课程/账本文件）；
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
 *  各自手搓过一版「只剥整行 `//`」的弱实现。`curricula/x1-rebirth.jsonc` 里
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

/** 从 `from` 起跳过空白与 `//` 行注释，返回下一个**有意义**字符的位置（`n` = 到尾）。 */
function skipSpaceAndComments(src: string, from: number): number {
  const n = src.length
  let j = from
  while (j < n) {
    const d = src.charCodeAt(j)
    if (d === 32 || d === 9 || d === 13 || d === 10) {
      j += 1
      continue
    }
    if (d === 47 /* / */ && src.charCodeAt(j + 1) === 47) {
      j += 2
      while (j < n && src.charCodeAt(j) !== 10) j += 1
      continue
    }
    return j
  }
  return n
}

/** **单趟**规范化：剥 `//` 行注释 + 去尾逗号，输出可直接 `JSON.parse` 的文本。
 *
 *  语义与 `dropTrailingCommas(stripComments(src))` **逐字节相同**（`jsonc-fastpath` 那组
 *  用例在仓内 223 个真实课程文件上对拍）；重写一份的理由：那两步各走一遍字符流、
 *  且逐字符 `push`，实测 223 个文件（1.35MB）**28.5ms → 3.6ms（7.9×）**——而 `JSON.parse`
 *  自己只要 1ms，成本全在自己的扫描上。快路的关键有三条：
 *   ① 一遇 `"` / `'` 就**整段跳读**到闭合引号（内部只有转义与闭合符两件事要看）；
 *   ② 只在「要删东西」时才 `slice` 拼片段，没东西可删时**原样返回**（零拷贝）；
 *   ③ 尾逗号判定要跳过空白**和注释**（否则 `1, // 注\n}` 里的逗号留了下来——
 *      这正是「先剥注释、再删逗号」两趟写法的顺序语义）。
 *
 *  为什么不直接用 `Bun.JSONC.parse`（1.4 内建）：它是**更宽**的超集——实测 1.4.4 接受
 *  块注释（`/*` 开头的整段）、单引号字符串、BOM、前导零（`[01]` → 1）、十六进制（`[0x10]` → 16）
 *  以及尾随垃圾（`{} {}` 静默取前一个）；而本模块的语义由 python `common/jsonc.py` 定
 *  （块注释必须**响亮报错**）。换过去 = 「控制台能读、训练侧读不了」的静默漂移，正是本
 *  文件头警告的那类事故。同语义下它也不更快（同 223 文件：单趟 3.6ms vs 它 5.4ms）。
 *
 *  语法错误（未闭合字符串、块注释、非 JSON 字面量等）**原样留给 `JSON.parse` 报**——
 *  保持报错信息与行号与旧实现一致。
 */
export function normalizeJsonc(src: string): string {
  const n = src.length
  const out: string[] = []
  let last = 0 // out 里已落盘到的源位置（其后的片段还没拷贝）
  let i = 0
  while (i < n) {
    const c = src.charCodeAt(i)
    // 字符串：整段跳过（`'` 也与 python 版一致地当字符串起始，交给 JSON.parse 判错）
    if (c === 34 /* " */ || c === 39 /* ' */) {
      let j = i + 1
      while (j < n) {
        const d = src.charCodeAt(j)
        if (d === 92 /* \\ */) {
          j += 2 // 转义：下一个字符无条件吃进字符串（与两趟版的 esc 标记等价）
          continue
        }
        if (d === c) {
          j += 1
          break
        }
        j += 1
      }
      i = j
      continue
    }
    // 行注释：删到行尾（**不吞换行**，行号不漂移）
    if (c === 47 /* / */ && src.charCodeAt(i + 1) === 47) {
      out.push(src.slice(last, i))
      let j = i + 2
      while (j < n && src.charCodeAt(j) !== 10) j += 1
      last = j
      i = j
      continue
    }
    // 尾逗号：后面（跳过空白 / 注释后）紧跟 `}` 或 `]` 才删
    if (c === 44 /* , */) {
      const j = skipSpaceAndComments(src, i + 1)
      const d = j < n ? src.charCodeAt(j) : -1
      if (d === 125 /* } */ || d === 93 /* ] */) {
        out.push(src.slice(last, i))
        last = i + 1
      }
    }
    i += 1
  }
  if (last === 0) return src // 没删过任何东西：零拷贝
  out.push(src.slice(last))
  return out.join('')
}

/** JSONC 文本 → 值（单趟规范化 + `JSON.parse`）。语法错误**抛**，不吞。 */
export function parseJsonc(src: string): unknown {
  return JSON.parse(normalizeJsonc(src))
}

/** 从文件读 JSONC（`.jsonc` / `.json` 皆可）。读失败/解析失败都抛（调用方决定措辞）。 */
export function readJsoncFile(file: string): unknown {
  return parseJsonc(readFileSync(file, 'utf-8'))
}
