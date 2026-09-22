/** tail-read.ts — 「按字节偏移读文件新增尾巴」的唯一实现（2026-09-22）。
 *
 * 两份调用方共用它：`ingest.ts`（课程 `eval_log.jsonl` → 账本入账）与 `rows.ts`
 * （账本 `games/*.jsonl` → 视图行）。两份都是**只追加的 jsonl**，都宁可少读不可读错。
 *
 * 一条判据贯穿全篇：**按「实际消费了什么」推进偏移，不按「读了多长」**。
 */

import { closeSync, openSync, readSync, statSync, type Stats } from 'fs'

/** 单次最多读的新增字节数（防一次读进整份巨文件；多余的留给下一拍）。 */
export const TAIL_MAX_BYTES = 4 * 1024 * 1024

/** 读位置（上次读取时刻的 stat 快照 + 已消费字节数）。 */
export interface TailPos {
  /** 已消费到第几字节（**最后一个换行之后**；末尾半行不算）。 */
  offset: number
  /** 文件身份（Windows 上恒 0 → 该判据自动退化为只看体积/时间）。 */
  ino: number
  /** 上次读到的体积。 */
  size: number
  /** 上次读到的 mtimeMs。 */
  mtimeMs: number
}

/** 一次「读新增尾巴」的结果。 */
export interface TailRead {
  /** 本次新读到的完整行（不含末尾那个还没写完的半行）。 */
  lines: string[]
  /** 下次从这里接着读。 */
  next: TailPos
  /** 是否从头重读（首次，或偏移已不可信）。 */
  reset: boolean
}

const EMPTY_POS: TailPos = { offset: 0, ino: 0, size: 0, mtimeMs: 0 }

/** 空位置（首次读取的 `prev`；`reset` 由调用侧的「没有 prev」决定）。 */
export function emptyPos(): TailPos {
  return EMPTY_POS
}

/**
 * 读一份 jsonl 的**新增尾巴**（决定读哪一段纯由 stat + prev 决定，单测直接钉它）。
 *
 * - `prev` 为空（首次）或偏移**不可信** → 从头读（`reset = true`）；否则只读 `[prev.offset, size)`；
 * - **只消费到最后一个换行**：写方可能在行中间 flush，半行留给下一拍（现在切下来会被当成坏
 *   JSON 丢掉，而它下一拍就是完整的一行）；
 * - 窗口读满还找不到换行 = 单行比窗口还长（异常数据）→ 整窗消费，否则永远读不动；
 * - 偏移不可信的判据：体积**变小**（截断）/ inode 变了 / **体积没变却被动过**（同尺寸原地
 *   改写，无从知道改了哪一段）。末一条是安全侧选择：宁可整份重读（调用侧的去重保证幂等），
 *   也不拿一个可能指错位置的偏移去读。
 */
/**
 * 读**整条新增尾巴**（把 `readTail` 的单窗口循环到文件末尾）。
 *
 * 为什么必须循环：`maxBytes` 是**单次**窗口，分片/日志比它大时一次读不完（实测 20k 行的
 * 月分片 ≈4.5MB，单窗口只读到前 8006 行）——而调用方只在文件变动时才会再来读，剩下的行就
 * 永远补不上。循环终止条件 = **本次没有前进**（末尾半行）或到了文件末尾。
 */
export function readTailDrained(
  absPath: string,
  prev: TailPos | undefined,
  maxBytes: number = TAIL_MAX_BYTES,
): TailRead {
  const first = readTail(absPath, prev, maxBytes)
  let lines = first.lines
  let at = first.next
  // 终止 = **没前进**（末尾半行）或到了文件末尾；首窗没读到换行但有前进（单行超长）也要继续。
  while (at.offset < at.size && at.offset > (prev?.offset ?? -1)) {
    const more = readTail(absPath, at, maxBytes)
    if (more.next.offset <= at.offset) break
    lines = lines.concat(more.lines)
    at = more.next
  }
  return { lines, next: at, reset: first.reset }
}

export function readTail(
  absPath: string,
  prev: TailPos | undefined,
  maxBytes: number = TAIL_MAX_BYTES,
): TailRead {
  let st: Stats
  try {
    st = statSync(absPath)
  } catch {
    return { lines: [], next: EMPTY_POS, reset: true }
  }
  const ino = st.ino ?? 0
  const mtimeMs = st.mtimeMs
  const replaced =
    !prev ||
    st.size < prev.offset ||
    (prev.ino !== 0 && ino !== 0 && prev.ino !== ino) ||
    (st.size === prev.size && mtimeMs !== prev.mtimeMs)
  const from = replaced ? 0 : prev.offset
  const next: TailPos = { offset: from, ino, size: st.size, mtimeMs }
  if (from >= st.size) return { lines: [], next, reset: replaced }
  const len = Math.min(st.size - from, maxBytes)
  const buf = Buffer.allocUnsafe(len)
  const fd = openSync(absPath, 'r')
  try {
    readSync(fd, buf, 0, len, from)
  } finally {
    closeSync(fd)
  }
  const text = buf.toString('utf8')
  const nl = text.lastIndexOf('\n')
  // 消费量：有换行 → 到最后一个换行为止；窗口读满仍无换行（超长行）→ 整窗吃掉；
  // 否则（末尾是**还没写完的半行**）→ 一字节都不前进，下一拍连半行一起读。
  const consumed = nl >= 0 ? text.slice(0, nl + 1) : len < maxBytes ? '' : text
  const advanced = nl >= 0 ? Buffer.byteLength(consumed, 'utf8') : len === maxBytes ? len : 0
  return {
    lines: consumed.split('\n').filter((l) => l.trim()),
    next: { ...next, offset: from + advanced },
    reset: replaced,
  }
}
