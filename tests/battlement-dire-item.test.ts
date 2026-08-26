import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { findDireItemTargetImpl } from '../src/ai/god/StrategyPlanner'
import { GRID } from '../src/constants'
import type { StageData, Tank, PowerUpType } from '../src/types'

// ================================================================
// E1 / 道具经济 (plan/Battlement-Hard-Exploration 反证判据): 危急道具拾取.
//
// When the base is in a DIRE state — enemies swarming within
// direItemApproachCells (6) with liveEnemies >= direItemMinEnemies (3), OR
// the ring damaged at/below direItemRingLow (4) — a nearby bomb/freeze/fence/
// emp within direItemRangeCells (10) is worth a divert even with enemies
// nearby. The §87 nearby-enemy + route-danger gates (which block under
// 4-enemy pressure) are bypassed; reachability + spawn-band gates apply.
//
// Gated by direItemMode (default 0 = OFF, byte-identical to pre-E1).
// ================================================================

/** Empty 26×26 arena with the classic base + the 8-brick protection ring. */
function ringArena(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    if (r === 23) row = row.slice(0, 11) + 'bbbb' + row.slice(15)
    if (r === 24 || r === 25) row = row.slice(0, 11) + 'b' + row.slice(12)
    if (r === 24 || r === 25) row = row.slice(0, 14) + 'b' + row.slice(15)
    tiles.push(row)
  }
  return { id: 9996, name: 'Ring Arena', tiles, enemies: ['basic'] }
}

function setup(): { world: World; ai: GodAIInput } {
  const world = seedWorld(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(ringArena(), 0)
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.x = 6 * 16
  p.y = 10 * 16
  const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
  ai.reset()
  return { world, ai }
}

function placeEnemy(world: World, col: number, row: number, kind: Tank['kind'] = 'basic'): Tank {
  const e = world.createTank(kind, col * 16, row * 16, 'left')
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

function placeItem(world: World, type: PowerUpType, col: number, row: number): void {
  world.addPowerUp({
    id: 9000 + col * 100 + row,
    type,
    x: col * 16,
    y: row * 16,
    w: 32,
    h: 32,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 0,
  })
}

describe('E1 — dire-item pickup (plan 反证判据)', () => {
  it('direItemMode defaults to 0 (OFF, byte-identical)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.direItemMode).toBe(0)
  })

  it('trigger A (swarm): bomb within range is returned when 3+ enemies converge', () => {
    const { world, ai } = setup()
    // 3 enemies; one (14,23) is within 6 cells of the base (12,24).
    placeEnemy(world, 16, 24)
    placeEnemy(world, 17, 25)
    placeEnemy(world, 14, 23)
    // Bomb 3 cells from the player (6,10) → (9,10).
    placeItem(world, 'bomb', 9, 10)
    const p = world.player!
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    expect(findDireItemTargetImpl(ai, pcx, pcy)).toBe(null) // mode 0
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    const t = findDireItemTargetImpl(ai, pcx, pcy)
    expect(t).not.toBe(null)
    expect(t!.col).toBe(9)
    expect(t!.row).toBe(10)
  })

  it('trigger A does NOT fire without the swarm (fewer enemies or none approaching)', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    placeEnemy(world, 16, 24) // 1 enemy only — below direItemMinEnemies (3)
    placeItem(world, 'bomb', 9, 10)
    const p = world.player!
    expect(findDireItemTargetImpl(ai, p.x + p.w / 2, p.y + p.h / 2)).toBe(null)

    // 3 enemies but none within 6 cells of the base (all at the top).
    const { world: w2, ai: ai2 } = setup()
    ai2.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    placeEnemy(w2, 3, 2)
    placeEnemy(w2, 10, 2)
    placeEnemy(w2, 20, 3)
    placeItem(w2, 'bomb', 9, 10)
    const p2 = w2.player!
    expect(findDireItemTargetImpl(ai2, p2.x + p2.w / 2, p2.y + p2.h / 2)).toBe(null)
  })

  it('trigger B (补环): fence is returned when the ring is damaged, even without enemies', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    // Destroy 5 of the 8 ring bricks → ringIntact 3 <= direItemRingLow (4).
    world.tileMap.destroy(11, 23)
    world.tileMap.destroy(12, 23)
    world.tileMap.destroy(13, 23)
    world.tileMap.destroy(14, 23)
    world.tileMap.destroy(11, 24)
    placeItem(world, 'fence', 9, 10)
    const p = world.player!
    const t = findDireItemTargetImpl(ai, p.x + p.w / 2, p.y + p.h / 2)
    expect(t).not.toBe(null)
    expect(t!.col).toBe(9)
  })

  it('trigger B does NOT fire with an intact ring (no swarm either)', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    placeItem(world, 'fence', 9, 10)
    const p = world.player!
    expect(findDireItemTargetImpl(ai, p.x + p.w / 2, p.y + p.h / 2)).toBe(null)
  })

  it('bypasses the §87 nearby-enemy gate (enemy within 3 cells does not block)', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    // Swarm trigger.
    placeEnemy(world, 16, 24)
    placeEnemy(world, 17, 25)
    placeEnemy(world, 14, 23)
    // A NON-dire enemy right next to the player — the §87 nearby-enemy gate
    // (pickupPriorityMinEnemyDist 5) would return null; E1 bypasses it.
    placeEnemy(world, 5, 11)
    placeItem(world, 'freeze', 9, 10)
    const p = world.player!
    const t = findDireItemTargetImpl(ai, p.x + p.w / 2, p.y + p.h / 2)
    expect(t).not.toBe(null)
  })

  it('out-of-range item (beyond direItemRangeCells) is skipped', () => {
    const { world, ai } = setup()
    ai.params = { ...DEFAULT_GOD_AI_PARAMS, direItemMode: 1 }
    placeEnemy(world, 16, 24)
    placeEnemy(world, 17, 25)
    placeEnemy(world, 14, 23)
    // 20 cells from the player (6,10) → beyond range 10.
    placeItem(world, 'bomb', 6, 25)
    const p = world.player!
    expect(findDireItemTargetImpl(ai, p.x + p.w / 2, p.y + p.h / 2)).toBe(null)
  })
})
