import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { InputRecorder } from '../src/replay/InputRecorder'
import { PlaybackController } from '../src/replay/PlaybackController'
import { ReplayManager } from '../src/replay/ReplayManager'
import { packFrame, unpackFrame, packFrames, unpackFrames } from '../src/replay/pack'
import type { Direction } from '../src/constants'
import type { InputLike } from '../src/game/Input'
import type { InputFrame, ReplayMetadata } from '../src/replay/types'

/**
 * 时光宝盒 (manual rewind) in replays (2p-review P1-1): the sim consumes the
 * rewind edge (`wasItemPressed('rewind')` → decrements the world-global
 * rewind stock + sets rewindPending), but the recorder never captured it —
 * bit7 of the frame byte sat reserved, `InputFrame` had no rewind field, and
 * `ReplayInput`'s kind union dropped 'rewind'. Any rewind during a recording
 * made playback diverge (stock / rewindPending / recovery flow all off).
 *
 * Fix: bit7 = rewind; recorder captures both streams; ReplayInput re-enacts.
 * Backward compatible: old recordings have bit7 clear → rewind=false.
 */

function idle(): InputFrame {
  return { direction: null, firing: false, guard: false, frenzy: false, rewind: false }
}

/** Scripted input over an InputFrame sequence (rewind-aware). */
class ScriptedInput implements InputLike {
  private i = 0
  constructor(private seq: InputFrame[]) {}
  private cur(): InputFrame {
    return this.seq[Math.min(this.i, this.seq.length - 1)]
  }
  getMoveDirection(): Direction | null {
    return this.cur().direction
  }
  isFiring(): boolean {
    return this.cur().firing
  }
  wasItemPressed(kind: 'guard' | 'frenzy' | 'rewind'): boolean {
    if (kind === 'guard') return this.cur().guard
    if (kind === 'frenzy') return this.cur().frenzy
    return this.cur().rewind
  }
  endFrame(): void {}
  reset(): void {
    this.i = 0
  }
  advance(): void {
    if (this.i < this.seq.length - 1) this.i++
  }
}

function replayMetadata(world: World): ReplayMetadata {
  return {
    stage: world.stageIndex,
    stageName: '',
    difficulty: 'classic',
    lives: world.lives,
    playerLevel: world.playerLevel,
    score: world.score,
    killCount: world.killCount,
    enemiesTotal: world.enemiesSpawned,
    playTimeMs: world.playTimeMs,
  }
}

describe('rewind frame bit (P1-1) — pack round-trip', () => {
  it('pack/unpack round-trips the rewind bit (bit7)', () => {
    const frames: InputFrame[] = [
      idle(),
      { ...idle(), rewind: true },
      { ...idle(), direction: 'up', rewind: true },
    ]
    for (const f of frames) {
      expect(unpackFrame(packFrame(f))).toEqual(f)
    }
    // Old-format compatibility: bit7 was reserved — a clear bit decodes false.
    expect(unpackFrame(0b0000_0000).rewind).toBe(false)
    expect(unpackFrame(0b1000_0000).rewind).toBe(true)
  })

  it('dual-stream packing keeps both streams’ rewind edges', () => {
    const p1 = [{ ...idle(), rewind: true }]
    const p2 = [{ ...idle(), rewind: true }]
    const packed = packFrames(p1, p2)
    const un = unpackFrames(packed)
    expect(un).not.toBeNull()
    expect(un!.p1[0].rewind).toBe(true)
    expect(un!.p2![0].rewind).toBe(true)
  })
})

describe('rewind edges in playback (P1-1) — stock consumption matches live', () => {
  it('a single rewind edge is recorded, then re-enacted by PlaybackController', () => {
    const TICKS = 120
    const EDGE = 40
    const frames: InputFrame[] = Array.from({ length: TICKS }, (_, i) =>
      i === EDGE ? { ...idle(), rewind: true } : idle(),
    )

    // ---- LIVE run: record ----
    const liveWorld = new World()
    liveWorld.startGame('classic', liveWorld.themeKey, 0)
    if (liveWorld.player) {
      liveWorld.player.spawnTimer = 0
      liveWorld.player.shieldTimer = 0
    }
    liveWorld.rewindStock = 2
    const liveIn = new ScriptedInput(frames)
    const sim = new Simulation(liveWorld, liveIn)
    const recorder = new InputRecorder()
    recorder.startNew(liveWorld)
    for (let t = 0; t < TICKS; t++) {
      sim.tick()
      recorder.recordFrame(liveIn)
      liveIn.advance()
    }
    const result = recorder.finalize()
    expect(result).not.toBeNull()
    // The edge was consumed by the sim at tick EDGE.
    expect(liveWorld.rewindStock).toBe(1)
    expect(liveWorld.rewindPending).toBe(true)

    // ---- REPLAY run via PlaybackController ----
    const mgr = new ReplayManager({ now: () => 0 })
    const replay = mgr.create(
      'clear',
      result!.snapshot,
      result!.frames,
      result!.tickCount,
      replayMetadata(liveWorld),
    )
    const replayWorld = new World()
    const sim2 = new Simulation(replayWorld, new ScriptedInput([idle()]))
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim2)
    let guard = 0
    while (!pc.isEnded && guard < TICKS + 10) {
      pc.update(16.7)
      guard++
    }
    expect(replayWorld.rewindStock).toBe(liveWorld.rewindStock) // 1
    expect(replayWorld.rewindPending).toBe(liveWorld.rewindPending) // true
  })

  it('2p: rewind edges in BOTH streams are recorded and re-enacted (world-global stock)', () => {
    const TICKS = 80
    const p1Frames: InputFrame[] = Array.from({ length: TICKS }, (_, i) =>
      i === 20 ? { ...idle(), rewind: true } : idle(),
    )
    const p2Frames: InputFrame[] = Array.from({ length: TICKS }, (_, i) =>
      i === 50 ? { ...idle(), rewind: true } : idle(),
    )

    // ---- LIVE 2p run ----
    const liveWorld = new World()
    liveWorld.startGame('classic', liveWorld.themeKey, 0)
    if (liveWorld.player) {
      liveWorld.player.spawnTimer = 0
      liveWorld.player.shieldTimer = 0
    }
    liveWorld.twoPlayer = true
    liveWorld.enablePlayer2()
    if (liveWorld.player2) {
      liveWorld.player2.spawnTimer = 0
      liveWorld.player2.shieldTimer = 0
    }
    liveWorld.rewindStock = 3
    const in1 = new ScriptedInput(p1Frames)
    const in2 = new ScriptedInput(p2Frames)
    const sim = new Simulation(liveWorld, in1)
    sim.input2 = in2
    const recorder = new InputRecorder()
    recorder.startNew(liveWorld)
    for (let t = 0; t < TICKS; t++) {
      sim.tick()
      recorder.recordFrame(in1, in2)
      in1.advance()
      in2.advance()
    }
    const result = recorder.finalize()
    expect(result).not.toBeNull()
    // The packed dual stream carries BOTH players' edges.
    const un = unpackFrames(result!.frames)
    expect(un).not.toBeNull()
    expect(un!.p1[20].rewind).toBe(true)
    expect(un!.p2![50].rewind).toBe(true)
    expect(liveWorld.rewindStock).toBe(1) // 3 - P1@20 - P2@50

    // ---- REPLAY ----
    const mgr = new ReplayManager({ now: () => 0 })
    const replay = mgr.create(
      'clear',
      result!.snapshot,
      result!.frames,
      result!.tickCount,
      replayMetadata(liveWorld),
    )
    const replayWorld = new World()
    const sim2 = new Simulation(replayWorld, new ScriptedInput([idle()]))
    const pc = new PlaybackController(replay)
    pc.start(replayWorld, sim2)
    let guard = 0
    while (!pc.isEnded && guard < TICKS + 10) {
      pc.update(16.7)
      guard++
    }
    expect(replayWorld.rewindStock).toBe(liveWorld.rewindStock) // 1
    expect(replayWorld.rewindPending).toBe(liveWorld.rewindPending)
  })

  it('recordings WITHOUT a rewind edge decode rewind=false (old-format compat)', () => {
    const frames: InputFrame[] = Array.from({ length: 10 }, () => idle())
    const packed = packFrames(frames)
    const un = unpackFrames(packed)!
    for (const f of un.p1) expect(f.rewind).toBe(false)
  })
})
