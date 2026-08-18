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
export const ITEM_DIM = 3

/** Default conv channel plan (14 -> 32 -> 48 -> 64). Mirrors model.py. */
export const CONV_CH = [32, 48, 64] as const
export const HEAD_HIDDEN = 64

const RELU = (x: number): number => (x > 0 ? x : 0)

interface Param {
  shape: number[]
  data: string // base64 little-endian f32
}

interface WeightsJson {
  arch?: { conv_ch?: number[]; head_hidden?: number }
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
  private itemW: Float32Array // [ITEM_DIM, headHidden]
  private itemB: Float32Array // [ITEM_DIM]

  // ---- reusable buffers (no per-tick allocation) ----
  private obsF: Float32Array // [inCh * board * board]
  private convBuf: Float32Array[] // per conv layer output [outCh * board * board]
  private pooled: Float32Array // [convOutCh]
  private hidden: Float32Array // [headHidden]
  readonly moveLogits: Float32Array // [MOVE_DIM]
  readonly fireLogits: Float32Array // [FIRE_DIM]
  readonly itemLogits: Float32Array // [ITEM_DIM]

  constructor(params: Record<string, Float32Array>, shapes?: Record<string, number[]>) {
    const p = (name: string): Float32Array => {
      const arr = params[name]
      if (!arr) throw new Error(`NNModel: missing weight "${name}"`)
      return arr
    }
    const s = (name: string): number[] => {
      if (shapes && shapes[name]) return shapes[name]
      // Infer shape from known layer naming if not supplied.
      const map: Record<string, number[]> = {
        'conv.0.weight': [32, 14, 3, 3],
        'conv.0.bias': [32],
        'conv.2.weight': [48, 32, 3, 3],
        'conv.2.bias': [48],
        'conv.4.weight': [64, 48, 3, 3],
        'conv.4.bias': [64],
        'fc.weight': [64, 64],
        'fc.bias': [64],
        'move_head.weight': [5, 64],
        'move_head.bias': [5],
        'fire_head.weight': [2, 64],
        'fire_head.bias': [2],
        'item_head.weight': [3, 64],
        'item_head.bias': [3],
      }
      const sh = map[name]
      if (!sh) throw new Error(`NNModel: unknown param shape "${name}"`)
      return sh
    }
    const convOutCh = CONV_CH[CONV_CH.length - 1]
    void s // shapes optional; laid out explicitly below

    this.layers = [
      { w: p('conv.0.weight'), b: p('conv.0.bias'), inCh: 14, outCh: 32 },
      { w: p('conv.2.weight'), b: p('conv.2.bias'), inCh: 32, outCh: 48 },
      { w: p('conv.4.weight'), b: p('conv.4.bias'), inCh: 48, outCh: 64 },
    ]
    this.fcW = p('fc.weight')
    this.fcB = p('fc.bias')
    this.moveW = p('move_head.weight')
    this.moveB = p('move_head.bias')
    this.fireW = p('fire_head.weight')
    this.fireB = p('fire_head.bias')
    this.itemW = p('item_head.weight')
    this.itemB = p('item_head.bias')

    const sp = BOARD * BOARD
    this.obsF = new Float32Array(this.inCh * sp)
    this.convBuf = this.layers.map((l) => new Float32Array(l.outCh * sp))
    this.pooled = new Float32Array(convOutCh)
    this.hidden = new Float32Array(HEAD_HIDDEN)
    this.moveLogits = new Float32Array(MOVE_DIM)
    this.fireLogits = new Float32Array(FIRE_DIM)
    this.itemLogits = new Float32Array(ITEM_DIM)
  }

  /**
   * Forward pass. `obs` is the flat NCHW Uint8 buffer from ObsEncoder.
   * `scalars` is accepted for API symmetry but the v1 backbone does not use
   * it (matches model.py).
   */
  forward(obs: Uint8Array, _scalars: Float32Array): void {
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

    // fc: hidden = relu(pooled · fcW^T + fcB)
    for (let o = 0; o < HEAD_HIDDEN; o++) {
      let acc = this.fcB[o]
      for (let i = 0; i < outCh; i++) acc += this.fcW[o * outCh + i] * this.pooled[i]
      this.hidden[o] = RELU(acc)
    }

    this.linear(this.hidden, this.moveW, this.moveB, this.moveLogits, MOVE_DIM, HEAD_HIDDEN)
    this.linear(this.hidden, this.fireW, this.fireB, this.fireLogits, FIRE_DIM, HEAD_HIDDEN)
    this.linear(this.hidden, this.itemW, this.itemB, this.itemLogits, ITEM_DIM, HEAD_HIDDEN)
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

/** Parse a weights-JSON object into a NNModel. */
export function buildModelFromJson(json: WeightsJson): NNModel {
  const params: Record<string, Float32Array> = {}
  for (const [name, param] of Object.entries(json.params)) {
    params[name] = b64ToF32(param.data)
  }
  return new NNModel(params)
}

/** Decode the base64 weights JSON text into a NNModel. */
export function buildModelFromText(text: string): NNModel {
  const json = JSON.parse(text) as WeightsJson
  return buildModelFromJson(json)
}
