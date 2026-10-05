/**
 * spatial-tower.ts — C=8 空间塔（plan/policy-spatial-head.plan.md §3.3）的**数学冻结实现**。
 *
 * bufA (h=64, 26×26) → 1×1 卷积 64→8（+bias）→ 逐格 ReLU → 4×4 分区平均 → FC 128→112。
 *
 * 三条冻结约束（改一个 = 重审 plan §3）：
 *   ① **ReLU 必须存在**：`mean(W·x) = W·mean(x)`——没有它整座塔退化为「区块均值 +
 *      共享线性变换」，块内计数型信息表达不出来（塔的全部表达力增益在这里）。
 *   ② **分区边界 = PyTorch `adaptive_avg_pool2d(4)` 等价式**（§3.5，左闭右开）：
 *      `第 i 块 = [floor(i×26/4), ceil((i+1)×26/4))`；相邻块**有重叠**（只有 index 6/19
 *      两格被重复计算；13 不重复）。两侧（torch/TS）必须逐字一致。
 *   ③ **布局**：分区均值向量按 `c*16 + (row*4+col)` 排（与 torch `(B,8,4,4).flatten(1)`
 *      逐字同序）；FC 128→112 写在本模块之外（`src/nn/infer.ts` 消费本函数的 128 维输出）。
 *
 * 数值口径（S0′-3 冻结）：torch 用 `Conv2d + ReLU + adaptive_avg_pool2d`，TS 用循环 + ReLU；
 * 数学等价、浮点舍入差 ≈1e-6，落在既有 golden 容差 1e-4 内。**禁止**后人"优化"成
 * 先池化后投影（数学不等价、且丢掉 ①）。
 *
 * 热路径：零分配（`zBuf` 由调用方持有复用；无中间数组）。
 */

import { BOARD } from './obs-encoder'

/** 塔输出通道数（冻结值；要试 C=16 属另起探针/新实验，不在 plan 内改数）。 */
export const SPATIAL_TOWER_C = 8
/** 分区数（每轴；4×4=16 区）。 */
export const SPATIAL_TOWER_POOL = 4
/** FC 输出维（塔的最终特征宽度；plan §3.3。 */
export const SPATIAL_TOWER_FC_OUT = 112
/** bufA 通道数（主干末层输出，h=64）。 */
export const SPATIAL_TOWER_IN_CH = 64

/**
 * 分区边界（§3.5 冻结）：`[floor(i×B/POOL), ceil((i+1)×B/POOL))`，左闭右开。
 * 由 BOARD 派生以保证与 `adaptive_avg_pool2d` 逐字一致（26 ⇒ [0,7)/[6,13)/[13,20)/[19,26)）。
 */
export const SPATIAL_POOL_BOUNDS: ReadonlyArray<readonly [number, number]> = Array.from(
  { length: SPATIAL_TOWER_POOL },
  (_, i) => {
    const lo = Math.floor((i * BOARD) / SPATIAL_TOWER_POOL)
    const hi = Math.ceil(((i + 1) * BOARD) / SPATIAL_TOWER_POOL)
    return [lo, hi] as const
  },
)

/** 塔的中间特征宽（C×16）。 */
export const SPATIAL_TOWER_FEAT = SPATIAL_TOWER_C * SPATIAL_TOWER_POOL * SPATIAL_TOWER_POOL

/** 校验参数形状（构造期一次；勿进热路径）。 */
export function assertSpatialTowerShapes(
  projW: Float32Array,
  projB: Float32Array,
  fcW: Float32Array,
  fcB: Float32Array,
): void {
  const h = SPATIAL_TOWER_IN_CH
  const c = SPATIAL_TOWER_C
  const need: Array<[string, number, number]> = [
    ['projW', projW.length, c * h],
    ['projB', projB.length, c],
    ['fcW', fcW.length, SPATIAL_TOWER_FC_OUT * SPATIAL_TOWER_FEAT],
    ['fcB', fcB.length, SPATIAL_TOWER_FC_OUT],
  ]
  for (const [name, got, want] of need) {
    if (got !== want) throw new Error(`spatial-tower: ${name} 长度 ${got} != ${want}`)
  }
}

/**
 * 塔前向：bufA(64×26×26) → FC(128→112) 特征（写入 `out`，长度 112）。
 *
 * @param zBuf   中间缓冲（长度 C×26×26，调用方持有复用）
 * @param vecBuf 分区均值向量缓冲（长度 128，调用方持有复用）
 */
export function spatialTowerForward(
  bufA: Float32Array,
  projW: Float32Array,
  projB: Float32Array,
  fcW: Float32Array,
  fcB: Float32Array,
  zBuf: Float32Array,
  vecBuf: Float32Array,
  out: Float32Array,
): void {
  const c = SPATIAL_TOWER_C
  const h = SPATIAL_TOWER_IN_CH
  const sp = BOARD * BOARD
  // ① 1×1 卷积 64→8 + ② 逐格 ReLU
  // 循环序（S0′-4 实测 345µs → 目标 <200µs）：**ic 外层、p 内层**——bufA 每个通道
  // 顺序读一次（原 oc 外层会把 173KB 的 bufA 反复读 8 遍，纯烧内存带宽）；
  // zBuf 8×2.7KB 常驻 L1。数学逐位同序（每个输出像素的 ic 累加次序不变）。
  for (let oc = 0; oc < c; oc++) {
    const oBase = oc * sp
    const bias = projB[oc]
    for (let p = 0; p < sp; p++) zBuf[oBase + p] = bias
  }
  for (let ic = 0; ic < h; ic++) {
    const iBase = ic * sp
    for (let oc = 0; oc < c; oc++) {
      const w = projW[oc * h + ic]
      if (w === 0) continue
      const oBase = oc * sp
      // p 循环 4 路展开（S0′-4：削减循环/边界检查开销；累加次序逐像素不变 ⇒ 逐位同序）。
      let p = 0
      for (; p + 3 < sp; p += 4) {
        zBuf[oBase + p] += w * bufA[iBase + p]
        zBuf[oBase + p + 1] += w * bufA[iBase + p + 1]
        zBuf[oBase + p + 2] += w * bufA[iBase + p + 2]
        zBuf[oBase + p + 3] += w * bufA[iBase + p + 3]
      }
      for (; p < sp; p++) zBuf[oBase + p] += w * bufA[iBase + p]
    }
  }
  for (let i = 0; i < c * sp; i++) if (zBuf[i] < 0) zBuf[i] = 0
  // ③ 4×4 分区平均（边界 §3.5；布局 c*16 + row*4+col，与 torch flatten(1) 同序）
  const feat = SPATIAL_TOWER_FEAT
  const vec = vecBuf
  for (let oc = 0; oc < c; oc++) {
    const oBase = oc * sp
    for (let rr = 0; rr < SPATIAL_TOWER_POOL; rr++) {
      const [rLo, rHi] = SPATIAL_POOL_BOUNDS[rr]
      for (let cc = 0; cc < SPATIAL_TOWER_POOL; cc++) {
        const [cLo, cHi] = SPATIAL_POOL_BOUNDS[cc]
        let sum = 0
        for (let r = rLo; r < rHi; r++) {
          const rowBase = oBase + r * BOARD
          for (let col = cLo; col < cHi; col++) sum += zBuf[rowBase + col]
        }
        const cells = (rHi - rLo) * (cHi - cLo)
        vec[oc * 16 + rr * 4 + cc] = sum / cells
      }
    }
  }
  // ④ FC 128→112（PyTorch Linear 布局 [out,in]）
  for (let o = 0; o < SPATIAL_TOWER_FC_OUT; o++) {
    let acc = fcB[o]
    const wb = o * feat
    for (let i = 0; i < feat; i++) acc += fcW[wb + i] * vec[i]
    out[o] = acc
  }
}
