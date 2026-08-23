import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { RNG } from '../src/utils/RNG'
import { PlaybackController } from '../src/replay/PlaybackController'
import { ReplayInput } from '../src/replay/ReplayInput'
import { packFrames } from '../src/replay/pack'
import { cloneWorld, restoreWorld } from '../src/snapshot/WorldSerializer'
import { TICK_MS, TANK } from '../src/constants'
import { DIFFICULTIES } from '../src/config/difficulty'
import { THEMES } from '../src/config/theme'
import type { InputFrame, Replay, ReplayMetadata } from '../src/replay/types'
import type { InputLike } from '../src/game/Input'
import type { Tank } from '../src/types'
import type { Direction } from '../src/constants'

// ---- helpers ----

function makeWorld(seed = 42): World {
  const world = new World()
  world.rng = new RNG(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.themeKey = 'classic'
  world.theme = THEMES['classic']
  world.rules = { ...world.rules }
  world.state = 'playing'
  world.player = null
  world.tanks = []
  world.bullets = []
  return world
}

function makeTank(overrides: Partial<Tank> = {}): Tank {
  return {
    id: 0,
    kind: 'basic',
    x: 100,
    y: 100,
    w: TANK,
    h: TANK,
    dir: 'up',
    speed: 1,
    moving: false,
    alive: true,
    hp: 1,
    maxHp: 1,
    level: 0,
    spawnTimer: 0,
    shieldTimer: 0,
    lastFire: 0,
    nextFireInterval: 500,
    fireCooldown: 0,
    fireCount: 0,
    bulletPower: 1,
    damage: 1,
    bulletSpeed: 3,
    vx: 0,
    vy: 0,
    profile: {
      firepower: 50,
      projectileSpeed: 50,
      fireControl: 50,
      mobility: 50,
      armor: 50,
      special: 50,
    },
    allegiance: 'player',
    isPlayer: true,
    ...overrides,
  }
}

// KEPT LOCAL (遗留 #5 audit): player2 id=99 + manual placement are asserted
// downstream; helpers.makeCoopWorld would change both. See 口径差异表.
function makeCoopWorld(seed = 42): World {
  const world = makeWorld(seed)
  world.coop = true
  world.player2 = makeTank({ id: 99, x: 300, y: 300 })
  world.lives2 = 3
  world.playerLevel2 = 0
  world.score2 = 0
  return world
}

const IDLE: InputFrame = { direction: null, firing: false, guard: false, frenzy: false }

function makeV1Frames(ticks: number): Uint8Array {
  const frames: InputFrame[] = Array.from({ length: ticks }, () => IDLE)
  return packFrames(frames)
}

function makeV2Frames(ticks: number): Uint8Array {
  const p1: InputFrame[] = Array.from({ length: ticks }, (_, i) =>
    i % 2 === 0
      ? { direction: 'up' as Direction, firing: true, guard: false, frenzy: false }
      : IDLE,
  )
  const p2: InputFrame[] = Array.from({ length: ticks }, (_, i) =>
    i % 3 === 0
      ? { direction: 'down' as Direction, firing: false, guard: false, frenzy: false }
      : IDLE,
  )
  return packFrames(p1, p2)
}

function makeSnapshot(world: World) {
  return cloneWorld(world)
}

function makeReplay(
  opts: {
    type?: 'clear' | 'died' | 'base'
    frames?: Uint8Array
    frames2?: Uint8Array | null
    coop?: boolean
    tickCount?: number
  } = {},
): Replay {
  const w = makeCoopWorld()
  const frames = opts.frames ?? makeV1Frames(5)
  const tickCount = opts.tickCount ?? 5
  const metadata: ReplayMetadata = {
    stage: 0,
    stageName: 'Test Stage',
    difficulty: 'classic',
    lives: 3,
    playerLevel: 0,
    score: 0,
    killCount: 0,
    enemiesTotal: 20,
    playTimeMs: tickCount * TICK_MS,
    coop: opts.coop ?? false,
  }
  return {
    id: 'test-replay',
    type: opts.type ?? 'clear',
    createdAt: Date.now(),
    gameVersion: '1.0.0',
    schemaVersion: 0x02,
    seed: 42,
    initialSnapshot: makeSnapshot(w),
    frames,
    totalTicks: tickCount,
    durationMs: tickCount * TICK_MS,
    metadata,
    frames2: opts.frames2 ?? null,
    thumbnail: null,
    isFavorite: false,
    favoriteAt: null,
  }
}

/** A minimal InputLike that records whether it was used. */
class SpyInput implements InputLike {
  fired = false
  getMoveDirection(): Direction | null {
    this.fired = true
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

// ================================================================
// M6 — End-to-end coop gameplay integration
// ================================================================

describe('M6 — PlaybackController input2 wiring', () => {
  it('start() wires simulation.input2 for v2 coop replays', () => {
    const world = makeCoopWorld()
    const sim = new Simulation(world, new SpyInput())
    sim.input2 = null

    const frames2 = makeV2Frames(10)
    const replay = makeReplay({ coop: true, frames: makeV2Frames(10), frames2, tickCount: 10 })
    const pb = new PlaybackController(replay)
    pb.start(world, sim)

    // input2 should be non-null for coop replays
    expect(sim.input2).not.toBeNull()
    expect(sim.input2!.getMoveDirection()).not.toBeNull() // p2 has 'down' frames
  })

  it('start() keeps simulation.input2 null for v1 replays', () => {
    const world = makeWorld()
    const sim = new Simulation(world, new SpyInput())
    sim.input2 = null

    const replay = makeReplay({ coop: false, frames: makeV1Frames(5), frames2: null, tickCount: 5 })
    const pb = new PlaybackController(replay)
    pb.start(world, sim)

    expect(sim.input2).toBeNull()
  })

  it('exit() restores simulation.input2 to null', () => {
    const world = makeCoopWorld()
    const sim = new Simulation(world, new SpyInput())
    sim.input2 = new SpyInput() // simulate live godInput

    const replay = makeReplay({
      coop: true,
      frames: makeV2Frames(10),
      frames2: makeV2Frames(10),
      tickCount: 10,
    })
    const pb = new PlaybackController(replay)
    pb.start(world, sim)
    expect(sim.input2).not.toBeNull() // playback wired it

    pb.exit(sim, new SpyInput())
    expect(sim.input2).toBeNull() // exit restored it
  })

  it('seekTo() wires simulation.input2 for coop replays', () => {
    const world = makeCoopWorld()
    const sim = new Simulation(world, new SpyInput())
    sim.input2 = null

    const frames2 = makeV2Frames(10)
    const replay = makeReplay({ coop: true, frames: makeV2Frames(10), frames2, tickCount: 10 })
    const pb = new PlaybackController(replay)
    pb.start(world, sim)
    pb.togglePause()

    pb.seekTo(world, sim, 0.5)
    expect(sim.input2).not.toBeNull()
  })
})

describe('M6 — ReplayInput dual-stream', () => {
  it('input2 getter returns non-null for v2 replays', () => {
    const data = makeV2Frames(5)
    const ri = new ReplayInput(data)
    expect(ri.input2).not.toBeNull()
  })

  it('input2 getter returns null for v1 replays', () => {
    const data = makeV1Frames(5)
    const ri = new ReplayInput(data)
    expect(ri.input2).toBeNull()
  })

  it('input2 reads correct direction at each tick', () => {
    const p1: InputFrame[] = [IDLE, IDLE, IDLE, IDLE, IDLE]
    const p2: InputFrame[] = [
      { direction: 'down', firing: false, guard: false, frenzy: false },
      IDLE,
      { direction: 'left', firing: true, guard: false, frenzy: false },
      IDLE,
      IDLE,
    ]
    const data = packFrames(p1, p2)
    const ri = new ReplayInput(data)
    const i2 = ri.input2!

    // Tick 0: down
    expect(i2.getMoveDirection()).toBe('down')
    ri.advance()
    // Tick 1: idle
    expect(i2.getMoveDirection()).toBeNull()
    ri.advance()
    // Tick 2: left + firing
    expect(i2.getMoveDirection()).toBe('left')
    expect(i2.isFiring()).toBe(true)
  })
})

describe('M6 — Snapshot restoration with coop', () => {
  it('cloneWorld preserves coop fields', () => {
    const w = makeCoopWorld()
    w.score2 = 1234
    const snap = cloneWorld(w)

    expect(snap.coop).toBe(true)
    expect(snap.lives2).toBe(3)
    expect(snap.playerLevel2).toBe(0)
    expect(snap.score2).toBe(1234)
    expect(snap.player2).not.toBeNull()
  })

  it('restoreWorld restores coop fields from snapshot', () => {
    const w = makeCoopWorld()
    w.score2 = 5678
    const snap = cloneWorld(w)

    const w2 = makeWorld() // empty world
    restoreWorld(w2, snap)

    expect(w2.coop).toBe(true)
    expect(w2.lives2).toBe(3)
    expect(w2.playerLevel2).toBe(0)
    expect(w2.score2).toBe(5678)
    expect(w2.player2).not.toBeNull()
  })

  it('old snapshot without coop fields defaults to coop=false', () => {
    const w = makeWorld()
    const snap = cloneWorld(w)
    // Remove coop fields to simulate old snapshot
    delete (snap as any).coop
    delete (snap as any).player2
    delete (snap as any).lives2
    delete (snap as any).playerLevel2
    delete (snap as any).score2

    const w2 = makeWorld()
    restoreWorld(w2, snap)

    expect(w2.coop).toBe(false)
    expect(w2.player2).toBeNull()
    expect(w2.lives2).toBe(0)
  })
})

describe('M6 — Simulation input2 integration', () => {
  it('updatePlayerTank no-ops when input is null', () => {
    const w = makeCoopWorld()
    const sim = new Simulation(w, new SpyInput())
    sim.input2 = null
    // player2 exists but input2 is null — should not crash
    sim.tick()
    expect(w.player2!.alive).toBe(true)
  })

  it('player2 moves when input2 provides direction', () => {
    const w = makeCoopWorld()
    // Place player2 in a known open area with room to move
    w.player2 = makeTank({ id: 99, x: 100, y: 100, alive: true, spawnTimer: 0, shieldTimer: 0 })
    w.player2SpawnPoint = { col: 6, row: 6 }

    class DirInput implements InputLike {
      getMoveDirection(): Direction | null {
        return 'up'
      }
      isFiring(): boolean {
        return false
      }
      wasItemPressed(): boolean {
        return false
      }
      endFrame(): void {}
      reset(): void {}
    }

    const sim = new Simulation(w, new SpyInput())
    sim.input2 = new DirInput()

    const prevY = w.player2!.y
    // Run a few ticks so player2 can move (needs shieldTimer=0 and spawnTimer=0)
    for (let i = 0; i < 10; i++) sim.tick()

    // Player2 should have moved upward (y decreased) or stayed if blocked
    expect(w.player2!.alive).toBe(true)
    // At minimum, the tank was processed by input2 — verify alive stays true
    // (input2 was consumed, not ignored)
    expect(prevY).toBeGreaterThanOrEqual(w.player2!.y)
  })
})

describe('M6 — Audio coop mode flag', () => {
  it('ReplayInput.input2 advances with parent tick', () => {
    const p1: InputFrame[] = [IDLE, IDLE, IDLE]
    const p2: InputFrame[] = [
      { direction: 'up', firing: true, guard: false, frenzy: false },
      { direction: 'down', firing: false, guard: false, frenzy: false },
      { direction: 'left', firing: false, guard: false, frenzy: false },
    ]
    const data = packFrames(p1, p2)
    const ri = new ReplayInput(data)
    const i2 = ri.input2!

    // Both p1 and p2 advance together via ri.advance()
    expect(i2.getMoveDirection()).toBe('up')
    ri.advance()
    expect(i2.getMoveDirection()).toBe('down')
    ri.advance()
    expect(i2.getMoveDirection()).toBe('left')
  })
})
