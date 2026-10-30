/** utils.ts — 纯函数（无 DOM / 无 GM）。 */

/**
 * 归一 `\r\n` 与孤立 `\r` 为 `\n`。
 * tqdm 用 `\r` 推进进度条；不归一就会在日志里变成一条巨长的行。
 */
export function normalizeNewlines(text: string): string {
  return text.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
}

const FNV_OFFSET = 0xcbf29ce484222325n
const FNV_PRIME = 0x100000001b3n
const MASK64 = 0xffffffffffffffffn

/** FNV-1a 64-bit over UTF-16 code units（与协议里的 `tailHash` 同一口径）。 */
export function fnv1a64Range(text: string, start: number, end: number): bigint {
  let h = FNV_OFFSET
  for (let i = start; i < end; i++) {
    h ^= BigInt(text.charCodeAt(i))
    h = (h * FNV_PRIME) & MASK64
  }
  return h
}

/** `[start, end)` 的 FNV-1a 64 十六进制（16 位左补 0）；区间裁剪到字符串内。 */
export function hashRange(text: string, start: number, end: number): string {
  const from = Math.max(0, Math.min(start, text.length))
  const to = Math.max(from, Math.min(end, text.length))
  return fnv1a64Range(text, from, to).toString(16).padStart(16, '0')
}

/** UTF-8 字节数（按码点算，不分配 TextEncoder 的结果字符串）。 */
export function utf8Bytes(text: string): number {
  let bytes = 0
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0
    bytes += cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4
  }
  return bytes
}

/** 数值夹取。 */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** 从末尾取 ≤ maxBytes 的整码点尾巴（超长单行/积压跳转用，不切断 emoji/中文）。 */
export function tailByBytes(text: string, maxBytes: number): string {
  let bytes = 0
  let cut = text.length
  let i = text.length
  while (i > 0) {
    // 先认代理对：从末尾看时，单看 `codePointAt(i-1)` 只会拿到低代理。
    const low = text.charCodeAt(i - 1)
    let width = 1
    if (low >= 0xdc00 && low <= 0xdfff && i >= 2) {
      const high = text.charCodeAt(i - 2)
      if (high >= 0xd800 && high <= 0xdbff) width = 2
    }
    const start = i - width
    const ch = text.slice(start, i)
    const b = utf8Bytes(ch)
    if (bytes + b > maxBytes) break
    bytes += b
    cut = start
    i = start
  }
  return text.slice(cut)
}

/**
 * 按 UTF-8 字节切块（plan §3.1 / 评审 B3）：
 *   1. 先按 `\n` 边界累加，凑满即出块；
 *   2. **单行本身超限 ⇒ 按码点硬切**（surrogate pair / emoji / 中文不切坏）。
 * 保证：每块 ≤ maxBytes，`chunks.join('') === text`。
 */
export function chunkText(text: string, maxBytes = 48 * 1024): string[] {
  if (text.length === 0) return []
  const chunks: string[] = []
  let buf = ''
  let bufBytes = 0
  const flush = (): void => {
    if (buf.length === 0) return
    chunks.push(buf)
    buf = ''
    bufBytes = 0
  }

  let i = 0
  while (i < text.length) {
    const nl = text.indexOf('\n', i)
    const end = nl === -1 ? text.length : nl + 1
    const line = text.slice(i, end)
    const lineBytes = utf8Bytes(line)
    if (bufBytes + lineBytes <= maxBytes) {
      buf += line
      bufBytes += lineBytes
      i = end
      continue
    }
    flush()
    if (lineBytes <= maxBytes) {
      buf = line
      bufBytes = lineBytes
      i = end
      continue
    }
    // 单行超限：按码点硬切（每块 ≤ maxBytes）
    let pos = 0
    while (pos < line.length) {
      let take = ''
      let bytes = 0
      for (const ch of line.slice(pos)) {
        const b = utf8Bytes(ch)
        if (bytes + b > maxBytes) break
        take += ch
        bytes += b
      }
      if (take.length === 0) take = String.fromCodePoint(line.codePointAt(pos) ?? 0) // maxBytes < 单码点宽度的理论兜底
      chunks.push(take)
      pos += take.length
    }
    i = end
  }
  flush()
  return chunks
}
