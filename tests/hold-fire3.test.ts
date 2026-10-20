import { describe, expect, it } from 'bun:test'
import { Input } from '../src/game/Input'
import { Simulation } from '../src/game/Simulation'
import { World } from '../src/game/World'
import { STAGES } from '../src/config/stages'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES } from '../src/config/rules'
import { CELL, GRID, type Direction } from '../src/constants'
import { seedWorld } from './helpers'

/**
 * v3 action contract (plan/nn-capability-upgrade.plan.md B1):
 *   hold=1          -> turn toward `move` but do not advance
 *   fireBeforeTurn  -> fire along the pre-turn facing, then turn
 *   otherwise       -> commit the turn first, then fire along the new facing
 *
 * The contract is opt-in via `usesHoldFire3()`. God-AI/human inputs omit it,
 * so the legacy 5-move/2-fire path stays byte-identical.
 */
class TestInput extends Input {
  private move: Direction | null = null
  private firing = false
  private hold = false
  private fireBeforeTurn = false

  set(move: Direction | null, firing = false, hold = false, fireBeforeTurn = false): void {
    this.move = move
    this.firing = firing
    this.hold = hold
    this.fireBeforeTurn = fireBeforeTurn
  }
  override getMoveDirection(): Direction | null {
    return this.move
  }
  override isFiring(): boolean {
    return this.firing
  }
  isHolding(): boolean {
    return this.hold
  }
  isFireBeforeTurn(): boolean {
    return this.fireBeforeTurn
  }
  usesHoldFire3(): boolean {
    return true
  }
}

function setup(turnCooldownMs = 200): {
  world: World
  sim: Simulation
  input: TestInput
} {
  const world = seedWorld(42)
  world.difficultyKey = 'classic'
  world.difficulty = DIFFICULTIES['classic']
  world.rules = { ...RULES['classic'], turnCooldownMs }
  const input = new TestInput()
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[0], 0)
  world.state = 'playing'

  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) world.tileMap.grid[r][c] = 'empty' as never
  }
  world.tanks.length = 0
  world.allies.length = 0
  world.spawnQueue.length = 0
  world.enemiesRemaining = 1

  const p = world.player!
  p.x = 8 * CELL
  p.y = 10 * CELL
  p.moving = false
  p.spawnTimer = 0
  p.alive = true
  p.hp = p.maxHp
  p.nextFireInterval = 1
  p.lastFire = -9999
  return { world, sim, input }
}

describe('v3 action contract: hold / fireBeforeTurn', () => {
  it('hold=1 + move=d ⇒ 转向到 d、不前进、真换向吃冷却（三头正交）', () => {
    // ⚠ 训练 rollout 与真部署**恒用 turn-only**（`export-rl-rollout` 的 ScriptedInput
    // 与浏览器侧都实现 `isHoldTurnOnly()=>true`）：能力超集，模型必须能学"转向不前进"。
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('right', false, true)
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.prevMoveDir as string).toBe('right')
    expect(p.moving).toBe(false)
    expect(p.x).toBe(8 * CELL)
    expect(p.y).toBe(10 * CELL)

    // Second turn inside the 200ms window must be rejected: hold is not free aim.
    input.set('down', false, true)
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.moving).toBe(false)
  })

  it('fireBeforeTurn fires along the old facing, then commits the turn', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('right', true, false, true)
    sim.tick()

    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot?.dir as string).toBe('up')
    expect(p.dir as string).toBe('right')
    expect(p.prevMoveDir as string).toBe('right')
    expect(p.moving).toBe(true)
  })

  it('default order commits the turn before firing (fire=2)', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('right', true, false, false)
    sim.tick()

    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot?.dir as string).toBe('right')
    expect(p.dir as string).toBe('right')
  })

  it('a rejected turn skips the shot instead of firing sideways (A1 bug)', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    // Requested facing already 'right' but not committed; cooldown blocks it.
    p.dir = 'right'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0

    input.set('right', true, false, false)
    sim.tick()

    expect(world.bullets.some((b) => b.ownerId === p.id)).toBe(false)
    expect(p.dir as string).toBe('up')
    expect(p.moving).toBe(false)
  })

  /**
   * plan §4-B1「实现细节（G6）」：frenzy 锁（`SimulationPlayer.ts:51-69`）必须保持在
   * v3 三头路径**之前**，frenzy 期间 hold / fireBeforeTurn 语义一律不存在。
   * 若有人把 frenzy 早退挪到 v3 分支之后，狂暴宣泄的"锁朝向"会被 NN 的请求覆盖。
   */
  it('frenzy 期间三头语义不生效：朝向/移动/可观测请求全由 frenzy 接管', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    // 手工进入 frenzy（不依赖 activateFrenzy 的库存/道具门槛，NN 路径拿不到道具）。
    const TICK = 1000 / 60
    p.frenzyTimer = 5 * 100
    p.frenzyDir = 'up'
    p.frenzyInterval = 100
    p.frenzyShotsLeft = 3
    // 差 ~1ms 不到一发 ⇒ 本拍不自动开火，隔离出「语义归属」这一个变量。
    p.frenzyLastFire = world.frame * TICK - 100 + 1
    // 先污染 lastAction*，验证 frenzy 分支会**覆写**它们而不是留上拍的陈旧值。
    p.lastActionDir = 'left'
    p.lastActionHold = true
    p.lastActionFire = 1

    // v3 请求：转向 down + 开火 + hold + 先打再走 —— 全套语义都该被忽略。
    input.set('down', true, true, true)
    sim.tick()

    // 朝向仍锁在 frenzyDir，不是 v3 请求的 'down'
    expect(p.dir as string).toBe('up')
    expect(p.prevMoveDir as string).toBe('up')
    expect(p.moving).toBe(false)
    // 可观测请求 = frenzy 的锁定向 + 自动射击，不是 v3 请求的 hold/fireBefore
    expect(p.lastActionDir as string).toBe('up')
    expect(p.lastActionHold).toBe(false)
    // 注：先落到一个显式宽类型的局部量 —— 上面 `p.lastActionFire = 1` 的污染赋值
    // 会把字段窄化成字面量 `1`，直接 `.toBe(2)` 是 TS2769（不是运行期行为问题）。
    const observedFire: number | undefined = p.lastActionFire
    expect(observedFire).toBe(2)
    // frenzy 锁移动 ⇒ 位置不动
    expect(p.x).toBe(8 * CELL)
    expect(p.y).toBe(10 * CELL)
  })

  it('frenzy 自动开火沿 frenzyDir，不沿 v3 请求的方向', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999
    p.frenzyTimer = 5 * 100
    p.frenzyDir = 'up'
    p.frenzyInterval = 100
    p.frenzyShotsLeft = 3
    p.frenzyLastFire = -9999 // 早该开火了 ⇒ 本拍必出弹

    input.set('down', true, false, true) // fireBeforeTurn + 请求 down
    sim.tick()

    const shots = world.bullets.filter((b) => b.ownerId === p.id)
    expect(shots.length).toBeGreaterThan(0)
    // 全沿 frenzyDir('up') —— 没有一发走 v3 请求的 'down'
    for (const b of shots) expect(b.dir as string).toBe('up')
  })
})

/**
 * 「保持朝向」不是转向请求 ⇒ 不占转向冷却（2026-10-20 用户裁定：
 * "不转向就不需要被限制"）。
 *
 * 事故形状：`applyTurnCooldown` 在冷却期内会**回退朝向**（`tank.dir = prevMoveDir`）
 * 并 **halt**（`tank.moving = false`）。旧写法对**每一拍**都要 `dir` 的输入都调它，
 * 于是「hold=1 + 朝向没变」这种纯粹的"站着不动"，只要前几拍刚转过就被判非法转向。
 * v6 的 move 头没有 null 类（4 类全是方向）⇒ 每拍都在请求方向 ⇒ 频繁撞上
 * ⇒ 策略卡死原地（ladder-c20-lives1 实测 idle 79%、pass 9/40 vs legacy 27/40）。
 *
 * legacy 路径本来就是这个语义：STOP 时 `dir === null`，根本不进 turn 分支。
 */
describe('v3 三头正交：hold / move / fire 各管各的（2×4×3 = 24 组合全合法）', () => {
  it('hold 只决定"前进与否"，不改 move 的方向语义', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    // hold=1 → 转向到 right 但不前进
    input.set('right', false, true)
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.moving).toBe(false)

    // hold=0 → 同一个 right，这次前进
    input.set('right', false, false)
    sim.tick()
    expect(p.dir as string).toBe('right')
    expect(p.moving).toBe(true)
  })

  it('fire 与 hold/move 独立：hold=0 时 fireBefore 仍沿旧朝向前置开火', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    // hold=0（前进）+ move=right + fire=1（before turn）
    input.set('right', true, false, true)
    sim.tick()
    const shot = world.bullets.find((b) => b.ownerId === p.id)
    expect(shot?.dir as string).toBe('up') // 沿旧朝向
    expect(p.dir as string).toBe('right')
    expect(p.moving).toBe(true)
  })

  it('hold=1 时 fireAfter 照常出弹（hold 不吞开火）', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = -9999

    input.set('up', true, true, false) // hold=1 + fire=2（after turn）
    sim.tick()
    expect(p.moving).toBe(false)
    expect(world.bullets.some((b) => b.ownerId === p.id)).toBe(true)
  })
})

describe('v3：保持朝向不受转向约束', () => {
  it('hold=1 但方向不变 ⇒ 不吃冷却（"不转向就不需要被限制"）', () => {
    // turn-only 下逃出"永远在冷却"的唯一途径：策略**保持朝向**。
    // 冷启动的策略做不到 ⇒ 这是评估期需要 freeze 的原因。
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0 // 冷却期内

    input.set('up', false, true)
    sim.tick()
    expect(p.dir as string).toBe('up')
    expect(p.moving).toBe(false)
    for (let i = 0; i < 6; i++) sim.tick()
    expect(p.dir as string).toBe('up')
    expect(p.y).toBe(10 * CELL)
  })

  it('hold=1 + 朝向不变 ⇒ 冷却期内也不被拒、朝向不回退、连续多拍都成立', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0 // 冷却期**内**（now - 0 < 200）

    input.set('up', false, true)
    sim.tick()
    expect(p.dir as string).toBe('up') // 不回退到 prevMoveDir
    expect(p.moving).toBe(false) // 不前进（hold 的本义）
    expect(p.x).toBe(8 * CELL)
    expect(p.y).toBe(10 * CELL)

    // 连续 6 拍：每一拍都是"保持朝向 + hold"，都不该被冷却拦。
    // 旧实现在第一拍就会 halt 并把 dir 打回 prevMoveDir。
    for (let i = 0; i < 6; i++) sim.tick()
    expect(p.dir as string).toBe('up')
    expect(p.y).toBe(10 * CELL)
  })

  it('keep-facing + hold=0 也不吃冷却，且冷却期内照样能开火', () => {
    // 旧行为：冷却期内即使朝向没变，`turnAccepted=false` ⇒ **不开火** + halt。
    // 朝向没变时"不能开火"没有依据 —— A1 要防的是"打到旧朝向"，这里没有旧朝向。
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0

    input.set('up', true, false, false) // 同朝向 + 开火（fireAfter）
    sim.tick()
    expect(p.dir as string).toBe('up')
    expect(p.moving).toBe(true) // 前进
    expect(world.bullets.some((b) => b.ownerId === p.id)).toBe(true) // 开火没被吞
  })

  it('真换向仍然吃冷却（这条约束不能松）', () => {
    const { world, sim, input } = setup(200)
    const p = world.player!
    p.dir = 'up'
    p.prevMoveDir = 'up'
    p.lastTurnMs = 0 // 冷却中

    input.set('right', false, true) // 换向 + hold
    sim.tick()
    // 被拒 ⇒ 朝向回退、halt（原有语义，未动）
    expect(p.dir as string).toBe('up')
    expect(p.moving).toBe(false)
    expect(p.x).toBe(8 * CELL)
  })
})
