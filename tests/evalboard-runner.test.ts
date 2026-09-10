/** evalboard-runner.test.ts ↔ tools/training/evalboard/runner.ts（§4.3 门控语料）。 */
import { describe, expect, it } from 'bun:test'
import {
  batchIterId,
  planBatch,
  REGRESSION_SEG,
  seedsOfSegment,
} from '../tools/training/evalboard/runner'
import { buildLadder } from '../tools/training/evalboard/ladder'

describe('planBatch (§4.3)', () => {
  const ladder = buildLadder()
  it('每批 = 当前 + 下一 rung（同 seg）；k Obatch 每 3 批加回归位（段 0）', () => {
    const p0 = planBatch({ batch_id: 'b0', course: 'c', ladder, ladderPos: 0, k: 0 })
    expect(p0.seg).toBe(0)
    expect(p0.units.length).toBe(2)
    expect(p0.units[0].rung).toBe('c4l1')
    expect(p0.units[1].rung).toBe('c6l1')
    expect(p0.units[0].seeds).toEqual(seedsOfSegment(0))
    expect(p0.units[0].unit_of).toBe(2)
    // k=2 → 回归位
    const p2 = planBatch({ batch_id: 'b2', course: 'c', ladder, ladderPos: 0, k: 2 })
    expect(p2.units.length).toBe(3)
    expect(p2.units[2].seed0).toBe(860001 + 100 * REGRESSION_SEG)
    expect(p2.units[2].rung).toBe('c4l1')
    // k 派生段：k=17 → seg 1（per-(course,rung) 序号，非全局 id）
    const p17 = planBatch({ batch_id: 'b17', course: 'c', ladder, ladderPos: 3, k: 17 })
    expect(p17.seg).toBe(1)
    expect(p17.units[0].rung).toBe('c10l2')
  })
  it('末关无前瞻；iterId 命名空间与 A 层隔离', () => {
    const p = planBatch({ batch_id: 'bx', course: 'c', ladder, ladderPos: 7, k: 0 })
    expect(p.units.length).toBe(1)
    expect(p.units[0].rung).toBe('s1l3b1')
    expect(batchIterId('run1', 'abcd1234', 0)).toBe('run1.babcd1234u0')
  })
  it('ladderPos 越界响亮报错', () => {
    expect(() => planBatch({ batch_id: 'b', course: 'c', ladder, ladderPos: 8, k: 0 })).toThrow()
  })
})
