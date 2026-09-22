/**
 * server-api-pool.test.ts — pool 视图结构与 selfStatus 占位 + 缓存 key 带 course（DS-E1）
 *
 * 分层：src/server/api（buildPoolView）
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { buildPoolView } from '../src/server/api'

// ────────────────────────── /api/pool 契约（DS-E1 / selfStatus 占位） ──────────────────────────
describe('/api/pool（buildPoolView）', () => {
  it('结构：nodes/local/cachedAt/activeFlow/selfStatus（null 或对象）', async () => {
    const pv = await buildPoolView()
    expect(typeof pv.cachedAt).toBe('number')
    expect(Array.isArray(pv.nodes)).toBe(true)
    for (const r of pv.nodes) {
      expect(r.kind).toBe('node')
      expect(r.id.length).toBeGreaterThan(0)
      expect(['healthy', 'warn', 'bad', 'noping', 'nodata', 'disabled']).toContain(r.status)
      expect(typeof r.ok).toBe('number')
      expect(typeof r.fail).toBe('number')
      expect(typeof r.contrib).toBe('number')
      expect(r.avgElapsedSec === null || typeof r.avgElapsedSec === 'number').toBe(true)
      expect(r.avgWallSec === null || typeof r.avgWallSec === 'number').toBe(true)
      expect(Array.isArray(r.recent)).toBe(true)
      expect(typeof r.lastError).toBe('string')
    }
    if (pv.local) {
      expect(pv.local.kind).toBe('local')
      expect(pv.local.id).toBe('local')
    }
    expect(pv.selfStatus === null || typeof pv.selfStatus.workers === 'number').toBe(true)
    expect(typeof pv.localHash).toBe('string')
  })

  it('探测层缓存命中（cachedAt 不变）+ 跨课程共用；?fresh=1 bypass 重算', async () => {
    const a = await buildPoolView(false)
    const b = await buildPoolView(false)
    expect(b.cachedAt).toBe(a.cachedAt)
    // ★ 探测层是**机器级**的（ping/池历史/codeHash/selfNode 都与看哪门课无关）：换个课程名
    //   仍然命中同一份探测 —— 切课不该重算 2.5s 的探测（课程只在视图里回显）。
    const other = await buildPoolView(false, 'another-course')
    expect(other.course).toBe('another-course')
    expect(other.cachedAt).toBe(a.cachedAt)
    const c = await buildPoolView(true)
    expect(c.cachedAt).toBeGreaterThanOrEqual(a.cachedAt)
  })
})
