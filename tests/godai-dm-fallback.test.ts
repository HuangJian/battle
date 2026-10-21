import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS, type GodAIParams } from '../src/ai/GodAIInput'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { RNG } from '../src/utils/RNG'
import { CELL } from '../src/constants'
import type { Direction } from '../src/utils/direction'

/**
 * A7 — `directMove`'s any-passable fallback (DECISIONS §2026-10-21-godai-a7-dm-fallback).
 *
 * The fallback is the last resort when BOTH greedy axes (the ones pointing at
 * the target) are blocked by something unbreakable — steel, water, the base,
 * another tank or a base-protection brick. Because `prefA` and `prefB` are
 * non-passable by construction at that point, the legacy scan
 *
 *     for (d of ALL_DIRS) if (d !== opposite(prefA) && canMoveDir(d)) return d
 *
 * has exactly ONE candidate left: `opposite(prefB)`. The fallback therefore
 * has no choice to make — and, worse, it re-makes that 「no choice」 every
 * tick. The escape unblocks the preferred axis after ~10 ticks (7 px of
 * travel), the tank walks straight back, and the 200 ms turn cooldown (12
 * ticks) is longer than either move, so it paces one cell indefinitely.
 *
 * `directMoveFallbackHold` commits the chosen escape until the window expires,
 * which is what lets the tank actually leave the wall (a cell needs ~23 ticks
 * of travel). Measured on hard s35 seed1002: fallback ticks 543 → 203, escape
 * episodes ending with < 8 px of net displacement 13 → 2.
 */

interface FallbackStats {
  /** Ticks on which directMove reached the any-passable fallback. */
  fallback: number
  /** Episodes (= maximal runs of consecutive fallback ticks). */
  episodes: number
  /** Episodes that end with < 8 px of net displacement (a failed escape). */
  stuckEpisodes: number
  outcome: string
}

function fallbackStats(
  params: GodAIParams,
  stageIdx: number,
  seed: number,
  maxTicks: number,
): FallbackStats {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? 3
  const input = new GodAIInput(world, params, new RNG((seed ^ 0x9e3779b9) >>> 0))
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()

  // Sample (tick, x, y, fallback?) per directMove call — read-only wrapper.
  const samples: Array<{ tick: number; x: number; y: number; fb: boolean }> = []
  const orig = input.directMove.bind(input)
  input.directMove = (playerCell: { col: number; row: number }) => {
    const p = world.player!
    const target = input.selectTarget(playerCell)
    let fb = false
    if (target && !(target.col === playerCell.col && target.row === playerCell.row)) {
      const dx = target.col * CELL - p.x
      const dy = target.row * CELL - p.y
      let prefA: Direction | null = null
      let prefB: Direction | null = null
      if (Math.abs(dy) > CELL / 2) {
        prefA = dy > 0 ? 'down' : 'up'
        if (dx > 0) prefB = 'right'
        else if (dx < 0) prefB = 'left'
      } else {
        prefA = dx > 0 ? 'right' : dx < 0 ? 'left' : null
        if (dy > 0) prefB = 'down'
        else if (dy < 0) prefB = 'up'
      }
      const pa = prefA !== null && input.canMoveOrBreak(p, prefA)
      const pb = prefB !== null && input.canMoveOrBreak(p, prefB)
      fb = !pa && !pb
    }
    samples.push({ tick: world.frame, x: p.x, y: p.y, fb })
    return orig(playerCell)
  }

  for (let t = 0; t < maxTicks; t++) {
    if (world.state !== 'playing' || !world.player?.alive) break
    sim.tick()
    input.endFrame()
  }

  let fallback = 0
  let episodes = 0
  let stuckEpisodes = 0
  let i = 0
  while (i < samples.length) {
    if (!samples[i].fb) {
      i++
      continue
    }
    let j = i
    while (
      j + 1 < samples.length &&
      samples[j + 1].fb &&
      samples[j + 1].tick === samples[j].tick + 1
    ) {
      j++
    }
    episodes++
    fallback += j - i + 1
    const end = Math.min(j + 20, samples.length - 1)
    const disp = Math.abs(samples[end].x - samples[i].x) + Math.abs(samples[end].y - samples[i].y)
    if (disp < 8) stuckEpisodes++
    i = j + 1
  }
  return { fallback, episodes, stuckEpisodes, outcome: world.state }
}

// A8（2026-10-21）把集合点解析到可站的 (12,22) 之后，fallback 的触发次数整体下降
// （s35 seed1002：543 → 149），本用例的「相对下降」比例被稀释。这里固定在 A8 之前
// 的口径，测的是 A7 承诺机制本身，而不是当前关卡分布的混合效果。
const ERA = { ...DEFAULT_GOD_AI_PARAMS, defenseCellResolve: 0 }
const OFF = { ...ERA }
const ON = { ...ERA, directMoveFallbackHold: 40 }

describe('A7 — directMove any-passable fallback commitment', () => {
  it('ships OFF by default (byte-identical gate)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.directMoveFallbackHold).toBe(0)
    // The ranked/reverse-allowing variant measured net-negative and stays OFF.
    expect(DEFAULT_GOD_AI_PARAMS.directMoveFallbackRank).toBe(0)
  })

  it('reset() clears the escape commitment state', () => {
    const world = new World()
    world.rng.reseed(1)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    const input = new GodAIInput(world, ON, new RNG(1))
    world.loadStageData(STAGES[0], 0)
    input.reset()
    input._dmEscDir = 'left'
    input._dmEscUntil = 12345
    input.reset()
    expect(input._dmEscDir as Direction | null).toBe(null)
    expect(input._dmEscUntil).toBe(0)
  })

  it('commits the escape so the tank leaves the wall instead of pacing', () => {
    const base = fallbackStats(OFF, 34, 1002, 12000)
    const fix = fallbackStats(ON, 34, 1002, 12000)
    // Fallback ticks at least 40% lower …
    expect(fix.fallback).toBeLessThan(base.fallback * 0.6)
    // … and the failed-escape (pacing) episodes clearly reduced.
    expect(fix.stuckEpisodes).toBeLessThan(base.stuckEpisodes * 0.5)
    expect(base.stuckEpisodes).toBeGreaterThanOrEqual(6)
  })

  it('is inert when the hold is 0 (same run, same outcome)', () => {
    const a = fallbackStats(OFF, 12, 1000, 9000)
    const b = fallbackStats({ ...ERA, directMoveFallbackRank: 0 }, 12, 1000, 9000)
    expect(b.fallback).toBe(a.fallback)
    expect(b.stuckEpisodes).toBe(a.stuckEpisodes)
    expect(b.outcome).toBe(a.outcome)
  })
})
