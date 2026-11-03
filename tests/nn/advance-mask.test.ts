import { describe, expect, it } from 'bun:test'
import { World } from '../../src/game/World'
import { computeMasks, restrictAdvanceMask } from '../../src/nn/obs-encoder'
import { clearArena, positionPlayer, seedWorld } from '../helpers'

/**
 * v6 的 `move` 兼管朝向（hold=1 + move=d = turn-only；STOP = hold=1 + 当前朝向）。
 * 因此地形只能掩 `hold[0]`（advance），不能掩 move。这里锁住这条语义：
 *   - 前脸被挡 ⇒ hold=[0,1]（不能前进，但仍可转向/停住）；
 *   - move 仍 [1,1,1,1]（朝向永远可选）；
 *   - 空方向 / 无玩家 / 死亡 ⇒ hold=[1,1]（不制造全掩码）。
 */

function withPlayer(seed = 1): World {
  const w = seedWorld(seed)
  if (!w.player) w.spawnPlayer()
  return w
}

function wallAbove(w: World, col: number, row: number): void {
  clearArena(w)
  w.tileMap.grid[row - 1][col] = 'brick'
}

describe('restrictAdvanceMask (v6)', () => {
  it('open four neighbours ⇒ hold [1,1], move [1,1,1,1]', () => {
    const w = withPlayer()
    clearArena(w)
    positionPlayer(w, 10, 10, 'up')
    const m = computeMasks(w, true)
    restrictAdvanceMask(w, 0, m)
    expect(m.move).toEqual([1, 1, 1, 1])
    expect(m.hold).toEqual([1, 1])
  })

  it('brick ahead ⇒ hold [0,1], but move/facing stays all-ones', () => {
    const w = withPlayer()
    wallAbove(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const m = computeMasks(w, true)
    restrictAdvanceMask(w, 0, m)
    expect(m.move).toEqual([1, 1, 1, 1])
    expect(m.hold).toEqual([0, 1])
  })

  it('mask follows the selected move, not the committed facing', () => {
    const w = withPlayer()
    wallAbove(w, 10, 10)
    positionPlayer(w, 10, 10, 'up')
    const m = computeMasks(w, true)
    restrictAdvanceMask(w, 2, m) // left is open even though the wall is above
    expect(m.hold).toEqual([1, 1])
  })

  it('no player / dead player ⇒ hold [1,1]', () => {
    const noPlayer = seedWorld(2)
    const m1 = computeMasks(noPlayer, true)
    restrictAdvanceMask(noPlayer, 0, m1)
    expect(m1.hold).toEqual([1, 1])

    const dead = withPlayer(3)
    clearArena(dead)
    positionPlayer(dead, 10, 10, 'up')
    dead.player!.alive = false
    const m2 = computeMasks(dead, true)
    restrictAdvanceMask(dead, 0, m2)
    expect(m2.hold).toEqual([1, 1])
  })

  it('legacy base masks stay move5 / fire2 / hold [1,1]', () => {
    const w = withPlayer()
    clearArena(w)
    positionPlayer(w, 10, 10, 'up')
    const m = computeMasks(w)
    expect(m.move).toEqual([1, 1, 1, 1, 1])
    expect(m.fire).toHaveLength(2)
    expect(m.hold).toEqual([1, 1])
  })
})
