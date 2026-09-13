/**
 * mcnemar.ts — 配对胜负显著性（McNemar 精确二项双侧检验）。
 *
 * 用途：同 (stage, seed) 配对下回答「checkpoint 是否真比 baseline 强」——
 * 只看意见不一致的对（b01 = 基线输/新权重赢；b10 = 反之），一致对（b11/b00）
 * 是题目难易不是差距，直接丢弃。in-loop eval 本是固定语料，跨轮可比；
 * 跨语料对比（bc 在 0-99 vs 新权重在 860001+）时配对能把卷面难度抵消掉。
 *
 * 纯函数、无状态、确定性。回归锚：mcnemarP(27, 12) ≈ 0.025（c6-pickup.it35
 * vs bc，seeds 0-99）；mcnemarP(21, 14) ≈ 0.3105（c6-gae.it160 vs bc）。
 */

export interface PairedTable {
  /** 基线输、新权重赢（政绩）。 */
  b01: number
  /** 基线赢、新权重输（学费）。 */
  b10: number
  /** 都赢。 */
  b11: number
  /** 都输。 */
  b00: number
}

/** 两列同长布尔（同 seed 配对）→ 四格表。长度不一致抛错，绝不静默截断。 */
export function pairOutcomes(base: boolean[], ckpt: boolean[]): PairedTable {
  if (base.length !== ckpt.length) {
    throw new Error(`[mcnemar] base(${base.length}) 与 ckpt(${ckpt.length}) 长度不一致，拒绝配对`)
  }
  let b01 = 0
  let b10 = 0
  let b11 = 0
  let b00 = 0
  for (let i = 0; i < base.length; i++) {
    if (!base[i] && ckpt[i]) b01++
    else if (base[i] && !ckpt[i]) b10++
    else if (base[i] && ckpt[i]) b11++
    else b00++
  }
  return { b01, b10, b11, b00 }
}

/**
 * McNemar 精确二项双侧 p 值：P(X ≥ max(b01,b10) | Bin(n, 0.5)) × 2，上限钳 1。
 * n = 0（全一致）→ 1.0（无证据，不是显著）。n ≤ ~1000 用递推求和，无溢出之忧。
 */
export function mcnemarP(b01: number, b10: number): number {
  const n = b01 + b10
  if (n === 0) return 1.0
  const k = Math.max(b01, b10)
  // P(X = k) 递推：p(k+1) = p(k) * (n-k)/(k+1)，从众数附近起步防下溢无妨（n 小）。
  let term = Math.pow(0.5, n) // P(X = 0)
  for (let i = 0; i < k; i++) term *= (n - i) / (i + 1)
  let tail = term
  for (let i = k; i < n; i++) {
    term *= (n - i) / (i + 1)
    tail += term
  }
  return Math.min(1.0, 2 * tail)
}

/** 净涨幅（局数差，可除以总数转 pp）。 */
export function pairedDelta(t: PairedTable): number {
  return t.b01 - t.b10
}

export type PairedVerdict = 'up' | 'down' | 'flat'

/** 判决：显著且方向定 → up/down；其余（证据不够/全一致）一律 flat。 */
export function pairedVerdict(t: PairedTable, alpha = 0.05): PairedVerdict {
  if (mcnemarP(t.b01, t.b10) >= alpha) return 'flat'
  if (t.b01 > t.b10) return 'up'
  if (t.b10 > t.b01) return 'down'
  return 'flat'
}
