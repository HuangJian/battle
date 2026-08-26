import { describe, it, expect } from 'bun:test'
import { parseDifficulties } from '../tools/lib/cli'

/**
 * M0 §3.1 protocol applied to difficulty list specs: a bare word ("hard")
 * must parse as ONE key, never iterate into characters (h/a/r/d) — that
 * char-iteration footgun silently ran 4× classic sweeps labeled as four
 * difficulties (2026-08-26, sweep-winrate --difficulties hard).
 */
describe('parseDifficulties', () => {
  const EVAL_KEYS = ['classic', 'hard', 'chaos']
  const ALL_KEYS = ['relax', 'classic', 'hard', 'chaos']

  it('absent spec → fallback defaults', () => {
    expect(parseDifficulties(undefined, EVAL_KEYS)).toEqual(['classic', 'hard', 'chaos'])
  })

  it('"hard" → ["hard"] — one token, not per-character h,a,r,d', () => {
    expect(parseDifficulties('hard', EVAL_KEYS, ALL_KEYS)).toEqual(['hard'])
  })

  it('"classic,hard,chaos" → three keys in order', () => {
    expect(parseDifficulties('classic,hard,chaos', EVAL_KEYS, ALL_KEYS)).toEqual([
      'classic',
      'hard',
      'chaos',
    ])
  })

  it('trims whitespace around tokens', () => {
    expect(parseDifficulties(' hard , chaos ', EVAL_KEYS, ALL_KEYS)).toEqual(['hard', 'chaos'])
  })

  it('unknown key throws with valid keys in the message', () => {
    expect(() => parseDifficulties('hardd', EVAL_KEYS, ALL_KEYS)).toThrow(/hardd/)
  })

  it('empty spec / empty token throws', () => {
    expect(() => parseDifficulties('', EVAL_KEYS, ALL_KEYS)).toThrow()
    expect(() => parseDifficulties('hard,,chaos', EVAL_KEYS, ALL_KEYS)).toThrow()
    expect(() => parseDifficulties(',', EVAL_KEYS, ALL_KEYS)).toThrow()
  })
})
