import { describe, expect, it } from 'bun:test'
import {
  avgTicksPerGame,
  fmtDur,
  gamesOf,
  healthVerdict,
  type IterEvent,
  klEff,
} from '../tools/diag/rl-hourly-inspect.ts'

function ev(partial: Partial<IterEvent>): IterEvent {
  return {
    iter: 1,
    time: '2026-08-24 00:00:00',
    winRate: 0,
    outcomes: {},
    score_mean: null,
    entropy: 1.3,
    kl: 0.03,
    ticks: null,
    ...partial,
  }
}

describe('gamesOf', () => {
  it('sums outcome counts (works for any per-iter game count)', () => {
    expect(gamesOf(ev({ outcomes: { base_destroyed: 60, stage_clear: 10 } }))).toBe(70)
    expect(
      gamesOf(
        ev({ outcomes: { base_destroyed: 436, lives_exhausted: 85, stage_clear: 53, timeout: 4 } }),
      ),
    ).toBe(578)
    expect(gamesOf(ev({ outcomes: {} }))).toBe(0)
  })
})

describe('avgTicksPerGame', () => {
  it('returns null for rows without rollout telemetry (legacy it1 artifact)', () => {
    const it1 = ev({ ticks: 0, outcomes: {} })
    expect(avgTicksPerGame(it1)).toBeNull()
    expect(avgTicksPerGame(ev({ ticks: null, outcomes: { base_destroyed: 70 } }))).toBeNull()
  })

  it('divides by actual game count from outcomes, not a hardcoded constant', () => {
    const e140 = ev({ ticks: 586_600, outcomes: { base_destroyed: 120, stage_clear: 20 } })
    expect(Math.round(avgTicksPerGame(e140)!)).toBe(4190)
    const e70 = ev({ ticks: 294_000, outcomes: { base_destroyed: 70 } })
    expect(avgTicksPerGame(e70)).toBe(4200)
  })
})

describe('healthVerdict — it1 zero-ticks misjudgment regression', () => {
  it('a no-telemetry row does NOT drag 局均ticks to 0 or trip 异常', () => {
    const recent = [
      ev({ iter: 1, ticks: 0, outcomes: {}, entropy: 1.299, kl: 0.0269 }), // §5 事故遗留行
      ev({ iter: 2, ticks: 294_000, outcomes: { base_destroyed: 70 }, entropy: 1.33, kl: 0.02 }),
      ev({
        iter: 3,
        ticks: 420_000,
        outcomes: { base_destroyed: 100, stage_clear: 40 },
        entropy: 1.32,
        kl: 0.025,
      }),
      ev({ iter: 4, ticks: 350_000, outcomes: { base_destroyed: 70 }, entropy: 1.31, kl: 0.024 }),
    ]
    expect(healthVerdict(recent)).toBe('健康')
  })

  it('still trips 异常 on genuinely low per-game ticks (秒投降特征)', () => {
    const recent = [
      ev({ ticks: 400_000, outcomes: { base_destroyed: 70 } }),
      ev({ ticks: 40_000, outcomes: { base_destroyed: 70 } }), // 局均 571 < 1000
    ]
    expect(healthVerdict(recent)).toBe('异常')
  })

  it('all rows without telemetry → ticks rules simply do not fire', () => {
    const recent = [ev({ ticks: 0, outcomes: {} }), ev({ ticks: null, outcomes: {} })]
    expect(healthVerdict(recent)).toBe('健康')
  })

  it('KL streak rule unchanged', () => {
    const recent = [ev({ kl: 0.16 }), ev({ kl: 0.16 }), ev({ kl: 0.16 })]
    expect(healthVerdict(recent)).toBe('异常')
  })
})

describe('fmtDur', () => {
  it('renders seconds/minutes and em-dash for missing legacy rows', () => {
    expect(fmtDur(null)).toBe('—')
    expect(fmtDur(undefined)).toBe('—')
    expect(fmtDur(15)).toBe('15s')
    expect(fmtDur(89.9)).toBe('90s')
    expect(fmtDur(402)).toBe('6.7m')
    expect(fmtDur(6632)).toBe('110.5m')
  })
})

describe('klEff — 累计 KL 展示口径（2026-08-25 遥测补盲）', () => {
  it('stream rows: kl_cum takes precedence over the last-wave kl', () => {
    // it53 实测：末 wave kl=0.0411（旧显示，误导），轮内累计 0.1399（熔断口径）
    expect(klEff(ev({ kl_cum: 0.1399, kl: 0.0411 }))).toBe(0.1399)
  })

  it('legacy rows without kl_cum fall back to single-value kl', () => {
    const legacy = ev({ kl: 0.0363 }) // 无 kl_cum 字段
    expect(legacy.kl_cum).toBeUndefined()
    expect(klEff(legacy)).toBe(0.0363)
  })

  it('explicit null kl_cum (串行 checkpoint-complete 轮) falls back to kl, then null', () => {
    expect(klEff(ev({ kl_cum: null, kl: 0.0389 }))).toBe(0.0389)
    expect(klEff(ev({ kl_cum: null, kl: null }))).toBeNull()
  })
})
