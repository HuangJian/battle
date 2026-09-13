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

export function fmtBytes(b: number | null | undefined): string {
  if (typeof b !== 'number' || !Number.isFinite(b)) return '—'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

/** 剥离文本开头的 ISO 时间戳前缀（sampler-agent lastError 的 `new Date().toISOString()` 前缀）。 */
export function stripIsoPrefix(text: string): string {
  return text.replace(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?\s*/, '')
}
