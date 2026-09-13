/** N4 教师探针（roadmap v2.0 §3-N4）：score 统计与 level 参数归一。 */

import { describe, it, expect } from 'bun:test'
import {
  EVAL_SEED0,
  TEACHER_LEVELS,
  normalizeLevelArg,
  scoreStats,
} from '../tools/sim/teacher-probe'
import type { GateRow } from '../tools/sim/curriculum-gate'

function row(over: Partial<GateRow>): GateRow {
  return {
    label: 'god',
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
    score: 0,
    ...over,
  }
}

describe('scoreStats（teacherWR + score 分布）', () => {
  it('winRate = win ∪ cleared；score 分布 mean/p50/p90/std/var', () => {
    const s = scoreStats([
      row({ score: 1, win: true }),
      row({ score: 2, cleared: true, outcome: 'max_ticks' }),
      row({ score: 3 }),
      row({ score: 4 }),
    ])
    expect(s.games).toBe(4)
    expect(s.wins).toBe(1)
    expect(s.cleared).toBe(1)
    expect(s.passed).toBe(2)
    expect(s.winRate).toBe(0.5)
    expect(s.scoreMean).toBe(2.5)
    expect(s.scoreP50).toBe(2.5)
    expect(s.scoreP90).toBe(3.7)
    expect(s.scoreVar).toBe(1.25)
    expect(s.scoreStd).toBe(1.118)
    expect(s.scoreMin).toBe(1)
    expect(s.scoreMax).toBe(4)
    expect(s.outcomes).toEqual({ gameover: 3, max_ticks: 1 })
  })

  it('空样本安全（除零）', () => {
    const s = scoreStats([])
    expect(s.games).toBe(0)
    expect(s.winRate).toBe(0)
    expect(s.scoreMean).toBe(0)
    expect(s.scoreStd).toBe(0)
  })

  it('缺 score 字段按 0 计（旧 row 兼容）', () => {
    const s = scoreStats([row({}), row({})])
    expect(s.scoreMean).toBe(0)
    expect(s.scoreVar).toBe(0)
  })
})

describe('normalizeLevelArg', () => {
  it('c01 / 01 / 1 / ladder-c01 都归一成 ladder-c01', () => {
    for (const spec of ['c01', '01', '1', 'ladder-c01']) {
      expect(normalizeLevelArg(spec)).toEqual(['ladder-c01'])
    }
  })
  it('all 展开成 7 个探针级；逗号可用', () => {
    expect(normalizeLevelArg('all')).toEqual([...TEACHER_LEVELS])
    expect(normalizeLevelArg('c01, c07')).toEqual(['ladder-c01', 'ladder-c07'])
  })
})

describe('常量锚', () => {
  it('探针域 = c01-c07；EVAL_SEED0 对齐 Python EVAL_SEEDS 首值', () => {
    expect(TEACHER_LEVELS).toEqual([
      'ladder-c01',
      'ladder-c02',
      'ladder-c03',
      'ladder-c04',
      'ladder-c05',
      'ladder-c06',
      'ladder-c07',
    ])
    // nn-training/rl/eval_local.py: EVAL_SEEDS = tuple(range(860001, 860201))
    expect(EVAL_SEED0).toBe(860001)
  })
})
