import { describe, it, expect } from 'bun:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, GRID } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { GoalExecutor } from '../../src/nn/goal-executor'
import type { StageData } from '../../src/types'

/**
 * T8.5 验收（headless 可测部分）：
 *  - 确定性：同 seed 双跑 ⇒ outcome/ticks/reselectTrace 逐字节一致
 *  - 心跳节奏：E4 重选间隔 = promiseTicks（§6.3 慢心跳）
 *  - E3 事件驱动：目标格被钢封死（revision bump）⇒ 立即重选且 clause='E3'（§6.6）
 *  - reset() 清态
 *  - 随机权重端到端跑完整局（不崩、走 InputLike 契约）
 *
 * 权重：goal-golden fixture 的瘦网络参数（h=16/d=2，随机初始化，仅测管线不测策略质量）。
 */
const GOLDEN = JSON.parse(
  readFileSync(join(import.meta.dir, '..', 'fixtures', 'goal-golden.json'), 'utf8'),
) as { h: number; d: number; params: Record<string, unknown> }
const GOAL_WEIGHTS_TEXT = JSON.stringify({
  arch: { kind: 'goal', h: GOLDEN.h, d: GOLDEN.d },
  params: GOLDEN.params,
})

function freshWorld(seed: number, stageIdx = 0): World {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard'] ?? DIFFICULTIES['classic']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  world.loadStageData(STAGES[stageIdx] as StageData, stageIdx)
  return world
}

function runGame(
  seed: number,
  maxTicks: number,
  promiseTicks = 240,
  mutate?: (world: World, exec: GoalExecutor, tick: number) => void,
): { ticks: number; state: string; trace: GoalExecutor['reselectTrace']; world: World } {
  const world = freshWorld(seed)
  const exec = new GoalExecutor(world, {
    weightsText: GOAL_WEIGHTS_TEXT,
    rng: new RNG((seed ^ 0x9e3779b9) >>> 0),
    promiseTicks,
    recordTrace: true,
  })
  const sim = new Simulation(world, exec as never)
  exec.reset()
  let t = 0
  while (t < maxTicks) {
    mutate?.(world, exec, t)
    sim.tick()
    exec.endFrame()
    t++
    if (world.state === 'gameover' || world.state === 'stageclear' || world.state === 'victory')
      break
  }
  return { ticks: t, state: world.state, trace: exec.reselectTrace, world }
}

describe('GoalExecutor（T8.5）', () => {
  it('确定性：同 seed 双跑 outcome/ticks/reselectTrace 一致（零 world.rng 消费）', () => {
    const a = runGame(11, 3000)
    const b = runGame(11, 3000)
    expect(a.ticks).toBe(b.ticks)
    expect(a.state).toBe(b.state)
    expect(a.trace.length).toBe(b.trace.length)
    for (let i = 0; i < a.trace.length; i++) {
      expect(a.trace[i].tick).toBe(b.trace[i].tick)
      expect(a.trace[i].cell).toBe(b.trace[i].cell)
      expect(a.trace[i].clause).toBe(b.trace[i].clause)
      expect(a.trace[i].outcome).toBe(b.trace[i].outcome)
    }
  })

  it('E4 心跳：promiseTicks=60 ⇒ 重选间隔 ≈60（§6.3 慢心跳）', () => {
    const { trace } = runGame(3, 2500, 60)
    expect(trace.length).toBeGreaterThan(5)
    for (let i = 1; i < trace.length; i++) {
      const gap = trace[i].tick - trace[i - 1].tick
      // 事件驱动（E1/E3/E5）可提前，但不会晚于心跳太多
      expect(gap).toBeGreaterThan(0)
      expect(gap).toBeLessThanOrEqual(65)
    }
  })

  it('E3 事件驱动：契约格足印被钢封死（revision bump）⇒ clause=E3 立即重选', () => {
    let sealed = false
    let sealedAt = -1
    const { trace } = runGame(11, 4000, 240, (world, exec, tick) => {
      if (!sealed && tick >= 500) {
        // 读当前契约格，把它的 2×2 足印铸成钢（避开玩家所在格 —— 玩家被封是另一场景）
        const cell = exec.currentGoalCell
        const p = world.player
        const pcol = p ? Math.round(p.x / 16) : -1
        const prow = p ? Math.round(p.y / 16) : -1
        if (cell >= 0) {
          const col = cell % GRID
          const row = (cell - col) / GRID
          if (
            row + 1 < GRID &&
            col + 1 < GRID &&
            !(Math.abs(col - pcol) <= 1 && Math.abs(row - prow) <= 1)
          ) {
            for (let dr = 0; dr <= 1; dr++)
              for (let dc = 0; dc <= 1; dc++) world.tileMap.set(col + dc, row + dr, 'steel')
            sealed = true
            sealedAt = tick
          }
        }
      }
    })
    expect(sealed).toBe(true)
    const e3 = trace.find((r) => r.clause === 'E3')
    expect(e3).toBeDefined()
    // 事件驱动：E3 在封格后 ≤3 tick 内触发（不等 E4 心跳）
    expect(e3!.tick).toBeGreaterThanOrEqual(sealedAt)
    expect(e3!.tick).toBeLessThanOrEqual(sealedAt + 3)
  })

  it('reset()：清空契约与遥测（restart 语义）', () => {
    const world = freshWorld(5)
    const exec = new GoalExecutor(world, {
      weightsText: GOAL_WEIGHTS_TEXT,
      rng: new RNG(77),
      recordTrace: true,
    })
    const sim = new Simulation(world, exec as never)
    exec.reset()
    for (let t = 0; t < 400; t++) {
      sim.tick()
      exec.endFrame()
    }
    expect(exec.reselectTrace.length).toBeGreaterThan(0)
    exec.reset()
    expect(exec.reselectTrace.length).toBe(0)
    // reset 后能继续跑（无状态泄漏崩坏）
    for (let t = 0; t < 100; t++) {
      sim.tick()
      exec.endFrame()
    }
  })

  it('完整局端到端：随机权重跑完 8000 tick 内结束且 Outcome 合法', () => {
    const { state, ticks } = runGame(21, 8000, 240)
    expect(['gameover', 'stageclear', 'victory', 'timeout']).toContain(state)
    expect(ticks).toBeGreaterThan(0)
  })
})
