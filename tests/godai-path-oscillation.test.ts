// §190: pixel-stuck fallback — when the player has been pixel-stuck for
// >= carveDigBlockTicks (1.5s) and no carve-dig is active, bypass A*
// pathfinding and use directMove instead. With replanInterval=1 (default
// on hard), A* recomputes every tick and target movement invalidates the
// replan cache — the first step oscillates between directions (left↔right),
// and the turn cooldown creates a back-and-forth with zero net progress.
// directMove picks a stable direction based on the target's relative
// position, breaking the oscillation cycle.
//
// Repro: headless S35 seed 10 (deterministic). Metric: the longest
// continuous period where the player's displacement from an anchor
// position stays within 1 cell (16px) — the idle-analysis threshold.
// Unfixed: ~1838 ticks (30.6s). Fixed: < 600 ticks (10s).
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { CELL } from '../src/constants'
import { RNG } from '../src/utils/RNG'
import { STAGES } from '../src/config/stages'

interface IdleResult {
  maxIdle: number
  combatExemptMax: number
  outcome: string
  endTick: number
}

/** Longest continuous period (in ticks) where the player's displacement
 * from an anchor stays within 1 cell (CELL=16px) in both axes, with no
 * kills or terrain destruction (non-combat idle). Mirrors the
 * idle-analysis.ts detection logic. */
function maxNonCombatIdle(stageIdx: number, seed: number, maxTicks: number): IdleResult {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard']
  world.playerLevel = world.difficulty.playerStartLevel ?? 0
  world.lives = world.difficulty.startLives ?? 3
  world.spectate = true
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  input.reset()

  let anchorX = 0
  let anchorY = 0
  let idleStart = -1
  let fireTicks = 0
  let killsAtStart = 0
  let revAtStart = 0
  let maxIdle = 0
  let combatExemptMax = 0
  let outcome = 'max_ticks'
  let endTick = maxTicks

  const flush = (tick: number) => {
    if (idleStart < 0) return
    const dur = tick - idleStart
    const kills = world.killCount - killsAtStart
    const terrain = world.tileMap.revision !== revAtStart
    const exempt = fireTicks > 0 && (kills > 0 || terrain)
    if (!exempt) {
      if (dur > maxIdle) maxIdle = dur
    } else {
      if (dur > combatExemptMax) combatExemptMax = dur
    }
  }

  for (let tick = 0; tick < maxTicks; tick++) {
    sim.tick()
    const p = world.player
    const alive = !!p && p.alive && p.spawnTimer <= 0
    const px = p ? Math.round(p.x + p.w / 2) : -1
    const py = p ? Math.round(p.y + p.h / 2) : -1
    const fire = input._fire

    input.endFrame()

    if (alive) {
      if (idleStart < 0) {
        idleStart = tick
        anchorX = px
        anchorY = py
        fireTicks = fire ? 1 : 0
        killsAtStart = world.killCount
        revAtStart = world.tileMap.revision
      } else if (Math.abs(px - anchorX) <= CELL && Math.abs(py - anchorY) <= CELL) {
        if (fire) fireTicks++
      } else {
        flush(tick)
        idleStart = tick
        anchorX = px
        anchorY = py
        fireTicks = fire ? 1 : 0
        killsAtStart = world.killCount
        revAtStart = world.tileMap.revision
      }
    } else {
      flush(tick)
      idleStart = -1
    }

    if (world.state === 'stageclear' || world.state === 'gameover') {
      flush(tick)
      outcome = world.state
      endTick = tick
      break
    }
  }

  if (outcome === 'max_ticks') {
    flush(maxTicks)
  }

  return { maxIdle, combatExemptMax, outcome, endTick }
}

describe('§190: pixel-stuck fallback', () => {
  it('S35 seed10: non-combat idle < 600 ticks (10s)', () => {
    // Before fix: maxIdle ≈ 1838 ticks (30.6s) — player oscillated at
    // (1,25) for the entire late game without firing.
    // After fix: directMove bypasses A* oscillation when pixel-stuck.
    const r = maxNonCombatIdle(34, 10, 36000)
    console.log(
      `outcome=${r.outcome} endTick=${r.endTick} maxIdle=${r.maxIdle} combatExemptMax=${r.combatExemptMax}`,
    )
    // 600 ticks = 10 seconds at 60 FPS
    expect(r.maxIdle).toBeLessThan(600)
  })
})
