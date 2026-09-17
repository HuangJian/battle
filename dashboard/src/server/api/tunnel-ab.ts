/** tunnel-ab.ts — M1 隧道 A/B 探针结果的只读派生（`tmp/tunnel-ab-*.json`）。
 *
 *  为什么放控制台：`plan/remote-wire-remediation.plan.md` §3.4 要求两条腿各跑 ≥2 个
 *  独立 run 才作数（墙钟类指标有 run-to-run 噪声地板）。数字埋在 JSON 里就等于没有
 *  —— 操作员需要一个「不翻日志就能读趋势」的地方。
 *
 *  数据源：`nn-training/remote/tunnel_ab_probe.py` 的 `--json-out` 产物（本机路径，
 *  与 iters 读 `tmp/<course>/` 同口径）。文件被 tmp 轮转清掉 = available:false 空态，
 *  不是错误。纯函数解析（`parseTunnelAb`）与磁盘读分离，便于单测喂字符串。
 */
import { readFileSync, readdirSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'

/** 单腿单方向的统计（探针 `_stats()` 的口径）。 */
export interface TunnelAbStat {
  n: number
  p50Sec: number
  p90Sec: number
  maxSec: number
  p50Mbps: number
}

/** 一行 = (腿, 方向)。方向是**关键列**：push 模式的真实大头是上行。 */
export interface TunnelAbRow {
  leg: string
  dir: 'up' | 'down'
  stat: TunnelAbStat
}

export interface TunnelAbRun {
  /** 文件名（形如 tunnel-ab-2.json），UI 用来指认哪一次运行。 */
  file: string
  /** 文件 mtime（epoch ms）→ UI 显示「多久前」。 */
  mtime: number
  /** 每发探测载荷字节数与发数（表头用）。 */
  bytes: number
  rounds: number
  rows: TunnelAbRow[]
  /** http2 vs quic 的 p50 倍率（quic ÷ http2；>1 = http2 更快）。缺任一腿 → null。
   *  这就是计划 §3.4 的判据本身，替操作员先算出来，免得对着秒数心算。 */
  speedup: { up: number | null; down: number | null }
}

export interface TunnelAbView {
  available: boolean
  /** 新的在前（读最近 N 次运行即可看出「连跑是否退化」）。 */
  runs: TunnelAbRun[]
  error?: string
}

function asStat(v: unknown): TunnelAbStat | null {
  if (!v || typeof v !== 'object') return null
  const s = v as Record<string, unknown>
  const num = (k: string): number | null =>
    typeof s[k] === 'number' && Number.isFinite(s[k] as number) ? (s[k] as number) : null
  const n = num('n')
  const p50 = num('p50_sec')
  const p90 = num('p90_sec')
  const max = num('max_sec')
  if (n === null || p50 === null || p90 === null || max === null) return null
  return { n, p50Sec: p50, p90Sec: p90, maxSec: max, p50Mbps: num('p50_mbps') ?? 0 }
}

/** 探针 JSON 文本 → 运行视图；形状不符（手改坏/半截文件）→ null（调用方跳过）。 */
export function parseTunnelAb(raw: string, file: string, mtime: number): TunnelAbRun | null {
  let doc: unknown
  try {
    doc = JSON.parse(raw)
  } catch {
    return null
  }
  if (!doc || typeof doc !== 'object') return null
  const d = doc as Record<string, unknown>
  const legs = d.legs
  if (!legs || typeof legs !== 'object') return null
  const rows: TunnelAbRow[] = []
  const byLegDir = new Map<string, number>()
  for (const [leg, lv] of Object.entries(legs as Record<string, unknown>)) {
    if (!lv || typeof lv !== 'object') continue
    for (const dir of ['up', 'down'] as const) {
      const st = asStat((lv as Record<string, unknown>)[dir])
      if (!st) continue
      rows.push({ leg, dir, stat: st })
      byLegDir.set(`${leg}.${dir}`, st.p50Sec)
    }
  }
  if (rows.length === 0) return null
  // 腿序固定：基线 → 协议腿（表里先看回环基线，再看 A/B）。
  const legRank = (l: string): number =>
    l === 'loopback' ? 0 : l === 'http2' ? 1 : l === 'quic' ? 2 : 3
  rows.sort(
    (a, b) => legRank(a.leg) - legRank(b.leg) || (a.dir === b.dir ? 0 : a.dir === 'up' ? -1 : 1),
  )
  const ratio = (dir: 'up' | 'down'): number | null => {
    const q = byLegDir.get(`quic.${dir}`)
    const h = byLegDir.get(`http2.${dir}`)
    if (q === undefined || h === undefined || h <= 0) return null
    return q / h
  }
  return {
    file,
    mtime,
    bytes: typeof d.bytes === 'number' ? d.bytes : 0,
    rounds: typeof d.rounds === 'number' ? d.rounds : 0,
    rows,
    speedup: { up: ratio('up'), down: ratio('down') },
  }
}

/** 读最近的探针结果（新→旧）。目录不存在/为空 = available:false 空态。 */
export function readTunnelAbRuns(limit = 5, dir = path.join(REPO_ROOT, 'tmp')): TunnelAbView {
  try {
    const files = readdirSync(dir)
      .filter((f) => f.startsWith('tunnel-ab-') && f.endsWith('.json'))
      .map((f) => {
        const p = path.join(dir, f)
        return { f, p, mtime: statSync(p).mtimeMs }
      })
      .sort((a, b) => b.mtime - a.mtime)
    // 先解析再截断：**不能**先 slice(0, limit)——最新那个文件恰好写坏（探针被 Ctrl-C）
    // 时，会连带把后面几次好运行一起挡掉，操作员看到空白却以为「没跑过」。
    const runs: TunnelAbRun[] = []
    for (const { f, p, mtime } of files) {
      const run = parseTunnelAb(readFileSync(p, 'utf8'), f, mtime)
      if (run) runs.push(run)
      if (runs.length >= limit) break
    }
    return { available: runs.length > 0, runs }
  } catch (e) {
    return { available: false, runs: [], error: e instanceof Error ? e.message : String(e) }
  }
}
