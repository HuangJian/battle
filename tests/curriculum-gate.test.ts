/** I4（roadmap §4-I4）晋级门 runner 的统计与判定单测。 */

import { describe, expect, it } from 'bun:test'
import { join } from 'path'
import {
  decideVerdict,
  hazardBuckets,
  mcnemarPaired,
  percentile,
  roundStat,
  wilsonLowerBound,
  upsertLedgerEntry,
  loadLedger,
  levelFilePath,
  type GateRow,
} from '../tools/sim/curriculum-gate'
import { parseWeightSpec } from '../tools/sim/eval-course-ckpt'

function row(over: Partial<GateRow>): GateRow {
  return {
    label: 'it30',
    seed: 0,
    win: false,
    cleared: false,
    outcome: 'gameover',
    kills: 0,
    ticks: 1000,
    playerHits: 0,
    playerDamageTaken: 0,
    playerShots: 0,
    powerUpsCollected: 0,
    ...over,
  }
}

describe('wilsonLowerBound', () => {
  it('0.8 点估计 / 400 局的下界 ≈ 0.757（双轮门报告口径）', () => {
    expect(wilsonLowerBound(320, 400)).toBeGreaterThan(0.75)
    expect(wilsonLowerBound(320, 400)).toBeLessThan(0.77)
  })
  it('边界：0 通过 → 0；空样本 → 0', () => {
    expect(wilsonLowerBound(0, 200)).toBe(0)
    expect(wilsonLowerBound(0, 0)).toBe(0)
  })
  it('全过 → 高下界（不等于 1，仍有区间）', () => {
    const lb = wilsonLowerBound(200, 200)
    expect(lb).toBeGreaterThan(0.96)
    expect(lb).toBeLessThan(1)
  })
})

describe('hazardBuckets（M1 归因行）', () => {
  it('未过关按死亡击杀数分桶，过关单列', () => {
    const b = hazardBuckets([
      row({ kills: 0 }),
      row({ kills: 0 }),
      row({ kills: 1 }),
      row({ kills: 2 }),
      row({ kills: 5 }),
      row({ win: true }),
      row({ cleared: true, outcome: 'max_ticks' }), // 歼灭但截断 = 过关
    ])
    expect(b).toEqual({ k0: 2, k1: 1, k2: 1, k3plus: 1, passed: 2 })
  })
})

describe('roundStat', () => {
  it('timeoutFrac 用已修口径（max_ticks 且未歼灭）', () => {
    const s = roundStat([
      row({ outcome: 'max_ticks', cleared: true }), // 歼灭截断 ≠ 超时
      row({ outcome: 'max_ticks', cleared: false }), // 真超时
      row({ outcome: 'gameover' }),
    ])
    expect(s.timeoutFrac).toBeCloseTo(1 / 3, 4)
  })
  it('dmg/kill 与分位数', () => {
    const s = roundStat([
      row({ ticks: 1000, playerDamageTaken: 100, kills: 2 }),
      row({ ticks: 2000, playerDamageTaken: 300, kills: 2 }),
      row({ ticks: 3000 }),
    ])
    expect(s.dmgPerKill).toBeCloseTo(400 / 4, 1)
    expect(s.ticksP50).toBe(2000)
    expect(s.ticksP90).toBe(2800) // 线性插值：2000 + (3000-2000)×0.8
  })
  it('percentile 空数组安全', () => {
    expect(percentile([], 0.5)).toBe(0)
  })
})

describe('mcnemarPaired（X1 同种子多腿配对）', () => {
  it('按 seed 配对：b/c 计数与 z', () => {
    const a = [
      row({ seed: 1, win: true }),
      row({ seed: 2, win: false }),
      row({ seed: 3, win: true }),
    ]
    const b = [
      row({ seed: 1, win: true, label: 'it25' }),
      row({ seed: 2, win: true, label: 'it25' }),
      row({ seed: 3, win: false, label: 'it25' }),
    ]
    const m = mcnemarPaired(a, b)
    expect(m).toEqual({ b: 1, c: 1, z: 0 })
  })
  it('无配对种子 → null', () => {
    expect(mcnemarPaired([row({ seed: 1 })], [row({ seed: 999, label: 'x' })])).toBe(null)
  })
  it('c5-gae 量级验证：18pp 差异/150 局 ⇒ |z| 显著', () => {
    // b=27 c=0（27 局翻赢）→ z = 27/√27 ≈ 5.2
    const a = Array.from({ length: 150 }, (_, i) => row({ seed: i, win: i < 60 }))
    const b = Array.from({ length: 150 }, (_, i) => row({ seed: i, win: i < 87, label: 'x' }))
    const m = mcnemarPaired(a, b)!
    expect(Math.abs(m.z!)).toBeGreaterThan(1.96)
  })
})

describe('decideVerdict（D4 单轨门）', () => {
  it('pooled ≥80% 且 ≥200 局 → graduate', () => {
    const v = decideVerdict(322, 400, 0.05)
    expect(v.verdict).toBe('graduate')
  })
  it('pooled <80% → stay（Wilson LB 进 reason）', () => {
    const v = decideVerdict(300, 400, 0.05)
    expect(v.verdict).toBe('stay')
    expect(v.reason).toContain('Wilson')
  })
  it('样本不足（<400 = 双轮各 200）→ stay 即使点估计达标', () => {
    const v = decideVerdict(170, 200, 0.05)
    expect(v.verdict).toBe('stay')
    expect(v.reason).toContain('样本不足')
  })
  it('连续 3 个门周期未过门 → escalate（roadmap §5.7 卡门升报）', () => {
    const v = decideVerdict(300, 400, 0.05, 0.8, 400, 2)
    expect(v.verdict).toBe('escalate')
    expect(v.stayAttempts).toBe(3)
    expect(v.reason).toContain('连续 3')
  })
  it('graduate ⇒ 卡门计数归零；stay ⇒ 累加', () => {
    expect(decideVerdict(322, 400, 0.05, 0.8, 400, 5).stayAttempts).toBe(0)
    expect(decideVerdict(300, 400, 0.05, 0.8, 400, 0).stayAttempts).toBe(1)
  })
  it('哨兵红（超时 >15%）也计入门周期，避免靠苟活无限续命', () => {
    expect(decideVerdict(390, 400, 0.2, 0.8, 400, 2).verdict).toBe('escalate')
  })
})

describe('I4 路径/权重规格（subprocess 接口契约）', () => {
  it('levelFilePath 指向 levels/<level>.jsonc（关卡文件持有 stages，课程文件没有）', () => {
    expect(levelFilePath('ladder-c04', 'X')).toBe(join('X', 'levels', 'ladder-c04.jsonc'))
  })

  it('parseWeightSpec 支持 label=path（I4 腿命名），裸路径回退文件名', () => {
    expect(parseWeightSpec('it30=nn-training/weights/a/it30.json')).toEqual({
      path: 'nn-training/weights/a/it30.json',
      label: 'it30',
    })
    expect(parseWeightSpec('nn-training/weights/a/it30.json')).toEqual({
      path: 'nn-training/weights/a/it30.json',
      label: 'it30.json',
    })
    // 含 '=' 但左侧像路径 ⇒ 不当 label 拆（避免破坏含 '=' 的裸路径）
    expect(parseWeightSpec('a/b=weird.json').label).toBe('b=weird.json')
  })
})

describe('LEDGER upsert（I5，原子写）', () => {
  it('创建/合并 level 条目，不覆盖其他级', () => {
    const path = `${import.meta.dir}/../tmp/test-ledger-${Date.now()}.jsonc`
    upsertLedgerEntry('ladder-c04', { status: 'ppo' }, path)
    upsertLedgerEntry('ladder-c04', { lastGate: { verdict: 'graduate' } }, path)
    upsertLedgerEntry('ladder-c05', { status: 'pending' }, path)
    const ledger = loadLedger(path)
    const levels = ledger.levels as Record<string, Record<string, unknown>>
    const c04 = levels['ladder-c04'] as Record<string, unknown>
    expect(c04.status).toBe('ppo')
    expect((c04.lastGate as Record<string, unknown>).verdict).toBe('graduate')
    expect(levels['ladder-c05']).toBeDefined()
    import('fs').then(({ rmSync }) => rmSync(path))
  })
})
