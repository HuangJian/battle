/**
 * paired-pd.test.ts — 同种子配对 verdict 工具验证。
 *
 * 1. 二项上尾 smell check（对称点 n=100/k=50 精确值 0.5398）。
 * 2. 真实 verdict 数值对账：x3-power（b01=12/b10=35，课程文件结算节：单侧 p=0.9998）
 *    与 x3-step（b01=12/b10=26，§50：单侧 p=0.9931）——工具算出的 p 必须与落账数一致。
 * 3. summarize 最小综合样例（b01/b10/pd/Δ/分关逐值手算）。
 * 4. checkPairing：键齐且对齐→0；错位→>0；无键→-1；行数不等→抛错。
 */
import { describe, it, expect } from 'bun:test'
import {
  binomUpper,
  mcnemar,
  summarize,
  checkPairing,
  type VerdictRow,
} from '../../tools/sim/paired-pd'

describe('binomUpper', () => {
  it('边界：k<=0 恒 1，k>n 恒 0', () => {
    expect(binomUpper(0, 47)).toBe(1)
    expect(binomUpper(-3, 47)).toBe(1)
    expect(binomUpper(48, 47)).toBe(0)
    expect(binomUpper(5, 0)).toBe(1)
  })
  it('对称点 P(X>=50|100,0.5) = 0.5+P(X=50)/2 ≈ 0.5398', () => {
    expect(binomUpper(50, 100)).toBeCloseTo(0.5398, 3)
  })
})

describe('mcnemar 真实落账对账', () => {
  it('x3-power b01=12/b10=35：单侧≈0.9998（课程文件结算节）', () => {
    const { pOneSided, pTwoSided } = mcnemar(12, 35)
    expect(pOneSided).toBeCloseTo(0.9998, 3)
    expect(pTwoSided).toBeLessThan(0.01)
  })
  it('x3-step b01=12/b10=26：单侧≈0.9931（§50）', () => {
    const { pOneSided, pTwoSided } = mcnemar(12, 26)
    expect(pOneSided).toBeCloseTo(0.9931, 3)
    expect(pTwoSided).toBeLessThan(0.05)
  })
  it('零翻转：双侧 p=1（不断言方向）', () => {
    expect(mcnemar(0, 0).pTwoSided).toBe(1)
  })
})

describe('summarize 最小样例', () => {
  // idx0: 双胜；idx1: 仅基线胜(b10)；idx2: 仅候选胜(b01)；idx3: 双负
  const base: VerdictRow[] = [
    { win: 1, seed: 1, stageId: 2000, stageName: 'abc' },
    { win: 1, seed: 2, stageId: 2000, stageName: 'abc' },
    { win: 0, seed: 3, stageId: 2001, stageName: 'abd' },
    { win: 0, seed: 4, stageId: 2001, stageName: 'abd' },
  ]
  const cand: VerdictRow[] = [
    { win: 1, seed: 1, stageId: 2000 },
    { win: 0, seed: 2, stageId: 2000 },
    { win: 1, seed: 3, stageId: 2001 },
    { win: 0, seed: 4, stageId: 2001 },
  ]
  it('pooled：2-2 打平，b01=b10=1，pd=0.5，Δ=0，单侧 p=0.75', () => {
    const s = summarize(base, cand)
    expect(s.n).toBe(4)
    expect(s.baseWins).toBe(2)
    expect(s.candWins).toBe(2)
    expect(s.deltaPp).toBe(0)
    expect(s.b01).toBe(1)
    expect(s.b10).toBe(1)
    expect(s.pd).toBe(0.5)
    expect(s.pOneSided).toBeCloseTo(0.75, 6)
  })
  it('分关：2000 Δ=-50pp，2001 Δ=+50pp', () => {
    const s = summarize(base, cand)
    expect(s.perStage).toHaveLength(2)
    const m = new Map(s.perStage.map((st) => [st.stageId, st]))
    expect(m.get('2000')!.deltaPp).toBe(-50)
    expect(m.get('2001')!.deltaPp).toBe(50)
  })
  it('行数不等抛错；配对键错位被检出', () => {
    expect(() => summarize(base, cand.slice(0, 3))).toThrow()
    expect(checkPairing(base, cand)).toBe(0)
    const shifted = [...cand.slice(1), cand[0]]
    expect(checkPairing(base, shifted)).toBeGreaterThan(0)
    expect(checkPairing([{ win: 1 }], [{ win: 0 }])).toBe(-1)
  })
})
