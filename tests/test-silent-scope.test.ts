import { describe, expect, it } from 'bun:test'
import {
  HEAVY_TESTS,
  allTestFiles,
  isDashboardOnly,
  isHeavyFile,
  isRootSuiteTestPath,
  staleHeavyEntries,
} from '../tools/test-silent'

/**
 * `dashboard/` 是独立 bun 项目（自带 package.json / bun.lock / node_modules，
 * DECISIONS §2026-09-14-goalnn-dashboard-project · 全文 → docs/nn/console.md §11），**不在**根套件之内。
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

/**
 * HEAVY_TESTS 的有效性 + 口径锁。
 *
 * **判据**（改名单前先跑 `bun tools/measure-suite.ts`，它会直接打出每个条目的达标判定）：
 * 只排除**单跑墙钟 ≥ 整份非 heavy 套件**的文件。2026-09-15 实测（16 vCPU，地板 6.1s）：
 *   · godai-score-gate 11.7–13.1s（2.0×）⇒ 达标，排除它使提交的测试步 6.1s → 18.6s——保留；
 *   · calibration 0.66–0.71s（0.11×）⇒ 不达标，当日从名单移除（排除它省不到 0.7s，代价是
 *     「改坏 calibration 时本地 hook 不再看得见」）；
 *   · nn/intent-rl-rollout 3.8s/1.7s（0.3–0.6×）⇒ 不达标，剔除实测只省 ~0.6s（噪声带内）。
 *
 * 注意本组断言**管不了**「条目的 heavy 身份已过期」（如 calibration）：那要靠测量，
 * 即 `bun tools/measure-suite.ts` —— 静态断言只能钉住「名字必须指向真文件」。
 *
 * 断言必须是**硬**的：名单里只剩一个写错/过期的名字时，排除机制会静默失效（整份套件
 * 变慢），而内联的 ⚠ 提示挂在绿行摘要上没人会读（§1.4 当时只要求「提示」）。
 * 先例：`god-ai-gate` 重命名后残留为死条目（docs/god-ai-tuning.progress.md §234）。
 */
describe('HEAVY_TESTS — 名单有效性与口径', () => {
  it('每个条目都指向一个真实存在的测试文件（重命名/删除残留 = 硬失败）', () => {
    expect(staleHeavyEntries(allTestFiles(joinRoot()))).toEqual([])
  })

  it('名单非空——排除机制一旦空转，12s 的 score gate 会静默塞回每次提交', () => {
    expect(HEAVY_TESTS.size).toBeGreaterThan(0)
  })

  it('名字口径与执行器过滤一致，且命中的文件都在根套件枚举范围内', () => {
    // 双向：条目数 == 被 isHeavyFile 命中的文件数（防「条目写全路径导致永不命中」），
    // 且这些文件确实会被根套件枚举（移到 dashboard/ 或 tmp/ 后条目即失效）。
    const heavyFiles = allTestFiles(joinRoot()).filter(isHeavyFile)
    expect(heavyFiles.length).toBe(HEAVY_TESTS.size)
    for (const f of heavyFiles) expect(isRootSuiteTestPath(f)).toBe(true)
  })
})

function joinRoot(): string {
  return `${import.meta.dir}/..`
}
