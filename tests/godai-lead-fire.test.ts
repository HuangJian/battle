import { describe, it, expect } from 'bun:test'
import { leadHitForDir } from '../src/ai/god/lead-fire'
import { CELL } from '../src/constants'
import { clearArena, placeEnemy, positionPlayer } from './helpers'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'

/**
 * A10 — 预判开火（lead fire）的到达时命中判据。
 *
 * 开火门 `shouldFireInDir` → `scanAhead` 只扫**当前几何**：敌人必须此刻在射线
 * 上。`leadHitForDir` 把「到达时的横向位置」算进来：
 * `|lateral + vLat·t*| < TANK/2 + BULLET/2 = 19px`，轴向 ≤13 格，且中途无阻弹地形。
 * 这个函数同时是 NN 观测 `leadHitD[4]` 的唯一实现（`nn/policy-extra` 直接 import），
 * 两个大脑必须对「这一发会不会中」给出一致答案。
 */

function setup() {
  const w = new World()
  w.rng.reseed(11)
  w.loadStageData(STAGES[0], 0) // spawns the player
  clearArena(w) // empty field + the base 2×2 restored
  positionPlayer(w, 8, 8, 'up')
  return w
}

const PCX = 8 * CELL + CELL // player centre x
const PCY = 8 * CELL + CELL // player centre y

describe('A10 — leadHitForDir（到达时命中）', () => {
  it('a static enemy 2 cells off the line is a MISS (lateral 32px ≥ 19px)', () => {
    const w = setup()
    placeEnemy(w, 10, 4) // 4 cells up, 2 cells right ⇒ lateral 32px
    expect(leadHitForDir(w, 'up', PCX, PCY)).toBe(0)
  })

  it('the same enemy drifting left is a HIT (32 − 16·1 = 16px < 19px at arrival)', () => {
    const w = setup()
    const e = placeEnemy(w, 10, 4)
    e.vx = -1 // 1 px/tick left; axial 64px / bulletSpeed 4 = 16 ticks
    expect(leadHitForDir(w, 'up', PCX, PCY)).toBe(1)
  })

  it('blocking terrain cancels the lead shot', () => {
    const w = setup()
    const e = placeEnemy(w, 10, 4)
    e.vx = -1
    // Steel on the player's own column, between the muzzle and the enemy rows.
    w.tileMap.set(9, 6, 'steel') // the player CENTRE sits in col 9 (x=128..159)
    expect(leadHitForDir(w, 'up', PCX, PCY)).toBe(0)
  })

  it('enemies BEHIND the muzzle are ignored (axial ≤ 0)', () => {
    const w = setup()
    const e = placeEnemy(w, 10, 8) // same row as the player, above is empty
    e.vx = -1
    expect(leadHitForDir(w, 'up', PCX, PCY)).toBe(0)
  })
})
