import { describe, it, expect } from 'bun:test'
import { setupGodGame, positionPlayer } from '../helpers'
import { World } from '../../src/game/World'
import { decodeStageGrid } from '../../src/nn/config-stage'
import { findPath } from '../../src/utils/pathfind'
import { computePlayer2SpawnCol } from '../../src/utils/helpers'
import { BASE_POS, GRID } from '../../src/constants'
import { DIR_DX, DIR_DY, dirIdx } from '../../src/utils/direction'
import type { Cell } from '../../src/utils/pathfind'
import {
  evaluateCorridor,
  planCorridorDig,
  pathGeometry,
  corridorBands,
  centralBrickCells,
  footprintClear,
  validateCorridorPath,
  corridorCarved,
  CORRIDOR_WINDOW_ROWS,
  CORRIDOR_ROW,
  HUG_ROW_MIN,
  LOWER_THIRD_ROW,
  RING_HARD_CELLS,
} from '../../src/nn/opening-corridor'

function endpoints(world: World): { from: Cell; to: Cell } {
  const s = world.playerSpawnPoint
  return {
    from: { col: s.col, row: s.row },
    to: { col: computePlayer2SpawnCol(s.col), row: s.row },
  }
}

function fill(
  world: World,
  colMin: number,
  colMax: number,
  rowMin: number,
  rowMax: number,
  type: 'brick' | 'steel',
): void {
  for (let r = rowMin; r <= rowMax; r++) {
    for (let c = colMin; c <= colMax; c++) world.tileMap.set(c, r, type)
  }
}

/** A northern detour exists, but the whole lower-third crossing is bricked. */
function brickBarrier(world: World): void {
  fill(world, 11, 14, LOWER_THIRD_ROW, 22, 'brick')
}

describe('P1↔P2 corridor geometry', () => {
  it('is derived from the board, not from stage IDs', () => {
    expect(CORRIDOR_ROW).toBe(BASE_POS.row - 3)
    expect(HUG_ROW_MIN).toBe(CORRIDOR_ROW - 1)
    expect(LOWER_THIRD_ROW).toBe(Math.floor((GRID * 2) / 3))
    const [hug, lower] = corridorBands({ col: 8, row: 24 }, { col: 16, row: 24 })
    // ideal = 8 horizontal + 3 up + 3 down = 14
    expect(hug.maxLen).toBe(20)
    expect(lower.maxLen).toBe(26)
    expect(hug.minTopLeftRow).toBe(HUG_ROW_MIN)
    expect(lower.minTopLeftRow).toBe(LOWER_THIRD_ROW)
    expect(CORRIDOR_WINDOW_ROWS).toEqual([20, 21])
  })

  it('a plain open A* path is a short, low lane on an open map', () => {
    const { world } = setupGodGame()
    const { from, to } = endpoints(world)
    const ev = evaluateCorridor(world.tileMap, from, to)
    expect(ev.connected).toBe(true)
    expect(ev.band).toBe('hug')
    expect(ev.probe?.geometry.length).toBe(14)
    expect(ev.probe?.geometry.minRow).toBe(CORRIDOR_ROW)
    expect(ev.probe?.geometry.hugRatio).toBe(1)
  })

  it('a long northern detour does NOT count as connected', () => {
    const { world } = setupGodGame()
    brickBarrier(world)
    const { from, to } = endpoints(world)

    // Plain reachability (the old criterion) says "connected" — via the north.
    const naive = findPath(world.tileMap, from, to, { hardBlockedCells: RING_HARD_CELLS })
    expect(naive).not.toBeNull()
    const naiveGeo = pathGeometry(from, naive!)
    expect(naiveGeo.minRow).toBeLessThan(LOWER_THIRD_ROW)

    const ev = evaluateCorridor(world.tileMap, from, to)
    expect(ev.connected).toBe(false)
    expect(ev.hug).toBeNull()
    expect(ev.lower).toBeNull()
  })

  it('carves the shortest legal lane: hug band, bottom third, one low central window', () => {
    const { world } = setupGodGame()
    brickBarrier(world)
    const { from, to } = endpoints(world)

    const dig = planCorridorDig(world.tileMap, from, to, {
      windowLock: null,
      windowRows: CORRIDOR_WINDOW_ROWS,
      maxBricks: 8,
    })
    expect(dig).not.toBeNull()
    expect(dig!.band).toBe('hug')
    expect(dig!.geometry.minRow).toBeGreaterThanOrEqual(HUG_ROW_MIN)
    expect(dig!.geometry.length).toBeLessThanOrEqual(20)
    expect(dig!.bricks).toBeLessThanOrEqual(8)
    expect(CORRIDOR_WINDOW_ROWS).toContain(dig!.windowRow as number)

    // Every centre-column brick outside the window stays hard-blocked.
    const outside = centralBrickCells(world.tileMap).filter(
      (c) => c.row !== dig!.windowRow && c.row !== dig!.windowRow! + 1,
    )
    expect(outside.length).toBeGreaterThan(0)
  })

  it('the low central window is the only way through centre-column bricks', () => {
    const { world } = setupGodGame()
    brickBarrier(world)
    const { from, to } = endpoints(world)
    const dig = planCorridorDig(world.tileMap, from, to, {
      windowLock: null,
      windowRows: [], // carveCorridorWindow: false
      maxBricks: 8,
    })
    expect(dig).toBeNull()
  })

  it('falls back to a bottom-third lane when the hug band is steel-sealed', () => {
    const { world } = setupGodGame()
    // Steel across the hug band only; rows 17..19 stay open.
    fill(world, 9, 16, 20, 22, 'steel')
    const { from, to } = endpoints(world)
    const ev = evaluateCorridor(world.tileMap, from, to)
    expect(ev.connected).toBe(true)
    expect(ev.band).toBe('lower')
    expect(ev.probe!.geometry.minRow).toBeGreaterThanOrEqual(LOWER_THIRD_ROW)
    expect(ev.probe!.geometry.hugRatio).toBeLessThan(1)
  })

  it('connectivity is spawn-to-spawn: a wandering tank does not change the verdict', () => {
    const { world } = setupGodGame()
    const { from, to } = endpoints(world)
    const before = evaluateCorridor(world.tileMap, from, to)
    positionPlayer(world, 2, 2)
    const after = evaluateCorridor(world.tileMap, from, to)
    expect(after.connected).toBe(before.connected)
    expect(after.probe?.path).toEqual(before.probe?.path)
  })

  it('a steel/water-sealed bottom third has no legal dig (handoff, never a north detour)', () => {
    const { world } = setupGodGame()
    fill(world, 9, 16, LOWER_THIRD_ROW, 22, 'steel')
    const { from, to } = endpoints(world)
    const ev = evaluateCorridor(world.tileMap, from, to)
    expect(ev.connected).toBe(false)
    const dig = planCorridorDig(world.tileMap, from, to, {
      windowLock: null,
      windowRows: CORRIDOR_WINDOW_ROWS,
      maxBricks: 8,
    })
    expect(dig).toBeNull()
  })
})

/**
 * 用户 2026-11-14 纠错：Checkers「现存的通道」只有 1 行高，2×2 坦克挤不进去。
 * 这两条用课程真实关卡做回归，防止再把 Bastion 的读数安到 Checkers 头上。
 */
describe('真实关卡回归：Checkers 的 1 行缝 ≠ 连通', () => {
  async function loadStage(idx: number): Promise<World> {
    const level = Bun.JSONC.parse(
      await Bun.file('nn-training/levels/classic35-safe-base.jsonc').text(),
    ) as { stages: Array<{ name: string }> }
    const world = new World()
    world.loadStageData(decodeStageGrid(level.stages[idx] as never, 2000 + idx), 0)
    return world
  }

  /**
   * 用户 2026-11-14 拍板：B 图这条走廊就是期望的 P1<->P2 通道。
   * 把它的形状钉死，防止以后又被「严格档」判回 handoff。
   */
  it('stageId 2020 Checkers：策略开出 28 步 / 35 砖的下 1/3 走廊', async () => {
    const world = await loadStage(20)
    const { from, to } = endpoints(world)
    const tm = world.tileMap

    // ① 现存的缝挤不进去：row 22 只有 1 行空，下沿压 row 23 环钢；抬到 row 21 是整排砖。
    expect(footprintClear(tm, 12, 22)).toBe(false)
    expect(footprintClear(tm, 12, 21)).toBe(false)
    expect(findPath(tm, from, to, { hardBlockedCells: RING_HARD_CELLS })).toBeNull()
    expect(evaluateCorridor(tm, from, to).connected).toBe(false)
    expect(corridorCarved(tm, from, to)).toBe(false)

    // ② 严格档无解 => 自动放宽档，产出用户确认的那条走廊。
    const dig = planCorridorDig(tm, from, to, {
      windowLock: null,
      windowRows: CORRIDOR_WINDOW_ROWS,
      maxBricks: 8,
    })
    expect(dig).not.toBeNull()
    expect(dig!.relaxed).toBe(true)
    expect(dig!.band).toBe('lower')
    expect(dig!.geometry.length).toBe(28)
    expect(dig!.geometry.minRow).toBeGreaterThanOrEqual(LOWER_THIRD_ROW)
    expect(dig!.bricks).toBe(35)
    expect(dig!.windowRow).toBe(20)

    // ③ 沿路径拆砖：只允许一个低位中央窗口；拆完坦克 2×2 真的能走。
    const dugRows = new Set<number>()
    let col = from.col
    let row = from.row
    for (const dir of dig!.path) {
      const di = dirIdx(dir)
      col += DIR_DX[di]
      row += DIR_DY[di]
      for (let dr = 0; dr <= 1; dr++) {
        for (let dc = 0; dc <= 1; dc++) {
          const c = col + dc
          const r = row + dr
          if (tm.get(c, r) !== 'brick') continue
          if (c === BASE_POS.col || c === BASE_POS.col + 1) dugRows.add(r)
          tm.set(c, r, 'empty')
        }
      }
    }
    expect([...dugRows].every((r) => r === 20 || r === 21)).toBe(true)
    expect(validateCorridorPath(tm, from, dig!.path)).toBe(true)

    // ④ 完成判据 ≠ 严格连通判据：开出来即算数，不再套 26 步长度门。
    expect(corridorCarved(tm, from, to)).toBe(true)
    expect(evaluateCorridor(tm, from, to).connected).toBe(false)
  })

  it('stageId 2019 Bastion：14 步 hug 通道逐格都能容纳 2×2 坦克', async () => {
    const world = await loadStage(19)
    const { from, to } = endpoints(world)
    const ev = evaluateCorridor(world.tileMap, from, to)
    expect(ev.connected).toBe(true)
    expect(ev.band).toBe('hug')
    expect(ev.probe?.geometry.length).toBe(14)
    expect(validateCorridorPath(world.tileMap, from, ev.probe!.path)).toBe(true)
  })
})
