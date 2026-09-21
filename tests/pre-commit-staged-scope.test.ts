import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * pre-commit 暂存清单的**单一来源**不变量（2026-09-21 假警告回归）。
 *
 * 钩子里有两处要看「暂存了什么」：开头建归因基准 `$STAGED_LIST`，结尾做并发守卫
 * （门禁期间是否有人往暂存区追加 → 提示「建议重新 commit」）。两处若各自跑一次 diff，
 * 口径就会漂：2026-09-21 修 ruff E902 时只给基准那处加了 `--diff-filter=ACMR`
 * （**删除的路径不能进归因集**——各 linter 按路径打开文件，路径不存在是硬错），
 * 守卫那处仍是不过滤的 diff ⇒ **任何含删除的提交都被误报成「门禁期间他人追加」**。
 * 实测：合并提交 `fb16b62` 删掉 `nn-training/tests/test_remote_degrade.py` 就被点名，
 * 而那次提交的内容完全正确（HEAD 与 origin 的树里该路径都不存在）。
 *
 * 所以这里钉三条不变量（与 `tests/freeze-scope.test.ts` 同一手法：读钩子源码，
 * 把「钩子做的一个范围决策」变成 CI 会拦的断言）：
 *   ① 每一处暂存清单 diff **都带 `--diff-filter`** —— 裸 diff 会把纯删除算进来；
 *   ② 过滤器的**唯一定义**排除 `D` —— 删除不进归因集，也就不是「门禁能判定」的改动；
 *   ③ 并发守卫的集合比较**走同一个来源**（引用那个单一出处，而不是自己再算一遍）。
 */

const HOOK_PATH = resolve(import.meta.dir, '..', 'tools/githook/pre-commit')
const HOOK = readFileSync(HOOK_PATH, 'utf8')

/** 匹配「列暂存清单」的真实调用（`--cached` 的 name-only diff）。 */
const STAGED_DIFF_RE = /^(?!\s*#).*git diff\s+--cached\s+--name-only/
/** 单一过滤器变量：`STAGED_FILTER=ACMR`。 */
const FILTER_DEF_RE = /^STAGED_FILTER=(\S+)/m
/** 归因基准的唯一出处（`staged_paths()` 定义 + 其调用）。 */
const SOURCE_FN_RE = /^staged_paths\(\) \{[^}]*\}$/m
/** 并发守卫所在段落的起点（注释横幅）。 */
const GUARD_RE = /# -+ 并发提示[\s\S]*$/

describe('pre-commit 暂存清单单一来源（纯删除误报回归）', () => {
  // 排除注释行：说明文字里会引用旧写法，不该被当成真实调用
  const stagedDiffLines = HOOK.split('\n').filter((line) => STAGED_DIFF_RE.test(line))
  const sourceFn = SOURCE_FN_RE.exec(HOOK)?.[0] ?? ''

  it('① 每一处暂存清单 diff 都带 --diff-filter（裸 diff 会算进纯删除）', () => {
    expect(stagedDiffLines.length).toBeGreaterThan(0) // 防扫描失效变空断言
    const unfiltered = stagedDiffLines.filter((line) => !line.includes('--diff-filter'))
    expect(unfiltered).toEqual([])
  })

  it('② 过滤器的唯一定义排除 D，且暂存清单走这个共享变量', () => {
    const filter = FILTER_DEF_RE.exec(HOOK)?.[1]
    expect(filter).toBeDefined()
    expect(filter).not.toContain('D')
    // 暂存清单由 `staged_paths()` 供给（而不是把字面量在各处抄一遍）
    expect(sourceFn).toContain('--diff-filter="$STAGED_FILTER"')
  })

  it('③ 并发守卫的比较走同一个来源', () => {
    const guard = GUARD_RE.exec(HOOK)?.[0] ?? ''
    expect(guard).not.toBe('') // 防「横幅注释被改名 → 断言变成空转」的假绿
    expect(guard).toContain('staged_paths')
  })
})
