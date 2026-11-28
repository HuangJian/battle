/**
 * P′-2b0：NNInput 决策状态 capture/restore 契约。
 *
 * 分支 rollout 的正确性前提：同一 tick 的 WorldSnapshot + NNInputDecisionState
 * 恢复后，继续跑 K 拍必须与「未回档」逐字段一致；同一份 capture 可重复 restore。
 * NNInput 是贪心策略（无采样/RNG）⇒ 不需要 rngState，但仍需 gate/已提交动作。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { NNInput } from '../../src/nn/policy-input'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES } from '../../src/config/rules'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'
import { HOLD_DIM, MOVE4_DIM, FIRE3_DIM } from '../../src/nn/action-space-v6'
import { seedWorld } from '../helpers'

const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
) as {
  h: number
  d: number
  head_hidden: number
  params: Record<string, { shape: number[]; data: string }>
}

function f32b64(arr: Float32Array): string {
  return Buffer.from(arr.buffer, arr.byteOffset, arr.byteLength).toString('base64')
}

/** v6 学生权重；`withValue` 时附加 [1,128] 价值头（零权重 ⇒ V≡0）。 */
function weightsText(withValue: boolean): string {
  const params = { ...GOLDEN.params }
  const k = GOLDEN.head_hidden
  const zeros = (rows: number, cols: number): Float32Array => new Float32Array(rows * cols)
  params['move_head.weight'] = { shape: [MOVE4_DIM, k], data: f32b64(zeros(MOVE4_DIM, k)) }
  params['move_head.bias'] = { shape: [MOVE4_DIM], data: f32b64(Float32Array.from([10, 0, 0, 0])) }
  params['fire_head.weight'] = { shape: [FIRE3_DIM, k], data: f32b64(zeros(FIRE3_DIM, k)) }
  params['fire_head.bias'] = { shape: [FIRE3_DIM], data: f32b64(Float32Array.from([0, 0, 10])) }
  params['hold_head.weight'] = { shape: [HOLD_DIM, k], data: f32b64(zeros(HOLD_DIM, k)) }
  params['hold_head.bias'] = { shape: [HOLD_DIM], data: f32b64(Float32Array.from([10, 0])) }
  if (withValue) {
    params['value_head.weight'] = { shape: [1, k], data: f32b64(zeros(1, k)) }
    params['value_head.bias'] = { shape: [1], data: f32b64(Float32Array.from([0])) }
  } else {
    // golden trunk 自带 value 头：BC 档必须显式删掉（hasValueHead=false）。
    delete params['value_head.weight']
    delete params['value_head.bias']
  }
  return JSON.stringify({ arch: { kind: 'student', h: GOLDEN.h, d: GOLDEN.d }, params })
}

function withWeightsFile(text: string): string {
  const dir = mkdtempSync(join(tmpdir(), 'nn-state-'))
  const path = join(dir, 'weights.json')
  writeFileSync(path, text)
  return path
}

function makeRun(weightsPath: string): {
  world: World
  input: NNInput
  sim: Simulation
} {
  const world = seedWorld(7)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  const input = new NNInput(world, { weightsPath, decisionK: 10 })
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[0], 0)
  world.state = 'playing'
  input.reset()
  return { world, input, sim }
}

function step(run: ReturnType<typeof makeRun>, n: number): void {
  for (let i = 0; i < n; i++) {
    run.sim.tick()
    run.input.endFrame()
  }
}

describe('NNInput decision state capture/restore', () => {
  it('restore 后续写一致：World 快照与决策状态逐字段相等', () => {
    const run = makeRun(withWeightsFile(weightsText(true)))
    step(run, 60)
    const snap = cloneWorld(run.world)
    const state = run.input.captureDecisionState()

    step(run, 60)
    const expectedWorld = cloneWorld(run.world)
    const expectedState = run.input.captureDecisionState()

    restoreWorld(run.world, snap)
    run.input.restoreDecisionState(state)
    step(run, 60)

    expect(canonicalizeIds(cloneWorld(run.world))).toEqual(canonicalizeIds(expectedWorld))
    expect(canonicalizeIds(run.input.captureDecisionState())).toEqual(
      canonicalizeIds(expectedState),
    )
  })

  it('同一份 capture 可重复 restore（分支之间不串味）', () => {
    const run = makeRun(withWeightsFile(weightsText(true)))
    step(run, 30)
    const snap = cloneWorld(run.world)
    const state = run.input.captureDecisionState()

    step(run, 60)
    const expectedWorld = cloneWorld(run.world)
    const expectedState = run.input.captureDecisionState()

    // 第一次回档后故意再跑 10 拍（改脏现场），第二次仍须回到同一终点。
    restoreWorld(run.world, snap)
    run.input.restoreDecisionState(state)
    step(run, 10)

    restoreWorld(run.world, snap)
    run.input.restoreDecisionState(state)
    step(run, 60)

    expect(canonicalizeIds(cloneWorld(run.world))).toEqual(canonicalizeIds(expectedWorld))
    expect(canonicalizeIds(run.input.captureDecisionState())).toEqual(
      canonicalizeIds(expectedState),
    )
  })

  it('valueEstimate 只读：有 value 头返回数值，调用前后 World/决策状态不变', () => {
    const run = makeRun(withWeightsFile(weightsText(true)))
    step(run, 40)
    const beforeWorld = canonicalizeIds(cloneWorld(run.world))
    const beforeState = canonicalizeIds(run.input.captureDecisionState())

    const v = run.input.valueEstimate()
    expect(typeof v).toBe('number')
    expect(Number.isFinite(v as number)).toBe(true)

    expect(canonicalizeIds(cloneWorld(run.world))).toEqual(beforeWorld)
    expect(canonicalizeIds(run.input.captureDecisionState())).toEqual(beforeState)
  })

  it('无 value 头的 BC 档 ⇒ valueEstimate 返回 null（不伪报 V）', () => {
    const run = makeRun(withWeightsFile(weightsText(false)))
    step(run, 20)
    expect(run.input.valueEstimate()).toBeNull()
  })
})
