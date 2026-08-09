import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { GRID } from '../src/constants'
import { computePlayer2SpawnCol } from '../src/utils/helpers'
import { InputRecorder } from '../src/replay/InputRecorder'
import { unpackFrames } from '../src/replay/pack'
import type { InputFrame } from '../src/replay/types'

// ================================================================
// 督战双玩家 (dual-spectate) recording regression
//
// BUG: GameCore.requestSpectateToggle's in-place single<->dual switch
// created `godInput2` (via enableSpectateDual) but never re-wired
// `simulation.input2` — unlike every sibling branch (enableSpectate /
// disableSpectate / rearmSpectateGodInput all call wireLiveInputs()). So
// `simulation.input2` stayed null, player2 was driven by nobody, and the
// InputRecorder captured an all-idle P2 stream. The replay then desynced
// (a stamped `clear` file replays to `gameover` because P2 contributed live
// but is absent on playback).
//
// FIX: wireLiveInputs() now runs at the end of every requestSpectateToggle
// transition, so simulation.input2 === godInput2 in dual spectate and the
// recorder captures P2's real input.
//
// This test pins the OBSERVABLE contract the fix restores: when input2 is
// wired to the player2 God AI, the recorded P2 stream is non-idle; when it
// is left null (the pre-fix bug), the stream is all idle. Exact production
// classes (GodAIInput + InputRecorder + Simulation) — no DOM (AGENTS §8).
// ================================================================

const BASE_CELLS: Array<[number, number]> = [
  [12, 24],
  [13, 24],
  [12, 25],
  [13, 25],
]

function baseWallCells(): Array<[number, number]> {
  const out: Array<[number, number]> = []
  for (let r = 23; r <= 25; r++) {
    for (let c = 11; c <= 14; c++) {
      if (BASE_CELLS.some(([bc, br]) => bc === c && br === r)) continue
      out.push([c, r])
    }
  }
  return out
}

function baseArena(world: World): void {
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const [c, r] of BASE_CELLS) world.tileMap.grid[r][c] = 'base'
  for (const [c, r] of baseWallCells()) world.tileMap.grid[r][c] = 'brick'
  world.tileMap.rebuildBaseCache()
  world.state = 'playing'
}

/** Build a dual-spectate (督战双玩家) world + spawn player2, mirroring
 *  GameCore.enableSpectate(dual). `seed` decouples the God-AI RNGs from the
 *  world RNG exactly like the production paths do. */
function dualSpectateWorld(seed: number): { world: World; sim: Simulation } {
  const world = new World()
  world.rng = new RNG(seed)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)
  baseArena(world)

  world.spectate = true
  world.spectateDual = true
  const d = world.difficulty
  world.lives2 = d?.startLives ?? 3
  world.playerLevel2 = d?.playerStartLevel ?? 0
  const p1Col = world.playerSpawnPoint?.col ?? 8
  world.player2SpawnPoint = { col: computePlayer2SpawnCol(p1Col), row: 24 }
  world.spawnPlayer2()

  // Two enemies to chase (upper half) — gives the P2 God AI a target so it
  // reliably produces movement.
  for (const [x, y] of [
    [64, 64],
    [320, 96],
  ]) {
    const t = world.createTank('basic', x, y, 'down')
    t.spawnTimer = 0
    world.tanks.push(t)
  }
  world.enemiesSpawned = 2

  return { world, sim }
}

/**
 * Record `ticks` frames of a dual-spectate session. `wireP2` controls whether
 * simulation.input2 is wired to the player2 God AI (the fixed path) or left
 * null (the pre-fix bug). Returns the decoded P2 frame stream.
 */
function recordDualStream(
  world: World,
  sim: Simulation,
  seed: number,
  wireP2: boolean,
  ticks = 600,
): InputFrame[] {
  const god1 = new GodAIInput(world, undefined, new RNG(seed ^ 0x9e3779b9))
  god1.reset()
  const god2 = new GodAIInput(
    world,
    undefined,
    new RNG((seed ^ 0x9e3779b9 ^ 0xdeadbeef) >>> 0),
    (w) => w.player2,
  )
  god2.reset()

  sim.input = god1
  sim.input2 = wireP2 ? god2 : null

  const recorder = new InputRecorder()
  recorder.startNew(world)
  for (let t = 0; t < ticks; t++) {
    sim.tick()
    // Keep the stage alive so P2 keeps playing (mirrors test harness pattern).
    if (world.state !== 'playing') world.state = 'playing'
    recorder.recordFrame(sim.input, sim.input2)
    god1.endFrame()
    god2.endFrame()
  }
  const result = recorder.finalize()
  if (!result) throw new Error('recorder.finalize() returned null')
  // The recorder stores frames2 as a single v1 stream (v1 header), so the P2
  // frames come back in `frames.p1` (p2 is null for a non-interleaved blob).
  if (!result.frames2) throw new Error('expected a non-null P2 stream (spectateDualAtStart)')
  const frames = unpackFrames(result.frames2)
  if (!frames) throw new Error('expected a decoded P2 stream')
  return frames.p1
}

function countNonIdle(frames: InputFrame[]): number {
  return frames.filter(
    (f) => f.direction !== null || f.firing || f.guard || f.frenzy,
  ).length
}

describe('督战双玩家 recording — P2 input is captured (GameCore wiring)', () => {
  const SEED = 1786239570044

  it('captures an ACTIVE P2 stream when input2 is wired to the player2 God AI', () => {
    const { world, sim } = dualSpectateWorld(SEED)
    const p2 = recordDualStream(world, sim, SEED, true)
    const nonIdle = countNonIdle(p2)
    expect(nonIdle).toBeGreaterThan(0)
    // P2 hunts the two enemies, so it should move most ticks.
    const moved = p2.filter((f) => f.direction !== null).length
    expect(moved).toBeGreaterThan(p2.length * 0.5)
  })

  it('captures an ALL-IDLE P2 stream when input2 is left null (pre-fix bug)', () => {
    const { world, sim } = dualSpectateWorld(SEED)
    const p2 = recordDualStream(world, sim, SEED, false)
    expect(countNonIdle(p2)).toBe(0)
  })
})
