import { seedWorld } from './helpers'
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { STAGES } from '../src/config/stages'
import { GRID } from '../src/constants'
import { canBreachRingFrom, canShootBaseFrom } from '../src/ai/god/SmartThreatModel'
import type { StageData, Tank } from '../src/types'

// ================================================================
// D2 / 拆环威胁 (ring-breach threat, plan/Battlement-Hard-Exploration §D2).
//
// The base's only defense is the 8-brick ring. An enemy aligned with an
// intact ring brick, with no other brick/steel in between, is BREACHING —
// its next bullet destroys the ring and opens the base lane. The static
// §59 predicate (canShootBaseFrom) stays false until the ring falls, so
// the breacher is invisible to the defense scorer until the fatal bullet
// is in flight. canBreachRingFrom fires EARLY, while the ring stands.
//
// Gated by defenseBreachBonus (default 0 = OFF, byte-identical to pre-D2).
// These tests lock the ring predicate + the mutual exclusivity with the
// clear-shot predicate.
// ================================================================

/** Battlement (STAGES[33]): pure-brick maze, eagle at (12-13, 24-25). */
function battlementWorld(): World {
  const world = seedWorld(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(STAGES[33], 0)
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  return world
}

/** Empty 26×26 arena with the classic base + the 8-brick protection ring. */
function ringArena(): StageData {
  const tiles: string[] = []
  for (let r = 0; r < GRID; r++) {
    let row = ''
    for (let c = 0; c < GRID; c++) row += '.'
    if (r === 24 || r === 25) row = row.slice(0, 12) + 'EE' + row.slice(14)
    if (r === 23) row = row.slice(0, 11) + 'bbbb' + row.slice(15)
    if (r === 24 || r === 25) row = row.slice(0, 11) + 'b' + row.slice(12) // left ring col
    if (r === 24 || r === 25) row = row.slice(0, 14) + 'b' + row.slice(15) // right ring col
    tiles.push(row)
  }
  return { id: 9997, name: 'Ring Arena', tiles, enemies: ['basic'] }
}

/** Enemy at the given grid cell (basic, fully spawned). */
function placeEnemy(world: World, col: number, row: number, kind: Tank['kind'] = 'basic'): Tank {
  const e = world.createTank(kind, col * 16, row * 16, 'left')
  e.spawnTimer = 0
  world.tanks.push(e)
  return e
}

describe('D2 — canBreachRingFrom predicate (Battlement geometry)', () => {
  it('right-wing enemy aligned with an intact ring brick IS a breacher', () => {
    const world = battlementWorld()
    const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
    ai.reset()
    // (15,24): row 24, clear line to ring brick (14,24) — still intact.
    expect(canBreachRingFrom(ai, 15, 24)).toBe(true)
    expect(canShootBaseFrom(ai, 15, 24)).toBe(false) // ring still blocks
    // (16,25): aligned with ring brick (14,25) via row 25.
    expect(canBreachRingFrom(ai, 16, 25)).toBe(true)
  })

  it('mid-field enemy not aligned with any ring cell is not a breacher', () => {
    const world = battlementWorld()
    const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
    ai.reset()
    expect(canBreachRingFrom(ai, 10, 10)).toBe(false)
    expect(canBreachRingFrom(ai, 15, 22)).toBe(false)
    expect(canBreachRingFrom(ai, 20, 18)).toBe(false)
  })

  it('scene brick between enemy and ring blocks the breach predicate', () => {
    const world = battlementWorld()
    const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
    ai.reset()
    // (15,23) is separated from ring cell (14,23) by nothing — check the
    // actual terrain: on Battlement row 23 col 15 is open (antechamber),
    // so (15,23) is a breacher; (13,21) has the maze wall between.
    // The blocking case: (11,21) is brick-walled above the left ring —
    // scan from (11,21) down to (11,23) must pass (11,22) and hit brick.
    const t = world.tileMap.get(11, 22)
    if (t === 'brick') {
      expect(canBreachRingFrom(ai, 11, 21)).toBe(false)
    } else {
      expect(canBreachRingFrom(ai, 11, 21)).toBe(true)
    }
  })

  it('ring brick destroyed → same enemy flips from breacher to clear-shot', () => {
    const world = battlementWorld()
    const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
    ai.reset()
    expect(canBreachRingFrom(ai, 15, 24)).toBe(true)
    expect(canShootBaseFrom(ai, 15, 24)).toBe(false)
    world.tileMap.destroy(14, 24) // first ring brick falls
    // (15,24) now has a clear horizontal line to the base (row 24).
    expect(canBreachRingFrom(ai, 15, 24)).toBe(false) // ring cell no longer brick
    expect(canShootBaseFrom(ai, 15, 24)).toBe(true) // direct base threat NOW
  })

  it('enemyCanBreachRing wrapper reads the tank cell', () => {
    const world = battlementWorld()
    const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
    ai.reset()
    const breacher = placeEnemy(world, 15, 24, 'fast')
    const wanderer = placeEnemy(world, 10, 10, 'basic')
    expect(canBreachRingFrom(ai, 15, 24)).toBe(true)
    // tankCell buffer discipline: read the cell before any other tankCell call.
    const b = ai.tankCell(breacher)
    expect(b.col).toBe(15)
    expect(b.row).toBe(24)
    expect(canBreachRingFrom(ai, 10, 10)).toBe(false)
    const wcell = ai.tankCell(wanderer)
    expect(wcell.col).toBe(10)
  })
})

describe('D2 — params gating', () => {
  it('defenseBreachBonus defaults to 0 (OFF, byte-identical to pre-D2)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.defenseBreachBonus).toBe(0)
  })

  it('breach bonus outranks a mid-field enemy in base-threat scoring (knob ON)', () => {
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
    // Player parked near the base — so the race-to-base check in
    // isBaseUnderThreat() does NOT fire (player is closer than the enemy);
    // the breacher at (18,24) is outside the static box too → the base is
    // NOT under threat → normal chase. With the knob ON, the breacher must
    // still outrank the closer mid-field enemy in the base-threat branch.
    p.x = 12 * 16
    p.y = 21 * 16
    const breacher = placeEnemy(world, 18, 24, 'fast') // aligned with ring (14,24)
    const closer = placeEnemy(world, 10, 21, 'power') // nearer the player, not breaching
    const ai = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG(0x1234))
    ai.reset()
    // Base under threat: the closer enemy at (10,21) is inside the static
    // threat box (|col−12|≤3, row≥18) — the defense scoring branch runs.
    // The breacher (18,24) is detected by the ring predicate (not by §59 —
    // the ring still blocks a direct shot).
    expect(ai.isBaseUnderThreat()).toBe(true)
    expect(canBreachRingFrom(ai, 18, 24)).toBe(true)
    expect(canBreachRingFrom(ai, 10, 21)).toBe(false)
    expect(canShootBaseFrom(ai, 18, 24)).toBe(false)
    // Target selection in threat mode must pick the breacher (18,24) — the
    // ring-breach term outranks the closer mid-field enemy.
    const target = ai.selectTarget({ col: 12, row: 21 })
    expect(target?.col).toBe(18)
    expect(target?.row).toBe(24)
    expect(breacher.alive).toBe(true)
    expect(closer.alive).toBe(true)
  })
})
