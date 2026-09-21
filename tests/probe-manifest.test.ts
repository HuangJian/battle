import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  parseProbeManifest,
  parseProbeManifestText,
  ProbeManifestError,
  stageOfGame,
} from '../src/probe/manifest'
import { flatten, stableStringify, courseShaOf } from '../tools/probe/flatten-manifest'

// ============================================================
// Human-opening probe — manifest + generator (plan v7 §T1)
//
// The generated manifest is a COMMITTED build artifact; these tests are the
// drift gate: regenerate from the committed inputs and byte-compare.
// ============================================================

const ROOT = join(import.meta.dir, '..')
const MANIFEST_PATH = join(ROOT, 'public/probe/x20-opening.json')
const GAMES_PATH = join(ROOT, 'tools/probe/x20-opening.games.json')
const LEVEL_PATH = join(ROOT, 'nn-training/levels/ladder-c20-lives1.jsonc')

const LEVEL_TEXT = readFileSync(LEVEL_PATH, 'utf8')
const GAMES_TEXT = readFileSync(GAMES_PATH, 'utf8')
const MANIFEST_TEXT = readFileSync(MANIFEST_PATH, 'utf8')

function cloneManifest(): Record<string, unknown> {
  return JSON.parse(MANIFEST_TEXT) as Record<string, unknown>
}

describe('probe manifest (committed artifact)', () => {
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

  it('generator output is byte-identical to the committed file (CI drift gate)', () => {
    const regenerated = flatten(LEVEL_TEXT, GAMES_TEXT).json
    expect(regenerated).toBe(MANIFEST_TEXT)
  })

  it('generator is deterministic and key-order independent', () => {
    const a = stableStringify({ b: 1, a: [{ d: 2, c: 3 }] })
    const b = stableStringify({ a: [{ c: 3, d: 2 }], b: 1 })
    expect(a).toBe(b)
    expect(flatten(LEVEL_TEXT, GAMES_TEXT).json).toBe(flatten(LEVEL_TEXT, GAMES_TEXT).json)
  })

  it('games.json mirrors the manifest games table', () => {
    const doc = JSON.parse(GAMES_TEXT) as {
      course: string
      games: Array<{ game: number; stage: number; seed: number; tag: string }>
    }
    const m = parseProbeManifestText(MANIFEST_TEXT)
    expect(doc.course).toBe(m.course)
    expect(doc.games.map((g) => [g.game, g.stage, g.seed, g.tag])).toEqual(
      m.games.map((g) => [g.game, g.stage, g.seed, g.tag]),
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
