import { describe, expect, it } from 'bun:test'
import { encodeOpeningAction } from '../../src/nn/opening-input'

describe('encodeOpeningAction', () => {
  it('v6 null 保持当前已提交朝向 + hold（原地不动）', () => {
    expect(encodeOpeningAction({ move: null, fire: false }, true, 'left')).toEqual({
      move: 2, // MOVE4_DECODE: up0 down1 left2 right3
      fire: 0,
      hold: 1,
    })
  })

  it('v6 null 在朝上时也保持 up + hold', () => {
    expect(encodeOpeningAction({ move: null, fire: false }, true, 'up')).toEqual({
      move: 0,
      fire: 0,
      hold: 1,
    })
  })

  it('v6 有方向时 hold=0，fire=2（after-turn）', () => {
    expect(encodeOpeningAction({ move: 'right', fire: true }, true, 'up')).toEqual({
      move: 3,
      fire: 2,
      hold: 0,
    })
  })

  it('legacy null 仍是 STOP（move=0, hold=0）', () => {
    expect(encodeOpeningAction({ move: null, fire: false }, false, 'left')).toEqual({
      move: 0,
      fire: 0,
      hold: 0,
    })
  })

  it('legacy 有方向时 fire=1、hold=0', () => {
    expect(encodeOpeningAction({ move: 'down', fire: true }, false, 'up')).toEqual({
      move: 2, // MOVE_DECODE: up1 down2 left3 right4
      fire: 1,
      hold: 0,
    })
  })
})
