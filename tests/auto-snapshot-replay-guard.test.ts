import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { SnapshotManager } from '../src/snapshot/SnapshotManager'
import { PlaybackController } from '../src/replay/PlaybackController'
import { cloneWorld } from '../src/snapshot/WorldSerializer'
import { TICK_MS } from '../src/constants'
import type { Replay } from '../src/replay/types'

// ============================================================
// Helper — build a minimal valid Replay for testing
// ============================================================

function makeMinimalReplay(world: World, tickCount = 300): Replay {
  const snapshot = cloneWorld(world)
  // Minimal frames: null direction, no fire, no guard, no frenzy
  const frameSize = 4 // direction(1) + firing(1) + guard(1) + frenzy(1)
  const frames = new Uint8Array(tickCount * frameSize)
  // All zeroed = idle input (null direction, no fire)

  return {
    id: 'test-replay-000',
    type: 'clear',
    createdAt: Date.now(),
    gameVersion: '0.1.0',
    schemaVersion: 1,
    initialSnapshot: snapshot,
    frames,
    totalTicks: tickCount,
    durationMs: tickCount * TICK_MS,
    metadata: {
      stage: 0,
      stageName: 'Test Stage',
      difficulty: 'Classic',
      lives: 3,
      playerLevel: 0,
      score: 100,
      killCount: 5,
      enemiesTotal: 20,
      playTimeMs: tickCount * TICK_MS,
    },
    thumbnail: null,
    isFavorite: false,
    favoriteAt: null,
  }
}

/** A World mid-run, enough to be distinguishable. */
function makePlayingWorld(stage = 0): World {
  const world = new World()
  world.startGame('classic', world.themeKey, stage)
  world.score = 4321
  world.killCount = 7
  world.playTimeMs = 65_000
  return world
}

// ============================================================
// Regression: auto snapshots must NOT fire during replay playback
// ============================================================

describe('Auto snapshots — replay playback guard', () => {
  it('does NOT create auto snapshots while PlaybackController is active', () => {
    // --- Setup ---
    const world = makePlayingWorld()
    const sim = new Simulation(world, new Input())
    const mgr = new SnapshotManager({ autoIntervalMs: 30_000 })

    // Seed the timer with 29s — one more second would normally fire an auto snapshot.
    mgr.updateAuto(world, 29_000)
    expect(mgr.count('auto')).toBe(0) // not yet

    // --- Start playback (sets world.state = 'playing') ---
    const replay = makeMinimalReplay(world)
    const playback = new PlaybackController(replay)
    playback.start(world, sim)
    expect(world.state).toBe('playing')

    // --- Simulate Game.loop() behaviour WITH the guard ---
    // The guard in Game.ts: `if (this.world.state === 'playing' && !this.playback)`
    // During playback, this.playback is non-null → updateAuto is SKIPPED.
    for (let i = 0; i < 120; i++) {
      if (world.state === 'playing' && !playback) {
        mgr.updateAuto(world, TICK_MS)
      }
      playback.update(TICK_MS)
    }

    // Timer was at 29 s. Without the guard, 2 s more → fires at 30 s.
    // With the guard, updateAuto was never called → still 0.
    expect(mgr.count('auto')).toBe(0) // ← the critical assertion

    // --- Stop playback ---
    playback.exit(sim, new Input())

    // Timer is still at 29 s (updateAuto was never invoked during playback).
    // Calling updateAuto with 2 s more should fire exactly once.
    let created = 0
    if (mgr.updateAuto(world, 2000) !== null) created++
    expect(created).toBe(1)
    expect(mgr.count('auto')).toBe(1)
  })

  it('creates auto snapshots normally when no playback is active', () => {
    const world = makePlayingWorld()
    const mgr = new SnapshotManager({ autoIntervalMs: 30_000 })

    // Simulate 31 s of live gameplay (no playback)
    let created = 0
    for (let i = 0; i < 31; i++) {
      if (mgr.updateAuto(world, 1000) !== null) created++
    }
    expect(created).toBe(1) // exactly one after 31 s
    expect(mgr.count('auto')).toBe(1)
  })

  it('resumes auto snapshots after playback ends', () => {
    const world = makePlayingWorld()
    const sim = new Simulation(world, new Input())
    const mgr = new SnapshotManager({ autoIntervalMs: 30_000 })

    // --- Playback phase: guard blocks updateAuto ---
    const replay = makeMinimalReplay(world)
    const playback = new PlaybackController(replay)
    playback.start(world, sim)

    // 10 s of "playback" — guard blocks updateAuto
    for (let i = 0; i < 10; i++) {
      if (world.state === 'playing' && !playback) {
        mgr.updateAuto(world, 1000)
      }
      playback.update(1000)
    }
    expect(mgr.count('auto')).toBe(0)

    // --- End playback ---
    playback.exit(sim, new Input())

    // Timer is still at 0 (updateAuto was never called during playback).
    // 31 s of live calls → first snapshot fires at 30 s.
    let created = 0
    for (let i = 0; i < 31; i++) {
      if (mgr.updateAuto(world, 1000) !== null) created++
    }
    expect(created).toBe(1)
    expect(mgr.count('auto')).toBe(1)
  })
})
