/**
 * P′-3 E2′ 目标执行层：targetKind=powerup / pathMode=direct|break / mask 回退。
 *
 * 只在 `_candidateOverride={'nnOption'}` + `_optionCmd` 注入时可达；默认链不受影响。
 */
import { describe, expect, it } from 'bun:test'
import { seedWorld, clearArena, positionPlayer } from '../helpers'
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
    { ...DEFAULT_GOD_AI_PARAMS, thinkInterval: 1 },
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
})
