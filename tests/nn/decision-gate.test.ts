/**
 * decision-gate.test.ts — 决策门单测（plan/new-era-stop.plan.md §6 R2）。
 *
 * 锁四件事（每条都对应一种会静默改变训练/判决口径的错）：
 *   ① **均匀模式 = 旧 `t % k === 0` 的精确实现**（x1 首刀逐字节不变的前提）；
 *   ② **threat-ONSET 是沿不是电平**（入带翻沿触发、出带不触发、持续带不重触发，hy P0-3）；
 *   ③ **最小间隔闸**（两次决策 Δt ≥ minGap；含「事件后紧邻的均匀边界被跳过」这条罕见语义）；
 *   ④ **state-init 交棒首段禁事件**（R2.4 方案 i：窗内开始的威胁不追发）。
 *
 * 夹具确定性：固定 seed、威胁态由 `alive` 开关控制（几何不变）、无墙钟、无 Math.random。
 */
import { describe, expect, it } from 'bun:test'
import { BULLET, CELL } from '../../src/constants'
import { World } from '../../src/game/World'
import { makeBullet, placeEnemy, positionPlayer, seedWorld } from '../helpers'
import {
  DEFAULT_DECISION_K,
  DEFAULT_DECISION_MIN_GAP,
  createDecisionGateConfig,
  createDecisionGateState,
  decisionDue,
  decisionReadout,
  markDecision,
  poolDecisionReadouts,
  threatOnsetEdge,
  type DecisionGateConfig,
  type DecisionGateState,
} from '../../src/nn/decision-gate'

const PL = 5 // 玩家格 (5, 5)
const PR = 5
const EL = 5 // 敌车同列，3 格上方、炮口朝下
const ER = 2

/** 玩家 + 一枚可控威胁源（`enemy.alive` 即威胁开关，几何恒定）。 */
function fixture(seed = 1): { world: World; enemy: { alive: boolean } } {
  const world = seedWorld(seed)
  world.startGame('hard', 'modern', 0)
  positionPlayer(world, PL, PR)
  const enemy = placeEnemy(world, EL, ER, 'basic', 'down')
  enemy.alive = false // 默认无威胁
  return { world, enemy }
}

/** 跑到 `to`（不含），返回所有 due tick；逐 tick 喂（门的契约）。 */
function dueTicks(
  world: World,
  state: DecisionGateState,
  cfg: DecisionGateConfig,
  to: number,
): number[] {
  const out: number[] = []
  for (let t = 0; t < to; t++) if (decisionDue(t, world, state, cfg)) out.push(t)
  return out
}

/** 在 [from, to) 打开威胁；其余区间关闭。 */
function threat(
  world: World,
  enemy: { alive: boolean },
  t: number,
  from: number,
  to: number,
): void {
  enemy.alive = t >= from && t < to
  void world
}

describe('decision-gate：配置与常量', () => {
  it('K=10 / minGap=3 是冻结值；事件模式下 k < minGap 响亮拒', () => {
    expect(DEFAULT_DECISION_K).toBe(10)
    expect(DEFAULT_DECISION_MIN_GAP).toBe(3)
    expect(() => createDecisionGateConfig(true, 1, 3)).toThrow(/k\(1\) < minGap\(3\)/)
    // 均匀模式不看 minGap（旧行为；k=1 的扫描类调用不该被静默节流）
    const uniform = createDecisionGateConfig(false, 1, 3)
    expect(uniform).toEqual({ k: 1, events: false, minGap: 3 })
  })
})

describe('decision-gate：均匀模式 = 旧 t % k === 0', () => {
  it('events=false：到期集合精确等于 k 的整数倍（有/无威胁都一样）', () => {
    const { world, enemy } = fixture()
    const cfg = createDecisionGateConfig(false)
    const state = createDecisionGateState()
    expect(dueTicks(world, state, cfg, 100)).toEqual([0, 10, 20, 30, 40, 50, 60, 70, 80, 90])
    // 威胁在场也不改变到期集合（均匀模式不读 inThreatLane）
    enemy.alive = true
    const state2 = createDecisionGateState()
    expect(dueTicks(world, state2, cfg, 40)).toEqual([0, 10, 20, 30])
    expect(state2.eventDecisions).toBe(0)
    expect(state.eventDecisions).toBe(0)
  })

  it('读出：均匀模式的 Δt 恒 = K（minDt == maxDt == K）', () => {
    const { world } = fixture()
    const state = createDecisionGateState()
    dueTicks(world, state, createDecisionGateConfig(false), 100)
    const r = decisionReadout(state)
    expect(r.n).toBe(10)
    expect(r.avgDt).toBe(10)
    expect(r.minDt).toBe(10)
    expect(r.maxDt).toBe(10)
  })
})

describe('decision-gate：threat-ONSET 是沿（入带翻沿 / 出带消沿 / 持续带不重触发）', () => {
  it('入带翻沿 → 事件步；出带不触发；持续带不重触发', () => {
    const { world, enemy } = fixture()
    const cfg = createDecisionGateConfig(true)
    const state = createDecisionGateState()
    const dues: number[] = []
    for (let t = 0; t < 60; t++) {
      threat(world, enemy, t, 25, 35) // 25..34 在带内
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    // 0/10/20 均匀；25 入带翻沿（距 20 有 5 tick，过闸）⇒ 事件步；30 均匀；
    // 31..34 持续带 ⇒ 不再触发；35 出带 ⇒ 也不是 onset；40/50 均匀。
    expect(dues).toEqual([0, 10, 20, 25, 30, 40, 50])
    const r = decisionReadout(state)
    expect(r.n).toBe(7)
    expect(r.events).toBe(1)
    expect(r.minDt).toBe(5)
    expect(r.maxDt).toBe(10)
  })

  it('持续威胁从开局就在：只有 t=0 那次决策（无翻沿 = 无事件；防御「电平入集」）', () => {
    const { world, enemy } = fixture()
    enemy.alive = true
    const state = createDecisionGateState()
    const dues = dueTicks(world, state, createDecisionGateConfig(true), 50)
    // t=0：均匀边界（同时也是「首次入带」，但 prevThreat 初值 false 只在 events 模式算 onset——
    // 这里 uniform 优先计数 ⇒ 不算事件步）。之后 10/20/30/40 全均匀，无事件步。
    expect(dues).toEqual([0, 10, 20, 30, 40])
    expect(decisionReadout(state).events).toBe(0)
  })

  it('threatOnsetEdge 纯函数（只读 World；翻沿判定单一来源）', () => {
    const { world, enemy } = fixture()
    expect(threatOnsetEdge(false, world)).toBe(false)
    enemy.alive = true
    expect(threatOnsetEdge(false, world)).toBe(true)
    expect(threatOnsetEdge(true, world)).toBe(false)
    const before = JSON.stringify({ tanks: world.tanks.length, hp: world.player!.hp })
    threatOnsetEdge(false, world)
    expect(JSON.stringify({ tanks: world.tanks.length, hp: world.player!.hp })).toBe(before)
  })

  it('敌弹入带同样只算翻沿（与 danger-metrics 同一判定）', () => {
    const { world, enemy } = fixture()
    const p = world.player!
    const pcx = p.x + p.w / 2
    const pcy = p.y + p.h / 2
    const state = createDecisionGateState()
    const cfg = createDecisionGateConfig(true)
    const dues: number[] = []
    for (let t = 0; t < 30; t++) {
      enemy.alive = false
      world.bullets.length = 0
      // t=13 起：正上方 3 格的下行来弹（t=12 会撞最小间隔闸——10 后仅 2 tick）。
      if (t >= 13) {
        world.bullets.push(
          makeBullet({ x: pcx - BULLET / 2, y: pcy - BULLET / 2 - 3 * CELL, dir: 'down' }),
        )
      }
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    expect(dues).toEqual([0, 10, 13, 20])
    expect(decisionReadout(state).events).toBe(1)
  })
})

describe('decision-gate：最小间隔闸（Δt ≥ 3 恒成立）', () => {
  it('事件距上次决策 < 3 tick ⇒ 抑制，且沿已被消耗（不追发）', () => {
    const { world, enemy } = fixture()
    const state = createDecisionGateState()
    const cfg = createDecisionGateConfig(true)
    const dues: number[] = []
    for (let t = 0; t < 40; t++) {
      threat(world, enemy, t, 11, 40) // 11 入带，恰在 10 后 1 tick
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    // 11 被闸掉（gap=1 < 3），12/13… 是持续带 ⇒ 无新沿 ⇒ 直到 20 均匀才有决策。
    expect(dues).toEqual([0, 10, 20, 30])
    expect(decisionReadout(state).events).toBe(0)
  })

  it('gap 恰为 3 放行；事件后紧邻的均匀边界被跳过（唯一会改均匀子集的路径）', () => {
    const { world, enemy } = fixture()
    const state = createDecisionGateState()
    const cfg = createDecisionGateConfig(true)
    const dues: number[] = []
    for (let t = 0; t < 40; t++) {
      threat(world, enemy, t, 18, 19) // 18 入带（gap=8 ≥3），19 出带
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    // 0/10 均匀；18 事件；20 均匀但 gap=2 < 3 ⇒ 跳过；30 恢复均匀。
    expect(dues).toEqual([0, 10, 18, 30])
    const r = decisionReadout(state)
    expect(r.events).toBe(1)
    expect(r.minDt).toBe(8) // 18-10=8 / 30-18=12 / 10-0=10 ⇒ min=8
  })

  it('Δt ≥ minGap 恒成立（脚本化 400 tick 威胁序列）', () => {
    const { world, enemy } = fixture()
    const state = createDecisionGateState()
    const cfg = createDecisionGateConfig(true)
    const dues: number[] = []
    for (let t = 0; t < 400; t++) {
      // 确定性「闪烁」：每 7 tick 开 1 tick（制造大量翻沿 + 与 K 边界碰撞）
      enemy.alive = t % 7 === 3
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    expect(dues.length).toBeGreaterThan(40)
    for (let i = 1; i < dues.length; i++) {
      expect(dues[i] - dues[i - 1]).toBeGreaterThanOrEqual(DEFAULT_DECISION_MIN_GAP)
    }
    const r = decisionReadout(state)
    expect(r.minDt).toBeGreaterThanOrEqual(DEFAULT_DECISION_MIN_GAP)
    expect(r.n).toBe(dues.length)
    expect(r.events).toBeGreaterThan(0)
    expect(r.avgDt).toBeGreaterThan(0)
    expect(r.avgDt).toBeLessThanOrEqual(DEFAULT_DECISION_K)
  })
})

describe('decision-gate：state-init 交棒首段禁事件（R2.4 方案 i）', () => {
  it('抑制窗 = [0, 25)：窗内开始的威胁不追发（沿被消耗），窗后才算翻沿', () => {
    const { world, enemy } = fixture()
    const state = createDecisionGateState()
    state.suppressEventsUntilTick = 25 // 交棒 tick=15 + K=10 的等价形态
    const cfg = createDecisionGateConfig(true)
    const dues: number[] = []
    for (let t = 0; t < 60; t++) {
      threat(world, enemy, t, 5, 60) // 威胁从 5 起持续到局尾
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    // 5..24 的入带被抑制（prevThreat 照常更新 ⇒ 25 时 prevThreat 已 true）⇒ 一次事件都不发。
    expect(dues).toEqual([0, 10, 20, 30, 40, 50])
    expect(decisionReadout(state).events).toBe(0)
  })

  it('负向对照：窗内出带、窗后重新入带 ⇒ 窗后的新沿照常触发', () => {
    const { world, enemy } = fixture()
    const state = createDecisionGateState()
    state.suppressEventsUntilTick = 25
    const cfg = createDecisionGateConfig(true)
    const dues: number[] = []
    for (let t = 0; t < 60; t++) {
      threat(world, enemy, t, 5, 12) // 窗内入带→出带（被抑制）
      if (t >= 27 && t < 28) enemy.alive = true // 窗后新沿
      if (decisionDue(t, world, state, cfg)) dues.push(t)
    }
    // 0/10/20 均匀；27 是窗后新沿（gap=7）⇒ 事件步；30/40/50 均匀。
    expect(dues).toEqual([0, 10, 20, 27, 30, 40, 50])
    expect(decisionReadout(state).events).toBe(1)
  })
})

describe('decision-gate：读数与汇总', () => {
  it('markDecision：绕开 cadence 的决策也进同一本账（NNInput.reset 的 tick 0）', () => {
    const state = createDecisionGateState()
    markDecision(state, 0)
    markDecision(state, 12)
    const r = decisionReadout(state)
    expect(r.n).toBe(2)
    expect(r.avgDt).toBe(12)
    expect(r.minDt).toBe(12)
    expect(r.maxDt).toBe(12)
  })

  it('poolDecisionReadouts：n/events 求和、avgDt 按 gap 加权、min/max 取极值', () => {
    const pooled = poolDecisionReadouts([
      { n: 3, events: 1, avgDt: 10, minDt: 10, maxDt: 10 }, // gaps=2, dtSum=20
      { n: 2, events: 2, avgDt: 3, minDt: 3, maxDt: 3 }, //   gaps=1, dtSum=3
      { n: 1, events: 0, avgDt: 0, minDt: 0, maxDt: 0 }, //   gaps=0，不参与加权
    ])
    expect(pooled.n).toBe(6)
    expect(pooled.events).toBe(3)
    expect(pooled.avgDt).toBe(+((20 + 3) / 3).toFixed(3))
    expect(pooled.minDt).toBe(3)
    expect(pooled.maxDt).toBe(10)
    expect(poolDecisionReadouts([])).toEqual({ n: 0, events: 0, avgDt: 0, minDt: 0, maxDt: 0 })
  })
})
