import { describe, it, expect } from 'bun:test'
import { setupGodGame, placeEnemy, positionPlayer } from '../helpers'
import {
  OpeningController,
  type OpeningAction,
  type OpeningStep,
  type OpeningTraceEvent,
} from '../../src/nn/opening-controller'
import { RING_CELLS } from '../../src/utils/base-ring'
import { CELL, GRID } from '../../src/constants'
import { World } from '../../src/game/World'
import { decodeStageGrid } from '../../src/nn/config-stage'
import { computePlayer2SpawnCol } from '../../src/utils/helpers'
import { planCorridorDig, CORRIDOR_WINDOW_ROWS } from '../../src/nn/opening-corridor'
import type { InputLike } from '../../src/game/Input'
import { DIR_DX, DIR_DY, dirIdx } from '../../src/utils/direction'

function setRingSteel(world: World): void {
  for (const c of RING_CELLS) world.tileMap.set(c.col, c.row, 'steel')
}

function setCentralBricks(world: World): void {
  // Stop above the eagle/ring: row 23 is hard-blocked ring, rows 24-25 are base.
  for (let r = 0; r < 23; r++) {
    world.tileMap.set(12, r, 'brick')
    world.tileMap.set(13, r, 'brick')
  }
}

function playerCell(world: World): { col: number; row: number } {
  const p = world.player!
  return { col: Math.floor(p.x / CELL), row: Math.floor(p.y / CELL) }
}

function footprintHasBrick(world: World, col: number, row: number): boolean {
  for (let dr = 0; dr <= 1; dr++) {
    for (let dc = 0; dc <= 1; dc++) {
      if (world.tileMap.get(col + dc, row + dr) === 'brick') return true
    }
  }
  return false
}

/** Apply one controller action without Simulation: destroy one brick ahead,
 *  then move one cell if the destination footprint is clear. */
function applyAction(world: World, action: OpeningAction): void {
  const p = world.player!
  if (!action.move) return
  const di = dirIdx(action.move)
  const cell = playerCell(world)
  const next = { col: cell.col + DIR_DX[di], row: cell.row + DIR_DY[di] }
  p.dir = action.move

  if (action.fire) {
    let destroyed = false
    for (let dr = 0; dr <= 1 && !destroyed; dr++) {
      for (let dc = 0; dc <= 1 && !destroyed; dc++) {
        if (world.tileMap.get(next.col + dc, next.row + dr) === 'brick') {
          world.tileMap.set(next.col + dc, next.row + dr, 'empty')
          destroyed = true
        }
      }
    }
    if (!destroyed) return
    if (footprintHasBrick(world, next.col, next.row)) return
  }

  p.x = next.col * CELL
  p.y = next.row * CELL
  p.spawnTimer = 0
  world.frame++
}

function drive(world: World, ctrl: OpeningController, maxSteps = 3000): OpeningStep {
  let last = ctrl.step(world)
  applyAction(world, last.action)
  let steps = 1
  while (!last.done && steps < maxSteps) {
    last = ctrl.step(world)
    if (!last.done) applyAction(world, last.action)
    steps++
  }
  return last
}

describe('OpeningController: P1↔P2', () => {
  it('digs through a brick wall and hands off once P2 is reachable', () => {
    const { world } = setupGodGame()
    setRingSteel(world)
    // Vertical brick wall at col 15 blocks P2 without touching central columns.
    for (let r = 0; r < GRID; r++) world.tileMap.set(15, r, 'brick')

    const ctrl = new OpeningController()
    const last = drive(world, ctrl)
    expect(last.done).toBe(true)
    expect(last.abortReason).toBeNull()
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
  })

  it('carves ONE low 2×2 central window by default (corridor band only)', () => {
    const { world } = setupGodGame()
    positionPlayer(world, 8, 20)
    setCentralBricks(world)

    const ctrl = new OpeningController()
    const first = ctrl.step(world)
    expect(first.phase).toBe('p1p2')
    expect(first.bricksPlanned).toBeGreaterThan(0)
    expect(first.bricksPlanned).toBeLessThanOrEqual(4)
    expect([20, 21]).toContain(ctrl.snapshot().centralWindowRow as number)
    expect(ctrl.snapshot().corridorBand).toBe('hug')
    applyAction(world, first.action)

    const last = drive(world, ctrl)
    expect(last.done).toBe(true)
    expect(last.abortReason).toBeNull()
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
  })

  it('hands off with no_corridor when centre-column bricks may not be touched', () => {
    const { world } = setupGodGame()
    positionPlayer(world, 8, 20)
    setCentralBricks(world)

    const ctrl = new OpeningController({ carveCorridorWindow: false })
    const step = ctrl.step(world)
    expect(step.done).toBe(true)
    expect(step.abortReason).toBe('no_corridor')
    expect(ctrl.snapshot().p1p2Connected).toBe(false)
  })

  it('acts on ANY map with a long northern detour, not just low-kill stages', () => {
    const { world } = setupGodGame()
    // Bricks seal the whole lower-third crossing; a northern detour exists.
    for (let r = 17; r <= 22; r++) {
      for (let c = 11; c <= 14; c++) world.tileMap.set(c, r, 'brick')
    }
    const ctrl = new OpeningController()
    const first = ctrl.step(world)
    expect(first.phase).toBe('p1p2')
    expect(first.bricksPlanned).toBeGreaterThan(0)
    const snap = ctrl.snapshot()
    expect(snap.corridorBand).toBe('hug')
    expect(snap.corridorLength).toBeLessThanOrEqual(20)
    expect(snap.corridorHugRatio).toBe(1)
    applyAction(world, first.action)

    const last = drive(world, ctrl)
    expect(last.done).toBe(true)
    expect(last.abortReason).toBeNull()
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
  })

  it('hands off when the bottom third is steel-sealed (no map-specific exception)', () => {
    const { world } = setupGodGame()
    for (let r = 17; r <= 22; r++) {
      for (let c = 9; c <= 16; c++) world.tileMap.set(c, r, 'steel')
    }
    const ctrl = new OpeningController()
    const step = ctrl.step(world)
    expect(step.done).toBe(true)
    expect(step.abortReason).toBe('no_corridor')
  })
})

describe('OpeningController: enemy corridor and handoff', () => {
  it('hands off immediately when any enemy already has an open path', () => {
    const { world } = setupGodGame()
    placeEnemy(world, 5, 5, 'basic')

    const ctrl = new OpeningController()
    const step = ctrl.step(world)
    expect(step.done).toBe(true)
    expect(step.phase).toBe('done')
    expect(step.abortReason).toBeNull()
    expect(ctrl.snapshot().enemyConnected).toBe(true)

    const after = ctrl.step(world)
    expect(after.action).toEqual({ move: null, fire: false })
  })

  it('picks the southernmost enemy that has a legal dig path', () => {
    const { world } = setupGodGame()
    // Brick wall separates the player from both enemies; no open path exists.
    for (let c = 0; c < GRID; c++) world.tileMap.set(c, 15, 'brick')
    const north = placeEnemy(world, 5, 5, 'basic')
    const south = placeEnemy(world, 10, 10, 'basic')

    const ctrl = new OpeningController()
    const step = ctrl.step(world)
    expect(step.phase).toBe('enemy')
    expect(step.targetId).toBe(south.id)
    expect(step.targetId).not.toBe(north.id)
  })

  it('reset returns the controller to a fresh p1p2 state', () => {
    const { world } = setupGodGame()
    placeEnemy(world, 5, 5, 'basic')
    const ctrl = new OpeningController()
    expect(ctrl.step(world).done).toBe(true)

    ctrl.reset()
    const snap = ctrl.snapshot()
    expect(snap.phase).toBe('p1p2')
    expect(snap.done).toBe(false)
    expect(snap.abortReason).toBeNull()
    expect(snap.elapsedTicks).toBe(0)
    expect(snap.p1p2Connected).toBe(false)
    expect(snap.corridorBand).toBeNull()
    expect(snap.corridorBricks).toBe(0)
  })

  it('connectivity is spawn-to-spawn, not "can the tank reach P2 from here"', () => {
    const { world } = setupGodGame()
    // Tank far north; the spawn-to-spawn lane is still open and short.
    positionPlayer(world, 2, 2)
    const ctrl = new OpeningController()
    const step = ctrl.step(world)
    expect(step.done).toBe(true)
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
    expect(ctrl.snapshot().corridorBand).toBe('hug')
    expect(ctrl.snapshot().corridorLength).toBe(14)
  })
})

describe('OpeningController: aborts', () => {
  it('aborts on timeout before planning', () => {
    const { world } = setupGodGame()
    const ctrl = new OpeningController({ maxTicks: 1 })
    const step = ctrl.step(world)
    expect(step.done).toBe(true)
    expect(step.abortReason).toBe('timeout')
  })

  it('aborts when a base-ring cell that started solid disappears', () => {
    const { world } = setupGodGame()
    setRingSteel(world)
    for (let c = 0; c < GRID; c++) world.tileMap.set(c, 15, 'brick')
    placeEnemy(world, 5, 5, 'basic')
    const ctrl = new OpeningController()
    const first = ctrl.step(world)
    expect(first.done).toBe(false)
    const ring = RING_CELLS[0]
    world.tileMap.set(ring.col, ring.row, 'empty')
    const step = ctrl.step(world)
    expect(step.abortReason).toBe('ring_breach')
  })

  it('外部打掉中央砖只 trace，不冤杀 controller（2026-11-15 取证修复）', () => {
    const { world } = setupGodGame()
    // 远处中央砖：controller 的计划路径根本不会经过它，只有敌人能打掉。
    world.tileMap.set(12, 2, 'brick')
    for (let c = 0; c < GRID; c++) world.tileMap.set(c, 15, 'brick')
    placeEnemy(world, 5, 5, 'basic')

    const traces: OpeningTraceEvent[] = []
    const ctrl = new OpeningController({ onTrace: (e) => traces.push(e) })
    const first = ctrl.step(world)
    expect(first.phase).toBe('enemy')
    world.tileMap.set(12, 2, 'empty')
    const step = ctrl.step(world)
    expect(step.abortReason).not.toBe('central_breach')
    expect(traces.some((t) => t.col === 12 && t.row === 2 && t.planned === false)).toBe(true)
  })

  it('aborts when the player is low HP', () => {
    const { world } = setupGodGame()
    world.player!.hp = 1
    const ctrl = new OpeningController({ abortHp: 0.9 })
    const step = ctrl.step(world)
    expect(step.abortReason).toBe('player_low_hp')
  })

  it('aborts on no progress while standing still', () => {
    const { world } = setupGodGame()
    setRingSteel(world)
    for (let r = 0; r < GRID; r++) world.tileMap.set(15, r, 'brick')
    const ctrl = new OpeningController({ noProgressTicks: 5 })
    let step = ctrl.step(world)
    for (let i = 0; i < 5 && !step.done; i++) step = ctrl.step(world)
    expect(step.abortReason).toBe('no_progress')
  })
})

describe('OpeningController: real Simulation wiring', () => {
  it('drives the player through a brick wall using InputLike actions', () => {
    const { world, sim } = setupGodGame()
    world.spawnQueue.length = 0
    setRingSteel(world)
    for (let r = 0; r < GRID; r++) world.tileMap.set(15, r, 'brick')

    let action: OpeningAction = { move: null, fire: false }
    const input: InputLike = {
      getMoveDirection: () => action.move,
      isFiring: () => action.fire,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    sim.input = input

    const ctrl = new OpeningController()
    for (let i = 0; i < 4000 && !ctrl.snapshot().done; i++) {
      action = ctrl.step(world).action
      sim.tick()
    }
    expect(ctrl.snapshot().done).toBe(true)
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
  })
})

/**
 * S20 Checkers（stageId 2020 / index 20）的用户确认通道：
 * 用户 2026-11-14 拍板 B 图走廊符合要求，并要求写进单测。
 */
describe('S20 Checkers：开好即 handoff + 双人实时协同', () => {
  async function checkers(): Promise<World> {
    const level = Bun.JSONC.parse(
      await Bun.file('nn-training/levels/classic35-safe-base.jsonc').text(),
    ) as { stages: unknown[] }
    const world = new World()
    world.loadStageData(decodeStageGrid(level.stages[20] as never, 2020), 0)
    return world
  }

  function clearPlannedBricks(world: World): void {
    const spawn = world.playerSpawnPoint
    const from = { col: spawn.col, row: spawn.row }
    const to = { col: computePlayer2SpawnCol(spawn.col), row: spawn.row }
    const dig = planCorridorDig(world.tileMap, from, to, {
      windowLock: null,
      windowRows: CORRIDOR_WINDOW_ROWS,
      maxBricks: 8,
    })!
    let col = from.col
    let row = from.row
    for (const dir of dig.path) {
      const di = dirIdx(dir)
      col += DIR_DX[di]
      row += DIR_DY[di]
      for (let dr = 0; dr <= 1; dr++) {
        for (let dc = 0; dc <= 1; dc++) {
          if (world.tileMap.get(col + dc, row + dr) === 'brick') {
            world.tileMap.set(col + dc, row + dr, 'empty')
          }
        }
      }
    }
  }

  it('实跑开挖：通道 OPEN 就 handoff，坦克不必走到 P2 出生点', async () => {
    const world = await checkers()
    const ctrl = new OpeningController()
    const first = ctrl.step(world)
    // 初始规划 = 用户确认的那条走廊；后续 replan 会随砖被拆而变短，
    // 所以形状只在这里断言，不拿终态 snapshot 反推。
    expect(first.phase).toBe('p1p2')
    expect(first.bricksPlanned).toBe(35)
    expect(ctrl.snapshot().corridorRelaxed).toBe(true)
    expect(ctrl.snapshot().corridorLength).toBe(28)
    // 35 砖 × ~74 tick + 28 步 ⇒ 预算必须由计划推导，不能用死 2400。
    expect(ctrl.snapshot().openingMaxTicks).toBeGreaterThan(2400)

    applyAction(world, first.action)
    const last = drive(world, ctrl, 5000)
    expect(last.done).toBe(true)
    expect(last.abortReason).toBeNull()
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
    const cell = playerCell(world)
    expect(cell.col === 16 && cell.row === 24).toBe(false)
  })

  it('双人各开一半：另一辆坦克拆完剩余砖后立刻 handoff，不重复开挖', async () => {
    const world = await checkers()
    const ctrl = new OpeningController()
    const first = ctrl.step(world)
    expect(first.phase).toBe('p1p2')
    expect(first.bricksPlanned).toBeGreaterThan(0)

    // P2 把另一半砖拆掉（地形 revision 变化，不经过本 controller）。
    clearPlannedBricks(world)
    const second = ctrl.step(world)
    expect(second.done).toBe(true)
    expect(second.abortReason).toBeNull()
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
    // 本 controller 一步没走，仍在 P1 出生点。
    const cell = playerCell(world)
    expect(cell.col).toBe(8)
    expect(cell.row).toBe(24)
  })
})

/** 用户 2026-11-14 拍板「全局加出袋」：所有关 handoff 前先推到通道中点。 */
describe('全局出袋 exitPocket（A/B 对照）', () => {
  it('默认关闭 = bare 对照臂：handoff 时坦克仍在 P1 出生点', () => {
    const { world } = setupGodGame()
    setRingSteel(world)
    const ctrl = new OpeningController()
    const last = drive(world, ctrl, 3000)
    expect(last.done).toBe(true)
    expect(ctrl.snapshot().exitPocketDone).toBe(false)
    expect(playerCell(world)).toEqual({ col: 8, row: 24 })
  })

  it('开启后：坦克被推到 P1↔P2 通道中点再 handoff（全地图统一）', () => {
    const { world } = setupGodGame()
    setRingSteel(world)
    const ctrl = new OpeningController({ exitPocket: true })
    const last = drive(world, ctrl, 3000)
    expect(last.done).toBe(true)
    const snap = ctrl.snapshot()
    expect(snap.p1p2Connected).toBe(true)
    expect(snap.exitPocketDone).toBe(true)
    expect(snap.exitPocketTarget).not.toBeNull()
    expect(snap.exitPocketTicks).toBeGreaterThan(0)
    expect(playerCell(world)).toEqual(snap.exitPocketTarget!)
    // staging 必须在下 1/3 且贴环墙 band 内（14 步通道的中点）。
    expect(snap.exitPocketTarget!.row).toBeGreaterThanOrEqual(20)
    expect(snap.exitPocketTarget!.row).toBeLessThanOrEqual(21)
  })

  it('Checkers：开挖 + 全局出袋串起来也能收敛', async () => {
    const level = Bun.JSONC.parse(
      await Bun.file('nn-training/levels/classic35-safe-base.jsonc').text(),
    ) as { stages: unknown[] }
    const world = new World()
    world.loadStageData(decodeStageGrid(level.stages[20] as never, 2020), 0)
    const ctrl = new OpeningController({ exitPocket: true })
    const last = drive(world, ctrl, 5000)
    expect(last.done).toBe(true)
    expect(last.abortReason).toBeNull()
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
    expect(ctrl.snapshot().exitPocketDone).toBe(true)
  })

  it('出袋预算耗尽 / 无路时仍然 handoff，不会卡死', () => {
    const { world } = setupGodGame()
    setRingSteel(world)
    const ctrl = new OpeningController({ exitPocket: true, exitMaxTicks: 1 })
    const last = drive(world, ctrl, 3000)
    expect(last.done).toBe(true)
    expect(ctrl.snapshot().exitPocketDone).toBe(false)
    expect(ctrl.snapshot().p1p2Connected).toBe(true)
  })
})

describe('OpeningController: determinism', () => {
  it('identical worlds produce identical action traces', () => {
    const run = () => {
      const { world } = setupGodGame()
      setRingSteel(world)
      for (let r = 0; r < GRID; r++) world.tileMap.set(15, r, 'brick')
      const ctrl = new OpeningController()
      const trace: OpeningAction[] = []
      let step = ctrl.step(world)
      trace.push(step.action)
      for (let i = 0; i < 30 && !step.done; i++) {
        applyAction(world, step.action)
        step = ctrl.step(world)
        trace.push(step.action)
      }
      return trace
    }
    expect(run()).toEqual(run())
  })
})
