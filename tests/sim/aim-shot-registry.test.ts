/**
 * aim-shot-registry.test.ts — settle-once / 所有权 / 交棒弹 真值表
 * （plan/aim-dodge-levers.plan.md §7.1「settle-once 真值表（sb P0-B）」）。
 *
 * 被测 = 训练侧与评估侧**共用**的 `tools/sim/aim-shot-registry.ts`（单一实现）。
 * 本文件不看两个导出器内部，只按文档口径自己写一个极小的消费循环
 * （fire → hit/brick/cancel/hurt → 局末兜底），把四条不变量钉死：
 *   ① 同 tick 开火+命中：登记先于结算 ⇒ 结算必命中；
 *   ② 一发拆 2×2 四格（同 bulletId 四条 terrain_destroyed）⇒ 只落一次 brick；
 *   ③ 未结算到局末 ⇒ 兜底 miss（出界/打钢未破/基地/局末在飞同一条路）；
 *   ④ 交棒弹（无 bullet_fired 记录）⇒ 不入任何桶；恒等式仍严格
 *      （misses = 登记数 − 已结算数）。
 * 另含所有权（玩家结算拿不到敌弹，反之亦然）与对消双边语义。
 */
import { describe, expect, it } from 'bun:test'
import {
  registerEnemyAimShot,
  registerPlayerAimShot,
  takeAimShot,
  type AimShotTable,
} from '../../tools/sim/aim-shot-registry'
import type { Bullet } from '../../src/types'

/** 类型占位：registry 只存引用（不进 gameplay），测试不需要真弹对象。 */
const dummy = {} as Bullet

type Ev =
  | { t: 'fireP'; id: number }
  | { t: 'fireE'; id: number }
  | { t: 'hit'; id: number | undefined }
  | { t: 'brick'; id: number | undefined }
  | { t: 'cancel'; aId: number | undefined; bId: number | undefined }
  | { t: 'hurt'; id: number | undefined }

/** 极简消费循环（独立重实现：只看文档口径，不 import 导出器的结算代码）。 */
function consume(events: Ev[]): {
  hits: number
  bricks: number
  ignited: number
  hurt: number
  misses: number
} {
  const table: AimShotTable = new Map()
  const out = { hits: 0, bricks: 0, ignited: 0, hurt: 0, misses: 0 }
  for (const e of events) {
    if (e.t === 'fireP') registerPlayerAimShot(table, e.id, 0, 0, 0, dummy)
    else if (e.t === 'fireE') registerEnemyAimShot(table, e.id, 0, dummy)
    else if (e.t === 'hit') {
      // 玩家弹命中（含致死）：所有权 = 玩家。
      if (e.id !== undefined && takeAimShot(table, e.id, true)) out.hits++
    } else if (e.t === 'brick') {
      // 真拆砖/破钢：一发多格多条事件 ⇒ 第二次 take 必得 null（settle-once）。
      if (e.id !== undefined && takeAimShot(table, e.id, true)) out.bricks++
    } else if (e.t === 'cancel') {
      // 对消：a/b 至多一边是玩家弹；敌侧 take(player) 返回 null，不计数。
      if (e.aId !== undefined && takeAimShot(table, e.aId, true)) out.ignited++
      if (e.bId !== undefined && takeAimShot(table, e.bId, true)) out.ignited++
    } else if (e.t === 'hurt') {
      // 敌弹火线承伤结算：所有权 = 敌方。
      if (e.id !== undefined && takeAimShot(table, e.id, false)) out.hurt++
    }
  }
  // 局末兜底：仍未结算的**玩家**登记弹 ⇒ miss；敌弹权重直接丢弃。
  for (const rec of table.values()) if (rec.isPlayer) out.misses++
  return out
}

describe('shot registry：settle-once 真值表（sb P0-B）', () => {
  it('① 同 tick 开火+命中：登记先于结算 ⇒ hit 落桶、无兜底 miss', () => {
    expect(
      consume([
        { t: 'fireP', id: 1 },
        { t: 'hit', id: 1 },
      ]),
    ).toEqual({
      hits: 1,
      bricks: 0,
      ignited: 0,
      hurt: 0,
      misses: 0,
    })
  })

  it('② 一发 power≥2 拆 2×2 四格（同 id 四条事件）⇒ 只落一次 brick', () => {
    const out = consume([
      { t: 'fireP', id: 2 },
      { t: 'brick', id: 2 },
      { t: 'brick', id: 2 },
      { t: 'brick', id: 2 },
      { t: 'brick', id: 2 },
    ])
    expect(out.bricks).toBe(1)
    expect(out.misses).toBe(0)
  })

  it('③ 未结算到局末 ⇒ 兜底 miss；重复结算事件不再计', () => {
    expect(consume([{ t: 'fireP', id: 3 }]).misses).toBe(1)
    // 重复 hit（理论不可达）也只计一次
    const dup = consume([
      { t: 'fireP', id: 4 },
      { t: 'hit', id: 4 },
      { t: 'hit', id: 4 },
    ])
    expect(dup.hits).toBe(1)
    expect(dup.misses).toBe(0)
  })

  it('④ 交棒弹（无 bullet_fired 记录）⇒ 不入任何桶；恒等式仍严格', () => {
    // 交棒瞬间已在飞的弹：本局没有它的 bullet_fired ⇒ 任何结算尝试都拿不到 rec。
    const out = consume([
      { t: 'fireP', id: 10 },
      { t: 'hit', id: 999 }, // 交棒弹命中（不可归因）
      { t: 'brick', id: 998 },
      { t: 'hurt', id: 997 },
      { t: 'cancel', aId: 996, bId: 995 },
    ])
    expect(out).toEqual({ hits: 0, bricks: 0, ignited: 0, hurt: 0, misses: 1 })
    // 恒等式由构造保证：登记数（1）= 已结算（0）+ 兜底 miss（1）
    expect(1).toBe(out.hits + out.bricks + out.ignited + out.misses)
  })

  it('对消双边：a/b 各查一次，只有玩家那一边落 ignited', () => {
    const out = consume([
      { t: 'fireP', id: 20 },
      { t: 'fireE', id: 21 },
      { t: 'cancel', aId: 20, bId: 21 },
    ])
    expect(out.ignited).toBe(1)
    expect(out.hits + out.bricks + out.ignited + out.misses).toBe(1) // 玩家弹已结算
  })

  it('所有权：玩家结算路径拿不到敌弹记录（反之亦然）', () => {
    const table: AimShotTable = new Map()
    registerEnemyAimShot(table, 30, 0, dummy)
    expect(takeAimShot(table, 30, true)).toBeNull() // 错侧 ⇒ 不结算、不出表
    const rec = takeAimShot(table, 30, false)
    expect(rec).not.toBeNull()
    expect(rec!.isPlayer).toBe(false)
    expect(takeAimShot(table, 30, false)).toBeNull() // settle-once：已出表
    // 未知 id（交棒弹/从未开火）
    expect(takeAimShot(table, 31, true)).toBeNull()
    expect(takeAimShot(table, 31, false)).toBeNull()
  })

  it('登记保真：玩家弹的 fireCol/fireRow/patchFrom 原样可取（d 端点与回写起点）', () => {
    const table: AimShotTable = new Map()
    registerPlayerAimShot(table, 40, 7, 9, 12, dummy)
    const rec = takeAimShot(table, 40, true)!
    expect(rec.fireCol).toBe(7)
    expect(rec.fireRow).toBe(9)
    expect(rec.patchFrom).toBe(12)
    expect(rec.laneWeight).toBe(0)
  })
})
