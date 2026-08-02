import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import type { GodAIParams } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { GRID, TANK } from '../src/constants'
import type { PowerUp, StageData, Tank } from '../src/types'

// ================================================================
// §87 — Urgent power-up pickup priority (user request 2026-08-02).
//
// A CLOSE power-up with a SAFE PATH outranks base defense (回防) and
// enemy-kill (杀敌) targets, by category distance gate:
//   HIGH bomb/freeze/fence → pickupPriorityHighRange (target 8 cells)
//   MID  star/tank/shield  → pickupPriorityMidRange  (target 4 cells)
//   LOW  boat              → pickupPriorityLowRange  (target 2 cells)
// "Path safe" = no enemy between the player and the item
// (calculateRouteDanger <= pickupPriorityMaxDanger, target 0) AND the
// item is A*-reachable (steel/water pockets are skipped, never chased).
//
// Gated by pickupPriorityMode (default 0 = OFF, byte-identical to
// pre-§87). The A/B validation lives at the tool level (eval-suite
// --compare + per-seed tick-diff); these tests lock the targeting
// primitives + the shipped default.
// ================================================================

/** §87 A/B candidate parameter set (the initial tuning targets). */
function onParams(): GodAIParams {
  return {
    ...DEFAULT_GOD_AI_PARAMS,
    pickupPriorityMode: 1,
    pickupPriorityHighRange: 8,
    pickupPriorityMidRange: 4,
    pickupPriorityLowRange: 2,
    pickupPriorityMaxDanger: 0,
    pickupPriorityMinEnemyDist: 5,
    pickupPrioritySpawnRowMax: 3,
  }
}

/** Empty 26×26 arena with the classic base (eagle) at rows 24-25, cols 12-13. */
function makeEmptyStage(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    tiles.push(row)
  }
  return { id: 9999, name: 'Empty Arena', tiles, enemies: ['basic'] }
}

/** Empty arena plus a 4×4 steel ring (cells 1-4 × 1-4) enclosing (2,2)-(3,3). */
function makeBoxedArena(): StageData {
  const grid = makeEmptyStage().tiles.map((row) => row.split(''))
  for (let c = 1; c <= 4; c++) {
    grid[1][c] = 's'
    grid[4][c] = 's'
  }
  for (let r = 1; r <= 4; r++) {
    grid[r][1] = 's'
    grid[r][4] = 's'
  }
  return { id: 9998, name: 'Boxed Arena', tiles: grid.map((r) => r.join('')), enemies: ['basic'] }
}

/**
 * Player parked at the bottom-left corner (cell 0,24 — center 16,400), no
 * enemies, God AI constructed with the given params and reset. Callers add
 * power-ups / enemies as needed.
 */
function setup(
  params: GodAIParams = DEFAULT_GOD_AI_PARAMS,
  seed = 42,
  stage: StageData = makeEmptyStage(),
): { world: World; ai: GodAIInput } {
  const world = new World()
  world.rng = new RNG(seed)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(stage, 0)
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.x = 0
  p.y = GRID * 16 - TANK // 384 — cell (0,24)
  p.dir = 'up'
  const ai = new GodAIInput(world, params, new RNG(seed ^ 0x1234))
  ai.reset()
  return { world, ai }
}

function makePowerUp(id: number, type: PowerUp['type'], col: number, row: number): PowerUp {
  return {
    id,
    type,
    x: col * 16,
    y: row * 16,
    w: TANK,
    h: TANK,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 0,
  }
}

/** Enemy at the given grid cell (basic, fully spawned). */
function placeEnemy(world: World, col: number, row: number): Tank {
  const e = world.createTank('basic', col * 16, row * 16, 'down')
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

/** Player center: tank top-left (0,384), w/h 32 → center (16, 400). */
const PCX = 16
const P_CY = 384 + TANK / 2

// ---------------------------------------------------------------- params

describe('§87 params — shipped default (DECISIONS §87)', () => {
  it('defaults are the A/B-validated values (35×60: +9 wins, 0 significant regressions)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.pickupPriorityMode).toBe(1)
    expect(DEFAULT_GOD_AI_PARAMS.pickupPriorityHighRange).toBe(8)
    expect(DEFAULT_GOD_AI_PARAMS.pickupPriorityMidRange).toBe(4)
    expect(DEFAULT_GOD_AI_PARAMS.pickupPriorityLowRange).toBe(2)
    expect(DEFAULT_GOD_AI_PARAMS.pickupPriorityMaxDanger).toBe(0)
    expect(DEFAULT_GOD_AI_PARAMS.pickupPriorityMinEnemyDist).toBe(5)
    expect(DEFAULT_GOD_AI_PARAMS.pickupPrioritySpawnRowMax).toBe(3)
  })
})

// ---------------------------------------------------------------- targeting

describe('§87 findUrgentPowerUpTarget — distance gates', () => {
  it('OFF (pickupPriorityMode=0): the new function is inert even with a close bomb', () => {
    const { world, ai } = setup({ ...DEFAULT_GOD_AI_PARAMS, pickupPriorityMode: 0 })
    world.addPowerUp(makePowerUp(900, 'bomb', 4, 24)) // 4 cells — well within any range

    // The §87 branch never runs when pickupPriorityMode = 0 (the think()
    // gate short-circuits it). NOTE: the OLD S5 economy may still collect
    // this bomb (pre-§87 behavior) — that is exactly why OFF must stay ON
    // for the A/B baseline.
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toBeNull()
    expect(ai.params.pickupPriorityMode).toBe(0)
  })

  it('HIGH (bomb/freeze/fence): within 8 cells → targeted; beyond 8 → not', () => {
    const { world, ai } = setup(onParams())
    // 10 cells away: outside the HIGH gate.
    world.addPowerUp(makePowerUp(900, 'bomb', 10, 24))
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toBeNull()

    // 4 cells away: inside the HIGH gate → the item's own cell.
    world.powerUps = []
    world.addPowerUp(makePowerUp(901, 'bomb', 4, 24))
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 4, row: 24 })

    // freeze and fence share the HIGH gate.
    world.powerUps = []
    world.addPowerUp(makePowerUp(902, 'freeze', 6, 24))
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 6, row: 24 })
    world.powerUps = []
    world.addPowerUp(makePowerUp(903, 'fence', 7, 24))
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 7, row: 24 })
  })

  it('MID (star/tank/shield): within 4 cells → targeted; beyond 4 → not', () => {
    const { world, ai } = setup(onParams())
    world.addPowerUp(makePowerUp(900, 'star', 6, 24)) // 6 cells — beyond MID gate
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toBeNull()

    world.powerUps = []
    world.addPowerUp(makePowerUp(901, 'star', 3, 24)) // 3 cells — inside
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 3, row: 24 })

    world.powerUps = []
    world.addPowerUp(makePowerUp(902, 'shield', 4, 24)) // 4 cells — boundary, inside
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 4, row: 24 })

    // tank (extra life) shares the MID gate: 2 cells away.
    world.powerUps = []
    world.addPowerUp(makePowerUp(903, 'tank', 2, 24))
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 2, row: 24 })
  })

  it('LOW (boat): within 2 cells → targeted; beyond 2 → not', () => {
    const { world, ai } = setup(onParams())
    world.addPowerUp(makePowerUp(900, 'boat', 3, 24)) // 3 cells — beyond LOW gate
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toBeNull()

    world.powerUps = []
    world.addPowerUp(makePowerUp(901, 'boat', 2, 24)) // 2 cells — boundary, inside
    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 2, row: 24 })
  })
})

describe('§87 findUrgentPowerUpTarget — path safety', () => {
  it('an enemy between the player and the item blocks the pickup (danger > maxDanger)', () => {
    const { world, ai } = setup(onParams())
    // Move the player to an interior cell so cell math is unambiguous.
    const p = world.player!
    p.x = 8 * 16
    p.y = 8 * 16
    const pcx = p.x + TANK / 2
    const pcy = p.y + TANK / 2
    // Bomb at (12,12) — center cell (13,13), 8 cells from the player (inside
    // the HIGH gate). A basic enemy at (10,10) sits between player (9,9) and
    // item, strictly closer to the item → route danger 1 > maxDanger 0.
    placeEnemy(world, 10, 10)
    world.addPowerUp(makePowerUp(900, 'bomb', 12, 12))

    expect(ai.findUrgentPowerUpTarget(pcx, pcy)).toBeNull()
  })

  it('an off-path enemy does NOT block the pickup (beyond the nearby radius)', () => {
    const { world, ai } = setup(onParams())
    // Enemy 6 cells north of the player (cell (0,20) vs player cell (1,25)) —
    // farther than pickupPriorityMinEnemyDist (5): neither between player and
    // item nor nearby → still urgent.
    placeEnemy(world, 0, 20)
    world.addPowerUp(makePowerUp(900, 'bomb', 4, 24))

    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 4, row: 24 })
  })

  it('an enemy within pickupPriorityMinEnemyDist (5) cells of the player blocks the pickup', () => {
    // Per-seed tick-diff finding (Lattice s2): the player diverted 2 cells to
    // a star with an enemy 5 cells away, stalled, and died. The nearby-enemy
    // gate is the fix.
    const { world, ai } = setup(onParams())
    // Player cell (1,25); enemy at (1,20) is 5 cells away (within the gate).
    placeEnemy(world, 1, 20)
    world.addPowerUp(makePowerUp(900, 'bomb', 4, 24))

    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toBeNull()
  })

  it('an unreachable steel-boxed item is skipped (never chased)', () => {
    const { world, ai } = setup(onParams(), 42, makeBoxedArena())
    // Bomb inside the 4×4 steel box at cells 1-4 — the tank can never reach it.
    world.addPowerUp(makePowerUp(900, 'bomb', 2, 2))

    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toBeNull()
  })

  it('an item in the enemy spawn band (rows <= pickupPrioritySpawnRowMax) is skipped', () => {
    // Per-seed tick-diff finding (Lattice s2/s32): pickups in the spawn band
    // (classic enemies spawn at row 0) pulled the player into the spawn
    // corridor and it died. The band is excluded from urgent pickups.
    const { world, ai } = setup(onParams())
    // Reposition the player near the top so the band is in urgent range.
    const p = world.player!
    p.x = 2 * 16
    p.y = 5 * 16
    const pcx = p.x + TANK / 2
    const pcy = p.y + TANK / 2
    // Bomb at (2,2) — row 2 (band), 3 cells away: urgent-range but skipped.
    world.addPowerUp(makePowerUp(900, 'bomb', 2, 2))
    expect(ai.findUrgentPowerUpTarget(pcx, pcy)).toBeNull()

    // Same bomb one row lower (row 4, outside the band) → urgent again.
    world.powerUps = []
    world.addPowerUp(makePowerUp(901, 'bomb', 2, 4))
    expect(ai.findUrgentPowerUpTarget(pcx, pcy)).toEqual({ col: 2, row: 4 })
  })
})

describe('§87 findUrgentPowerUpTarget — selection order', () => {
  it('nearest in-range item wins (a 3-cell shield beats a 6-cell bomb)', () => {
    const { world, ai } = setup(onParams())
    world.addPowerUp(makePowerUp(900, 'bomb', 6, 24))
    world.addPowerUp(makePowerUp(901, 'shield', 3, 24))

    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 3, row: 24 })
  })

  it('ties break toward the higher-value item (bomb over star at equal distance)', () => {
    const { world, ai } = setup(onParams())
    world.addPowerUp(makePowerUp(900, 'bomb', 4, 24)) // dist 4, priority 0
    world.addPowerUp(makePowerUp(901, 'star', 0, 20)) // dist 4, priority 1

    expect(ai.findUrgentPowerUpTarget(PCX, P_CY)).toEqual({ col: 4, row: 24 })
  })
})

describe('§87 think() integration', () => {
  it('ON: a close safe bomb diverts the player into the power-up branch', () => {
    const { world, ai } = setup(onParams())
    world.addPowerUp(makePowerUp(900, 'bomb', 4, 24))

    const dir = ai.getMoveDirection()
    expect(dir).not.toBeNull()
    expect(ai.branchCounts.powerup).toBe(1)
  })

  it('ON: a far bomb does NOT divert (falls through to normal navigate)', () => {
    const { world, ai } = setup(onParams())
    world.addPowerUp(makePowerUp(900, 'bomb', 12, 24)) // 12 cells — beyond HIGH gate

    const dir = ai.getMoveDirection()
    // No enemy, no threat: navigate targets the nearest enemy — none — so the
    // fallback defense position (base row 24) keeps the player put.
    expect(ai.branchCounts.powerup).toBe(0)
    expect(dir).not.toBeNull() // still produces a (defense) move decision
  })

  it('FREEZE: the §87 branch is inert during the freeze window (aggressive owns pickups)', () => {
    // Deliberate design (DECISIONS §92): during freeze the aggressive branch
    // already grabs power-ups when no enemy is aligned, and an aligned frozen
    // enemy is a free kill the §87 branch must NOT interrupt. The gate is
    // `!self.aggressive` in think() — prove it with the same close-safe-bomb
    // geometry that diverts in normal mode (first test above: powerup == 1).
    const { world, ai } = setup(onParams())
    world.freezeTimer = 60000 // aggressive mode active
    world.addPowerUp(makePowerUp(900, 'bomb', 4, 24)) // 4 cells, safe path

    const dir = ai.getMoveDirection()
    expect(ai.branchCounts.powerup).toBe(0) // §87 branch never ran
    expect(dir).not.toBeNull() // aggressive still navigates (to the bomb / enemy)
  })
})
