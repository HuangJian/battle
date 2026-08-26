import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import type { GodAIParams } from '../src/ai/GodAIInput'
import { superItemPressesImpl } from '../src/ai/god/SuperItems'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { GRID, TANK } from '../src/constants'
import type { StageData } from '../src/types'
import { placeEnemy, seedWorld } from './helpers'

// ================================================================
// §167 / B4 — super-item strategic activation.
//
// God AI previously never activated stocked super items (wasItemPressed
// always false). hard 35×120 forensics: ~8% of losing runs finish holding
// unused guard/frenzy stock. superItemMode=1 lets think() press F5 (guard
// when the base is threatened) and F6 (frenzy when the facing corridor has
// an enemy and no bullet is incoming). Default 0 = byte-identical.
// ================================================================

function onParams(): GodAIParams {
  // Explicitly both gates ON — the shipped default keeps frenzyAim at 0.
  return {
    ...DEFAULT_GOD_AI_PARAMS,
    superItemMode: 1,
    superItemGuardThreat: 1,
    superItemFrenzyAim: 1,
  }
}

/** Explicit OFF (the shipped default keeps guard ON — mode 1, frenzy 0). */
function offParams(): GodAIParams {
  return { ...DEFAULT_GOD_AI_PARAMS, superItemMode: 0 }
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

/** Player parked at cell (0,24), facing up, God AI constructed + reset. */
function setup(
  params: GodAIParams = DEFAULT_GOD_AI_PARAMS,
  seed = 42,
): { world: World; ai: GodAIInput } {
  const world = seedWorld(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = { ...RULES['hard'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(makeEmptyStage(), 0)
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

/** Minimal DecisionContext for direct superItemPressesImpl calls. */
function makeCtx(ai: GodAIInput): DecisionContext {
  const w = ai.world
  const p = w.player!
  return {
    w,
    p,
    pcx: p.x + p.w / 2,
    pcy: p.y + p.h / 2,
    onCooldown: false,
    aimDir: null,
    threat: null,
    shielded: false,
  }
}

describe('§167 / B4 — super-item strategic activation', () => {
  it('OFF (mode 0): never presses, even with stock + threat (byte-identical)', () => {
    const { world, ai } = setup(offParams())
    world.guardStock = 1
    world.frenzyStock = 1
    placeEnemy(world, 12, 22) // right next to the base — threat
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressGuard).toBe(false)
    expect(ai._pressFrenzy).toBe(false)
    expect(ai.wasItemPressed('guard')).toBe(false)
    expect(ai.wasItemPressed('frenzy')).toBe(false)
    expect(ai.wasItemPressed('rewind')).toBe(false)
  })

  it('guard: presses F5 when base threatened + stock + no living guard', () => {
    const { world, ai } = setup(onParams())
    world.guardStock = 1
    placeEnemy(world, 12, 22) // inside the static threat box (row>=18, |col-12|<=3)
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressGuard).toBe(true)
  })

  it('guard: no press without threat (enemy far north)', () => {
    const { world, ai } = setup(onParams())
    world.guardStock = 1
    placeEnemy(world, 2, 2) // far from base, not aligned-clear-shot either? (col 2 not a base lane)
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressGuard).toBe(false)
  })

  it('guard: no press without stock', () => {
    const { world, ai } = setup(onParams())
    world.guardStock = 0
    placeEnemy(world, 12, 22)
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressGuard).toBe(false)
  })

  it('guard: no re-press while an allied guard already lives', () => {
    const { world, ai } = setup(onParams())
    world.guardStock = 1
    placeEnemy(world, 12, 22)
    const g = world.createTank('armor', 14 * 16, 23 * 16, 'up')
    g.allegiance = 'ally'
    g.spawnTimer = 0
    world.allies.push(g)
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressGuard).toBe(false)
  })

  it('frenzy: presses F6 when facing corridor has an enemy, no threat', () => {
    const { world, ai } = setup(onParams())
    world.frenzyStock = 1
    const p = world.player!
    p.x = 12 * 16 // col 12, facing up — park in open ground (row 18), NOT on the eagle
    p.y = 18 * 16
    p.dir = 'up'
    placeEnemy(world, 12, 8) // straight up the facing corridor
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressFrenzy).toBe(true)
    expect(ai._pressGuard).toBe(false)
  })

  it('frenzy: no press when the facing corridor is empty', () => {
    const { world, ai } = setup(onParams())
    world.frenzyStock = 1
    world.player!.x = 12 * 16
    world.player!.y = 18 * 16
    world.player!.dir = 'up'
    placeEnemy(world, 3, 8) // nowhere near the facing lane
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressFrenzy).toBe(false)
  })

  it('frenzy: suppressed while an incoming bullet threat is active', () => {
    const { world, ai } = setup(onParams())
    world.frenzyStock = 1
    world.player!.x = 12 * 16
    world.player!.y = 18 * 16
    world.player!.dir = 'up'
    placeEnemy(world, 12, 8)
    const ctx = makeCtx(ai)
    ctx.threat = { id: 1 } as never // any non-null threat stands in
    superItemPressesImpl(ai, ctx)
    expect(ai._pressFrenzy).toBe(false)
  })

  it('frenzy: never re-releases mid-barrage', () => {
    const { world, ai } = setup(onParams())
    world.frenzyStock = 1
    world.player!.x = 12 * 16
    world.player!.y = 18 * 16
    world.player!.dir = 'up'
    world.player!.frenzyTimer = 500
    placeEnemy(world, 12, 8)
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressFrenzy).toBe(false)
  })

  it('endFrame clears the flags', () => {
    const { world, ai } = setup(onParams())
    world.guardStock = 1
    placeEnemy(world, 12, 22)
    const ctx = makeCtx(ai)
    superItemPressesImpl(ai, ctx)
    expect(ai._pressGuard).toBe(true)
    ai.endFrame()
    expect(ai._pressGuard).toBe(false)
    expect(ai._pressFrenzy).toBe(false)
  })

  it('end-to-end: Simulation consumes the press — guard ally spawns, stock spent', () => {
    const world = seedWorld(1234)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = { ...RULES['hard'] }
    const ai = new GodAIInput(world, onParams(), new RNG(1234 ^ 0x1234))
    const sim = new Simulation(world, ai)
    world.loadStageData(makeEmptyStage(), 0)
    ai.reset()
    const p = world.player!
    p.spawnTimer = 0
    p.x = 0
    p.y = GRID * 16 - TANK
    // Threat + stock.
    world.guardStock = 1
    world.tanks = []
    const e = world.createTank('basic', 12 * 16, 22 * 16, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)

    expect(world.allies.length).toBe(0)
    sim.tick()
    ai.endFrame()
    expect(world.guardStock).toBe(0) // stock consumed
    expect(world.allies.length).toBe(1) // the summoned guard
    expect(world.allies[0].allegiance).toBe('ally')
  })

  it('determinism: same seed, same world → identical press decisions', () => {
    const run = () => {
      const { world, ai } = setup(onParams(), 77)
      world.guardStock = 1
      world.frenzyStock = 1
      world.player!.x = 12 * 16
      world.player!.y = 18 * 16
      world.player!.dir = 'up'
      placeEnemy(world, 12, 8)
      const ctx = makeCtx(ai)
      superItemPressesImpl(ai, ctx)
      return `${ai._pressGuard}:${ai._pressFrenzy}`
    }
    expect(run()).toBe(run())
  })
})
