import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * CoordConv 坐标通道 golden 双向校验（plan/python-refactor.md P2-5）。
 *
 * golden 单一事实来源：nn-training/models/coord_golden.json（Python 侧
 * tests/test_coord_golden.py 对同一文件断言）。本测试独立用 infer.ts 的公式
 * （Math.round，half-up）重算并与 golden 逐值比对。
 *
 * 背景：Python 用 torch.round（banker's rounding），TS 用 Math.round（half-up）。
 * BOARD=26 时 j×255/25 = j×10.2 无 `.5` 平局，两者侥幸一致；BOARD 变更后若出现
 * `.5`，任一侧改动都会在此处变红（或 Python 侧 test_coord_formula_no_half_
 * integer_collisions 先行触发），杜绝静默分叉。
 */
const GOLDEN = JSON.parse(
  readFileSync(
    join(import.meta.dir, '..', '..', 'nn-training', 'models', 'coord_golden.json'),
    'utf8',
  ),
) as { board: number; coords: number[] }

describe('coord channels golden (P2-5)', () => {
  it('TS 坐标公式与 golden 逐值一致', () => {
    const B = GOLDEN.board
    const sp = B * B
    expect(GOLDEN.coords.length).toBe(2 * sp)
    for (let r = 0; r < B; r++) {
      for (let c = 0; c < B; c++) {
        // 与 src/nn/infer.ts:428-429 完全相同的公式（勿改：双端契约）
        const x = Math.round((c / (B - 1)) * 255)
        const y = Math.round((r / (B - 1)) * 255)
        expect(x, `x 通道 [${r},${c}]`).toBe(GOLDEN.coords[r * B + c])
        expect(y, `y 通道 [${r},${c}]`).toBe(GOLDEN.coords[sp + r * B + c])
      }
    }
  })

  it('坐标通道端点语义：0 与 255', () => {
    const B = GOLDEN.board
    const sp = B * B
    expect(GOLDEN.coords[0]).toBe(0)
    expect(GOLDEN.coords[B - 1]).toBe(255)
    expect(GOLDEN.coords[sp + (B - 1) * B]).toBe(255)
  })
})
