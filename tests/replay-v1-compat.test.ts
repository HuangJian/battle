import { describe, it, expect } from 'bun:test'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import type { InputLike } from '../src/game/Input'
import type { Direction } from '../src/constants'
import { cloneWorld } from '../src/snapshot/WorldSerializer'
import { parseReplayFile, serializeReplayFile } from '../src/replay/file'
import { ReplayManager } from '../src/replay/ReplayManager'
import { ReplayInput } from '../src/replay/ReplayInput'
import { PlaybackController } from '../src/replay/PlaybackController'
import {
  FRAME_SCHEMA_V1,
  FRAME_SCHEMA_VERSION,
  isSupportedFrameSchema,
  SUPPORTED_FRAME_SCHEMA_VERSIONS,
} from '../src/replay/config'
import { frameSchemaVersionOf, packFrames } from '../src/replay/pack'
import type { InputFrame, ReplayMetadata } from '../src/replay/types'

// ================================================================
// Frame-schema compatibility (DECISIONS #76)
//
// The packed blob's leading byte is the ONLY authority on layout.
// Envelope `frameSchemaVersion` and `Replay.schemaVersion` are
// descriptive and must agree with it. Readers accept every schema
// this build can decode — v1 files stay importable and playable.
// ================================================================

const SAMPLE: InputFrame[] = [
  { direction: 'up', firing: true, guard: false, frenzy: false },
  { direction: 'left', firing: false, guard: true, frenzy: false },
  { direction: null, firing: false, guard: false, frenzy: false },
  { direction: 'right', firing: true, guard: false, frenzy: true },
]

const METADATA: ReplayMetadata = {
  stage: 0,
  stageName: 'STAGE 1',
  difficulty: 'classic',
  lives: 3,
  playerLevel: 0,
  score: 0,
  killCount: 0,
  enemiesTotal: 20,
  playTimeMs: 0,
}

class IdleInput implements InputLike {
  getMoveDirection(): Direction | null {
    return null
  }
  isFiring(): boolean {
    return false
  }
  wasItemPressed(_k: 'guard' | 'frenzy'): boolean {
    return false
  }
  endFrame(): void {}
  reset(): void {}
}

function makeSnapshot() {
  const w = new World()
  w.startGame('classic', w.themeKey, 0)
  return cloneWorld(w)
}

function writeFile(frames: Uint8Array, totalTicks: number): string {
  return serializeReplayFile({
    source: 'sim',
    seed: 42,
    initialSnapshot: makeSnapshot(),
    frames,
    totalTicks,
    metadata: METADATA,
  })
}

// ============================================================
// The blob is the source of truth
// ============================================================

describe('frame schema — the blob byte is authoritative', () => {
  it('frameSchemaVersionOf reads the leading byte, 0 for empty', () => {
    expect(frameSchemaVersionOf(packFrames(SAMPLE))).toBe(FRAME_SCHEMA_V1)
    expect(frameSchemaVersionOf(packFrames(SAMPLE, SAMPLE))).toBe(FRAME_SCHEMA_VERSION)
    expect(frameSchemaVersionOf(new Uint8Array(0))).toBe(0)
    expect(frameSchemaVersionOf(null)).toBe(0)
  })

  it('both v1 and v2 are supported; nothing else is', () => {
    expect(SUPPORTED_FRAME_SCHEMA_VERSIONS).toEqual([FRAME_SCHEMA_V1, FRAME_SCHEMA_VERSION])
    expect(isSupportedFrameSchema(FRAME_SCHEMA_V1)).toBe(true)
    expect(isSupportedFrameSchema(FRAME_SCHEMA_VERSION)).toBe(true)
    expect(isSupportedFrameSchema(0)).toBe(false)
    expect(isSupportedFrameSchema(0x99)).toBe(false)
    expect(isSupportedFrameSchema(undefined)).toBe(false)
    expect(isSupportedFrameSchema('2')).toBe(false)
  })

  it('serializeReplayFile stamps the envelope with the blob version', () => {
    const v1 = JSON.parse(writeFile(packFrames(SAMPLE), SAMPLE.length))
    const v2 = JSON.parse(writeFile(packFrames(SAMPLE, SAMPLE), SAMPLE.length))
    expect(v1.frameSchemaVersion).toBe(FRAME_SCHEMA_V1)
    expect(v2.frameSchemaVersion).toBe(FRAME_SCHEMA_VERSION)
  })
})

// ============================================================
// Parsing accepts every decodable schema
// ============================================================

describe('parseReplayFile — v1 compatibility', () => {
  it('accepts a v1 file and reports schemaVersion 1', () => {
    const result = parseReplayFile(writeFile(packFrames(SAMPLE), SAMPLE.length))
    if ('error' in result) throw new Error(`Parse failed: ${result.error}`)
    expect(result.replay.schemaVersion).toBe(FRAME_SCHEMA_V1)
    expect(result.replay.frames2).toBeNull()
    expect(result.replay.totalTicks).toBe(SAMPLE.length)
  })

  it('accepts a v2 coop file and rebuilds the standalone P2 stream', () => {
    const result = parseReplayFile(writeFile(packFrames(SAMPLE, SAMPLE), SAMPLE.length))
    if ('error' in result) throw new Error(`Parse failed: ${result.error}`)
    expect(result.replay.schemaVersion).toBe(FRAME_SCHEMA_VERSION)
    expect(result.replay.frames2).not.toBeNull()
    // frames2 is a SINGLE stream, so it must carry the v1 header.
    expect(frameSchemaVersionOf(result.replay.frames2)).toBe(FRAME_SCHEMA_V1)
    expect(result.replay.frames2!.length).toBe(SAMPLE.length + 1)
  })

  it('tolerates the historical envelope lie (declared v2, v1 bytes)', () => {
    const env = JSON.parse(writeFile(packFrames(SAMPLE), SAMPLE.length))
    env.frameSchemaVersion = FRAME_SCHEMA_VERSION // what older builds wrote
    const result = parseReplayFile(JSON.stringify(env))
    if ('error' in result) throw new Error(`Parse failed: ${result.error}`)
    // The bytes win — this is a v1 replay no matter what the envelope claims.
    expect(result.replay.schemaVersion).toBe(FRAME_SCHEMA_V1)
  })

  it('still rejects an undecodable declared version', () => {
    const env = JSON.parse(writeFile(packFrames(SAMPLE), SAMPLE.length))
    env.frameSchemaVersion = 99
    const r = parseReplayFile(JSON.stringify(env))
    expect(r).toHaveProperty('error')
    expect((r as { error: string }).error).toMatch(/frame schema version/)
  })

  it('still rejects an undecodable blob even when the envelope looks fine', () => {
    const env = JSON.parse(writeFile(packFrames(SAMPLE), SAMPLE.length))
    env.replay.framesBase64 = Buffer.from(new Uint8Array([0x99, 0x00, 0x00])).toString('base64')
    const r = parseReplayFile(JSON.stringify(env))
    expect(r).toHaveProperty('error')
    expect((r as { error: string }).error).toMatch(/frame schema version/)
  })
})

// ============================================================
// canPlay + real on-disk artifacts
// ============================================================

describe('ReplayManager.canPlay — v1 is playable', () => {
  it('accepts a v1 blob and rejects an unknown one', () => {
    const mgr = new ReplayManager()
    const v1 = mgr.create('clear', makeSnapshot(), packFrames(SAMPLE), SAMPLE.length, METADATA)
    expect(v1.schemaVersion).toBe(FRAME_SCHEMA_V1)
    expect(mgr.canPlay(v1)).toBe(true)
    expect(mgr.canPlay({ ...v1, frames: new Uint8Array([0x99, 0x00]) })).toBe(false)
  })
})

describe('the .replay artifacts in replays/ are importable', () => {
  const dir = join(import.meta.dir, '..', 'replays')
  // `replays/` is gitignored, so a clean checkout ships with no stored
  // artifacts. Reads must not throw when the directory is absent — treat a
  // missing dir as "no files" so the block skips gracefully (see skipIf
  // below) instead of raising an unhandled error that fails the whole run.
  let files: string[] = []
  try {
    files = readdirSync(dir).filter((f) => f.endsWith('.replay'))
  } catch {
    files = []
  }

  // `replays/` is gitignored, so a clean checkout ships with no stored
  // artifacts. This block is a regression guard for *existing* replays — when
  // none are present there is nothing to guard, so skip instead of failing the
  // suite. Drop a `.replay` into replays/ and these checks run automatically.
  it.skipIf(files.length === 0)('finds at least one stored replay to guard', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  for (const name of files) {
    it(`imports and starts playback: ${name}`, () => {
      const result = parseReplayFile(readFileSync(join(dir, name), 'utf8'))
      if ('error' in result) throw new Error(`${name}: ${result.error}`)
      const replay = result.replay

      expect(isSupportedFrameSchema(replay.schemaVersion)).toBe(true)
      expect(replay.schemaVersion).toBe(frameSchemaVersionOf(replay.frames))
      expect(new ReplayManager().canPlay(replay)).toBe(true)

      // The frames must actually decode into the recorded number of ticks.
      const input = new ReplayInput(replay.frames)
      expect(input.totalFrames).toBe(replay.totalTicks)

      // And playback must be able to take the world over without throwing.
      const world = new World()
      world.startGame('classic', world.themeKey, 0)
      const sim = new Simulation(world, new IdleInput())
      const pb = new PlaybackController(replay)
      pb.start(world, sim)
      expect(world.state).toBe('playing')
      for (let i = 0; i < 30; i++) sim.tick()
    })
  }
})
