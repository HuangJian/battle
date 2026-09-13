/** eval-export.ts — 评估页标签与 CSV 导出（RFC4180 + UTF-8 BOM）。 */
import { EvalIterRow, EvalLadderRow } from './eval-types'

export function rungLabel(
  row: Pick<EvalLadderRow, 'rung' | 'lives' | 'starLevel' | 'stageBrief' | 'dimension'>,
): string {
  const b = row.stageBrief
  const enemyStr =
    b.enemies.length === 0
      ? `${b.enemyCount || 0}敌`
      : `${b.enemies.length}敌(${enemyBreakdown(b.enemies)})`
  return (
    `${row.rung} · ${enemyStr} · ${row.lives}命 · ${row.starLevel}★ · ` +
    `${b.hasTerrain ? '有地形' : '无地形'} · ${b.hasBase ? '有基地(可摧毁)' : '无基地'}｜${row.dimension}`
  )
}

/** R9 CSV：UTF-8 BOM（否则 Excel 中文表头乱码）。 */
export const CSV_BOM = '\uFEFF'

/** RFC 4180 转义：含 , " 换行时加引号；内部 " → ""。 */
export function csvEscape(v: string): string {
  return /[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v
}

/** 一行 CSV（列序即调用方给的列序）。 */
export function csvRow(cells: string[]): string {
  return cells.map(csvEscape).join(',')
}

/** CSV 数值列（value 返回 null = 空单元格，不写 0）。 */
export interface EvalCsvCol {
  header: string
  value: (row: EvalIterRow) => number | null
}

/**
 * R9 CSV 构建（纯函数，可单测）：首列 `<course> / iter`，表头拍平 = `<rung> / <指标>`。
 * 数值用 String()（无千分位、无科学计数的小数不会出现）。导出范围由调用方按可见列给。
 */
export function buildEvalCsv(rows: EvalIterRow[], cols: EvalCsvCol[]): string {
  const lines = [csvRow(['course / iter', ...cols.map((c) => c.header)])]
  for (const r of rows) {
    lines.push(
      csvRow([
        `${r.course} / ${r.kind === 'god' ? 'God' : `it${r.iter}`}`,
        ...cols.map((c) => {
          const v = c.value(r)
          return v === null || v === undefined ? '' : String(v)
        }),
      ]),
    )
  }
  return `${CSV_BOM}${lines.join('\r\n')}\r\n`
}

/** 敌型构成：全同 ⇒ `全kind`；多型 ⇒ `Nkind+Mkind`（首现序）。 */
export function enemyBreakdown(enemies: string[]): string {
  const counts = new Map<string, number>()
  for (const k of enemies) counts.set(k, (counts.get(k) ?? 0) + 1)
  const parts = [...counts.entries()]
  if (parts.length === 1) return `全${parts[0]![0]}`
  return parts.map(([k, n]) => `${n}${k}`).join('+')
}
