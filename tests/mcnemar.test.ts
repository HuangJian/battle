import { describe, expect, it } from 'bun:test'
import { mcnemarP, pairedDelta, pairedVerdict, pairOutcomes } from '../tools/eval/mcnemar'

describe('mcnemar', () => {
  it('回归锚：c6-pickup.it35 vs bc (27, 12) ≈ 0.025', () => {
    expect(Math.abs(mcnemarP(27, 12) - 0.025) < 0.002).toBe(true)
  })

  it('回归锚：c6-gae.it160 vs bc (21, 14) ≈ 0.3105', () => {
    expect(Math.abs(mcnemarP(21, 14) - 0.3105) < 0.002).toBe(true)
  })

  it('全一致对 → p=1（无证据，不是显著）', () => {
    expect(mcnemarP(0, 0)).toBe(1.0)
    expect(pairedVerdict({ b01: 0, b10: 0, b11: 60, b00: 40 })).toBe('flat')
  })

  it('对称性：mcnemarP(a,b) === mcnemarP(b,a)，但判决方向相反', () => {
    expect(mcnemarP(27, 12)).toBe(mcnemarP(12, 27))
    expect(pairedVerdict({ b01: 27, b10: 12, b11: 0, b00: 0 })).toBe('up')
    expect(pairedVerdict({ b01: 12, b10: 27, b11: 0, b00: 0 })).toBe('down')
  })

  it('pairOutcomes 计数 + 长度不一致抛错', () => {
    const t = pairOutcomes([false, true, true, false], [true, false, true, false])
    expect(t).toEqual({ b01: 1, b10: 1, b11: 1, b00: 1 })
    expect(pairedDelta(t)).toBe(0)
    expect(() => pairOutcomes([true], [true, false])).toThrow()
  })

  it('分多但软 vs 分少但硬', () => {
    // 21-14：delta=7 但 p≈0.31 → flat；7-0：delta=7 但 p<0.02 → up
    expect(pairedVerdict({ b01: 21, b10: 14, b11: 0, b00: 0 })).toBe('flat')
    expect(pairedVerdict({ b01: 7, b10: 0, b11: 0, b00: 0 })).toBe('up')
  })
})
