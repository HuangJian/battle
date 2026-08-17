import { describe, it, expect } from 'bun:test'
import { parseStageSpec, StageSpecError, paramsHash, runHeader } from '../tools/lib/stage-spec'

/**
 * Open-test protocol M0 §3.1: the stage parser must give `all`, ranges,
 * comma lists and single stages one shared semantics, and REJECT empty /
 * reverse / out-of-range / junk specs instead of silently degrading to S1
 * (the §213 CMA-ES 口径 bug: `--stages 1-35` parsed as S1 only).
 */
describe('parseStageSpec (M0 §3.1)', () => {
  it('1-35 → 35 0-based indices', () => {
    const idxs = parseStageSpec('1-35')
    expect(idxs.length).toBe(35)
    expect(idxs[0]).toBe(0)
    expect(idxs[34]).toBe(34)
    expect(idxs).toEqual(Array.from({ length: 35 }, (_, i) => i))
  })

  it('1,3,7 → [0,2,6]', () => {
    expect(parseStageSpec('1,3,7')).toEqual([0, 2, 6])
  })

  it('all → every stage', () => {
    const idxs = parseStageSpec('all')
    expect(idxs.length).toBe(35)
    expect(idxs[0]).toBe(0)
    expect(idxs[34]).toBe(34)
  })

  it('single stage 34 → [33]', () => {
    expect(parseStageSpec('34')).toEqual([33])
  })

  it('mixed tokens 1,3-5,7 expand in order and dedupe', () => {
    expect(parseStageSpec('1,3-5,7')).toEqual([0, 2, 3, 4, 6])
    expect(parseStageSpec('1,1,2')).toEqual([0, 1])
  })

  it('rejects empty spec and empty tokens', () => {
    expect(() => parseStageSpec('')).toThrow(StageSpecError)
    expect(() => parseStageSpec('   ')).toThrow(StageSpecError)
    expect(() => parseStageSpec('1,,3')).toThrow(StageSpecError)
  })

  it('rejects reverse ranges', () => {
    expect(() => parseStageSpec('35-1')).toThrow(StageSpecError)
    expect(() => parseStageSpec('5-3')).toThrow(StageSpecError)
  })

  it('rejects out-of-range (0, 36, ranges crossing the bound)', () => {
    expect(() => parseStageSpec('0')).toThrow(StageSpecError)
    expect(() => parseStageSpec('36')).toThrow(StageSpecError)
    expect(() => parseStageSpec('30-40')).toThrow(StageSpecError)
  })

  it('rejects illegal tokens instead of parseInt-degrading to S1', () => {
    // The §213 bug class: parseInt('1-35') === 1 → S1 only. Must throw now.
    expect(() => parseStageSpec('1-')).toThrow(StageSpecError)
    expect(() => parseStageSpec('-5')).toThrow(StageSpecError)
    expect(() => parseStageSpec('1-3-5')).toThrow(StageSpecError)
    expect(() => parseStageSpec('a')).toThrow(StageSpecError)
    expect(() => parseStageSpec('1,b,3')).toThrow(StageSpecError)
  })

  it('is pure — repeated calls return identical arrays, no side effects', () => {
    const a = parseStageSpec('1,3,7')
    const b = parseStageSpec('1,3,7')
    expect(a).toEqual(b)
    // Key-order-independent hash stability feeds the run header.
    expect(paramsHash({ x: 1, y: 2 })).toBe(paramsHash({ y: 2, x: 1 }))
    expect(paramsHash({ x: 1 })).not.toBe(paramsHash({ x: 2 }))
  })

  it('runHeader prints the official caliber fields', () => {
    const line = runHeader({
      difficulty: 'hard',
      stageCount: 35,
      seedCount: 60,
      stageIndex: 0,
      maxTicks: 36000,
      params: { a: 1 },
    })
    expect(line).toContain('difficulty=hard')
    expect(line).toContain('stages=35')
    expect(line).toContain('seeds=60')
    expect(line).toContain('stageIndex=0')
    expect(line).toContain('maxTicks=36000')
    expect(line).toMatch(/params=[0-9a-f]{8}/)
  })
})
