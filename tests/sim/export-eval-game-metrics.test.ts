/**
 * 切片 5（plan/o1-eval-metrics-remediation §6）：`export-eval-game.ts` 直出
 * `pickupDist` + `th*` 10 列。这里只验「报告里有、类型/哨兵对、同 seed 可复现」；
 * 逐列与 Python `eval_rows` 的集合对账在 `nn-training/tests/worker/test_eval_metric_columns.py`。
 */

import { describe, expect, it } from 'bun:test'
import { STAGES } from '../../src/config/stages'
import { runEvalOne } from '../../tools/sim/export-eval-game'

/** god 策略（免权重文件）跑一小段：只读采样，不依赖任何 ckpt。 */
function run(seed: number, maxTicks: number) {
  return runEvalOne(0, STAGES[0], seed, 'hard', maxTicks, '', 'god')
}

describe('export-eval-game metrics v13 补列（切片 5）', () => {
  it('11 列全部落盘，哨兵/范围正确', () => {
    const r = run(860001, 40)
    // pickupDist：无存活拾取 / 玩家不在场 = 哨兵 -1；有读数时 ≥ 0。
    expect(Number.isInteger(r.pickupDist)).toBe(true)
    expect(r.pickupDist).toBeGreaterThanOrEqual(-1)
    for (const v of [r.thBreach, r.thBreachAimed, r.thDirect, r.thDirectAimed]) {
      expect(Number.isInteger(v)).toBe(true)
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(4) // 场上敌车上限（threatSplit 的四轴互斥计数）
    }
    expect(Number.isInteger(r.thShotsAtBaseCum)).toBe(true)
    expect(r.thShotsAtBaseCum).toBeGreaterThanOrEqual(0)
    expect(Number.isInteger(r.thBulletsAtBase)).toBe(true)
    expect(r.thBulletsAtBase).toBeGreaterThanOrEqual(0)
    for (const v of [r.thCostBreach, r.thCostBreachAimed, r.thCostDirect, r.thCostDirectAimed]) {
      expect(Number.isInteger(v)).toBe(true)
      expect(v).toBeGreaterThanOrEqual(0)
    }
  })

  it('同 seed 两次逐字段一致（只读采样不进 RNG/World）', () => {
    const a = run(4242, 60)
    const b = run(4242, 60)
    expect({
      pickupDist: a.pickupDist,
      thBreach: a.thBreach,
      thBreachAimed: a.thBreachAimed,
      thDirect: a.thDirect,
      thDirectAimed: a.thDirectAimed,
      thShotsAtBaseCum: a.thShotsAtBaseCum,
      thBulletsAtBase: a.thBulletsAtBase,
      thCostBreach: a.thCostBreach,
      thCostBreachAimed: a.thCostBreachAimed,
      thCostDirect: a.thCostDirect,
      thCostDirectAimed: a.thCostDirectAimed,
    }).toEqual({
      pickupDist: b.pickupDist,
      thBreach: b.thBreach,
      thBreachAimed: b.thBreachAimed,
      thDirect: b.thDirect,
      thDirectAimed: b.thDirectAimed,
      thShotsAtBaseCum: b.thShotsAtBaseCum,
      thBulletsAtBase: b.thBulletsAtBase,
      thCostBreach: b.thCostBreach,
      thCostBreachAimed: b.thCostBreachAimed,
      thCostDirect: b.thCostDirect,
      thCostDirectAimed: b.thCostDirectAimed,
    })
  })
})
