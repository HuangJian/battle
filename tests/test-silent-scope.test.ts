import { describe, expect, it } from 'bun:test'
import { allTestFiles, isDashboardOnly, isRootSuiteTestPath } from '../tools/test-silent'

/**
 * `dashboard/` 是独立 bun 项目（自带 package.json / bun.lock / node_modules，
 * DECISIONS §2026-09-14-goalnn-dashboard-project），**不在**根套件之内。
 *
 * 这两条约束就靠这里锁住：
 *   · 根套件枚举 dashboard 测试 → 根门禁反向依赖 dashboard 的安装状态；
 *   · 「只改 dashboard」的提交不识别 → 静默 fallback 到根全量套件（分钟级白烧）。
 */
describe('根套件范围 — dashboard 是独立项目', () => {
  it('枚举测试文件时从不出现在 dashboard/ 下的测试', () => {
    const files = allTestFiles(joinRoot())
    expect(files.length).toBeGreaterThan(0)
    expect(files.filter((f) => f.startsWith('dashboard/'))).toEqual([])
  })

  it('isRootSuiteTestPath 排除 dashboard/，但保留根测试', () => {
    expect(isRootSuiteTestPath('dashboard/tests/training-console.test.ts')).toBe(false)
    expect(isRootSuiteTestPath('dashboard\\tests\\training-console.test.ts')).toBe(false)
    expect(isRootSuiteTestPath('tests/test-silent-scope.test.ts')).toBe(true)
    expect(isRootSuiteTestPath('tests/not-a-test.ts')).toBe(false)
    expect(isRootSuiteTestPath('node_modules/pkg/x.test.ts')).toBe(false)
  })

  it('isDashboardOnly 识别「只改 dashboard」的改动集（含 Windows 分隔符）', () => {
    expect(isDashboardOnly(['dashboard/src/server/server.ts'])).toBe(true)
    expect(isDashboardOnly(['dashboard\\src\\web\\view\\index.ts', 'dashboard/package.json'])).toBe(
      true,
    )
    // 空集不是「只改 dashboard」（否则干净工作树会被误判）
    expect(isDashboardOnly([])).toBe(false)
    // 混入根文件就不是 dashboard-only
    expect(isDashboardOnly(['dashboard/src/core/paths.ts', 'src/game/World.ts'])).toBe(false)
    expect(isDashboardOnly(['src/game/World.ts'])).toBe(false)
    // 前缀相近但不同的目录不应误命中
    expect(isDashboardOnly(['dashboard-old/x.ts'])).toBe(false)
  })
})

function joinRoot(): string {
  return `${import.meta.dir}/..`
}
