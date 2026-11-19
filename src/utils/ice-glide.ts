/**
 * ice-glide.ts — pure ice-traversal direction guard.
 *
 * On ice, requesting the reverse of the current momentum causes a real
 * reverse/slide; the caller holds instead. Shared by God-AI navigation and the
 * NN policy input.
 */
import type { Direction } from '../constants'

export function iceGlideAdjust(
  moveDir: Direction | null,
  onIce: boolean,
  vx: number,
  vy: number,
  minSpeed: number,
): Direction | null {
  if (!moveDir || !onIce) return moveDir
  const glideSpeed = Math.abs(vx) + Math.abs(vy)
  if (glideSpeed < minSpeed) return moveDir
  // axis-lock（SimulationCombat）每 tick 只保留主导轴，|vx| === |vy| 的"对角"
  // 是瞬时态；tie-break 归 x 轴意味着此时 y 轴反向不会被判为 reverse（视为垂直
  // 转弯放行）——无害，勿"修复"。
  const axisX = Math.abs(vx) >= Math.abs(vy)
  const reverse = axisX
    ? (moveDir === 'right' && vx < 0) || (moveDir === 'left' && vx > 0)
    : (moveDir === 'down' && vy < 0) || (moveDir === 'up' && vy > 0)
  return reverse ? null : moveDir
}
