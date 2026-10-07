/**
 * server-api-pool.test.ts — pool 视图结构 + selfStatus 占位 + 切天零重算（DS-E1）
 *
 * 分层：src/server/api（buildPoolView）
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * ★ 2026-09-26（plan/nodes-decouple-from-course.plan.md）：节点统计与课程解耦 ——
 *   `?course=` 已移除，改为 `?days=` 本地日窗口；贡献字段由「对齐轮 contrib*」改为
 *   「窗口内局数 winRollout/winEval」。
 */

import { describe, expect, it } from 'bun:test'
import { buildPoolView, poolCountersView } from '../src/server/api'
import { poolHistoryCounters } from '../src/server/pool-history'

// ────────────────────────── /api/pool 契约（DS-E1 / selfStatus 占位） ──────────────────────────
describe('/api/pool（buildPoolView）', () => {
  it('结构：nodes/local/cachedAt/sources/window/selfStatus（null 或对象）', async () => {
    const pv = await buildPoolView()
    expect(typeof pv.cachedAt).toBe('number')
    expect(Array.isArray(pv.nodes)).toBe(true)
    for (const r of pv.nodes) {
      expect(r.kind).toBe('node')
      expect(r.id.length).toBeGreaterThan(0)
      expect(['healthy', 'warn', 'bad', 'noping', 'nodata', 'disabled']).toContain(r.status)
      expect(typeof r.ok).toBe('number')
      expect(typeof r.fail).toBe('number')
      // ★ 窗口口径：不再有 contrib / lastIter / globalMaxIt
      expect(typeof r.winRollout).toBe('number')
      expect(typeof r.winEval).toBe('number')
      expect(typeof r.lastContrib).toBe('number')
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
    expect(Array.isArray(pv.sources)).toBe(true)
    expect(pv.window.key).toBe('today')
    expect(typeof pv.window.label).toBe('string')
  })

  it('探测层缓存命中（cachedAt 不变）+ 切天零重算；?fresh=1 bypass 重算', async () => {
    const a = await buildPoolView(false, 'today')
    const b = await buildPoolView(false, 'today')
    expect(b.cachedAt).toBe(a.cachedAt)
    // ★ 探测层是**机器级**的且缓存**全量** agg（与窗口无关）：切天只重投影，不重算 2.5s 的探测
    //   （cachedAt = 探测层算完时刻；它不变 ⇒ 聚合与 ping 一次都没重跑）。
    for (const days of ['yesterday', '7', 'all']) {
      const w = await buildPoolView(false, days)
      expect(w.window.key).toBe(days)
      expect(w.cachedAt).toBe(a.cachedAt)
    }
    const c = await buildPoolView(true)
    expect(c.cachedAt).toBeGreaterThanOrEqual(a.cachedAt)
  })

  it('/api/poolCounters：纯读计数器，不触发任何聚合（calls 不变）', () => {
    // 观测面（plan/dashboard-pool-history-idle-cost P1-3）：它只做浅拷贝 —— 若哪天有人把
    // 「顺手算一下」塞进来，空置取证的读数就不再可信，故这里钉死它零副作用。
    const before = poolHistoryCounters()
    const c = poolCountersView()
    expect(typeof c.computes).toBe('number')
    expect(typeof c.fullRescans).toBe('number')
    expect(typeof c.bytesRead).toBe('number')
    expect(poolHistoryCounters().calls).toBe(before.calls)
  })
})
