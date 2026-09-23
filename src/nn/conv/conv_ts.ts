/**
 * conv_ts.ts —— student 特征提取的**纯 TypeScript 孪生实现**（三个后端里的最后一环）。
 *
 * 选择链（rollout 与 eval 同路径，见 ./conv.ts）：native（bun:ffi）→ wasm32 SIMD →
 * **TS（本文件）**。前两个由 `conv.c` 编出（native 16px / wasm 8px，见 conv_native.h），
 * 本文件是那份算式的 TS 手工展开版 —— 三者的输出必须逐位一致（native↔wasm 由
 * tests/native-parity.test.ts 机械守护；TS 侧由 tests/nn/student-infer.test.ts 等 golden
 * 与 eval 的 `feat='ts'` 记账用例覆盖）。
 *
 * 为什么要单独一个文件（而不是留在 infer.ts 的类里）：
 *   ① 三个后端应当并排可见 —— 改 `conv.c` 时必须能一眼看到 TS 侧的对应实现（此前它散在
 *      `StudentModel` 的私有方法里，读者只会在看模型时偶然发现它）；② 它是**参考实现**：
 *      加速后端一旦可疑（attestation 之外的人工排查、新平台第一次跑），拿它对拍最直接；
 *   ③ 与 `infer.ts` 解耦：本文件不 import 任何模型/权重类型，入参只是一个 buffer 视图。
 *
 * **改动纪律**：这里的算术顺序是「逐位一致」契约的一半 —— 只允许做**不改变每元素累加次序**
 * 的改写（与 conv.c 的四项重排同一口径）；任何改累加序的"优化"都是语义变更，会让上面那些
 * golden/对拍一起红（那是信号，不是噪声）。性能上这里不需要手写向量化：生产档（h64/d8）必走
 * native/wasm，本路径只在架构不符或两个加速后端都不可用时生效（瘦身 fixture、BC/小模型、
 * 平台没有 prebuilt 库）。
 *
 * 本文件由 infer.ts 的 `StudentModel.features()` 调用；它写入 `pooled`（GAP 结果，随后接 fc）
 * 与 `bufA`（GAP 前的空间特征，goal 热图头消费 —— 两者都必须是最新的，见 conv_wasm_adapter.ts
 * 关于「只回拷 pooled」那次事故的注释）。
 */

/** 网格边长（26×26 子格，NCHW 布局；与 obs-encoder.BOARD / conv_native.h 的 CF_GRID 同值）。 */
const BOARD = 26

/** 模型 buffer 视图：只读字段来自架构，可写字段来自模型自带的可复用缓冲（无每 tick 分配）。 */
export interface ConvTsView {
  /** 卷积通道数 h（stem/dw/pw 的输出通道）。 */
  readonly h: number
  /** ConvMixer 块数 d。 */
  readonly d: number
  /** 输入通道数（obs 通道数；in16 里另有 2 个 coord 通道）。 */
  readonly inCh: number
  /** [inCh+2, 26, 26] 输入（obs 全通道 + 2 coords）。 */
  in16: Float32Array
  /** [h, inCh+2, 3, 3] stem 权重。 */
  stemW: Float32Array
  /** [h] */
  stemB: Float32Array
  /** 每块 [h, 1, 5, 5]（depthwise）。 */
  dwW: Float32Array[]
  /** 每块 [h] */
  dwB: Float32Array[]
  /** 每块 [h, h, 1, 1]（pointwise）。 */
  pwW: Float32Array[]
  /** 每块 [h] */
  pwB: Float32Array[]
  /** [h, 26, 26] 块输入 / 残差累加目标，最终 = GAP 前的空间特征。 */
  bufA: Float32Array
  /** [h, 26, 26] depthwise 输出。 */
  bufB: Float32Array
  /** [h, 26, 26] pointwise 输出。 */
  bufC: Float32Array
  /** [h] GAP 结果。 */
  pooled: Float32Array
}

/**
 * 跑一遍 student 卷积主干：stem(3×3, inCh+2→h) + ReLU → d × [dw5×5 + ReLU + pw1×1 + ReLU +
 * 残差] → GAP。写 `bufA`（空间特征）与 `pooled`（GAP），其余字段只读。
 */
export function runStudentConvTs(m: ConvTsView): void {
  const sp = BOARD * BOARD
  const h = m.h

  // stem: conv 3x3 (inCh+2=18)->h + ReLU
  conv3x3(m.in16, m.inCh + 2, m.stemW, m.stemB, m.bufA)
  reluInPlace(m.bufA)

  // d ConvMixer blocks: depthwise 5x5 + pointwise 1x1 + residual.
  for (let i = 0; i < m.d; i++) {
    conv5x5dw(m.bufA, m.dwW[i], m.dwB[i], m.bufB)
    reluInPlace(m.bufB)
    conv1x1(m.bufB, m.pwW[i], m.pwB[i], m.bufC)
    reluInPlace(m.bufC)
    // residual: bufA += bufC
    for (let j = 0; j < m.bufA.length; j++) m.bufA[j] += m.bufC[j]
  }

  // GAP
  m.pooled.fill(0)
  for (let ch = 0; ch < h; ch++) {
    const base = ch * sp
    let sum = 0
    for (let i = 0; i < sp; i++) sum += m.bufA[base + i]
    m.pooled[ch] = sum / sp
  }
}

function reluInPlace(buf: Float32Array): void {
  for (let i = 0; i < buf.length; i++) if (buf[i] < 0) buf[i] = 0
}

/** Conv 3x3, padding 1, stride 1, no groups (matches stem / BC conv2d). */
function conv3x3(
  input: Float32Array,
  inCh: number,
  w: Float32Array,
  b: Float32Array,
  out: Float32Array,
): void {
  const outCh = b.length
  const board = BOARD
  const sp = board * board
  for (let oc = 0; oc < outCh; oc++) {
    const wBase = oc * inCh * 9
    const oBase = oc * sp
    const bias = b[oc]
    for (let oh = 0; oh < board; oh++) {
      for (let ow = 0; ow < board; ow++) {
        let acc = bias
        for (let ic = 0; ic < inCh; ic++) {
          const iBase = ic * sp
          const wBaseIc = wBase + ic * 9
          // kh = 0
          let ih = oh - 1
          let iw = ow - 1
          if (ih >= 0 && iw >= 0) acc += w[wBaseIc + 0] * input[iBase + ih * board + iw]
          iw = ow
          if (ih >= 0) acc += w[wBaseIc + 1] * input[iBase + ih * board + iw]
          iw = ow + 1
          if (ih >= 0 && iw < board) acc += w[wBaseIc + 2] * input[iBase + ih * board + iw]
          // kh = 1
          ih = oh
          iw = ow - 1
          if (iw >= 0) acc += w[wBaseIc + 3] * input[iBase + ih * board + iw]
          iw = ow
          acc += w[wBaseIc + 4] * input[iBase + ih * board + iw]
          iw = ow + 1
          if (iw < board) acc += w[wBaseIc + 5] * input[iBase + ih * board + iw]
          // kh = 2
          ih = oh + 1
          iw = ow - 1
          if (ih < board && iw >= 0) acc += w[wBaseIc + 6] * input[iBase + ih * board + iw]
          iw = ow
          if (ih < board) acc += w[wBaseIc + 7] * input[iBase + ih * board + iw]
          iw = ow + 1
          if (ih < board && iw < board) acc += w[wBaseIc + 8] * input[iBase + ih * board + iw]
        }
        out[oBase + oh * board + ow] = acc
      }
    }
  }
}

/** Depthwise conv 5x5, padding 2, stride 1, groups=outCh. */
function conv5x5dw(input: Float32Array, w: Float32Array, b: Float32Array, out: Float32Array): void {
  const outCh = b.length // == input channel count (depthwise)
  const board = BOARD
  const sp = board * board
  for (let oc = 0; oc < outCh; oc++) {
    const wBase = oc * 25
    const base = oc * sp
    const bias = b[oc]
    for (let oh = 0; oh < board; oh++) {
      for (let ow = 0; ow < board; ow++) {
        let acc = bias
        // kh 0..4 / kw 0..4 with zero padding (out of [0, board) => 0)
        for (let kh = 0; kh < 5; kh++) {
          const ih = oh + kh - 2
          if (ih < 0 || ih >= board) continue
          const rowBase = base + ih * board
          for (let kw = 0; kw < 5; kw++) {
            const iw = ow + kw - 2
            if (iw < 0 || iw >= board) continue
            acc += w[wBase + kh * 5 + kw] * input[rowBase + iw]
          }
        }
        out[base + oh * board + ow] = acc
      }
    }
  }
}

/** Pointwise conv 1x1 (h -> h), no groups. */
function conv1x1(input: Float32Array, w: Float32Array, b: Float32Array, out: Float32Array): void {
  const outCh = b.length
  const sp = BOARD * BOARD
  for (let oc = 0; oc < outCh; oc++) {
    const wBase = oc * outCh
    const oBase = oc * sp
    const bias = b[oc]
    for (let p = 0; p < sp; p++) {
      let acc = bias
      for (let ic = 0; ic < outCh; ic++) acc += w[wBase + ic] * input[ic * sp + p]
      out[oBase + p] = acc
    }
  }
}
