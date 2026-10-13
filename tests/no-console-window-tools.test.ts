/**
 * no-console-window-tools.test.ts —— `tools/**` 的子进程调用必须显式带 `windowsHide`
 * （2026-10-13 用户二次报障「启动 trainer/hub 还是有三个黑窗闪过」）。
 *
 * 分层：`tools/**`（CLI / 库 / 门禁工具）。dashboard 那一半是
 * `dashboard/tests/no-console-window.test.ts`（同一纪律，不同扫描面）。
 *
 * **机制**（本机实测，2026-10-13）：
 *   · `windowsHide: true` / `CREATE_NO_WINDOW` 出来的进程**没有控制台**（GetConsoleWindow()=0）；
 *   · `detached: true` 同样没有控制台；
 *   · 于是「无控制台的父进程 + 未带旗标的 console 子进程」= Windows **新建一块可见控制台窗口**。
 *   实测矩阵（父=detached 无控制台）：孙 spawn 不带旗标 ⇒ visible=true；带旗标 ⇒ 无控制台。
 *   ⇒ 黑窗真身不必是直接子进程：训练组件（都是 `spawnBg` detached 出来的）再往下生的
 *   **孙子进程**照样弹窗，这就是 10:45 那次启动的三处：`remote/code_snapshot._git_head` 的
 *   `git`、`common/instance_lock._win_cmdline_cim` 的 `pwsh`（启动期锚核验）、以及本目录下
 *   `tools/lib/node-upgrade` 的 `git`。
 *
 * 扫描是**文本层**的（剥注释与字符串字面量 → 括号配平取实参），故：
 *   · 报错文案里的 `spawn (` 不算（字符串已整体剥掉 —— level-gen 踩过）；
 *   · 方法/函数**声明**（`private spawn(): Worker {`）不算（收括号后接 `:`/`{` 的一律跳过）；
 *   · 空实参 `()` 不算；
 *   · `spawn(...)` 这种**完全没有 options** 的形态照样算违规（本次病灶形状）。
 */

import { describe, expect, it } from 'bun:test'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const TOOLS = join(import.meta.dir, '..', 'tools')
const CALL = /(?<![\w.$])(?:Bun\.)?(?:spawn|spawnSync|execFileSync|execSync|exec)\s*\(/g

/** 已知豁免（**逐条带理由，不是通配**）：注入式 runner 的**调用点**——`windowsHide` 加在
 *  默认实现里（`deps.spawn ?? (argv, input, cwd) => Bun.spawnSync({ windowsHide: true, … })`），
 *  调用点只是把参数递给它，第 4 个参数会被忽略。 */
const EXEMPT: string[] = ['opts.repoRoot']

function walk(dir: string, out: string[]): string[] {
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name)
    if (ent.isDirectory()) walk(p, out)
    else if (ent.name.endsWith('.ts') && !ent.name.endsWith('.d.ts')) out.push(p)
  }
  return out
}

/** 剥注释与字符串字面量（等长空格替换 ⇒ 偏移/行号不变）。 */
function stripCommentsAndStrings(src: string): string {
  const out: string[] = []
  let i = 0
  while (i < src.length) {
    const c = src[i]!
    const next = src[i + 1] ?? ''
    if (c === '/' && next === '/') {
      const j = src.indexOf('\n', i)
      const end = j < 0 ? src.length : j
      out.push(src.slice(i, end).replace(/[^\n]/g, ' '))
      i = end
      continue
    }
    if (c === '/' && next === '*') {
      const j = src.indexOf('*/', i + 2)
      const end = j < 0 ? src.length : j + 2
      out.push(src.slice(i, end).replace(/[^\n]/g, ' '))
      i = end
      continue
    }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c
      const start = i
      i++
      while (i < src.length) {
        if (src[i] === '\\') {
          i += 2
          continue
        }
        if (src[i] === quote) {
          i++
          break
        }
        i++
      }
      out.push(src[start] + src.slice(start + 1, i - 1).replace(/[^\n]/g, ' ') + (src[i - 1] ?? ''))
      continue
    }
    out.push(c)
    i++
  }
  return out.join('')
}

/** 配平的实参文本；配平失败 → null。 */
function callSpan(src: string, openParen: number): string | null {
  let depth = 0
  for (let i = openParen; i < src.length; i++) {
    const c = src[i]!
    if (c === '(') depth++
    else if (c === ')') {
      depth--
      if (depth === 0) return src.slice(openParen, i + 1)
    }
  }
  return null
}

describe('Windows 子进程不许弹黑窗（tools/**）', () => {
  it('tools/** 的每个子进程调用都显式带 windowsHide', () => {
    const files = walk(TOOLS, [])
    expect(files.length).toBeGreaterThan(100) // 扫描面没空（搬迁/改名后要立刻红）
    const offenders: string[] = []
    let calls = 0
    for (const abs of files) {
      const raw = readFileSync(abs, 'utf8')
      const src = stripCommentsAndStrings(raw)
      for (const m of src.matchAll(CALL)) {
        const at = m.index ?? 0
        const span = callSpan(src, at + m[0].length - 1)
        if (span === null) continue
        if (span.slice(1, -1).trim() === '') continue // `()`：声明或空调用
        const after = src[at + m[0].length + span.length - 1] ?? ''
        if (after === ':' || after === '{') continue // 方法/函数声明
        if (EXEMPT.some((e) => span.includes(e))) continue
        calls++
        if (span.includes('windowsHide')) continue
        const line = src.slice(0, at).split('\n').length
        offenders.push(
          `${abs.slice(TOOLS.length + 1).replace(/\\/g, '/')}:${line}  ` +
            span.replace(/\s+/g, ' ').slice(0, 110),
        )
      }
    }
    expect(calls).toBeGreaterThan(20) // 正则失效（换 API/改语法）时别静默通过
    expect(offenders).toEqual([])
  })
})
