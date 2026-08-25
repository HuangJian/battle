import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { scanAheadImpl, shouldFireInDirImpl } from '../src/ai/god/FireControl'
import { CELL } from '../src/constants'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { clearArena, placeEnemy, seedWorld } from './helpers'

/**
 * FireControl steel-blocking unit tests.
 *
 * Bug: God AI fires at an enemy even when steel blocks the bullet path.
 * Root cause: scanAheadImpl uses two independent offset scan lines. If
 * offset 0 hits steel but offset 1 finds an enemy, BOTH result.steel=true
 * AND result.enemy=true are set. shouldFireInDirImpl checked enemy BEFORE
 * steel, so it fired when result.enemy was true, ignoring the steel.
 *
 * Fix: moved steel/baseWall check BEFORE enemy check in shouldFireInDirImpl.
 * Also added scanAhead check in think()'s aggressive mode.
 */

function setupWorld(): { world: World; input: GodAIInput; sim: Simulation } {
  const world = seedWorld(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = RULES['classic'] ?? DEFAULT_RULES
  const input = new GodAIInput(world)
  const sim = new Simulation(world, new Input())
  world.startGame('classic', 'modern', 0)

  // Clear all terrain and place base cells.
  clearArena(world)

  return { world, input, sim }
}

describe('scanAheadImpl — steel and enemy on dual offset lines', () => {
  it('detects enemy when no obstacle is in the way', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'up')
    expect(result.enemy).toBe(true)
    expect(result.steel).toBe(false)
  })

  it('steel on BOTH offset lines blocks enemy detection', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    // Steel at BOTH cols 8 and 9 — blocks both offset scan lines.
    world.tileMap.grid[6][8] = 'steel'
    world.tileMap.grid[6][9] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'up')
    expect(result.steel).toBe(true)
    expect(result.enemy).toBe(false)
  })

  it('dual-offset: steel on one line + enemy on the other sets BOTH flags', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2 // 136
    const pcy = 10 * CELL + CELL / 2 // 168

    // Enemy at col 8, row 3 — spans x=128-160, so it overlaps BOTH
    // offset scan lines (sx=128 and sx=144).
    placeEnemy(world, 8, 3)
    // Steel at col 8, row 6 — only hits offset 0 (sx=128, col=8).
    // Offset 1 (sx=144, col=9) has no steel and finds the enemy.
    world.tileMap.grid[6][8] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'up')
    // Offset 0 hits steel → steel=true. Offset 1 finds enemy → enemy=true.
    // This is the root cause of the bug: both flags are true.
    expect(result.steel).toBe(true)
    expect(result.enemy).toBe(true)
  })
})

describe('shouldFireInDirImpl — steel must block fire even when enemy is visible', () => {
  it('fires when enemy is visible and no steel blocks', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(true)
  })

  it('does NOT fire when steel blocks both offset lines (level < 3)', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    // Steel on BOTH offset lines — enemy not detected.
    world.tileMap.grid[6][8] = 'steel'
    world.tileMap.grid[6][9] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(false)
  })

  it('does NOT fire through steel on dual-offset line (level < 3)', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    // Steel on offset 0 only — enemy visible on offset 1.
    // Before fix: fired because result.enemy was checked first.
    // After fix: does NOT fire because result.steel blocks it.
    world.tileMap.grid[6][8] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(false)
  })

  it('fires through steel when player level >= 3 (can pierce)', () => {
    const { world, input } = setupWorld()
    const pcx = 8 * CELL + CELL / 2
    const pcy = 10 * CELL + CELL / 2

    placeEnemy(world, 8, 3)
    world.tileMap.grid[6][8] = 'steel'

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    if (world.player) world.player.level = 3

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'up')
    expect(fired).toBe(true)
  })
})

/**
 * §74: Base-wall dual-offset suicide bug.
 *
 * Bug: In think()'s aggressive (T2b) and navigate break-through fire paths,
 * the condition `bs.enemy || (!bs.baseWall && !(bs.baseSteel && lvl >= 3))`
 * fires when bs.enemy is true — even if bs.baseWall is ALSO true. Because
 * scanAheadImpl uses two independent offset scan lines, one offset can find
 * a base protection brick (baseWall=true) while the other finds an enemy
 * (enemy=true). The `bs.enemy ||` short-circuits the OR, bypassing the base
 * protection and destroying the player's own base.
 *
 * In S33 Diamond (120 seeds), this caused 4 base_destroyed failures with
 * killer=player (seeds 26, 34, 78, 82).
 *
 * Fix: Remove `bs.enemy ||` from the condition so base protection always
 * takes priority, matching shouldFireInDirImpl's protection order:
 *   if (!bs.baseWall && !(bs.baseSteel && lvl >= 3)) this._fire = !onCooldown
 */
describe('scanAheadImpl — baseWall and enemy on dual offset lines', () => {
  // Player center at col 10, row 22. Scan DOWN.
  // Offset 0 (sx=pcx-8=160, col=10): hits base protection brick at (10,23).
  // Offset 1 (sx=pcx+8=176, col=11): finds enemy at (11,23).
  const pcx = 10 * CELL + CELL / 2 // 168
  const pcy = 22 * CELL + CELL / 2 // 360

  it('dual-offset: baseWall on one line + enemy on the other sets BOTH flags', () => {
    const { world, input } = setupWorld()
    // Base protection brick at (10, 23): dc=2, dr=1 → within ring.
    world.tileMap.grid[23][10] = 'brick'
    // Enemy at (11, 23): aligned with offset 1 (sx=176), not offset 0 (sx=160).
    placeEnemy(world, 11, 23)

    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx, pcy, 'down')
    expect(result.baseWall).toBe(true)
    expect(result.enemy).toBe(true)
  })

  it('base terrain (eagle) sets baseWall when scanned directly', () => {
    const { world, input } = setupWorld()
    // Player at col 12, row 22, scanning down. Offsets hit cols 11 and 12.
    // Col 11 row 24 is a brick (protection ring), col 12 row 24 is 'base'.
    // Both offsets hit base-related terrain → baseWall=true.
    const pcx2 = 12 * CELL + CELL / 2 // 200
    // Ensure base cells are present (setupWorld already places them).
    world.tileMap.grid[24][12] = 'base'
    world.tileMap.grid[24][13] = 'base'

    input.reset()
    input.hasBase = world.tileMap.hasBase()

    const result = scanAheadImpl(input, pcx2, pcy, 'down')
    expect(result.baseWall).toBe(true)
  })
})

describe('shouldFireInDirImpl — baseWall must block fire even when enemy is on dual-offset line', () => {
  // Player center at col 11, row 20. Scan DOWN.
  // Offset 0 (sx = pcx−8 = 176, col 11): hits the REAL ring brick at
  // (11,23) — an exact base-protection cell (dc=1/dr=1) → baseWall=true.
  // Offset 1 (sx = pcx+8 = 192, col 12): finds the enemy at (10,23),
  // whose 32px body (cols 10-11) overlaps both offset lines.
  const pcx = 11 * CELL + CELL / 2 // 184
  const pcy = 20 * CELL + CELL / 2 // 328

  it('does NOT fire when baseWall and enemy are both true (dual-offset)', () => {
    const { world, input } = setupWorld()
    // REAL ring brick at (11, 23) — offset 0 (col 11).
    world.tileMap.grid[23][11] = 'brick'
    // Enemy at (10, 23) — body spans cols 10-11, overlapping both offsets.
    placeEnemy(world, 10, 23)

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const fired = shouldFireInDirImpl(input, pcx, pcy, 'down')
    // shouldFireInDirImpl checks baseWall BEFORE enemy (§70), so this
    // correctly returns false. The bug was in think()'s break-through
    // paths which bypassed shouldFireInDirImpl.
    expect(fired).toBe(false)
  })

  it('break-through condition: base protection must take priority over enemy', () => {
    const { world, input } = setupWorld()
    world.tileMap.grid[23][11] = 'brick'
    placeEnemy(world, 10, 23)

    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }

    const bs = scanAheadImpl(input, pcx, pcy, 'down')
    // Root cause: both flags are true simultaneously.
    expect(bs.baseWall).toBe(true)
    expect(bs.enemy).toBe(true)

    // The break-through fire condition in think() (§74 fix):
    // Base protection takes ABSOLUTE priority — even when enemy is visible
    // on the other offset line, the AI must NOT fire.
    const lvl = 0
    const shouldFire = !bs.baseWall && !(bs.baseSteel && lvl >= 3)
    expect(shouldFire).toBe(false)

    // The OLD (buggy) condition would have fired:
    // bs.enemy || (!bs.baseWall && !(bs.baseSteel && lvl >= 3))
    // = true || (false && ...) = true → DESTROYS OWN BASE
  })

  it('D4 §140 exact-ring: a NON-ring brick near the base is NOT baseWall', () => {
    // Pool default (baseWallExactRing=1): an ordinary scene brick at (10,23)
    // (dc=2/dr=1 — inside the old loose radius rectangle but NOT one of the
    // 8 ring cells) must NOT be flagged baseWall. The bullet fired at it
    // stops at the brick (col 10) and never reaches the base (cols 12-13),
    // so the fire is harmless — this is the flip side of the Battlement
    // pocket fix (§140): the loose rectangle suppressed break-through fire
    // at real scene bricks (Battlement (9,21) vs the far (10,19) offset).
    const { world, input } = setupWorld()
    world.tileMap.grid[23][10] = 'brick'
    input.reset()
    input.hasBase = world.tileMap.hasBase()
    input.params = { ...DEFAULT_GOD_AI_PARAMS, aimError: 0 }
    const bs = scanAheadImpl(input, 10 * CELL + CELL / 2, 22 * CELL + CELL / 2, 'down')
    expect(bs.baseWall).toBe(false)
    expect(bs.wall).toBe(true)
    // Firing at it is allowed (harmless scene brick).
    const fired = shouldFireInDirImpl(input, 10 * CELL + CELL / 2, 22 * CELL + CELL / 2, 'down')
    expect(fired).toBe(true)
  })
})
