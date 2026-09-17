/**
 * server-api-route.test.ts — 未知动作 404 / 参数错误 400 / busy 互斥 409 / 节点启停与并发回写 / setMode 持久化
 *
 * 分层：src/server/api/route.ts（routeAction）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { actions, loadConfig, post, postJson } from './helpers/console-fixture'
import { configPath } from '../src/core/paths'
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'

describe('console/api.routeAction', () => {
  it('未知动作 → null（路由层 404）', async () => {
    expect(await post('noSuchAction', {})).toBeNull()
  })

  it('未知组件 → 400', async () => {
    const r = (await postJson('start', { component: 'nope' })) as { __status: number }
    expect(r.__status).toBe(400)
  })

  it('preset 隧道选项白名单：非法 cfProtocol/cfEdgeIp → 400（M1）', async () => {
    const a = (await postJson('preset', { mode: 'pull', cfProtocol: 'bogus' })) as {
      __status: number
    }
    expect(a.__status).toBe(400)
    const b = (await postJson('preset', { mode: 'push', cfEdgeIp: '5' })) as { __status: number }
    expect(b.__status).toBe(400)
  })

  it('preset 瘦身开关白名单：非法 slim → 400（M2；只接受 on|off）', async () => {
    const r = (await postJson('preset', { mode: 'local', slim: '0' })) as { __status: number }
    expect(r.__status).toBe(400)
    const r2 = (await postJson('preset', { mode: 'local', slim: 'true' })) as { __status: number }
    expect(r2.__status).toBe(400)
  })

  it('preset rollout 位置白名单：非法 rolloutSrc → 400（M3；只接受 auto|local|node）', async () => {
    // 与 python `--rollout-src choices=("auto","local","node")` 同域——
    // 尤其不许放行 on/off 这类「另一个开关的域」，否则训练侧 choices 直接报错退出。
    for (const bad of ['on', 'off', 'cloud', '1']) {
      const r = (await postJson('preset', { mode: 'local', rolloutSrc: bad })) as {
        __status: number
      }
      expect(`${bad}:${r.__status}`).toBe(`${bad}:400`)
    }
  })

  it('节点并发越界 → 动作失败且不写盘', async () => {
    const before = readFileSync(configPath(), 'utf-8')
    const r = await postJson('setNodeConcurrency', { id: 'self', concurrency: 999 })
    expect(r.ok).toBe(false)
    expect(readFileSync(configPath(), 'utf-8')).toBe(before)
  })

  it('节点不存在 → 409 ActionError', async () => {
    const r = (await postJson('setNodeEnabled', { id: 'nosuch-node', enabled: true })) as {
      __status: number
    }
    expect(r.__status).toBe(409)
  })

  it('节点并发回写 rl-config.json（写后还原）', async () => {
    const cfg = loadConfig()
    // 必须挑**有 concurrency 的** enabled 节点：gpu_push 节点线上就没有该字段
    // （不参与并发配额），拿它算 orig + 1 会得 NaN（2026-09-15）。
    const target = cfg.nodes.find((n) => n.enabled && typeof n.concurrency === 'number')!
    expect(target).toBeTruthy()
    const orig = target.concurrency
    const r = await postJson('setNodeConcurrency', { id: target.id, concurrency: orig + 1 })
    expect(r.ok).toBe(true)
    const after = loadConfig()
    expect(after.nodes.find((n) => n.id === target.id)!.concurrency).toBe(orig + 1)
    // 还原
    const r2 = await postJson('setNodeConcurrency', { id: target.id, concurrency: orig })
    expect(r2.ok).toBe(true)
    expect(loadConfig().nodes.find((n) => n.id === target.id)!.concurrency).toBe(orig)
  })

  it('节点启停回写 rl-config.json（写后还原）', async () => {
    const cfg = loadConfig()
    const target = cfg.nodes.find((n) => n.enabled)!
    const orig = target.enabled
    const r = await postJson('setNodeEnabled', { id: target.id, enabled: !orig })
    expect(r.ok).toBe(true)
    expect(loadConfig().nodes.find((n) => n.id === target.id)!.enabled).toBe(!orig)
    await postJson('setNodeEnabled', { id: target.id, enabled: orig })
    expect(loadConfig().nodes.find((n) => n.id === target.id)!.enabled).toBe(orig)
  })

  it('模式开关回写 rl-config.json 的 rl 键（写后还原）', async () => {
    const before = loadConfig().rl.stream as number | undefined
    const flip = Number(before ?? 0) === 1 ? 0 : 1
    const r = await postJson('setMode', { key: 'rl.stream', value: String(flip) })
    expect(r.ok).toBe(true)
    expect(loadConfig().rl.stream).toBe(flip)
    await postJson('setMode', { key: 'rl.stream', value: String(before ?? 0) })
    expect(loadConfig().rl.stream).toBe(before ?? 0)
  })

  it('非法模式值 → 动作失败', async () => {
    const r = await postJson('setMode', { key: 'rl.stream', value: 'yes' })
    expect(r.ok).toBe(false)
  })

  it('trainer 模式切换持久化到 console-state（前后还原）', async () => {
    const before = actions.loadConsoleState().trainerPpo
    const next = before === 'pull' ? 'local' : 'pull'
    const r = await postJson('setMode', { key: 'trainer.ppo', value: next })
    expect(r.ok).toBe(true)
    expect(actions.loadConsoleState().trainerPpo).toBe(next)
    await postJson('setMode', { key: 'trainer.ppo', value: before })
    expect(actions.loadConsoleState().trainerPpo).toBe(before)
  })

  it('busy 互斥：同 key 第二次调用 409', async () => {
    actions.busy.add('node:self')
    try {
      const r = (await postJson('setNodeEnabled', { id: 'self', enabled: true })) as {
        __status: number
      }
      expect(r.__status).toBe(409)
    } finally {
      actions.busy.delete('node:self')
    }
  })

  it('setCourse 持久化；非法课程 409（不 process.exit）', async () => {
    const r = (await postJson('setCourse', { course: 'no-such-course-xyz' })) as {
      __status: number
      ok: boolean
    }
    expect(r.__status).toBe(409)
    expect(r.ok).toBe(false)
  })
})
