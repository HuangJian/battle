/**
 * P′-2a2：IntentOptionSearch（修饰维度 headroom 探针）契约。
 *
 * 断言：
 *   · L0（纯委托）与 IntentOptionProbe 同 cadence 时逐拍动作/终局一致（plumbing 对账）；
 *   · 搜索分支绝不把主世界改脏，pending events 不丢；
 *   · 同 seed 双跑 intentTrace / 终局逐字节一致（零 Math.random、exec RNG 同源）；
 *   · 候选集随 modifierLevel 单调扩展，且 option 恒等于注入意图。
 */
import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { BASE_POS, START_LIVES } from '../../src/constants'
import { INTENT_IDS } from '../../src/ai/intent/vocab'
import { IntentOptionProbe } from '../../src/nn/intent-option-probe'
import {
  IntentOptionSearch,
  buildModifierCandidates,
  type ModifierLevel,
} from '../../tools/sim/intent-option-search'
import { cloneWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'

function makeWorld(seed: number): World {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  world.loadStageData(STAGES[9], 9)
  return world
}

function makeSearch(
  seed: number,
  opts: { searchEvery: number; horizon: number; modifierLevel?: ModifierLevel },
): { world: World; probe: IntentOptionSearch; sim: Simulation } {
  const world = makeWorld(seed)
  // 测试口径：跳过出生动画，保证 oracle 在首窗就能给出意图（真实对局里 spawnTimer 由 tick 递减）。
  world.player!.spawnTimer = 0
  const probe = new IntentOptionSearch(world, { seed, ...opts })
  const sim = new Simulation(world, probe)
  probe.reset()
  return { world, probe, sim }
}

describe('P′-2a2 IntentOptionSearch', () => {
  it('L0 ≡ intent-option（同 cadence）：逐拍动作与终局逐字节一致', () => {
    const seed = 20261130
    const {
      world: wa,
      probe: a,
      sim: sa,
    } = makeSearch(seed, {
      searchEvery: 60,
      horizon: 60,
      modifierLevel: 0,
    })
    const worldB = makeWorld(seed)
    worldB.player!.spawnTimer = 0 // 与 makeSearch 的测试口径一致
    const b = new IntentOptionProbe(worldB, { seed, replanEvery: 60 })
    const sb = new Simulation(worldB, b)
    b.reset()

    for (let t = 0; t < 600; t++) {
      if (wa.state !== 'playing' || worldB.state !== 'playing') break
      const ma = a.getMoveDirection()
      const fa = a.isFiring()
      const mb = b.getMoveDirection()
      const fb = b.isFiring()
      expect(ma).toBe(mb)
      expect(fa).toBe(fb)
      sa.tick()
      a.endFrame()
      sb.tick()
      b.endFrame()
    }
    expect(canonicalizeIds(cloneWorld(worldB))).toEqual(canonicalizeIds(cloneWorld(wa)))
    expect(JSON.stringify(b.intentTrace)).toBe(JSON.stringify(a.intentTrace))
  })

  it('搜索分支不改主世界，且 pending events 原样保留', () => {
    const { world, probe } = makeSearch(20261131, {
      searchEvery: 60,
      horizon: 20,
      modifierLevel: 4,
    })
    world.consumeEvents()
    const marker = { type: 'explosion', x: 0, y: 0, kind: 'small' } as const
    let checked = false
    // fresh stage 首窗可能还在 spawn/dead（currentIntent=null，不搜）；逐个窗口试到首次搜索。
    for (let base = 60; base <= 600 && !checked; base += 60) {
      world.frame = base - 1
      probe.getMoveDirection()
      probe.endFrame()
      world.frame = base
      world.pushEvent(marker)
      const before = canonicalizeIds(cloneWorld(world))
      const searchesBefore = probe.readSearchStats().searches
      probe.getMoveDirection() // frame=base ⇒ 若意图非空则触发一次完整搜索
      const stats = probe.readSearchStats()
      if (stats.searches === searchesBefore) {
        // 本窗未搜索：标记事件还在，清掉标记继续下一窗口。
        world.consumeEvents()
        continue
      }
      expect(stats.searches).toBe(searchesBefore + 1)
      expect(canonicalizeIds(cloneWorld(world))).toEqual(before)
      expect(world.consumeEvents()).toContain(marker)
      // 每个候选（含纯委托控制组）恰好 horizon 拍。
      const intent = INTENT_IDS[probe.intentTrace[probe.intentTrace.length - 1]]
      const candidates = buildModifierCandidates(world, intent, 4).length
      expect(stats.branchTicks).toBe(candidates * 20)
      checked = true
    }
    expect(checked).toBe(true)
  })

  it('确定性：同 seed 双跑意图序列与终局逐字节一致', () => {
    const a = makeSearch(20261132, { searchEvery: 60, horizon: 20, modifierLevel: 4 })
    const b = makeSearch(20261132, { searchEvery: 60, horizon: 20, modifierLevel: 4 })
    for (let t = 0; t < 300; t++) {
      if (a.world.state !== 'playing' || b.world.state !== 'playing') break
      a.sim.tick()
      a.probe.endFrame()
      b.sim.tick()
      b.probe.endFrame()
    }
    expect(JSON.stringify(b.probe.intentTrace)).toBe(JSON.stringify(a.probe.intentTrace))
    expect(b.world.state).toBe(a.world.state)
    expect(canonicalizeIds(cloneWorld(b.world))).toEqual(canonicalizeIds(cloneWorld(a.world)))
  })

  it('候选集随档位单调扩展：option 恒等于意图，break 只挂 enemy，param 只挂 enemy/anchor', () => {
    // 开放场地 + 一个敌人 + 一个道具；HUNT 激活 enemy、powerup，不激活 anchor。
    const world = makeWorld(20261133)
    world.enemiesRemaining = 5
    const e = world.createTank('basic', 8 * 16, 5 * 16, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    world.addPowerUp({
      id: 1,
      type: 'star',
      x: 10 * 16,
      y: 5 * 16,
      w: 32,
      h: 32,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    })

    const sizes: number[] = []
    for (let level = 0 as ModifierLevel; level <= 4; level++) {
      const cmds = buildModifierCandidates(world, 'HUNT', level as ModifierLevel)
      sizes.push(cmds.length)
      for (const c of cmds) expect(c.option).toBe('HUNT')
      expect(cmds[0].targetKind).toBe('none')
      if (level === 1) {
        expect(cmds.map((c) => c.targetKind).sort()).toEqual(['enemy', 'none', 'powerup'])
      }
      if (level >= 3) {
        const breaks = cmds.filter((c) => c.pathMode === 'break')
        expect(breaks.length).toBeGreaterThan(0)
        for (const c of breaks) expect(c.targetKind).toBe('enemy')
      }
      if (level >= 4) {
        const params = cmds.filter((c) => c.param > 0)
        expect(params.length).toBeGreaterThan(0)
        for (const c of params) {
          expect(c.targetKind === 'enemy' || c.targetKind === 'anchor').toBe(true)
        }
      }
    }
    for (let i = 1; i < sizes.length; i++) expect(sizes[i]).toBeGreaterThanOrEqual(sizes[i - 1])

    // HOLD_LANE 激活 anchor：锚点必须是基地外一格的可站立格。
    const hold = buildModifierCandidates(world, 'HOLD_LANE', 1)
    const anchor = hold.find((c) => c.targetKind === 'anchor')
    expect(anchor).toBeDefined()
    const t = anchor!.target!
    // 锚点必须落在预注册的基地 ring 外一格集合（上/左/右）。
    expect(t.col >= BASE_POS.col - 2 && t.col <= BASE_POS.col + 3).toBe(true)
    expect(t.row >= BASE_POS.row - 2 && t.row <= BASE_POS.row + 1).toBe(true)
    expect(t.col >= BASE_POS.col && t.col <= BASE_POS.col + 1 && t.row >= BASE_POS.row).toBe(false)
  })
})
