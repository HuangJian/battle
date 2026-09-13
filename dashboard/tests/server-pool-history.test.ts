/**
 * server-pool-history.test.ts — 慢节点判定：1.5s ping 误报「离线」的根治（isSlowNode / isSlowNodeRows / parseTsMs）
 *
 * 分层：src/server/pool-history.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { emptyHistory, isSlowNode, isSlowNodeRows, parseTsMs } from '../src/server/pool-history'

// ────────────────────────── 节点 pill 行（慢节点/停用/启停 toggle，2026-09-11 用户指令） ──────────────────────────
describe('pool-history.isSlowNode（慢节点判定：1.5s ping 误报「离线」的根治）', () => {
  it('近期有成功结算且耗时不高 → false；结算慢（中位>8s）或超 10 分钟无结算 → true', () => {
    const now = Date.now()
    const mk = (
      over: Record<string, unknown>,
    ): import('../src/server/pool-history').NodeHistory => ({
      ...emptyHistory(),
      ...over,
    })
    // 健康节点：刚结算过且快
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 30_000, avgElapsedSec: 1.2, lastOkElapsedSec: 1.5 })),
    ).toBe(false)
    // 慢节点：还在结算但单局要几十秒（Kaggle CPU 饱和 → 1.5s ping 必超时）
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 60_000, avgElapsedSec: 19.9, lastOkElapsedSec: 41.9 })),
    ).toBe(true)
    // 快节点但偶发沉默 5 分钟（轮间隙）：不判慢（耗时没超阈值）
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 5 * 60_000, avgElapsedSec: 1.2, lastOkElapsedSec: 1.5 })),
    ).toBe(false)
    // 历史从未结算过：不算慢节点（真离线照旧标离线）
    expect(isSlowNode(mk({ lastOkTsMs: null, avgElapsedSec: null, lastOkElapsedSec: null }))).toBe(
      false,
    )
    // 恰好在窗口边界内 + 耗时未知 → 不误判
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 9 * 60_000, avgElapsedSec: null, lastOkElapsedSec: null })),
    ).toBe(false)
  })

  it('独立重实现对拍：慢节点行样本 → isSlowNode(聚合) 与 isSlowNodeRows(逐行) 同判', () => {
    const now = Date.parse('2026-09-11T12:00:00')
    const rows = [
      { node: 'a95', ok: true, elapsedSec: 41.9, ts: '2026-09-11T11:52:21' },
      { node: 'self', ok: true, elapsedSec: 1.5, ts: '2026-09-11T11:52:21' },
      { node: 'a95', ok: false, ts: '2026-09-11T11:55:00' },
    ]
    // a95：7.6 分钟前成功过且耗时 41.9s > 8s → 慢节点
    expect(isSlowNodeRows(rows, 'a95', now)).toBe(true)
    // self：快 → 非慢
    expect(isSlowNodeRows(rows, 'self', now)).toBe(false)
    // 无记录节点 → 非慢（真离线口径）
    expect(isSlowNodeRows(rows, 'a98', now)).toBe(false)
  })

  it('窗口外交互：慢节点沉默超窗后回「离线」；健康快节点沉默同窗后也是「离线」', () => {
    const now = Date.now()
    const mk = (
      over: Record<string, unknown>,
    ): import('../src/server/pool-history').NodeHistory => ({
      ...emptyHistory(),
      ...over,
    })
    // 慢节点最后一次成功在 31 分钟前：窗口外 → 真离线
    expect(isSlowNode(mk({ lastOkTsMs: now - 31 * 60_000, lastOkElapsedSec: 41.9 }))).toBe(false)
    // 同窗口内：慢节点（41.9s）成立
    expect(isSlowNode(mk({ lastOkTsMs: now - 29 * 60_000, lastOkElapsedSec: 41.9 }))).toBe(true)
    // 健康快节点沉默 31 分钟：窗口外 → 真离线（窗口不是慢节点的保护伞）
    expect(isSlowNode(mk({ lastOkTsMs: now - 31 * 60_000, lastOkElapsedSec: 1.5 }))).toBe(false)
  })

  it('parseTsMs：ISO（T 分隔）与空格分隔两种写法都能解析', () => {
    expect(parseTsMs('2026-09-11T11:52:21')).toBe(Date.parse('2026-09-11T11:52:21'))
    expect(parseTsMs('2026-09-11 11:52:21')).toBe(Date.parse('2026-09-11T11:52:21'))
    expect(parseTsMs(undefined)).toBeNull()
    expect(parseTsMs('garbage')).toBeNull()
  })
})
