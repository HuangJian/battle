import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { BASE_POS } from '../src/constants'
import {
  canShootBaseLine,
  canBreachRingLine,
  ringBreachTargetOf,
  bulletHitsBaseOrRing,
  threatSplit,
  tankCenterCell,
  RING_CELLS,
  type ThreatSplit,
} from '../src/ai/god/ThreatBudget'

/**
 * `threatSplit`（metrics v13 / plan/r5-next §11.3）的护栏。
 *
 * 锁四件事：
 *   1. **互斥**：直击射界优先于破墙射界，同一辆车只进一格（否则计数会重复计价）；
 *   2. **朝向**：`*Aimed` 只在炮口真的指向目标轴时为真（这是"位置"与"对准"的分界）；
 *   3. **只算敌人**：玩家/盟军车不进计数；
 *   4. **不发明第二套几何**：`ringBreachTargetOf` 判空 ≡ `canBreachRingLine`。
 */

function buildWorld(stageIdx: number): World {
  const w = new World()
  w.rng.reseed(20260816)
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[stageIdx], stageIdx)
  w.playerLevel = 1
  return w
}

/** 清空鹰上方通道（**只清到环墙行之前**，绝不碰鹰所在的 24–25 行）。 */
function clearAboveBase(w: World) {
  for (let r = 16; r < BASE_POS.row; r++) {
    for (let c = BASE_POS.col - 1; c <= BASE_POS.col + 2; c++) w.tileMap.destroy(c, r)
  }
}

/** 把敌车中心摆在 cell (col,row)：`createTank` 收的是左上角，中心 = x+16。 */
function addEnemyAt(w: World, col: number, row: number, dir: 'up' | 'down' | 'left' | 'right') {
  const e = w.createTank('basic' as never, col * 16 - 16, row * 16 - 16, dir)
  e.spawnTimer = 0
  e.alive = true
  // `createTank` 只构造不登记（登记走 spawnEnemyTank）；本测试直接操作 tanks 数组。
  w.tanks.push(e)
  return e
}

function zero(): ThreatSplit {
  return { breach: 0, breachAimed: 0, direct: 0, directAimed: 0, bulletsAtBase: 0 }
}

describe('threatSplit：四轴互斥计数（metrics v13）', () => {
  it('直击射界 + 炮口朝下 ⇒ directAimed=1，且不重复进 breach', () => {
    const w = buildWorld(0)
    clearAboveBase(w)
    const e = addEnemyAt(w, BASE_POS.col, BASE_POS.row - 4, 'down')
    expect(tankCenterCell(e)).toEqual({ col: BASE_POS.col, row: BASE_POS.row - 4 })
    expect(canShootBaseLine(w, BASE_POS.col, BASE_POS.row - 4)).toBe(true)
    const s = threatSplit(w, zero())
    expect(s.directAimed).toBe(1)
    expect(s.direct).toBe(0)
    expect(s.breach + s.breachAimed).toBe(0)
  })

  it('同一位置、炮口朝上 ⇒ 只算 direct（未对准），不是 aimed', () => {
    const w = buildWorld(0)
    clearAboveBase(w)
    addEnemyAt(w, BASE_POS.col, BASE_POS.row - 4, 'up')
    const s = threatSplit(w, zero())
    expect(s.direct).toBe(1)
    expect(s.directAimed).toBe(0)
  })

  it('只有破墙射界（不在鹰的两轴上）⇒ breach 档，对准与否分成两格', () => {
    const w = buildWorld(0)
    const ring = RING_CELLS.find((c) => c.col > BASE_POS.col + 1 && c.row === BASE_POS.row - 1)!
    const row = ring.row
    const col = ring.col + 1
    // 直线走廊：从 (col,row) 往左全清，末端恢复成 brick（productive breach 的唯一目标）
    for (let c = col; c > BASE_POS.col; c--) w.tileMap.destroy(c, row)
    w.tileMap.set(ring.col, ring.row, 'brick')
    expect(canShootBaseLine(w, col, row)).toBe(false)
    expect(canBreachRingLine(w, col, row)).toBe(true)
    const tgt = ringBreachTargetOf(w, col, row)!
    expect(tgt.col).toBe(ring.col)

    const e = addEnemyAt(w, col, row, tgt.col > col ? 'right' : 'left')
    let s = threatSplit(w, zero())
    expect(s.breachAimed).toBe(1)
    expect(s.breach).toBe(0)

    e.dir = tgt.col > col ? 'left' : 'right'
    s = threatSplit(w, zero())
    expect(s.breach).toBe(1)
    expect(s.breachAimed).toBe(0)
  })

  it('玩家不进计数（只算 allegiance=enemy）', () => {
    const w = buildWorld(0)
    clearAboveBase(w)
    const p = w.player!
    p.x = BASE_POS.col * 16 - 16
    p.y = (BASE_POS.row - 4) * 16 - 16
    p.dir = 'down'
    p.alive = true
    p.spawnTimer = 0
    expect(tankCenterCell(p)).toEqual({ col: BASE_POS.col, row: BASE_POS.row - 4 })
    const s = threatSplit(w, zero())
    expect(s.direct + s.directAimed).toBe(0)
  })

  it('ringBreachTargetOf 判空 ≡ canBreachRingLine（不发明第二套几何）', () => {
    const w = buildWorld(0)
    let checked = 0
    for (let r = 0; r < 26; r += 2) {
      for (let c = 0; c < 26; c += 2) {
        checked++
        expect(ringBreachTargetOf(w, c, r) !== null).toBe(canBreachRingLine(w, c, r))
      }
    }
    expect(checked).toBeGreaterThan(100)
  })

  it('out 参数被复用且每次调用清零（增量语义，不是累计）', () => {
    const w = buildWorld(0)
    clearAboveBase(w)
    const out = zero()
    addEnemyAt(w, BASE_POS.col, BASE_POS.row - 4, 'down')
    expect(threatSplit(w, out)).toBe(out)
    expect(out.directAimed).toBe(1)
    for (const t of w.tanks) if (t.allegiance === 'enemy') t.alive = false
    threatSplit(w, out)
    expect(out.directAimed).toBe(0)
    expect(out.breach + out.breachAimed + out.direct + out.bulletsAtBase).toBe(0)
  })
})

describe('bulletHitsBaseOrRing：弹道投影', () => {
  it('正对鹰的弹（base 列、朝下）⇒ true；同一发朝上 ⇒ false', () => {
    const w = buildWorld(0)
    clearAboveBase(w)
    // 弹 8×8：朝下时前缘 = y+8 ⇒ 取 y = r*16+4 让前缘落在 r 行中心
    const shot = {
      x: BASE_POS.col * 16 + 4,
      y: (BASE_POS.row - 5) * 16 + 4,
      w: 8,
      h: 8,
      dir: 'down' as const,
    }
    expect(bulletHitsBaseOrRing(w, shot)).toBe(true)
    expect(bulletHitsBaseOrRing(w, { ...shot, dir: 'up' as const })).toBe(false)
  })

  it('射向环墙 brick 的弹 ⇒ true；打空（反向）⇒ false', () => {
    const w = buildWorld(0)
    const ring = RING_CELLS.find((c) => c.col > BASE_POS.col + 1 && c.row === BASE_POS.row - 1)!
    const row = ring.row
    for (let c = ring.col + 3; c > BASE_POS.col; c--) w.tileMap.destroy(c, row)
    w.tileMap.set(ring.col, ring.row, 'brick')
    const shot = { x: (ring.col + 2) * 16 + 4, y: row * 16 + 4, w: 8, h: 8, dir: 'left' as const }
    expect(bulletHitsBaseOrRing(w, shot)).toBe(true)
    expect(bulletHitsBaseOrRing(w, { ...shot, dir: 'right' as const })).toBe(false)
  })
})
