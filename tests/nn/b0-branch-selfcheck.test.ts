/**
 * P′-2b0 self-check：NN 实际动作分支 ≡ 「不强制」分支（逐字段）。
 *
 * 这是 24 动作 headroom harness 的正确性锚：强制拍必须走正式 `ForcedTickInput`，
 * 且「用 NN 自己的动作强制一拍」不得改变轨迹（否则 headroom 里混进了钩子误差）。
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
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { cloneWorld, restoreWorld } from '../../src/snapshot/WorldSerializer'
import { canonicalizeIds } from '../support/canonicalize-ids'
import { MOVE4_DECODE, HOLD_DIM, MOVE4_DIM, FIRE3_DIM } from '../../src/nn/action-space-v6'
import { ForcedTickInput, type ForcedAction } from '../../tools/sim/forced-input'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { seedWorld } from '../helpers'
import type { Direction } from '../../src/constants'

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

function v6WeightsDir(): { dir: string; path: string } {
  const params = { ...GOLDEN.params }
  const k = GOLDEN.head_hidden
  const z = (rows: number, cols: number): Float32Array => new Float32Array(rows * cols)
  params['move_head.weight'] = { shape: [MOVE4_DIM, k], data: f32b64(z(MOVE4_DIM, k)) }
  params['move_head.bias'] = { shape: [MOVE4_DIM], data: f32b64(Float32Array.from([10, 0, 0, 0])) }
  params['fire_head.weight'] = { shape: [FIRE3_DIM, k], data: f32b64(z(FIRE3_DIM, k)) }
  params['fire_head.bias'] = { shape: [FIRE3_DIM], data: f32b64(Float32Array.from([0, 0, 10])) }
  params['hold_head.weight'] = { shape: [HOLD_DIM, k], data: f32b64(z(HOLD_DIM, k)) }
  params['hold_head.bias'] = { shape: [HOLD_DIM], data: f32b64(Float32Array.from([10, 0])) }
  const dir = mkdtempSync(join(tmpdir(), 'b0-selfcheck-'))
  const path = join(dir, 'weights.json') // resolveLatestWeights 的兜底文件名
  writeFileSync(
    path,
    JSON.stringify({ arch: { kind: 'student', h: GOLDEN.h, d: GOLDEN.d }, params }),
  )
  return { dir, path }
}

function v6WeightsPath(): string {
  return v6WeightsDir().path
}

function makeRun(): { world: World; input: NNInput; sim: Simulation } {
  const world = seedWorld(7)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  const input = new NNInput(world, { weightsPath: v6WeightsPath(), decisionK: 10 })
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[0], 0)
  world.state = 'playing'
  input.reset()
  return { world, input, sim }
}

function actualAction(input: NNInput): ForcedAction {
  const move = input.getMoveDirection()
  const dir: Direction = move ?? MOVE4_DECODE[0]
  const fire = input.isFiring() ? (input.isFireBeforeTurn() ? 1 : 2) : 0
  return { moveDir: dir, fire: fire !== 0, fireBeforeTurn: fire === 1, holding: input.isHolding() }
}

describe('P′-2b0 branch hook self-check', () => {
  it('harness 构造/推进与 runSimulation 一致（同权重 120 拍逐字段对账）', () => {
    const { dir, path } = v6WeightsDir()
    const weightFile = path
    const fromRunner = runSimulation({
      seed: 20261130,
      stage: STAGES[0],
      stageIndex: 0,
      difficulty: 'hard',
      policy: 'nn',
      nnWeightsDir: dir,
      playerLevel: 0,
      maxTicks: 120,
      collectMetrics: false,
      collectEvents: false,
    })

    // 与 tools/sim/nn-branch-headroom.ts::runGame 同序的手工循环。
    const world = new World()
    world.rng.reseed(20261130)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    world.playerLevel = 0
    world.lives = world.difficulty?.startLives ?? 3
    const input = new NNInput(world, { weightsPath: weightFile, decisionK: 10 })
    const sim = new Simulation(world, input)
    world.loadStageData(STAGES[0], 0)
    input.reset()
    for (let t = 0; t < 120; t++) {
      if (world.state !== 'playing') break
      sim.tick()
      input.endFrame()
      world.consumeEvents()
    }

    const outcome =
      world.state === 'stageclear' || world.state === 'victory'
        ? 'stage_clear'
        : world.state === 'playing'
          ? 'max_ticks'
          : world.state
    expect(outcome).toBe(fromRunner.outcome)
    expect(world.frame).toBe(fromRunner.ticks)
    expect(world.killCount).toBe(fromRunner.finalState.killCount)
    expect(world.lives).toBe(fromRunner.finalState.lives)
    expect(!world.tileMap.isBaseDestroyed()).toBe(fromRunner.finalState.baseAlive)
    expect(world.playerLevel).toBe(fromRunner.finalState.playerLevel)
  })

  it('强制 NN 实际动作 1 拍 ≡ 不强制（H=20 逐字段一致）', () => {
    const setup = makeRun()
    for (let i = 0; i < 40; i++) {
      setup.sim.tick()
      setup.input.endFrame()
    }
    const snap = cloneWorld(setup.world)
    const state = setup.input.captureDecisionState()
    const forcedAction = actualAction(setup.input)

    const run = (force: ForcedAction | null): unknown => {
      restoreWorld(setup.world, snap)
      setup.input.restoreDecisionState(state)
      const H = 20
      for (let k = 0; k < H; k++) {
        if (k === 0 && force) {
          const wrapped = new ForcedTickInput(setup.input, force, 1)
          setup.sim.input = wrapped
          setup.sim.tick()
          wrapped.endFrame()
          setup.sim.input = setup.input
        } else {
          setup.sim.tick()
          setup.input.endFrame()
        }
      }
      return canonicalizeIds({
        world: cloneWorld(setup.world),
        nn: setup.input.captureDecisionState(),
      })
    }

    const control = run(null)
    const withForced = run(forcedAction)
    expect(withForced).toEqual(control)
  })
})
