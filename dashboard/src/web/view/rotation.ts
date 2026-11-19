/** rotation.ts — 采样子集（窗口）读数的**展示纯函数**（plan/it-stage-ratation §9.3）。
 *
 *  两处渲染器共用这一份：`/metrics` 完整表的两列（窗口 / 各关进度）与「采集窗口」面板
 *  （`app/panels/StageRotationPanel.tsx`）。与 `metric-columns.ts` 同一条纪律：**文案只写一次**
 *  —— 列上的短文案与面板上的明细如果各写一遍，迟早对不上，而这里对不上意味着
 *  「表说达标 11/12、面板说 12/12」这种没人能查的账。
 *
 *  纯数据 + 纯函数（无 JSX / 无 IO），与其它 `view/` 模块同规：服务端与客户端共用。
 */

import type { IterRotation, IterVolume, IterVolumeStage } from './metric-types'

/** 关号清单 → 压缩文字（`[0,1,2,5]` ⇒ `0-2,5`；非连续关号 `2000-2004,2030-2034` 照旧）。 */
export function stageRangeText(stages: readonly number[]): string {
  if (stages.length === 0) return ''
  const sorted = [...new Set(stages)].sort((a, b) => a - b)
  const parts: string[] = []
  let start = sorted[0] as number
  let prev = start
  const flush = (): void => {
    parts.push(start === prev ? String(start) : `${start}-${prev}`)
  }
  for (const s of sorted.slice(1)) {
    if (s === prev + 1) {
      prev = s
      continue
    }
    flush()
    start = s
    prev = s
  }
  flush()
  return parts.join(',')
}

/** 窗口一格文字：`12/35`（窗口关数 / `--stages` 全关数）；无读数 ⇒ `—`。 */
export function windowText(r: IterRotation | null | undefined): string {
  return r ? `${r.window.length}/${r.all}` : '—'
}

/** 窗口 hover 口径：模式 / 每轮关数 / pinned / 关号清单。 */
export function windowTitle(r: IterRotation | null | undefined): string {
  if (!r) return '账本没有窗口读数（未开轮换，或该轮是旧账本行）'
  const pin = r.pinned.length > 0 ? `；pinned 每轮恒采 ${stageRangeText(r.pinned)}` : ''
  return (
    `本轮采样子集 ${r.mode}：${r.window.length}/${r.all} 关（per_it=${r.perIt}${pin}）；` +
    `关号 ${stageRangeText(r.window)}`
  )
}

/** 达标关数（`collected ≥ 达标线`）；账本缺 `quota` ⇒ null（不编一个总数）。 */
export function metStages(v: IterVolume | null | undefined): number | null {
  if (!v || v.quota === null) return null
  return v.stages.filter((s) => s.collected >= (s.quota ?? 0)).length
}

/** 各关进度一格文字：`达标 11/12`；无读数 / 缺 quota ⇒ `—`。 */
export function volumeText(v: IterVolume | null | undefined): string {
  const met = metStages(v)
  if (!v || met === null) return '—'
  return `达标 ${met}/${v.stages.length}`
}

/** 各关进度 hover：逐关 `关 收到/达标线（缺 n · est e）`。 */
export function volumeTitle(v: IterVolume | null | undefined): string {
  if (!v) return '该轮账本没有分关采集账（非动态采集轮 / 云机腿）'
  const parts = v.stages.map((s) => {
    const est = s.estS !== null ? ` · est ${s.estS}` : ''
    if (s.quota === null) return `${s.stage}: ${s.collected}${est}`
    const gap = s.shortfall ? ` · 缺 ${s.shortfall}` : ''
    return `${s.stage}: ${s.collected}/${s.quota}${gap}${est}`
  })
  return `本轮逐关采集账${v.rule ? `（${v.rule}）` : ''}：${parts.join(' · ')}`
}

/** 采集健康判据（用户 2026-11-14：「健康就显示一个 badge，点开才看明细」）。
 *
 *  · `ok`      —— 逐关账在手、每关 `shortfall ≤ 0`、没触单关硬顶；
 *  · `gap`     —— 任何一关有缺口，或触了硬顶（「顶到了」不是「快好了」）；
 *  · `unknown` —— 没分关账 / 达标线或缺口未知（不可知 ≠ 健康，也 ≠ 有病）。 */
export type RotationHealth = 'ok' | 'gap' | 'unknown'

export function rotationHealth(row: {
  volume?: IterVolume | null
  capped?: boolean | null
}): RotationHealth {
  const v = row.volume ?? null
  if (!v || v.stages.length === 0) return 'unknown'
  if (row.capped) return 'gap'
  let unknown = v.quota === null
  for (const s of v.stages) {
    if (s.quota === null || s.shortfall === null) {
      unknown = true
      continue
    }
    if (s.shortfall > 0) return 'gap'
  }
  return unknown ? 'unknown' : 'ok'
}

/** 健康 badge 文案：`采集健康 达标 12/12` / `采集有缺口 达标 9/12` / `采集读数不全`。
 *  缺口是「顶出来的」时直接说「触硬顶」——那时达标数看着漂亮，原因不在没采够。 */
export function healthText(row: { volume?: IterVolume | null; capped?: boolean | null }): string {
  const h = rotationHealth(row)
  if (h === 'unknown') return '采集读数不全'
  const total = row.volume?.stages.length ?? 0
  const met = metStages(row.volume)
  const tail = met === null ? '' : ` 达标 ${met}/${total}`
  if (h === 'ok') return `采集健康${tail}`
  return row.capped ? '采集有缺口 触硬顶' : `采集有缺口${tail}`
}

/** 逐关一格（chip）的 hover 口径：收到 / 达标线 / 缺口 / 局数（硬顶）/ est。 */
export function stageTitle(s: IterVolumeStage): string {
  const parts = [`关 ${s.stage}：收到 ${s.collected}`]
  parts.push(s.quota === null ? '达标线未知' : `达标线 ${s.quota}`)
  if (s.shortfall !== null && s.shortfall > 0) parts.push(`缺 ${s.shortfall}`)
  if (s.games !== null) {
    parts.push(`局 ${s.games}${s.capGames !== null ? `/${s.capGames}` : ''}`)
  }
  if (s.estS !== null) parts.push(`est ${s.estS}`)
  return parts.join(' · ')
}

/** 一轮在面板上的一句话读数（窗口 + 达标 + 触顶）。 */
export function roundSummaryText(row: {
  rotation?: IterRotation | null
  volume?: IterVolume | null
  capped?: boolean | null
}): string {
  const parts: string[] = []
  const rot = row.rotation ?? null
  if (rot) parts.push(`窗口 ${windowText(rot)} 关（${stageRangeText(rot.window)}）`)
  const vol = row.volume ?? null
  if (vol) parts.push(volumeText(vol))
  if (row.capped) parts.push('触单关硬顶')
  return parts.join(' · ')
}
