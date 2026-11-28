/**
 * P′-4a：HierOptionExecutor（option 联合槽 → E2′ nn-option）契约。
 *
 * 断言：
 *   · 掩码 argmax 永不选 HOLD_break；
 *   · rlPick 采样路径：slotTrace 记录承诺切换、窗口内命令冻结；
 *   · 同 seed 双跑逐字节一致（零 Math.random、God RNG 同源）；
 *   · 6 维以外的权重在构造期响亮拒绝（intentDim 必须 = 6）。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { OPTION_DIM } from '../../src/ai/intent/options'
import { HierOptionExecutor, argmaxOptionSlot } from '../../src/nn/hier-option-executor'
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

function makeExec(
  seed: number,
  pick: (tick: number) => number,
): { world: World; exec: HierOptionExecutor; sim: Simulation } {
  const world = makeWorld(seed)
  const exec = new HierOptionExecutor(world, {
    rlPick: (_obs, _scal, _inj, tick) => pick(tick),
    replanEvery: 30,
  })
  const sim = new Simulation(world, exec)
  exec.reset()
  return { world, exec, sim }
}

describe('P′-4a HierOptionExecutor', () => {
  it('掩码 argmax 跳过 HOLD_break（槽 5）', () => {
    const logits = new Float32Array(OPTION_DIM)
    logits[5] = 100
    logits[2] = 1
    expect(argmaxOptionSlot(logits)).toBe(2)
    const allMasked = new Float32Array(OPTION_DIM).fill(-Infinity)
    expect(argmaxOptionSlot(allMasked)).toBe(-1)
  })

  it('rlPick 采样：承诺切换入 trace，未切换不重复记录', () => {
    // 出生动画会让前几个 replan 帧不查询输入 ⇒ 用「第几次决策」而不是 tick 计数，
    // 前 2 次选 0、之后选 4。
    let picks = 0
    const { exec, sim } = makeExec(20261201, () => (picks++ < 2 ? 0 : 4))
    for (let t = 0; t < 400; t++) {
      sim.tick()
      exec.endFrame()
    }
    expect(exec.slotTrace).toEqual([0, 4])
    expect(exec.currentSlotId()).toBe('HOLD_direct')
  })

  it('同 seed 双跑逐字节一致（含 6 槽命令注入）', () => {
    const seed = 20261202
    const a = makeExec(seed, (tick) => (tick % 120 < 60 ? 1 : 4))
    const b = makeExec(seed, (tick) => (tick % 120 < 60 ? 1 : 4))
    for (let t = 0; t < 900; t++) {
      if (a.world.state !== 'playing' || b.world.state !== 'playing') break
      a.sim.tick()
      a.exec.endFrame()
      b.sim.tick()
      b.exec.endFrame()
    }
    expect(canonicalizeIds(cloneWorld(b.world))).toEqual(canonicalizeIds(cloneWorld(a.world)))
    expect(b.exec.slotTrace).toEqual(a.exec.slotTrace)
  })

  it('非 6 维权重构造期拒绝', () => {
    const world = makeWorld(20261203)
    // intent-golden 是现役 8 意图档（intentDim=8）——option 线必须响亮拒绝。
    // golden 的 h/d 在顶层（不在 arch），这里补 arch 让它走真实权重加载路径。
    const golden = JSON.parse(readFileSync('tests/fixtures/intent-golden.json', 'utf8')) as {
      h: number
      d: number
      params: Record<string, { shape: number[]; data: string }>
    }
    const weightsText = JSON.stringify({
      arch: { kind: 'intent', h: golden.h, d: golden.d, intentDim: 8, injectDim: 9 },
      params: golden.params,
    })
    expect(() => new HierOptionExecutor(world, { weightsText })).toThrow(/intentDim=8/)
  })
})
