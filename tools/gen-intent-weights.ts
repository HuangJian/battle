#!/usr/bin/env bun
// gen-intent-weights.ts — 生成随机全尺寸意图权重 JSON（M4 阶段 m1-eval sanity 用；
// M5 出真实训练权重后此工具仅供测试 fixture。确定性 LCG，无随机性泄漏）。
// 用法：bun tools/gen-intent-weights.ts <out.json>
const H = 64
const D = 8

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
function f32ToB64(a: Float32Array): string {
  const bytes = new Uint8Array(a.buffer)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

const out = process.argv[2]
if (!out) {
  console.error('usage: bun tools/gen-intent-weights.ts <out.json>')
  process.exit(1)
}

const rand = lcg(20260826)
const params: Record<string, unknown> = {}
const add = (name: string, shape: number[]): void => {
  params[name] = { shape, data: f32ToB64(randLayer(rand, shape)) }
}
add('stem.weight', [H, 16, 3, 3])
add('stem.bias', [H])
for (let i = 0; i < D; i++) {
  add(`blocks.${i}.dw.weight`, [H, 1, 5, 5])
  add(`blocks.${i}.dw.bias`, [H])
  add(`blocks.${i}.pw.weight`, [H, H, 1, 1])
  add(`blocks.${i}.pw.bias`, [H])
}
add('fc.weight', [128, H + 19])
add('fc.bias', [128])
add('move_head.weight', [5, 128])
add('move_head.bias', [5])
add('fire_head.weight', [2, 128])
add('fire_head.bias', [2])
add('intent_head.weight', [8, 137])
add('intent_head.bias', [8])
add('enemy_head.weight', [5, 137])
add('enemy_head.bias', [5])
add('anchor_head.weight', [16, 137])
add('anchor_head.bias', [16])

const json = {
  format: 'nn-weights-json',
  version: 1,
  schema_major: 2,
  arch: {
    kind: 'intent',
    h: H,
    d: D,
    headHidden: 128,
    intentDim: 8,
    enemyDim: 5,
    anchorDim: 16,
    injectDim: 9,
  },
  numParams: 71529,
  params,
  note: 'random-weight test fixture (M4 sanity; M5 real training replaces it)',
}
await Bun.write(out, JSON.stringify(json, null, 1))
console.log(`wrote ${out} (${Object.keys(params).length} layers)`)

export {}
