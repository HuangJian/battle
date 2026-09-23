/**
 * conv_wasm_adapter.ts —— StudentModel.features 的 wasm32 SIMD 后端（DECISIONS §311 提速①）。
 *
 * conv.wasm 由**与 native 同一份** `conv.c` 编译（clang --target=wasm32 -msimd128 -O3
 * -ffp-contract=off …，命令与 flags 见 native-prebuilt.ts::wasmBuildFlags 与
 * tools/agent/native-build.ts --wasm；产物入库在 prebuilt/wasm/conv.wasm）。
 *
 * 单源化（2026-09-22）之后两侧的差异只剩一个常量：`CF_PW_PX`（wasm 8 / 其余 16，
 * 只决定「哪些像素进同一条向量寄存器」，**不改变任何元素的累加次序**）。因此这里调用的是
 * native 侧同一个入口 `cf_student_features(wblob, in16, pooled, bufA)`：
 * 权重是**单 blob**（顺序 == conv_native.h::CF_BLOB_FLOATS == native 侧 ensureBlob），
 * 不再有六区分别上传 + 11 参调用，bufB/bufC 由内核自己的静态区承担。
 * 两侧输出逐位一致由 tests/native-parity.test.ts 钉死（native↔wasm 逐字节）。
 *
 * 契约：仅适用于 h=64 / d=8 / board=26 / IN_CH=18 的 per-tick/intent/goal student 特征
 * （共享 StudentModel.features 骨架）。不匹配 / 缺 wasm / ABI 不符 → 返回 false 走 TS 原路径。
 *
 * 内存布局（单例；线性内存自 base 起，base = max(1MB, 模块初始内存) —— 内核的静态
 * scratch/bufB 落在模块自己的 BSS 里，避开它才不会互相踩）：
 *     [wblob CF_BLOB_FLOATS][in16 18×676][pooled 64][bufA 64×676]
 * 每 forward 拷 in16（48KB）+ 读回 pooled（256B）+ bufA（169KB）。bufA 回拷实测
 * +2.9 µs/次 ≈ features 的 0.04%——无条件拷贝，以杜绝“某个头悄悄读到陈旧空间特征”
 * 这类静默 bug（2026-09-10 的 goalForward 常量热图即此因）。
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
// v3（obs-schema-v3.plan.md v4.0）：stem 输入通道 = 16 obs + 2 coord（hy E4 链）
const IN_CH = 18
const SP = BOARD * BOARD
const H = 64
const D = 8
const STEM_W = IN_CH * 576
const DW_W = D * H * 25
const PW_W = D * H * H
/** 权重 blob 的 float 数（== conv_native.h::CF_BLOB_FLOATS）。 */
const BLOB_FLOATS = STEM_W + H + DW_W + D * H + PW_W + D * H
const ABI = 1

let _runner: WasmRunner | null | undefined = undefined // undefined=未探测

function loadRunner(): WasmRunner | null {
  try {
    const bytes = readFileSync(new URL('./prebuilt/wasm/conv.wasm', import.meta.url))
    const mod = new WebAssembly.Module(bytes)
    const inst = new WebAssembly.Instance(mod)
    const mem = inst.exports.memory as WebAssembly.Memory | undefined
    if (!mem) {
      console.error('[conv-wasm] 模块未导出 memory（构建需带 -Wl,--export-memory）→ 回退 TS')
      return null
    }
    const abi = inst.exports['cf_abi'] as (() => number) | undefined
    const feats = inst.exports['cf_student_features'] as
      | ((wblob: number, in16: number, pooled: number, bufA: number) => number)
      | undefined
    if (!feats) {
      console.error('[conv-wasm] 模块缺 cf_student_features 导出 → 回退 TS')
      return null
    }
    const got = abi?.()
    if (got !== ABI) {
      console.error(`[conv-wasm] ABI 不符（wasm ${String(got)} ≠ 期望 ${ABI}）→ 回退 TS`)
      return null
    }
    // base 必须在模块自己的静态区（内核 scratch/bufB，落在 BSS）之后：BSS 不进数据段，
    // 只能问模块实例化后的初始内存。1MB 是历史下界（旧版内核 + 旧 JS 布局），保留它
    // 是为了让内存布局与旧产物在数值上完全可比。
    const base = Math.max(1 << 20, mem.buffer.byteLength)
    const need = base + BLOB_FLOATS * 4 + IN_CH * SP * 4 + H * 4 + H * SP * 4
    const grow = Math.ceil((need - mem.buffer.byteLength) / 65536)
    if (grow > 0) mem.grow(grow)
    const f32At = (byteOff: number): Float32Array => new Float32Array(mem.buffer, byteOff)

    // 固定字节偏移布局：单个连续 blob（与 native 侧 ensureBlob 拼的顺序逐字段一致）
    const offBlob = base
    const offStemW = offBlob
    const offStemB = offStemW + STEM_W * 4
    const offDwW = offStemB + H * 4
    const offDwB = offDwW + DW_W * 4
    const offPwW = offDwB + D * H * 4
    const offPwB = offPwW + PW_W * 4
    const offIn = offBlob + BLOB_FLOATS * 4
    const offPooled = offIn + IN_CH * SP * 4
    const offBufA = offPooled + H * 4

    // 上传缓存键 = stemW 的**引用身份**（与 native 侧 `k.blobOwner === m.stemW` 同族）：
    // 成立前提是权重视图不可变（`buildModelFromText` 一次性构建，全仓无原地换权重的路径）。
    // 若将来出现原地换权重，这里会静默继续用旧权重 —— 换键或显式换视图，别只改一侧
    // （详见 conv_native_adapter.ts::ensureBlob 的同一段说明）。
    let uploaded: unknown = null

    const runner: WasmRunner = {
      run(in16, stemW, stemB, dwW, dwB, pwW, pwB, pooled, bufA) {
        const memNow = mem.buffer.byteLength
        const check = (name: string, off: number, len: number): void => {
          if (off + len * 4 > memNow)
            throw new Error(`[conv-wasm] ${name} 越界: off=${off} len=${len} mem=${memNow}`)
        }
        if (uploaded !== stemW) {
          // 首次或换实例：按 blob 内的固定偏移逐区上传（无需先在 JS 侧拼一份大数组）
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
        const rc = feats(offBlob, offIn, offPooled, offBufA)
        if (rc !== 0) throw new Error(`[conv-wasm] kernel rc=${rc}`)
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

/** bench/测试：强制关 wasm（null=已探测且禁用）或重置探测。生产路径不调用。 */
export function setStudentConvWasmEnabled(enabled: boolean): void {
  _runner = enabled ? undefined : null
}

/**
 * wasm 后端（**强制**走 wasm，不走 native）：对拍/基准的参考实现，也是 native 的
 * attestation 参照。生产路径请用 ./conv.ts 的 `runStudentFeatures`。
 * 返回 true = pooled+bufA 已填。
 */
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
