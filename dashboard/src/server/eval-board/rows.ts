/** rows.ts — 账本逐局行的**增量读**（按分片字节偏移，2026-09-22）。
 *
 * `store.loadRows` 每次把 `games/*.jsonl` 全读 + 全解析；而它在 `buildEvalBoardView` **缓存
 * 未命中**路径上（30s TTL 过期 / `?fresh=1` / 动作后重算），`ladderTick` 还**每门课各调一次**
 * （N 课 = N 倍）——本机 2 万行实测单次 69ms、整视图冷算 86ms，且随账本线性增长。
 *
 * 这里只做一件事：**把「已解析的行」按分片留在进程内，下次只处理变了的部分**。
 *   · 分片未动 → 零 IO、零解析（连返回的数组都是同一个实例）；
 *   · 分片只增长（append-only 账本的常态）→ 只读新增尾巴、只解析新行；
 *   · 分片被截断/原地改写（体积变小、inode 变、同尺寸被动过）→ 那一片整片重解析（安全侧）。
 * 判据与偏移规则全部复用 `tail-read.ts`（与 `eval_log` 入账同一套：按「实际消费了什么」推进）。
 *
 * 语义与 `store.loadRows` 一致（坏行跳过、只读 `games/*.jsonl`），差别只有两点：**行序**由
 * 分片名排序决定（`YYYY-MM.jsonl` ⇒ 月份序，比 `readdirSync` 的返回顺序**更**确定），以及
 * 返回的是**共享只读数组**（调用方不得就地改动——要排序先 `[...rows]`）。
 *
 * 顺带维护一份 **按课程的行指纹**（`rowsSignatureFor`）：视图缓存靠它判「这门课的数据变了没」。
 * 指纹只在新解析的行上滚动更新（增量），所以判「没变」也是零成本。
 */

import { readdirSync } from 'fs'
import path from 'path'
import { gamesDir, type EvalGameRow } from '../../evalboard/store'
import { readTailDrained, type TailPos } from './tail-read'

/** 单课程在某分片内的行指纹：`n` = 行数，`h` = 跨行文本的滚动 FNV-1a。 */
interface CourseDigest {
  n: number
  h: number
}

/** 单个分片的缓存状态。 */
interface ShardState {
  /** 已消费位置（`tail-read` 的偏移 + 上次 stat 快照）。 */
  pos: TailPos
  /** 该分片已解析的行（文件序）。 */
  rows: EvalGameRow[]
  /** 课程 → 该课程在本分片内的行指纹（增量维护；本分片重读时整体重建）。 */
  byCourse: Map<string, CourseDigest>
}

interface RootState {
  /** 分片名 → 状态（只含当前存在的分片）。 */
  shards: Map<string, ShardState>
  /** 合并后的行（**按分片名排序** + 文件序）；未变动时原样复用同一个实例。 */
  merged: EvalGameRow[]
}

/** 每个数据根一份（键 = `dataRoot`）。store 是 append-only 账本，控制台无清库路径。 */
const roots = new Map<string, RootState>()

/** 当前分片名（排序 ⇒ 行序确定；`YYYY-MM.jsonl` 排序即月份序）。 */
function shardNames(dataRoot: string): string[] {
  try {
    return readdirSync(gamesDir(dataRoot))
      .filter((f) => f.endsWith('.jsonl'))
      .sort()
  } catch {
    return []
  }
}

const FNV_BASIS = 2166136261

/** 把一行原始文本折进某课程的指纹（FNV-1a 32-bit；行数在 `n` 上）。 */
function foldLine(d: CourseDigest, line: string): void {
  let h = d.h
  for (let i = 0; i < line.length; i++) {
    h = Math.imul(h ^ line.charCodeAt(i), 16777619)
  }
  d.h = h >>> 0
  d.n += 1
}

/** 逐行解析（坏行跳过，与 `store.loadRows` 同规）+ 同时滚动维护按课程指纹。 */
function parseLines(lines: string[], byCourse: Map<string, CourseDigest>): EvalGameRow[] {
  const out: EvalGameRow[] = []
  for (const line of lines) {
    if (!line.trim()) continue
    let row: EvalGameRow
    try {
      row = JSON.parse(line) as EvalGameRow
    } catch {
      continue // 坏行跳过（S8 在批次级另行拒绝）
    }
    out.push(row)
    const course = typeof row.course === 'string' ? row.course : ''
    const d = byCourse.get(course) ?? { n: 0, h: FNV_BASIS }
    foldLine(d, line)
    byCourse.set(course, d)
  }
  return out
}

/** 按序拼接某课程（或缺省 = **全部课程**）在各分片内的指纹。 */
function digestText(s: ShardState, course: string): string {
  if (course) {
    const d = s.byCourse.get(course)
    return d ? `${d.n}:${d.h}` : '-'
  }
  const all = [...s.byCourse.entries()]
    .sort((a, b) => (a[0] < b[0] ? -1 : 1))
    .map(([c, d]) => `${c}:${d.n}:${d.h}`)
  return all.length > 0 ? all.join(',') : '-'
}

/**
 * 某数据根下**某课程**（缺省 = 全部课程，对应 `course=''` 的聚合视图）的行指纹。
 *
 * **自带 stat 级刷新**（内部先走一遍 `loadRowsCached` 让状态追上盘；未变时无解析开销），
 * 所以调用方不需要（也不应该）自己先读一遍 —— 忘了那一步就会拿到上一拍的指纹。
 */
export function rowsSignatureFor(dataRoot: string, course = ''): string {
  loadRowsCached(dataRoot)
  const state = roots.get(dataRoot)
  if (!state) return 'cold'
  return [...state.shards.keys()]
    .sort()
    .map((n) => `${n}=${digestText(state.shards.get(n)!, course)}`)
    .join('|')
}

/**
 * 读某数据根下全部逐局行（**增量**）。返回数组是共享只读视图：未变动时是同一个实例。
 *
 * 这是 `store.loadRows` 的会话内缓存版；需要「刚从盘上重读一遍」的场景（CLI / 对账）仍用
 * `store.loadRows`。
 */
export function loadRowsCached(dataRoot: string): EvalGameRow[] {
  const names = shardNames(dataRoot)
  let state = roots.get(dataRoot)
  if (!state) {
    state = { shards: new Map(), merged: [] }
    roots.set(dataRoot, state)
  }
  let changed = false
  const present = new Set(names)
  for (const name of state.shards.keys()) {
    if (!present.has(name)) {
      state.shards.delete(name) // 分片被删 → 它的行也随之消失
      changed = true
    }
  }
  const ordered: ShardState[] = []
  for (const name of names) {
    const prev = state.shards.get(name)
    const tail = readTailDrained(path.join(gamesDir(dataRoot), name), prev?.pos)
    // 重读 = 指纹整片重建；追加 = 折进已有的指纹
    const byCourse = tail.reset ? new Map<string, CourseDigest>() : new Map(prev?.byCourse ?? [])
    const fresh = parseLines(tail.lines, byCourse)
    // 一个字节都没动过（偏移/体积/时间全同，且不是重读）→ 整片原样复用
    if (
      prev &&
      !tail.reset &&
      tail.next.offset === prev.pos.offset &&
      tail.next.size === prev.pos.size &&
      tail.next.mtimeMs === prev.pos.mtimeMs
    ) {
      ordered.push(prev)
      continue
    }
    // 重读 = 新行**取代**旧行（截断/改写后旧行已不存在）；否则追加到旧行之后
    const next: ShardState = {
      pos: tail.next,
      rows: tail.reset ? fresh : [...(prev?.rows ?? []), ...fresh],
      byCourse,
    }
    state.shards.set(name, next)
    ordered.push(next)
    changed = true
  }
  if (changed) state.merged = ordered.flatMap((s) => s.rows)
  return state.merged
}

/** 仅供测试/诊断：清空会话内缓存（换根或要「从盘上重读」时用）。 */
export function resetRowsCache(): void {
  roots.clear()
}
