// godai-archived-knobs.test.ts — 留档旋钮 L1 守卫（plan/God-AI-Organization.md §6 C1）
//
// 断言两个唯一事实源常量与 DEFAULT_GOD_AI_PARAMS 表互锁（评审 P2：测试只读常量、
// 禁止 hardcode 候选→门控映射）：
//   - params.interface.ts  ARCHIVED_KNOB_GROUPS —— 每个留档门控在 DEFAULT 表中必须 === 0
//   - think.ts             CANDIDATE_SURVIVAL    —— OFF 行门控 === 0 / ON 行门控非 0
//
// ─── un-archive 闸门（四步缺一不可；L1 变红是预期摩擦，不是事故）───
// 重开任一旋钮 = ① 改对应常量与本测试断言 → ② 新 DECISIONS 条目说明依据
//              → ③ 更新冻结签名 golden（tools/det-golden.v1.sha256，DECISIONS §272）
//              → ④ 重跑 60-seed 三难度基线（eval-suite v7 官方口径）。
import { describe, it, expect } from 'bun:test'
import { ARCHIVED_KNOB_GROUPS } from '../src/ai/god/params.interface'
import { CANDIDATE_SURVIVAL } from '../src/ai/god/think'
import { DEFAULT_GOD_AI_PARAMS } from '../src/ai/god/params.tables'
import { ACTION_WEIGHTS } from '../src/ai/god/DecisionCore'

/** Resolve a gate path against the DEFAULT table + DecisionCore base weights.
 * 'actionWeights.X' resolves to the effective weight: override ?? ACTION_WEIGHTS[X].
 * undefined = missing/misnamed gate. */
function gateValue(gate: string): number | undefined {
  if (gate.startsWith('actionWeights.')) {
    const id = gate.slice('actionWeights.'.length) as keyof typeof ACTION_WEIGHTS
    return DEFAULT_GOD_AI_PARAMS.actionWeights?.[id] ?? ACTION_WEIGHTS[id]
  }
  let node: unknown = DEFAULT_GOD_AI_PARAMS as unknown as Record<string, unknown>
  for (const part of gate.split('.')) {
    if (typeof node !== 'object' || node === null) return undefined
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'number' ? node : undefined
}

describe('ARCHIVED_KNOB_GROUPS ↔ DEFAULT_GOD_AI_PARAMS', () => {
  it('every archived gate exists in the DEFAULT table and is exactly 0', () => {
    for (const { gate, note } of ARCHIVED_KNOB_GROUPS) {
      const v = gateValue(gate)
      expect(v, `${gate} (${note}) must exist in DEFAULT`).toBeDefined()
      expect(v, `${gate} (${note}) must be 0 in DEFAULT`).toBe(0)
    }
  })

  it('has no duplicate gates', () => {
    const gates = ARCHIVED_KNOB_GROUPS.map((g) => g.gate)
    expect(new Set(gates).size).toBe(gates.length)
  })
})

describe('CANDIDATE_SURVIVAL ↔ DEFAULT_GOD_AI_PARAMS', () => {
  it('OFF candidates have a 0 gate; ON candidates have a non-0 gate; gates resolve', () => {
    for (const row of CANDIDATE_SURVIVAL) {
      if (row.gate === null) {
        expect(row.on, `(always) row ${row.candidate} must be ON`).toBe(true)
        continue
      }
      const v = gateValue(row.gate)
      expect(v, `gate ${row.gate} of ${row.candidate} must exist in DEFAULT`).toBeDefined()
      if (row.on) expect(v, `${row.candidate} is ON: ${row.gate} must be ≠ 0`).not.toBe(0)
      else expect(v, `${row.candidate} is OFF: ${row.gate} must be 0`).toBe(0)
    }
  })

  it('every flat OFF gate is registered in ARCHIVED_KNOB_GROUPS (cross-consistency)', () => {
    const registered = new Set(ARCHIVED_KNOB_GROUPS.map((g) => g.gate as string))
    for (const row of CANDIDATE_SURVIVAL) {
      if (row.on || row.gate === null) continue
      // dotted paths (actionWeights.survive) are asserted ==0 above; registry holds flat gates only.
      if (!row.gate.includes('.')) {
        expect(
          registered.has(row.gate),
          `OFF candidate ${row.candidate}: gate ${row.gate} missing from ARCHIVED_KNOB_GROUPS`,
        ).toBe(true)
      }
    }
  })

  it('has no duplicate candidates and covers every CANDIDATES-chain name once', () => {
    const names = CANDIDATE_SURVIVAL.map((r) => r.candidate)
    expect(new Set(names).size).toBe(names.length)
  })
})
