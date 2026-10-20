import { describe, expect, it } from 'bun:test'
import {
  AUTO_RESTART_ATTEMPTS,
  AUTO_RESTART_BUDGET,
  AUTO_RESTART_GRACE_MS,
  AUTO_RESTART_KEYS,
  attemptAutoRestart,
  decideAutoRestart,
  scheduleAutoRestart,
} from '../src/server/auto-restart'

/**
 * 受管组件的自动重启（2026-10-20 hub 崩溃事故的修法）。
 *
 * 事故：训练机提交限额被击穿（Windows Event 2004 @ 14:44:24）⇒ hub-server 被外部
 * 终止（无 traceback）⇒ 看护器只记不重启 ⇒ hub 停 1h29m（14:44:22 → 16:13:36）。
 *
 * 覆盖范围 = **全部受管组件**（用户口径：「trainingLoop 和 cloudflared 也需要自动
 * 重启」）——关键不在「哪些组件」，而在**退出性质**：设计内停车（门判决 / ALL DONE）
 * 不重启，意外退出才重启。
 *
 * 这里钉五件事：
 *   ① 全部受管组件都纳管（含 trainingLoop / cloudflared）；
 *   ② **设计内停车不重启**（判决不是崩溃；悄悄续跑会推翻人的判死决定）；
 *   ③ **操作员手动停过的不复活**（宽限后账本条目已被清 ⇒ 尊重操作员）；
 *   ④ 已经恢复应答的不抢（别人拉起来了）；
 *   ⑤ 崩溃循环有闸（窗口内重启次数上限）+ 瞬时失败有限重试。
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

  it('意外退出 ⇒ 重启', () => {
    const d = decideAutoRestart(base)
    expect(d.restart).toBe(true)
    expect(d.reason).toContain('自动拉起')
  })

  it('全部受管组件都纳管（含 trainingLoop / cloudflared / localWorker）', () => {
    for (const k of ['selfNode', 'hubServer', 'cloudflared', 'localWorker', 'trainingLoop']) {
      expect(AUTO_RESTART_KEYS).toContain(k as never)
      expect(decideAutoRestart({ ...base, key: k as never }).restart).toBe(true)
    }
  })

  it('未来新增的未纳管组件 ⇒ 不重启（fail-closed）', () => {
    const d = decideAutoRestart({ ...base, key: 'brandNew' as never })
    expect(d.restart).toBe(false)
    expect(d.reason).toContain('未纳入')
  })

  it('设计内停车（门判决 / ALL DONE）不重启——那是判决不是崩溃', () => {
    const d = decideAutoRestart({ ...base, key: 'trainingLoop', planned: 'gate_verdict: 超时' })
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

  it('瞬时失败会重试（第一次抛错、第二次成功）', async () => {
    let restarts = 0
    const ok = await attemptAutoRestart('cloudflared', {
      sleep: async () => {},
      entryRegistered: () => true,
      healthy: async () => false,
      restart: async () => {
        restarts++
        if (restarts === 1) throw new Error('端口未释放')
      },
      warnFn: () => {},
    })
    expect(ok).toBe(true)
    expect(restarts).toBe(2)
  })

  it('连续失败到上限 ⇒ 返回 false（不炸看护器）', async () => {
    let restarts = 0
    const ok = await attemptAutoRestart('hubServer', {
      sleep: async () => {},
      entryRegistered: () => true,
      healthy: async () => false,
      restart: async () => {
        restarts++
        throw new Error('启动即退出')
      },
      warnFn: () => {},
    })
    expect(ok).toBe(false)
    expect(restarts).toBe(AUTO_RESTART_ATTEMPTS)
  })

  it('重试期间被别人拉起 ⇒ 停手（不抢）', async () => {
    let restarts = 0
    let healthyCalls = 0
    const ok = await attemptAutoRestart('hubServer', {
      sleep: async () => {},
      entryRegistered: () => true,
      healthy: async () => {
        healthyCalls++
        return healthyCalls > 1 // 第一次探=false，第二次（重试前）变 true
      },
      restart: async () => {
        restarts++
        throw new Error('启动即退出')
      },
      warnFn: () => {},
    })
    expect(ok).toBe(true)
    expect(restarts).toBe(1)
  })

  it('非纳管组件直接跳过', async () => {
    let restarts = 0
    const ok = await attemptAutoRestart('brandNew' as never, {
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
  it('设计内停车不排重启（返回即止，无副作用）', () => {
    expect(() => scheduleAutoRestart('hubServer', 'gate_verdict: x')).not.toThrow()
    expect(() => scheduleAutoRestart('trainingLoop', null)).not.toThrow()
  })
})
