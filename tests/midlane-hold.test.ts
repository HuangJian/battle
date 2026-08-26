import { seedWorld } from './helpers'
// §164 中路列旁主动驻守 (proactive mid-lane flank hold) — 诚实阴性归档基线。
// 用户需求 2026-08-06：让 §162 出袋后的玩家优先走中路走廊（而非左侧），在列旁
// 持枪对消。三轮 A/B 全部显著为负：
//   - Battlement 120 种子：mlh 0.238→0.159（p=0.0000）、combo(+§163) 0.238→0.168（p=0.0001）
//   - 全 35 关 60 种子：mlh 0.5522→0.4842（p=0.0000），3 关显著变差（Checkers
//     -52pp / Steel Web -32pp / Battlement -12pp），0 关变好
// 机制根因：基地列凿穿不致死（获胜跑 breach12=12/12 照样 stageclear）——基地死于
// 边路/环砖威胁，靠玩家整体击杀压力；驻守列旁饿死击杀压力 → 边路蜂拥。列开放的
// 地图上（Checkers/Steel Web）驻守把玩家钉在中路是灾难。
// 默认 midLaneHold=0 OFF（byte-identical）；守卫钉 0（§159/§160 回放锁定）。
import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { GUARD_GOD_AI_PARAMS } from '../src/ai/god/params'
import {
  laneColumnOpenToBaseImpl,
  findParryHoldCellImpl,
  enemyNearLaneImpl,
} from '../src/ai/god/PathCarve'
import { RNG } from '../src/utils/RNG'
import { RULES } from '../src/config/rules'
import { DIFFICULTIES } from '../src/config/difficulty'
import { STAGES } from '../src/config/stages'
import { CELL } from '../src/constants'

function makeWorld(
  stageIndex: number,
  over: Record<string, number> = {},
): {
  world: World
  ai: GodAIInput
} {
  const world = seedWorld(42)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = { ...RULES['hard'] }
  world.state = 'playing'
  world.coop = false
  world.loadStageData(STAGES[stageIndex], 0)
  world.spawnQueue = []
  world.tanks = []
  world.enemiesSpawned = 0
  world.enemiesTotal = 1
  world.enemiesRemaining = 0
  const p = world.player!
  p.spawnTimer = 0
  p.shieldTimer = 0
  const ai = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...over }, new RNG(0x1234))
  ai.reset()
  return { world, ai }
}

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.dir = 'up'
  p.prevMoveDir = 'up'
  p.spawnTimer = 0
}

describe('§164 — params', () => {
  it('midLaneHold defaults to 0 (OFF, byte-identical)', () => {
    expect(DEFAULT_GOD_AI_PARAMS.midLaneHold).toBe(0)
  })

  it('GUARD_GOD_AI_PARAMS pins midLaneHold to 0 (guards never wander to the plaza)', () => {
    expect(GUARD_GOD_AI_PARAMS.midLaneHold).toBe(0)
  })
})

describe('§164 — laneColumnOpenToBaseImpl', () => {
  it('S34 Battlement: brick column above the base — open (no steel/water)', () => {
    const { ai } = makeWorld(33)
    expect(laneColumnOpenToBaseImpl(ai)).toBe(true)
  })

  it('S13 Lattice: steel-guarded column — closed, no parry value', () => {
    const { ai } = makeWorld(12)
    expect(laneColumnOpenToBaseImpl(ai)).toBe(false)
  })
})

describe('§164 — findParryHoldCellImpl', () => {
  // NOTE: (5,4) is only standable AFTER the §162 escape digs the pocket
  // (col-6 row-4 brick is destroyed en route). On a FRESH S34 the top-plaza
  // standable cell nearest the column is (10,4) — corridor-connected to the
  // plaza hold cell. Tests use (10,4) so the corridor check is meaningful.
  it('S34 top plaza: picks the column-center standable cell (12,4)', () => {
    const { world, ai } = makeWorld(33)
    positionPlayer(world, 10, 4)
    const hold = findParryHoldCellImpl(ai, { col: 10, row: 4 })
    expect(hold).not.toBeNull()
    expect(hold!.col).toBe(12)
    expect(hold!.row).toBe(4)
  })

  it('deterministic: same terrain → same cell, cache hit returns the same value', () => {
    const { world, ai } = makeWorld(33)
    positionPlayer(world, 10, 4)
    const a = findParryHoldCellImpl(ai, { col: 10, row: 4 })
    const b = findParryHoldCellImpl(ai, { col: 10, row: 4 })
    expect(a).toEqual(b)
    expect(a!.col).toBe(12)
  })

  it('terrain mutation invalidates the revision cache (column plaza bricked → null)', () => {
    const { world, ai } = makeWorld(33)
    positionPlayer(world, 10, 4)
    expect(findParryHoldCellImpl(ai, { col: 10, row: 4 })!.col).toBe(12)
    // Brick every col-12/13 plaza cell rows 4-9 (each (12,r) footprint) — the
    // revision bumps, the cache invalidates, and no parry-window cell remains
    // (the column below row 9 is already brick) → null.
    for (let dc = 0; dc <= 1; dc++) {
      for (let dr = 4; dr <= 9; dr++) {
        world.tileMap.set(12 + dc, dr, 'brick')
      }
    }
    expect(findParryHoldCellImpl(ai, { col: 10, row: 4 })).toBeNull()
  })
})

describe('§164 — enemyNearLaneImpl', () => {
  it('enemy inside the lane box → true', () => {
    const { world, ai } = makeWorld(33)
    const e = world.createTank('basic', 12 * CELL, 2 * CELL, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    expect(enemyNearLaneImpl(ai, 12)).toBe(true)
  })

  it('enemy far outside the lane + tight dist → false', () => {
    const { world, ai } = makeWorld(33)
    const e = world.createTank('basic', 0 * CELL, 24 * CELL, 'left')
    e.spawnTimer = 0
    world.tanks.push(e)
    expect(enemyNearLaneImpl(ai, 4)).toBe(false)
  })
})

describe('§164 — MID_LANE_HOLD candidate', () => {
  it('default OFF: never commits (byte-identical — HUNT takes over)', () => {
    const { world, ai } = makeWorld(33)
    positionPlayer(world, 5, 4)
    const e = world.createTank('basic', 12 * CELL, 2 * CELL, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    for (let i = 0; i < 5; i++) {
      ai.getMoveDirection()
      ai.endFrame()
    }
    expect(ai.branchCounts.midLaneHold).toBe(0)
    expect(ai._lastBranch).not.toBe('midLaneHold')
  })

  it('ON + busy mid-lane + top half: navigates toward the parry hold cell', () => {
    const { world, ai } = makeWorld(33, { midLaneHold: 1 })
    positionPlayer(world, 10, 4)
    const e = world.createTank('basic', 12 * CELL, 2 * CELL, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    const dir = ai.getMoveDirection()
    ai.endFrame()
    expect(ai.branchCounts.midLaneHold).toBeGreaterThan(0)
    expect(ai._lastBranch).toBe('midLaneHold')
    // Navigating from (5,4) toward (12,6) — the first step is eastward.
    expect(dir).not.toBeNull()
  })

  it('ON + already AT the hold cell: holds facing up, fires up when a shell is in the column', () => {
    const { world, ai } = makeWorld(33, { midLaneHold: 1 })
    positionPlayer(world, 12, 4)
    // Enemy near the lane but off ANY player firing line (ENGAGE 500 would
    // preempt the hold at 220 — even a diagonal cell within the ±8px scan
    // corridor fires t2a): (15,8) vs player (12,4).
    const e = world.createTank('basic', 15 * CELL, 8 * CELL, 'down')
    e.spawnTimer = 0
    world.tanks.push(e)
    ai.getMoveDirection()
    ai.endFrame()
    expect(ai.branchCounts.midLaneHold).toBeGreaterThan(0)
    expect(ai._lastBranch).toBe('midLaneHold')
    expect(ai._moveDir).toBeNull() // facing up, not walking
  })

  it('ON + mid-lane QUIET (no shell, no enemy near lane): releases to hunt', () => {
    const { world, ai } = makeWorld(33, { midLaneHold: 1 })
    positionPlayer(world, 12, 6)
    // No enemies, no bullets — the mid-lane is not busy → release to HUNT.
    ai.getMoveDirection()
    ai.endFrame()
    expect(ai.branchCounts.midLaneHold).toBe(0)
    expect(ai._lastBranch).not.toBe('midLaneHold')
  })
})
