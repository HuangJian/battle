/**
 * dodge-l0.ts — 独立 L0 保底层（plan/goal-nn-action.md §3.5(e) / 卡 A3）。
 *
 * 为什么独立：保底层触发时 executed action ≠ 网络采样动作，而 shard 记的是采样
 * 动作及其 logp ⇒ 直接进 PPO 会污染 importance ratio。方案 (e) 不复用"每 tick 先
 * 跑完整 God-AI 链再判 `_lastBranch==='dodge'`"的写法（goal-executor.ts:204），
 * 改写一条独立 dodge 规则——保底层仍在、不再依赖 God-AI think 管线、省掉部署时的
 * God-AI 前向、A5 消融结论干净。
 *
 * **白名单（§3.5 F1 裁定，硬边界）**：只准用 `src/ai/perception.ts` 基元
 * （canStep）+ World 只读；**禁止** `ThreatAssessor` 及任何读 `_enemies` /
 * `_threatCache` 的函数；本模块不 import GodAIInput（tests/nn/dodge-l0.test.ts
 * 有源码扫描哨兵）。无需 `scanAheadImpl`——canStep + 弹道几何已够。
 *
 * 规则（一句话，§3.5 A3④：L0 优先于任何路径约束，保命 > 沿路走）：
 *   采样动作会把玩家留在/送进对齐逼近的敌方弹道（≤6 格）时，改判到
 *   "移动后离所有弹道最远"的方向——横移出弹道最优先，其次沿弹道后退
 *   （争取时间），绝不迎弹前进；无更优方向则不覆盖（交还策略）。
 * 只覆盖 move；fire 不动（开火纪律是策略/L3 的职责）。
 *
 * 覆盖步记账（§3.5 F3）：落盘 executed 动作 + 该动作在采集策略下的 logp
 * （logits 现成，取对应下标）⇒ PPO 的 ratio 对覆盖步良定义（导出器实现）。
 */

import { CELL } from '../constants'
import type { Direction } from '../constants'
import { canStep } from '../ai/perception'
import type { World } from '../game/World'

/** 触发半径：6 格内有来袭敌方子弹才考虑覆盖（之外交给策略学）。 */
const DODGE_RADIUS = 6 * CELL
/** 对齐带宽（与 simulation-runner 的 countIncomingThreats 同款：±0.75 格）。 */
const ALIGN_BAND = CELL * 0.75

export interface DodgeDecision {
  triggered: boolean
  /** 覆盖动作的移动方向（triggered=false 时为 null）。 */
  dir: Direction | null
  /** 触发原因（'perp' 横移出弹道 | 'retreat' 沿弹道后退 | '' 未触发）。 */
  reason: string
}

const NO_DODGE: DodgeDecision = { triggered: false, dir: null, reason: '' }

const CANDIDATE_DIRS: readonly Direction[] = ['up', 'down', 'left', 'right']

/** 一次"移一步后"对某威胁弹的安全分：弹道内 = 接近距离（越大越安全）；出弹道 = ∞。 */
function laneScore(bullets: World['bullets'], cx: number, cy: number): number {
  let worst = Infinity
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    // 只算逼近玩家的弹（远离的弹不构成威胁）——以玩家当前位置为参照。
    const approaching =
      (b.dir === 'down' && by < cy) ||
      (b.dir === 'up' && by > cy) ||
      (b.dir === 'right' && bx < cx) ||
      (b.dir === 'left' && bx > cx)
    if (!approaching) continue
    // 目的格中心是否在该弹的对齐逼近弹道内（带宽 ±0.75 格）。
    const inLane = vertical ? Math.abs(bx - cx) < ALIGN_BAND : Math.abs(by - cy) < ALIGN_BAND
    const dist = vertical ? Math.abs(by - cy) : Math.abs(bx - cx)
    if (inLane && dist <= DODGE_RADIUS) {
      // 弹道内：接近距离就是安全分（距离越大越安全；≤DODGE_RADIUS 才计入）。
      if (dist < worst) worst = dist
    }
  }
  return worst
}

/**
 * L0 dodge 判定。`sampledDir` = 采样动作的有效方向（move=0 ⇒ 保持 lastDir——
 * ScriptedInput 语义：none = 沿当前朝向继续走）。
 * 评分制：对 4 个方向算"移一步后的安全分"（出弹道 = ∞ > 弹道内距离），
 * 只当最优方向严格优于采样动作时才覆盖——保底层只兜底，不做转向教学
 * （覆盖率纪律 §3.5①：覆盖率应低且可辩护）。
 */
export function dodgeL0(world: World, sampledDir: Direction | null): DodgeDecision {
  const p = world.player
  if (!p || !p.alive) return NO_DODGE
  const pcx = p.x + p.w / 2
  const pcy = p.y + p.h / 2
  // 玩家当前是否处于对齐逼近弹道（≤6 格）——不在弹道里就绝不覆盖。
  const bullets = world.bullets
  let inLaneNow = false
  for (let i = 0; i < bullets.length; i++) {
    const b = bullets[i]
    if (!b.alive || b.allegiance !== 'enemy') continue
    const bx = b.x + b.w / 2
    const by = b.y + b.h / 2
    const vertical = b.dir === 'up' || b.dir === 'down'
    const aligned = vertical ? Math.abs(bx - pcx) < ALIGN_BAND : Math.abs(by - pcy) < ALIGN_BAND
    if (!aligned) continue
    const approaching =
      (b.dir === 'down' && by < pcy) ||
      (b.dir === 'up' && by > pcy) ||
      (b.dir === 'right' && bx < pcx) ||
      (b.dir === 'left' && bx > pcx)
    if (!approaching) continue
    const dist = vertical ? Math.abs(by - pcy) : Math.abs(bx - pcx)
    if (dist <= DODGE_RADIUS) {
      inLaneNow = true
      break
    }
  }
  if (!inLaneNow) return NO_DODGE

  // 采样动作安全分；∞ = 采样动作已出弹道（安全）→ 绝不覆盖。
  let sampledScore = -1
  if (sampledDir) {
    const sp = stepCenter(p, sampledDir)
    sampledScore = laneScore(bullets, sp.x, sp.y)
  }
  if (sampledScore === Infinity) return NO_DODGE

  // 4 方向评分（canStep 通过才参评），严格更优才覆盖（同分保留采样动作）。
  let bestDir: Direction | null = null
  let bestScore = sampledScore
  for (let i = 0; i < CANDIDATE_DIRS.length; i++) {
    const dir = CANDIDATE_DIRS[i]
    if (!canStep(world, p, dir, true)) continue
    const dst = stepCenter(p, dir)
    const score = laneScore(bullets, dst.x, dst.y)
    // 横移出弹道（∞）> 沿弹道后退（距离变大，天然排除迎弹向）。
    if (score > bestScore) {
      bestScore = score
      bestDir = dir
    }
  }
  if (!bestDir) return NO_DODGE
  const reason = bestScore === Infinity ? 'perp' : 'retreat'
  return { triggered: true, dir: bestDir, reason }
}

/** 沿 dir 移动约一格后的坦克中心（格步进口径，与 canStep 一致）。 */
function stepCenter(
  p: { x: number; y: number; w: number; h: number },
  dir: Direction,
): { x: number; y: number } {
  const cx = p.x + p.w / 2
  const cy = p.y + p.h / 2
  switch (dir) {
    case 'up':
      return { x: cx, y: cy - CELL }
    case 'down':
      return { x: cx, y: cy + CELL }
    case 'left':
      return { x: cx - CELL, y: cy }
    case 'right':
      return { x: cx + CELL, y: cy }
  }
}
