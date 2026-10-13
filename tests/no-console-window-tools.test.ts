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

function walk(dir: string, out: string[]): string[] {
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, ent.name)
    if (ent.isDirectory()) walk(p, out)
    else if (ent.name.endsWith('.ts') && !ent.name.endsWith('.d.ts')) out.push(p)
  }
  return out
}

/** 剥注释与字符串字面量（等长空格替换 ⇒ 偏移/行号不变）。
 *
 *  ⚠ **模板字面量的 `${…}` 插值按代码继续处理**（2026-10-13 实测漏洞：`say(`bun :
 *  ${spawnSync(BUN, …)}`)` 这种把调用塞进插值的写法，整体剥掉会让守门**静默放过**——
 *  `perf-cmp-rollout` 的两处版本探针就是这么漏的）。字符串字面量的**内容**与注释一律
 *  换成空格（`level-gen` 的报错文案里那句 `spawn (` 因此不算违规）。
 */
function stripCommentsAndStrings(src: string): string {
  const out: string[] = []
  //: 帧栈：`template`（模板字面量文本）/ `interp`（`${…}` 里的代码，`depth` = 花括号深度）。
  //: ⚠ 插值结束时**只弹 interp 帧**，模板帧要留到反引号 —— 第一版在这里踩过坑：弹早了，
  //: 同一个模板里的**后续文本**会被当成代码，`level-gen` 文案里的 `spawn (` 因此变成假阳性。
  const stack: Array<{ kind: 'template' | 'interp'; depth: number }> = []
  let mode: 'code' | 'template' | 'line' | 'block' | 'sq' | 'dq' = 'code'
  let i = 0
  const put = (text: string, keep: boolean): void => {
    out.push(keep ? text : text.replace(/[^\n]/g, ' '))
  }
  while (i < src.length) {
    const c = src[i]!
    const next = src[i + 1] ?? ''
    if (mode === 'line') {
      if (c === '\n') {
        out.push('\n')
        mode = 'code'
      } else put(c, false)
      i++
      continue
    }
    if (mode === 'block') {
      if (c === '*' && next === '/') {
        put('*/', false)
        i += 2
        mode = 'code'
        continue
      }
      put(c, false)
      i++
      continue
    }
    if (mode === 'sq' || mode === 'dq') {
      const quote = mode === 'sq' ? "'" : '"'
      if (c === '\\') {
        put(src.slice(i, i + 2), false)
        i += 2
        continue
      }
      if (c === quote) {
        out.push(quote)
        i++
        mode = 'code'
        continue
      }
      put(c, false)
      i++
      continue
    }
    if (mode === 'template') {
      if (c === '\\') {
        put(src.slice(i, i + 2), false)
        i += 2
        continue
      }
      if (c === '`') {
        out.push('`')
        stack.pop()
        i++
        mode = 'code'
        continue
      }
      if (c === '$' && next === '{') {
        out.push('${')
        stack.push({ kind: 'interp', depth: 0 })
        i += 2
        mode = 'code'
        continue
      }
      put(c, false)
      i++
      continue
    }
    // mode === 'code'
    if (c === '/' && next === '/') {
      mode = 'line'
      i += 2
      continue
    }
    if (c === '/' && next === '*') {
      mode = 'block'
      i += 2
      continue
    }
    if (c === '"') {
      out.push('"')
      i++
      mode = 'dq'
      continue
    }
    if (c === "'") {
      out.push("'")
      i++
      mode = 'sq'
      continue
    }
    if (c === '`') {
      out.push('`')
      stack.push({ kind: 'template', depth: 0 })
      i++
      mode = 'template'
      continue
    }
    const frame = stack[stack.length - 1]
    if (c === '{' && frame?.kind === 'interp') {
      frame.depth++
      out.push('{')
      i++
      continue
    }
    if (c === '}' && frame?.kind === 'interp') {
      if (frame.depth === 0) {
        stack.pop()
        out.push('}')
        i++
        mode = 'template'
        continue
      }
      frame.depth--
      out.push('}')
      i++
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

/** 扫描一段源码，返回缺 `windowsHide` 的调用（`label:行号  实参`）。 */
function scanForOffenders(raw: string, label: string): { offenders: string[]; calls: number } {
  const src = stripCommentsAndStrings(raw)
  const offenders: string[] = []
  let calls = 0
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
    offenders.push(`${label}:${line}  [${m[0].trim()}] ${span.replace(/\s+/g, ' ').slice(0, 100)}`)
  }
  return { offenders, calls }
}

/** 扫描器自检夹具：四种**必须**抓到的形态 + 三种**必须**放过的形态。
 *
 *  为什么要有它（2026-10-13 实测）：早先版本用「扫到的调用数 > 20」当下限，那是个魔数，
 *  而且它**真的漏过**——模板字面量 `${…}` 插值里的调用被整体剥掉，`perf-cmp-rollout` 的
 *  两处版本探针就这样静默过关。夹具把判据钉死在**行为**上，与仓库里有多少调用无关。 */
const FIXTURE = [
  "// 注释里的 spawn('x', []) 不算", // 1 行注释
  "const ok = spawnSync('bun', ['--version'], { windowsHide: true, stdio: 'inherit' })", // 2 带旗标 → 放过
  "const bad = spawnSync('git', ['show'], { encoding: 'utf8' })", // 3 ★ 缺旗标
  'say(`node: ${spawnSync(NODE_BIN, [\u0027--version\u0027], { encoding: \u0027utf8\u0027 })} ok`)', // 4 ★ 模板插值里缺旗标
  "exec('wmic cpu get LoadPercentage', { encoding: 'utf8' }, (e, o) => {})", // 5 ★ 缺旗标
  '  private spawn(): Worker { return w }', // 6 方法声明 → 放过
  'errors.push(`Enemy spawn (1,2) not reachable`)', // 7 文案里的 `spawn (` → 放过
].join('\n')

/** 已知豁免（**逐条带理由，不是通配**）：注入式 runner 的**调用点**——`windowsHide` 加在
 *  默认实现里（`deps.spawn ?? (argv, input, cwd) => Bun.spawnSync({ windowsHide: true, … })`），
 *  调用点只是把参数递给它，第 4 个参数会被忽略。 */
const EXEMPT: string[] = ['opts.repoRoot']

describe('Windows 子进程不许弹黑窗（tools/**）', () => {
  it('扫描器自检：四种病灶抓到、三种非病灶放过（含模板插值）', () => {
    const { offenders } = scanForOffenders(FIXTURE, 'fx')
    expect(offenders.map((o) => Number.parseInt(o.split(':')[1] ?? '', 10))).toEqual([3, 4, 5])
  })

  it('tools/** 的每个子进程调用都显式带 windowsHide', () => {
    const files = walk(TOOLS, [])
    expect(files.length).toBeGreaterThan(100) // 扫描面没空（搬迁/改名后要立刻红）
    const offenders: string[] = []
    let calls = 0
    for (const abs of files) {
      const rel = abs.slice(TOOLS.length + 1).replace(/\\/g, '/')
      const r = scanForOffenders(readFileSync(abs, 'utf8'), rel)
      offenders.push(...r.offenders)
      calls += r.calls
    }
    expect(calls).toBeGreaterThan(5) // 扫描面明显塌掉（换 API/改语法）时别静默通过
    expect(offenders).toEqual([])
  })
})
