import { describe, expect, it } from 'bun:test'
import {
  AUTO_RESTART_BUDGET,
  AUTO_RESTART_GRACE_MS,
  AUTO_RESTART_KEYS,
  attemptAutoRestart,
  decideAutoRestart,
  scheduleAutoRestart,
} from '../src/server/auto-restart'

/**
 * 共享基础设施组件的自动重启（2026-10-20 hub 崩溃事故的修法）。
 *
 * 事故：训练机提交限额被击穿（Windows Event 2004 @ 14:44:24）⇒ hub-server 被外部
 * 终止（无 traceback）⇒ 看护器只记不重启 ⇒ hub 停 1h29m（14:44:22 → 16:13:36）。
 *
 * 这里钉四件事：
 *   ① 只有**基础设施**组件会自动重启（trainingLoop 是课程状态，死了要人来判）；
 *   ② **操作员手动停过的不复活**（宽限后账本条目已被清 ⇒ 尊重操作员）；
 *   ③ 已经恢复应答的不抢（别人拉起来了）；
 *   ④ 崩溃循环有闸（窗口内重启次数上限）——超闸只报警。
 */

describe('自动重启策略 decideAutoRestart', () => {
  const base = {
    key: 'hubServer' as const,
    planned: null,
    entryRegistered: true,
    healthy: false,
    restartsInWindow: 0,
    inFlight: false,
  }

  it('基础设施组件意外退出 ⇒ 重启', () => {
    const d = decideAutoRestart(base)
    expect(d.restart).toBe(true)
    expect(d.reason).toContain('自动拉起')
  })

  it('课程状态组件（trainingLoop）不自动续跑', () => {
    const d = decideAutoRestart({ ...base, key: 'trainingLoop' })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('非基础设施')
  })

  it('设计内停车（门判决 / ALL DONE）不重启', () => {
    const d = decideAutoRestart({ ...base, planned: 'gate_verdict: 超时' })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('设计内停车')
  })

  it('账本条目已清（操作员停过）⇒ 不复活', () => {
    const d = decideAutoRestart({ ...base, entryRegistered: false })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('操作员')
  })

  it('已恢复应答 ⇒ 不抢', () => {
    const d = decideAutoRestart({ ...base, healthy: true })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('已恢复')
  })

  it('崩溃循环闸：窗口内超上限 ⇒ 只报警不拉起', () => {
    const d = decideAutoRestart({ ...base, restartsInWindow: AUTO_RESTART_BUDGET })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('频次闸')
  })

  it('在途重启不重复拉起', () => {
    const d = decideAutoRestart({ ...base, inFlight: true })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('在途')
  })
})

describe('自动重启执行 attemptAutoRestart', () => {
  it('意外退出 ⇒ 宽限后拉起一次', async () => {
    let slept = 0
    let restarts = 0
    const ok = await attemptAutoRestart('hubServer', {
      sleep: async (ms) => {
        slept += ms
      },
      entryRegistered: () => true,
      healthy: async () => false,
      restart: async () => {
        restarts++
      },
      warnFn: () => {},
    })
    expect(ok).toBe(true)
    expect(restarts).toBe(1)
    expect(slept).toBe(AUTO_RESTART_GRACE_MS)
  })

  it('操作员停过（条目已清）⇒ 不拉起', async () => {
    let restarts = 0
    const ok = await attemptAutoRestart('hubServer', {
      sleep: async () => {},
      entryRegistered: () => false,
      healthy: async () => false,
      restart: async () => {
        restarts++
      },
      warnFn: () => {},
    })
    expect(ok).toBe(false)
    expect(restarts).toBe(0)
  })

  it('已健康 ⇒ 不拉起', async () => {
    let restarts = 0
    const ok = await attemptAutoRestart('hubServer', {
      sleep: async () => {},
      entryRegistered: () => true,
      healthy: async () => true,
      restart: async () => {
        restarts++
      },
      warnFn: () => {},
    })
    expect(ok).toBe(false)
    expect(restarts).toBe(0)
  })

  it('启动抛错 ⇒ 不炸看护器（返回 false）', async () => {
    const ok = await attemptAutoRestart('hubServer', {
      sleep: async () => {},
      entryRegistered: () => true,
      healthy: async () => false,
      restart: async () => {
        throw new Error('端口被占')
      },
      warnFn: () => {},
    })
    expect(ok).toBe(false)
  })

  it('非纳管组件直接跳过', async () => {
    let restarts = 0
    const ok = await attemptAutoRestart('trainingLoop', {
      sleep: async () => {},
      entryRegistered: () => true,
      healthy: async () => false,
      restart: async () => {
        restarts++
      },
      warnFn: () => {},
    })
    expect(ok).toBe(false)
    expect(restarts).toBe(0)
  })
})

describe('自动重启触发面 scheduleAutoRestart', () => {
  it('纳管集合 = 基础设施组件（hub + 采样底线），不含课程状态', () => {
    expect(AUTO_RESTART_KEYS).toContain('hubServer')
    expect(AUTO_RESTART_KEYS).toContain('selfNode')
    expect(AUTO_RESTART_KEYS).not.toContain('trainingLoop')
  })

  it('设计内停车不排重启（返回即止，无副作用）', () => {
    expect(() => scheduleAutoRestart('hubServer', 'gate_verdict: x')).not.toThrow()
    expect(() => scheduleAutoRestart('trainingLoop', null)).not.toThrow()
  })
})
