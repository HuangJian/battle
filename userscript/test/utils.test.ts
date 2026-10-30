import { describe, expect, it } from 'bun:test'
import { clamp, hashRange, normalizeNewlines, utf8Bytes } from '../src/utils'

/** 独立重实现（测试不调用被测实现），已知向量 + 随机串双向对账。 */
function refFnv1a64(text: string): bigint {
  let h = 0xcbf29ce484222325n
  for (let i = 0; i < text.length; i++) {
    h ^= BigInt(text.charCodeAt(i))
    h = (h * 0x100000001b3n) & 0xffffffffffffffffn
  }
  return h
}

const hex = (n: bigint): string => n.toString(16).padStart(16, '0')

describe('utils', () => {
  it('normalizeNewlines：CRLF 与孤立 CR 都归一，不改 LF', () => {
    expect(normalizeNewlines('a\r\nb\rc\nd')).toBe('a\nb\nc\nd')
    expect(normalizeNewlines('x\r\r\ny')).toBe('x\n\ny')
    expect(normalizeNewlines('')).toBe('')
  })

  it('hashRange：已知 FNV-1a 64 向量 + 与独立实现逐串相等', () => {
    expect(hashRange('', 0, 0)).toBe('cbf29ce484222325')
    expect(hashRange('a', 0, 1)).toBe('af63dc4c8601ec8c')
    for (const s of ['abc', 'foobar', '中文🙂', 'x'.repeat(300), 'a\r\nb']) {
      expect(hashRange(s, 0, s.length)).toBe(hex(refFnv1a64(s)))
    }
  })

  it('hashRange：区间语义是 [start, end)，且越界裁剪', () => {
    expect(hashRange('0123456789', 3, 7)).toBe(hex(refFnv1a64('3456')))
    expect(hashRange('abc', -5, 99)).toBe(hex(refFnv1a64('abc')))
    expect(hashRange('abc', 9, 12)).toBe('cbf29ce484222325')
  })

  it('utf8Bytes：ASCII/中文/emoji 按码点计', () => {
    expect(utf8Bytes('a')).toBe(1)
    expect(utf8Bytes('中')).toBe(3)
    expect(utf8Bytes('🙂')).toBe(4)
    expect(utf8Bytes('a中🙂')).toBe(8)
    expect(utf8Bytes('')).toBe(0)
  })

  it('clamp', () => {
    expect(clamp(5, 0, 3)).toBe(3)
    expect(clamp(-1, 0, 3)).toBe(0)
    expect(clamp(2, 0, 3)).toBe(2)
  })
})
