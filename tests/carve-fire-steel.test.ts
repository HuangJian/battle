import { setupGodGame } from './helpers'
import { describe, it, expect } from 'bun:test'
import { carveFire } from '../src/ai/god/candidates/shared'
import type { DecisionContext } from '../src/ai/god/DecisionCore'
import { CELL } from '../src/constants'

/**
 * 机制修复（2026-10-20）：carveFire 的 `bs.steel` 是**整条射线**上有没有钢。
 * 钢在 9 格外时，眼前 (x,y-1) 的砖也会被拒绝击破 ⇒ 坦克对着砖墙原地卡死。
 * 实测修复前本树 **1.59% 的全部 tick**（≈87 拍/局）命中该否决、其中 13201 拍本可开火；
 * 修复后 hard 60-seed：SUITE 0.5748 → 0.5793、胜率 79% → 80%、
 * Battlement 0.339 → 0.479（最弱关易主为 Riverbed）。
 */
function ctxFor(input: ReturnType<typeof setupGodGame>['input']) {
  const p = input.world.player!
  return {
    p,
    pcx: p.x + p.w / 2,
    pcy: p.y + p.h / 2,
    onCooldown: false,
  } as unknown as DecisionContext
}

/** Player at (8,8) facing up, not on fire cooldown, blocked ahead. */
function setup() {
  const g = setupGodGame({ seed: 7 })
  const p = g.world.player!
  p.x = 8 * CELL
  p.y = 8 * CELL
  p.dir = 'up'
  p.moving = true
  p.lastFire = -1e9
  return { ...g, p }
}

describe('carveFire 钢墙守卫 — 只有钢真的挡在前面时才拒绝破砖', () => {
  it('砖在钢之前 ⇒ 破砖放行（这是被修的 bug）', () => {
    const { world, input } = setup()
    world.tileMap.grid[7][8] = 'brick' // 眼前的砖
    world.tileMap.grid[2][8] = 'steel' // 9 格外的钢（不该阻止打砖）
    input._fire = false
    carveFire(input, ctxFor(input), 'up')
    expect(input._fire).toBe(true)
  })

  it('钢就在眼前 ⇒ 不放行（保留原有的“别朝钢开火”）', () => {
    const { world, input } = setup()
    world.tileMap.grid[7][8] = 'steel'
    input._fire = false
    carveFire(input, ctxFor(input), 'up')
    expect(input._fire).toBe(false)
  })

  it('车道畅通时走正常射击判定（不误触发破砖）', () => {
    const { world, input } = setup()
    world.tileMap.grid[7][8] = 'empty'
    input._fire = false
    carveFire(input, ctxFor(input), 'up')
    expect(input._fire).toBe(false) // 无敌人、无墙 ⇒ 不空放
  })
})
