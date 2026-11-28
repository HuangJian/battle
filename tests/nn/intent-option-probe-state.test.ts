/**
 * P′-2b1：IntentOptionProbe 决策状态快照 + ForcedTickInput.decideFirst 契约。
 *
 *   · capture/restore 必须让分支从同一 tick 复现（同 seed 双跑逐字节一致）；
 *   · 强制「实际动作」且 decideFirst=true 时，必须与不强制分支在 1 拍后逐字段一致
 *     （executor 有 tracker，跳过 inner think 会分叉 ⇒ 这是 b1 self-check 的底座）。
 */
import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { IntentOptionProbe } from '../../src/nn/intent-option-probe'
import { ForcedTickInput } from '../../tools/sim/forced-input'
import { executorActionOf } from '../../tools/sim/executor-branch-headroom'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'
import type { Direction } from '../../src/constants'
import type { InputLike } from '../../src/game/Input'

function makeRun(seed: number): {
  world: World
  probe: IntentOptionProbe
  sim: Simulation
} {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const probe = new IntentOptionProbe(world, { seed })
  const sim = new Simulation(world, probe)
  world.loadStageData(STAGES[9], 9)
  probe.reset()
  return { world, probe, sim }
}

function run(world: World, probe: IntentOptionProbe, sim: Simulation, ticks: number): void {
  for (let t = 0; t < ticks; t++) {
    if (world.state !== 'playing') break
    sim.tick()
    probe.endFrame()
  }
}

/** 录制第一拍动作（其余透传）。 */
function recording(probe: IntentOptionProbe): {
  input: InputLike
  first: { moveDir: Direction | null; fire: boolean }
} {
  const first: { moveDir: Direction | null; fire: boolean } = { moveDir: null, fire: false }
  let recorded = false
  const input: InputLike = {
    getMoveDirection: () => {
      const d = probe.getMoveDirection()
      if (!recorded) first.moveDir = d
      return d
    },
    isFiring: () => {
      const f = probe.isFiring()
      if (!recorded) first.fire = f
      return f
    },
    endFrame: () => {
      recorded = true
      probe.endFrame()
    },
    wasItemPressed: () => probe.wasItemPressed(),
    reset: () => probe.reset(),
  }
  return { input, first }
}

describe('P′-2b1 probe state & forced tick', () => {
  it('capture/restore：同一快照的两个 60 拍分支逐字节一致', () => {
    const { world, probe, sim } = makeRun(20261140)
    run(world, probe, sim, 150)
    const snap = cloneWorld(world)
    const st = probe.captureDecisionState()

    run(world, probe, sim, 60)
    const a = canonicalizeIds(cloneWorld(world))

    // 恢复后重跑同一 60 拍（state 内部再深拷贝 ⇒ 可重复使用）。
    restoreWorld(world, snap)
    probe.restoreDecisionState(st)
    run(world, probe, sim, 60)
    const b = canonicalizeIds(cloneWorld(world))

    expect(b).toEqual(a)
  })

  it('ForcedTickInput(decideFirst)：强制实际动作 ≡ 不强制（1 拍）', () => {
    const { world, probe, sim } = makeRun(20261141)
    run(world, probe, sim, 200)

    const rec = recording(probe)
    const snap = cloneWorld(world)
    const st = probe.captureDecisionState()

    sim.input = rec.input
    sim.tick()
    rec.input.endFrame()
    sim.input = probe
    const control = canonicalizeIds(cloneWorld(world))

    restoreWorld(world, snap)
    probe.restoreDecisionState(st)
    // 执行器口径：null move ⇒ hold=1；否则 hold=0 + 方向；fire 二分（无 fireBeforeTurn）。
    const moveIdx = rec.first.moveDir
      ? ['up', 'down', 'left', 'right'].indexOf(rec.first.moveDir)
      : 0
    const hold = rec.first.moveDir === null ? 1 : 0
    const idx = hold * 12 + Math.max(0, moveIdx) * 3 + (rec.first.fire ? 2 : 0)
    const forced = new ForcedTickInput(probe, executorActionOf(idx), 1, { decideFirst: true })
    sim.input = forced
    sim.tick()
    forced.endFrame()
    sim.input = probe

    expect(canonicalizeIds(cloneWorld(world))).toEqual(control)
  })
})
