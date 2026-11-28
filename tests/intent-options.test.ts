/**
 * P′-4a：option 联合槽契约（预注册 p4 映射 / 掩码 / target 规则）。
 * 纯函数测试——不依赖 World（nearestBaseAnchor 的几何由 hier-option-executor 的
 * 集成用例覆盖）。
 */
import { describe, expect, it } from 'bun:test'
import {
  INTENT_TO_OPTION_SLOT,
  OPTION_DIM,
  OPTION_INJECT_DIM,
  OPTION_SLOTS,
  OPTION_SLOT_IDS,
  OPTION_SLOT_MASK,
  OPTION_SLOT_SHAPING_MULT,
  buildOptionCommandFromContext,
  intentIndexToOptionSlot,
  isOptionSlotValid,
  optionSlotId,
  optionSlotShapingMult,
  type OptionCommandContext,
} from '../src/ai/intent/options'
import { INTENT_IDS } from '../src/ai/intent/vocab'

const NO_TARGET: OptionCommandContext = {
  hasEnemy: false,
  hasPowerUp: false,
  threatened: false,
  anchor: null,
}
const ENEMY: OptionCommandContext = { ...NO_TARGET, hasEnemy: true }
const ANCHOR = { col: 6, row: 24 }
const HOLDING: OptionCommandContext = {
  ...NO_TARGET,
  hasEnemy: true,
  threatened: true,
  anchor: ANCHOR,
}
const ANCHOR_ONLY: OptionCommandContext = {
  ...NO_TARGET,
  hasEnemy: true,
  anchor: ANCHOR,
}

describe('P′-4a option slot vocab', () => {
  it('6 槽与注入维/掩码（HOLD_break 整槽禁用）', () => {
    expect(OPTION_DIM).toBe(6)
    expect(OPTION_INJECT_DIM).toBe(7)
    expect(OPTION_SLOTS.map((s) => s.id)).toEqual([
      'HUNT_direct',
      'HUNT_break',
      'INTERCEPT_BASE_direct',
      'INTERCEPT_BASE_break',
      'HOLD_direct',
      'HOLD_break',
    ])
    expect(OPTION_SLOT_IDS[5]).toBe('HOLD_break')
    expect(OPTION_SLOT_MASK).toEqual([1, 1, 1, 1, 1, 0])
    expect(isOptionSlotValid(5)).toBe(false)
    expect(isOptionSlotValid(4)).toBe(true)
    expect(isOptionSlotValid(-1)).toBe(false)
    expect(isOptionSlotValid(6)).toBe(false)
    expect(optionSlotId(1)).toBe('HUNT_break')
    expect(optionSlotId(99)).toBe('?')
  })

  it('8 意图 → 槽 = 预注册 option3 映射 + CLEAR→break', () => {
    expect(INTENT_IDS.map((id) => INTENT_TO_OPTION_SLOT[id])).toEqual([2, 2, 0, 4, 1, 0, 0, 4])
    expect(INTENT_IDS.map((_, i) => intentIndexToOptionSlot(i))).toEqual([2, 2, 0, 4, 1, 0, 0, 4])
    expect(intentIndexToOptionSlot(99)).toBe(0)
  })

  it('shaping 权重镜像防守类放大（HOLD 1.6 / IB 1.2 / HUNT 1.0）', () => {
    expect(OPTION_SLOT_SHAPING_MULT).toEqual([1.0, 1.0, 1.2, 1.2, 1.6, 1.0])
    expect(optionSlotShapingMult(4)).toBe(1.6)
    expect(optionSlotShapingMult(99)).toBe(1.0)
  })
})

describe('P′-4a option command rules', () => {
  it('HUNT：敌 > 道具 > 纯委托；break 只对敌目标', () => {
    const hunt = buildOptionCommandFromContext(0, ENEMY)
    expect(hunt).toMatchObject({
      option: 'HUNT',
      targetKind: 'enemy',
      pathMode: 'direct',
      param: 0,
    })
    const huntBreak = buildOptionCommandFromContext(1, ENEMY)
    expect(huntBreak).toMatchObject({ targetKind: 'enemy', pathMode: 'break' })
    const pickup = buildOptionCommandFromContext(1, { ...NO_TARGET, hasPowerUp: true })
    expect(pickup).toMatchObject({ targetKind: 'powerup', pathMode: 'direct', target: null })
    const none = buildOptionCommandFromContext(1, NO_TARGET)
    expect(none).toMatchObject({ option: 'HUNT', targetKind: 'none', pathMode: 'direct' })
  })

  it('INTERCEPT_BASE：威胁→最近敌（break 槽可拆）；否则基地锚点 direct', () => {
    const ib = buildOptionCommandFromContext(2, HOLDING)
    expect(ib).toMatchObject({ option: 'INTERCEPT', targetKind: 'enemy', pathMode: 'direct' })
    const ibBreak = buildOptionCommandFromContext(3, HOLDING)
    expect(ibBreak).toMatchObject({ targetKind: 'enemy', pathMode: 'break' })
    const guard = buildOptionCommandFromContext(3, ANCHOR_ONLY)
    expect(guard).toMatchObject({
      option: 'INTERCEPT',
      targetKind: 'anchor',
      target: ANCHOR,
      pathMode: 'direct',
    })
    const none = buildOptionCommandFromContext(2, NO_TARGET)
    expect(none).toMatchObject({ option: 'INTERCEPT', targetKind: 'none', pathMode: 'direct' })
  })

  it('HOLD：基地锚点；无锚点纯委托；HOLD_break 恒 direct', () => {
    const hold = buildOptionCommandFromContext(4, { ...NO_TARGET, anchor: ANCHOR })
    expect(hold).toMatchObject({
      option: 'HOLD_LANE',
      targetKind: 'anchor',
      target: ANCHOR,
      pathMode: 'direct',
    })
    const masked = buildOptionCommandFromContext(5, { ...NO_TARGET, anchor: ANCHOR })
    expect(masked.pathMode).toBe('direct')
    const none = buildOptionCommandFromContext(4, NO_TARGET)
    expect(none).toMatchObject({ option: 'HOLD_LANE', targetKind: 'none', pathMode: 'direct' })
  })

  it('越界槽回退 HUNT_direct（mask 已保证不发生，行为必须有定义）', () => {
    const cmd = buildOptionCommandFromContext(99, ENEMY)
    expect(cmd).toMatchObject({ option: 'HUNT', targetKind: 'enemy', pathMode: 'direct', param: 0 })
  })
})
