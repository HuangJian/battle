#!/usr/bin/env bun
/**
 * bench-intent-infer.ts — M4-B 意图网单前向推理基准（计划 §4.3 实测口径落档）。
 *
 * 用全尺寸（h=64/d=8）IntentNet 随机权重测量 intentForward 的真实墙钟成本。
 * 权重值不改变 MAC 数/内存访问形状（仅影响 L2 命中率的一阶项），随机权重 +
 * 确定性 LCG 是合法的算术成本探针；M5 训练出真实三头权重后重跑一次做终值。
 *
 * 口径（预注册 §4.3）：
 *   - 单前向 ms 实测落档；
 *   - 摊销 ms/tick = 单前向 ÷ replan（24 与 50 两档）；
 *   - 对照 16.7ms 帧预算：>16.7 → 实机需 Worker/瘦身档，headless 不受限。
 *
 * 用法：bun tools/bench-intent-infer.ts
 */
import { StudentModel } from '../src/nn/infer'

const H = 64
const D = 8
const BOARD = 26
const SP = BOARD * BOARD
const SCALAR = 19
const INJECT = 9

// 确定性 LCG 随机权重（值域 [-0.2, 0.2)）。
function lcg(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff
    return (s / 0x7fffffff - 0.5) * 0.4
  }
}

function randLayer(rand: () => number, shape: number[]): Float32Array {
  const n = shape.reduce((a, b) => a * b, 1)
  const out = new Float32Array(n)
  for (let i = 0; i < n; i++) out[i] = rand()
  return out
}

function buildRandomIntentModel(): StudentModel {
  const rand = lcg(20260826)
  const params: Record<string, Float32Array> = {}
  const H2 = H * H
  params['stem.weight'] = randLayer(rand, [H, 16, 3, 3])
  params['stem.bias'] = randLayer(rand, [H])
  for (let i = 0; i < D; i++) {
    params[`blocks.${i}.dw.weight`] = randLayer(rand, [H, 1, 5, 5])
    params[`blocks.${i}.dw.bias`] = randLayer(rand, [H])
    params[`blocks.${i}.pw.weight`] = randLayer(rand, [H, H, 1, 1])
    params[`blocks.${i}.pw.bias`] = randLayer(rand, [H])
  }
  params['fc.weight'] = randLayer(rand, [128, H + SCALAR])
  params['fc.bias'] = randLayer(rand, [128])
  // 构造器要求 move/fire 头（intentForward 路径不使用，给占位维度即可）。
  params['move_head.weight'] = randLayer(rand, [5, 128])
  params['move_head.bias'] = randLayer(rand, [5])
  params['fire_head.weight'] = randLayer(rand, [2, 128])
  params['fire_head.bias'] = randLayer(rand, [2])
  params['intent_head.weight'] = randLayer(rand, [8, 137])
  params['intent_head.bias'] = randLayer(rand, [8])
  params['enemy_head.weight'] = randLayer(rand, [5, 137])
  params['enemy_head.bias'] = randLayer(rand, [5])
  params['anchor_head.weight'] = randLayer(rand, [16, 137])
  params['anchor_head.bias'] = randLayer(rand, [16])
  // P3-4 一致性保证：student_model.py 的 kaiming 初始化同样跟踪了这些形状；
  // 这里只测算术成本，无需真实训练权重。
  void H2
  return new StudentModel(params, { h: H, d: D }, true)
}

function main(): void {
  const model = buildRandomIntentModel()
  const obs = new Uint8Array(14 * SP)
  const scalars = new Float32Array(SCALAR)
  const inject = new Float32Array(INJECT)
  for (let i = 0; i < 14 * SP; i++) obs[i] = (i * 7 + 3) & 0xff
  for (let i = 0; i < SCALAR; i++) scalars[i] = ((i % 10) + 1) / 10
  inject[0] = 1

  const WARMUP = 200
  for (let i = 0; i < WARMUP; i++) model.intentForward(obs, scalars, inject)

  const ITERS = 4000
  const t0 = performance.now()
  for (let i = 0; i < ITERS; i++) model.intentForward(obs, scalars, inject)
  const t1 = performance.now()
  const perMs = (t1 - t0) / ITERS

  let finite = true
  for (let i = 0; i < 8; i++) if (!Number.isFinite(model.intentLogits[i])) finite = false
  for (let i = 0; i < 5; i++) if (!Number.isFinite(model.enemyLogits[i])) finite = false
  for (let i = 0; i < 16; i++) if (!Number.isFinite(model.anchorLogits[i])) finite = false

  const MADDS = 37.5e6 // §4.3 intent 网主干+三头 MAdds 量级
  const macPerSec = MADDS / (perMs / 1000)

  console.log('--- intent-net inference benchmark (M4-B, h=64 d=8, random weights) ---')
  console.log(`per intentForward (ms) : ${perMs.toFixed(3)}`)
  console.log(`intentForward FPS     : ${(1000 / perMs).toFixed(0)}`)
  console.log(`implied MAC/s         : ${(macPerSec / 1e9).toFixed(2)} G`)
  console.log(`margins finite        : ${finite}`)
  console.log('--- amortized per tick (plan §4.3 --replan cadence) ---')
  console.log(`÷ replan 24  -> ${(perMs / 24).toFixed(3)} ms/tick`)
  console.log(`÷ replan 50  -> ${(perMs / 50).toFixed(3)} ms/tick`)
  console.log(
    `frame budget 16.7ms  : ${perMs <= 16.7 ? 'FITS (ideal)' : 'EXCEEDS -> Worker/瘦身档（h=48 ≈25M）另立部署决策'}`,
  )
}

main()
