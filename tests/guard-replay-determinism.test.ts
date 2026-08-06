import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { RNG } from '../src/utils/RNG'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { InputRecorder } from '../src/replay/InputRecorder'
import { ReplayInput } from '../src/replay/ReplayInput'
import { serializeReplayFile, parseReplayFile } from '../src/replay/file'
import { restoreWorld, cloneWorld } from '../src/snapshot/WorldSerializer'
import type { InputLike } from '../src/game/Input'

/**
 * 守卫回放逐字节一致 (DECISIONS §159/§160): a recorded session in which a
 * 天降神兵 guard yields (§159) to a moving player must replay tick-for-tick
 * identical. The guard's God AI brain is NOT part of the replay — it is
 * re-created from the restored World on playback (guardAIById is empty in a
 * fresh Simulation) and GUARD_GOD_AI_PARAMS zeros the imperfection gates, so
 * identical World state ⇒ identical guard decisions. This test proves it
 * end-to-end: the FULL World snapshot (canonicalized for the process-global
 * genId() counter) must match after every recorded tick.
 */

/** Mutable per-run tick clock so a scripted input can be frame-accurate. */
interface Clock {
  tick: number
}

/**
 * Deterministic scripted input: the player advances up the lane and holds
 * fire. When `guardFrames` is given, the guard key (F5 天降神兵) is held on
 * exactly those ticks — recorded into the input stream and replayed verbatim.
 */
function makeAdvanceInput(clock: Clock, guardFrames: ReadonlySet<number> | null): InputLike {
  return {
    getMoveDirection: () => 'up',
    isFiring: () => true,
    wasItemPressed: (kind) =>
      kind === 'guard' && guardFrames !== null && guardFrames.has(clock.tick),
    endFrame: () => {},
    reset: () => {},
  }
}

/** The F5 天降神兵 summon is held on these frames (spends guardStock 3→0). */
const GUARD_SUMMON_FRAMES: ReadonlySet<number> = new Set([30, 90, 150])

/**
 * Stage-0 arena with the yield geometry already in place: player (192,224)
 * facing up, guard (192,192) occupying the player's forward cell. Arena rows
 * 6-18 / cols 4-20 cleared; the player is shield-invulnerable, the guard is
 * tanky, the base is indestructible — the window can never hit a terminal
 * state. A flank enemy (left of the guard) and a lane enemy (up) give the
 * §160 sweep/lane suppression fire something to shoot at.
 */
function buildGuardYieldWorld(seed: number): World {
  const world = new World()
  world.seed = seed
  world.rng = new RNG(seed)
  world.startGame('classic', 'modern', 0)

  for (let r = 6; r <= 18; r++) {
    for (let c = 4; c <= 20; c++) world.tileMap.grid[r][c] = 'empty'
  }

  const p = world.player!
  p.x = 192
  p.y = 224
  p.dir = 'up'
  p.prevMoveDir = 'up'
  p.spawnTimer = 0
  p.shieldTimer = 999999 // invulnerable — no respawn, no terminal state

  const g = world.createTank('basic', 192, 192, 'up')
  g.allegiance = 'ally'
  g.isPlayer = false
  g.spawnTimer = 0
  g.hp = 1000
  g.lastFire = -99999
  g.guardExpireFrame = 999999

  const lane = world.createTank('basic', 192, 48, 'down')
  lane.spawnTimer = 0
  lane.hp = 1
  const flank = world.createTank('basic', 80, 192, 'right')
  flank.spawnTimer = 0
  flank.hp = 1

  world.tanks = [lane, flank]
  world.allies = [g]
  world.baseHp = 9999
  world.baseMaxHp = 9999
  return world
}

/**
 * Stage-0 arena for the F5 天降神兵 SUMMON path: NO pre-planted guard. The
 * player holds the guard key at frames 30/90/150, spending guardStock 3→0
 * through `activateGuard` — which rolls the guard kind from **world.rng** and
 * spawns accompanying balance enemies. The guards stay dormant (spawnTimer
 * 1000) inside this short window; the rng draws + entity creation are the
 * determinism surface under test.
 */
function buildGuardSummonWorld(seed: number): World {
  const world = new World()
  world.seed = seed
  world.rng = new RNG(seed)
  world.startGame('classic', 'modern', 0)

  for (let r = 6; r <= 18; r++) {
    for (let c = 4; c <= 20; c++) world.tileMap.grid[r][c] = 'empty'
  }

  const p = world.player!
  p.x = 192
  p.y = 224
  p.dir = 'up'
  p.prevMoveDir = 'up'
  p.spawnTimer = 0
  p.shieldTimer = 999999

  world.tanks = []
  world.allies = []
  world.guardStock = 3 // the summon path spends this
  world.baseHp = 9999
  world.baseMaxHp = 9999
  return world
}

/**
 * Canonical world hash. genId() (World.ts) is a process-global counter that
 * is NOT reset between Worlds, so entities created during playback get
 * different absolute ids than the original run — references stay internally
 * consistent. Remap every id by first occurrence so the hash compares WORLD
 * state, not process-global counter state.
 */
function hashWorld(world: World): string {
  const snap = JSON.parse(JSON.stringify(cloneWorld(world))) as Record<string, unknown>
  const remap = new Map<number, number>()
  let next = 1
  const canon = (id: number): number => {
    if (id <= 0) return id
    let k = remap.get(id)
    if (k === undefined) {
      k = next++
      remap.set(id, k)
    }
    return k
  }
  const rewrite = (node: unknown): void => {
    if (node === null || typeof node !== 'object') return
    if (Array.isArray(node)) {
      for (let i = 0; i < node.length; i++) rewrite(node[i])
      return
    }
    const obj = node as Record<string, unknown>
    for (const key of Object.keys(obj)) {
      const v = obj[key]
      if (
        typeof v === 'number' &&
        (key === 'id' || key === 'ownerId' || key === 'activeCommanderId')
      ) {
        obj[key] = canon(v)
      } else if (v !== null && typeof v === 'object') {
        rewrite(v)
      }
    }
  }
  rewrite(snap)
  return JSON.stringify(snap)
}

interface RecordedRun {
  world: World
  hashes: string[]
  lateralSteps: number
  maxAllies: number
  snapshot: ReturnType<typeof cloneWorld>
  frames: Uint8Array
  tickCount: number
}

/**
 * Record `ticks` of a scenario (world + frame-accurate input), capturing the
 * full canonical world state after every tick plus scenario metrics.
 * Kept in sync with tools/replay/verify-guard-replay.ts.
 */
function recordRun(world: World, input: InputLike, clock: Clock, ticks: number): RecordedRun {
  const sim = new Simulation(world, input)
  const recorder = new InputRecorder()
  recorder.startNew(world)

  const hashes: string[] = []
  let lateralSteps = 0
  let maxAllies = world.allies.length
  let prevGuardX = world.allies[0]?.x ?? -1
  for (let t = 0; t < ticks; t++) {
    clock.tick = t
    sim.tick()
    recorder.recordFrame(input)
    input.endFrame()
    world.consumeEvents()
    hashes.push(hashWorld(world))
    const g = world.allies[0]
    if (g && g.alive && g.x !== prevGuardX && world.player?.moving) lateralSteps++
    prevGuardX = g?.x ?? -1
    if (world.allies.length > maxAllies) maxAllies = world.allies.length
  }
  const rec = recorder.finalize()!
  return {
    world,
    hashes,
    lateralSteps,
    maxAllies,
    snapshot: rec.snapshot,
    frames: rec.frames,
    tickCount: rec.tickCount,
  }
}

/**
 * Replay a recorded run through the full file pipeline; returns per-tick
 * hashes plus the replay World (for direct guard-state comparison).
 * Kept in sync with tools/replay/verify-guard-replay.ts (same scenario +
 * same hashWorld canonicalization).
 */
function replayRun(
  run: RecordedRun,
  seed: number,
  ticks: number,
): { hashes: string[]; world: World } {
  const text = serializeReplayFile({
    source: 'sim',
    seed,
    sim: {
      seed,
      difficulty: 'classic',
      stageIndex: 0,
      stageName: STAGES[0].name,
      outcome: 'timeout',
      status: 'timeout',
      maxTicks: 36000,
    },
    initialSnapshot: run.snapshot,
    frames: run.frames,
    totalTicks: run.tickCount,
    metadata: {
      stage: 0,
      stageName: STAGES[0].name,
      difficulty: 'classic',
      lives: 3,
      playerLevel: 0,
      score: 0,
      killCount: 0,
      enemiesTotal: 20,
      playTimeMs: 0,
    },
  })
  const parsed = parseReplayFile(text)
  if ('error' in parsed) throw new Error(`Parse failed: ${parsed.error}`)
  const replay = parsed.replay

  const world = new World()
  world.rng.reseed(replay.seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  world.loadStageData(STAGES[replay.metadata.stage] ?? STAGES[0], 0)
  restoreWorld(world, replay.initialSnapshot)
  const input = new ReplayInput(replay.frames)
  const sim = new Simulation(world, input)
  sim.input = input
  sim.input2 = input.input2 ?? null
  world.state = 'playing'

  const hashes: string[] = []
  for (let t = 0; t < ticks; t++) {
    sim.tick()
    input.advance()
    world.consumeEvents()
    hashes.push(hashWorld(world))
  }
  return { hashes, world }
}

describe('天降神兵 — guard-yield replay is byte-identical (DECISIONS.md §159/§160)', () => {
  it('records a session with a real yield + suppression fire, and the replay reproduces every tick exactly', () => {
    const SEED = 12345
    const TICKS = 240
    const clock: Clock = { tick: 0 }
    const world = buildGuardYieldWorld(SEED)
    const run = recordRun(world, makeAdvanceInput(clock, null), clock, TICKS)

    // The scenario must actually exercise the yield: the guard steps
    // sideways (§159 perpendicular) while the player advances.
    expect(run.lateralSteps).toBeGreaterThan(0)
    // And it must exercise §160 suppression fire: the guard fires at the
    // flank/lane enemies while yielding (fireCount increments per shot).
    expect(run.world.allies[0]?.fireCount ?? 0).toBeGreaterThan(0)

    const replay = replayRun(run, SEED, TICKS)

    // Byte-identical: the FULL canonical world state matches after every tick.
    expect(replay.hashes).toEqual(run.hashes)

    // The guard brain is re-created from the restored World on playback (a
    // fresh Simulation has an empty guardAIById), yet it lands identically.
    const g = run.world.allies[0]
    const rg = replay.world.allies[0]
    expect(rg?.x).toBe(g?.x)
    expect(rg?.y).toBe(g?.y)
    expect(rg?.dir).toBe(g?.dir)
  })

  it('same seed produces identical recordings (guard decisions are pure World-state functions)', () => {
    const clockA: Clock = { tick: 0 }
    const clockB: Clock = { tick: 0 }
    const a = recordRun(buildGuardYieldWorld(4242), makeAdvanceInput(clockA, null), clockA, 200)
    const b = recordRun(buildGuardYieldWorld(4242), makeAdvanceInput(clockB, null), clockB, 200)
    expect(b.hashes).toEqual(a.hashes)
    expect(b.lateralSteps).toBe(a.lateralSteps)
  })
})

describe('天降神兵 — F5 召唤路径回放确定性 (guard bit in frames + activateGuard rng rolls)', () => {
  it('the recorded summon reproduces identically: kind rolls, balance enemies, stock spending', () => {
    const SEED = 67890
    const TICKS = 420
    const clock: Clock = { tick: 0 }
    const world = buildGuardSummonWorld(SEED)
    const run = recordRun(world, makeAdvanceInput(clock, GUARD_SUMMON_FRAMES), clock, TICKS)

    // The summon path must actually fire: 3 guard-bit frames → 3 guards
    // summoned (activateGuard, world.rng kind rolls + balance enemies),
    // and guardStock spent 3 → 0.
    expect(run.maxAllies).toBe(3)
    expect(run.world.guardStock).toBe(0)

    const replay = replayRun(run, SEED, TICKS)

    // Byte-identical across every tick — the rng draws inside activateGuard
    // (guard kind + accompanying-enemy kind) reproduce exactly.
    expect(replay.hashes).toEqual(run.hashes)
    expect(replay.world.allies.length).toBe(run.world.allies.length)
    expect(replay.world.guardStock).toBe(0)
  })
})
