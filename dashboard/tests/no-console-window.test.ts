/**
 * no-console-window.test.ts — 控制台**不许**再出现「裸 spawn」的黑窗（2026-10-13）
 *
 * 分层：`dashboard/src/**`（控制台服务端 + 启动器 + CLI 全在内）。
 *
 * 为什么这条是硬规则（用户报障：「dashboard 上启动 trainer/hub 时，有几个命令行黑窗
 * 一闪而过」）：控制台进程常以**没有控制台**的形态被拉起（后台/agent/guarded 启动），
 * 此时任何 console 子系统子进程默认会**自建一个可见控制台窗口**；而这批子进程全是
 * **秒退探针**（`netstat -ano` 找端口占用者、`wmic` 列 python 进程、`python -c import torch`
 * 探活、`git rev-parse`、`zip`…）⇒ 用户看到的就是「一闪而过」的黑窗。
 * Windows 上唯一的正解 = 每个 spawn 显式 `windowsHide: true`（与 `core/proc.ts::spawnBg`
 * 同规；python 侧等价物 = `common.platform_utils.POPEN_NO_WINDOW`，那边早已统一）。
 *
 * 扫描是**文本层**的（剥注释与字符串字面量 → 括号配平取实参），故：
 *   · 注释里提到 `spawnSync(` 不算（规则本身就在注释里写了好几次）；
 *   · 正则/字符串里的 `exec(` 不算；
 *   · `spawnSync(['netstat','-ano'])` 这种**完全没有 options** 的形态照样算违规
 *     ——它正是本次报障的病灶。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DASHBOARD_ROOT } from '../src/core/paths'

/** 子进程调用点：`spawn(` / `spawnSync(` / `execFileSync(` / `execSync(` / `exec(`（可带 Bun. 前缀）。 */
const CALL = /(?<![\w.$])(?:Bun\.)?(?:spawn|spawnSync|execFileSync|execSync|exec)\s*\(/g

/** 递归列 `.ts`（Bun.Glob 原生扫描；`dot: true` 与旧 walker 同面——它不跳点开头条目）。 */
function walk(dir: string): string[] {
  return [...new Bun.Glob('**/*.ts').scanSync({ cwd: dir, dot: true })].map((rel) => join(dir, rel))
}

/** 剥掉行注释 / 块注释 / 字符串字面量（保留换行数，行号才准）。 */
function stripCommentsAndStrings(src: string): string {
  let out = ''
  let i = 0
  while (i < src.length) {
    const c = src[i]!
    const next = src[i + 1] ?? ''
    if (c === '/' && next === '/') {
      const j = src.indexOf('\n', i)
      i = j < 0 ? src.length : j
      continue
    }
    if (c === '/' && next === '*') {
      const j = src.indexOf('*/', i + 2)
      const end = j < 0 ? src.length : j + 2
      out += src.slice(i, end).replace(/[^\n]/g, ' ')
      i = end
      continue
    }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c
      out += ' '
      i++
      while (i < src.length) {
        if (src[i] === '\\') {
          out += '  '
          i += 2
          continue
        }
        const ch = src[i]!
        out += ch === '\n' ? '\n' : ' '
        i++
        if (ch === quote) break
      }
      continue
    }
    out += c
    i++
  }
  return out
}

/** 从 `(` 起做括号配平，返回整段调用实参文本。 */
function callSpan(src: string, openParen: number): string {
  let depth = 0
  for (let i = openParen; i < src.length; i++) {
    const c = src[i]!
    if (c === '(') depth++
    else if (c === ')') {
      depth--
      if (depth === 0) return src.slice(openParen, i + 1)
    }
  }
  return src.slice(openParen)
}

describe('Windows 子进程不许弹黑窗', () => {
  it('dashboard/src 的每个子进程调用都显式带 windowsHide', () => {
    const files = walk(join(DASHBOARD_ROOT, 'src'))
    expect(files.length).toBeGreaterThan(50) // 扫描面没空（目录搬迁后要立刻红）
    const offenders: string[] = []
    let calls = 0
    for (const abs of files) {
      const src = stripCommentsAndStrings(readFileSync(abs, 'utf8'))
      for (const m of src.matchAll(CALL)) {
        const at = m.index ?? 0
        const span = callSpan(src, at + m[0].length - 1)
        calls++
        if (span.includes('windowsHide')) continue
        const line = src.slice(0, at).split('\n').length
        const rel = abs.slice(DASHBOARD_ROOT.length + 1).replace(/\\/g, '/')
        offenders.push(`${rel}:${line}  ${span.replace(/\s+/g, ' ').slice(0, 110)}`)
      }
    }
    expect(calls).toBeGreaterThan(5) // 正则失效（改语法/换 API）时别静默通过
    expect(offenders).toEqual([])
  })
})
