import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ReplayInput } from '../src/replay/ReplayInput'
import { ReplayManager } from '../src/replay/ReplayManager'
import { PlaybackController } from '../src/replay/PlaybackController'
import { cloneWorld } from '../src/snapshot/WorldSerializer'
import { packFrame, unpackFrame, packFrames, unpackFrames } from '../src/replay/pack'
import { FRAME_SCHEMA_VERSION, FRAME_SCHEMA_V1, REPLAY_RETENTION_POLICIES } from '../src/replay/config'
import type { Direction } from '../src/constants'
import type { InputLike } from '../src/game/Input'
import type { InputFrame } from '../src/replay/types'

/**
 * Replay System — plan/replay.md
 *
 * Guards the Definition of Done:
 *  - Frames pack/unpack losslessly (1 byte each, schema-version prefixed)
 *  - ReplayInput feeds recorded input deterministically (advance() per tick,
 *    endFrame() is a no-op so it can't double-step)
 *  - Schema-version mismatch is rejected (defensive backstop, L3)
 *  - ReplayManager.create() derives duration from tick count, not wall-clock
 *  - canPlay() rejects unplayable replays
 *  - Record → Replay reproduces the exact same World (determinism, the heart
 *    of the whole feature)
 */

// ---- helpers ----

/** A scripted input source: cycles through a fixed sequence of frames. */
class ScriptedInput implements InputLike {
  private seq: InputFrame[]
  private i = 0
  constructor(seq: InputFrame[]) {
    this.seq =
      seq.length > 0 ? seq : [{ direction: null, firing: false, guard: false, frenzy: false }]
  }
  private cur(): InputFrame {
    return this.seq[Math.min(this.i, this.seq.length - 1)]
  }
  getMoveDirection(): Direction | null {
    return this.cur().direction
  }
  isFiring(): boolean {
    return this.cur().firing
  }
  wasItemPressed(kind: 'guard' | 'frenzy'): boolean {
    return kind === 'guard' ? this.cur().guard : this.cur().frenzy
  }
  endFrame(): void {
    /* no-op for live scripted input too — Game calls endFrame per frame,
       but we hand the scripted input directly to the recorder per tick */
  }
  reset(): void {
    this.i = 0
  }
  /** Called once per sim tick during the LIVE recording run. */
  advance(): void {
    if (this.i < this.seq.length - 1) this.i++
  }
}

const SAMPLE_FRAMES: InputFrame[] = [
  { direction: 'up', firing: true, guard: false, frenzy: false },
  { direction: 'left', firing: false, guard: true, frenzy: false },
  { direction: 'down', firing: true, guard: false, frenzy: true },
  { direction: null, firing: false, guard: false, frenzy: false },
  { direction: 'right', firing: false, guard: false, frenzy: false },
]

/** Strip entity id-like fields so two deterministically-equal worlds compare
 *  equal even when their module-global genId counter started at different
 *  values (e.g. `id`, `ownerId`, `bulletId`). */
const ID_KEY_RE = /id$/i
function normalize(snap: unknown): string {
  return JSON.stringify(snap, (key, value) => {
    if (ID_KEY_RE.test(key)) return undefined
    return value
  })
}

// ============================================================
// Frame packing (§3.1–§3.3) — lossless round-trip
// ============================================================

describe('Replay frame packing', () => {
  it('packFrame/unpackFrame round-trips every field', () => {
    for (const f of SAMPLE_FRAMES) {
      const b = packFrame(f)
      const out = unpackFrame(b)
      expect(out.direction).toBe(f.direction)
      expect(out.firing).toBe(f.firing)
      expect(out.guard).toBe(f.guard)
      expect(out.frenzy).toBe(f.frenzy)
    }
  })

  it('packFrames prefixes the schema version and unpackFrames validates it', () => {
    const packed = packFrames(SAMPLE_FRAMES)
    // v1 when no frames2: first byte is 0x01 (FRAME_SCHEMA_V1)
    expect(packed[0]).toBe(0x01)
    expect(packed.length).toBe(SAMPLE_FRAMES.length + 1)
    const out = unpackFrames(packed)
    expect(out).not.toBeNull()
    expect(out!.p1.length).toBe(SAMPLE_FRAMES.length)
    expect(out!.p2).toBeNull()
    for (let i = 0; i < SAMPLE_FRAMES.length; i++) {
      expect(out!.p1[i].direction).toBe(SAMPLE_FRAMES[i].direction)
      expect(out!.p1[i].firing).toBe(SAMPLE_FRAMES[i].firing)
    }
  })

  it('packFrames with frames2 produces v2 format', () => {
    const p2frames: InputFrame[] = [
      { direction: 'left', firing: true, guard: false, frenzy: false },
      { direction: null, firing: false, guard: false, frenzy: false },
    ]
    const packed = packFrames(SAMPLE_FRAMES, p2frames)
    expect(packed[0]).toBe(FRAME_SCHEMA_VERSION) // v2
    expect(packed[1] & 0x01).toBe(1) // hasP2 flag
    const out = unpackFrames(packed)
    expect(out).not.toBeNull()
    // v2 interleaved: tickCount = P1 length (5); P2 is shorter → padded with idle frames
    expect(out!.p1.length).toBe(SAMPLE_FRAMES.length)
    expect(out!.p2).not.toBeNull()
    expect(out!.p2!.length).toBe(SAMPLE_FRAMES.length)
    // First two P2 frames match the provided data
    expect(out!.p2![0].direction).toBe('left')
    expect(out!.p2![0].firing).toBe(true)
    expect(out!.p2![1].direction).toBe(null)
    // Remaining P2 frames are idle (defaulted)
    expect(out!.p2![2].firing).toBe(false)
  })

  it('unpackFrames returns null on schema-version mismatch (L3 guard)', () => {
    const packed = new Uint8Array([0x99, 0x00, 0x01])
    expect(unpackFrames(packed)).toBeNull()
  })
})

// ============================================================
// ReplayInput — deterministic playback feed
// ============================================================

describe('ReplayInput', () => {
  it('advances exactly one frame per advance() call', () => {
    const input = new ReplayInput(packFrames(SAMPLE_FRAMES))
    for (let i = 0; i < SAMPLE_FRAMES.length; i++) {
      const f = SAMPLE_FRAMES[i]
      expect(input.getMoveDirection()).toBe(f.direction)
      expect(input.isFiring()).toBe(f.firing)
      expect(input.wasItemPressed('guard')).toBe(f.guard)
      expect(input.wasItemPressed('frenzy')).toBe(f.frenzy)
      expect(input.progress).toBeCloseTo(i / SAMPLE_FRAMES.length, 5)
      input.advance()
    }
    // Past the end → idle frame, finished
    expect(input.isFinished).toBe(true)
    expect(input.getMoveDirection()).toBe(null)
    expect(input.isFiring()).toBe(false)
  })

  it('endFrame() is a no-op (cannot double-step the cursor)', () => {
    const input = new ReplayInput(packFrames(SAMPLE_FRAMES))
    const before = input.getMoveDirection()
    input.endFrame()
    input.endFrame()
    expect(input.getMoveDirection()).toBe(before)
    expect(input.progress).toBe(0)
  })

  it('rejects an unknown schema version → zero frames → immediate end', () => {
    const bad = new Uint8Array([0x02, 0x10]) // version 2, with a payload byte
    const input = new ReplayInput(bad)
    expect(input.isFinished).toBe(true)
    expect(input.getMoveDirection()).toBe(null)
  })

  it('handles an empty frame blob gracefully', () => {
    const input = new ReplayInput(new Uint8Array([FRAME_SCHEMA_VERSION]))
    expect(input.isFinished).toBe(true)
  })
})

// ============================================================
// ReplayManager — creation lifecycle & canPlay (L1, L3)
// ============================================================

describe('ReplayManager', () => {
  it('create() derives totalTicks + durationMs from tickCount, not wall-clock', () => {
    const mgr = new ReplayManager({ now: () => 1_000_000 })
    const world = new World()
    world.startGame('classic', world.themeKey, 0)
    const initial = JSON.parse(JSON.stringify({ stageIndex: 0, score: 0 }))
    // minimal snapshot stand-in (ReplayManager only stores it)
    const snapshot = initial as any
    const frames = packFrames(SAMPLE_FRAMES)
    const metadata = {
      stage: 0,
      stageName: 'STAGE 1',
      difficulty: 'classic',
      lives: 3,
      playerLevel: 1,
      score: 1000,
      killCount: 5,
      enemiesTotal: 20,
      playTimeMs: 12345, // wall-clock — must NOT leak into duration
    }
    const replay = mgr.create('clear', snapshot, frames, SAMPLE_FRAMES.length, metadata)
    expect(replay.totalTicks).toBe(SAMPLE_FRAMES.length)
    expect(replay.durationMs).toBe(Math.round(SAMPLE_FRAMES.length * (1000 / 60)))
    expect(replay.durationMs).not.toBe(12345)
    expect(replay.thumbnail).toBeNull()
    // schemaVersion mirrors the blob's leading byte, not the newest schema
    // this build knows: SAMPLE_FRAMES has no P2 stream → packFrames emits v1.
    expect(frames[0]).toBe(FRAME_SCHEMA_V1)
    expect(replay.schemaVersion).toBe(FRAME_SCHEMA_V1)
  })

  it('create() records v2 for a coop (dual-stream) recording', () => {
    const mgr = new ReplayManager({ now: () => 0 })
    const frames = packFrames(SAMPLE_FRAMES, SAMPLE_FRAMES)
    const replay = mgr.create('clear', {} as any, frames, SAMPLE_FRAMES.length, {
      stage: 0,
      stageName: '',
      difficulty: '',
      lives: 0,
      playerLevel: 0,
      score: 0,
      killCount: 0,
      enemiesTotal: 0,
      playTimeMs: 0,
    })
    expect(frames[0]).toBe(FRAME_SCHEMA_VERSION)
    expect(replay.schemaVersion).toBe(FRAME_SCHEMA_VERSION)
    expect(mgr.canPlay(replay)).toBe(true)
  })

  it('canPlay() rejects replays with a wrong schema version (L3)', () => {
    const mgr = new ReplayManager()
    const good = mgr.create('clear', {} as any, packFrames(SAMPLE_FRAMES), SAMPLE_FRAMES.length, {
      stage: 0,
      stageName: '',
      difficulty: '',
      lives: 0,
      playerLevel: 0,
      score: 0,
      killCount: 0,
      enemiesTotal: 0,
      playTimeMs: 0,
    })
    expect(mgr.canPlay(good)).toBe(true)
    const bad = { ...good, schemaVersion: 0x99, frames: new Uint8Array([0x99, 0x00]) }
    expect(mgr.canPlay(bad)).toBe(false)
    expect(mgr.canPlay({ ...good, frames: new Uint8Array(0) } as any)).toBe(false)
    // The BLOB is the gate, not the descriptive `schemaVersion` field: older
    // builds stamped 0x02 on every replay including v1 blobs, and those files
    // are perfectly playable. (DECISIONS #76)
    expect(mgr.canPlay({ ...good, schemaVersion: FRAME_SCHEMA_VERSION })).toBe(true)
  })

  it('enforces retention policy (circular overwrite, favorited exempt)', () => {
    const mgr = new ReplayManager({ now: () => 0 })
    const make = (fav = false) => {
      const r = mgr.create('clear', {} as any, packFrames(SAMPLE_FRAMES), SAMPLE_FRAMES.length, {
        stage: 0,
        stageName: '',
        difficulty: '',
        lives: 0,
        playerLevel: 0,
        score: 0,
        killCount: 0,
        enemiesTotal: 0,
        playTimeMs: 0,
      })
      if (fav) mgr.toggleFavorite(r.id)
      return r
    }
    // Policy limit comes from config (250 for clear since the demo-corpus
    // bump); create limit+5, keep the newest limit.
    const limit = REPLAY_RETENTION_POLICIES.clear.limit
    for (let i = 0; i < limit + 5; i++) make(false)
    expect(mgr.count('clear')).toBe(limit)
    // A favorited replay is never evicted even past the limit.
    const fav = make(true)
    for (let i = 0; i < 10; i++) make(false)
    expect(mgr.get(fav.id)).not.toBeNull()
    expect(mgr.favoriteCount()).toBe(1)
  })
})

// ============================================================
// End-to-end determinism — record a live run, replay it, compare (the
// core promise of the replay system)
// ============================================================

describe('Replay determinism (record → replay)', () => {
  it('reproduces the exact same World from the recorded input stream', () => {
    const TICKS = 600 // 10 seconds of play

    // ---- LIVE run: record ----
    const liveWorld = new World()
    liveWorld.startGame('classic', liveWorld.themeKey, 0)
    const scripted = new ScriptedInput(SAMPLE_FRAMES)
    const sim = new Simulation(liveWorld, scripted)
    const recorder = new InputRecorder()
    recorder.startNew(liveWorld)

    for (let t = 0; t < TICKS; t++) {
      sim.tick()
      recorder.recordFrame(scripted)
      scripted.advance()
    }
    const result = recorder.finalize()
    expect(result).not.toBeNull()
    expect(result!.tickCount).toBe(TICKS)

    // Build a real Replay via the manager (exercise create() + canPlay too).
    const mgr = new ReplayManager({ now: () => 1_000_000 })
    const metadata = {
      stage: liveWorld.stageIndex,
      stageName: 'STAGE 1',
      difficulty: 'classic',
      lives: liveWorld.lives,
      playerLevel: liveWorld.playerLevel,
      score: liveWorld.score,
      killCount: liveWorld.killCount,
      enemiesTotal: liveWorld.enemiesSpawned,
      playTimeMs: liveWorld.playTimeMs,
    }
    const replay = mgr.create(
      'clear',
      result!.snapshot,
      result!.frames,
      result!.tickCount,
      metadata,
    )
    expect(mgr.canPlay(replay)).toBe(true)

    // ---- REPLAY run via the real PlaybackController path ----
    const replayWorld = new World()
    // The constructor needs an input; PlaybackController.start() immediately
    // swaps it for the recorded ReplayInput, so a placeholder is fine.
    const sim2 = new Simulation(replayWorld, scripted)
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim2) // restoreWorld + swap input + state='playing'
    let guard = 0
    while (!pc.isEnded && guard < TICKS + 100) {
      pc.update(16.7) // ~1 tick per call at ×1 speed
      guard++
    }
    expect(pc.isEnded).toBe(true)

    // ---- Compare normalized world state (ignoring entity ids) ----
    const liveSnap = cloneForCompare(liveWorld)
    const replaySnap = cloneForCompare(replayWorld)
    expect(normalize(replaySnap)).toBe(normalize(liveSnap))
  })
})

/** Build a comparable snapshot directly from a live World via cloneWorld. */
function cloneForCompare(world: World): unknown {
  return cloneWorld(world)
}
