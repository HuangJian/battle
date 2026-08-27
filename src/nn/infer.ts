/**
 * infer.ts — pure-TS runtime inference for the NN player policy (plan §NN-M1).
 *
 * Replicates the Python `NNPolicy` forward pass from `nn-training/model.py`
 * byte-for-byte from the exported weights (plan §NN-M1 determinism ②). No
 * torch, no DOM, no per-tick allocation on the hot path (reused buffers).
 *
 * The model is fully-convolutional (no size-dependent FC layers) with only
 * ReLU activations + a self-implemented softmax, so the forward pass is
 * exactly reproducible in TS. See model.py for the reference implementation.
 *
 * Layout conventions (must match nn-training/schema.py + obs-encoder.ts):
 *   obs  : flat Uint8  [ch * 26 * 26]  (NCHW: ch, row, col)
 *   GAP  : global average pool over the 26x26 spatial -> divide by 676
 *   heads: move(5) / fire(2) / item(3); backbone ignores scalars.
 */

import { OBS_CHANNELS, BOARD, SCALAR_DIM } from './obs-encoder'

export const MOVE_DIM = 5
export const FIRE_DIM = 2

/** Default conv channel plan (14 -> 32 -> 48 -> 64). Mirrors model.py. */
export const CONV_CH = [32, 48, 64] as const
export const HEAD_HIDDEN = 64

/** Common forward surface shared by the BC model and the distilled student. */
export interface ModelLike {
  forward(obs: Uint8Array, scalars: Float32Array): void
  readonly inCh: number
  readonly board: number
  readonly scalarDim: number
  readonly moveLogits: Float32Array
  readonly fireLogits: Float32Array
}

const RELU = (x: number): number => (x > 0 ? x : 0)

interface Param {
  shape: number[]
  data: string // base64 little-endian f32
}

interface WeightsJson {
  arch?: {
    conv_ch?: number[]
    head_hidden?: number
    kind?: string
    h?: number
    d?: number
  }
  params: Record<string, Param>
}

/** Decode a base64 (little-endian f32) string into a Float32Array. */
function b64ToF32(b64: string): Float32Array {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Float32Array(bytes.buffer, bytes.byteOffset, bytes.byteLength >> 2)
}

interface ConvLayer {
  w: Float32Array // [outCh, inCh, 3, 3]
  b: Float32Array // [outCh]
  inCh: number
  outCh: number
}

/**
 * Reusable concatenated buffer for [pooled(convOutCh) | scalars(scalarDim)].
 * Prevents per-tick allocation on the hot path.
 */
let fusedBuf: Float32Array | null = null
function getFusedBuf(convOutCh: number, scalarDim: number): Float32Array {
  const len = convOutCh + scalarDim
  if (!fusedBuf || fusedBuf.length !== len) fusedBuf = new Float32Array(len)
  return fusedBuf
}

/**
 * Pure-TS NN policy network. Holds the decoded weights and reusable activation
 * buffers. `forward` returns the three heads' raw logits.
 */
export class NNModel {
  readonly inCh = OBS_CHANNELS
  readonly board = BOARD
  readonly scalarDim = SCALAR_DIM
  readonly convCh = CONV_CH
  readonly headHidden = HEAD_HIDDEN

  private layers: ConvLayer[]
  private fcW: Float32Array // [headHidden, convOutCh]
  private fcB: Float32Array // [headHidden]
  private moveW: Float32Array // [MOVE_DIM, headHidden]
  private moveB: Float32Array // [MOVE_DIM]
  private fireW: Float32Array // [FIRE_DIM, headHidden]
  private fireB: Float32Array // [FIRE_DIM]

  // ---- reusable buffers (no per-tick allocation) ----
  private obsF: Float32Array // [inCh * board * board]
  private convBuf: Float32Array[] // per conv layer output [outCh * board * board]
  private pooled: Float32Array // [convOutCh]
  private hidden: Float32Array // [headHidden]
  readonly moveLogits: Float32Array // [MOVE_DIM]
  readonly fireLogits: Float32Array // [FIRE_DIM]

  constructor(params: Record<string, Float32Array>, shapes?: Record<string, number[]>) {
    const p = (name: string): Float32Array => {
      const arr = params[name]
      if (!arr) throw new Error(`NNModel: missing weight "${name}"`)
      return arr
    }
    void shapes // shapes read from JSON; used for validation only
    const convOutCh = CONV_CH[CONV_CH.length - 1]

    this.layers = [
      { w: p('conv.0.weight'), b: p('conv.0.bias'), inCh: 14, outCh: 32 },
      { w: p('conv.2.weight'), b: p('conv.2.bias'), inCh: 32, outCh: 48 },
      { w: p('conv.4.weight'), b: p('conv.4.bias'), inCh: 48, outCh: 64 },
    ]
    // FC input = convOutCh (64) + scalarDim (19) = 83 for v2 scalar-fusion
    this.fcW = p('fc.weight')
    this.fcB = p('fc.bias')
    this.moveW = p('move_head.weight')
    this.moveB = p('move_head.bias')
    this.fireW = p('fire_head.weight')
    this.fireB = p('fire_head.bias')

    const sp = BOARD * BOARD
    this.obsF = new Float32Array(this.inCh * sp)
    this.convBuf = this.layers.map((l) => new Float32Array(l.outCh * sp))
    this.pooled = new Float32Array(convOutCh)
    this.hidden = new Float32Array(HEAD_HIDDEN)
    this.moveLogits = new Float32Array(MOVE_DIM)
    this.fireLogits = new Float32Array(FIRE_DIM)
  }

  /**
   * Forward pass. `obs` is the flat NCHW Uint8 buffer from ObsEncoder.
   * `scalars` is the 19-dim feature vector fused into the FC layer.
   */
  forward(obs: Uint8Array, scalars: Float32Array): void {
    const sp = this.board * this.board
    // uint8 -> float
    for (let i = 0; i < obs.length; i++) this.obsF[i] = obs[i]

    let input = this.obsF
    for (let li = 0; li < this.layers.length; li++) {
      const layer = this.layers[li]
      const out = this.convBuf[li]
      this.conv2d(input, layer, out)
      // ReLU in place
      for (let i = 0; i < out.length; i++) if (out[i] < 0) out[i] = 0
      input = out
    }

    // Global average pool over spatial -> divide by sp.
    const c = this.layers[this.layers.length - 1].outCh
    const outCh = c
    this.pooled.fill(0)
    for (let ch = 0; ch < outCh; ch++) {
      const base = ch * sp
      let sum = 0
      for (let i = 0; i < sp; i++) sum += input[base + i]
      this.pooled[ch] = sum / sp
    }

    // Concatenate pooled (outCh) + scalars (scalarDim) -> fused
    const fused = getFusedBuf(outCh, this.scalarDim)
    for (let i = 0; i < outCh; i++) fused[i] = this.pooled[i]
    for (let i = 0; i < this.scalarDim; i++) fused[outCh + i] = scalars[i]
    const fusedLen = outCh + this.scalarDim

    // fc: hidden = relu(fused · fcW^T + fcB)
    for (let o = 0; o < HEAD_HIDDEN; o++) {
      let acc = this.fcB[o]
      for (let i = 0; i < fusedLen; i++) acc += this.fcW[o * fusedLen + i] * fused[i]
      this.hidden[o] = RELU(acc)
    }

    this.linear(this.hidden, this.moveW, this.moveB, this.moveLogits, MOVE_DIM, HEAD_HIDDEN)
    this.linear(this.hidden, this.fireW, this.fireB, this.fireLogits, FIRE_DIM, HEAD_HIDDEN)
  }

  /** head = W · h + b ; W is [outDim, inDim] (PyTorch Linear layout). */
  private linear(
    h: Float32Array,
    w: Float32Array,
    b: Float32Array,
    out: Float32Array,
    outDim: number,
    inDim: number,
  ): void {
    for (let o = 0; o < outDim; o++) {
      let acc = b[o]
      const wb = o * inDim
      for (let i = 0; i < inDim; i++) acc += w[wb + i] * h[i]
      out[o] = acc
    }
  }

  /**
   * Conv2d with kernel 3, padding 1, stride 1, no dilation, no groups, bias.
   * Matches PyTorch's Conv2d (weight layout [outCh, inCh, kH, kW], zero pad).
   */
  private conv2d(input: Float32Array, layer: ConvLayer, out: Float32Array): void {
    const { inCh, outCh, w, b } = layer
    const board = this.board
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
            // unrolled kh/kw 0..2 with padding-zero (out of [0,board) => 0)
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

  /** Softmax over a head's logits into `dst` (length n). */
  static softmax(src: Float32Array, n: number, dst: Float32Array): void {
    let max = -Infinity
    for (let i = 0; i < n; i++) if (src[i] > max) max = src[i]
    let sum = 0
    for (let i = 0; i < n; i++) {
      const e = Math.exp(src[i] - max)
      dst[i] = e
      sum += e
    }
    const inv = 1 / sum
    for (let i = 0; i < n; i++) dst[i] *= inv
  }
}

/**
 * CoordConv-ConvMixer-Lite student (plan/RL-Net-Selection.md §4.3), BN-free.
 * Reproduces `nn-training/student_model.py` byte-for-byte from the exported
 * weights. 16 input channels = 14 encoder obs + 2 computed coord channels.
 */
export class StudentModel implements ModelLike {
  readonly inCh = OBS_CHANNELS
  readonly board = BOARD
  readonly scalarDim = SCALAR_DIM
  readonly h: number
  readonly d: number
  readonly headHidden: number

  private stemW: Float32Array // [h, 16, 3, 3]
  private stemB: Float32Array // [h]
  private dwW: Float32Array[] // per block [h, 1, 5, 5]
  private dwB: Float32Array[] // per block [h]
  private pwW: Float32Array[] // per block [h, h, 1, 1]
  private pwB: Float32Array[] // per block [h]
  private fcW: Float32Array // [headHidden, h + scalarDim]
  private fcB: Float32Array // [headHidden]
  private moveW: Float32Array
  private moveB: Float32Array
  private fireW: Float32Array
  private fireB: Float32Array
  // Per-tick value head [128 -> 1] (PPOStudent): consumes `hidden`, used in forward().
  private valueW: Float32Array
  private valueB: Float32Array
  // Intent value head [137 -> 1] (IntentNet with_value, P1-5②): consumes the 137-dim
  // hidden+inject commitment vector, used in intentForward(). M8-only; BC/intent
  // checkpoints without a value head leave this null and valueOut reads 0.
  private valueW137: Float32Array | null
  private valueB137: Float32Array | null
  // M4 intent heads (optional): [137, out] where 137 = 128 hidden + 9 inject.
  private intentW: Float32Array | null
  private intentB: Float32Array | null
  private enemyW: Float32Array | null
  private enemyB: Float32Array | null
  private anchorW: Float32Array | null
  private anchorB: Float32Array | null

  // ---- reusable buffers (no per-tick allocation) ----
  private in16: Float32Array // [16 * board * board] (14 obs + 2 coords)
  private coords: Float32Array // [2 * board * board] precomputed coord channels
  private bufA: Float32Array // [h * board * board] (block input / residual out)
  private bufB: Float32Array // [h * board * board] (depthwise out)
  private bufC: Float32Array // [h * board * board] (pointwise out)
  private pooled: Float32Array // [h]
  private hidden: Float32Array // [headHidden]
  /** inject concat buffer (M4): hidden(128) + inject(9) -> [137]. */
  private hiddenInject: Float32Array
  readonly moveLogits: Float32Array
  readonly fireLogits: Float32Array
  readonly valueOut: Float32Array
  readonly intentLogits: Float32Array // [8] (M4)
  readonly enemyLogits: Float32Array // [5] (M4)
  readonly anchorLogits: Float32Array // [16] (M4)

  constructor(
    params: Record<string, Float32Array>,
    arch: { h?: number; d?: number },
    intentHeads?: boolean,
  ) {
    const p = (name: string): Float32Array => {
      const arr = params[name]
      if (!arr) throw new Error(`StudentModel: missing weight "${name}"`)
      return arr
    }
    this.h = arch.h ?? 64
    this.d = arch.d ?? 8
    this.headHidden = 128
    const h = this.h
    const sp = BOARD * BOARD

    this.stemW = p('stem.weight')
    this.stemB = p('stem.bias')
    this.dwW = []
    this.dwB = []
    this.pwW = []
    this.pwB = []
    for (let i = 0; i < this.d; i++) {
      this.dwW.push(p(`blocks.${i}.dw.weight`))
      this.dwB.push(p(`blocks.${i}.dw.bias`))
      this.pwW.push(p(`blocks.${i}.pw.weight`))
      this.pwB.push(p(`blocks.${i}.pw.bias`))
    }
    this.fcW = p('fc.weight')
    this.fcB = p('fc.bias')
    this.moveW = p('move_head.weight')
    this.moveB = p('move_head.bias')
    this.fireW = p('fire_head.weight')
    this.fireB = p('fire_head.bias')
    // Value head is OPTIONAL: RL weights include value_head.*; BC-only
    // checkpoints don't, in which case we zero-init (harmless for the
    // pure-policy deployment path). M8 (P1-5②): intent RL weights carry a
    // 137-wide value head (128 hidden + 9 inject) — value must see the
    // commitment state, so intentForward() consumes hiddenInject. The
    // per-tick student value head is 128-wide and stays in forward().
    const vh = params['value_head.weight']
    if (vh && vh.length === this.headHidden + 9) {
      this.valueW137 = vh
      this.valueB137 = params['value_head.bias'] ?? new Float32Array(1)
      this.valueW = new Float32Array(0)
      this.valueB = new Float32Array(1)
    } else {
      this.valueW137 = null
      this.valueB137 = null
      if (vh) {
        this.valueW = vh
        this.valueB = params['value_head.bias'] ?? new Float32Array(1)
      } else {
        this.valueW = new Float32Array(this.headHidden)
        this.valueB = new Float32Array(1)
      }
    }
    // M4 intent heads: intent_head./enemy_head./anchor_head. on the 137-dim
    // (hidden 128 + inject 9) entry space. Loaded only when intentHeads=true
    // (IntentModel path); the plain StudentModel stays byte-identical.
    if (intentHeads) {
      this.intentW = p('intent_head.weight')
      this.intentB = p('intent_head.bias')
      this.enemyW = p('enemy_head.weight')
      this.enemyB = p('enemy_head.bias')
      this.anchorW = p('anchor_head.weight')
      this.anchorB = p('anchor_head.bias')
    } else {
      this.intentW = this.enemyW = this.anchorW = null
      this.intentB = this.enemyB = this.anchorB = null
    }

    this.in16 = new Float32Array(16 * sp)
    // Coord channels: ch14[r*B+c] = round(c/(B-1)*255), ch15[r*B+c] = round(r/(B-1)*255).
    // MUST match nn-training/student_model.py coord_channels() exactly.
    this.coords = new Float32Array(2 * sp)
    for (let r = 0; r < BOARD; r++) {
      for (let c = 0; c < BOARD; c++) {
        this.coords[r * BOARD + c] = Math.round((c / (BOARD - 1)) * 255)
        this.coords[sp + r * BOARD + c] = Math.round((r / (BOARD - 1)) * 255)
      }
    }
    this.bufA = new Float32Array(h * sp)
    this.bufB = new Float32Array(h * sp)
    this.bufC = new Float32Array(h * sp)
    this.pooled = new Float32Array(h)
    this.hidden = new Float32Array(this.headHidden)
    this.hiddenInject = new Float32Array(this.headHidden + 9)
    this.moveLogits = new Float32Array(MOVE_DIM)
    this.fireLogits = new Float32Array(FIRE_DIM)
    this.valueOut = new Float32Array(1)
    this.intentLogits = new Float32Array(8)
    this.enemyLogits = new Float32Array(5)
    this.anchorLogits = new Float32Array(16)
  }

  forward(obs: Uint8Array, scalars: Float32Array): void {
    this.features(obs, scalars)

    this.linear(this.hidden, this.moveW, this.moveB, this.moveLogits, MOVE_DIM, this.headHidden)
    this.linear(this.hidden, this.fireW, this.fireB, this.fireLogits, FIRE_DIM, this.headHidden)
    if (this.valueW.length === this.headHidden) {
      this.linear(this.hidden, this.valueW, this.valueB, this.valueOut, 1, this.headHidden)
    } else {
      this.valueOut[0] = 0 // no per-tick value head (intent model path)
    }
  }

  /**
   * M4: intent 三头前向（IntentModel 路径）。inject = Float32Array(9)：
   * [prev-intent one-hot(8), duration]。hidden(128) + inject(9) -> 137 -> 三头。
   * 与 intent_net.py 的 forward 数值一致（P3-4 字节一致测试锚定）。
   *
   * M8（P1-5②）：value 头与三头并列消费同一 137 隐藏（128+9 注入）——value 必须
   * 看到承诺状态，否则同 obs 不同意图龄的 value 估计系统性偏差。137 宽 value 头
   * 在此计算 valueOut；无 value 头（BC/intent 检查点）置 0（首个 RL 轮次零基线）。
   */
  intentForward(obs: Uint8Array, scalars: Float32Array, inject: Float32Array): void {
    const iw = this.intentW
    const ew = this.enemyW
    const aw = this.anchorW
    if (!iw || !ew || !aw) {
      for (let i = 0; i < 8; i++) this.intentLogits[i] = 0
      for (let i = 0; i < 5; i++) this.enemyLogits[i] = 0
      for (let i = 0; i < 16; i++) this.anchorLogits[i] = 0
      this.valueOut[0] = 0
      return
    }
    this.features(obs, scalars)
    this.hiddenInject.set(this.hidden)
    this.hiddenInject.set(inject, this.headHidden)
    this.linear(this.hiddenInject, iw, this.intentB as Float32Array, this.intentLogits, 8, 137)
    this.linear(this.hiddenInject, ew, this.enemyB as Float32Array, this.enemyLogits, 5, 137)
    this.linear(this.hiddenInject, aw, this.anchorB as Float32Array, this.anchorLogits, 16, 137)
    if (this.valueW137) {
      this.linear(
        this.hiddenInject,
        this.valueW137,
        this.valueB137 as Float32Array,
        this.valueOut,
        1,
        137,
      )
    } else {
      this.valueOut[0] = 0
    }
  }

  /** 主干：stem + ConvMixer + GAP + fc → this.hidden（forward/intentForward 共用）。 */
  private features(obs: Uint8Array, scalars: Float32Array): void {
    const sp = this.board * this.board
    const h = this.h
    // 16ch input: copy 14 obs channels then append the precomputed coords.
    for (let i = 0; i < 14 * sp; i++) this.in16[i] = obs[i]
    this.in16.set(this.coords, 14 * sp)

    // stem: conv 3x3 16->h + ReLU
    this.conv3x3(this.in16, 16, this.stemW, this.stemB, this.bufA)
    this.reluInPlace(this.bufA)

    // d ConvMixer blocks: depthwise 5x5 + pointwise 1x1 + residual.
    for (let i = 0; i < this.d; i++) {
      this.conv5x5dw(this.bufA, this.dwW[i], this.dwB[i], this.bufB)
      this.reluInPlace(this.bufB)
      this.conv1x1(this.bufB, this.pwW[i], this.pwB[i], this.bufC)
      this.reluInPlace(this.bufC)
      // residual: bufA += bufC
      for (let j = 0; j < this.bufA.length; j++) this.bufA[j] += this.bufC[j]
    }

    // GAP
    this.pooled.fill(0)
    for (let ch = 0; ch < h; ch++) {
      const base = ch * sp
      let sum = 0
      for (let i = 0; i < sp; i++) sum += this.bufA[base + i]
      this.pooled[ch] = sum / sp
    }

    // fc: hidden = relu(fused · fcW^T + fcB)
    const fusedLen = h + this.scalarDim
    for (let o = 0; o < this.headHidden; o++) {
      let acc = this.fcB[o]
      const wb = o * fusedLen
      for (let i = 0; i < h; i++) acc += this.fcW[wb + i] * this.pooled[i]
      for (let i = 0; i < this.scalarDim; i++) acc += this.fcW[wb + h + i] * scalars[i]
      this.hidden[o] = RELU(acc)
    }
  }

  private reluInPlace(buf: Float32Array): void {
    for (let i = 0; i < buf.length; i++) if (buf[i] < 0) buf[i] = 0
  }

  /** head = W · h + b ; W is [outDim, inDim] (PyTorch Linear layout). */
  private linear(
    h: Float32Array,
    w: Float32Array,
    b: Float32Array,
    out: Float32Array,
    outDim: number,
    inDim: number,
  ): void {
    for (let o = 0; o < outDim; o++) {
      let acc = b[o]
      const wb = o * inDim
      for (let i = 0; i < inDim; i++) acc += w[wb + i] * h[i]
      out[o] = acc
    }
  }

  /** Conv 3x3, padding 1, stride 1, no groups (matches stem / BC conv2d). */
  private conv3x3(
    input: Float32Array,
    inCh: number,
    w: Float32Array,
    b: Float32Array,
    out: Float32Array,
  ): void {
    const outCh = b.length
    const board = this.board
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
  private conv5x5dw(
    input: Float32Array,
    w: Float32Array,
    b: Float32Array,
    out: Float32Array,
  ): void {
    const outCh = b.length // == input channel count (depthwise)
    const board = this.board
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
  private conv1x1(input: Float32Array, w: Float32Array, b: Float32Array, out: Float32Array): void {
    const outCh = b.length
    const sp = this.board * this.board
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
}

/** Parse a weights-JSON object into a ModelLike (BC or student by arch). */
export function buildModelFromJson(json: WeightsJson): ModelLike {
  const params: Record<string, Float32Array> = {}
  const shapes: Record<string, number[]> = {}
  for (const [name, param] of Object.entries(json.params)) {
    params[name] = b64ToF32(param.data)
    if (param.shape) shapes[name] = param.shape
  }
  const arch = json.arch ?? {}
  if (arch.kind === 'student') {
    return new StudentModel(params, arch as { h?: number; d?: number })
  }
  return new NNModel(params, shapes)
}

/** M4: intent 三头模型的推理面（intentForward + 三组 logits）。M8: + valueOut。 */
export interface IntentModelLike {
  intentForward(obs: Uint8Array, scalars: Float32Array, inject: Float32Array): void
  readonly intentLogits: Float32Array // [8]
  readonly enemyLogits: Float32Array // [5]
  readonly anchorLogits: Float32Array // [16]
  readonly valueOut: Float32Array // [1] — value head over the 137 hidden+inject (P1-5②)
}

/** 解析 intent 权重 JSON（arch.kind === 'intent'）→ IntentModelLike。 */
export function buildIntentModelFromJson(json: WeightsJson): IntentModelLike {
  const params: Record<string, Float32Array> = {}
  for (const [name, param] of Object.entries(json.params)) {
    params[name] = b64ToF32(param.data)
  }
  const arch = json.arch ?? {}
  return new StudentModel(params, arch as { h?: number; d?: number }, true)
}

/** 从 JSON 文本解析 intent 模型。 */
export function buildIntentModelFromText(text: string): IntentModelLike {
  return buildIntentModelFromJson(JSON.parse(text) as WeightsJson)
}

/** Decode the base64 weights JSON text into a ModelLike. */
export function buildModelFromText(text: string): ModelLike {
  const json = JSON.parse(text) as WeightsJson
  return buildModelFromJson(json)
}
