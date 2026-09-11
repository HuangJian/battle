/** evalboard-sentinels.test.ts ↔ tools/training/evalboard/sentinels.ts（§7 逐条）。 */
import { describe, expect, it } from 'bun:test'
import {
  checkS1,
  checkS2,
  checkS3,
  checkS4,
  checkS5norm,
  checkS6,
  checkS7,
  checkS8,
  checkS9,
  checkS10,
  checkS11,
  checkS12,
  fuseOf,
  type PairedDelta,
} from '../tools/training/evalboard/sentinels'

const pd = (over: Partial<PairedDelta> = {}): PairedDelta => ({
  dWin: 0,
  dDeath: 0,
  dKillCompletion: 0,
  dTimeout: 0,
  dAccuracy: 0,
  dShotsPerGame: 0,
  dWinTickMedian: 0,
  mdd: 0.06,
  winTickNa: false,
  ...over,
})

const m = (over: Record<string, number | null> = {}) => ({
  n: 400,
  winRate: 0.5,
  clearRate: 0.5,
  killCompletion: 0.9,
  lifePrice: 2,
  winDmgMedian: 1,
  winTickMedian: 1000,
  deathRate: 0.1,
  timeoutRate: 0.05,
  accuracy: 0.2,
  shotsPerGame: 50,
  firstKillTickMedian: 200,
  stuckP95: 5,
  cellsVisitedMean: 30,
  meanKills: 3,
  meanPowerUps: 0,
  winTickMean: 1000,
  winHpLeftMean: 100,
  ...over,
})

describe('S1–S6', () => {
  it('S1 命价失衡；+∞ 触发', () => {
    expect(checkS1(m({ lifePrice: 20 }), m({ lifePrice: 1.5 }))).not.toBeNull()
    expect(checkS1(m({ lifePrice: Infinity }), m({ lifePrice: 1.5 }))?.id).toBe('S1')
    expect(checkS1(m({ lifePrice: 2 }), m({ lifePrice: 1.5 }))).toBeNull()
  })
  it('S2 崩塌需 death+5pp 且 win−1MDD', () => {
    expect(checkS2(pd({ dDeath: 0.06, dWin: -0.07 }))?.id).toBe('S2')
    expect(checkS2(pd({ dDeath: 0.06, dWin: -0.01 }))).toBeNull()
    expect(checkS2(pd({ dDeath: 0.02, dWin: -0.07 }))).toBeNull()
  })
  it('S3 苟化 / S4 闭嘴', () => {
    expect(checkS3(pd({ dKillCompletion: -0.07, dTimeout: 0.07 }))?.id).toBe('S3')
    expect(checkS3(pd({ dKillCompletion: -0.07, dTimeout: 0.01 }))).toBeNull()
    expect(checkS4(pd({ dAccuracy: -0.07, dShotsPerGame: -5 }))?.id).toBe('S4')
    expect(checkS4(pd({ dAccuracy: -0.07, dShotsPerGame: 5 }))).toBeNull()
  })
  it('S5 空集 N/A 不进判定', () => {
    expect(checkS5norm(0.5, 0.06, true)).toBeNull()
    expect(checkS5norm(0.07, 0.06, false)?.id).toBe('S5')
    expect(checkS5norm(0.01, 0.06, false)).toBeNull()
  })
  it('S6 高翻转低净值禁 verdict', () => {
    const h = checkS6(0.45, 0.01, 0.06)
    expect(h?.id).toBe('S6')
    expect(h?.blocksVerdict).toBe(true)
    expect(checkS6(0.45, 0.05, 0.06)).toBeNull()
    expect(checkS6(0.2, 0.01, 0.06)).toBeNull()
  })
})

describe('S7–S12', () => {
  it('S7 冷启动 <5 点不武装；基线偏离才告警', () => {
    expect(checkS7([0.3, 0.31, 0.29, 0.3])).toBeNull()
    expect(checkS7([0.3, 0.31, 0.29, 0.3, 0.32])).toBeNull()
    expect(checkS7([0.3, 0.31, 0.29, 0.3, 0.31, 0.5])?.id).toBe('S7')
  })
  it('S8 整批拒绝', () => {
    const h = checkS8({ dropped: 2, metrics_version: 1, ckpt_sha16: 'a' })
    expect(h?.rejectBatch).toBe(true)
    expect(checkS8({ dropped: 0, metrics_version: 1, ckpt_sha16: 'a' })).toBeNull()
    expect(checkS8({ dropped: 0, metrics_version: 2, ckpt_sha16: 'a' })).not.toBeNull()
    expect(checkS8({ dropped: 0, metrics_version: 1, ckpt_sha16: null })).not.toBeNull()
  })
  it('S9 相对峰值（非绝对电平）', () => {
    expect(checkS9(0.2, 0.3, 0.01)?.id).toBe('S9') // 跌幅 33% > 30%
    expect(checkS9(0.2492, 0.3, 0.01)).toBeNull() // ks1 腿旧误报点：跌幅 17% 不报
    expect(checkS9(0.3, 0.3, 0.06)?.id).toBe('S9')
  })
  it('S10 引擎漂移 / S11 超老师只提示 / S12 静默平台', () => {
    expect(checkS10(['e1', 'e1'])).toBeNull()
    expect(checkS10(['e1', 'e2'])?.id).toBe('S10')
    const s11 = checkS11(
      { student: m({ winRate: 0.75 }), god: m({ winRate: 0.64 }), mdd: 0.049 },
      false,
    )
    expect(s11?.id).toBe('S11')
    expect(s11?.severity).toBe('note')
    expect(
      checkS11({ student: m({ winRate: 0.66 }), god: m({ winRate: 0.64 }), mdd: 0.049 }, false),
    ).toBeNull()
    expect(checkS12([0.01, 0.02], 0.062)?.id).toBe('S12')
    expect(checkS12([0.01], 0.062)).toBeNull()
    expect(checkS12([0.01, 0.2], 0.062)).toBeNull()
  })
  it('熔断：S2/S3/S4 或超时爆', () => {
    expect(fuseOf([{ id: 'S2', severity: 'red', message: 'x' }], 0.05)).toBe('S2')
    expect(fuseOf([], 0.2)).toBe('S-timeout')
    expect(fuseOf([], 0.05)).toBeNull()
  })
})
