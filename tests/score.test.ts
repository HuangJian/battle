import { describe, it, expect } from 'bun:test'
import {
  KILL_BASE_SCORE,
  ITEM_SCORE,
  DIFFICULTY_SCORE_FACTOR,
  AI_SCORE_FACTOR,
  levelFactor,
  killScore,
  stageClearScore,
} from '../src/config/score'

describe('scoring coefficients', () => {
  it('uses a 100 base kill score regardless of kind', () => {
    expect(KILL_BASE_SCORE).toBe(100)
  })

  it('awards 100 per power-up collected', () => {
    expect(ITEM_SCORE).toBe(100)
  })

  it('maps difficulty to the spec multipliers', () => {
    expect(DIFFICULTY_SCORE_FACTOR).toEqual({
      classic: 1.0,
      relax: 1.0,
      hard: 1.2,
      chaos: 1.5,
    })
  })

  it('maps AI tier to the spec multipliers', () => {
    expect(AI_SCORE_FACTOR).toEqual({
      none: 1.0,
      rookie: 1.0,
      soldier: 1.2,
      veteran: 1.5,
      commander: 2.0,
    })
  })
})

describe('levelFactor', () => {
  // stageIndex is 0-based; spec "第 N 关" is 1-based (offset +1)
  it('is 1.05 ** (stageIndex + 1)', () => {
    expect(levelFactor(0)).toBeCloseTo(1.05, 10)
    expect(levelFactor(19)).toBeCloseTo(Math.pow(1.05, 20), 10)
  })
})

describe('killScore', () => {
  it('classic + rookie + stage 1 = 100 * 1 * 1.05 * 1 = 105', () => {
    expect(killScore('classic', 'rookie', 0)).toBe(105)
  })

  it('classic + none (no AI) is also 105', () => {
    expect(killScore('classic', 'none', 0)).toBe(105)
  })

  it('hard + commander + stage 1 = 100 * 1.2 * 1.05 * 2 = 252', () => {
    expect(killScore('hard', 'commander', 0)).toBe(252)
  })

  it('chaos + veteran + stage 20 = 100 * 1.5 * 1.05^20 * 1.5 ≈ 597', () => {
    expect(killScore('chaos', 'veteran', 19)).toBe(597)
  })

  it('falls back to 1.0 for unknown difficulty / AI', () => {
    expect(killScore('unknown', undefined, 0)).toBe(105)
  })
})

describe('stageClearScore', () => {
  it('stage 1 clear = 1000 * 1.05 = 1050', () => {
    expect(stageClearScore(0)).toBe(1050)
  })

  it('stage 20 clear = 1000 * 1.05^20 ≈ 2653', () => {
    expect(stageClearScore(19)).toBe(2653)
  })
})
