import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { Input } from '../src/game/Input'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { findMustPickupHighImpl } from '../src/ai/god/StrategyPlanner'
import { CELL } from '../src/constants'
import type { PowerUp } from '../src/types'
import { clearArena, placeEnemy, makePowerUp as makePowerUpShared, seedWorld } from './helpers'

/**
 * 2026-10-29 用户规则：除非基地正在被威胁，8 格以内的 freeze / bomb 必捡；
 * 8 格以外（≤ highSafePickupRange）在保证安全的前提下也捡。
 *
 * 这些用例锁定：
 *   1. mustPickupHighMode=0 → null（byte-identical）。
 *   2. MUST 带（≤8 格）无视 nearbyEnemy / routeDanger —— 敌人贴脸也去捡。
 *   3. 基地受威胁 → null（回防优先，规则唯一例外）。
 *   4. SAFE 带（8 < dist ≤ highSafePickupRange）保留 nearbyEnemy 安全门。
 *   5. SAFE 带无敌人时成立。
 *   6. 只对 freeze / bomb 生效（star 不捡）。
 */

function setupWorld(params: Partial<typeof DEFAULT_GOD_AI_PARAMS> = {}): {
  world: World
  input: GodAIInput
} {
  const world = seedWorld(42)
  const input = new GodAIInput(world, { ...DEFAULT_GOD_AI_PARAMS, ...params })
  const sim = new Simulation(world, new Input())
  world.startGame('hard', 'modern', 0)
  clearArena(world)
  void sim
  input.reset()
  return { world, input }
}

function positionPlayer(world: World, col: number, row: number): void {
  const p = world.player!
  p.x = col * CELL
  p.y = row * CELL
  p.hp = 100
  p.spawnTimer = 0
  p.shieldTimer = 0
  p.level = 0
  world.playerLevel = 0
}

const makePowerUp = (world: World, col: number, row: number, type: PowerUp['type']): PowerUp => {
  const pu = makePowerUpShared(col, row, type)
  world.powerUps.push(pu)
  return pu
}

const CENTER = (col: number, row: number) => ({ x: col * CELL + CELL, y: row * CELL + CELL })

describe('must-pick HIGH (freeze/bomb) — 2026-10-29 user rule', () => {
  it('mode 0 → null (byte-identical)', () => {
    const { world, input } = setupWorld({ mustPickupHighMode: 0 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 14, 10, 'freeze')
    const { x, y } = CENTER(10, 10)
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
  })

  it('MUST band ignores the nearby-enemy + route-danger gates', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      highSafePickupRange: 0,
      pickupPriorityMinEnemyDist: 5,
    })
    positionPlayer(world, 10, 4)
    // Enemy 2 cells away from the player (would block the §87 urgent path),
    // freeze 4 cells away, same row. Both are far from the base (row <= 6),
    // so the base-threat gate stays false.
    placeEnemy(world, 10, 6)
    makePowerUp(world, 14, 4, 'freeze')
    expect(input.isBaseUnderThreat()).toBe(false)
    const { x, y } = CENTER(10, 4)
    const target = findMustPickupHighImpl(input, x, y)
    expect(target).not.toBeNull()
    expect(target!.col).toBe(14)
    expect(target!.row).toBe(4)
  })

  it('returns null while the base is under threat (the one exception)', () => {
    const { world, input } = setupWorld({ mustPickupHighMode: 1, mustPickupHighRange: 8 })
    positionPlayer(world, 10, 10)
    makePowerUp(world, 14, 10, 'freeze')
    // Enemy inside the static base box (|col-12| <= 3, row >= 18).
    placeEnemy(world, 12, 20)
    expect(input.isBaseUnderThreat()).toBe(true)
    const { x, y } = CENTER(10, 10)
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
  })

  it('SAFE band keeps the nearby-enemy gate', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      highSafePickupRange: 16,
      pickupPriorityMinEnemyDist: 5,
    })
    positionPlayer(world, 10, 4)
    placeEnemy(world, 10, 6) // 2 cells — blocks the SAFE band, no base threat
    makePowerUp(world, 10, 13, 'bomb') // 9 cells away (beyond MUST=8)
    expect(input.isBaseUnderThreat()).toBe(false)
    const { x, y } = CENTER(10, 4)
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
  })

  it('SAFE band works when the field is safe', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      highSafePickupRange: 16,
    })
    positionPlayer(world, 10, 4)
    makePowerUp(world, 10, 13, 'bomb') // 9 cells away, beyond MUST
    const { x, y } = CENTER(10, 4)
    const target = findMustPickupHighImpl(input, x, y)
    expect(target).not.toBeNull()
    expect(target!.row).toBe(13)
  })

  it('does not divert for non-HIGH items (star)', () => {
    const { world, input } = setupWorld({ mustPickupHighMode: 1, mustPickupHighRange: 8 })
    positionPlayer(world, 10, 4)
    makePowerUp(world, 14, 4, 'star')
    const { x, y } = CENTER(10, 4)
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
  })

  it('optional contact gate (mustPickupMinEnemyDist) blocks an adjacent enemy grab', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      mustPickupMinEnemyDist: 2,
    })
    positionPlayer(world, 10, 4)
    placeEnemy(world, 10, 6) // 2 cells from the player
    makePowerUp(world, 14, 4, 'freeze')
    const { x, y } = CENTER(10, 4)
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
  })

  it('contact gate 1 lets a 2-cell-away enemy grab through', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      mustPickupMinEnemyDist: 1,
    })
    positionPlayer(world, 10, 4)
    placeEnemy(world, 10, 6)
    makePowerUp(world, 14, 4, 'freeze')
    const { x, y } = CENTER(10, 4)
    expect(findMustPickupHighImpl(input, x, y)).not.toBeNull()
  })

  it('minKills gate suppresses must-pick in the opening', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      mustPickupMinKills: 5,
    })
    positionPlayer(world, 10, 4)
    makePowerUp(world, 14, 4, 'freeze')
    const { x, y } = CENTER(10, 4)
    world.killCount = 2
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
    world.killCount = 5
    expect(findMustPickupHighImpl(input, x, y)).not.toBeNull()
  })

  it('commit window keeps the first target when a closer item appears', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      mustPickupCommitTicks: 90,
    })
    positionPlayer(world, 10, 4)
    makePowerUp(world, 14, 4, 'freeze')
    const { x, y } = CENTER(10, 4)
    const first = findMustPickupHighImpl(input, x, y)
    expect(first).not.toBeNull()
    expect(first!.col).toBe(14)
    // A closer bomb appears — without the commit this would win (nearest-first).
    makePowerUp(world, 11, 4, 'bomb')
    const second = findMustPickupHighImpl(input, x, y)
    expect(second).not.toBeNull()
    expect(second!.col).toBe(14)
  })

  it('commit is dropped when the committed item disappears', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      mustPickupCommitTicks: 90,
    })
    positionPlayer(world, 10, 4)
    const frozen = makePowerUp(world, 14, 4, 'freeze')
    const { x, y } = CENTER(10, 4)
    expect(findMustPickupHighImpl(input, x, y)).not.toBeNull()
    frozen.alive = false
    world.powerUps = world.powerUps.filter((pu) => pu !== frozen)
    makePowerUp(world, 11, 4, 'bomb')
    const next = findMustPickupHighImpl(input, x, y)
    expect(next).not.toBeNull()
    expect(next!.col).toBe(11)
  })

  it('base threat cancels an active commit', () => {
    const { world, input } = setupWorld({
      mustPickupHighMode: 1,
      mustPickupHighRange: 8,
      mustPickupCommitTicks: 90,
    })
    positionPlayer(world, 10, 4)
    makePowerUp(world, 14, 4, 'freeze')
    const { x, y } = CENTER(10, 4)
    expect(findMustPickupHighImpl(input, x, y)).not.toBeNull()
    placeEnemy(world, 12, 20) // base box threat
    // The threat predicate is memoized per think tick; clear it for the test.
    input._baseUnderThreatCache = null
    expect(input.isBaseUnderThreat()).toBe(true)
    expect(findMustPickupHighImpl(input, x, y)).toBeNull()
    expect(input._mustCommitActive).toBe(false)
  })
})
