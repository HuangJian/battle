import type { Direction } from '../constants'
import { encodeMove } from './action-space'
import { encodeMove4 } from './action-space-v6'

/**
 * opening-input.ts — OpeningController 动作 → ScriptedInput 的唯一映射
 * (plan/opening-null-move.plan.md)。
 *
 * 背景：v6 的 move 头没有 STOP 类，`move=null` 由 `hold=1` 表达。旧适配器把它
 * 写成 `hold=1 + move=0(up)`，而 `SimulationPlayer` 的 v6 语义是「hold = 转向 move
 * 但不前进」⇒ null 请求会在转向冷却允许时把坦克转向 up，而不是原地不动。
 *
 * 现行语义：v6 `move=null` 映射到**当前已提交朝向**（`prevMoveDir`）+ `hold=1`，
 * 于是 `dir === prevMoveDir`，不触发转向、不占冷却、不前进。
 */
export interface OpeningActionLike {
  move: Direction | null
  fire: boolean
}

export interface ScriptedAction {
  move: number
  /** v6: 0 none / 2 after-turn; legacy: 0/1. */
  fire: number
  /** v6: 1 = turn-only (no advance); legacy 恒 0. */
  hold: number
}

/**
 * @param currentDir 当前**已提交**朝向（world.player.prevMoveDir ?? dir ?? 'up'）
 */
export function encodeOpeningAction(
  act: OpeningActionLike,
  v6: boolean,
  currentDir: Direction,
): ScriptedAction {
  if (v6) {
    return {
      move: encodeMove4(act.move ?? currentDir),
      fire: act.fire ? 2 : 0,
      hold: act.move === null ? 1 : 0,
    }
  }
  return {
    move: act.move ? encodeMove(act.move) : 0,
    fire: act.fire ? 1 : 0,
    hold: 0,
  }
}
