/**
 * 2026-10-31 敌方运动感知（pursuitLeadMode = 2 的预测源）。
 *
 * 旧版预判用的是 `t.dir`（瞬时朝向）：敌人一转向，提前点就落在空地上。这里改成
 * **实测位移**：每 tick 记录每个敌人的格位变化，只有「同方向连续稳定 N 拍」才输出
 * 速度；静止/刚转向时不预判（提前量 0），避免把玩家导向空地。
 *
 * 纯观察：只读 World，写 GodAIInput 的 AI 内部缓存（不序列化、不回灌决策）。
 */
import type { GodAIInput } from '../GodAIInput'
import type { Cell } from './pathfind'

export interface EnemyMotionSlot {
  valid: boolean
  id: number
  col: number
  row: number
  /** Last cell delta (cells/tick, each ∈ {-1,0,1}). */
  dc: number
  dr: number
  /** Consecutive ticks with the same delta. */
  stable: number
  /** Consecutive ticks at the same cell. */
  still: number
  /** Transient per-tick marker (not state). */
  seen: boolean
}

export function emptyEnemyMotionSlot(): EnemyMotionSlot {
  return { valid: false, id: -1, col: -1, row: -1, dc: 0, dr: 0, stable: 0, still: 0, seen: false }
}

const SLOTS = 8

export function makeEnemyMotionSlots(): EnemyMotionSlot[] {
  const out: EnemyMotionSlot[] = []
  for (let i = 0; i < SLOTS; i++) out.push(emptyEnemyMotionSlot())
  return out
}

/**
 * 每 tick 更新一次（think 里 `_enemies` 快照建好后）。O(enemies × slots) ≤ 32。
 */
export function updateEnemyMotion(self: GodAIInput): void {
  const slots = self._enemyMotion
  for (let i = 0; i < slots.length; i++) slots[i].seen = false
  const list = self._enemies
  for (let i = 0; i < list.length; i++) {
    const t = list[i]
    if (!t.alive || t.spawnTimer > 0 || t.allegiance !== 'enemy') continue
    const c = self.tankCell(t)
    const col = c.col
    const row = c.row
    let slot: EnemyMotionSlot | null = null
    for (let s = 0; s < slots.length; s++) {
      if (slots[s].id === t.id) {
        slot = slots[s]
        break
      }
    }
    if (slot) slot.seen = true
    if (slot) {
      const dc = col - slot.col
      const dr = row - slot.row
      if (dc === slot.dc && dr === slot.dr && (dc !== 0 || dr !== 0)) slot.stable++
      else slot.stable = 0
      if (dc === 0 && dr === 0) slot.still++
      else slot.still = 0
      slot.col = col
      slot.row = row
      slot.dc = dc
      slot.dr = dr
      continue
    }
    // New enemy: take a free slot.
    for (let s = 0; s < slots.length; s++) {
      if (slots[s].valid) continue
      const s2 = slots[s]
      s2.valid = true
      s2.seen = true
      s2.id = t.id
      s2.col = col
      s2.row = row
      s2.dc = 0
      s2.dr = 0
      s2.stable = 0
      s2.still = 1
      break
    }
  }
  for (let i = 0; i < slots.length; i++) {
    if (!slots[i].seen) slots[i].valid = false
  }
}

/**
 * 该敌人的稳定运动方向；不够稳定（刚转向/静止）返回 null。
 * 调用方保证已跑过 updateEnemyMotion 本 tick。
 */
export function enemyStableVelocity(
  self: GodAIInput,
  id: number,
  minStableTicks: number,
): Cell | null {
  const slots = self._enemyMotion
  for (let s = 0; s < slots.length; s++) {
    const slot = slots[s]
    if (!slot.valid || slot.id !== id) continue
    if (slot.stable < minStableTicks) return null
    if (slot.dc === 0 && slot.dr === 0) return null
    return { col: slot.dc, row: slot.dr }
  }
  return null
}
