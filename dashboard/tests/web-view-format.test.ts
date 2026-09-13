/**
 * web-view-format.test.ts — 时间 / 文本格式化纯函数：fmtTs 同日跨日、fmtFullTs、fmtPct、stripIsoPrefix、formatBytes
 *
 * 分层：src/web/view/format.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { fmtFullTs, fmtPct, fmtTs, stripIsoPrefix } from '../src/web/view'

// ────────────────────────── 纯函数：时间 / 文本 ──────────────────────────
describe('view 纯函数：时间与文本', () => {
  it('fmtTs：同日省略日期 HH:mm:ss；跨日 MM-DD HH:mm:ss（GLM-U3 精确化）', () => {
    const now = new Date(2026, 8, 7, 12, 0, 0).getTime()
    expect(fmtTs(new Date(2026, 8, 7, 9, 8, 5).getTime(), now)).toBe('09:08:05')
    expect(fmtTs(new Date(2026, 8, 6, 23, 59, 59).getTime(), now)).toBe('09-06 23:59:59')
    expect(fmtTs(new Date(2026, 7, 7, 9, 8, 5).getTime(), now)).toBe('08-07 09:08:05')
  })

  it('fmtFullTs：完整「YYYY-MM-DD HH:MM:SS」（历史锚点比较用）', () => {
    expect(fmtFullTs(new Date(2026, 8, 7, 9, 8, 5).getTime())).toBe('2026-09-07 09:08:05')
  })

  it('fmtPct / stripIsoPrefix', () => {
    expect(fmtPct(0.123)).toBe('12.3%')
    expect(fmtPct(null)).toBe('—')
    expect(stripIsoPrefix('2026-09-07T02:03:04.567Z link timeout')).toBe('link timeout')
    expect(stripIsoPrefix('2026-09-07T02:03:04Z s5: boom')).toBe('s5: boom')
    expect(stripIsoPrefix('2026-09-07T02:03:04 s5: boom')).toBe('s5: boom')
    expect(stripIsoPrefix('plain error')).toBe('plain error')
  })
})
