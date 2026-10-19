/**
 * B4 哨兵（plan/nn-capability-upgrade.plan.md §4-B4 / 评审 Bf9）：
 * `postThawDamageRate` = 解冻后 60 拍内的 `player_damage` 份额。
 *
 * 口径在本文件钉死（避免实现时发明第二套「解冻」）：
 *   · 解冻帧 = `freezeTimer` 由 >0 变 ≤0 的那一拍；
 *   · 窗口 = `[thawFrame, thawFrame + 60)`（同帧命中算「解冻后」）；
 *   · 重新冻结 ⇒ 窗关；
 *   · 分母 = 整局 player_damage 之和，零承伤 ⇒ 0。
 */
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { STAGES } from '../../src/config/stages'
import {
  POST_THAW_WINDOW_TICKS,
  createPostThawState,
  postThawDamageRate,
  postThawTick,
  recordPostThawDamage,
  runEvalOne,
} from '../../tools/sim/export-eval-game'

describe('postThawDamageRate：窗口口径（纯状态机）', () => {
  it('窗口常量 = 60 拍（1 s）', () => {
    expect(POST_THAW_WINDOW_TICKS).toBe(60)
  })

  it('无解冻 ⇒ 全部承伤都在窗外，份额 0；零承伤也 0（不产生 NaN）', () => {
    const s = createPostThawState()
    for (let f = 0; f < 100; f++) postThawTick(s, 0, 0, f)
    recordPostThawDamage(s, 50, 7)
    expect(s.inWindow).toBe(0)
    expect(s.total).toBe(7)
    expect(postThawDamageRate(s)).toBe(0)

    const empty = createPostThawState()
    expect(postThawDamageRate(empty)).toBe(0)
  })

  it('解冻帧及其后 59 拍计入，第 60 拍起不计（左闭右开）', () => {
    const s = createPostThawState()
    postThawTick(s, 16, 0, 10) // 第 10 拍解冻
    recordPostThawDamage(s, 9, 100) // 冻着时的伤害：窗外
    recordPostThawDamage(s, 10, 1) // 同帧：窗内
    recordPostThawDamage(s, 69, 2) // thaw + 59
    recordPostThawDamage(s, 70, 4) // thaw + 60：窗外
    expect(s.inWindow).toBe(3)
    expect(s.total).toBe(107)
    expect(postThawDamageRate(s)).toBeCloseTo(3 / 107, 12)
  })

  it('窗口刷新：第二次解冻重新起算；重新冻结立刻关窗', () => {
    const s = createPostThawState()
    postThawTick(s, 16, 0, 10)
    recordPostThawDamage(s, 20, 5)
    postThawTick(s, 0, 16, 30) // 第 30 拍重新冻结 ⇒ 窗关
    recordPostThawDamage(s, 35, 9)
    postThawTick(s, 16, 0, 40) // 第 40 拍再次解冻
    recordPostThawDamage(s, 100, 1) // 40 + 60：窗外
    recordPostThawDamage(s, 41, 2)
    expect(s.inWindow).toBe(7)
    expect(s.total).toBe(17)
  })
})

describe('接线：eval 报告直出份额（不新增 metrics 列）', () => {
  const studentWeights = (() => {
    const g = JSON.parse(
      readFileSync(join(import.meta.dir, '..', 'fixtures', 'student-golden.json'), 'utf8'),
    ) as { h: number; d: number; params: Record<string, unknown> }
    return JSON.stringify({ arch: { kind: 'student', h: g.h, d: g.d }, params: g.params })
  })()

  it('返回值与分母自洽：rate = inWindow / playerDamageTaken', () => {
    const r = runEvalOne(0, STAGES[0] as never, 9, 'hard', 1500, studentWeights)
    expect(r.postThawDamage).toBeGreaterThanOrEqual(0)
    expect(r.postThawDamage).toBeLessThanOrEqual(r.playerDamageTaken)
    expect(r.postThawDamageRate).toBeGreaterThanOrEqual(0)
    expect(r.postThawDamageRate).toBeLessThanOrEqual(1)
    const want = r.playerDamageTaken > 0 ? r.postThawDamage / r.playerDamageTaken : 0
    expect(r.postThawDamageRate).toBeCloseTo(want, 12)
  })
})
