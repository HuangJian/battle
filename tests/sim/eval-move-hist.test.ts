import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'

/**
 * 新纪元死刑通道（plan/new-era-stop.plan.md §2 #9/P1-2 方案 a）：
 * runEvalOne 每局附带决策动作直方图 + 物理 idle 计数 + stop 段长（可选字段，
 * 旧 bundle/旧读数方缺席即无信号）。本测试钉住三条一致性：
 *   ① sum(moveHist) == decisions（直方图与决策数对账）；
 *   ② stopPickRate = moveHist[0]/decisions ∈ [0,1]（意图读数有界）；
 *   ③ stopRuns 各段长度之和 ≤ decisions（段长不噬总数），idleTicks ≤ ticks。
 *
 * 权重用 tests/fixtures/student-golden.json（瘦身规格，forward 便宜）。
 */
function runShort(stageIdx: number, seed: number) {
  const g = JSON.parse(
    readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
  ) as { h: number; d: number; params: Record<string, unknown> }
  const weightsText = JSON.stringify({
    arch: { kind: 'student', h: g.h, d: g.d },
    params: g.params,
  })
  return runEvalOne(stageIdx, STAGES[stageIdx], seed, 'hard', 3000, weightsText, 'nn')
}

describe('runEvalOne 死刑通道读数一致性（moveHist/decisions/idleTicks/stopRuns）', () => {
  it('stage 0 seed 1：直方图求和 = 决策数，读数有界', () => {
    const r = runShort(0, 1) as unknown as Record<string, unknown>
    const moveHist = r['moveHist'] as number[]
    const decisions = r['decisions'] as number
    const idleTicks = r['idleTicks'] as number
    const stopRuns = r['stopRuns'] as number[]
    const ticks = r['ticks'] as number
    expect(Array.isArray(moveHist)).toBe(true)
    expect(moveHist.length).toBe(5)
    expect(decisions).toBeGreaterThan(0)
    expect(moveHist.reduce((a, b) => a + b, 0)).toBe(decisions)
    const stopPickRate = (moveHist[0] as number) / (decisions as number)
    expect(stopPickRate >= 0 && stopPickRate <= 1).toBe(true)
    expect(stopRuns.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(decisions)
    expect(idleTicks).toBeGreaterThanOrEqual(0)
    expect(idleTicks).toBeLessThanOrEqual(ticks)
  })

  it('stage 0 seed 2：同一契约成立（换种子不翻）', () => {
    const r = runShort(0, 2) as unknown as Record<string, unknown>
    const moveHist = r['moveHist'] as number[]
    const decisions = r['decisions'] as number
    expect(moveHist.reduce((a, b) => a + b, 0)).toBe(decisions)
  })
})
