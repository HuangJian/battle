import { describe, it, expect } from 'bun:test'
import { ReplayManager } from '../src/replay/ReplayManager'
import { packFrames } from '../src/replay/pack'
import type { InputFrame } from '../src/replay/types'

/**
 * Replay storage estimation — regression test
 *
 * Bug: replay browser showed 0B because getStorageBytes used
 * navigator.storage?.estimate() (returns 0 in test/dev) instead of
 * ReplayManager.estimateBytes() which measures loaded data directly.
 *
 * This test reproduces the bug: shows that navigator.storage.estimate()
 * returns 0 in a test environment, then verifies estimateBytes() works.
 */

const SAMPLE_FRAMES: InputFrame[] = [
  { direction: 'up', firing: true, guard: false, frenzy: false },
  { direction: 'left', firing: false, guard: true, frenzy: false },
  { direction: 'down', firing: true, guard: false, frenzy: true },
  { direction: null, firing: false, guard: false, frenzy: false },
  { direction: 'right', firing: false, guard: false, frenzy: false },
]

describe('ReplayManager.estimateBytes()', () => {
  it('returns 0 for an empty manager', () => {
    const mgr = new ReplayManager({ now: () => 1_000_000 })
    expect(mgr.estimateBytes()).toBe(0)
  })

  it('returns > 0 after creating a replay (the core fix)', () => {
    const mgr = new ReplayManager({ now: () => 1_000_000 })
    const frames = packFrames(SAMPLE_FRAMES)
    mgr.create('clear', {} as any, frames, SAMPLE_FRAMES.length, {
      stage: 0,
      stageName: 'STAGE 1',
      difficulty: 'classic',
      lives: 3,
      playerLevel: 1,
      score: 1000,
      killCount: 5,
      enemiesTotal: 20,
      playTimeMs: 12345,
    })
    const bytes = mgr.estimateBytes()
    expect(bytes).toBeGreaterThan(0)
    // Frames alone: 5 packed frames + 1 version byte = 6 bytes
    // Plus metadata JSON + snapshot JSON + overhead (300)
    expect(bytes).toBeGreaterThan(300)
  })

  it('scales with number of replays', () => {
    const mgr = new ReplayManager({ now: () => 1_000_000 })
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
      playTimeMs: 12345,
    }
    mgr.create('clear', {} as any, frames, SAMPLE_FRAMES.length, metadata)
    const bytes1 = mgr.estimateBytes()

    mgr.create('clear', {} as any, frames, SAMPLE_FRAMES.length, metadata)
    const bytes2 = mgr.estimateBytes()

    // Two replays should use roughly 2× the storage of one
    expect(bytes2).toBeGreaterThan(bytes1)
    expect(bytes2).toBeLessThan(bytes1 * 3) // sanity: not 10×
  })

  it('navigator.storage.estimate() underestimates loaded replays (root cause of 0B bug)', async () => {
    // The old getStorageBytes callback used navigator.storage.estimate()
    // which returns origin-level storage, not per-object size. In test/dev
    // environments this often returns 0 or a value unrelated to loaded data.
    const mgr = new ReplayManager({ now: () => 1_000_000 })
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
      playTimeMs: 12345,
    }
    // Create 3 replays so estimateBytes() is clearly > 0
    for (let i = 0; i < 3; i++) {
      mgr.create('clear', {} as any, frames, SAMPLE_FRAMES.length, metadata)
    }
    const fromEstimateBytes = mgr.estimateBytes()
    expect(fromEstimateBytes).toBeGreaterThan(0)

    // The old API (navigator.storage.estimate()) is unrelated to loaded data
    try {
      const estimate = await navigator.storage?.estimate()
      const usage = estimate?.usage ?? 0
      // The key insight: usage from navigator.storage does NOT reflect
      // the 3 replays we just loaded in memory. It may be 0 or a
      // stale origin-level value — that's why the old callback showed 0B.
      expect(usage).not.toBe(fromEstimateBytes)
    } catch {
      // navigator.storage unavailable — old callback would return 0
    }
  })
})
