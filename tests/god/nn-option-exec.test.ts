/**
 * P′-3 E2′ 目标执行层：targetKind=powerup / pathMode=direct|break / mask 回退。
 * P′-2a2 追加：显式 targetId 跟踪/失效回退 + param standoff。
 *
 * 只在 `_candidateOverride={'nnOption'}` + `_optionCmd` 注入时可达；默认链不受影响。
 */
import { describe, expect, it } from 'bun:test'
import { seedWorld, clearArena, positionPlayer, placeEnemy } from '../helpers'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { CELL } from '../../src/constants'
import { DEFAULT_GOD_AI_PARAMS, GodAIInput } from '../../src/ai/GodAIInput'
import type { NNOptionCommand } from '../../src/ai/god/NNOption'
import { RNG } from '../../src/utils/RNG'
import { executorSeed } from '../../tools/sim/simulation-runner'
import type { PowerUp } from '../../src/types'

function makeWorld() {
  const world = seedWorld(7)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'] }
  world.loadStageData(STAGES[0], 0)
  clearArena(world)
  world.state = 'playing'
  positionPlayer(world, 5, 5, 'right')
  world.player!.lastFire = -9999
  return world
}

function makeInput(world: ReturnType<typeof makeWorld>, cmd: NNOptionCommand): GodAIInput {
  const input = new GodAIInput(
    world,
    // 关掉 thinkImpl 的链前快速路径（A23 turnToFire / A24 lateHunt），
    // 让本文件的断言只测 E2′ 目标执行层本身。
    { ...DEFAULT_GOD_AI_PARAMS, thinkInterval: 1, turnToFireMode: 0, lateHuntMode: 0 },
    new RNG(executorSeed(7)),
  )
  input.reset()
  input._optionCmd = cmd
  input._candidateOverride = new Set(['nnOption'])
  return input
}

function powerUp(col: number, row: number): PowerUp {
  return {
    id: 1,
    type: 'star',
    x: col * CELL,
    y: row * CELL,
    w: 32,
    h: 32,
    alive: true,
    blinkTimer: 0,
    lifeTimer: 0,
  }
}

const NONE: NNOptionCommand = {
  option: 'HUNT',
  targetKind: 'none',
  target: null,
  pathMode: 'direct',
  param: 0,
}

describe('P′-3 E2′ 目标执行层', () => {
  it('targetKind=powerup / direct：朝最近道具走（先对齐行再右转）并记录 nnOption', () => {
    const world = makeWorld()
    world.addPowerUp(powerUp(10, 5))
    const input = makeInput(world, { ...NONE, targetKind: 'powerup' })
    // A* 先 down 对齐目标行 6，再 right；首步是 down 属正常。
    expect(['down', 'right']).toContain(input.getMoveDirection()!)
    expect(input._lastBranch).toBe('nnOption')
  })

  it('pathMode=break：贴砖时沿 A* 砖代价路径走并开火拆墙', () => {
    const world = makeWorld()
    positionPlayer(world, 6, 6, 'right')
    world.player!.lastFire = -9999
    // 整列砖墙（无绕行）⇒ 必须走 break 路径。
    for (let r = 0; r < 26; r++) world.tileMap.grid[r][8] = 'brick'
    const input = makeInput(world, {
      ...NONE,
      targetKind: 'enemy',
      target: { col: 11, row: 6 },
      pathMode: 'break',
    })
    expect(input.getMoveDirection()).toBe('right')
    expect(input.isFiring()).toBe(true)
    expect(input._lastBranch).toBe('nnOption')
  })

  it('无目标（无道具）⇒ 回退到 option 子链，与 targetKind=none 动作一致', () => {
    const w1 = makeWorld()
    const w2 = makeWorld()
    const deleg = makeInput(w1, NONE)
    const fallback = makeInput(w2, { ...NONE, targetKind: 'powerup' })
    expect(fallback.getMoveDirection()).toBe(deleg.getMoveDirection())
    expect(fallback.isFiring()).toBe(deleg.isFiring())
    expect(fallback._lastBranch).toBe(deleg._lastBranch)
  })

  it('非法 pathMode 在运行期 mask 回 direct（有目标时仍能走）', () => {
    const world = makeWorld()
    world.addPowerUp(powerUp(10, 5))
    const input = makeInput(world, {
      ...NONE,
      targetKind: 'powerup',
      pathMode: 'bogus' as never,
    })
    expect(['down', 'right']).toContain(input.getMoveDirection()!)
    expect(input._lastBranch).toBe('nnOption')
  })

  it('targetId：按 id 逐 tick 解析目标格并执行 nnOption', () => {
    const world = makeWorld()
    const enemy = placeEnemy(world, 8, 5)
    const input = makeInput(world, {
      ...NONE,
      targetKind: 'enemy',
      targetId: enemy.id,
      param: 0,
    })
    expect(input.getMoveDirection()).not.toBeNull()
    expect(input._lastBranch).toBe('nnOption')
  })

  it('targetId 失效（目标已死）⇒ 回退 option 子链，不静默改就近目标', () => {
    const w1 = makeWorld()
    const w2 = makeWorld()
    const dead = placeEnemy(w1, 8, 5)
    const alive = placeEnemy(w2, 8, 5)
    dead.alive = false
    const deleg = makeInput(w1, NONE)
    const fallback = makeInput(w1, { ...NONE, targetKind: 'enemy', targetId: dead.id, param: 0 })
    expect(fallback.getMoveDirection()).toBe(deleg.getMoveDirection())
    expect(fallback.isFiring()).toBe(deleg.isFiring())
    expect(fallback._lastBranch).toBe(deleg._lastBranch)
    // 对照世界（敌人活着）里同一命令会走 nnOption —— 证明差异来自 targetId 失效。
    const hit = makeInput(w2, { ...NONE, targetKind: 'enemy', targetId: alive.id, param: 0 })
    expect(hit.getMoveDirection()).not.toBeNull()
    expect(hit._lastBranch).toBe('nnOption')
  })

  it('param standoff：距离 ≤ 档位时交回 option 子链（param=0 仍贴近目标）', () => {
    const w1 = makeWorld()
    const w2 = makeWorld()
    const e1 = placeEnemy(w1, 8, 5) // 玩家 (5,5) ⇒ 距离 3
    const e2 = placeEnemy(w2, 8, 5)
    const standoff = makeInput(w1, {
      ...NONE,
      targetKind: 'enemy',
      targetId: e1.id,
      param: 1, // standoff = 3
    })
    const deleg = makeInput(w1, NONE)
    expect(standoff.getMoveDirection()).toBe(deleg.getMoveDirection())
    expect(standoff._lastBranch).toBe(deleg._lastBranch)

    const press = makeInput(w2, { ...NONE, targetKind: 'enemy', targetId: e2.id, param: 0 })
    expect(press.getMoveDirection()).not.toBeNull()
    expect(press._lastBranch).toBe('nnOption')
  })
})
