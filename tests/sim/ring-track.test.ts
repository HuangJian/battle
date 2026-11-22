/**
 * ring-track.test.ts — metrics v18「环内事件流」的两条判定（几何 + 进/出环记账）。
 *
 * 为什么单独钉（2026-11-22，g11-ringkill 立课）：
 *   · 几何（`inBaseIntruderRing`）是 rollout 侧**唯一**实现的环定义，eval 侧 inline 同式；
 *     6 格边界差一格 = 训练在罚另一个环，而它只在"重放整段"里出现，训练看不出异常。
 *   · 记账（`syncRingMembers`）决定 `ringIntrudedCum` 与"环内击杀"的归因口径：
 *     出生保护中的车不算入侵、出环再进算第二次、出环后再杀**不算**环内击杀
 *     （最后这条由调用方在 `tank_destroyed` 里 `delete` 的返回值决定，本文件用集合状态钉住）。
 */
import { describe, expect, it } from 'bun:test'
import {
  BASE_INTRUDER_CELLS,
  baseRingCenterX,
  baseRingCenterY,
  inBaseIntruderRing,
  syncRingMembers,
  type RingTrackable,
} from '../../src/nn/danger-metrics'
import { CELL, TANK } from '../../src/constants'

/** 造一辆中心落在基地正右方 `dCells` 格处的敌车（未在出生保护中）。 */
function enemyAt(dCells: number, over: Partial<RingTrackable> = {}): RingTrackable {
  const cx = baseRingCenterX() + dCells * CELL
  const cy = baseRingCenterY()
  return {
    id: Math.round(dCells * 1000),
    x: cx - TANK / 2,
    y: cy - TANK / 2,
    w: TANK,
    h: TANK,
    alive: true,
    allegiance: 'enemy',
    spawnTimer: 0,
    ...over,
  }
}

describe('metrics v18：基地入侵环几何', () => {
  it('Chebyshev ≤ 6 格在环内（含边界），6 格 + 1px 出环', () => {
    expect(BASE_INTRUDER_CELLS).toBe(6)
    expect(inBaseIntruderRing(enemyAt(6))).toBe(true)
    expect(inBaseIntruderRing(enemyAt(0))).toBe(true)
    expect(inBaseIntruderRing(enemyAt(6.5))).toBe(false)
    // 恰好在边界外沿 1px：用 w/h 把中心推到 6*CELL + 1
    const just = enemyAt(6)
    expect(inBaseIntruderRing({ ...just, x: just.x + 1 })).toBe(false)
  })

  it('是对角上的 Chebyshev（不是欧氏）：两轴各 6 格仍在环内', () => {
    const cx = baseRingCenterX() + 6 * CELL
    const cy = baseRingCenterY() + 6 * CELL
    const diag: RingTrackable = {
      ...enemyAt(0),
      x: cx - TANK / 2,
      y: cy - TANK / 2,
    }
    expect(inBaseIntruderRing(diag)).toBe(true)
  })
})

describe('metrics v18：进/出环记账（syncRingMembers）', () => {
  it('出生保护中的车不算入侵；解除后进环记一次，停留不重复计', () => {
    const ring = new Set<number>()
    const protectedTank = enemyAt(2, { spawnTimer: 30 })
    expect(syncRingMembers([protectedTank], ring)).toBe(0)
    expect(ring.size).toBe(0)
    const live = enemyAt(2)
    expect(syncRingMembers([live], ring)).toBe(1)
    expect(syncRingMembers([live], ring)).toBe(0) // 停留
    expect(ring.has(live.id)).toBe(true)
  })

  it('出环即摘；**再次进环算第二次**（同一辆车两次入侵）', () => {
    const ring = new Set<number>()
    const t = enemyAt(2)
    expect(syncRingMembers([t], ring)).toBe(1)
    const out = { ...t, x: t.x + 20 * CELL }
    expect(syncRingMembers([out], ring)).toBe(0)
    expect(ring.has(t.id)).toBe(false)
    expect(syncRingMembers([t], ring)).toBe(1) // 第二次
    expect(syncRingMembers([t], ring)).toBe(0)
  })

  it('玩家/已阵亡/非敌车一律不记账', () => {
    const ring = new Set<number>()
    const tanks: RingTrackable[] = [
      enemyAt(1, { id: 1, allegiance: 'player' }),
      enemyAt(1, { id: 2, alive: false }),
      enemyAt(1, { id: 3 }),
    ]
    expect(syncRingMembers(tanks, ring)).toBe(1)
    expect([...ring]).toEqual([3])
  })

  it('出环后再杀**不算**环内击杀：集合里没有它（调用方 delete 返回 false）', () => {
    const ring = new Set<number>()
    const t = enemyAt(2)
    syncRingMembers([t], ring)
    // 走了再说：出环 → 摘除
    syncRingMembers([{ ...t, x: t.x + 20 * CELL }], ring)
    expect(ring.delete(t.id)).toBe(false) // ⇒ 调用方不会 ringKilledCum++
    // 而"在环内被打死"的车仍在集合里 ⇒ delete 返回 true（= 记一次环内击杀）
    syncRingMembers([t], ring)
    expect(ring.delete(t.id)).toBe(true)
  })
})
