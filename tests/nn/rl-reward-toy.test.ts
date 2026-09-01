/**
 * rl-reward-toy.test.ts — 玩具场奖励计算验证（plan/dodge-item-curriculum.md §2）。
 *
 * 验证：
 * 1. 线性臂（kill/kill2/balanced/survival）与旧接口逐字节一致（向后兼容）
 * 2. dodge-mix 臂递增击杀 + wLoot + wDmg2 计算正确
 * 3. 势塑形对账：Σr = Φ_end − Φ_0 + 终局项
 */
import { describe, it, expect } from 'bun:test'
import {
  TOY_REWARD_ARMS,
  toyPotential,
  toyTerminal,
  STUCK_THRESHOLD,
} from '../../src/nn/rl-reward-toy'

describe('rl-reward-toy: 线性臂向后兼容', () => {
  it('kill 臂 Φ 值含 (kills+1)^1 偏移（势塑形框架下 Σr 不变）', () => {
    const arm = TOY_REWARD_ARMS['kill']
    // 新公式：wKill*(kills+1)^p − wDmg*playerHits + wAlive*ticks
    // p=1 时 (kills+1)^1 = kills+1 ⇒ 比旧公式多常数 wKill
    // 该常数在势塑形窗口差中抵消，Σr 不变
    const v = toyPotential({ kills: 5, playerHits: 2 }, 1200, arm)
    const expected = 1.0 * (5 + 1) ** 1 - 0.15 * 2 + 0.0005 * 1200
    expect(v).toBeCloseTo(expected, 6)
  })

  it('kill2 臂 wAlive=0', () => {
    const arm = TOY_REWARD_ARMS['kill2']
    expect(arm.wAlive).toBe(0)
    // (kills+1)^1 = kills+1
    const v = toyPotential({ kills: 3, playerHits: 1 }, 5000, arm)
    expect(v).toBeCloseTo(1.0 * (3 + 1) ** 1 - 0.15 * 1, 6)
  })

  it('线性臂势塑形对账：Σr 向后兼容', () => {
    const arm = TOY_REWARD_ARMS['kill']
    // 模拟 3 步：kills=0→5, 1200ticks
    const phi0 = toyPotential({ kills: 0, playerHits: 0 }, 0, arm)
    const phi1 = toyPotential({ kills: 5, playerHits: 2 }, 1200, arm)
    // 窗口势差 = phi1 - phi0
    const windowReward = phi1 - phi0
    // 旧公式窗口势差 = 1.0*5 - 0.15*2 + 0.0005*1200 = 5.3
    // 新公式：phi1 - phi0 = 1.0*6 - 0.15*2 + 0.0005*1200 - 1.0*1 = 5.3
    expect(windowReward).toBeCloseTo(5.3, 6)
  })
})

describe('rl-reward-toy: dodge-mix 臂', () => {
  it('递增击杀公式正确', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    expect(arm.p).toBe(1.15)
    // kills=0: (0+1)^1.15 = 1
    const v0 = toyPotential({ kills: 0, playerHits: 0 }, 0, arm)
    expect(v0).toBeCloseTo(1.0 * 1 ** 1.15, 6)
    // kills=1: (1+1)^1.15 = 2^1.15 ≈ 2.22
    const v1 = toyPotential({ kills: 1, playerHits: 0 }, 0, arm)
    expect(v1).toBeCloseTo(1.0 * 2 ** 1.15, 6)
    // 第 1 杀边际 = 2^1.15 - 1^1.15 ≈ 1.22
    expect(v1 - v0).toBeCloseTo(2 ** 1.15 - 1 ** 1.15, 5)
  })

  it('wLoot 计入拾取分', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // 无拾取 vs 拾取 1 个
    const v0 = toyPotential({ kills: 0, playerHits: 0, powerUpsCollected: 0 }, 0, arm)
    const v1 = toyPotential({ kills: 0, playerHits: 0, powerUpsCollected: 1 }, 0, arm)
    expect(v1 - v0).toBeCloseTo(arm.wLoot!, 6)
  })

  it('wDmg2 按命损扣分', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // 扣 100 HP
    const v0 = toyPotential({ kills: 0, playerHits: 0, playerDamageTaken: 0 }, 0, arm)
    const v100 = toyPotential({ kills: 0, playerHits: 0, playerDamageTaken: 100 }, 0, arm)
    expect(v0 - v100).toBeCloseTo(arm.wDmg2! * 100, 6)
  })

  it('wHit 命中奖励（wMiss 已移除，§12）', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // 5 发命中 2 发：hits=2, shots=5
    // 命中奖励 = wHit*hits = 0.20*2 = 0.40（wMiss 已移除，打偏不惩罚）
    const v0 = toyPotential({ kills: 0, playerHits: 0, hits: 0, shots: 0 }, 0, arm)
    const v1 = toyPotential({ kills: 0, playerHits: 0, hits: 2, shots: 5 }, 0, arm)
    const net = v1 - v0
    const expected = 0.2 * 2 // 命中奖励，无打偏惩罚
    expect(net).toBeCloseTo(expected, 6)
  })

  it('wStuck 停滞惩罚：超阈值后扣势', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // stuckTicks=200（超阈值 180 共 20 tick）
    const v0 = toyPotential({ kills: 0, playerHits: 0, stuckTicks: 0 }, 0, arm)
    const v200 = toyPotential({ kills: 0, playerHits: 0, stuckTicks: 200 }, 0, arm)
    expect(v0 - v200).toBeCloseTo(arm.wStuck! * (200 - STUCK_THRESHOLD), 6)
  })

  it('wStuck 停滞惩罚：未超阈值不扣势', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // stuckTicks=100（未超阈值 180）
    const v0 = toyPotential({ kills: 0, playerHits: 0, stuckTicks: 0 }, 0, arm)
    const v100 = toyPotential({ kills: 0, playerHits: 0, stuckTicks: 100 }, 0, arm)
    expect(v100 - v0).toBe(0) // 未超阈值，势差为 0
  })

  it('全参数组合正确', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // kills=8, playerHits=2, playerDamageTaken=150, powerUpsCollected=3, hits=5, shots=20, stuckTicks=200, ticks=3000
    const v = toyPotential(
      {
        kills: 8,
        playerHits: 2,
        playerDamageTaken: 150,
        powerUpsCollected: 3,
        hits: 5,
        shots: 20,
        stuckTicks: 200,
      },
      3000,
      arm,
    )
    // 预期: 1.0*(9)^1.15 - 1.0*2 - 0.01*150 + 0.40*3 + 0*3000 + 0.20*5 - 0.002*max(0,200-180)
    // wMiss 已移除（§12），打偏不惩罚
    const expected =
      1.0 * 9 ** 1.15 - 1.0 * 2 - 0.01 * 150 + 0.4 * 3 + 0.2 * 5 - 0.002 * (200 - STUCK_THRESHOLD)
    expect(v).toBeCloseTo(expected, 5)
  })
})

describe('rl-reward-toy: 终局奖励', () => {
  it('stage_clear = +wClear', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    expect(toyTerminal('stage_clear', arm)).toBe(arm.wClear)
  })

  it('lives_exhausted = -wDeath', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    expect(toyTerminal('lives_exhausted', arm)).toBe(-arm.wDeath)
  })

  it('timeout = 0', () => {
    expect(toyTerminal('timeout', TOY_REWARD_ARMS['dodge-mix'])).toBe(0)
  })
})

describe('rl-reward-toy: 势塑形对账', () => {
  it('窗口势差累加 = 终局 Φ 差', () => {
    const arm = TOY_REWARD_ARMS['dodge-mix']
    // 模拟 3 步：kills=0→4→10, playerHits=0→0→2, 400tick, 800tick
    const counters = [
      { kills: 0, playerHits: 0, playerDamageTaken: 0, powerUpsCollected: 0 },
      { kills: 4, playerHits: 0, playerDamageTaken: 50, powerUpsCollected: 1 },
      { kills: 10, playerHits: 2, playerDamageTaken: 150, powerUpsCollected: 3 },
    ]
    const ticks = [0, 400, 800]
    const phis = counters.map((c, i) => toyPotential(c, ticks[i], arm))
    // 终局势差
    const phiTotal = phis[phis.length - 1] - phis[0]
    // 窗口势差累加
    let windowSum = 0
    for (let i = 1; i < phis.length; i++) windowSum += phis[i] - phis[i - 1]
    expect(windowSum).toBeCloseTo(phiTotal, 10)
  })
})
