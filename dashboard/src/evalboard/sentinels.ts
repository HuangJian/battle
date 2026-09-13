/** sentinels.ts — 异常巡检 S1–S12（plan/rl-eval-system.md §7）。
 *
 * 输入口径规则（§7 表头）：A 层同 seed 序列相邻 ckpt = 配对；B 层跨批/跨窗 = 非配对。
 * S7/S9 是基线偏离（§7.1），不是绝对阈值。熔断 S2/S3/S4/S5 单批即可触发（§7.2）。
 */

import { mddPaired, mddUnpaired, type TierMetrics } from './stats'

export type Severity = 'red' | 'yellow' | 'note' | 'star'

export interface SentinelHit {
  id: string
  severity: Severity
  message: string
  /** S6 命中时禁下 verdict（§7）；S8 整批拒绝。 */
  blocksVerdict?: boolean
  rejectBatch?: boolean
}

/** 相邻 ckpt 配对比较输入（A 层，同 EVAL_SEEDS）。 */
export interface PairedDelta {
  /** 本期 winRate − 上期 winRate（比例点）。 */
  dWin: number
  dDeath: number
  dKillCompletion: number
  dTimeout: number
  dAccuracy: number
  dShotsPerGame: number
  dWinTickMedian: number
  /** 配对 MDD（按实测 flips 重算，§2.4）。 */
  mdd: number
  /** 空集 N/A 不进判定（S5，§4.4）。 */
  winTickNa: boolean
}

/** S1/S11 窗输入（B 层，400 局）。 */
export interface WindowInput {
  student: TierMetrics
  god: TierMetrics
  /** 配对 McNemar MDD（同 seed，God 确定性，§5.2）。 */
  mdd: number
}

export interface SentinelCfg {
  costUpper?: number
  timeoutGate?: number
  /** S7：冷启动 eval 点数（默认 5，§7）。 */
  s7MinPoints?: number
  /** S7：基线窗长（默认 20，可配）。 */
  s7Window?: number
  /** S12：静默平台窗数（默认连续 2 窗，窗级 n=400）。 */
  s12Windows?: number
}

const COST_UPPER_DFT = 13.5
const TIMEOUT_DFT = 0.15

function costGateValue(godLifePrice: number, upper = COST_UPPER_DFT): number {
  return Math.min(Math.max(3 * godLifePrice, godLifePrice), upper)
}

/** S1 命价失衡（B 窗）：lifePrice > clamp(3×God, God, 13.5)；wins=0 ⇒ +∞。 */
export function checkS1(
  student: TierMetrics,
  god: TierMetrics,
  upper = COST_UPPER_DFT,
): SentinelHit | null {
  if (student.lifePrice > costGateValue(god.lifePrice, upper)) {
    return {
      id: 'S1',
      severity: 'yellow',
      message: `命价失衡 lifePrice=${Number.isFinite(student.lifePrice) ? student.lifePrice.toFixed(2) : '+∞'} > 门 ${costGateValue(god.lifePrice, upper).toFixed(2)}`,
    }
  }
  return null
}

/** S2 崩塌（A 相邻 ckpt 配对）：deathRate 上行 ≥5pp 且 winRate 下行 ≥1 MDD。 */
export function checkS2(d: PairedDelta): SentinelHit | null {
  if (d.dDeath >= 0.05 && d.dWin <= -d.mdd) {
    return {
      id: 'S2',
      severity: 'red',
      message: `崩塌 death+${(d.dDeath * 100).toFixed(1)}pp win${(d.dWin * 100).toFixed(1)}pp，建议停腿`,
    }
  }
  return null
}

/** S3 苟化（A 相邻 ckpt 配对）：killCompletion 跌 + timeoutRate 涨（各 ≥1 MDD）。 */
export function checkS3(d: PairedDelta): SentinelHit | null {
  if (d.dKillCompletion <= -d.mdd && d.dTimeout >= d.mdd) {
    return { id: 'S3', severity: 'red', message: '苟化 kill↓ + timeout↑' }
  }
  return null
}

/** S4 闭嘴螺旋（A 相邻 ckpt 配对）：accuracy 跌 ≥1 MDD 且 shotsPerGame 跌。 */
export function checkS4(d: PairedDelta): SentinelHit | null {
  if (d.dAccuracy <= -d.mdd && d.dShotsPerGame < 0) {
    return { id: 'S4', severity: 'red', message: '闭嘴螺旋 accuracy↓ + shots↓' }
  }
  return null
}

/** S5 tempo 恶化（A 相邻 ckpt 配对）：winTickMedian 涨 ≥1 MDD（空集 N/A 不进判定）。 */
export function checkS5(d: PairedDelta): SentinelHit | null {
  if (d.winTickNa) return null
  if (d.dWinTickMedian >= d.mdd * 2400) {
    return { id: 'S5', severity: 'yellow', message: 'tempo 恶化 winTickMedian↑' }
  }
  return null
}

/**
 * S5 tick 版（归一化口径）：调用方把 dWinTickMedian 除以 maxTicks 传 dWinTickNorm，
 * 与无量纲 MDD 比较。保留两位入口，哨兵语义同一条。
 */
export function checkS5norm(
  dWinTickNorm: number,
  mdd: number,
  winTickNa: boolean,
): SentinelHit | null {
  if (winTickNa) return null
  if (dWinTickNorm >= mdd)
    return { id: 'S5', severity: 'yellow', message: 'tempo 恶化 winTickMedian↑' }
  return null
}

/** S6 高翻转低净值（任一配对比较）：flipRate > 40% 且 |Δ| < 0.5 MDD ⇒ 禁下 verdict。 */
export function checkS6(flipRate: number, delta: number, mdd: number): SentinelHit | null {
  if (flipRate > 0.4 && Math.abs(delta) < 0.5 * mdd) {
    return {
      id: 'S6',
      severity: 'note',
      message: '高翻转低净值：读数不可用，禁下 verdict',
      blocksVerdict: true,
    }
  }
  return null
}

/**
 * S7 训练/部署背离（A 层滚动，每 eval 点）：|(rollout−eval)_now − 该腿基线均值| > 10pp；
 * ≥5 个 eval 点才武装（§7）。
 */
export function checkS7(gaps: number[], window = 20, minPoints = 5): SentinelHit | null {
  if (gaps.length < minPoints) return null
  const base = gaps.slice(-window, -1)
  if (base.length === 0) return null
  const mean = base.reduce((s, v) => s + v, 0) / base.length
  const now = gaps[gaps.length - 1]
  if (Math.abs(now - mean) > 0.1) {
    return {
      id: 'S7',
      severity: 'red',
      message: `训练/部署背离 gap=${(now * 100).toFixed(1)}pp vs 基线 ${(mean * 100).toFixed(1)}pp`,
    }
  }
  return null
}

/** S8 数据污染（批次级）：dropped>0 / metrics_version 不符 / ckpt_sha16 缺失 ⇒ 整批拒绝。 */
export function checkS8(meta: {
  dropped: number
  metrics_version: number | null
  ckpt_sha16: string | null
}): SentinelHit | null {
  const reasons: string[] = []
  if (meta.dropped > 0) reasons.push(`dropped=${meta.dropped}`)
  if (meta.metrics_version !== 1) reasons.push(`metrics_version=${meta.metrics_version}`)
  if (!meta.ckpt_sha16) reasons.push('ckpt_sha16 缺失')
  if (reasons.length > 0) {
    return {
      id: 'S8',
      severity: 'red',
      message: `数据污染 ${reasons.join(' ')}：整批拒绝`,
      rejectBatch: true,
    }
  }
  return null
}

/**
 * S9 训练健康（训练日志，每 iter）：熵相对该腿峰值跌幅 >30%；KL > 0.05 ⇒ 标黄。
 * 绝对电平是训练侧内建熔断，EvalBench 不重复造（§7.1）。
 */
export function checkS9(entropy: number, entropyPeak: number, kl: number): SentinelHit | null {
  if (entropyPeak > 0 && (entropyPeak - entropy) / entropyPeak > 0.3) {
    return {
      id: 'S9',
      severity: 'yellow',
      message: `熵相对峰值跌幅 >30%（${entropy.toFixed(3)} vs ${entropyPeak.toFixed(3)}）`,
    }
  }
  if (kl > 0.05) return { id: 'S9', severity: 'yellow', message: `KL=${kl.toFixed(4)} > 0.05` }
  return null
}

/** S10 引擎漂移（记录级）：同课程记录跨 engine_epoch ⇒ 重跑参照组或标 stale。 */
export function checkS10(epochs: string[]): SentinelHit | null {
  if (new Set(epochs).size > 1) {
    return {
      id: 'S10',
      severity: 'yellow',
      message: '引擎漂移：同课程跨 engine_epoch，重跑参照组或标 stale',
    }
  }
  return null
}

/**
 * S11 超老师（B 窗，同 seed 配对）：studentWin > godWin + 1 MDD（配对 McNemar）。
 * Δ_space 未出数前对历史结论只提示（severity note，不判能力，§7/R6）。
 */
export function checkS11(w: WindowInput, spaceCalibrated: boolean): SentinelHit | null {
  if (w.student.winRate > w.god.winRate + w.mdd) {
    return {
      id: 'S11',
      severity: spaceCalibrated ? 'star' : 'note',
      message: spaceCalibrated ? '超老师 ★（人工核实）' : '超老师（Δ_space 未出数：只提示不判定）',
    }
  }
  return null
}

/** S12 静默平台（窗级 n=400）：连续 ≥2 窗 |Δ| < 0.5 MDD（≈3.5pp）。 */
export function checkS12(windowDeltas: number[], mdd: number, needWindows = 2): SentinelHit | null {
  if (windowDeltas.length < needWindows) return null
  const tail = windowDeltas.slice(-needWindows)
  if (tail.every((d) => Math.abs(d) < 0.5 * mdd)) {
    return { id: 'S12', severity: 'note', message: '静默平台：耐心窗计时 + 决策提示' }
  }
  return null
}

/** 熔断集合（§7.2：S2/S3/S4 命中或超时爆 ⇒ 立即停批 + 告警）。 */
export function fuseOf(
  hits: SentinelHit[],
  timeoutRate: number,
  timeoutGate = TIMEOUT_DFT,
): string | null {
  const trip = hits.find((h) => h.id === 'S2' || h.id === 'S3' || h.id === 'S4')
  if (trip) return trip.id
  if (timeoutRate > timeoutGate) return 'S-timeout'
  return null
}

export { mddPaired, mddUnpaired }
