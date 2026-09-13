/**
 * web-view-legacy-keys.test.ts — GLM-U6 / DS-E9：localStorage 迁移失败保留语义 + 白名单清理
 *
 * 分层：src/web/view/legacy-keys.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { cleanupNonTcKeys, LEGACY_KEY_RULES, migrateLegacyKey } from '../src/web/view'

// ────────────────────────── localStorage 迁移（GLM-U6 / DS-E9） ──────────────────────────
class MockStorage {
  map = new Map<string, string>()
  getItem(k: string): string | null {
    return this.map.get(k) ?? null
  }
  setItem(k: string, v: string): void {
    this.map.set(k, v)
  }
  removeItem(k: string): void {
    this.map.delete(k)
  }
  key(i: number): string | null {
    return [...this.map.keys()][i] ?? null
  }
  get length(): number {
    return this.map.size
  }
}

describe('localStorage 迁移与白名单清理', () => {
  it('合法值迁移：写新键 + 删旧键；返回 true', () => {
    const s = new MockStorage()
    s.setItem('pool.iterFilter', 'eval')
    const ok = migrateLegacyKey(s as never, LEGACY_KEY_RULES[1]!)
    expect(ok).toBe(true)
    expect(s.getItem('tc.metrics.filter')).toBe('eval')
    expect(s.getItem('pool.iterFilter')).toBeNull()
  })

  it('非法值 → 保留旧键 + 返回 false（GLM-U6：调用方 console.warn 语义）', () => {
    const s = new MockStorage()
    s.setItem('pool.iterFilter', 'bad-value')
    const ok = migrateLegacyKey(s as never, LEGACY_KEY_RULES[1]!)
    expect(ok).toBe(false)
    expect(s.getItem('pool.iterFilter')).toBe('bad-value')
    expect(s.getItem('tc.metrics.filter')).toBeNull()
  })

  it('旧键不存在 → true 无副作用', () => {
    const s = new MockStorage()
    expect(migrateLegacyKey(s as never, LEGACY_KEY_RULES[0]!)).toBe(true)
    expect(s.length).toBe(0)
  })

  it('cleanupNonTcKeys：清理白名单外残留，保留 tc.* 与遗留键', () => {
    const s = new MockStorage()
    s.setItem('tc.card.nodes', '1')
    s.setItem('stale.key', 'x')
    s.setItem('pool.iterFilter', 'all')
    const removed = cleanupNonTcKeys(s as never)
    expect(removed).toEqual(['stale.key'])
    expect(s.getItem('tc.card.nodes')).toBe('1')
    expect(s.getItem('pool.iterFilter')).toBe('all')
    expect(s.getItem('stale.key')).toBeNull()
  })
})
