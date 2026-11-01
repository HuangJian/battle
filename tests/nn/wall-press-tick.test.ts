import { describe, it, expect } from 'bun:test'
import { World } from '../../src/game/World'
import { CELL, GRID } from '../../src/constants'
import { isWallPressTick, corridorCells } from '../../src/nn/danger-metrics'
import { seedWorld, clearArena, positionPlayer } from '../helpers'

/**
 * `isWallPressTick` —— metrics v13 `wallPressTicks` 的**唯一**判据
 * （`plan/r5-next.plan.md` §5.3；两个导出器必须逐字同源）。
 *
 * 六个条件：存活/非重生保护 · 非 hold · 零 px 位移 · 请求=提交朝向 · 前脸地形挡。
 * 这里逐条断开验证 —— 任何一条放松，机制闸就会把别的东西算成撞墙。
 */

function withPlayer(seed = 1): World {
  const w = seedWorld(seed)
  if (!w.player) w.spawnPlayer()
  return w
}

function wallInFront(w: World, col: number, row: number) {
  // 在 (col,row) 上方放一格砖，其余清空
  clearArena(w)
  w.tileMap.grid[row - 1][col] = 'brick'
}

describe('isWallPressTick：撞墙的唯一判据', () => {
  it('六条全真 ⇒ true（有意图、零位移、前脸是砖、朝向=请求）', () => {
    const w = withPlayer()
    clearArena(w)
    wallInFront(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = 'up'
    expect(corridorCells(w, p, 'up', 1)).toBe(0) // 前脸确实被挡
    expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(true)
  })

  it('hold ⇒ false（原地转向不是撞墙）', () => {
    const w = withPlayer()
    clearArena(w)
    wallInFront(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = 'up'
    expect(isWallPressTick(w, p, { movedPx: false, hold: true })).toBe(false)
  })

  it('本拍动了 1 px ⇒ false（必须 px 判，格可能没变）', () => {
    const w = withPlayer()
    clearArena(w)
    wallInFront(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = 'up'
    expect(isWallPressTick(w, p, { movedPx: true, hold: false })).toBe(false)
  })

  it('请求方向 ≠ 提交朝向（被转向冷却按回）⇒ false', () => {
    const w = withPlayer()
    clearArena(w)
    wallInFront(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = 'left' // 想左转，被冷却按回，dir 仍是 up
    expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(false)
  })

  it('没按方向（lastActionDir 为空）⇒ false', () => {
    const w = withPlayer()
    clearArena(w)
    wallInFront(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = null
    expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(false)
  })

  it('重生保护期 ⇒ false（那段时间引擎不跑移动，不是撞墙）', () => {
    const w = withPlayer()
    clearArena(w)
    wallInFront(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = 'up'
    p.spawnTimer = 500
    expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(false)
  })

  it('前脸是空的 ⇒ false（想走却不动，但不是墙挡的）', () => {
    const w = withPlayer()
    clearArena(w)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    p.lastActionDir = 'up'
    expect(corridorCells(w, p, 'up', 1)).toBeGreaterThan(0)
    expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(false)
  })

  it('贴边界也算撞（corridorCells 出界即 0 格可走）', () => {
    const w = withPlayer()
    clearArena(w)
    positionPlayer(w, 0, 3, 'left')
    const p = w.player!
    p.lastActionDir = 'left'
    expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(true)
  })

  it('清场后的空地上永远不触发（正例的对照，防"恒真"）', () => {
    const w = withPlayer()
    clearArena(w)
    positionPlayer(w, 10, 10, 'up')
    const p = w.player!
    for (const d of ['up', 'down', 'left', 'right'] as const) {
      p.dir = d
      p.lastActionDir = d
      expect(isWallPressTick(w, p, { movedPx: false, hold: false })).toBe(false)
    }
    void GRID
    void CELL
  })
})
