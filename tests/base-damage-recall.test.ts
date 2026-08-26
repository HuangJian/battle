import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { CLASSIC_MODEL_PARAMS, GUARD_GOD_AI_PARAMS } from '../src/ai/god/params'
import { CELL } from '../src/constants'
import type { Tank } from '../src/types'
import { clearArena, seedWorld } from './helpers'

/**
 * §173: base damage recall (基地损伤召回) — unit tests.
 *
 * Root cause (hp-leash probe, tmp/probe-hpleash.ts): in S34 losses the base
 * takes its first hit with the player at median 25 cells away and only a
 * 5.1s median survival window left; in wins the player is already home
 * (median 10). The predictive threat signal flickers through that window
 * (§169: 9.8 flips/10s), so the recall never holds.
 *
 * Fix: isBaseUnderThreat() also returns true once the base has actually
 * TAKEN A HIT (baseHp < baseMaxHp) AND the player is farther than the
 * baseDamageRecall gate (cells). Damage is a fact, not a prediction — it
 * never flickers back; the distance gate releases the cascade once the
 * player comes home (arm 1 = unconditional was net −24 on open stages).
 * Gated by baseDamageRecall (0 = OFF).
 *
 * Tests isolate the §173 branch by disabling the §88 chokepoint detection
 * (chokepointMode=0) and the §157 clear-shot check (baseClearShotThreat=0),
 * and by placing no enemies near the base (static box / race checks silent).
 */

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
  input.hasBase = world.tileMap.hasBase()
  input.reset()
  return { world, input }
}

function placeEnemy(world: World, col: number, row: number, dir: Tank['dir'] = 'down'): Tank {
  const enemy = world.createTank('basic', col * CELL, row * CELL, dir)
  enemy.alive = true
  enemy.spawnTimer = 0
  world.tanks.push(enemy)
  return enemy
}

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.hp = 100
  p.spawnTimer = 0
  p.alive = true
}

function refreshEnemies(input: GodAIInput, world: World): void {
  input._baseUnderThreatCache = null
  input._enemies.length = 0
  for (const t of world.tanks) {
    if (t.alive && t.spawnTimer <= 0) input._enemies.push(t)
  }
}

const ISOLATED = { chokepointMode: 0, baseClearShotThreat: 0 }

describe('§173: base damage recall', () => {
  it('fires once the base has taken a hit and the player is past the gate', () => {
    const { world, input } = setupWorld({ ...ISOLATED, baseDamageRecall: 1 })
    positionPlayer(world, 2, 2) // dist to base |2-12|+|2-24| = 32 > 1
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(false)
    // One direct hit lands: ring breached, baseHp drops.
    world.baseHp = world.baseMaxHp - 34
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('distance gate: near-home player does NOT trigger (release condition)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, baseDamageRecall: 12 })
    positionPlayer(world, 10, 22) // dist |10-12|+|22-24| = 4 ≤ 12
    world.baseHp = world.baseMaxHp - 34
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(false)
    // Same damage, player far away (dist 32 > 12) — recall engages.
    positionPlayer(world, 2, 2)
    input.endFrame() // invalidate the per-tick playerCell cache
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('baseDamageRecall=0 is byte-identical (damage alone does not fire)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, baseDamageRecall: 0 })
    world.baseHp = world.baseMaxHp - 34
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('does NOT fire while the base is undamaged (baseHp == baseMaxHp)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, baseDamageRecall: 1 })
    expect(world.baseHp).toBe(world.baseMaxHp)
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(false)
  })

  it('never reduces existing detection (static box still fires undamaged)', () => {
    const { world, input } = setupWorld({ ...ISOLATED, baseDamageRecall: 1 })
    // Enemy inside the static box (|col-12|<=3, row>=18), base undamaged.
    placeEnemy(world, 10, 20)
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('stays engaged after the hit (flicker-free): repeated reads stay true', () => {
    const { world, input } = setupWorld({ ...ISOLATED, baseDamageRecall: 1 })
    positionPlayer(world, 2, 2)
    world.baseHp = world.baseMaxHp - 34
    refreshEnemies(input, world)
    expect(input.isBaseUnderThreat()).toBe(true)
    // Next tick: cache cleared, still no predictive trigger, damage persists.
    input._baseUnderThreatCache = null
    input.endFrame()
    expect(input.isBaseUnderThreat()).toBe(true)
  })

  it('defaults: shipped 0, classic restore 0, guard profile 0', () => {
    expect(DEFAULT_GOD_AI_PARAMS.baseDamageRecall).toBe(0)
    expect(CLASSIC_MODEL_PARAMS.baseDamageRecall).toBe(0)
    expect(GUARD_GOD_AI_PARAMS.baseDamageRecall).toBe(0)
  })
})
