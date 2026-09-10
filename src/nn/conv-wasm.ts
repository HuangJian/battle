/**
 * conv-wasm.ts —— StudentModel.features 的 wasm32 SIMD 后端（DECISIONS §311 提速①）。
 *
 * conv_feats.wasm 由 clang --target=wasm32 -O3 -msimd128 编译（外积排布自动向量化），
 * probe 实测 features ~6.9ms vs TS ~41ms（×6），pooled max|Δ|≈4.8e-6（累加顺序级）。
 *
 * 契约：仅适用于 h=64 / d=8 / board=26 的 per-tick/intent/goal student 特征（共享
 * StudentModel.features 骨架，卷积段布局一致）。不匹配 → 返回 false 走 TS 原路径。
 *
 * 内存布局（单例，线性内存自 1MB 起，避开模块 .bss scratch）：权重区一次性上传
 * （按实例引用变化重传），每 forward 拷 in16（43KB）+ 读回 pooled（256B）+ bufA
 * （169KB，目标热图头消费）。bufA 回拷实测 +2.9 µs/次 ≈ features 的 0.04%——无条件
 * 拷贝，以杜绝"某个头悄悄读到陈旧空间特征"这类静默 bug（2026-09-10 的 goalForward
 * 常量热图即此因，见 run() 注释）。
 */

import { readFileSync } from 'fs'

interface WasmRunner {
  /** 上传权重（实例变化时）并跑 features → pooled 与 bufA 均已填。返回 true=本次成功。
   *
   *  bufA = GAP 前的空间特征（h×26×26），**目标热图头唯一消费的空间张量**（infer.ts
   *  goalForward：out[p] = bias + Σ_ic gw[ic]·bufA[ic·sp + p]）。wasm 侧 features()
   *  把同一张量写在 offBufA，故必须与 pooled 一起回拷——2026-09-10 前只回拷 pooled，
   *  导致生产档（h64/d8 必走 wasm）下 bufA 恒为陈旧值、热图退化成常量、argmax 恒选
   *  同一格（实测对 py golden 误差 12.45；h16/d2 的 TS 路径正常故 golden 测试未暴露）。 */
  run(
    in16: Float32Array,
    stemW: Float32Array,
    stemB: Float32Array,
    dwW: Float32Array[],
    dwB: Float32Array[],
    pwW: Float32Array[],
    pwB: Float32Array[],
    pooled: Float32Array,
    bufA: Float32Array,
  ): boolean
}

const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8

let _runner: WasmRunner | null | undefined = undefined // undefined=未探测

function loadRunner(): WasmRunner | null {
  try {
    const bytes = readFileSync(new URL('./wasm/conv_feats.wasm', import.meta.url))
    const mod = new WebAssembly.Module(bytes)
    const inst = new WebAssembly.Instance(mod)
    const mem = inst.exports.memory as WebAssembly.Memory
    const need =
      (1 << 20) +
      (9216 + 8 * (1600 + 64 + 4096 + 64)) * 4 + // 权重（stem/dw/pw + biases）
      16 * SP * 4 + // in16
      3 * H * SP * 4 + // bufA/B/C
      H * 4 // pooled
    const grow = Math.ceil((need - mem.buffer.byteLength) / 65536)
    if (grow > 0) mem.grow(grow)
    const base = 1 << 20
    const feats = inst.exports['features'] as (
      a: number,
      b: number,
      c: number,
      d: number,
      e: number,
      f: number,
      g: number,
      h: number,
      i: number,
      j: number,
      k: number,
    ) => void
    const f32At = (byteOff: number): Float32Array => new Float32Array(mem.buffer, byteOff)

    // 固定字节偏移布局
    const offStemW = base
    const offStemB = offStemW + 9216 * 4
    const offDwW = offStemB + 64 * 4
    const offDwB = offDwW + D * H * 25 * 4
    const offPwW = offDwB + D * H * 4
    const offPwB = offPwW + D * H * H * 4
    const offIn = offPwB + D * H * 4
    const offBufA = offIn + 16 * SP * 4
    const offBufB = offBufA + H * SP * 4
    const offBufC = offBufB + H * SP * 4
    const offPooled = offBufC + H * SP * 4

    let uploaded: unknown = null

    const runner: WasmRunner = {
      run(in16, stemW, stemB, dwW, dwB, pwW, pwB, pooled, bufA) {
        const memNow = mem.buffer.byteLength
        const check = (name: string, off: number, len: number): void => {
          if (off + len * 4 > memNow)
            throw new Error(`[conv-wasm] ${name} 越界: off=${off} len=${len} mem=${memNow}`)
        }
        if (uploaded !== stemW) {
          // 首次或换实例：整批上传权重
          check('stemW', offStemW, stemW.length)
          f32At(offStemW).set(stemW)
          check('stemB', offStemB, stemB.length)
          f32At(offStemB).set(stemB)
          for (let i = 0; i < D; i++) {
            check(`dwW${i}`, offDwW + i * H * 25 * 4, dwW[i].length)
            f32At(offDwW + i * H * 25 * 4).set(dwW[i])
            check(`dwB${i}`, offDwB + i * H * 4, dwB[i].length)
            f32At(offDwB + i * H * 4).set(dwB[i])
          }
          for (let i = 0; i < D; i++) {
            check(`pwW${i}`, offPwW + i * H * H * 4, pwW[i].length)
            f32At(offPwW + i * H * H * 4).set(pwW[i])
            check(`pwB${i}`, offPwB + i * H * 4, pwB[i].length)
            f32At(offPwB + i * H * 4).set(pwB[i])
          }
          uploaded = stemW
        }
        check('in16', offIn, in16.length)
        f32At(offIn).set(in16)
        check('pooled', offPooled, pooled.length)
        check('bufA', offBufA, bufA.length)
        feats(
          offIn,
          offStemW,
          offStemB,
          offDwW,
          offDwB,
          offPwW,
          offPwB,
          offBufA,
          offBufB,
          offBufC,
          offPooled,
        )
        // 目标 pooled(64) 短于 buffer 尾部 view —— 必须 subarray 限长，否则 set 抛 Range
        pooled.set(f32At(offPooled).subarray(0, pooled.length))
        // bufA 同理限长。缺这一行 ⇒ 目标热图在生产档（h64/d8 必走 wasm）退化为常量：
        // 热图头只消费 bufA，而 bufA 只在非 wasm 的 TS 分支被 conv3x3 填（见 infer.ts
        // features/goalForward）。回归锚点：tests/nn/goal-infer.test.ts 的
        // goal-golden-wasm.json（h=64/d=8）组。
        bufA.set(f32At(offBufA).subarray(0, bufA.length))
        return true
      },
    }
    return runner
  } catch (e) {
    console.error(
      `[conv-wasm] 加载失败，回退 TS 特征路径: ${e instanceof Error ? e.message : String(e)}`,
    )
    return null
  }
}

/** 返回单例 runner（不匹配 h64/d8 时 null）。 */
export function studentConvWasm(): WasmRunner | null {
  if (_runner === undefined) _runner = loadRunner()
  return _runner
}

/** StudentModel.features 接入点：h64/d8/board26 时优先 wasm；返回 true=pooled+bufA 已填。 */
export function runStudentConvWasm(model: unknown): boolean {
  const m = model as {
    in16: Float32Array
    stemW: Float32Array
    stemB: Float32Array
    dwW: Float32Array[]
    dwB: Float32Array[]
    pwW: Float32Array[]
    pwB: Float32Array[]
    pooled: Float32Array
    bufA: Float32Array
  }
  if (m.dwW.length !== D) return false // 非 d=8（架构不符）
  // bufA 尺寸是 h=64/board=26 的充分标识（StudentModel: bufA = Float32Array(h*sp)），
  // 与本文件的固定通道布局一一对应；不符即退 TS 原路径（正确性优先于速度）。
  if (!m.bufA || m.bufA.length !== H * SP) return false
  const r = studentConvWasm()
  if (!r) return false
  try {
    return r.run(m.in16, m.stemW, m.stemB, m.dwW, m.dwB, m.pwW, m.pwB, m.pooled, m.bufA)
  } catch (e) {
    console.error(
      `[conv-wasm] run 异常: ${e instanceof Error ? e.message : String(e)}\n${e instanceof Error ? e.stack : ''}`,
    )
    return false // 运行时异常 → TS 原路径兜底
  }
}
