#!/usr/bin/env bun
/**
 * goal-space-budget.ts — Goal-Space 重建的机时/算力预算实测（手册 §11.4 / §16）。
 *
 * 三件事：
 *   ① 硬模拟吞吐（ticks/sec/core、单局 tick 与墙钟）→ 反事实标注成本外推
 *   ② 单前向推理成本（可配迭代数）→ 实机帧预算对照
 *   ③ 目标头相对主干的算力占比（解析给出，因头尚未实现）
 *
 * 用法：
 *   bun tools/perf/goal-space-budget.ts                      # 默认：模拟吞吐 + 快速推理
 *   bun tools/perf/goal-space-budget.ts --games 12           # 指定模拟局数
 *   bun tools/perf/goal-space-budget.ts --infer-iters 200    # 推理迭代数（默认 200）
 *   bun tools/perf/goal-space-budget.ts --skip-infer         # 跳过推理（快）
 *
 * 注：本工具是**测量**工具，不是门禁。门禁见手册 T11（上机实测）与 §16.5。
 */
import { STAGES } from '../../src/config/stages'
import { runSimulation } from '../sim/simulation-runner'

function argNum(name: string, dflt: number): number {
  const i = process.argv.indexOf('--' + name)
  if (i < 0) return dflt
  const v = parseInt(process.argv[i + 1], 10)
  return Number.isFinite(v) ? v : dflt
}
const hasFlag = (name: string) => process.argv.includes('--' + name)

// 网络维度（模块作用域：③④ 都要用）
const NET_H = 64
const NET_D = 8
const BOARD = 26
const SP = BOARD * BOARD
const SCALAR = 19
/** 主干 MAdds（bench-intent-infer.ts 常量）。 */
const MADDS = 37.5e6

// ---------------------------------------------------------------- ① 模拟吞吐
const GAMES_PER_STAGE = Math.max(1, argNum('games-per-stage', 3))
const probeStages = [STAGES[0], STAGES[9], STAGES[19], STAGES[33]]

let totalTicks = 0
const t0 = performance.now()
for (const st of probeStages) {
  for (let seed = 1; seed <= GAMES_PER_STAGE; seed++) {
    const r = runSimulation({
      seed,
      stage: st,
      difficulty: 'hard',
      policy: 'god',
      collectEvents: false,
    })
    totalTicks += r.ticks ?? 0
  }
}
const simMs = performance.now() - t0
const games = probeStages.length * GAMES_PER_STAGE
const tps = totalTicks / (simMs / 1000)
const ticksPerGame = totalTicks / games
const msPerGame = simMs / games
const replan = 30
const decisionsPerGame = Math.round(ticksPerGame / replan)

console.log('===== ① 硬模拟吞吐（hard, policy=god）=====')
console.log(`样本 ${games} 局（${probeStages.length} 关 × ${GAMES_PER_STAGE} seed）`)
console.log(`吞吐        : ${Math.round(tps).toLocaleString()} ticks/sec/core`)
console.log(
  `单局        : ${Math.round(ticksPerGame).toLocaleString()} tick / ${msPerGame.toFixed(0)} ms`,
)
console.log(`决策数/局   : ${decisionsPerGame}（replan=${replan}）`)

console.log('')
console.log('===== ② 反事实标注成本外推 =====')
console.log('公式：成本 = 局数 × 决策数/局 × K × H ÷ 吞吐')
for (const nGames of [350, 2100]) {
  for (const K of [8, 12, 16]) {
    for (const H of [120, 240]) {
      const sec = (nGames * decisionsPerGame * K * H) / tps
      const oneCore = sec / 60
      const six = oneCore / 6
      if (K === 12 || H === 120) {
        console.log(
          `  ${String(nGames).padStart(4)} 局 K=${String(K).padStart(2)} H=${String(H).padStart(3)}` +
            ` : ${oneCore.toFixed(1).padStart(6)} min/核 · 6 节点 ${six.toFixed(1).padStart(5)} min`,
        )
      }
    }
  }
}

// ---------------------------------------------------------------- ③ 推理成本
console.log('')
if (hasFlag('skip-infer')) {
  console.log('===== ③ 推理成本：--skip-infer，跳过 =====')
  console.log('（完整基准：bun tools/bench-intent-infer.ts，约 7.5 min）')
} else {
  const { StudentModel } = await import('../../src/nn/infer')
  const H = NET_H
  const D = NET_D

  const rand = (() => {
    let s = 20260826 >>> 0
    return () => {
      s = (s * 1103515245 + 12345) & 0x7fffffff
      return (s / 0x7fffffff - 0.5) * 0.4
    }
  })()
  const layer = (shape: number[]) => {
    const a = new Float32Array(shape.reduce((x, y) => x * y, 1))
    for (let i = 0; i < a.length; i++) a[i] = rand()
    return a
  }

  const params: Record<string, Float32Array> = {}
  params['stem.weight'] = layer([H, 16, 3, 3])
  params['stem.bias'] = layer([H])
  for (let i = 0; i < D; i++) {
    params[`blocks.${i}.dw.weight`] = layer([H, 1, 5, 5])
    params[`blocks.${i}.dw.bias`] = layer([H])
    params[`blocks.${i}.pw.weight`] = layer([H, H, 1, 1])
    params[`blocks.${i}.pw.bias`] = layer([H])
  }
  params['fc.weight'] = layer([128, H + SCALAR])
  params['fc.bias'] = layer([128])
  params['move_head.weight'] = layer([5, 128])
  params['move_head.bias'] = layer([5])
  params['fire_head.weight'] = layer([2, 128])
  params['fire_head.bias'] = layer([2])
  params['intent_head.weight'] = layer([8, 137])
  params['intent_head.bias'] = layer([8])
  params['enemy_head.weight'] = layer([5, 137])
  params['enemy_head.bias'] = layer([5])
  params['anchor_head.weight'] = layer([16, 137])
  params['anchor_head.bias'] = layer([16])

  const model = new StudentModel(params, { h: H, d: D }, true)
  const obs = new Uint8Array(14 * SP)
  const scalars = new Float32Array(SCALAR)
  const inject = new Float32Array(9)
  for (let i = 0; i < 14 * SP; i++) obs[i] = (i * 7 + 3) & 0xff
  inject[0] = 1

  const iters = argNum('infer-iters', 200)
  for (let i = 0; i < 50; i++) model.intentForward(obs, scalars, inject)
  const samples: number[] = []
  for (let rep = 0; rep < 3; rep++) {
    const s = performance.now()
    for (let i = 0; i < iters; i++) model.intentForward(obs, scalars, inject)
    samples.push((performance.now() - s) / iters)
  }
  samples.sort((a, b) => a - b)
  const median = samples[1]
  const macPerSec = MADDS / (median / 1000)

  console.log('===== ③ 单前向推理成本（h=64 d=8，随机权重）=====')
  console.log(`迭代 ${iters} × 3 组，中位 = ${median.toFixed(1)} ms`)
  console.log(`implied     = ${(macPerSec / 1e9).toFixed(2)} G MAC/s`)
  console.log(
    `60fps 帧预算 16.7 ms : ${median <= 16.7 ? 'FITS' : 'EXCEEDS → 需 Worker / 分帧摊还'}`,
  )
  console.log(`文档 §19 记的 41.1 ms 已过期，勿引用（手册 §16.1）`)
}

// ---------------------------------------------------------------- ④ 目标头占比
console.log('')
console.log('===== ④ 目标头算力占比（解析值）=====')
const goalMacs = NET_H * 1 * SP
console.log(`主干            : ${MADDS_LABEL()}`)
console.log(
  `goal 热图（1×1）: ${goalMacs.toLocaleString()} MACs = ${((goalMacs / 37.5e6) * 100).toFixed(3)}%`,
)
console.log(`两张热图        : ${(((2 * goalMacs) / 37.5e6) * 100).toFixed(3)}%`)
console.log(`删除 enemy+anchor 头: -${(137 * 5 + 137 * 16).toLocaleString()} MACs`)
console.log(
  `净变化          : +${(2 * goalMacs - 137 * 21).toLocaleString()} MACs = ` +
    `${(((2 * goalMacs - 137 * 21) / 37.5e6) * 100).toFixed(3)}%`,
)

function MADDS_LABEL(): string {
  return '37.5 M MACs（bench-intent-infer.ts 常量）'
}
