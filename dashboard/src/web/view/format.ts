/** format.ts — 时间 / 数值 / 文本格式化纯函数。 */
import { PairedCompare } from './metric-types'

// ────────────────────────── 纯函数：时间 ──────────────────────────

const pad2 = (x: number): string => String(x).padStart(2, '0')

/** 完整本机时区时间 'YYYY-MM-DD HH:MM:SS'（空格格式；历史锚点/字符串比较用）。 */
export function fmtFullTs(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

export function fmtTs(ms: number, now = Date.now()): string {
  const d = new Date(ms)
  const sameDay =
    d.getFullYear() === new Date(now).getFullYear() &&
    d.getMonth() === new Date(now).getMonth() &&
    d.getDate() === new Date(now).getDate()
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
  if (sameDay) return hm
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${hm}`
}

/** 完整 ISO（hover 详情用）。 */
export function fullIso(ms: number): string {
  return new Date(ms).toISOString()
}

/** 相对时间 'Xs 前' / '刚刚'。 */
export function fmtRel(ms: number, now = Date.now()): string {
  const diff = Math.max(0, Math.round((now - ms) / 1000))
  if (diff < 5) return '刚刚'
  if (diff < 60) return `${diff}s 前`
  if (diff < 3600) return `${Math.round(diff / 60)}m 前`
  return `${Math.round(diff / 3600)}h 前`
}

// ────────────────────────── 纯函数：数值/文本 ──────────────────────────

export function fmtPct(v: number | null | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—'
}

/** 每局平均：Σ / games，保留 digits 位小数；null → '-'。 */
export function fmtPerGameAvg(total: number | null | undefined, games: number, digits = 2): string {
  if (total == null) return '-'
  if (games <= 0) return total.toFixed(digits)
  return (total / games).toFixed(digits)
}

/** 指标表道具列：每局平均「拾取数/掉落数」。掉落数缺失 → `拾取/-`。 */
export function fmtLootPickDrop(
  collected: number | null | undefined,
  spawned: number | null | undefined,
  games: number,
): string {
  if (collected == null) return '-'
  const pick = fmtPerGameAvg(collected, games, 2)
  if (spawned == null) return `${pick}/-`
  return `${pick}/${fmtPerGameAvg(spawned, games, 2)}`
}

/** 配对裁判 verdict 文案：灰是正常态（100 对下 99% 时间证据不够），不是故障。 */
export function pairedVerdictText(v: 'up' | 'down' | 'flat'): string {
  if (v === 'up') return '显著涨'
  if (v === 'down') return '显著跌'
  return '方向对，证据不够'
}

/** 配对裁判单行文案（Hero 趋势旁）：`vs开腿 +7.0pp p=0.31 (21/14,n=100) 方向对，证据不够`。 */
export function fmtPaired(c: PairedCompare | null, label: string): string {
  if (!c) return `${label} 数据不足`
  const sign = c.deltaPp > 0 ? '+' : ''
  return (
    `${label} ${sign}${c.deltaPp.toFixed(1)}pp p=${c.p.toFixed(2)} ` +
    `(${c.b01}/${c.b10},n=${c.paired}) ${pairedVerdictText(c.verdict)}`
  )
}

/** 配对列头 hover 文案（大白话；MetricsTable 与 Hero 共用同一份，防两处分化）。 */
export const PAIRED_COL_TITLES = {
  b01: '基线输、新权重赢的局数——新学会的本事，涨没涨看它',
  b10: '基线赢、新权重输的局数——学费（遗忘/漂移），只看涨幅会漏掉它',
  p: '假设没进步、纯靠运气搞出这份比分的概率；<0.05才算数，灰色=证据不够',
  delta: '净涨幅=b01−b10；不告诉你有多硬——7-0和21-14都是+7，硬度看p',
} as const

/** p 值徽章色：显著涨绿/显著跌红/其余灰（灰是正常态，不是故障）。 */
export function pairedTone(v: PairedCompare['verdict']): 'g' | 'r' | 'gray' {
  if (v === 'up') return 'g'
  if (v === 'down') return 'r'
  return 'gray'
}

/** 表格配对列的基线轮：任一非空 pairedVsFirst 的 baseIter（全空 → null）。 */
export function pairedBaselineOf(vals: Array<PairedCompare | null | undefined>): number | null {
  for (const v of vals) if (v) return v.baseIter
  return null
}

/** 过拟合列头 hover（大白话；Hero EvalTable 与 MetricsTable 共用）。 */
export const OVERFIT_COL_TITLE =
  '过拟合 gap = 锚点胜率 − 轮转胜率（百分点）。' +
  '锚点=固定老种子（熟题），轮转=周期性换的新种子（生题）。' +
  '正值越大越可疑：熟题分高、生题分低 = 可能背题了。' +
  '单轮 ≥5pp 才值得警惕，且要看近 3 轮是否持续；' +
  '未开双轨 / 无轮转数据时显示 —。'

/** 过拟合徽章色：≥8 红 / ≥5 黄 / 其余灰（含负 gap=轮转更高，正常）。 */
export function overfitTone(gapPp: number): 'r' | 'y' | 'gray' {
  if (gapPp >= 8) return 'r'
  if (gapPp >= 5) return 'y'
  return 'gray'
}

/** 过拟合 gap 展示：带符号 1 位小数 + pp（如 `+3.2pp` / `-8.0pp`）。 */
export function fmtOverfitGap(gapPp: number): string {
  return (gapPp > 0 ? '+' : '') + gapPp.toFixed(1) + 'pp'
}

/** 过拟合单元格 hover：有双轨时展开锚/轮读数；否则缺数据说明。 */
export function overfitCellTitle(e: {
  anchorWr?: number | null
  rotorWr?: number | null
  overfitGapPp?: number | null
}): string {
  if (e.overfitGapPp == null) return '未开双轨，或本轮无轮转数据（it0 基线恒 —）'
  if (e.anchorWr != null && e.rotorWr != null) {
    return (
      `锚点 ${fmtPct(e.anchorWr)} − 轮转 ${fmtPct(e.rotorWr)} = ${fmtOverfitGap(e.overfitGapPp)}` +
      '；≥5pp 且近 3 轮持续才报警'
    )
  }
  return '过拟合 gap（锚点 − 轮转）'
}

export function fmtBytes(b: number | null | undefined): string {
  if (typeof b !== 'number' || !Number.isFinite(b)) return '—'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

/** 阶段耗时三元组（rollout=纯采集 · ppo=真训练 · net=网络/传输/排队）。 */
export interface PhaseSecs {
  rollout: number
  ppo: number
  net: number
}

/**
 * 从 IterRow 拆出准确阶段耗时。
 * - rollout：优先 pureCollectSec。口径（2026-09-19 用户定义，边分发边开采）：
 *   权重就绪**开始分发** → 样本采集完毕可交 PPO（含与采集重叠的分发墙钟）。
 *   volume 多波：loop 层 combine_reports 聚合 min(分发起点)→max(样本齐)。
 *   旧账本 pure_collect = 末局结算−权重下发完毕；无此键时回退 rolloutSec。
 * - ppo：优先 ppoCloudSec（云端自报真训练秒）；旧账本/本机回退 ppoSec。
 * - net：权重下发（distPhaseSec）+ 远端往返超出真训练的部分（ppoSec−ppoCloudSec）。
 *   本机/流式（ppoCloudSec 缺失或 == ppoSec）时 ppo 侧净开销为 0。
 */
export function phaseSecs(r: {
  rolloutSec: number
  ppoSec: number
  pureCollectSec?: number | null
  ppoCloudSec?: number | null
  distPhaseSec?: number | null
}): PhaseSecs {
  const rollout = r.pureCollectSec != null ? r.pureCollectSec : r.rolloutSec
  const ppo = r.ppoCloudSec != null ? r.ppoCloudSec : r.ppoSec
  const dist = r.distPhaseSec ?? 0
  const ppoNet = r.ppoCloudSec != null ? Math.max(0, r.ppoSec - r.ppoCloudSec) : 0
  return { rollout, ppo, net: dist + ppoNet }
}

/**
 * 阶段耗时展示：`120/80/15s`（rollout/ppo/net，整秒）。
 * rollout = 权重开始分发→样本齐（2026-09-19 用户口径，边分发边开采）。
 */
export function fmtPhaseSecs(p: PhaseSecs): string {
  return `${p.rollout.toFixed(0)}/${p.ppo.toFixed(0)}/${p.net.toFixed(0)}s`
}

/** 阶段耗时 hover：把三段拆开写清楚，避免再把网络算进训练。 */
export function phaseSecsTitle(p: PhaseSecs): string {
  return (
    `rollout 权重分发→样本齐 ${p.rollout.toFixed(0)}s · ` +
    `ppo 真训练 ${p.ppo.toFixed(0)}s · ` +
    `net 网络/排队 ${p.net.toFixed(0)}s`
  )
}

/** 剥离文本开头的 ISO 时间戳前缀（sampler-agent lastError 的 `new Date().toISOString()` 前缀）。 */
export function stripIsoPrefix(text: string): string {
  return text.replace(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?\s*/, '')
}
