import { describe, it, expect } from 'bun:test'
import { runLengths, median, frameStats, analyzeReplayText, pool } from '../../tools/eval/idle-rate'
import { World } from '../../src/game/World'
import { cloneWorld } from '../../src/snapshot/WorldSerializer'
import { packFrames } from '../../src/replay/pack'
import { serializeReplayFile } from '../../src/replay/file'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES } from '../../src/config/rules'
import type { InputFrame } from '../../src/replay/types'

const idle = (): InputFrame => ({ direction: null, firing: false, guard: false, frenzy: false })
const move = (d: 'up' | 'down' | 'left' | 'right'): InputFrame => ({
  direction: d,
  firing: false,
  guard: false,
  frenzy: false,
})

describe('runLengths', () => {
  it('returns maximal true-run lengths in order', () => {
    expect(runLengths([true, true, false, true, false, false, true, true, true])).toEqual([2, 1, 3])
  })
  it('handles all-true / all-false / empty', () => {
    expect(runLengths([true, true])).toEqual([2])
    expect(runLengths([false, false])).toEqual([])
    expect(runLengths([])).toEqual([])
  })
  it('counts a trailing run', () => {
    expect(runLengths([false, true, true])).toEqual([2])
  })
})

describe('median', () => {
  it('odd length = middle', () => expect(median([3, 1, 2])).toBe(2))
  it('even length = mean of middles', () => expect(median([1, 2, 3, 4])).toBe(2.5))
  it('empty = 0', () => expect(median([])).toBe(0))
  it('does not mutate input', () => {
    const xs = [3, 1, 2]
    median(xs)
    expect(xs).toEqual([3, 1, 2])
  })
})

describe('frameStats', () => {
  it('counts null-direction frames as stop intent', () => {
    const frames = [idle(), move('up'), idle(), idle(), move('left')]
    const s = frameStats(frames)
    expect(s.totalTicks).toBe(5)
    expect(s.nullTicks).toBe(3)
    expect(s.nullRunLens).toEqual([1, 2])
  })
})

/** Build a deterministic synthetic `.replay` from a real stage-0 world. */
function syntheticReplay(frames: InputFrame[]): string {
  const world = new World()
  world.rng.reseed(1)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? RULES['classic']
  world.loadStageData(STAGES[0], 0)
  const snap = cloneWorld(world)
  return serializeReplayFile({
    source: 'sim',
    seed: 1,
    sim: {
      seed: 1,
      difficulty: 'hard',
      stageIndex: 0,
      stageName: STAGES[0].name,
      outcome: 'timeout',
      status: 'timeout',
      maxTicks: frames.length,
    },
    initialSnapshot: snap,
    frames: packFrames(frames),
    totalTicks: frames.length,
    metadata: {
      stage: 0,
      stageName: STAGES[0].name,
      difficulty: 'hard',
      lives: 3,
      playerLevel: 0,
      score: 0,
      killCount: 0,
      enemiesTotal: 20,
      playTimeMs: frames.length * (1000 / 60),
    },
  })
}

describe('analyzeReplayText', () => {
  it('reports stopPickRate from recorded frames', () => {
    const frames = [
      ...Array(60).fill(0).map(idle),
      ...Array(60)
        .fill(0)
        .map(() => move('right')),
    ]
    const r = analyzeReplayText(syntheticReplay(frames), 'synthetic')
    expect(r.parseError).toBeUndefined()
    expect(r.stopPickRate).toBeCloseTo(0.5, 5)
    expect(r.stopPickPer1k).toBeCloseTo(500, 1)
  })

  it('is deterministic for the same replay', () => {
    const frames = [
      ...Array(30).fill(0).map(idle),
      ...Array(90)
        .fill(0)
        .map(() => move('up')),
    ]
    const text = syntheticReplay(frames)
    const a = analyzeReplayText(text, 'x')
    const b = analyzeReplayText(text, 'x')
    expect(JSON.stringify(a)).toBe(JSON.stringify(b))
  })

  it('surfaces parse errors instead of throwing', () => {
    const r = analyzeReplayText('{not json', 'bad.replay')
    expect(r.parseError).toBeTruthy()
  })
})

describe('pool', () => {
  it('aggregates stop-intent over all frames', () => {
    const framesA = [idle(), idle(), move('up'), move('up')]
    const framesB = [idle(), move('up')]
    const a = analyzeReplayText(syntheticReplay(framesA), 'a')
    const b = analyzeReplayText(syntheticReplay(framesB), 'b')
    const p = pool([a, b])
    expect(p.replays).toBe(2)
    expect(p.frames).toBe(6)
    // 3 null of 6 frames.
    expect(p.stopPickRate).toBeCloseTo(0.5, 5)
  })

  it('skips parse-error replays', () => {
    const bad = analyzeReplayText('nope', 'bad')
    const p = pool([bad])
    expect(p.replays).toBe(0)
    expect(p.frames).toBe(0)
  })
})
