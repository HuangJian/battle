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
import {
  fmtFullTs,
  fmtOverfitGap,
  fmtPct,
  fmtPhaseSecs,
  fmtTs,
  OVERFIT_COL_TITLE,
  overfitCellTitle,
  overfitTone,
  phaseSecs,
  phaseSecsTitle,
  stripIsoPrefix,
} from '../src/web/view'

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

  it('过拟合列：大白话表头 + tone 档位 + gap 展示 + cell hover', () => {
    expect(OVERFIT_COL_TITLE).toContain('锚点')
    expect(OVERFIT_COL_TITLE).toContain('轮转')
    expect(OVERFIT_COL_TITLE).toContain('5pp')
    expect(overfitTone(2.5)).toBe('gray')
    expect(overfitTone(-8)).toBe('gray')
    expect(overfitTone(5)).toBe('y')
    expect(overfitTone(8)).toBe('r')
    expect(fmtOverfitGap(-8)).toBe('-8.0pp')
    expect(fmtOverfitGap(2.5)).toBe('+2.5pp')
    expect(fmtOverfitGap(0)).toBe('0.0pp')
    // it0 基线 / 未开双轨：无轮转 → gap null
    expect(overfitCellTitle({ anchorWr: 0.65, rotorWr: null, overfitGapPp: null })).toContain(
      '无轮转',
    )
    expect(overfitCellTitle({ anchorWr: 0.655, rotorWr: 0.735, overfitGapPp: -8 })).toContain(
      '锚点',
    )
  })
})

describe('phaseSecs：rollout/ppo/net 准确拆分', () => {
  it('远端 pull：纯采集 + 真训练 + 网络（下发 + 往返超出）', () => {
    const p = phaseSecs({
      rolloutSec: 400,
      ppoSec: 120,
      pureCollectSec: 350,
      ppoCloudSec: 80,
      distPhaseSec: 20,
    })
    expect(p).toEqual({ rollout: 350, ppo: 80, net: 60 }) // 20 dist + 40 (120-80)
    expect(fmtPhaseSecs(p)).toBe('350/80/60s')
    expect(phaseSecsTitle(p)).toContain('纯采集')
  })

  it('本机/流式：无 cloud 拆分 → net 仅 dist；ppo 回退 ppoSec', () => {
    expect(
      phaseSecs({
        rolloutSec: 200,
        ppoSec: 90,
        pureCollectSec: null,
        ppoCloudSec: null,
        distPhaseSec: 5,
      }),
    ).toEqual({ rollout: 200, ppo: 90, net: 5 })
  })

  it('旧账本（全缺）→ 回退 rollout/ppo，net=0', () => {
    expect(
      phaseSecs({
        rolloutSec: 60,
        ppoSec: 40,
        pureCollectSec: null,
        ppoCloudSec: null,
        distPhaseSec: null,
      }),
    ).toEqual({ rollout: 60, ppo: 40, net: 0 })
  })
})
