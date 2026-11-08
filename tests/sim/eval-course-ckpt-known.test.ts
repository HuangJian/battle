/**
 * eval-course-ckpt 汇总的 **known 分母**契约（plan/o1-eval-metrics-remediation §6 S1，
 * 2026-11-08 评审 P1-3）：可选均值列只除「有这一列」的局数；0 局有键 ⇒ `N/A`，
 * 绝不把「旧 bundle 没这列」读成 0.00（与 `v11KnownDelta` 的 R15 口径同规）。
 */
import { describe, expect, it } from 'bun:test'
import { meanKnown } from '../../tools/sim/eval-course-ckpt'

describe('eval-course-ckpt known 分母', () => {
  it('有键 ⇒ 除 known；真 0 保留 0', () => {
    expect(meanKnown(10, 4)).toBe(2.5)
    expect(meanKnown(0, 4)).toBe(0)
  })

  it('0 局有键 ⇒ null（显示 N/A，不是 0）', () => {
    expect(meanKnown(0, 0)).toBeNull()
    expect(meanKnown(12, 0)).toBeNull()
  })
})
