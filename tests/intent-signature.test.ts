import { describe, expect, it } from 'bun:test'
import { signatureIntent, type SigContext } from '../src/ai/intent/signature'

/**
 * M2 签名器单测：合成典型人像行为场景 → 断言输出意图（抽检 acc≥90% 的前置）。
 * 场景构造 = "已知样本"（行为的规范定义，避免循环论证）。
 */
function ctx(partial: Partial<SigContext>): SigContext {
  return {
    playerCell: { col: 10, row: 20 },
    moveDir: 'up',
    facingDir: 'up',
    firing: false,
    nearestEnemy: { col: 10, row: 6, dist: 14 },
    enemyAligned: true,
    wallAhead: false,
    baseThreat: false,
    baseDist: 12,
    pickupNear: false,
    ...partial,
  }
}

describe('M2 人像签名标签器（判据镜像、宁缺勿错）', () => {
  it('驻防态向威胁开火 → INTERCEPT（敌对齐∧距基地近∧开火）', () => {
    const c = ctx({
      firing: true,
      baseThreat: true,
      baseDist: 6,
      moveDir: null,
      enemyAligned: true,
    })
    expect(signatureIntent(c)).toBe('INTERCEPT')
  })

  it('回防赶路：向基地移动 + 断火 → RETURN_DEFENSE', () => {
    // 基地 (12,24)：玩家 (4,8) → 向右下赶；moveDir 主轴 toward base。
    const c = ctx({ playerCell: { col: 4, row: 8 }, moveDir: 'down', firing: false, baseDist: 24 })
    expect(signatureIntent(c)).toBe('RETURN_DEFENSE')
  })

  it('站桩朝列敌开火 → HOLD_LANE', () => {
    const c = ctx({ firing: true, moveDir: null, enemyAligned: true })
    expect(signatureIntent(c)).toBe('HOLD_LANE')
  })

  it('朝敌移动 + 开火 → HUNT', () => {
    const c = ctx({ firing: true, moveDir: 'up', nearestEnemy: { col: 10, row: 6, dist: 14 } })
    expect(signatureIntent(c)).toBe('HUNT')
  })

  it('面向墙持续开火 → CLEAR', () => {
    const c = ctx({ firing: true, moveDir: null, enemyAligned: false, wallAhead: true })
    expect(signatureIntent(c)).toBe('CLEAR')
  })

  it('近道具移动不交战 → PICKUP', () => {
    const c = ctx({ firing: false, moveDir: 'right', pickupNear: true })
    expect(signatureIntent(c)).toBe('PICKUP')
  })

  it('无聚焦漫游 → CRUISE', () => {
    const c = ctx({ firing: false, moveDir: 'down', pickupNear: false, enemyAligned: false })
    expect(signatureIntent(c)).toBe('CRUISE')
  })

  it('静止无聚焦 → null（宁缺勿错）', () => {
    const c = ctx({ firing: false, moveDir: null, pickupNear: false, enemyAligned: false })
    expect(signatureIntent(c)).toBeNull()
  })

  it('判据优先级：驻防拦截 > 回防 > 驻守（同帧多候选只取最高）', () => {
    // 同时满足 INTERCEPT（对齐∧baseThreat∧近基地∧开火）与 HOLD_LANE → INTERCEPT。
    const c = ctx({
      firing: true,
      baseThreat: true,
      baseDist: 5,
      moveDir: null,
      enemyAligned: true,
    })
    expect(signatureIntent(c)).toBe('INTERCEPT')
    // 同时 HUNT（朝敌开火）与 CLEAR（墙+开火）→ HUNT 优先。
    const h = ctx({ firing: true, moveDir: 'up', wallAhead: true })
    expect(signatureIntent(h)).toBe('HUNT')
  })

  it('已到基地同格不判回防（dirTowardBase 无主向）', () => {
    const c = ctx({ playerCell: { col: 12, row: 24 }, moveDir: 'up', firing: false, baseDist: 0 })
    const r = signatureIntent(c)
    expect(r).not.toBe('RETURN_DEFENSE')
  })
})
