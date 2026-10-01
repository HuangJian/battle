import { describe, expect, it } from 'bun:test'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { resolve, sep } from 'node:path'

/**
 * dashboard 里**真 spawn 的 python 脚本路径**必须在盘上存在。
 *
 * 背景（2026-09-30，nn-training 刀 4）：`nn-training/rl/` 按「编排 vs 纯逻辑」拆出 `biz/`
 * （64 个纯逻辑模块搬了家）。搬家的机械改写只看得见**整条字面量**（`'rl/eval_replays_once.py'`），
 * 看不见 `path.join(NN_TRAINING, 'rl', 'eval_replays_once.py')` 这种**逗号分片**形态 ——
 * 实测就是这一处（`server/api/route.ts` 的「导出 replay」spawn）被漏掉：五条门禁全绿，
 * **点按钮才炸**。本测试把这一类变成门禁：扫描面里的每条 `'<段>', '<x>.py'` 都必须真存在。
 *
 * 口径（故意的窄口径，宁可漏报不可误报）：
 *   · 只看 `join(` / `resolve(` 调用**同行**里的字面量（多行 join 看不到 ⇒ 下面的
 *     「找到 ≥ 2 条」断言会在扫描面缩水时先红，而不是静默通过）；
 *   · 首个字面量是 `'nn-training'` ⇒ 从仓根起算；否则按 `NN_TRAINING` 起算
 *     （`dashboard/src/core/paths.ts` 的常量就是这个含义）；
 *   · 最后一段不是 `.py` 的（`.venv/bin/python3` 那类）一律跳过。
 */

const DASH = resolve(import.meta.dir, '..')
const ROOT = resolve(DASH, '..')
const NN = resolve(ROOT, 'nn-training')

/** 一行里 `join(` / `resolve(` 调用内部的**字符串字面量**序列（单引号或双引号）。
 *
 * 只取到**第一个** `)` 为止（本仓这两处都是一行、参数里没有嵌套调用）——写成
 * `path.join(a, f(x), 'b.py')` 就会截错，所以下面那条「找到 ≥ 2 条」的断言是必须的：
 * 扫描面缩水先红，不会静默变成「没扫到 = 没问题」。
 */
function literalsInLine(line: string): string[] {
  const call = /(?:join|resolve)\(/.exec(line)
  if (!call) return []
  const inner = line.slice(call.index).split(')')[0] ?? ''
  return [...inner.matchAll(/'([^']*)'|"([^"]*)"/g)].map((m) => m[1] ?? m[2] ?? '')
}

interface SpawnTarget {
  file: string
  literal: string
  abs: string
}

function spawnTargets(): SpawnTarget[] {
  const found: SpawnTarget[] = []
  const walk = (dir: string): void => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = resolve(dir, e.name)
      if (e.isDirectory()) {
        if (e.name === 'node_modules' || e.name.startsWith('.')) continue
        walk(p)
        continue
      }
      if (!/\.tsx?$/.test(e.name)) continue
      const src = readFileSync(p, 'utf8')
      for (const line of src.split('\n')) {
        const lits = literalsInLine(line)
        const last = lits.at(-1)
        if (!last || !last.endsWith('.py')) continue
        const abs = lits[0] === 'nn-training' ? resolve(ROOT, ...lits) : resolve(NN, ...lits)
        found.push({
          file: p
            .slice(ROOT.length + 1)
            .split('\\')
            .join('/'),
          literal: lits.join('/'),
          abs,
        })
      }
    }
  }
  walk(resolve(DASH, 'src'))
  return found
}

describe('dashboard spawn 的 python 脚本路径', () => {
  const targets = spawnTargets()

  it('扫描面可信：找到 ≥ 2 条 py spawn 路径（扫描坏了先红，不静默通过）', () => {
    expect(targets.length).toBeGreaterThanOrEqual(2)
  })

  it('每条路径都真实存在（搬家漏改 = 这里红，而不是点按钮才炸）', () => {
    const missing = targets.filter((t) => !existsSync(t.abs)).map((t) => `${t.file}: ${t.literal}`)
    expect(missing).toEqual([])
  })

  it('路径落在 nn-training/ 下（拼错根 = 永远不存在）', () => {
    // 分隔符按平台取：本仓在 Windows 上开发，`${NN}/` 的字面量比较会让**每一条**都判成
    // 「在外面」（2026-10-02 合并实测：origin 侧这条在 Linux CI 绿、Windows 全红）。
    const outside = targets.filter((t) => !t.abs.startsWith(NN + sep)).map((t) => t.literal)
    expect(outside).toEqual([])
  })

  it('目标都是文件而不是目录（`join` 里写了个目录名也会红）', () => {
    const dirs = targets.filter((t) => existsSync(t.abs) && !statSync(t.abs).isFile())
    expect(dirs.map((t) => `${t.file}: ${t.literal}`)).toEqual([])
  })
})
