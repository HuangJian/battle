/**
 * conv-wasm.test.ts —— wasm 内核（conv/prebuilt/wasm/conv.wasm）与 TS 参考实现的数值对拍
 * （DECISIONS §311）。wasm 由 clang -O3 -msimd128 编自 `src/nn/conv/conv.c`（与 native
 * **同一份源码**，2026-09-22 单源化）替换 StudentModel 卷积段前，必须保证 pooled 与
 * TS naive 实现 ≤1e-3（累加顺序级差异）。权重/输入合成随机（无需真实权重文件），固定 seed 可复现。
 *
 * 本用例直接按**原始 ABI** 驱动 wasm（单 blob + 4 参，与 native 侧同一个入口），
 * 不复用适配器 —— 它要验的正是内存布局与导出名本身；走生产入口的那条链由
 * tests/native-parity.test.ts（native↔wasm 逐字节）守护。
 */
import { describe, expect, test } from 'bun:test'
import { readFileSync } from 'fs'

const B = 26,
  SP = B * B,
  H = 64,
  D = 8

function mulberry(seed: number) {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
function randArr(rnd: () => number, n: number): Float32Array {
  const a = new Float32Array(n)
  for (let i = 0; i < n; i++) a[i] = (rnd() - 0.5) * 2
  return a
}

// ---- TS 参考（naive，语义同 infer.ts 旧实现） ----
function tsFeatures(
  in16: Float32Array,
  stemW: Float32Array,
  stemB: Float32Array,
  dwW: Float32Array[],
  dwB: Float32Array[],
  pwW: Float32Array[],
  pwB: Float32Array[],
): Float32Array {
  const conv3 = (
    inp: Float32Array,
    w: Float32Array,
    b: Float32Array,
    out: Float32Array,
    inCh: number,
    outCh: number,
  ) => {
    for (let oc = 0; oc < outCh; oc++) {
      const wb = oc * inCh * 9,
        ob = oc * SP,
        bias = b[oc]
      for (let oh = 0; oh < B; oh++)
        for (let ow = 0; ow < B; ow++) {
          let acc = bias
          for (let ic = 0; ic < inCh; ic++) {
            const ib = ic * SP,
              wi = wb + ic * 9
            for (let kh = -1; kh <= 1; kh++)
              for (let kw = -1; kw <= 1; kw++) {
                const ih = oh + kh,
                  iw = ow + kw
                if (ih >= 0 && ih < B && iw >= 0 && iw < B)
                  acc += w[wi + (kh + 1) * 3 + (kw + 1)] * inp[ib + ih * B + iw]
              }
          }
          out[ob + oh * B + ow] = acc < 0 ? 0 : acc
        }
    }
  }
  const dw5 = (inp: Float32Array, w: Float32Array, b: Float32Array, out: Float32Array) => {
    for (let oc = 0; oc < H; oc++) {
      const wb = oc * 25,
        ob = oc * SP,
        bias = b[oc]
      for (let oh = 0; oh < B; oh++)
        for (let ow = 0; ow < B; ow++) {
          let acc = bias
          for (let kh = -2; kh <= 2; kh++)
            for (let kw = -2; kw <= 2; kw++) {
              const ih = oh + kh,
                iw = ow + kw
              if (ih >= 0 && ih < B && iw >= 0 && iw < B)
                acc += w[wb + (kh + 2) * 5 + (kw + 2)] * inp[ob + ih * B + iw]
            }
          out[ob + oh * B + ow] = acc < 0 ? 0 : acc
        }
    }
  }
  const pw1 = (inp: Float32Array, w: Float32Array, b: Float32Array, out: Float32Array) => {
    for (let oc = 0; oc < H; oc++) {
      const wb = oc * H,
        ob = oc * SP,
        bias = b[oc]
      for (let p = 0; p < SP; p++) {
        let acc = bias
        for (let ic = 0; ic < H; ic++) acc += w[wb + ic] * inp[ic * SP + p]
        out[ob + p] = acc < 0 ? 0 : acc
      }
    }
  }
  const bufA = new Float32Array(H * SP),
    bufB = new Float32Array(H * SP),
    bufC = new Float32Array(H * SP)
  conv3(in16, stemW, stemB, bufA, 18, H)
  for (let i = 0; i < D; i++) {
    dw5(bufA, dwW[i], dwB[i], bufB)
    pw1(bufB, pwW[i], pwB[i], bufC)
    for (let j = 0; j < H * SP; j++) bufA[j] += bufC[j]
  }
  const pooled = new Float32Array(H)
  for (let c = 0; c < H; c++) {
    let s = 0
    for (let i = 0; i < SP; i++) s += bufA[c * SP + i]
    pooled[c] = s / SP
  }
  return pooled
}

describe('conv.wasm vs TS naive（数值对拍）', () => {
  test('随机权重 × 3 帧 pooled ≤1e-3', () => {
    const wasmBytes = readFileSync(
      new URL('../src/nn/conv/prebuilt/wasm/conv.wasm', import.meta.url),
    )
    const inst = new WebAssembly.Instance(new WebAssembly.Module(wasmBytes))
    const mem = inst.exports.memory as WebAssembly.Memory
    const STEM_W = 18 * 576,
      DW_W = D * H * 25,
      PW_W = D * H * H
    const BLOB = STEM_W + H + DW_W + D * H + PW_W + D * H
    // base 必须在模块自己的静态区之后（内核 scratch/bufB 在 BSS，不在数据段）
    const base = Math.max(1 << 20, mem.buffer.byteLength)
    const need = base + BLOB * 4 + 18 * SP * 4 + H * 4 + H * SP * 4
    const grow = Math.ceil((need - mem.buffer.byteLength) / 65536)
    if (grow > 0) mem.grow(grow)
    const f32At = (o: number) => new Float32Array(mem.buffer, o)
    const oStemW = base,
      oStemB = oStemW + STEM_W * 4,
      oDwW = oStemB + H * 4
    const oDwB = oDwW + DW_W * 4,
      oPwW = oDwB + D * H * 4,
      oPwB = oPwW + PW_W * 4
    const oIn = base + BLOB * 4,
      oPool = oIn + 18 * SP * 4,
      oBufA = oPool + H * 4
    expect((inst.exports['cf_abi'] as () => number)()).toBe(1)
    const feats = inst.exports['cf_student_features'] as (
      wblob: number,
      in16: number,
      pooled: number,
      bufA: number,
    ) => number

    for (let frame = 0; frame < 3; frame++) {
      const rnd = mulberry(0xc0ffee + frame * 97)
      const stemW = randArr(rnd, 10368),
        stemB = randArr(rnd, 64)
      const dwW: Float32Array[] = [],
        dwB: Float32Array[] = [],
        pwW: Float32Array[] = [],
        pwB: Float32Array[] = []
      for (let i = 0; i < D; i++) {
        dwW.push(randArr(rnd, H * 25))
        dwB.push(randArr(rnd, H))
      }
      for (let i = 0; i < D; i++) {
        pwW.push(randArr(rnd, H * H))
        pwB.push(randArr(rnd, H))
      }
      const in16 = randArr(rnd, 18 * SP)

      const ts = tsFeatures(in16, stemW, stemB, dwW, dwB, pwW, pwB)
      // 上传
      f32At(oStemW).set(stemW)
      f32At(oStemB).set(stemB)
      for (let i = 0; i < D; i++) {
        f32At(oDwW + i * H * 25 * 4).set(dwW[i])
        f32At(oDwB + i * H * 4).set(dwB[i])
      }
      for (let i = 0; i < D; i++) {
        f32At(oPwW + i * H * H * 4).set(pwW[i])
        f32At(oPwB + i * H * 4).set(pwB[i])
      }
      f32At(oIn).set(in16)
      expect(feats(oStemW, oIn, oPool, oBufA)).toBe(0)
      const wm = f32At(oPool).subarray(0, H)
      let maxD = 0
      let maxA = 0
      for (let i = 0; i < H; i++) {
        maxD = Math.max(maxD, Math.abs(wm[i] - ts[i]))
        maxA = Math.max(maxA, Math.abs(ts[i]))
      }
      // 随机权重（无归一）经 8 层深度放大后 pooled 可达 1e3+ → 用相对误差判
      expect(maxD / (maxA + 1e-9)).toBeLessThan(1e-3)
    }
  })
})
