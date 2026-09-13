/** rows.ts — 指标行派生：最新轮 / 色调 / 过滤分组 / 通用排序。 */
import { EvalSummary, IterRow } from './metric-types'

// ────────────────────────── 纯函数：最新轮 / 色调（顶栏状态条 + 指标卡共用） ──────────────────────────

/** 最新一轮迭代（按 iter 最大；空 = null）。固定头部状态条的单一数据口径。 */
export function latestRow(iters: IterRow[]): IterRow | null {
  let best: IterRow | null = null
  for (const r of iters) if (!best || r.iter > best.iter) best = r
  return best
}

export type ValueTone = 'g' | 'y' | 'r'

export function winTone(v: number): ValueTone {
  if (v >= 0.3) return 'g'
  if (v >= 0.1) return 'y'
  return 'r'
}

export function klTone(v: number): ValueTone {
  if (v > 0.05) return 'r'
  if (v > 0.02) return 'y'
  return 'g'
}

export function retTone(v: number): ValueTone {
  if (v > -0.5) return 'g'
  if (v > -1.0) return 'y'
  return 'r'
}

// ────────────────────────── 纯函数：指标行过滤（DS-U1 13 列 + eval 子行） ──────────────────────────

export type IterFilter = 'all' | 'rollout' | 'eval'

/** 主行 + eval 子行 分组（时间倒序 = 新轮在前，与旧页一致）。 */
export function iterGroups(
  rows: IterRow[],
): Array<{ iter: number; main: IterRow; eval: EvalSummary | null }> {
  return [...rows]
    .sort((a, b) => b.iter - a.iter)
    .map((r) => ({ iter: r.iter, main: r, eval: r.evalData ?? null }))
}

export function filterGroups(
  groups: ReturnType<typeof iterGroups>,
  mode: IterFilter,
): Array<{ iter: number; main: IterRow; eval: EvalSummary | null }> {
  if (mode === 'all') return groups
  if (mode === 'eval') return groups.filter((g) => g.eval !== null)
  return groups.filter((g) => g.eval === null)
}

/** Hero「最新 6 轮完整指标」主表行：真实迭代（iter>0）倒序前 6。
 *  it0 合成行（bc 权重基线，console/iters.ts 合成）只有干净评估，rollout 派生字段是
 *  NaN 缺口——主表单元格直接 `.toFixed()` 会渲染出 "NaN" 垃圾行，故只收真实迭代
 *  （eval 视图与 MetricsTable 各自已处理 it0：前者只渲染 eval 子行，后者跳过主行）。 */
export function heroMainRows(iters: IterRow[]): IterRow[] {
  return iters
    .filter((r) => r.iter > 0)
    .sort((a, b) => b.iter - a.iter)
    .slice(0, 6)
}

// ────────────────────────── 纯函数：通用排序 / 过滤 ──────────────────────────

export type SortDir = 'asc' | 'desc'

/** 通用行排序：字符串 localeCompare，数值/可 parseFloat 优先（data-v 语义保留）。 */
export function sortRows<T>(rows: T[], key: keyof T, dir: SortDir): T[] {
  if (rows.length <= 1) return rows
  const n = 1 // 逗号前保持较少的返回分支（纯函数）；实现见下
  void n
  const mult = dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * mult
    if (av === null || av === undefined) return 1
    if (bv === null || bv === undefined) return -1
    const an = typeof av === 'string' || typeof av === 'number' ? Number(av) : Number.NaN
    const bn = typeof bv === 'string' || typeof bv === 'number' ? Number(bv) : Number.NaN
    const cmp =
      !Number.isNaN(an) && !Number.isNaN(bn) ? an - bn : String(av).localeCompare(String(bv))
    return cmp * mult
  })
}

export function keywordMatch(row: Record<string, unknown>, keys: string[], kw: string): boolean {
  const q = kw.trim().toLowerCase()
  if (!q) return true
  return keys.some((k) => {
    const v = row[k]
    if (v === null || v === undefined) return false
    return String(v).toLowerCase().includes(q)
  })
}
