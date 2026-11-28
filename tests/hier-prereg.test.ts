/**
 * P′-0：三池预注册必须是机器可读且自带断言。
 * 任何「seed 重叠 / 阈值缺失 / 命令缺失 / 口径回退」都必须报错，而不是静默通过。
 */
import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parseSeedSpec, validatePrereg } from '../tools/sim/prereg'

const PREREG_PATH = join(import.meta.dir, '..', 'plan', 'hier-options-prereg.v1.json')

function load(): Record<string, unknown> {
  return JSON.parse(readFileSync(PREREG_PATH, 'utf8')) as Record<string, unknown>
}

describe('hier-options prereg v1', () => {
  it('落盘文件通过全部断言', () => {
    const res = validatePrereg(load())
    expect(res.errors).toEqual([])
    expect(res.ok).toBe(true)
  })

  it('seed 区间解析（含端点；必须 from ≤ to）', () => {
    expect(parseSeedSpec('2000000001-2000000020')).toEqual({
      from: 2000000001,
      to: 2000000020,
    })
    expect(parseSeedSpec('2000000001-20')).toBeNull() // 文档简写不是合法机器输入
    expect(parseSeedSpec('1-20')).toEqual({ from: 1, to: 20 })
    expect(parseSeedSpec('x-y')).toBeNull()
  })

  it('池重叠 / 阈值缺失 / 命令缺失 / 95 回退 都会报错', () => {
    const overlap = structuredClone(load()) as {
      pools: { sealed: { seeds: string } }
      p2a: { A: { confirm_lcb?: number } }
      commands: Record<string, string>
      eval: { p95: string }
    }
    overlap.pools.sealed.seeds = '2000000001-2000000030'
    expect(validatePrereg(overlap).errors.some((e) => e.includes('overlaps'))).toBe(true)

    const noThreshold = structuredClone(load()) as {
      pools: { sealed: { seeds: string } }
      p2a: { A: { confirm_lcb?: number } }
      commands: Record<string, string>
      eval: { p95: string }
    }
    delete noThreshold.p2a.A.confirm_lcb
    expect(validatePrereg(noThreshold).errors.some((e) => e.includes('confirm_lcb'))).toBe(true)

    const noCommand = structuredClone(load()) as {
      pools: { sealed: { seeds: string } }
      p2a: { A: { confirm_lcb?: number } }
      commands: Record<string, string>
      eval: { p95: string }
    }
    delete noCommand.commands.p1
    expect(validatePrereg(noCommand).errors.some((e) => e.includes('commands.p1'))).toBe(true)

    const p95 = structuredClone(load()) as {
      pools: { sealed: { seeds: string } }
      p2a: { A: { confirm_lcb?: number } }
      commands: Record<string, string>
      eval: { p95: string }
    }
    p95.eval.p95 = 'planned'
    expect(validatePrereg(p95).errors.some((e) => e.includes('eval.p95'))).toBe(true)
  })
})
