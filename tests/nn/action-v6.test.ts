import { describe, expect, it } from 'bun:test'
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import {
  FIRE3_DIM,
  HOLD_DIM,
  MASK_V6_DIM,
  MOVE4_DIM,
  decodeMove4,
  encodeMove4,
} from '../../src/nn/action-space-v6'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'
import { computeMasks } from '../../src/nn/obs-encoder'
import { NNInput } from '../../src/nn/policy-input'
import { World } from '../../src/game/World'
import { STAGES } from '../../src/config/stages'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES } from '../../src/config/rules'
import { seedWorld } from '../helpers'

/**
 * plan/nn-capability-upgrade.plan.md §4-B1: the v6 action space is three heads
 *   hold(2) / move(4, no STOP) / fire(3: none, before-turn, after-turn).
 *
 * Legacy 5/2 checkpoints must keep loading byte-identically (no hold_head key),
 * so the switch is the presence of `hold_head.*` in the weights JSON — never a
 * global constant flip. These tests pin the switch and the decode semantics.
 */

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

function zeros(rows: number, cols: number): Float32Array {
  return new Float32Array(rows * cols)
}

/** Build a v6 student: same trunk as the slim golden, heads rewritten. */
function v6Weights(opts: { moveBias: number[]; fireBias: number[]; holdBias: number[] }): string {
  const params = { ...GOLDEN.params }
  const inDim = GOLDEN.head_hidden
  params['move_head.weight'] = { shape: [MOVE4_DIM, inDim], data: f32b64(zeros(MOVE4_DIM, inDim)) }
  params['move_head.bias'] = { shape: [MOVE4_DIM], data: f32b64(Float32Array.from(opts.moveBias)) }
  params['fire_head.weight'] = { shape: [FIRE3_DIM, inDim], data: f32b64(zeros(FIRE3_DIM, inDim)) }
  params['fire_head.bias'] = { shape: [FIRE3_DIM], data: f32b64(Float32Array.from(opts.fireBias)) }
  params['hold_head.weight'] = { shape: [HOLD_DIM, inDim], data: f32b64(zeros(HOLD_DIM, inDim)) }
  params['hold_head.bias'] = { shape: [HOLD_DIM], data: f32b64(Float32Array.from(opts.holdBias)) }
  return JSON.stringify({
    arch: { kind: 'student', h: GOLDEN.h, d: GOLDEN.d },
    params,
  })
}

function makeWorld(): World {
  const world = seedWorld(7)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  world.loadStageData(STAGES[0], 0)
  world.state = 'playing'
  return world
}

function withWeightsFile(text: string): string {
  const dir = mkdtempSync(join(tmpdir(), 'nn-v6-'))
  const path = join(dir, 'weights.json')
  writeFileSync(path, text)
  return path
}

describe('B1 action space v6: constants and decode', () => {
  it('dims are hold2 / move4 / fire3 / mask9', () => {
    expect(HOLD_DIM).toBe(2)
    expect(MOVE4_DIM).toBe(4)
    expect(FIRE3_DIM).toBe(3)
    expect(MASK_V6_DIM).toBe(9)
  })

  it('move4 has no STOP class: 0..3 = up/down/left/right, out-of-range clamps to up', () => {
    expect([0, 1, 2, 3].map(decodeMove4)).toEqual(['up', 'down', 'left', 'right'])
    expect(decodeMove4(9)).toBe('up')
    expect(decodeMove4(-1)).toBe('up')
    for (const d of ['up', 'down', 'left', 'right'] as const) {
      expect(decodeMove4(encodeMove4(d))).toBe(d)
    }
  })
})

describe('B1 masks: legacy 5/2 vs v6 4/3', () => {
  it('legacy masks keep move=5 / fire=2 (old shards unchanged)', () => {
    const m = computeMasks(makeWorld())
    expect(m.move.length).toBe(5)
    expect(m.fire.length).toBe(2)
    expect(m.hold).toEqual([1, 1])
  })

  it('v6 masks are move=4 / fire=3 with only the cooldown gate carrying information', () => {
    const w = makeWorld()
    const p = w.player!
    p.nextFireInterval = 1
    p.lastFire = -9999
    const ready = computeMasks(w, true)
    expect(ready.move).toEqual([1, 1, 1, 1])
    expect(ready.fire).toEqual([1, 1, 1])
    expect(ready.hold).toEqual([1, 1])

    // Not ready: both shot slots masked, "no shot" stays legal.
    p.lastFire = w.frame * (1000 / 60)
    const cooling = computeMasks(w, true)
    expect(cooling.fire).toEqual([1, 0, 0])
  })
})

describe('B1 v6 model detection (hold_head presence, not a global flip)', () => {
  it('legacy golden has no hold head and keeps 5/2 heads', () => {
    const legacy = buildModelFromText(
      JSON.stringify({
        arch: { kind: 'student', h: GOLDEN.h, d: GOLDEN.d },
        params: GOLDEN.params,
      }),
    )
    expect(legacy.usesHoldFire3).toBe(false)
    expect(legacy.moveLogits.length).toBe(5)
    expect(legacy.fireLogits.length).toBe(2)
    expect(legacy.holdLogits ?? null).toBeNull()
  })

  it('a hold_head weights file switches the model to move4 / fire3 / hold2', () => {
    const model = buildModelFromText(
      v6Weights({
        moveBias: [0, 10, 0, 0],
        fireBias: [0, 0, 10],
        holdBias: [0, 10],
      }),
    ) as ModelLike
    expect(model.usesHoldFire3).toBe(true)
    expect(model.moveDim).toBe(4)
    expect(model.fireDim).toBe(3)
    expect(model.moveLogits.length).toBe(4)
    expect(model.fireLogits.length).toBe(3)
    expect(model.holdLogits?.length).toBe(2)

    const obs = new Uint8Array(16 * 26 * 26)
    model.forward(obs, new Float32Array(40), new Float32Array(37))
    // Trunk weights are zeroed? No — the slim golden trunk is random, so the
    // head output is bias + random projection. Only the ordering matters here:
    // the crafted bias dominates because head weights are exactly zero.
    expect(Array.from(model.moveLogits)).toEqual([0, 10, 0, 0])
    expect(Array.from(model.fireLogits)).toEqual([0, 0, 10])
    expect(Array.from(model.holdLogits!)).toEqual([0, 10])
  })
})

describe('B1 NNInput commits the v6 semantics', () => {
  it('hold=1 + move=direction + fire=2 reaches InputLike as such', () => {
    const path = withWeightsFile(
      v6Weights({
        moveBias: [10, 0, 0, 0], // up
        fireBias: [0, 0, 10], // fire after turn
        holdBias: [0, 10], // hold
      }),
    )
    const world = makeWorld()
    world.player!.lastFire = -9999
    const input = new NNInput(world, { weightsPath: path, decisionK: 10 })
    input.reset()

    expect(input.usesHoldFire3()).toBe(true)
    expect(input.getMoveDirection()).toBe('up')
    expect(input.isHolding()).toBe(true)
    expect(input.isFiring()).toBe(true)
    expect(input.isFireBeforeTurn()).toBe(false)
  })

  it('fire=1 is exposed as fireBeforeTurn', () => {
    const path = withWeightsFile(
      v6Weights({
        moveBias: [0, 0, 10, 0], // left
        fireBias: [0, 10, 0], // fire before turn
        holdBias: [10, 0], // advance
      }),
    )
    const world = makeWorld()
    world.player!.lastFire = -9999
    const input = new NNInput(world, { weightsPath: path, decisionK: 10 })
    input.reset()

    expect(input.getMoveDirection()).toBe('left')
    expect(input.isHolding()).toBe(false)
    expect(input.isFiring()).toBe(true)
    expect(input.isFireBeforeTurn()).toBe(true)
  })

  it('a cooling player cannot select either shot slot', () => {
    const path = withWeightsFile(
      v6Weights({
        moveBias: [10, 0, 0, 0],
        fireBias: [0, 0, 10],
        holdBias: [10, 0],
      }),
    )
    const world = makeWorld()
    const p = world.player!
    p.nextFireInterval = 1000
    p.lastFire = world.frame * (1000 / 60) // just fired
    const input = new NNInput(world, { weightsPath: path, decisionK: 10 })
    input.reset()

    expect(input.isFiring()).toBe(false)
    expect(input.isFireBeforeTurn()).toBe(false)
  })
})
