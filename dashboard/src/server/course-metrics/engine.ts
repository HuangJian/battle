/** engine.ts — 转存/冷存的**纯函数**内核（零 IO；IO 薄壳在 `mirror.ts` / `compact.ts`）。
 *
 *  为什么把判定剥出来：游标/锚点/复位/预算/打包阈值这些分支一旦和 `fs` 混在一起，用例只能靠
 *  真目录树构造，边界（半行、截短、同尺寸改写、世代冲突）很难钉住。这里每个判定都是
 *  「输入 → 有限状态」，`mirror.ts` 只负责按判定读写。
 *
 *  语义来源：`plan/course-metrics-persistence.plan.md` §3.2（A/B/C）与评审 F5/F9。
 */

import { ANCHOR_BYTES } from './meta'
import type { MirrorFileMeta } from './meta'

export interface SourceStat {
  size: number
  mtimeMs: number
}

export type AppendPlan =
  | { kind: 'skip' }
  | { kind: 'reset'; reason: string }
  | { kind: 'append'; from: number; to: number }

/** 最后一个 `\n` 之后的位置（= 完整行边界）。没有换行 ⇒ 0。 */
export function completeEnd(buf: Uint8Array, length = buf.length): number {
  const n = Math.min(length, buf.length)
  for (let i = n - 1; i >= 0; i--) {
    if (buf[i] === 10) return i + 1
  }
  return 0
}

/** `[0, end)` 区域尾部至多 64B 的十六进制（0 字节 ⇒ 空串）。 */
export function anchorHex(buf: Uint8Array, end: number): string {
  if (end <= 0) return ''
  const from = Math.max(0, end - ANCHOR_BYTES)
  return Buffer.from(buf.subarray(from, Math.min(end, buf.length))).toString('hex')
}

export function countNewlines(buf: Uint8Array, start = 0, end = buf.length): number {
  let n = 0
  for (let i = Math.max(0, start); i < Math.min(end, buf.length); i++) {
    if (buf[i] === 10) n++
  }
  return n
}

/**
 * append 型的三种走向。`end` = 源当前**完整行**边界（调用方从文件尾窗口算出来）。
 *
 *  ① 源变短 ⇒ reset（重写/换 run）；
 *  ② size+mtime 都没变 ⇒ skip（**不读正文**，稳态零成本）；
 *  ③ 锚点失配 ⇒ reset（同尺寸重写靠这条兜住）；
 *  ④ 其余 ⇒ append [bytes, end)；`end <= bytes` = 半行还没写完 ⇒ skip。
 */
export function decideAppend(
  prev: MirrorFileMeta | undefined,
  src: SourceStat,
  srcAnchor: string,
  end: number,
): AppendPlan {
  if (prev && prev.mode === 'append') {
    if (src.size < prev.bytes) return { kind: 'reset', reason: 'source-shrunk' }
    if (src.size === prev.bytes && src.mtimeMs === prev.srcMtime) return { kind: 'skip' }
    if (prev.bytes > 0 && srcAnchor !== prev.anchor) {
      return { kind: 'reset', reason: 'anchor-mismatch' }
    }
    if (end <= prev.bytes) return { kind: 'skip' }
    return { kind: 'append', from: prev.bytes, to: end }
  }
  if (end <= 0) return { kind: 'skip' }
  return { kind: 'append', from: 0, to: end }
}

/** 重写型：sha 相同 ⇒ 不写盘（只刷新 meta 的尺寸/mtime）。 */
export function decideRewrite(prev: MirrorFileMeta | undefined, sha: string): 'skip' | 'write' {
  return prev && prev.mode === 'rewrite' && prev.sha256 === sha ? 'skip' : 'write'
}

export interface PackDecisionInput {
  /** `max(meta.files[*].srcMtime)`；0 = 从未同步过 ⇒ 不打包。 */
  lastSourceActivity: number
  now: number
  days: number
  /** `tmp/<课>/` 是否存在。**存在就不冷**（评审 F5：否则每拍重打包）。 */
  tmpExists: boolean
  /** 当前 `<课>.tar.gz` 或最近世代包的 mtimeMs（没有 ⇒ null）。 */
  newestArchiveMtime: number | null
}

/** 冷存触发判据（plan §3.2-B v3）。 */
export function decidePack(i: PackDecisionInput): boolean {
  if (i.tmpExists) return false
  if (i.lastSourceActivity <= 0) return false
  if (!(i.days > 0)) return false
  if (i.now - i.lastSourceActivity <= i.days * 86_400_000) return false
  if (i.newestArchiveMtime != null && i.newestArchiveMtime >= i.lastSourceActivity) return false
  return true
}

/** `20261018T120000`（UTC、ASCII——日志与文件名都不过本地化）。 */
export function stampFor(now: number): string {
  return new Date(now).toISOString().replace(/[-:]/g, '').slice(0, 15)
}

/** 冷存包名。 */
export function archiveName(course: string): string {
  return `${course}.tar.gz`
}

/** 旧世代冷存包名。 */
export function generationName(course: string, ts: string): string {
  return `${course}.${ts}.tar.gz`
}

/** 从文件名解析课程与世代（`.tar.gz` / `.<ts>.tar.gz`；课程名本身可含点）。 */
export function parseArchiveFileName(file: string): { course: string; ts: string | null } | null {
  if (!file.endsWith('.tar.gz')) return null
  const base = file.slice(0, -'.tar.gz'.length)
  // 同秒冲突时世代名会带 `-<n>` 后缀（`uniqueGeneration`），解析必须认得它，
  // 否则它会被当成另一门课 ⇒ 永不修剪（2026-10-18 用例抓到）。
  const m = /^(.*)\.(\d{8}T\d{6})(?:-(\d+))?$/.exec(base)
  if (m && m[1]) return { course: m[1], ts: m[2] ?? null }
  return base ? { course: base, ts: null } : null
}

/** 世代列表按新→旧（ts 是 `YYYYMMDDTHHmmss`，字典序 = 时间序）。 */
export function sortGenerations(files: string[]): string[] {
  return [...files].sort().reverse()
}

/** `--course` 参数解析：`*`/空 = 全部（CLI 与 ticker 共用）。 */
export function wantsCourse(filter: string | undefined, course: string): boolean {
  return !filter || filter === '*' || filter === course
}

/** 预算：0 = 不限（plan §3.5 的 `--budget 0` 就是这条）；负值按 0 处理。 */
export function budgetLeft(budget: number, used: number): number {
  if (!Number.isFinite(budget) || budget <= 0) return Number.POSITIVE_INFINITY
  return Math.max(0, budget - used)
}
