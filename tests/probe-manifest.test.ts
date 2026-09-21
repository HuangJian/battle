import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import {
  parseProbeManifest,
  parseProbeManifestText,
  ProbeManifestError,
  stageOfGame,
} from '../src/probe/manifest'
import {
  flatten,
  stableStringify,
  courseShaOf,
  defaultArgs,
  parseArgs,
} from '../tools/probe/flatten-manifest'
import {
  PROBE_GAMES,
  PROBE_GAMES_JSON,
  PROBE_LEVEL_PATH,
  PROBE_MANIFEST_TEXT,
} from './probe-fixture'

// ============================================================
// Human-opening probe — manifest + generator (plan v7 §T1)
//
// The manifest is GENERATED per session, not committed: the list is scratch
// (`tmp/probe/`), the served copies are gitignored, and the durable record of a
// run is its session pack. So there is no golden file to compare against here —
// the corpus is built in `tests/probe-fixture.ts` from the one committed input
// (the level file) + the canonical rows, and what is asserted is the CONTRACT
// (shape, validation, determinism), not a frozen copy of today's output.
// ============================================================

const LEVEL_TEXT = readFileSync(PROBE_LEVEL_PATH, 'utf8')
const MANIFEST_TEXT = PROBE_MANIFEST_TEXT

function cloneManifest(): Record<string, unknown> {
  return JSON.parse(MANIFEST_TEXT) as Record<string, unknown>
}

describe('probe manifest (generated per session)', () => {
  it('parses with every field explicit', () => {
    const m = parseProbeManifestText(MANIFEST_TEXT)
    expect(m.course).toBe('x20-opening')
    expect(m.difficulty).toBe('hard')
    expect(m.max_ticks).toBe(12900)
    expect(m.lives).toBe(1)
    expect(m.level).toBe(0)
    expect(m.stages).toHaveLength(4)
    expect(m.games).toHaveLength(7)
    for (const s of m.stages) {
      expect(s.tiles).toHaveLength(26)
      expect(s.tiles[0]).toHaveLength(26)
      expect(s.enemies.length).toBeGreaterThan(0)
      expect(s.playerSpawn).toBeDefined()
      expect(s.enemySpawns.length).toBeGreaterThan(0)
      expect(s.layoutHash).toMatch(/^[0-9a-f]{8}$/)
    }
  })

  it('every game resolves to a declared stage', () => {
    const m = parseProbeManifestText(MANIFEST_TEXT)
    for (const g of m.games) {
      expect(stageOfGame(m, g.game).id).toBe(g.stage)
      expect(g.tick0Hash).toMatch(/^[0-9a-f]{8}$/)
    }
    expect(() => stageOfGame(m, m.games.length)).toThrow(ProbeManifestError)
  })

  it('a list is the whole definition of a course — two lists, two courses', () => {
    const other = JSON.stringify({
      course: 'y30-probe',
      level: 'ladder-c20-lives1',
      games: PROBE_GAMES.slice(0, 2).map((g, i) => ({ ...g, game: i })),
    })
    const a = flatten(LEVEL_TEXT, PROBE_GAMES_JSON)
    const b = flatten(LEVEL_TEXT, other)
    const bDoc = JSON.parse(b.json) as { course: string; games: unknown[] }
    expect(bDoc.course).toBe('y30-probe')
    expect(bDoc.games).toHaveLength(2)
    expect(b.json).not.toBe(a.json)
    // Reproducibility — not a committed copy — is what makes the manifest
    // trustworthy: same level file + same list, same bytes, every time. (This
    // is what replaced the old committed-artifact drift gate.)
    expect(flatten(LEVEL_TEXT, PROBE_GAMES_JSON).json).toBe(a.json)
    // The course name comes from the list, never from a flag — the manifest's
    // basename has to match it for `?probe=<course>` to find the file.
    expect(parseProbeManifestText(b.json).course).toBe('y30-probe')
  })

  it('generator is deterministic and key-order independent', () => {
    const a = stableStringify({ b: 1, a: [{ d: 2, c: 3 }] })
    const b = stableStringify({ a: [{ c: 3, d: 2 }], b: 1 })
    expect(a).toBe(b)
    expect(flatten(LEVEL_TEXT, PROBE_GAMES_JSON).json).toBe(
      flatten(LEVEL_TEXT, PROBE_GAMES_JSON).json,
    )
  })

  it('courseSha is checkout-stable (CRLF normalised)', () => {
    expect(courseShaOf('a\r\nb')).toBe(courseShaOf('a\nb'))
    expect(courseShaOf(LEVEL_TEXT)).toMatch(/^[0-9a-f]{64}$/)
  })
})

describe('probe manifest validation (no silent defaults)', () => {
  it('rejects a stage without enemySpawns', () => {
    const raw = cloneManifest()
    delete (raw.stages as Array<Record<string, unknown>>)[0].enemySpawns
    expect(() => parseProbeManifest(raw)).toThrow(/enemySpawns/)
  })

  it('rejects a stage without playerSpawn', () => {
    const raw = cloneManifest()
    delete (raw.stages as Array<Record<string, unknown>>)[0].playerSpawn
    expect(() => parseProbeManifest(raw)).toThrow(/playerSpawn/)
  })

  it('rejects a malformed tile row', () => {
    const raw = cloneManifest()
    const stage = (raw.stages as Array<Record<string, unknown>>)[0]
    const tiles = stage.tiles as string[]
    tiles[3] = 'X'.repeat(26)
    expect(() => parseProbeManifest(raw)).toThrow(/tiles\[3\]/)
  })

  it('rejects a non-integer seed', () => {
    const raw = cloneManifest()
    ;(raw.games as Array<Record<string, unknown>>)[2].seed = 1.5
    expect(() => parseProbeManifest(raw)).toThrow(/seed/)
  })

  it('rejects a game pointing at an undeclared stage', () => {
    const raw = cloneManifest()
    ;(raw.games as Array<Record<string, unknown>>)[1].stage = 9999
    expect(() => parseProbeManifest(raw)).toThrow(/不在 stages 表里/)
  })

  it('rejects a game index that does not match its position', () => {
    const raw = cloneManifest()
    ;(raw.games as Array<Record<string, unknown>>)[1].game = 7
    expect(() => parseProbeManifest(raw)).toThrow(/必须等于下标/)
  })

  it('rejects a non-sha256 courseSha', () => {
    const raw = cloneManifest()
    raw.courseSha = 'deadbeef'
    expect(() => parseProbeManifest(raw)).toThrow(/courseSha/)
  })

  it('rejects a bad tick0Hash', () => {
    const raw = cloneManifest()
    ;(raw.games as Array<Record<string, unknown>>)[0].tick0Hash = 'NOTAHASH'
    expect(() => parseProbeManifest(raw)).toThrow(/tick0Hash/)
  })

  it('rejects malformed JSON loudly', () => {
    expect(() => parseProbeManifestText('{ nope')).toThrow(ProbeManifestError)
  })
})

describe('probe manifest generator — a second course needs no code change', () => {
  it('reads scratch, writes served, and needs nothing versioned but the level file', () => {
    const d = defaultArgs()
    // The list is scratch and the served copies are generated (both gitignored
    // — see .gitignore `/public/probe/`); the level file is the ONE committed
    // input and it belongs to the training stack anyway.
    expect(d.games.startsWith('tmp/')).toBe(true)
    expect(d.out.startsWith('public/probe/')).toBe(true)
    expect(d.index.startsWith('public/probe/')).toBe(true)
    expect(d.level.startsWith('nn-training/levels/')).toBe(true)
  })

  it('zero args stay the default course', () => {
    // Pinned literally on purpose: these are the paths the zero-arg command
    // reads and writes, so shifting one silently changes what it generates.
    expect(defaultArgs()).toEqual({
      games: 'tmp/probe/x20-opening.games.json',
      level: 'nn-training/levels/ladder-c20-lives1.jsonc',
      out: 'public/probe/x20-opening.json',
      index: 'public/probe/index.json',
    })
    expect(parseArgs([])).toEqual(defaultArgs())
  })

  it('another course is named by flags', () => {
    const args = parseArgs([
      '--games',
      'tmp/probe/y30.games.json',
      '--level',
      'nn-training/levels/y30.jsonc',
      '--out',
      'public/probe/y30.json',
      '--index',
      'tmp/probe/y30-index.json',
    ])
    expect(args).toEqual({
      games: 'tmp/probe/y30.games.json',
      level: 'nn-training/levels/y30.jsonc',
      out: 'public/probe/y30.json',
      index: 'tmp/probe/y30-index.json',
    })
  })

  it('refuses an unknown flag or a valueless one instead of guessing', () => {
    expect(() => parseArgs(['--stage', '1'])).toThrow(/未知参数 --stage/)
    expect(() => parseArgs(['--games'])).toThrow(/缺少取值/)
  })
})
