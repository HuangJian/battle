import { describe, it, expect } from 'bun:test'
import { readStoreZip } from '../src/probe/zip'
import {
  buildSessionJson,
  buildSessionPack,
  buildVerdictsJsonl,
  packFilename,
} from '../src/probe/session'
import { ProbeVerdictError, type ProbeVerdict } from '../src/probe/verdict'

// ============================================================
// Human-opening probe — session pack (plan v7 §1.2 / T4)
// ============================================================

const META = {
  course: 'x20-opening',
  courseSha: 'a'.repeat(64),
  startedAt: '2026-09-21T10:00:00.000Z',
  games: [
    { game: 0, stage: 2000, seed: 414009 },
    { game: 1, stage: 2000, seed: 414013 },
  ],
}

function verdict(over: Partial<ProbeVerdict> = {}): ProbeVerdict {
  return {
    game: 0,
    stage: 2000,
    seed: 414009,
    attempts: 1,
    best: { band: 'tough', reason: '' },
    kills: 5,
    deathTick: 800,
    ...over,
  }
}

describe('probe session pack', () => {
  it('names the pack from course + start date', () => {
    expect(packFilename('x20-opening', '2026-09-21T10:00:00.000Z')).toBe(
      'probe-x20-opening-20260921.zip',
    )
  })

  it('session.json carries identity + the game table', () => {
    const json = JSON.parse(buildSessionJson(META)) as Record<string, unknown>
    expect(json.course).toBe('x20-opening')
    expect(json.courseSha).toBe(META.courseSha)
    expect(json.startedAt).toBe(META.startedAt)
    expect(json.games).toEqual(META.games)
  })

  it('verdicts.jsonl is one line per verdict, ordered by game, newline-terminated', () => {
    const jsonl = buildVerdictsJsonl([
      verdict({ game: 1, seed: 414013, best: { band: 'solvable', reason: '' } }),
      verdict(),
    ])
    const lines = jsonl.trimEnd().split('\n')
    expect(lines).toHaveLength(2)
    expect(JSON.parse(lines[0]).game).toBe(0)
    expect(JSON.parse(lines[1]).game).toBe(1)
    expect(jsonl.endsWith('\n')).toBe(true)
  })

  it('builds a zip with session.json + replays + verdicts.jsonl', () => {
    const pack = buildSessionPack({
      meta: META,
      replays: [
        { file: '1.replay', text: '{"format":"bc-replay"}' },
        { file: '0.replay', text: '{"format":"bc-replay","n":0}' },
      ],
      verdicts: [verdict()],
    })
    expect(pack.filename).toBe('probe-x20-opening-20260921.zip')
    const names = readStoreZip(pack.bytes).map((e) => e.name)
    // Replays are sorted by file name; session first, verdicts last.
    expect(names).toEqual(['session.json', '0.replay', '1.replay', 'verdicts.jsonl'])
  })

  it('refuses to build when an unsolvable verdict has no reason', () => {
    expect(() =>
      buildSessionPack({
        meta: META,
        replays: [],
        verdicts: [verdict({ best: { band: 'unsolvable', reason: '  ' } })],
      }),
    ).toThrow(ProbeVerdictError)
  })

  it('an empty session still produces a valid pack', () => {
    const pack = buildSessionPack({ meta: { ...META, games: [] }, replays: [], verdicts: [] })
    const entries = readStoreZip(pack.bytes)
    expect(entries.map((e) => e.name)).toEqual(['session.json', 'verdicts.jsonl'])
    expect(new TextDecoder().decode(entries[1].data)).toBe('')
  })
})
