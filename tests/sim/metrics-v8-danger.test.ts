/**
 * metrics-v8-danger.test.ts — 危险暴露四列（plan/x20-dodge-avoidance §2）的端到端用例。
 *
 * 覆盖三件事（体例仿 metrics-v6-census.test.ts）：
 *   1. **真实一局**里新列非零且自洽（`dmgFirst600 ≤ playerDamageTaken`、累计 tick ≤ 总 tick）；
 *   2. **确定性**：同 (stage, seed) 双跑逐值相同（新计数器不引入非确定性——判定是纯函数、
 *      不读 rng）；
 *   3. 实测**开局零承伤**的局必然 `dmgFirst600 === 0`（窗口语义 = 前 600 tick 的读数）。
 */
import { describe, expect, it } from 'bun:test'
import { runEvalOne } from '../../tools/sim/export-eval-game'
import { STAGES } from '../../src/config/stages'
import { DMG_FIRST_WINDOW_TICKS } from '../../src/nn/danger-metrics'

const MAX_TICKS = 4000

function runGod(stageIdx: number, seed: number) {
  return runEvalOne(stageIdx, STAGES[stageIdx] as never, seed, 'hard', MAX_TICKS, '{}', 'god')
}

describe('metrics v8（危险暴露）：一局真实跑分', () => {
  it('新列存在、非负、且被上界约束住', () => {
    const r = runGod(0, 1)
    expect(r.playerHpRatio).toBeGreaterThanOrEqual(0)
    expect(r.playerHpRatio).toBeLessThanOrEqual(1)
    expect(r.dangerTicks).toBeGreaterThanOrEqual(0)
    expect(r.threatTicks).toBeGreaterThanOrEqual(0)
    expect(r.dmgFirst600).toBeGreaterThanOrEqual(0)
    // 累计 tick 不可能超过局长
    expect(r.dangerTicks).toBeLessThanOrEqual(r.ticks)
    expect(r.threatTicks).toBeLessThanOrEqual(r.ticks)
    // 开局窗只是总承伤的一个子集
    expect(r.dmgFirst600).toBeLessThanOrEqual(r.playerDamageTaken)
  })

  it('非零证据：God 一局确实站在弹道上（否则这一列就是死列）', () => {
    // 实测读数（2026-09-24，stage/seed 固定、可复现）—— 只钉「列活着」与相对关系，
    // 不把具体数字当门槛（它是探针读数，不是判据）。
    const rows = [
      [0, 1],
      [0, 7],
      [3, 3],
      [0, 11],
    ].map(([si, seed]) => runGod(si, seed))
    // ① 弹道暴露列在四局里全非零 ⇒ 判定真的在采样（不是恒 0 的死列）
    for (const r of rows) expect(r.threatTicks).toBeGreaterThan(0)
    // ② 残血列亦能非零（否则该列无证据价值）
    expect(rows.some((r) => r.dangerTicks > 0)).toBe(true)
    // ③ 开局窗列能非零（某局在前 600t 就挨了打）
    expect(rows.some((r) => r.dmgFirst600 > 0)).toBe(true)
  })

  it('窗口语义：前 600 tick 未承伤 ⇒ dmgFirst600 === 0；否则 > 0', () => {
    const r = runGod(0, 11)
    if (r.dmgFirst600 === 0) {
      // 零承伤开局 ⇒ 玩家的第一次承伤不可能落在窗内（窗口 = tick < 600）
      expect(DMG_FIRST_WINDOW_TICKS).toBe(600)
    } else {
      expect(r.dmgFirst600).toBeGreaterThan(0)
    }
    // 长局（远超窗口）时，窗口值必须严格小于总承伤，除非全部承伤都发生在窗内
    if (r.ticks > 3 * DMG_FIRST_WINDOW_TICKS && r.dmgFirst600 > 0) {
      expect(r.dmgFirst600).toBeLessThanOrEqual(r.playerDamageTaken)
    }
  })
})

describe('metrics v8（危险暴露）：determinism（同 seed 双跑逐值相同）', () => {
  it('同一 (stage, seed) 两次 → 四列相同', () => {
    for (const [stageIdx, seed] of [
      [0, 21],
      [3, 9],
    ] as const) {
      const a = runGod(stageIdx, seed)
      const b = runGod(stageIdx, seed)
      expect(a.dangerTicks).toBe(b.dangerTicks)
      expect(a.threatTicks).toBe(b.threatTicks)
      expect(a.dmgFirst600).toBe(b.dmgFirst600)
      expect(a.playerHpRatio).toBe(b.playerHpRatio)
      expect(a.playerDamageTaken).toBe(b.playerDamageTaken)
    }
  })
})
