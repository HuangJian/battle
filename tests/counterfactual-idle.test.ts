import { describe, it, expect } from 'bun:test'
import { classifyIdleEvent } from '../tools/diag/counterfactual-idle'
import type { BranchRecord } from '../tools/diag/counterfactual-idle'

/**
 * M3 counterfactual classifier (plan/God-AI-Hard-Open-Test-Protocol.md §6.3).
 * Pure-logic tests — the branch records are synthetic summaries of the four
 * replay branches (continue / turn-and-fire / move-to-intercept /
 * clear-or-advance), the same shape the tool persists to --json.
 */

function branch(over: Partial<BranchRecord> = {}): BranchRecord {
  return {
    checkpoints: [],
    firstBaseDamageOffset: -1,
    firstFireOffset: -1,
    firstMoveOffset: -1,
    targetDeathOffset: -1,
    playerDeathOffset: -1,
    endState: 'playing',
    rngStateEnd: 0,
    ...over,
  }
}

describe('classifyIdleEvent (M3 §6.3)', () => {
  it('idle_causal: continue loses base HP, an alternative avoids it', () => {
    const cont = branch({ firstBaseDamageOffset: 48, firstFireOffset: 48, firstMoveOffset: 37 })
    const tAndF = branch({ firstBaseDamageOffset: -1, firstFireOffset: 29, targetDeathOffset: 40 })
    expect(classifyIdleEvent(cont, [tAndF, branch(), branch()])).toBe('idle_causal')
  })

  it('idle_legitimate: continue also avoids base damage (the stall cost nothing)', () => {
    const cont = branch({ firstFireOffset: 52, firstMoveOffset: 31 })
    expect(classifyIdleEvent(cont, [branch(), branch(), branch()])).toBe('idle_legitimate')
  })

  it('travel_or_turn_causal: continue damaged and NO alternative avoids', () => {
    const cont = branch({ firstBaseDamageOffset: 44 })
    const alts = [
      branch({ firstBaseDamageOffset: 44, firstFireOffset: 35 }),
      branch({ firstBaseDamageOffset: 44, firstMoveOffset: 7 }),
      branch({ firstBaseDamageOffset: 87, firstFireOffset: 53, firstMoveOffset: 42 }),
    ]
    expect(classifyIdleEvent(cont, alts)).toBe('travel_or_turn_causal')
  })

  it('unresolved: continue damaged but no alternative ever acted (degenerate scripts)', () => {
    const cont = branch({ firstBaseDamageOffset: 41 })
    expect(classifyIdleEvent(cont, [branch(), branch(), branch()])).toBe('unresolved')
  })

  it('an avoiding alternative outranks non-avoiding acted ones (idle_causal wins)', () => {
    const cont = branch({ firstBaseDamageOffset: 158 })
    const alts = [
      branch({ firstBaseDamageOffset: -1 }), // avoided by standing (inaction)
      branch({ firstBaseDamageOffset: 176, firstMoveOffset: 56, firstFireOffset: 173 }),
      branch({ firstBaseDamageOffset: 176, firstMoveOffset: 56, firstFireOffset: 30 }),
    ]
    expect(classifyIdleEvent(cont, alts)).toBe('idle_causal')
  })

  it('legitimate takes precedence over unresolved when continue held the base', () => {
    // No alternative acted AND continue kept the base — the stall was fine.
    const cont = branch()
    expect(classifyIdleEvent(cont, [branch(), branch(), branch()])).toBe('idle_legitimate')
  })
})
