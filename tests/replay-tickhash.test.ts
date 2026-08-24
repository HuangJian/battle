import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder, type RecorderResult } from '../src/replay/InputRecorder'
import { parseReplayFile, serializeReplayFile } from '../src/replay/file'
import { REPLAY_HASH_INTERVAL } from '../src/replay/config'
import { packFrames, unpackFrames } from '../src/replay/pack'
import { fnv1a } from '../src/replay/tickHash'
import { verifyReplayText, decideVerdict } from '../tools/replay/verify-replay'
import type { InputLike } from '../src/game/Input'
import type { InputFrame } from '../src/replay/types'
import type { Direction } from '../src/constants'

/**
 * Replay Tick-Hash Chain — plan/Replay-TickHash-Chain.md §5
 *
 * Acceptance tests T1–T7:
 *  - T1: a fresh recording carries one hash per REPLAY_HASH_INTERVAL ticks and
 *    a headless verify round-trips with hashVerified === true (phase pin).
 *  - T2a/T2b: a tampered frame stream diverges the world; the hash chain
 *    localizes the divergence to its 100-tick window.
 *  - T3①: legacy files without the chain verify by terminal state only.
 *  - T4: same seed ⇒ identical hash chains.
 *  - T5: startNew() resets the chain (no residue across sessions).
 *  - T7: decideVerdict decision table.
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
    /* no-op */
  }
  reset(): void {
    this.i = 0
  }
  /** Called once per sim tick during the LIVE recording run. */
  advance(): void {
    if (this.i < this.seq.length - 1) this.i++
  }
}

const TICKS = 500
const SEED = 12345
/** 'hard' uses the 'cooldown' fire model — a fire bit flip at t=250 always
 *  produces a bullet (a 'classic' run would gate it behind the bullet cap:
 *  the t=168 shot is still in flight at t=250, so the flip would be a no-op). */
const DIFF = 'hard'

/**
 * Scripted input with two known state-change segments (plan §5):
 *  - t=150–179: idle→fire transition (T2b deletes frame 149 → the fire bit
 *    shifts one tick early, diverging the world at t=149).
 *  - t=250–279: fire segment (T2a flips frame 250's fire bit fire→idle,
 *    diverging the world at t=250).
 */
function scriptedFrames(count: number): InputFrame[] {
  const frames: InputFrame[] = []
  for (let t = 0; t < count; t++) {
    frames.push({ direction: null, firing: false, guard: false, frenzy: false })
  }
  for (let t = 150; t < 180; t++) frames[t].firing = true
  for (let t = 250; t < 280; t++) frames[t].firing = true
  return frames
}

interface RecordedRun {
  text: string
  result: RecorderResult
  world: World
  aliveAt149: boolean
  aliveAt250: boolean
}

/** Record 500 ticks of scripted play into a serialized .replay text. */
function recordRun(): RecordedRun {
  const world = new World()
  world.seed = SEED
  world.rng.reseed(SEED)
  world.startGame(DIFF, world.themeKey, 0)
  const scripted = new ScriptedInput(scriptedFrames(TICKS))
  const sim = new Simulation(world, scripted)
  const recorder = new InputRecorder()
  recorder.startNew(world)

  let aliveAt149 = true
  let aliveAt250 = true
  for (let t = 0; t < TICKS; t++) {
    sim.tick()
    if (t === 149) aliveAt149 = world.player?.alive ?? false
    if (t === 250) aliveAt250 = world.player?.alive ?? false
    recorder.recordFrame(scripted)
    scripted.advance()
  }
  const result = recorder.finalize()
  if (!result) throw new Error('recording finalized empty')

  const text = serializeReplayFile({
    source: 'sim',
    seed: world.seed,
    sim: {
      seed: world.seed,
      difficulty: world.difficultyKey,
      stageIndex: world.stageIndex,
      stageName: 'STAGE 1',
      outcome: 'died',
      status: 'died',
      maxTicks: TICKS,
    },
    initialSnapshot: result.snapshot,
    frames: result.frames,
    totalTicks: result.tickCount,
    metadata: {
      stage: world.stageIndex,
      stageName: 'STAGE 1',
      difficulty: world.difficultyKey,
      lives: world.lives,
      playerLevel: world.playerLevel,
      score: world.score,
      killCount: world.killCount,
      enemiesTotal: world.enemiesSpawned,
      playTimeMs: world.playTimeMs,
    },
    tickHashes: result.tickHashes,
    hashInterval: REPLAY_HASH_INTERVAL,
  })
  return { text, result, world, aliveAt149, aliveAt250 }
}

/** Re-serialize a .replay text with a mutated / deleted frame in the P1 stream. */
function tamperFrame(
  text: string,
  index: number,
  mutate?: (f: InputFrame) => void,
  deleteFrame = false,
): string {
  const env = JSON.parse(text) as { replay: { framesBase64: string } }
  const un = unpackFrames(Buffer.from(env.replay.framesBase64, 'base64'))
  if (!un) throw new Error('unpack failed')
  if (deleteFrame) un.p1.splice(index, 1)
  else if (mutate) mutate(un.p1[index])
  env.replay.framesBase64 = Buffer.from(packFrames(un.p1)).toString('base64')
  return JSON.stringify(env)
}

// ============================================================
// T1 — fresh recording: one hash per interval, round-trip verifies
// ============================================================

describe('Replay tick-hash chain (T1–T7)', () => {
  it('T1: records one hash per REPLAY_HASH_INTERVAL ticks and round-trips identical', () => {
    const { text, result, world, aliveAt149, aliveAt250 } = recordRun()

    // Harness preconditions: the player must be alive at both fire segments
    // and the run must never go terminal — otherwise the tamper tests would
    // not exercise their intended divergences. (Deterministic — fixed seed.)
    expect(world.state).toBe('playing')
    expect(aliveAt149).toBe(true)
    expect(aliveAt250).toBe(true)

    expect(result.tickCount).toBe(TICKS)
    expect(result.tickHashes.length).toBe(Math.floor(TICKS / REPLAY_HASH_INTERVAL))
    for (const h of result.tickHashes) {
      expect(h).toMatch(/^[0-9a-f]{8}$/)
    }

    // The chain must also survive serialization → parse.
    const parsed = parseReplayFile(text)
    expect('error' in parsed).toBe(false)
    if (!('error' in parsed)) {
      expect(parsed.replay.tickHashes).toEqual(result.tickHashes)
      expect(parsed.replay.hashInterval).toBe(REPLAY_HASH_INTERVAL)
    }

    // Phase pin: headless verify compares the same post-tick states and
    // matches every checkpoint (T1 failure ⇒ phase drifted between
    // recorder and verifier — see tickHash.ts header).
    const r = verifyReplayText(text, 'test.replay')
    expect(r.hashVerified).toBe(true)
    expect(r.firstHashMismatch).toBeNull()
    expect(r.verdict).toBe('OK')
  })

  // ============================================================
  // T2 — tamper detection & window localization
  // ============================================================

  it('T2a: flipping frame 250 fire→idle diverges at t=250 → window [200,300)', () => {
    const { text } = recordRun()
    const tampered = tamperFrame(text, 250, (f) => {
      f.firing = false
    })
    const r = verifyReplayText(tampered, 'test.replay')
    expect(r.hashVerified).toBe(false)
    expect(r.firstHashMismatch).not.toBeNull()
    expect(r.firstHashMismatch!.checkpoint).toBe(300)
    expect(r.firstHashMismatch!.tickWindow).toEqual([200, 300])
    expect(r.verdict).toBe('DESYNC')
    expect(r.reason).toContain('hash mismatch at t300')
  })

  it('T2b: deleting frame 149 shifts the fire one tick early → window [100,200)', () => {
    const { text } = recordRun()
    const tampered = tamperFrame(text, 149, undefined, true)
    const r = verifyReplayText(tampered, 'test.replay')
    expect(r.hashVerified).toBe(false)
    expect(r.firstHashMismatch).not.toBeNull()
    expect(r.firstHashMismatch!.checkpoint).toBe(200)
    expect(r.firstHashMismatch!.tickWindow).toEqual([100, 200])
    expect(r.verdict).toBe('DESYNC')
  })

  // ============================================================
  // T3 — legacy files (no chain)
  // ============================================================

  it('T3①: legacy files without a hash chain verify by terminal state only', () => {
    const { text } = recordRun()
    const env = JSON.parse(text) as { replay: Record<string, unknown> }
    delete env.replay.tickHashes
    delete env.replay.hashInterval
    const r = verifyReplayText(JSON.stringify(env), 'test.replay')
    expect(r.hashVerified).toBeNull()
    expect(r.firstHashMismatch).toBeNull()
    expect(r.verdict).toBe('OK')
    expect(r.reason).toBe('replay reproduced the recorded outcome')
  })

  // ============================================================
  // T4 — determinism
  // ============================================================

  it('T4: same seed produces identical hash chains', () => {
    const a = recordRun().result
    const b = recordRun().result
    expect(a.tickHashes).toEqual(b.tickHashes)
  })

  // ============================================================
  // T5 — startNew() resets the chain
  // ============================================================

  it('T5: startNew() resets the hash chain — no residue across sessions', () => {
    const world = new World()
    world.seed = SEED
    world.rng.reseed(SEED)
    world.startGame(DIFF, world.themeKey, 0)
    const scripted = new ScriptedInput(scriptedFrames(TICKS))
    const sim = new Simulation(world, scripted)
    const recorder = new InputRecorder()

    recorder.startNew(world)
    for (let t = 0; t < 120; t++) {
      sim.tick()
      recorder.recordFrame(scripted)
      scripted.advance()
    }
    // Mid-session restart (recovery / stage change) — the new session's chain
    // starts fresh: no checkpoint at 200 from the previous session's frames.
    recorder.startNew(world)
    for (let t = 0; t < 100; t++) {
      sim.tick()
      recorder.recordFrame(scripted)
      scripted.advance()
    }
    const result = recorder.finalize()
    expect(result).not.toBeNull()
    expect(result!.tickCount).toBe(100)
    expect(result!.tickHashes.length).toBe(1)
  })

  // ============================================================
  // T7 — decideVerdict decision table (plan §1.4)
  // ============================================================

  it('T7: decideVerdict applies the hash × terminal decision table', () => {
    expect(decideVerdict(true, true)).toEqual({ verdict: 'OK', exitCode: 0 })
    expect(decideVerdict(true, false)).toEqual({ verdict: 'DESYNC', exitCode: 1 })
    expect(decideVerdict(false, true)).toEqual({ verdict: 'DESYNC', exitCode: 1 })
    expect(decideVerdict(false, false)).toEqual({ verdict: 'DESYNC', exitCode: 1 })
    expect(decideVerdict(null, true)).toEqual({ verdict: 'OK', exitCode: 0 })
    expect(decideVerdict(null, false)).toEqual({ verdict: 'DESYNC', exitCode: 1 })
  })

  it('fnv1a matches the stage-spec paramsHash family (fixed-vector check)', () => {
    expect(fnv1a('')).toBe('811c9dc5')
    expect(fnv1a('a')).toBe('e40c292c')
    expect(fnv1a('abc')).toBe('1a47e90b')
  })
})
