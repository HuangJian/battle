import { describe, expect, it } from 'bun:test'
import { chunkText, normalizeNewlines, tailByBytes, utf8Bytes } from '../src/utils'

describe('normalizeNewlines', () => {
  it('CRLF 与孤立 CR 都归一成 LF（tqdm 的进度条不再变成一条巨长行）', () => {
    expect(normalizeNewlines('a\r\nb\rc\nd')).toBe('a\nb\nc\nd')
    expect(normalizeNewlines('a\r\r\nb')).toBe('a\n\nb')
    expect(normalizeNewlines('no-newline')).toBe('no-newline')
  })
})

describe('chunkText（48 KiB 三重边界）', () => {
  it('按行累加：每块 ≤ 48 KiB，拼接逐字节相等', () => {
    const text = Array.from({ length: 500 }, (_, i) => `line ${i} ${'x'.repeat(200)}\n`).join('')
    const chunks = chunkText(text)
    expect(chunks.length).toBeGreaterThan(1)
    expect(chunks.join('')).toBe(text)
    for (const c of chunks) expect(utf8Bytes(c)).toBeLessThanOrEqual(48 * 1024)
  })

  it('1 MiB 无换行 ⇒ 硬切成多块且每块 ≤48 KiB（不能假设一行 ≤ N 字节）', () => {
    const text = 'y'.repeat(1024 * 1024)
    const chunks = chunkText(text)
    expect(chunks.length).toBeGreaterThanOrEqual(22)
    expect(chunks.join('')).toBe(text)
    for (const c of chunks) expect(utf8Bytes(c)).toBeLessThanOrEqual(48 * 1024)
  })

  it('emoji / 中文不切坏（码点边界，不是 UTF-16 码元边界）', () => {
    const text = '中文🎮'.repeat(20_000)
    const chunks = chunkText(text, 1024)
    expect(chunks.join('')).toBe(text)
    for (const c of chunks) {
      expect(utf8Bytes(c)).toBeLessThanOrEqual(1024)
      // 不出现孤立代理项
      expect(c).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/)
      expect(c).not.toMatch(/(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/)
    }
  })

  it('空串 ⇒ 空数组；正好一块不白切', () => {
    expect(chunkText('')).toEqual([])
    expect(chunkText('abc')).toEqual(['abc'])
  })
})

describe('tailByBytes', () => {
  it('从末尾取 ≤ 上限的整码点尾巴', () => {
    expect(tailByBytes('abcdef', 3)).toBe('def')
    expect(tailByBytes('中文🎮x', 5)).toBe('🎮x') // 4 + 1
    expect(tailByBytes('', 10)).toBe('')
    expect(tailByBytes('abc', 99)).toBe('abc')
  })
})
