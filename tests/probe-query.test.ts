import { describe, it, expect } from 'bun:test'
import { probeGameHref, requestedProbeCourse, resolveProbeTarget } from '../src/probe/query'
// The course corpus is GENERATED (a probe course is a session, not a repo
// artifact) — see tests/probe-fixture.ts.
import { PROBE_MANIFEST as MANIFEST } from './probe-fixture'

// ============================================================
// Human-opening probe — boot query (plan v7 §1.2 / T2)
//
// Exactly two runtime rejection paths: course mismatch and game out of range.
// `courseSha` is deliberately NOT checked at runtime — the browser cannot
// recompute it (the level file is not served).
// ============================================================

describe('probe boot query', () => {
  it('accepts the course with an explicit game index', () => {
    expect(resolveProbeTarget('?probe=x20-opening&game=3', MANIFEST)).toEqual({
      ok: true,
      course: 'x20-opening',
      game: 3,
    })
  })

  it('defaults to game 0 when the index is omitted', () => {
    expect(resolveProbeTarget('?probe=x20-opening', MANIFEST)).toEqual({
      ok: true,
      course: 'x20-opening',
      game: 0,
    })
  })

  it('reports an absent probe param (not a rejection — just a normal load)', () => {
    expect(resolveProbeTarget('?fireLineDetour=1', MANIFEST)).toEqual({
      ok: false,
      reason: 'absent',
    })
  })

  it('rejects a course mismatch', () => {
    expect(resolveProbeTarget('?probe=x20-other&game=0', MANIFEST)).toEqual({
      ok: false,
      reason: 'course-mismatch',
    })
  })

  it('rejects a game index out of range', () => {
    expect(resolveProbeTarget('?probe=x20-opening&game=7', MANIFEST)).toEqual({
      ok: false,
      reason: 'game-out-of-range',
    })
    expect(resolveProbeTarget('?probe=x20-opening&game=999', MANIFEST)).toEqual({
      ok: false,
      reason: 'game-out-of-range',
    })
  })

  it('rejects a non-integer game index', () => {
    expect(resolveProbeTarget('?probe=x20-opening&game=abc', MANIFEST)).toEqual({
      ok: false,
      reason: 'game-not-integer',
    })
    expect(resolveProbeTarget('?probe=x20-opening&game=-1', MANIFEST)).toEqual({
      ok: false,
      reason: 'game-not-integer',
    })
  })

  it('accepts an already-parsed URLSearchParams', () => {
    expect(resolveProbeTarget(new URLSearchParams('probe=x20-opening&game=6'), MANIFEST)).toEqual({
      ok: true,
      course: 'x20-opening',
      game: 6,
    })
  })

  it('builds launcher hrefs the resolver accepts (round-trip)', () => {
    const href = probeGameHref('x20-opening', 5)
    expect(href).toBe('?probe=x20-opening&game=5')
    expect(resolveProbeTarget(href, MANIFEST)).toEqual({ ok: true, course: 'x20-opening', game: 5 })
  })
})

describe('requested course (read BEFORE the manifest is fetched)', () => {
  it('reads the course name out of the query', () => {
    expect(requestedProbeCourse('?probe=x20-opening&game=3')).toBe('x20-opening')
    expect(requestedProbeCourse(new URLSearchParams('probe=y30&game=0'))).toBe('y30')
  })

  it('is null when absent or empty — the caller then fetches nothing at all', () => {
    expect(requestedProbeCourse('?game=3')).toBeNull()
    expect(requestedProbeCourse('?probe=')).toBeNull()
    expect(requestedProbeCourse('')).toBeNull()
  })
})
