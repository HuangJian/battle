/**
 * M1 decision-chain invariants (plan/God-AI-Redesign-v2 §3.2, DECISIONS §99).
 *
 * Selection in runChain is ARRAY order (first commit wins, early exit); the
 * `weight` field is metadata that M2 will make data-driven. This test locks
 * the invariant that the array order strictly mirrors ACTION_WEIGHTS
 * descending — a reorder without a weight update silently changes behavior,
 * and a weight change without a reorder lies about priority.
 */
import { describe, it, expect } from 'bun:test'
import { ACTION_WEIGHTS, orderedCandidates } from '../src/ai/god/DecisionCore'
import { CANDIDATES } from '../src/ai/god/think'

describe('M1 decision-chain invariants', () => {
  it('chain order strictly matches ACTION_WEIGHTS descending', () => {
    for (let i = 1; i < CANDIDATES.length; i++) {
      expect(CANDIDATES[i].weight).toBeLessThan(CANDIDATES[i - 1].weight)
      // Each candidate's weight must equal the declared ACTION_WEIGHTS entry.
      expect(CANDIDATES[i].weight).toBe(ACTION_WEIGHTS[CANDIDATES[i].id])
    }
    expect(CANDIDATES[0].weight).toBe(ACTION_WEIGHTS[CANDIDATES[0].id])
  })

  it('the actions match the documented M1 chain + M3 survive', () => {
    expect(CANDIDATES.map((c) => c.id)).toEqual([
      'dodge',
      'interceptBase',
      'pickupHigh',
      'aggro',
      'pickupMid',
      'engage',
      'pickupLow',
      'hunt',
      'survive',
    ])
    expect(ACTION_WEIGHTS).toEqual({
      dodge: 1000,
      interceptBase: 900,
      pickupHigh: 800,
      aggro: 700,
      pickupMid: 600,
      engage: 500,
      pickupLow: 400,
      hunt: 200,
      // M3: survive 默认 0 — 不激活（byte-identical）；M4 经 actionWeights 提升。
      survive: 0,
    })
  })

  it('every Candidate id is a declared ActionId', () => {
    const ids = Object.keys(ACTION_WEIGHTS)
    for (const c of CANDIDATES) {
      expect(ids).toContain(c.id)
    }
  })

  it('M2: orderedCandidates with no overrides returns the base chain unchanged', () => {
    expect(orderedCandidates(CANDIDATES, undefined)).toBe(CANDIDATES)
  })

  it('M2: orderedCandidates applies weight overrides in descending effective order', () => {
    // Promote engage above pickupMid (600→650): engage must move ahead of it.
    const ordered = orderedCandidates(CANDIDATES, { engage: 650 })
    const ids = ordered.map((c) => c.id)
    expect(ids.indexOf('engage')).toBeLessThan(ids.indexOf('pickupMid'))
    // All candidates still present, no duplicates.
    expect(ordered.length).toBe(CANDIDATES.length)
    expect(new Set(ids).size).toBe(CANDIDATES.length)
    // Effective weights strictly non-increasing across the list.
    const eff = (c: (typeof CANDIDATES)[number]): number =>
      c.id === 'engage' ? 650 : ACTION_WEIGHTS[c.id]
    for (let i = 1; i < ordered.length; i++) {
      expect(eff(ordered[i])).toBeLessThanOrEqual(eff(ordered[i - 1]))
    }
  })
})
