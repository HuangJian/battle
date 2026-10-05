#!/usr/bin/env bun
/**
 * spatial-s0a-golden.ts — S0-a 第二部分：旧权重（hu150）在**三后端**上的 golden 复现。
 *
 * 为什么需要（plan/policy-spatial-head.plan.md §4 Step 2 S0-a）：schema 升级之前，这是
 * 最后一次能用旧布局做「语义级」确认的机会——"能加载"只证明形状对，golden 才证明语义对。
 * 做法：固定 obs/scalars（LCG 确定性），同一权重分别走 native / wasm / TS 三条路径，
 * 逐位比对 pooled / bufA / 三头 logits：
 *   · native ↔ wasm：契约 = **逐字节相等**（同内核源码两编译目标；tests/native-parity 同门）；
 *   · TS ↔ wasm：数学等价、累加次序异 ⇒ 容差 ≤1e-4（goal/student golden 同规）。
 *
 * Usage:
 *   bun tools/sim/spatial-s0a-golden.ts --weights nn-training/weights/x20-adv-hurt/x20-adv-hurt.it150.*.json
 */
import { readFileSync, writeFileSync, mkdirSync } from 'fs'
import { dirname } from 'path'
import { createHash } from 'crypto'
import { buildModelFromText } from '../../src/nn/infer'
import {
  featuresEngine,
  resetNativeConvForTest,
  setNativeConvEnabled,
} from '../../src/nn/conv/conv_native_adapter'
import { setStudentConvWasmEnabled } from '../../src/nn/conv/conv_wasm_adapter'
import { OBS_CHANNELS, BOARD, SCALAR_DIM } from '../../src/nn/obs-encoder'

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function makeRng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

type Backend = 'native' | 'wasm' | 'ts'

interface Capture {
  pooled: Float32Array
  bufA: Float32Array
  moveLogits: Float32Array
  fireLogits: Float32Array
  valueOut: Float32Array
  engine: string
}

function forceBackend(b: Backend): void {
  // ⚠ 顺序：先 reset（清模块级缓存），再设开关——反了的话 reset 会把
  // setNativeConvEnabled(false) 的强制关闭状态清掉（_status=null ⇒ 重新 attest ⇒ 又走 native）。
  resetNativeConvForTest()
  setNativeConvEnabled(b === 'native')
  setStudentConvWasmEnabled(b !== 'ts')
}

function runOne(weightsText: string, obs: Uint8Array, scalars: Float32Array, b: Backend): Capture {
  forceBackend(b)
  const model = buildModelFromText(weightsText) as unknown as {
    forward(obs: Uint8Array, sc: Float32Array): void
    pooled: Float32Array
    bufA: Float32Array
    moveLogits: Float32Array
    fireLogits: Float32Array
    valueOut: Float32Array
  }
  model.forward(obs, scalars)
  return {
    pooled: Float32Array.from(model.pooled),
    bufA: Float32Array.from(model.bufA),
    moveLogits: Float32Array.from(model.moveLogits),
    fireLogits: Float32Array.from(model.fireLogits),
    valueOut: Float32Array.from(model.valueOut),
    engine: featuresEngine(),
  }
}

function bytesOf(a: Float32Array): Buffer {
  return Buffer.from(a.buffer, a.byteOffset, a.byteLength)
}

function maxAbs(a: Float32Array, b: Float32Array): number {
  let m = 0
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]))
  return m
}

/** 相对漂移：max|Δ| / (1 + max|ref|)（logits 量级随输入分布变化，绝对容差没有可比性）。 */
function relDelta(a: Float32Array, b: Float32Array): number {
  let m = 0
  let ref = 0
  for (let i = 0; i < a.length; i++) {
    m = Math.max(m, Math.abs(a[i] - b[i]))
    ref = Math.max(ref, Math.abs(b[i]))
  }
  return m / (1 + ref)
}

function main(): void {
  const weightsPath = arg('weights')
  if (!weightsPath) throw new Error('--weights <json> is required')
  const outPath = arg('out', 'tmp/spatial-s0a/golden-readings.json')!
  const trials = parseInt(arg('trials', '4')!, 10)
  const weightsText = readFileSync(weightsPath, 'utf8')
  const sha16 = createHash('sha256').update(weightsText).digest('hex').slice(0, 16)

  const sp = BOARD * BOARD
  const rng = makeRng(20261005)
  const readings: Array<Record<string, number | string | boolean>> = []
  let nativeWasmBitwise = true
  // 两类输入：① 稀疏（贴近真实 obs：~94% 为 0）；② 稠密随机（压力档，logits 会很大）。
  const kinds = ['sparse', 'dense'] as const
  const tsDelta: Record<string, { abs: number; rel: number }> = {}
  for (const kind of kinds) {
    for (let t = 0; t < trials; t++) {
      const obs = new Uint8Array(OBS_CHANNELS * sp)
      for (let i = 0; i < obs.length; i++) {
        obs[i] =
          kind === 'sparse'
            ? rng() < 0.06
              ? Math.floor(rng() * 32) + 1
              : 0
            : Math.floor(rng() * 256)
      }
      const scalars = new Float32Array(SCALAR_DIM)
      for (let i = 0; i < scalars.length; i++) scalars[i] = (rng() - 0.5) * 2

      const nat = runOne(weightsText, obs, scalars, 'native')
      const was = runOne(weightsText, obs, scalars, 'wasm')
      const ts = runOne(weightsText, obs, scalars, 'ts')

      const bitwise =
        bytesOf(nat.pooled).equals(bytesOf(was.pooled)) &&
        bytesOf(nat.bufA).equals(bytesOf(was.bufA)) &&
        bytesOf(nat.moveLogits).equals(bytesOf(was.moveLogits)) &&
        bytesOf(nat.fireLogits).equals(bytesOf(was.fireLogits)) &&
        bytesOf(nat.valueOut).equals(bytesOf(was.valueOut))
      nativeWasmBitwise = nativeWasmBitwise && bitwise
      const abs = Math.max(
        maxAbs(ts.pooled, was.pooled),
        maxAbs(ts.bufA, was.bufA),
        maxAbs(ts.moveLogits, was.moveLogits),
        maxAbs(ts.fireLogits, was.fireLogits),
        maxAbs(ts.valueOut, was.valueOut),
      )
      const rel = Math.max(
        relDelta(ts.pooled, was.pooled),
        relDelta(ts.bufA, was.bufA),
        relDelta(ts.moveLogits, was.moveLogits),
        relDelta(ts.fireLogits, was.fireLogits),
        relDelta(ts.valueOut, was.valueOut),
      )
      const cur = tsDelta[kind] ?? { abs: 0, rel: 0 }
      tsDelta[kind] = { abs: Math.max(cur.abs, abs), rel: Math.max(cur.rel, rel) }
      readings.push({
        kind,
        trial: t,
        native_vs_wasm_bitwise: bitwise,
        ts_vs_wasm_max_abs_delta: abs,
        ts_vs_wasm_rel_delta: rel,
        engines: `${nat.engine}/${was.engine}/${ts.engine}`,
        moveLogits0: was.moveLogits[0],
      })
    }
  }
  // 恢复默认（native 优先）——进程收尾不影响其他，但保持礼貌。
  setNativeConvEnabled(true)
  setStudentConvWasmEnabled(true)
  resetNativeConvForTest()

  const out = {
    weights: weightsPath,
    weightsSha16: sha16,
    trials_per_kind: trials,
    native_vs_wasm_bitwise_all: nativeWasmBitwise,
    ts_vs_wasm: tsDelta,
    ts_tolerance_note:
      'TS 与加速后端数学等价、累加次序异 ⇒ 漂移 ∝ 量级；判据看相对漂移（sparse 档 ≈ 生产分布），dense 档为压力读数',
    per_trial: readings,
  }
  mkdirSync(dirname(outPath), { recursive: true })
  writeFileSync(outPath, JSON.stringify(out, null, 2))
  console.log(JSON.stringify(out, null, 2))
}

main()
