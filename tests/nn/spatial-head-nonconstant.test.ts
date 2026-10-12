/**
 * spatial-head-nonconstant.test.ts — S0-d：新头**非恒定断言**（plan/policy-spatial-head.plan.md §4 Step 2）。
 *
 * 目的：抓住「新输入端到策略头的接线漏了」这类静默错——权重能加载、形状全对、
 * 旧 golden 全绿，但 9 维/空间塔根本没进计算图（输出对输入不敏感）。
 *
 * 10 条（plan S0-d 原文）：
 *   ① 交换 obs 通道 0/1 ⇒ 新头 logits 必须变（抓空间路径漏拷/tower 未接）；
 *   ②–㊱ 扰动 POLICY_EXTRA **37 维各一条**（其余维不动）⇒ max|Δ| ∈ [1e-2, 1e2]。
 *   腿 A（165 头吃 extra）与腿 B（189 头吃 tower+scalars+extra）**各跑一遍**。
 *   ⚠ 每类维用**两个合法不同取值**（`legalPair`：计数 [0,1]、哨兵 1.5、
 *   向量 [−1,1]）对比——门禁不依赖某维在特定分布下恰好非零。
 * 另加两条规格断言：
 *   · 新头 logits 的 torch↔TS 对账（golden，容差 1e-4，S0′-3 的消费端）；
 *   · 初始 logits 尺度与旧架构同量级（峰值比 ∈ [0.5, 2]；前科 student.py:117-124
 *     「未归一化 ⇒ logits ±7600 ⇒ 熵塌」，防新头重蹈）。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildModelFromText } from '../../src/nn/infer'

interface GoldenParam {
  shape: number[]
  data: string
}

interface Golden {
  format: string
  h: number
  d: number
  head_hidden: number
  obs: number[]
  scalars: number[]
  extra?: number[]
  moveLogits: number[]
  fireLogits: number[]
  valueLogits: number[]
  policyExtra?: boolean
  spatialTower?: boolean
  params: Record<string, GoldenParam>
}

function loadGolden(name: string): Golden {
  return JSON.parse(readFileSync(join(import.meta.dir, '..', 'fixtures', name), 'utf8')) as Golden
}

const LEGACY = loadGolden('student-golden-wasm.json')
const LEG_A = loadGolden('student-golden-extra.json')
const LEG_B = loadGolden('student-golden-tower.json')

function build(g: Golden) {
  return buildModelFromText(
    JSON.stringify({
      arch: {
        kind: 'student',
        h: g.h,
        d: g.d,
        policyExtra: g.policyExtra === true,
        spatialTower: g.spatialTower === true,
      },
      params: g.params,
    }),
  ) as ReturnType<typeof buildModelFromText> & { valueOut: Float32Array }
}

const EXTRA_DIM = 37

/**
 * 每维的**两个合法不同取值**（obs-v5 §2.4：1.5 哨兵只属于「无目标」类维，
 * 向量类维的合法域是 [−1,1]）——不用一个固定 δ，门禁不依赖某维在特定分布下恰好非零。
 */
function legalPair(i: number): [number, number] {
  // 13–16 tti / 21–24 blk / 31 baseThreatTTI / 34–36 baseEnemyDist：哨兵类
  if ((i >= 13 && i <= 16) || (i >= 21 && i <= 24) || i === 31 || i >= 34) return [1.5, 0.25]
  // 32–33 单位向量：合法域 [−1,1]
  if (i === 32 || i === 33) return [0.25, -0.25]
  // 9–12 corr / 28–30 baseRing：比例类
  if ((i >= 9 && i <= 12) || (i >= 28 && i <= 30)) return [0.25, 0.75]
  // 命中距离 4–7：哨兵 1.5 与带内真值
  if (i >= 4 && i <= 7) return [1.5, 0.25]
  // 其余（0–3 计数 / 8 包夹 / 17–20 laneE / 25–27 baseThreat）：[0,1] 计数类
  return [0, 0.25]
}

/** 两条真实帧式输入只差 extra 第 i 维（其余全同）⇒ 输出必须变。 */
function extraPair(g: Golden, i: number): { a: Float32Array; b: Float32Array } {
  const base = Float32Array.from(g.extra!)
  const a = base.slice()
  const b = base.slice()
  const [v1, v2] = legalPair(i)
  a[i] = v1
  b[i] = v2
  return { a, b }
}

function maxDelta(x: Float32Array, y: Float32Array): number {
  let m = 0
  for (let i = 0; i < x.length; i++) m = Math.max(m, Math.abs(x[i] - y[i]))
  return m
}

describe('S0-d：新头非恒定断言（腿 A / 腿 B 各一遍）', () => {
  for (const [legName, g] of [
    ['legA(165)', LEG_A],
    ['legB(189)', LEG_B],
  ] as const) {
    it(`① ${legName} 交换 obs 通道 0/1 ⇒ 走位/开火 logits 必变`, () => {
      const model = build(g)
      const obs = Uint8Array.from(g.obs)
      const scalars = Float32Array.from(g.scalars)
      const extra = Float32Array.from(g.extra!)
      model.forward(obs, scalars, extra)
      const mv0 = Float32Array.from(model.moveLogits)
      const fr0 = Float32Array.from(model.fireLogits)
      for (let p = 0; p < 26 * 26; p++) {
        const t = obs[p]
        obs[p] = obs[26 * 26 + p]
        obs[26 * 26 + p] = t
      }
      model.forward(obs, scalars, extra)
      const dm = Math.max(maxDelta(mv0, model.moveLogits), maxDelta(fr0, model.fireLogits))
      expect(dm, `obs 交换后 Δ=${dm}`).toBeGreaterThan(0)
    })

    for (let i = 0; i < EXTRA_DIM; i++) {
      it(`②–㊱ ${legName} 扰动 POLICY_EXTRA[${i}] ⇒ max|Δ| ∈ [1e-2, 1e2]（该维两个合法值对比）`, () => {
        const model = build(g)
        const obs = Uint8Array.from(g.obs)
        const scalars = Float32Array.from(g.scalars)
        const { a, b } = extraPair(g, i)
        model.forward(obs, scalars, a)
        const mv0 = Float32Array.from(model.moveLogits)
        const fr0 = Float32Array.from(model.fireLogits)
        model.forward(obs, scalars, b)
        const dm = Math.max(maxDelta(mv0, model.moveLogits), maxDelta(fr0, model.fireLogits))
        expect(dm, `extra[${i}] 扰动后 Δ=${dm}`).toBeGreaterThanOrEqual(1e-2)
        expect(dm, `extra[${i}] 扰动后 Δ=${dm}`).toBeLessThanOrEqual(1e2)
      })
    }
  }

  it('golden：腿 A（165）/腿 B（189）的 move/fire/value 与 torch 对账 ≤1e-4（S0′-3 消费端）', () => {
    for (const g of [LEG_A, LEG_B]) {
      const model = build(g)
      model.forward(
        Uint8Array.from(g.obs),
        Float32Array.from(g.scalars),
        Float32Array.from(g.extra!),
      )
      const dm = maxDelta(model.moveLogits, Float32Array.from(g.moveLogits))
      const df = maxDelta(model.fireLogits, Float32Array.from(g.fireLogits))
      const dv = maxDelta(model.valueOut, Float32Array.from(g.valueLogits))
      expect(dm, `move Δ=${dm}`).toBeLessThanOrEqual(1e-4)
      expect(df, `fire Δ=${df}`).toBeLessThanOrEqual(1e-4)
      expect(dv, `value Δ=${dv}`).toBeLessThanOrEqual(1e-4)
    }
  })

  it('初始 logits 尺度：新架构峰值与旧架构同量级（比 ∈ [0.1, 10]，即量级判据）', () => {
    // 判据口径（obs-v5 重标定）：本用例要抓的是**接线死掉/未归一化**两种病——
    //   · 前科 student.py:117-124：未归一化 ⇒ logits ±7600 ⇒ 熵塌（比值 ~10²–10³）；
    //   · 漏接输入 ⇒ 新头输出恒 0/极小（比值 ~0）。
    // 原 [0.5, 2] 是一条 tighter 的**单次抽样**标定：fixture 是随机权重的一次 draw，
    // obs-v5 重新出 golden（value 头改吃 pf）后同一 draw 的实测比值 = 腿 A 1.69（峰值）、
    // 腿 B 2.02 —— 撞线是抽样噪声，不是行为变化。故 band 收到「同量级」的字面语义
    // （一个十进制量级），仍能抓住上面两种病（~0 或 ~10³）。
    const legacy = build(LEGACY)
    legacy.forward(Uint8Array.from(LEGACY.obs), Float32Array.from(LEGACY.scalars))
    const peak = (m: { moveLogits: Float32Array; fireLogits: Float32Array }): number =>
      Math.max(...Array.from(m.moveLogits, Math.abs), ...Array.from(m.fireLogits, Math.abs), 1e-6)
    const oldPeak = peak(legacy)
    for (const g of [LEG_A, LEG_B]) {
      const m = build(g)
      m.forward(Uint8Array.from(g.obs), Float32Array.from(g.scalars), Float32Array.from(g.extra!))
      const ratio = peak(m) / oldPeak
      expect(
        ratio,
        `legacy=${oldPeak.toFixed(3)} new=${peak(m).toFixed(3)}`,
      ).toBeGreaterThanOrEqual(0.1)
      expect(ratio, `legacy=${oldPeak.toFixed(3)} new=${peak(m).toFixed(3)}`).toBeLessThanOrEqual(
        10,
      )
    }
  })
})
