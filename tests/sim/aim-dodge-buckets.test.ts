/**
 * aim-dodge-buckets.test.ts — 8 列桶语义的端到端对账（plan §7.2「双端对账」/§7.3）。
 *
 * 两类证据：
 *  A. **训练侧（export-rl-rollout）恒等式**：真跑确定性轮次（零权重 student 夹具，
 *     形状与真权重同源 ⇒ schema 变了就红），断言末行
 *     `playerShots == aimHits+aimBricks+aimIgnited+aimMisses`（§3.1 铁律）+
 *     每局标量与末行逐字段一致；同 seed 双跑末行逐位相同（determinism）。
 *  B. **评估侧（export-eval-game）独立重算**：用 God 策略同一局的事件流**不看导出器**
 *     重算四桶（Set 去重 = settle-once / 一发多格只落一次），与导出器读数逐值相等；
 *     并验证 `runSimulation` 与 `runEvalOne` 同局（ticks 相同）后才对账。
 */
import { describe, expect, it } from 'bun:test'
import { STAGES } from '../../src/config/stages'
import { OBS_CHANNELS, SCALAR_DIM } from '../../src/nn/obs-encoder'
import { runSimulation } from '../../tools/sim/simulation-runner'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { AIM_COL, runOneBench } from '../../tools/sim/export-rl-rollout'

/**
 * 零填充 student 权重（不读真权重：`nn-training/weights/**` 是 gitignore，干净 clone 上不存在）。
 *
 * 形状 = **当前生产架构**（h=64 / d=8 / 8 个 block / 三头 + value 头）⇒ `buildModelFromText`
 * 与 `assertRolloutWeightsSchema` 仍按生产 schema 校验、跑的是生产特征路径
 * （**保留 schema 护栏**：不要缩小成 h=16/d=2 的瘦身规格）。
 *
 * 输入/标量宽度必须**跟住** `src/nn/obs-encoder.ts`（17 obs + 2 coord = 19 通道，标量 46）：
 * 加速卷积内核（native / wasm）的准入就是这组宽度，写成旧的 18 通道（16 obs / 30 标量）
 * 会静默回退 TS 特征路径 —— 实测同一局 1200 拍 **4.7s → 0.26s（约 17×）**。
 * 零权重下两条路径的 logits 都是 0（= 零 bias），轨迹逐位相同（2026-11-08 A/B：三个
 * (stage, seed) 的末行 metrics 完全相同）⇒ 这是纯成本收窄，不改任何断言。
 */
function zeroStudentWeights(): string {
  const h = 64
  const d = 8
  const inCh = OBS_CHANNELS
  const scalarDim = SCALAR_DIM
  const shapes: Record<string, number[]> = {
    'stem.weight': [h, inCh + 2, 3, 3],
    'stem.bias': [h],
    'fc.weight': [128, h + scalarDim],
    'fc.bias': [128],
    'move_head.weight': [5, 128],
    'move_head.bias': [5],
    'fire_head.weight': [2, 128],
    'fire_head.bias': [2],
    'value_head.weight': [1, 128],
    'value_head.bias': [1],
  }
  for (let i = 0; i < d; i++) {
    shapes[`blocks.${i}.dw.weight`] = [h, 1, 5, 5]
    shapes[`blocks.${i}.dw.bias`] = [h]
    shapes[`blocks.${i}.pw.weight`] = [h, h, 1, 1]
    shapes[`blocks.${i}.pw.bias`] = [h]
  }
  const params: Record<string, { shape: number[]; data: string }> = {}
  for (const [name, shape] of Object.entries(shapes)) {
    const n = shape.reduce((a, b) => a * b, 1)
    const f32 = new Float32Array(n)
    params[name] = { shape, data: Buffer.from(f32.buffer).toString('base64') }
  }
  return JSON.stringify({
    format: 'nn-weights-json',
    version: 1,
    schema_major: 3,
    arch: {
      kind: 'student',
      in_ch: inCh,
      board: 26,
      scalar_dim: scalarDim,
      h,
      d,
      head_hidden: 128,
    },
    params,
  })
}

const WEIGHTS = zeroStudentWeights()

describe('训练侧桶恒等式 + determinism（export-rl-rollout 真跑）', () => {
  it('末行：playerShots == hits+bricks+ignited+misses；标量与末行一致', () => {
    for (const [stageIdx, seed] of [
      [0, 1],
      [0, 7],
      [3, 1],
    ] as const) {
      const r = runOneBench(
        stageIdx,
        STAGES[stageIdx],
        seed,
        'hard',
        1200,
        WEIGHTS,
        'off',
        false,
        1,
        null,
      )
      const last = r.shard.metrics[r.shard.metrics.length - 1]!
      const sum =
        last[AIM_COL.hits]! + last[AIM_COL.bricks]! + last[AIM_COL.ignited]! + last[AIM_COL.misses]!
      expect(last[5]).toBe(sum) // playerShots ≡ 四桶之和（§3.1）
      expect(r.aimHits).toBe(last[AIM_COL.hits]!)
      expect(r.aimHitDistSum).toBe(last[AIM_COL.dist]!)
      expect(r.aimBricks).toBe(last[AIM_COL.bricks]!)
      expect(r.aimIgnited).toBe(last[AIM_COL.ignited]!)
      expect(r.aimMisses).toBe(last[AIM_COL.misses]!)
      expect(r.hurtWeight).toBe(last[AIM_COL.hurt]!)
      expect(r.enclWeightTicks).toBe(last[AIM_COL.encl]!)
      expect(r.cornerWeightTicks).toBe(last[AIM_COL.corner]!)
      // 计数列非负且为整数（回写不产生分数、不产生负数）
      for (const col of [AIM_COL.hits, AIM_COL.bricks, AIM_COL.ignited, AIM_COL.misses]) {
        expect(last[col]!).toBeGreaterThanOrEqual(0)
        expect(Number.isInteger(last[col]!)).toBe(true)
      }
    }
  })

  it('同 seed 双跑：末行逐位相同（回写补丁是确定性的）', () => {
    const a = runOneBench(0, STAGES[0], 1, 'hard', 1200, WEIGHTS, 'off', false, 1, null)
    const b = runOneBench(0, STAGES[0], 1, 'hard', 1200, WEIGHTS, 'off', false, 1, null)
    expect(JSON.stringify(a.shard.metrics[a.shard.metrics.length - 1])).toBe(
      JSON.stringify(b.shard.metrics[b.shard.metrics.length - 1]),
    )
  })
})

describe('评估侧独立重算（god 同局事件流，不看导出器）', () => {
  it('四桶逐值相等 + 一发多格只落一次 + 恒等式严格', () => {
    for (const [stageIdx, seed] of [
      [0, 1],
      [3, 1],
    ] as const) {
      const local = runSimulation({
        seed,
        stage: STAGES[stageIdx] as never,
        stageIndex: stageIdx,
        difficulty: 'hard',
        policy: 'god',
        maxTicks: 3000,
        collectMetrics: false,
      })
      const evalR = runEvalOne(stageIdx, STAGES[stageIdx] as never, seed, 'hard', 3000, '{}', 'god')
      expect(evalR.ticks).toBe(local.ticks) // 先证明「同局」，对账才有意义

      // ---- 独立重算（只看事件流）----
      const playerIds = new Set<number>()
      for (const e of local.events) {
        if (e.type === 'bullet_fired' && e.bullet.isPlayer) playerIds.add(e.bullet.id)
      }
      const hitIds = new Set<number>()
      const brickIds = new Set<number>()
      let brickEvents = 0
      let ignited = 0
      for (const e of local.events) {
        if (e.type === 'enemy_hit') {
          if (e.bulletId !== undefined && playerIds.has(e.bulletId)) hitIds.add(e.bulletId)
        } else if (e.type === 'terrain_destroyed') {
          if (e.bulletId !== undefined && playerIds.has(e.bulletId)) {
            brickIds.add(e.bulletId)
            brickEvents++
          }
        } else if (e.type === 'bullet_cancelled') {
          const a = e.aId !== undefined && playerIds.has(e.aId)
          const b = e.bId !== undefined && playerIds.has(e.bId)
          if (a || b) ignited++ // 单玩家局 a/b 至多一边是玩家弹
        }
      }
      const hits = hitIds.size
      const bricks = brickIds.size
      const misses = playerIds.size - hits - bricks - ignited

      expect(evalR.playerShots).toBe(playerIds.size)
      expect(evalR.aimHits).toBe(hits)
      expect(evalR.aimBricks).toBe(bricks) // Set 去重 = settle-once（一发多格事件 > 桶数时严格）
      expect(evalR.aimIgnited).toBe(ignited)
      expect(evalR.aimMisses).toBe(misses)
      // 恒等式（§3.1）：局末在飞/出界/打钢未破/基地全折入 miss
      expect(evalR.aimHits + evalR.aimBricks + evalR.aimIgnited + evalR.aimMisses).toBe(
        evalR.playerShots,
      )
      // 去重证据：一弹多格破坏时事件数 ≥ 桶数（相等也行——本局可能没有多格破坏）
      expect(brickEvents).toBeGreaterThanOrEqual(bricks)
    }
  })
})
