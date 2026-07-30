import { describe, it, expect } from 'bun:test'
import { serializeReplayFile, parseReplayFile, buildReplayFilename } from '../src/replay/file'
import { GAME_VERSION } from '../src/snapshot/config'
import { FRAME_SCHEMA_VERSION } from '../src/replay/config'
import { packFrames } from '../src/replay/pack'
import type { InputFrame } from '../src/replay/types'

// ============================================================
// Helpers
// ============================================================

const SAMPLE_FRAMES: InputFrame[] = [
  { direction: 'up', firing: true, guard: false, frenzy: false },
  { direction: 'left', firing: false, guard: true, frenzy: false },
  { direction: null, firing: false, guard: false, frenzy: false },
]

function makeMinimalSnapshot(): any {
  return {
    frame: 0,
    state: 'playing',
    score: 0,
    lives: 3,
    playerLevel: 0,
    killCount: 0,
    enemiesSpawned: 0,
    rngState: [12345, 67890],
    stageIndex: 0,
    tileMap: { terrain: [] },
    tanks: [],
    bullets: [],
    powerUps: [],
    spawnQueue: [],
    freezeTimer: 0,
    spawnPointIndex: 0,
    spawnSeqCounter: 0,
    activeCommanderId: null,
    commanderQuotaRemaining: 0,
    directiveSeqCounter: 0,
  }
}

// ============================================================
// serializeReplayFile + parseReplayFile round-trip
// ============================================================

describe('Replay file format round-trip', () => {
  it('serializes and parses a sim-source replay losslessly', () => {
    const snapshot = makeMinimalSnapshot()
    const frames = packFrames(SAMPLE_FRAMES)
    const text = serializeReplayFile({
      source: 'sim',
      seed: 42,
      sim: {
        seed: 42,
        difficulty: 'classic',
        stageIndex: 0,
        stageName: 'STAGE 1',
        outcome: 'stage_clear',
        status: 'clear',
        maxTicks: 36000,
        godAIParams: { aimError: 0.03 },
      },
      finalState: { score: 1500, lives: 2, killCount: 18, ticks: 9600 },
      initialSnapshot: snapshot,
      frames,
      totalTicks: 3,
      metadata: {
        stage: 0,
        stageName: 'STAGE 1',
        difficulty: 'classic',
        lives: 3,
        playerLevel: 1,
        score: 1500,
        killCount: 18,
        enemiesTotal: 20,
        playTimeMs: 160,
      },
    })

    const result = parseReplayFile(text)
    expect(result).toHaveProperty('replay')
    if ('error' in result) throw new Error(`Parse failed: ${result.error}`)

    const { replay, envelope } = result as { replay: any; envelope: any }
    // Envelope fields
    expect(envelope.format).toBe('bc-replay')
    expect(envelope.formatVersion).toBe(1)
    expect(envelope.gameVersion).toBe(GAME_VERSION)
    expect(envelope.frameSchemaVersion).toBe(FRAME_SCHEMA_VERSION)
    expect(envelope.source).toBe('sim')
    expect(envelope.sim?.seed).toBe(42)
    expect(envelope.sim?.status).toBe('clear')
    expect(envelope.finalState?.score).toBe(1500)
    expect(envelope.finalState?.ticks).toBe(9600)

    // Replay fields
    expect(replay.totalTicks).toBe(3)
    expect(replay.frames.length).toBe(frames.length)
    // Frames match byte-for-byte
    for (let i = 0; i < frames.length; i++) {
      expect(replay.frames[i]).toBe(frames[i])
    }
    // Metadata
    expect(replay.metadata.stage).toBe(0)
    expect(replay.metadata.stageName).toBe('STAGE 1')
    expect(replay.metadata.score).toBe(1500)
    expect(replay.metadata.killCount).toBe(18)
    // New UUID generated
    expect(replay.id).not.toBe('test-replay-000')
    // Thumbnail null in headless
    expect(replay.thumbnail).toBeNull()
    expect(replay.isFavorite).toBe(false)
    // Seed round-trips (carried in the replay envelope, not just sim)
    expect(replay.seed).toBe(42)
  })

  it('serializes and parses a browser-source replay', () => {
    const snapshot = makeMinimalSnapshot()
    const frames = packFrames(SAMPLE_FRAMES)
    const text = serializeReplayFile({
      source: 'browser',
      seed: 12345,
      initialSnapshot: snapshot,
      frames,
      totalTicks: 3,
      metadata: {
        stage: 0,
        stageName: 'STAGE 1',
        difficulty: 'classic',
        lives: 3,
        playerLevel: 0,
        score: 100,
        killCount: 5,
        enemiesTotal: 20,
        playTimeMs: 160,
      },
    })

    const result = parseReplayFile(text)
    expect(result).toHaveProperty('replay')
    if ('error' in result) throw new Error(`Parse failed: ${result.error}`)
    expect((result as any).envelope.source).toBe('browser')
    expect((result as any).envelope.sim).toBeUndefined()
    // Browser-source seed still round-trips via the replay envelope
    expect((result as any).replay.seed).toBe(12345)
  })

  it('reconciles initialSnapshot.stageIndex with metadata.stage (S32 bug)', () => {
    // Regression: sim-generated replays recorded the stage via
    // loadStageData(stage, 0), so the snapshot carried stageIndex 0 even
    // though metadata.stage was correct (e.g. 31 = S32). On playback the
    // world was restored with stageIndex 0 and the HUD showed "STAGE 01".
    const snapshot = makeMinimalSnapshot() // stageIndex: 0
    const frames = packFrames(SAMPLE_FRAMES)
    const text = serializeReplayFile({
      source: 'sim',
      seed: 2483393699,
      sim: {
        seed: 2483393699,
        difficulty: 'classic',
        stageIndex: 31,
        stageName: 'Diamond',
        outcome: 'stage_clear',
        status: 'clear',
        maxTicks: 36000,
      },
      initialSnapshot: snapshot,
      frames,
      totalTicks: 3,
      metadata: {
        stage: 31,
        stageName: 'Diamond',
        difficulty: 'classic',
        lives: 3,
        playerLevel: 1,
        score: 5400,
        killCount: 15,
        enemiesTotal: 20,
        playTimeMs: 160,
      },
    })

    const result = parseReplayFile(text)
    if ('error' in result) throw new Error(`Parse failed: ${result.error}`)
    const { replay } = result as { replay: any }

    // The snapshot's stale stageIndex must be corrected to match metadata.
    expect(replay.initialSnapshot.stageIndex).toBe(31)
    expect(replay.metadata.stage).toBe(31)
  })
})

// ============================================================
// Error handling
// ============================================================

describe('Replay file parse errors', () => {
  it('rejects invalid JSON', () => {
    const r = parseReplayFile('not json')
    expect(r).toHaveProperty('error')
  })

  it('rejects unknown format', () => {
    const r = parseReplayFile(
      JSON.stringify({ format: 'wrong', formatVersion: 1, frameSchemaVersion: 1 }),
    )
    expect(r).toHaveProperty('error')
    expect((r as any).error).toMatch(/Unknown format/)
  })

  it('rejects bad formatVersion', () => {
    const r = parseReplayFile(
      JSON.stringify({ format: 'bc-replay', formatVersion: 99, frameSchemaVersion: 1 }),
    )
    expect(r).toHaveProperty('error')
    expect((r as any).error).toMatch(/format version/)
  })

  it('rejects bad frameSchemaVersion', () => {
    const r = parseReplayFile(
      JSON.stringify({ format: 'bc-replay', formatVersion: 1, frameSchemaVersion: 99 }),
    )
    expect(r).toHaveProperty('error')
    expect((r as any).error).toMatch(/frame schema version/)
  })

  it('rejects missing replay section', () => {
    const r = parseReplayFile(
      JSON.stringify({ format: 'bc-replay', formatVersion: 1, frameSchemaVersion: 1 }),
    )
    expect(r).toHaveProperty('error')
    expect((r as any).error).toMatch(/Missing replay/)
  })

  it('rejects missing initialSnapshot', () => {
    const r = parseReplayFile(
      JSON.stringify({
        format: 'bc-replay',
        formatVersion: 1,
        frameSchemaVersion: 1,
        replay: { framesBase64: 'AA==', totalTicks: 1, metadata: {} },
      }),
    )
    expect(r).toHaveProperty('error')
    expect((r as any).error).toMatch(/initialSnapshot/)
  })

  it('handles garbage base64 gracefully (either error or decode)', () => {
    // Bun's Buffer.from is lenient with invalid base64 — it may decode
    // garbage without throwing. The important thing is no crash.
    const r = parseReplayFile(
      JSON.stringify({
        format: 'bc-replay',
        formatVersion: 1,
        frameSchemaVersion: 1,
        replay: { initialSnapshot: {}, framesBase64: '!!!invalid!!!', totalTicks: 1, metadata: {} },
      }),
    )
    // Either parse succeeds (garbage decoded) or returns error — both acceptable
    expect(r).toBeDefined()
  })
})

// ============================================================
// buildReplayFilename
// ============================================================

describe('buildReplayFilename', () => {
  it('generates correct filename for stage_clear', () => {
    const name = buildReplayFilename({
      difficulty: 'classic',
      stageIndex: 0,
      status: 'clear',
      lives: 3,
      totalTicks: 13800,
      seed: 123,
    })
    expect(name).toBe('classic-s01-clear-l3-t230-seed123.replay')
  })

  it('generates correct filename for base_destroyed', () => {
    const name = buildReplayFilename({
      difficulty: 'chaos',
      stageIndex: 34,
      status: 'base',
      lives: 0,
      totalTicks: 6000,
      seed: 9999,
    })
    expect(name).toBe('chaos-s35-base-l0-t100-seed9999.replay')
  })

  it('generates correct filename for lives_exhausted', () => {
    const name = buildReplayFilename({
      difficulty: 'hard',
      stageIndex: 11,
      status: 'died',
      lives: 0,
      totalTicks: 18000,
      seed: 42,
    })
    expect(name).toBe('hard-s12-died-l0-t300-seed42.replay')
  })

  it('generates correct filename for timeout', () => {
    const name = buildReplayFilename({
      difficulty: 'relax',
      stageIndex: 5,
      status: 'timeout',
      lives: 1,
      totalTicks: 36000,
      seed: 777,
    })
    expect(name).toBe('relax-s06-timeout-l1-t600-seed777.replay')
  })
})
