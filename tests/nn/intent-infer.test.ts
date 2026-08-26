import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildIntentModelFromText } from '../../src/nn/infer'

/**
 * M4-A（P3-4）：TS/Py 前向数值一致测试——计划明文"现状不存在，需新建"。
 *
 * golden 由 nn-training/intent_net.py --golden 生成（固定 seed 瘦身 h=16/d=2、
 * 固定随机输入含非零注入），写入 tests/fixtures/intent-golden.json。TS 端
 * buildIntentModelFromText 加载同一权重 → intentForward → 三头 logits 与
 * golden 期望对比。
 *
 * 容差口径：TS（手写循环）与 torch（向量化内核）的浮点累加顺序不同，数值在
 * ≤1e-4 内一致即为通过（真实差异远小于此，见断言输出的 maxDiff）。
 */
const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'intent-golden.json'), 'utf8'),
) as {
  h: number
  d: number
  format: string
  obs: number[]
  scalars: number[]
  inject: number[]
  intentLogits: number[]
  enemyLogits: number[]
  anchorLogits: number[]
  params: Record<string, unknown>
}

function maxAbsDiff(a: number[], b: Float32Array): number {
  let m = 0
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]))
  return m
}

describe('IntentNet TS/Py forward consistency (P3-4)', () => {
  const model = buildIntentModelFromText(
    JSON.stringify({
      arch: { kind: 'intent', h: GOLDEN.h, d: GOLDEN.d },
      params: GOLDEN.params,
    }),
  )

  const obs = new Uint8Array(GOLDEN.obs)
  const scalars = new Float32Array(GOLDEN.scalars)
  const inject = new Float32Array(GOLDEN.inject)

  it('intent 头 logits 与 py golden ≤1e-4 一致', () => {
    model.intentForward(obs, scalars, inject)
    const diff = maxAbsDiff(GOLDEN.intentLogits, model.intentLogits)
    expect(diff).toBeLessThanOrEqual(1e-4)
  })

  it('enemy 头 logits 与 py golden ≤1e-4 一致', () => {
    model.intentForward(obs, scalars, inject)
    const diff = maxAbsDiff(GOLDEN.enemyLogits, model.enemyLogits)
    expect(diff).toBeLessThanOrEqual(1e-4)
  })

  it('anchor 头 logits 与 py golden ≤1e-4 一致', () => {
    model.intentForward(obs, scalars, inject)
    const diff = maxAbsDiff(GOLDEN.anchorLogits, model.anchorLogits)
    expect(diff).toBeLessThanOrEqual(1e-4)
  })

  it('注入变化 → 输出变化（注入路径确实被消费）', () => {
    model.intentForward(obs, scalars, inject)
    const base = [...model.intentLogits]
    const inject2 = new Float32Array(9)
    inject2[7] = 1 // prev-intent = 最后一类
    model.intentForward(obs, scalars, inject2)
    let changed = false
    for (let i = 0; i < 8; i++) if (base[i] !== model.intentLogits[i]) changed = true
    expect(changed).toBe(true)
  })

  it('主干 shape 与 exported arch 报道一致（h/d 溯源）', () => {
    expect(GOLDEN.format).toBe('intent-golden')
    expect(GOLDEN.h).toBe(16)
    expect(GOLDEN.d).toBe(2)
  })
})
