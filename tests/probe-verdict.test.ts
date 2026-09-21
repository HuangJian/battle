import { describe, it, expect } from 'bun:test'
import {
  BAND_TABLE,
  PROBE_BANDS,
  aggregateSeedVerdict,
  bandRequiresReason,
  isProbeBand,
  suggestBand,
  validateProbeVerdict,
  verdictOfBand,
  ProbeVerdictError,
  type ProbeVerdict,
} from '../src/probe/verdict'

// ============================================================
// Human-opening probe — band → verdict aggregation (plan §0)
// ============================================================

describe('probe band table', () => {
  it('covers every band exactly once', () => {
    expect(BAND_TABLE.map((b) => b.band).sort()).toEqual([...PROBE_BANDS].sort())
  })

  it('aggregates exactly per the plan table (solvable/slight → solvable)', () => {
    expect(verdictOfBand('solvable')).toBe('solvable')
    expect(verdictOfBand('slight')).toBe('solvable')
    expect(verdictOfBand('tough')).toBe('unknown')
    expect(verdictOfBand('unsolvable')).toBe('unknown')
    expect(verdictOfBand('retry')).toBeNull()
  })

  it('only "unsolvable" demands a reason', () => {
    for (const b of PROBE_BANDS) {
      expect(bandRequiresReason(b)).toBe(b === 'unsolvable')
    }
  })

  it('suggests a band from the run outcome', () => {
    expect(suggestBand(20, true)).toBe('solvable')
    expect(suggestBand(14, false)).toBe('slight')
    expect(suggestBand(19, false)).toBe('slight')
    expect(suggestBand(4, false)).toBe('tough')
    expect(suggestBand(13, false)).toBe('tough')
    expect(suggestBand(3, false)).toBe('unsolvable')
    expect(suggestBand(0, false)).toBe('unsolvable')
  })

  it('a clear always suggests solvable regardless of kills', () => {
    expect(suggestBand(20, true)).toBe('solvable')
  })
})

describe('seed verdict aggregation', () => {
  it('one solvable band is enough', () => {
    expect(aggregateSeedVerdict(['tough', 'unsolvable', 'slight'])).toBe('solvable')
  })

  it('retry never counts', () => {
    expect(aggregateSeedVerdict(['retry', 'retry'])).toBe('unknown')
  })

  it('all-tough/unsolvable is still unknown (human death carries no information)', () => {
    expect(aggregateSeedVerdict(['tough', 'unsolvable'])).toBe('unknown')
    expect(aggregateSeedVerdict([])).toBe('unknown')
  })
})

describe('verdict validation', () => {
  const base: ProbeVerdict = {
    game: 1,
    stage: 2000,
    seed: 414013,
    attempts: 2,
    best: { band: 'tough', reason: '' },
    kills: 6,
    deathTick: 900,
  }

  it('accepts a tough verdict without a reason', () => {
    expect(() => validateProbeVerdict(base)).not.toThrow()
  })

  it("refuses unsolvable without a reason (the protocol's only hard check)", () => {
    expect(() =>
      validateProbeVerdict({ ...base, best: { band: 'unsolvable', reason: '   ' } }),
    ).toThrow(ProbeVerdictError)
    expect(() =>
      validateProbeVerdict({ ...base, best: { band: 'unsolvable', reason: 'wall in the way' } }),
    ).not.toThrow()
  })

  it('never validates kill counts against the band (§4 rule 6)', () => {
    expect(() =>
      validateProbeVerdict({ ...base, kills: 20, best: { band: 'tough', reason: '' } }),
    ).not.toThrow()
  })

  it('rejects unknown bands and non-positive attempts', () => {
    expect(() =>
      validateProbeVerdict({ ...base, best: { band: 'nope' as never, reason: '' } }),
    ).toThrow(ProbeVerdictError)
    expect(() => validateProbeVerdict({ ...base, attempts: 0 })).toThrow(ProbeVerdictError)
  })

  it('isProbeBand narrows only known bands', () => {
    expect(isProbeBand('solvable')).toBe(true)
    expect(isProbeBand('SOLVABLE')).toBe(false)
    expect(isProbeBand(3)).toBe(false)
  })
})
