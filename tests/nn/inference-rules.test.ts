import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'
import { CELL, FIELD, BASE_POS, POWERUP_TIMEOUT_MS, type Direction } from '../../src/constants'
import { STAGES } from '../../src/config/stages'
import { World } from '../../src/game/World'
import type { PowerUp, Tank, TerrainType } from '../../src/types'
import {
  USEFUL_FIRE_MAX_AXIAL_PX,
  USEFUL_FIRE_VERSION,
  alternativeMoveDir,
  brickAheadWithin,
  brickBlocksAdvance,
  crossfireEscapeDir,
  duelShotReady,
  firstBulletBlocker,
  freezeAttackDir,
  leadMissShot,
  pincerBreakoutDir,
  reachablePickupDir,
  steelBlockedMove,
  steelOnlyShot,
  uselessShot,
  usefulFire,
  turnWillCommit,
} from '../../src/nn/inference-rules'
import { turnWillCommit as turnWillCommitSource } from '../../src/ai/god/turn-gate'

/**
 * P-1b-0 predicate matrix (plan/nn-input-inference-rules.plan.md §2.3 / §7 #15).
 *
 * Geometry: player at (8*CELL, 10*CELL) → centre (144, 176), shooter cell
 * (col 9, row 11). A target at (8*CELL, 16*CELL) is 6 cells straight ahead
 * (axial 96px), centre-aligned. The corridor rows 12..23 / cols 7..12 are
 * cleared so only the cell under test can block.
 */

const PX = 8 * CELL
const PY = 10 * CELL
const SHOOTER_COL = 9
const SHOOTER_ROW = 11

function buildWorld(): World {
  const w = new World()
  w.difficultyKey = 'hard'
  w.loadStageData(STAGES[0], 0)
  w.playerLevel = 0
  const p = w.player!
  p.alive = true
  p.spawnTimer = 0
  return w
}

function placePlayer(w: World, x = PX, y = PY, dir: Direction = 'down'): Tank {
  const p = w.player!
  p.x = x
  p.y = y
  p.dir = dir
  p.prevMoveDir = dir
  p.moving = false
  p.alive = true
  p.spawnTimer = 0
  return p
}

function addEnemy(w: World, x: number, y: number): Tank {
  const e = w.createTank('basic', x, y, 'up')
  e.alive = true
  e.spawnTimer = 0
  w.tanks.push(e)
  return e
}

function clearCorridor(w: World): void {
  for (let r = 4; r <= 25; r++) {
    for (let c = 6; c <= 13; c++) w.tileMap.destroy(c, r)
  }
}

function laneCell(w: World, col: number, row: number, type: TerrainType): void {
  w.tileMap.set(col, row, type)
}

describe('usefulFire v1 (P-1b-0)', () => {
  it('freezes the predicate version', () => {
    expect(USEFUL_FIRE_VERSION).toBe('u1-static-lane-2026-11-13')
    expect(USEFUL_FIRE_MAX_AXIAL_PX).toBe(13 * CELL)
  })

  it('re-exports the single turnWillCommit implementation', () => {
    expect(turnWillCommit).toBe(turnWillCommitSource)
  })

  it('returns false with no player / dead player', () => {
    const w = buildWorld()
    expect(usefulFire(w, null, 'down')).toBe(false)
    const p = placePlayer(w)
    p.alive = false
    expect(usefulFire(w, p, 'down')).toBe(false)
  })

  it('hits an enemy straight ahead within 13 cells', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX, PY + 6 * CELL)
    expect(usefulFire(w, p, 'down')).toBe(true)
  })

  it('accepts ±18px lateral offset and rejects ±20px', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)

    const near = addEnemy(w, PX + 18, PY + 6 * CELL)
    expect(usefulFire(w, p, 'down')).toBe(true)
    near.alive = false

    addEnemy(w, PX + 20, PY + 6 * CELL)
    expect(usefulFire(w, p, 'down')).toBe(false)
  })

  it('rejects an enemy behind the shooter', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX, PY - 6 * CELL)
    expect(usefulFire(w, p, 'down')).toBe(false)
  })

  it('rejects when the first lane cell is brick / steel / base', () => {
    for (const type of ['brick', 'steel', 'base'] as const) {
      const w = buildWorld()
      clearCorridor(w)
      const p = placePlayer(w)
      addEnemy(w, PX, PY + 6 * CELL)
      laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, type)
      expect(usefulFire(w, p, 'down')).toBe(false)
    }
  })

  it('lets bullets pass water / grass / ice', () => {
    for (const type of ['water', 'forest', 'ice'] as const) {
      const w = buildWorld()
      clearCorridor(w)
      const p = placePlayer(w)
      addEnemy(w, PX, PY + 6 * CELL)
      laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, type)
      expect(usefulFire(w, p, 'down')).toBe(true)
    }
  })

  it('rejects an enemy behind steel even when the lane is otherwise clear', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX, PY + 6 * CELL)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(usefulFire(w, p, 'down')).toBe(false)
  })

  it('ignores spawning enemies, decoys and non-enemy allegiance', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)

    const spawning = addEnemy(w, PX, PY + 6 * CELL)
    spawning.spawnTimer = 100
    expect(usefulFire(w, p, 'down')).toBe(false)

    spawning.spawnTimer = 0
    spawning.isDecoy = true
    expect(usefulFire(w, p, 'down')).toBe(false)

    spawning.isDecoy = false
    spawning.allegiance = 'ally'
    expect(usefulFire(w, p, 'down')).toBe(false)
  })

  it('enforces the 13-cell axial cap', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    const edge = addEnemy(w, PX, PY + USEFUL_FIRE_MAX_AXIAL_PX)
    expect(edge.y + edge.h).toBeLessThanOrEqual(FIELD)
    expect(usefulFire(w, p, 'down')).toBe(true)

    edge.alive = false
    addEnemy(w, PX, PY + USEFUL_FIRE_MAX_AXIAL_PX + CELL)
    expect(usefulFire(w, p, 'down')).toBe(false)
  })

  it('handles horizontal lanes with the same ±19px band', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w, PX, PY, 'right')
    addEnemy(w, PX + 6 * CELL, PY)
    expect(usefulFire(w, p, 'right')).toBe(true)

    w.tanks.length = 0
    addEnemy(w, PX + 6 * CELL, PY + 18)
    expect(usefulFire(w, p, 'right')).toBe(true)

    w.tanks.length = 0
    addEnemy(w, PX + 6 * CELL, PY + 20)
    expect(usefulFire(w, p, 'right')).toBe(false)
  })
})

describe('steelOnlyShot v1 (skipSteelOnlyShots)', () => {
  it('is true when the first blocker is steel and no enemy is hittable', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(steelOnlyShot(w, p, 'down')).toBe(true)
  })

  it('is false when the player can pierce steel (level 3)', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    p.level = 3
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(steelOnlyShot(w, p, 'down')).toBe(false)
  })

  it('is false when the first blocker is brick or base', () => {
    for (const type of ['brick', 'base'] as const) {
      const w = buildWorld()
      clearCorridor(w)
      const p = placePlayer(w)
      laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, type)
      expect(steelOnlyShot(w, p, 'down')).toBe(false)
    }
  })

  it('is false when an enemy is hittable before the steel', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX, PY + 3 * CELL)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 4, 'steel')
    expect(steelOnlyShot(w, p, 'down')).toBe(false)
  })

  it('is true when an enemy is behind the steel (the shot hits steel first)', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX, PY + 6 * CELL)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(steelOnlyShot(w, p, 'down')).toBe(true)
  })

  it('is false on a clear lane', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    expect(steelOnlyShot(w, p, 'down')).toBe(false)
  })

  it('passes water / forest / ice before the steel', () => {
    for (const type of ['water', 'forest', 'ice'] as const) {
      const w = buildWorld()
      clearCorridor(w)
      const p = placePlayer(w)
      laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, type)
      laneCell(w, SHOOTER_COL, SHOOTER_ROW + 2, 'steel')
      expect(steelOnlyShot(w, p, 'down')).toBe(true)
    }
  })

  it('is false for a dead player or no player', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    p.alive = false
    expect(steelOnlyShot(w, p, 'down')).toBe(false)
    expect(steelOnlyShot(w, null, 'down')).toBe(false)
  })

  it('handles horizontal lanes', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w, PX, PY, 'right')
    laneCell(w, SHOOTER_COL + 1, SHOOTER_ROW, 'steel')
    expect(steelOnlyShot(w, p, 'right')).toBe(true)
  })
})

describe('B2 v1 predicates (break path bricks)', () => {
  it('firstBulletBlocker returns the first blocking terrain only', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 2, 'brick')
    expect(firstBulletBlocker(w, p, 'down')).toBe('brick')

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(firstBulletBlocker(w, p, 'down')).toBe('steel')

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'base')
    expect(firstBulletBlocker(w, p, 'down')).toBe('base')

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'water')
    expect(firstBulletBlocker(w, p, 'down')).toBe('brick')
  })

  it('brickBlocksAdvance requires an adjacent brick and a blocked step', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'brick')
    expect(brickBlocksAdvance(w, p, 'down')).toBe(true)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'empty')
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 2, 'brick')
    expect(brickBlocksAdvance(w, p, 'down')).toBe(false)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(brickBlocksAdvance(w, p, 'down')).toBe(false)
  })

  it('brickAheadWithin sees bricks up to the cap but stops at steel/base', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 2, 'brick')
    expect(brickAheadWithin(w, p, 'down', 1)).toBe(false)
    expect(brickAheadWithin(w, p, 'down', 2)).toBe(true)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(brickAheadWithin(w, p, 'down', 3)).toBe(false)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'water')
    expect(brickAheadWithin(w, p, 'down', 2)).toBe(true)
  })

  it('ignores base-ring bricks (breaking them exposes the base)', () => {
    const w = buildWorld()
    clearCorridor(w)
    const ringCol = BASE_POS.col
    const ringRow = BASE_POS.row - 1
    laneCell(w, ringCol, ringRow, 'brick')

    const far = placePlayer(w, (ringCol - 1) * CELL, (ringRow - 5) * CELL, 'down')
    expect(firstBulletBlocker(w, far, 'down')).toBe('brick')
    expect(brickAheadWithin(w, far, 'down', 5)).toBe(false)

    const near = placePlayer(w, (ringCol - 1) * CELL, (ringRow - 2) * CELL, 'down')
    expect(brickBlocksAdvance(w, near, 'down')).toBe(false)
  })

  it('returns false for a dead player or no player', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'brick')
    p.alive = false
    expect(brickBlocksAdvance(w, p, 'down')).toBe(false)
    expect(brickAheadWithin(w, p, 'down', 2)).toBe(false)
    expect(firstBulletBlocker(w, p, 'down')).toBeNull()
    expect(brickBlocksAdvance(w, null, 'down')).toBe(false)
    expect(brickAheadWithin(w, null, 'down', 2)).toBe(false)
  })
})

/**
 * backlog / dormant batch v1 (B8/B10/B11/C3/C5/C7/C8/D1).
 * Each predicate is pure (World-in, no mutation); these cases pin the trigger
 * boundaries that the 175-pair A/B arms rely on.
 */
describe('backlog v1 predicates', () => {
  it('B8 uselessShot: empty lane yes, enemy / brick no, steel yes', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    expect(uselessShot(w, p, 'down')).toBe(true)

    const e = addEnemy(w, PX, PY + 6 * CELL)
    expect(uselessShot(w, p, 'down')).toBe(false)
    e.alive = false

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 2, 'brick')
    expect(uselessShot(w, p, 'down')).toBe(false)
    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 2, 'steel')
    expect(uselessShot(w, p, 'down')).toBe(true)
  })

  it('B10 leadMissShot: clear lane yes, a hittable enemy no', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    expect(leadMissShot(w, p, 'down')).toBe(true)
    addEnemy(w, PX, PY + 6 * CELL)
    expect(leadMissShot(w, p, 'down') === usefulFire(w, p, 'down')).toBe(false)
  })

  it('B11 steelBlockedMove / alternativeMoveDir', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    expect(steelBlockedMove(w, p, 'down')).toBe(false)

    laneCell(w, SHOOTER_COL, SHOOTER_ROW + 1, 'steel')
    expect(steelBlockedMove(w, p, 'down')).toBe(true)
    expect(alternativeMoveDir(w, p, 'down')).toBe('up')
  })

  it('C3 pincerBreakoutDir: two-sided enemies bias away from the nearest', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX - 2 * CELL, PY)
    addEnemy(w, PX + 2 * CELL, PY)
    expect(pincerBreakoutDir(w, p)).toBe(3) // away from the first (left) enemy = right
  })

  it('C5 freezeAttackDir: only while freezeTimer > 0', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    addEnemy(w, PX, PY + 3 * CELL)
    expect(freezeAttackDir(w, p)).toBe(-1)
    w.freezeTimer = 1000
    expect(freezeAttackDir(w, p)).toBe(1) // down
  })

  it('C7 duelShotReady: ready + enemy facing the player', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    p.lastFire = -9999
    p.nextFireInterval = 1000
    const e = addEnemy(w, PX, PY + 3 * CELL)
    e.dir = 'up'
    expect(duelShotReady(w, p)).toBe(true)
    e.dir = 'left'
    expect(duelShotReady(w, p)).toBe(false)
  })

  it('C8 crossfireEscapeDir: needs ≥2 threat lanes and a near enemy', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    const below = addEnemy(w, PX, PY + 3 * CELL)
    below.dir = 'up'
    const above = addEnemy(w, PX, PY - 3 * CELL)
    above.dir = 'down'
    addEnemy(w, PX + 2 * CELL, PY + 2 * CELL)
    expect(crossfireEscapeDir(w, p)).toBeGreaterThanOrEqual(0)

    above.alive = false
    expect(crossfireEscapeDir(w, p)).toBe(-1)
  })

  it('D1 reachablePickupDir: ETA ≤ TTL and no nearby enemy', () => {
    const w = buildWorld()
    clearCorridor(w)
    const p = placePlayer(w)
    p.speed = 1
    const pu: PowerUp = {
      id: 1,
      type: 'star',
      x: PX,
      y: PY + 4 * CELL,
      w: 16,
      h: 16,
      alive: true,
      blinkTimer: 0,
      lifeTimer: 0,
    }
    w.powerUps.push(pu)
    expect(reachablePickupDir(w, p)).toBe(1) // down

    pu.lifeTimer = POWERUP_TIMEOUT_MS - 500 // remaining ticks < ETA
    expect(reachablePickupDir(w, p)).toBe(-1)

    pu.lifeTimer = 0
    addEnemy(w, PX, PY + 2 * CELL)
    expect(reachablePickupDir(w, p)).toBe(-1)
  })
})

/**
 * 部署面源码门禁（plan/nn-input-inference-rules.plan.md §8.2/§8.3）：
 * 推理侧规则只允许出现在 eval/deploy 链；训练链（rollout / dagger）恒关。
 * 用静态断言而不是运行时开关：将来有人把默认值翻成 true 时，这里先红。
 */
describe('deployment source gate', () => {
  const repoFile = (rel: string): string =>
    readFileSync(new URL(`../../${rel}`, import.meta.url), 'utf8')

  it('training chains never reference the eval-only rule', () => {
    for (const rel of [
      'tools/sim/export-rl-rollout.ts',
      'tools/sim/export-dagger-beta.ts',
      'tools/sim/export-dagger-labels.ts',
    ]) {
      const src = repoFile(rel)
      expect(src.includes('skipSteelOnlyShots')).toBe(false)
      expect(src.includes('breakBricksOnPath')).toBe(false)
      expect(src.includes('skip-steel-only-shots')).toBe(false)
      expect(src.includes('break-bricks-on-path')).toBe(false)
      expect(src.includes('steelOnlyShot')).toBe(false)
      expect(src.includes('suppressUselessFire')).toBe(false)
      expect(src.includes('suppressLeadMissFire')).toBe(false)
      expect(src.includes('suppress-useless-fire')).toBe(false)
      expect(src.includes('suppress-lead-miss-fire')).toBe(false)
      expect(src.includes('uselessShot')).toBe(false)
      expect(src.includes('leadMissShot')).toBe(false)
    }
  })

  it('the eval chain exposes the explicit opt-in flags', () => {
    const src = repoFile('tools/sim/export-eval-game.ts')
    expect(src).toContain('--skip-steel-only-shots')
    expect(src).toContain('--break-bricks-on-path')
    expect(src).toContain('--suppress-useless-fire')
    expect(src).toContain('--suppress-lead-miss-fire')
  })
})
