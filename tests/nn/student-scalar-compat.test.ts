import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildModelFromText, type ModelLike } from '../../src/nn/infer'
import { SCALAR_DIM } from '../../src/nn/obs-encoder'

/**
 * ★ v5 权重 × v6 运行时的融合宽度契约（2026-10-19 修复，回归防护）。
 *
 * 背景：B′（`plan/nn-capability-upgrade.plan.md` §4-B2）把 `SCALAR_DIM` 40 → 46，
 * fixture 跟着改了行宽（`student-golden.json` 是 h=16 ⇒ fc 行宽 62 = 16+46），
 * 但**生产权重是训练产出的，改不了** —— `y25-price` 系列 `arch.scalar_dim=40`、
 * `fc.weight` shape `[128, 104]` = 128 × (64+40)。
 *
 * 而 `StudentModel.scalarDim` 是硬取 `SCALAR_DIM` 的类字段 ⇒ forward 时按
 * `fusedLen = h + 46 = 110` 步进读一张 104 宽的表 ⇒ **从第 0 行起就错位**，
 * 越界部分读到 `undefined` ⇒ NaN ⇒ 被 `RELU = x > 0 ? x : 0`（`infer.ts:57`）
 * **静默吞成 0**：不崩、不 NaN、输出有限但完全错误（实测 hidden 最大偏差 7481）。
 *
 * 修复：`scalarDim` 从 `fc.weight` 的**真实行宽**反推（权重自带行宽 = h + scalar_dim），
 * 且与 `arch.scalar_dim`（若存在）交叉校验，不符即响亮拒绝。
 *
 * 断言手法（不需要参照实现，最干净）：
 *   行宽 = h+40 的权重 ⇒ 只能看到 `scalars[0..39]`，**改后 6 维输出必须逐字节不变**；
 *   行宽 = h+46 的权重 ⇒ 46 维全在看，**改后 6 维输出必须变化**（防止"一律截断到 40"的反向 bug）。
 */
interface StudentGolden {
  format: string
  version: number
  h: number
  d: number
  head_hidden: number
  seed: number
  obs: number[]
  scalars: number[]
  moveLogits: number[]
  fireLogits: number[]
  valueLogits: number[]
  params: Record<string, { shape: number[]; data: string }>
}

const SLIM = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
) as StudentGolden

/** 把 `fc.weight` 重排行宽（保留左半部分 = 前 `newWidth` 列），模拟不同 schema 的权重。 */
function reflowFcWidth(g: StudentGolden, newWidth: number): StudentGolden {
  const fc = g.params['fc.weight']
  const bin = Buffer.from(fc.data, 'base64')
  const src = new Float32Array(bin.buffer, bin.byteOffset, bin.byteLength >> 2)
  const rows = fc.shape[0]
  const cols = fc.shape[1]
  const out = new Float32Array(rows * newWidth)
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < newWidth; c++) out[r * newWidth + c] = c < cols ? src[r * cols + c] : 0
  }
  return {
    ...g,
    params: {
      ...g.params,
      'fc.weight': {
        shape: [rows, newWidth],
        data: Buffer.from(out.buffer, out.byteOffset, out.byteLength).toString('base64'),
      },
    },
  }
}

function build(g: StudentGolden): ModelLike & { valueOut: Float32Array } {
  return buildModelFromText(
    JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params }),
  ) as ModelLike & { valueOut: Float32Array }
}

function forwardWith(
  g: StudentGolden,
  scalars: Float32Array,
): { move: Float32Array; fire: Float32Array; value: Float32Array } {
  const m = build(g)
  m.forward(new Uint8Array(g.obs), scalars)
  return {
    move: Float32Array.from(m.moveLogits),
    fire: Float32Array.from(m.fireLogits),
    value: Float32Array.from(m.valueOut),
  }
}

function maxAbsDiff(a: Float32Array, b: Float32Array): number {
  let m = 0
  for (let i = 0; i < a.length; i++) m = Math.max(m, Math.abs(a[i] - b[i]))
  return m
}

/** 把 `scalars` 的第 40..45 位（B′ 新增的 6 维）换成完全不同的值。 */
function perturbTail(scalars: number[]): Float32Array {
  const s = new Float32Array(scalars)
  for (let i = 40; i < SCALAR_DIM; i++) s[i] = (s[i] + 0.5) % 1
  return s
}

describe('v5 权重 × v6 运行时：融合宽度必须按权重真实行宽走', () => {
  const V5 = reflowFcWidth(SLIM, SLIM.h + 40) // 行宽 16+40 = 56
  const V6 = SLIM // 行宽 16+46 = 62

  it('行宽 = h+40 的权重 ⇒ scalarDim 反推为 40，不是硬取的 SCALAR_DIM', () => {
    // 修复前：scalarDim === 46 ⇒ 这条红
    expect(build(V5).scalarDim).toBe(40)
  })

  it('行宽 = h+SCALAR_DIM 的权重 ⇒ scalarDim 反推为 46（回归保护，别一律截断）', () => {
    expect(build(V6).scalarDim).toBe(SCALAR_DIM)
  })

  it('★ v5 权重：改 scalars[40..45] 输出必须逐字节不变（只读前 40 维）', () => {
    const base = forwardWith(V5, new Float32Array(SLIM.scalars))
    const pert = forwardWith(V5, perturbTail(SLIM.scalars))
    expect(maxAbsDiff(base.move, pert.move)).toBe(0)
    expect(maxAbsDiff(base.fire, pert.fire)).toBe(0)
    expect(maxAbsDiff(base.value, pert.value)).toBe(0)
  })

  it('v6 权重：改 scalars[40..45] 输出必须变化（46 维全在用）', () => {
    const base = forwardWith(V6, new Float32Array(SLIM.scalars))
    const pert = forwardWith(V6, perturbTail(SLIM.scalars))
    const d =
      maxAbsDiff(base.move, pert.move) +
      maxAbsDiff(base.fire, pert.fire) +
      maxAbsDiff(base.value, pert.value)
    expect(d).toBeGreaterThan(1e-6)
  })

  it('行宽除不尽 head_hidden ⇒ 响亮拒绝（不静默截断）', () => {
    const fc = SLIM.params['fc.weight']
    const bin = Buffer.from(fc.data, 'base64')
    // 砍掉最后一个 float ⇒ 长度不再是 head_hidden(128) 的整数倍 ⇒ 行宽非整数
    const cut = bin.subarray(0, bin.byteLength - 4)
    const broken: StudentGolden = {
      ...SLIM,
      params: { ...SLIM.params, 'fc.weight': { shape: fc.shape, data: cut.toString('base64') } },
    }
    expect(() => build(broken)).toThrow(/行宽/)
  })

  it('arch.scalar_dim 与 fc 行宽不符 ⇒ 响亮拒绝（双源不一致不许静默取其一）', () => {
    expect(() =>
      buildModelFromText(
        JSON.stringify({
          arch: { kind: 'student', h: SLIM.h, d: SLIM.d, scalar_dim: 99 },
          params: SLIM.params,
        }),
      ),
    ).toThrow(/scalar_dim/)
  })
})
