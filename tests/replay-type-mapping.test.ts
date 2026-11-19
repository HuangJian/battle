import { describe, it, expect } from 'bun:test'
import { replayTypeFromOutcome, type ReplayType, type SimOutcome } from '../src/replay/types'

// ============================================================
// 终局四态 → ReplayType 的唯一映射
//
// 2026-11-16 事故：`export-rl-rollout.ts` / `export-nn-replays.ts` 都曾按
// `outcome === 'gameover'` 判定 'died'，而 `'gameover'` 是 `world.state` 的取值、
// **不是 outcome**（outcome 只有 stage_clear / base_destroyed / lives_exhausted / timeout）
// ⇒ 该分支永不命中，`lives_exhausted` 局全被写成 `status = 'timeout'`。
// 后果：replay 语料转换器 `final status died != recorded timeout` 硬失败
// （实测 stage 5 / seed 2000000001，maxTicks=12000，第 3452 tick 三命打光）。
// ============================================================

/** 与 `src/replay/types.ts::SimOutcome` 及 Python `biz/reward_library.py::OUTCOMES` 同集合。 */
const ALL_OUTCOMES: SimOutcome[] = ['stage_clear', 'base_destroyed', 'lives_exhausted', 'timeout']

const EXPECTED: Record<SimOutcome, ReplayType> = {
  stage_clear: 'clear',
  base_destroyed: 'base',
  lives_exhausted: 'died',
  timeout: 'timeout',
}

describe('replayTypeFromOutcome', () => {
  it('四态逐一映射（truth table）', () => {
    for (const outcome of ALL_OUTCOMES) {
      expect(replayTypeFromOutcome(outcome)).toBe(EXPECTED[outcome])
    }
  })

  it('lives_exhausted 必须映射到 died（回归：曾全部落 timeout）', () => {
    expect(replayTypeFromOutcome('lives_exhausted')).toBe('died')
  })

  it("'gameover' 不是 outcome，不得被当成 died 的判定条件", () => {
    // 落回 timeout 是**有意的**：ReplayType 只有四态，未知值只能取兜底。
    // 真出问题的是调用方按 'gameover' 判定 —— 那样 lives_exhausted 就永远拿不到 'died'。
    expect(replayTypeFromOutcome('gameover')).toBe('timeout')
    expect(replayTypeFromOutcome('lives_exhausted')).not.toBe(replayTypeFromOutcome('gameover'))
  })

  it('未知值兜底 timeout，不抛', () => {
    expect(replayTypeFromOutcome('')).toBe('timeout')
    expect(replayTypeFromOutcome('defeat')).toBe('timeout')
  })

  it('映射是全覆盖的：没有哪个 outcome 落到 undefined', () => {
    for (const outcome of ALL_OUTCOMES) {
      expect(['clear', 'base', 'died', 'timeout']).toContain(replayTypeFromOutcome(outcome))
    }
  })
})
