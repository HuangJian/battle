/**
 * P′-0：simulation-runner 的批量契约。
 *   · 同 seed 两次运行结果一致（replay 逐字节）；
 *   · branchTotals 透传可用；
 *   · 未知 policy / B8+B10 同开必须响亮失败（fail-closed）。
 */
import { describe, it, expect } from 'bun:test'
import {
  assertSimPolicy,
  executorSeed,
  runSimulation,
  LOCAL_ONLY_POLICIES,
  SIM_POLICIES,
  type RunOptions,
} from '../tools/sim/simulation-runner'
import { STAGES } from '../src/config/stages'
import { canonicalizeIds } from './support/canonicalize-ids'

const base: RunOptions = {
  seed: 987654321,
  stage: STAGES[0],
  stageIndex: 0,
  difficulty: 'hard',
  maxTicks: 400,
  sampleInterval: 60,
  collectMetrics: false,
  collectEvents: false,
  policy: 'god',
  branchTotals: true,
}

describe('simulation-runner P′-0 contracts', () => {
  it('policy 白名单 fail-closed', () => {
    expect(assertSimPolicy(undefined)).toBe('god')
    expect(assertSimPolicy('intent-oracle')).toBe('intent-oracle')
    expect(assertSimPolicy('intent-search')).toBe('intent-search')
    expect(assertSimPolicy('intent-option')).toBe('intent-option')
    expect(() => assertSimPolicy('bogus')).toThrow(/unknown policy/)
    expect([...SIM_POLICIES]).toContain('goal-god')
    expect([...SIM_POLICIES]).toContain('intent-search')
    expect([...SIM_POLICIES]).toContain('intent-option')
    expect([...LOCAL_ONLY_POLICIES]).toContain('intent-oracle')
    expect([...LOCAL_ONLY_POLICIES]).toContain('intent-option')
    expect([...LOCAL_ONLY_POLICIES]).toContain('intent-search')
    expect(() => runSimulation({ ...base, policy: 'bogus' as never })).toThrow(/unknown policy/)
  })

  it('B8/B10 同开 = 配置错误（同一机制不组合）', () => {
    expect(() =>
      runSimulation({ ...base, suppressUselessFire: true, suppressLeadMissFire: true }),
    ).toThrow(/mutually exclusive/)
  })

  it('同 seed 两次运行逐字节一致（含 replay）', () => {
    const r1 = runSimulation({ ...base, record: true })
    const r2 = runSimulation({ ...base, record: true })
    expect(r2.outcome).toBe(r1.outcome)
    expect(r2.ticks).toBe(r1.ticks)
    expect(r2.finalState).toEqual(r1.finalState)
    expect(r2.firstKillTick).toBe(r1.firstKillTick)
    expect(r2.failure).toEqual(r1.failure)
    expect(r2.branchTotals).toEqual(r1.branchTotals)
    expect(r1.replay).toBeDefined()
    expect(r2.replay!.tickCount).toBe(r1.replay!.tickCount)
    expect(canonicalizeIds(r2.replay!.initialSnapshot)).toEqual(
      canonicalizeIds(r1.replay!.initialSnapshot),
    )
    expect(r2.replay!.frames).toEqual(r1.replay!.frames)
  })

  it('branchTotals 只在显式请求时返回，且 key 集来自 input.branchCounts', () => {
    const r = runSimulation(base)
    expect(r.branchTotals).toBeDefined()
    expect(r.branchTotals!.navigate).toBeGreaterThanOrEqual(0)
    expect(Object.keys(r.branchTotals!).length).toBeGreaterThan(5)
    expect(runSimulation({ ...base, branchTotals: false }).branchTotals).toBeUndefined()
  })

  it('executorSeed 是单一派生口径', () => {
    expect(executorSeed(123)).toBe((123 ^ 0x9e3779b9) >>> 0)
  })

  it('playerLevel 覆盖任务口径（0★）', () => {
    const short = { ...base, maxTicks: 5, branchTotals: false }
    expect(runSimulation({ ...short, playerLevel: 0 }).finalState.playerLevel).toBe(0)
    expect(runSimulation({ ...short, playerLevel: 1 }).finalState.playerLevel).toBe(1)
  })
})
