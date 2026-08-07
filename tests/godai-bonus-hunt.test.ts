import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS, GUARD_GOD_AI_PARAMS } from '../src/ai/god/params'
import { RNG } from '../src/utils/RNG'
import { CELL, GRID } from '../src/constants'
import type { Tank } from '../src/types'

/**
 * §172: bonus enemy hunt bias (bonus 敌人追猎权重) — unit tests.
 *
 * Drop-economy lever: only bonus enemies drop power-ups; 75% of losses
 * never see a star (item spawns 531 vs 948 loss vs win). The historical
 * bonus preference was a hardcoded −2 Manhattan bias — nearly irrelevant
 * at 10–30 cell hunt distances. bonusHuntBias parameterizes it (default
 * 2 = byte-identical); candidate arms 4 / 6.
 *
 * Isolation: chokepointMode=0 + baseGuardAnchorMode=0 (no §88/§137 holds),
 * enemiesRemaining=20 (canHunt false → NORMAL nearest-selection branch),
 * enemies parked in the upper rows far from the base (no threat).
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = new World()
  world.rng = new RNG(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty'
  }
  for (const r of [24, 25]) {
    for (const c of [12, 13]) world.tileMap.grid[r][c] = 'base'
  }
  void sim
  world.enemiesRemaining = 20 // keep canHunt false → normal hunt branch
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

function placeEnemy(world: World, col: number, row: number, bonus = false): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, 'down')
  enemy.alive = true
  enemy.spawnTimer = 0
  enemy.bonus = bonus
  world.tanks.push(enemy)
  return enemy
}

function refresh(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._selTargetValid = false
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

function cellOf(input: GodAIInput, t: Tank): { col: number; row: number } {
  const c = input.tankCell(t)
  return { col: c.col, row: c.row } // copy — tankCell shares a buffer
}

const ISOLATED = { chokepointMode: 0, baseGuardAnchorMode: 0 }

describe('§172: bonus enemy hunt bias (bonus 敌人追猎权重)', () => {
  it('bias 0: pure Manhattan — the nearer plain enemy wins over the bonus', () => {
    const { world, input } = setupWorld({ ...ISOLATED, bonusHuntBias: 0 })
    // Player at (8,24). N plain dist 18, B bonus dist 19 — no bias keeps N.
    placeEnemy(world, 8, 6) // N: |24-6| = 18
    const b = placeEnemy(world, 8, 5, true) // B: |24-5| = 19
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t).not.toBeNull()
    expect(t!.row).not.toBe(cellOf(input, b).row) // N at row 6, B at row 5
  })

  it('default 2: byte-identical to the historical −2 constant (1-cell gap flips)', () => {
    const { world, input } = setupWorld(ISOLATED) // default bonusHuntBias = 2
    placeEnemy(world, 8, 6) // N dist 18
    const b = placeEnemy(world, 8, 5, true) // B dist 19 → adjusted 17 < 18
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, b).col)
    expect(t!.row).toBe(cellOf(input, b).row)
  })

  it('default 2: a 3-cell-farther bonus enemy still loses (bias is not oversized)', () => {
    const { world, input } = setupWorld(ISOLATED)
    const n = placeEnemy(world, 8, 6) // N dist 18
    placeEnemy(world, 8, 3, true) // B dist 21 → adjusted 19 > 18
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, n).col)
    expect(t!.row).toBe(cellOf(input, n).row)
  })

  it('bias 6: a 5-cell-farther bonus enemy flips the pick (default would not)', () => {
    // Same geometry twice: default keeps N, bias 6 takes B.
    const run = (bias: number) => {
      const { world, input } = setupWorld({ ...ISOLATED, bonusHuntBias: bias })
      const n = placeEnemy(world, 8, 10) // N: |24-10| = 14
      const b = placeEnemy(world, 8, 5, true) // B: 19 → adj(bias): 19-bias
      refresh(input, world)
      const t = input.selectTarget(input.playerCell())!
      const bc = cellOf(input, b)
      const nc = cellOf(input, n)
      return { pickedBonus: t.col === bc.col && t.row === bc.row, nc }
    }
    const def = run(2) // adjusted 17 > 14 → N
    expect(def.pickedBonus).toBe(false)
    const hot = run(6) // adjusted 13 < 14 → B
    expect(hot.pickedBonus).toBe(true)
  })

  it('bias does not apply to plain enemies (only the bonus flag triggers it)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, bonusHuntBias: 6 })
    const n = placeEnemy(world, 8, 6) // plain dist 18, no bias
    placeEnemy(world, 8, 3) // plain dist 21, no bias → N wins regardless
    refresh(input, world)
    const t = input.selectTarget(input.playerCell())
    expect(t!.col).toBe(cellOf(input, n).col)
    expect(t!.row).toBe(cellOf(input, n).row)
  })

  it('defaults: shipped 2, classic restore 2, guard profile 2', () => {
    expect(DEFAULT_GOD_AI_PARAMS.bonusHuntBias).toBe(2)
    expect(CLASSIC_MODEL_PARAMS.bonusHuntBias).toBe(2)
    expect(GUARD_GOD_AI_PARAMS.bonusHuntBias).toBe(2)
  })
})
