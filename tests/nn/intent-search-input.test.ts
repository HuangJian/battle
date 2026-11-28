/**
 * P′-2a-B：IntentSearchInput（oracle-search 选择器）契约。
 *
 * 断言：
 *   · 搜索分支绝不把主世界改脏（cloneWorld 前后逐字段相等），pending events 不丢；
 *   · 同 seed 双跑 intentTrace / 终局逐字节一致（零 Math.random、exec RNG 同源）；
 *   · 8 类意图的白名单 override 均由 vocab 唯一入口展开（ESCAPE 只含 survive+dodge）。
 */
import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { INTENT_IDS, candidateOverrideForIntent } from '../../src/ai/intent/vocab'
import { IntentSearchInput } from '../../tools/sim/intent-search-input'
import { cloneWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'

function makeRun(
  seed: number,
  opts: { searchEvery: number; horizon: number },
): { world: World; probe: IntentSearchInput; sim: Simulation } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const probe = new IntentSearchInput(world, { seed, ...opts })
  const sim = new Simulation(world, probe)
  world.loadStageData(STAGES[9], 9)
  probe.reset()
  return { world, probe, sim }
}

describe('P′-2a-B IntentSearchInput', () => {
  it('搜索分支不改主世界，且 pending events 原样保留', () => {
    const { world, probe } = makeRun(20261128, { searchEvery: 60, horizon: 20 })
    world.consumeEvents() // 清掉关卡加载事件
    const before = canonicalizeIds(cloneWorld(world))
    const marker = { type: 'explosion', x: 0, y: 0, kind: 'small' } as const
    world.pushEvent(marker)

    probe.getMoveDirection() // frame 0 触发一次完整搜索（8 候选 × 20 拍）

    expect(canonicalizeIds(cloneWorld(world))).toEqual(before)
    expect(world.consumeEvents()).toContain(marker)
    expect(probe.intentTrace.length).toBe(1)
    expect(probe.readSearchStats().searches).toBe(1)
    expect(probe.readSearchStats().branchTicks).toBe(8 * 20)
  })

  it('确定性：同 seed 双跑意图序列与终局逐字节一致', () => {
    const a = makeRun(20261129, { searchEvery: 60, horizon: 20 })
    const b = makeRun(20261129, { searchEvery: 60, horizon: 20 })
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

  it('白名单唯一入口：8 类非空；ESCAPE=survive+dodge；RETURN_DEFENSE 压制 dodge', () => {
    expect(candidateOverrideForIntent('ESCAPE')).toEqual(new Set(['survive', 'dodge']))
    expect(candidateOverrideForIntent('RETURN_DEFENSE')?.has('dodge')).toBe(false)
    for (const id of INTENT_IDS) {
      expect((candidateOverrideForIntent(id)?.size ?? 0) > 0).toBe(true)
    }
  })
})
