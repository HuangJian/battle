import { describe, it, expect } from 'bun:test'
import { World, genId } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { RNG } from '../src/utils/RNG'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CELL } from '../src/constants'
import type { Tank } from '../src/types'
import { clearArena, positionPlayer } from './helpers'

/**
 * M13 (DECISIONS §113, SHIPPED 2026-08-04): field-wide outnumbered positioning
 * retreat — unit tests. Original shipped defaults: outnumberedFieldRetreat=1,
 * outnumberedFieldEnemies=3, outnumberedFieldDistCells=15, pool-model ONLY.
 * §115 (M4 round-2) widened the pool defaults to 4/26 — this test uses the
 * explicit M13_PARAMS constant (3/15) below so it keeps testing M13's own
 * contract independent of the M4 retune (classic keeps 3/15 via
 * CLASSIC_MODEL_PARAMS).
 *
 * P4.2 (shipped earlier) only retreats when 3+ enemies CONVERGE within
 * outnumberedRadiusCells (9). M13 extends this to the FIELD-wide count: when
 * `outnumberedFieldRetreat` is ON, `selectTarget` returns the defense position
 * while live enemies (field-wide, Cluster C) >= outnumberedFieldEnemies AND
 * the player is beyond outnumberedFieldDistCells from the base. Targets the
 * dominant death mode (probe: 70% of hard/chaos deaths with the full 4-enemy
 * field alive, 39% at >20 cells). Skipped when the base is under threat, in
 * aggressive mode, or in the classic 'instant' combat model.
 *
 * Geometry: player at CENTER (12,12) for the far cases (distToBase = 12, but
 * the shipped gate is 15 — so the "far" cases use (10,10) → distToBase = 14?
 * No — the gate must fire, so use a clearly-far position). Enemies sit at the
 * four corners (>9 cells away) so P4.2 (nearby) NEVER fires — isolating M13.
 * distToBase(12,12) = 12 ≤ 15 would NOT fire the shipped gate, so the far
 * cases place the player at (12,4): distToBase = 20 > 15 ✓ and still >9 cells
 * from every corner enemy (nearest = (1,1): 14) so P4.2 stays inert.
 */

function setupWorld(difficulty: 'hard' | 'classic'): { world: World; input: GodAIInput } {
  const world = new World()
  world.rng = new RNG(42)
  // Explicit clone (NOT the DEFAULT singleton) — mutating input.params must
  // not leak into DEFAULT_GOD_AI_PARAMS (DECISIONS §98).
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS })
  const sim = new Simulation(world, new Input())
  world.startGame(difficulty, 'modern', 0)
  clearArena(world)
  input.hasBase = world.tileMap.hasBase()
  input._baseUnderThreatCache = false
  void sim
  return { world, input }
}

function makeEnemy(world: World, x: number, y: number): Tank {
  const p = world.player!
  // Clone the player as a template (has every Tank field), then override.
  return {
    ...p,
    id: genId(),
    x,
    y,
    kind: 'basic',
    alive: true,
    spawnTimer: 0,
    isPlayer: false,
    allegiance: 'enemy',
    bonus: false,
    level: 0,
  }
}

/** Corner enemies — every one >9 cells from the center (isolates P4.2). */
function cornerEnemies(world: World, count: number): Tank[] {
  const corners: Array<[number, number]> = [
    [1 * CELL, 1 * CELL],
    [24 * CELL, 1 * CELL],
    [1 * CELL, 24 * CELL],
    [24 * CELL, 24 * CELL],
  ]
  return corners.slice(0, count).map(([x, y]) => makeEnemy(world, x, y))
}

const M13_PARAMS = {
  outnumberedFieldRetreat: 1,
  outnumberedFieldEnemies: 3,
  outnumberedFieldDistCells: 15,
}

// Player at (12,4): distToBase = |12-12|+|4-24| = 20 > 15 (far, fires M13);
// >9 cells from every corner enemy (nearest (1,1): 14) — P4.2 inert.
const FAR = { col: 12, row: 4 }

describe('M13 field-wide outnumbered positioning', () => {
  it('OFF: outnumberedFieldRetreat=0 leaves target selection untouched', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, FAR.col, FAR.row)
    input._enemies = cornerEnemies(world, 3)
    // All M13 knobs set EXCEPT the master gate — must be inert.
    input.params.outnumberedFieldRetreat = 0
    input.params.outnumberedFieldEnemies = 3
    input.params.outnumberedFieldDistCells = 15
    const target = input.selectTarget(FAR)
    const defense = input.getDefaultDefensePosition()
    expect(target).not.toEqual(defense)
  })

  it('ON + 3 enemies (shipped threshold) + far → defense position', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, FAR.col, FAR.row)
    input._enemies = cornerEnemies(world, 3)
    Object.assign(input.params, M13_PARAMS)
    const target = input.selectTarget(FAR)
    const defense = input.getDefaultDefensePosition()
    expect(target).toEqual(defense)
  })

  it('ON but only 2 enemies (below threshold) → normal chase, not defense', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, FAR.col, FAR.row)
    input._enemies = cornerEnemies(world, 2)
    Object.assign(input.params, M13_PARAMS)
    const target = input.selectTarget(FAR)
    const defense = input.getDefaultDefensePosition()
    expect(target).not.toEqual(defense)
  })

  it('ON + 3 enemies but close to base (≤ dist gate) → normal chase, not defense', () => {
    const { world, input } = setupWorld('hard')
    positionPlayer(world, 12, 16) // distToBase = |0|+|16-24| = 8 ≤ 15 (close)
    input._enemies = cornerEnemies(world, 3)
    Object.assign(input.params, M13_PARAMS)
    const target = input.selectTarget({ col: 12, row: 16 })
    const defense = input.getDefaultDefensePosition()
    expect(target).not.toEqual(defense)
  })

  it('pool-only: classic (instant model) never retreats even with knobs ON', () => {
    const { world, input } = setupWorld('classic')
    positionPlayer(world, FAR.col, FAR.row)
    input._enemies = cornerEnemies(world, 3)
    Object.assign(input.params, M13_PARAMS)
    const target = input.selectTarget(FAR)
    const defense = input.getDefaultDefensePosition()
    expect(target).not.toEqual(defense)
  })
})
