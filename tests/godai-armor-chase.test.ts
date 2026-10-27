import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { shotsToKill } from '../src/ai/god/constants'
import { CELL } from '../src/constants'
import type { Tank } from '../src/types'
import { clearArena, seedWorld } from './helpers'

/**
 * A28 (2026-10-27) — focus-fire chase surcharge, and the unit trap behind it.
 *
 * `Tank.hp` is in DAMAGE POINTS (`maxHp = armor × HP_SCALE`; basic = 250,
 * armor = 500; one player shell ≈ 100). The original implementation compared
 * `hp > 1`, which is true for every enemy, so the surcharge was added to every
 * candidate and the ordering never changed — 700/700 byte-identical games.
 * It was also wired only into `huntModeTarget` (the `canHunt` endgame branch),
 * so even a correct unit left 350/350 games identical.
 *
 * These tests pin both halves of the fix:
 *  1. `shotsToKill` converts points → shots (the unit guard);
 *  2. the surcharge really re-orders `selectTarget`: a 5-shot armour body next
 *     door loses to a 1-shot remnant that is three cells farther.
 */

describe('A28 shotsToKill — hp is damage points, not shots', () => {
  it('converts the real archetype HP values', () => {
    expect(shotsToKill(250, 100)).toBe(3) // basic
    expect(shotsToKill(500, 100)).toBe(5) // armor
    expect(shotsToKill(100, 100)).toBe(1) // one-shot remnant
    expect(shotsToKill(101, 100)).toBe(2)
  })
  it('never returns 0 and tolerates a missing reference damage', () => {
    expect(shotsToKill(0, 100)).toBe(1)
    expect(shotsToKill(250, 0)).toBe(3) // falls back to 100/shot
  })
})

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  clearArena(world)
  void sim
  world.enemiesRemaining = 20 // canHunt false → normal nearest-enemy cascade
  // Park the player in the upper half: rows < 18 keep the base-threat box off,
  // and both enemies sit > 12 cells from the base (no race-threat either).
  world.player!.x = 12 * CELL
  world.player!.y = 10 * CELL
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

function placeEnemy(world: World, col: number, row: number, hp: number): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, 'down')
  enemy.alive = true
  enemy.spawnTimer = 0
  enemy.hp = hp
  world.tanks.push(enemy)
  return enemy
}

function refresh(input: GodAIInput, world: World): void {
  // Pin "base NOT under threat" — this test is about the nearest-enemy
  // cascade, and the race heuristic would otherwise divert to defence
  // (an enemy parked in the base column at 19 cells still trips it).
  input._baseUnderThreatCache = false
  input._selTargetValid = false
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

describe('A28 armorChasePenalty — chase ordering', () => {
  it('penalty 0 chases the nearest body even when it needs 5 shots', () => {
    const { world, input } = setupWorld({
      armorChasePenalty: 0,
      chokepointMode: 0,
      baseGuardAnchorMode: 0,
    })
    const armor = placeEnemy(world, 14, 10, 500) // dist 2, +4 → 6
    placeEnemy(world, 12, 5, 100) // dist 5, +0 → 5
    refresh(input, world)
    const picked = input.selectTarget(input.playerCell())
    expect(picked).not.toBeNull()
    expect(picked!.col).toBe(input.tankCell(armor).col)
    expect(picked!.row).toBe(input.tankCell(armor).row)
  })

  it('penalty 1 (shipped) prefers the 1-shot remnant three cells farther', () => {
    const { world, input } = setupWorld({ chokepointMode: 0, baseGuardAnchorMode: 0 })
    expect(input.params.armorChasePenalty).toBe(1)
    placeEnemy(world, 14, 10, 500) // dist 2 + (5−1)×1 = 6
    const remnant = placeEnemy(world, 12, 5, 100) // dist 5 + 0 = 5
    refresh(input, world)
    const picked = input.selectTarget(input.playerCell())
    expect(picked).not.toBeNull()
    expect(picked!.col).toBe(input.tankCell(remnant).col)
    expect(picked!.row).toBe(input.tankCell(remnant).row)
  })
})
