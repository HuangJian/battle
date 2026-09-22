/** fingerprint-cache.test.ts ↔ dashboard/src/core/fingerprint-cache.ts（输入指纹缓存原语）。 */
import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { appendFileSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import {
  createFingerprintCache,
  fileSignature,
  filesSignature,
} from '../src/core/fingerprint-cache'

/** 可控时钟 + 可变输入：`sig` 就是「输入」。 */
function fixture() {
  let sig = 'v1'
  let clock = 1_000_000
  const cache = createFingerprintCache<string>({
    backstopMs: 600_000,
    signature: () => sig,
    now: () => clock,
  })
  let computed = 0
  const compute = (): string => {
    computed++
    return `val#${computed}`
  }
  return {
    cache,
    compute,
    computed: () => computed,
    setSig: (s: string) => {
      sig = s
    },
    advance: (ms: number) => {
      clock += ms
    },
  }
}

describe('createFingerprintCache', () => {
  it('指纹未变且未超兵底 → 命中（同一实例、compute 不跑）', () => {
    const f = fixture()
    const a = f.cache.get('k', f.compute)
    const b = f.cache.get('k', f.compute)
    expect(b).toBe(a)
    expect(f.computed()).toBe(1)
    expect(f.cache.stats().hits).toBe(1)
    expect(f.cache.stats().misses).toBe(1) // 首次必然算一次
  })

  it('指纹变了 → 立即重算（不等兵底）', () => {
    const f = fixture()
    const a = f.cache.get('k', f.compute)
    f.setSig('v2')
    const b = f.cache.get('k', f.compute)
    expect(b).not.toBe(a)
    expect(f.computed()).toBe(2)
    expect(f.cache.stats().misses).toBe(2)
  })

  it('兵底到点（指纹没变）→ 重算一次（防指纹清单漏项）', () => {
    const f = fixture()
    const a = f.cache.get('k', f.compute)
    f.advance(599_999)
    expect(f.cache.get('k', f.compute)).toBe(a) // 差 1ms 仍命中
    f.advance(2)
    expect(f.cache.get('k', f.compute)).not.toBe(a) // 过点即重算
    expect(f.cache.stats().backstops).toBe(1)
    expect(f.computed()).toBe(2)
  })

  it('重算后**重新取指纹**：compute 自己改了输入 → 下一次立即命中（入账就是这样）', () => {
    let sig = 'a'
    let clock = 0
    let n = 0
    const cache = createFingerprintCache<number>({
      backstopMs: 600_000,
      signature: () => sig,
      now: () => clock,
    })
    cache.get('k', () => {
      sig = `a+${++n}` // 重算体往「输入」里写了一条
      return n
    })
    expect(cache.get('k', () => 99)).toBe(1) // 命中，不会以为输入又变了（否则每读都重算）
    expect(cache.stats().hits).toBe(1)
  })

  it('按 key 分条：别的 key 变了不影响本 key', () => {
    let sigByKey: Record<string, string> = { a: '1', b: '1' }
    const cache = createFingerprintCache<string>({
      backstopMs: 600_000,
      signature: (k) => sigByKey[k]!,
    })
    const a1 = cache.get('a', () => 'A1')
    const b1 = cache.get('b', () => 'B1')
    sigByKey = { a: '1', b: '2' }
    expect(cache.get('a', () => 'A2')).toBe(a1)
    expect(cache.get('b', () => 'B2')).not.toBe(b1)
  })

  it('signature 收到的就是 key 本身（key 必须是业务身份，别加装饰前缀）', () => {
    const seen: string[] = []
    const cache = createFingerprintCache<number>({
      backstopMs: 600_000,
      signature: (k) => {
        seen.push(k)
        return k
      },
    })
    cache.get('cA', () => 1)
    expect(seen).toEqual(['cA'])
    // 反例（评估板踩过）：key = `view:cA` 而指纹函数按「课程名」取数 → 算到不存在的课程上，
    // 指纹恒等 ⇒ 命中永远成立 ⇒ **永不重算**（静默出错）。
  })

  it('getWithStatus 报命中；peek 只读且不记账', () => {
    const f = fixture()
    expect(f.cache.getWithStatus('k', f.compute).hit).toBe(false)
    expect(f.cache.peek('k')).toBe(true)
    expect(f.cache.stats()).toEqual({ hits: 0, misses: 1, backstops: 0 }) // peek 不计
    expect(f.cache.getWithStatus('k', f.compute).hit).toBe(true)
    f.setSig('zz')
    expect(f.cache.peek('k')).toBe(false) // 指纹变了：不再命中
    f.advance(700_000)
    expect(f.cache.peek('k')).toBe(false) // 兵底过了：不再命中
  })

  it('invalidate(key) 单条 / invalidate() 整表 / clear() 连计数归零', () => {
    let sig: Record<string, string> = { a: '1', b: '1' }
    const cache = createFingerprintCache<string>({
      backstopMs: 600_000,
      signature: (k) => sig[k]!,
    })
    const a = cache.get('a', () => 'A')
    const b = cache.get('b', () => 'B')
    cache.invalidate('a')
    expect(cache.get('a', () => 'A2')).not.toBe(a)
    expect(cache.get('b', () => 'B2')).toBe(b) // 单条作废不牵连别的键
    cache.invalidate()
    expect(cache.get('b', () => 'B3')).not.toBe(b)
    sig = { a: '1', b: '1' }
    cache.clear()
    expect(cache.stats()).toEqual({ hits: 0, misses: 0, backstops: 0 })
  })
})

describe('fileSignature / filesSignature（清单式输入的公共写法）', () => {
  let DIR = ''
  beforeAll(() => {
    DIR = mkdtempSync(path.join(tmpdir(), 'fp-cache-'))
  })
  afterAll(() => {
    rmSync(DIR, { recursive: true, force: true })
  })

  it('缺失 = "-"；写入/追加后指纹变', () => {
    const p = path.join(DIR, 'x.jsonl')
    expect(fileSignature(p)).toBe('-')
    writeFileSync(p, 'a\n')
    const s1 = fileSignature(p)
    expect(s1).toMatch(/^\d+:/)
    appendFileSync(p, 'bb\n')
    expect(fileSignature(p)).not.toBe(s1)
  })

  it('filesSignature 拼接（标签参与）：任一文件变整串就变', () => {
    const p1 = path.join(DIR, 'a.jsonl')
    const p2 = path.join(DIR, 'b.jsonl')
    writeFileSync(p1, '1\n')
    const before = filesSignature([
      ['a', p1],
      ['b', p2],
    ])
    expect(
      filesSignature([
        ['a', p1],
        ['b', p2],
      ]),
    ).toBe(before) // 稳定（只 stat）
    writeFileSync(p2, '2\n')
    expect(
      filesSignature([
        ['a', p1],
        ['b', p2],
      ]),
    ).not.toBe(before)
  })
})
