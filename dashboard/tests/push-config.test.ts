/** push-config.test.ts — Push 启动前置：URL 归一化 + rl-config 回写（无真实 HTTP）。 */
import { describe, expect, it } from 'bun:test'
import {
  applyPushNodeConfig,
  enabledGpuPushNodes,
  findHealthyGpuPushNode,
  normalizePushUrl,
} from '../src/stack/push-config'
import type { RlConfig } from '../src/core/types'

function baseCfg(): RlConfig {
  return {
    version: 1,
    nodes: [
      {
        id: 'self',
        url: 'http://127.0.0.1:8443',
        authKey: 'sampler',
        concurrency: 8,
        enabled: true,
      },
    ],
    rl: {
      hub_port: 8787,
      agent_port: 8443,
      remote_token: 'hub-tok',
    },
    courses: { 'x2-start': { slot: 1 } },
  }
}

describe('normalizePushUrl', () => {
  it('补 https、去尾斜杠', () => {
    expect(normalizePushUrl('abc.example.com/')).toBe('https://abc.example.com')
    expect(normalizePushUrl('http://127.0.0.1:8790///')).toBe('http://127.0.0.1:8790')
  })
  it('空/非法协议报错', () => {
    expect(() => normalizePushUrl('')).toThrow()
    expect(() => normalizePushUrl('ftp://x')).toThrow()
  })
})

describe('applyPushNodeConfig', () => {
  it('新增 gpu_push 节点 + 回写 courses.push_node_url', () => {
    const cfg = applyPushNodeConfig(baseCfg(), 'x2-start', 'https://gpu.example', 'tok-gpu')
    const push = cfg.nodes.find((n) => n.gpu_push)
    expect(push?.url).toBe('https://gpu.example')
    expect(push?.authKey).toBe('tok-gpu')
    expect(push?.enabled).toBe(true)
    expect(cfg.courses?.['x2-start']?.push_node_url).toBe('https://gpu.example')
    // sampler 节点不动
    expect(cfg.nodes.find((n) => n.id === 'self')?.gpu_push).toBeUndefined()
  })

  it('已有 gpu_push 节点则原地更新（多课 N:1）', () => {
    let cfg = applyPushNodeConfig(baseCfg(), 'a', 'https://gpu.example', 'tok1')
    cfg = applyPushNodeConfig(cfg, 'b', 'https://gpu.example', 'tok2')
    const pushes = cfg.nodes.filter((n) => n.gpu_push)
    expect(pushes.length).toBe(1)
    expect(pushes[0]!.authKey).toBe('tok2')
    expect(cfg.courses?.['a']?.push_node_url).toBe('https://gpu.example')
    expect(cfg.courses?.['b']?.push_node_url).toBe('https://gpu.example')
  })
})

describe('enabledGpuPushNodes / findHealthyGpuPushNode', () => {
  it('只取 enabled 的 gpu_push（enabled 缺省视为 true）', () => {
    const cfg = baseCfg()
    cfg.nodes.push(
      {
        id: 'g1',
        url: 'https://a',
        authKey: 'k',
        concurrency: 1,
        enabled: false,
        gpu_push: true,
      },
      { id: 'g2', url: 'https://b', authKey: 'k', concurrency: 1, enabled: true, gpu_push: true },
    )
    expect(enabledGpuPushNodes(cfg).map((n) => n.id)).toEqual(['g2'])
  })

  it('ping 全不通 → null（调用方再要求手填）', async () => {
    const cfg = baseCfg()
    cfg.nodes.push({
      id: 'g1',
      url: 'https://127.0.0.1:1',
      authKey: 'k',
      concurrency: 1,
      enabled: true,
      gpu_push: true,
    })
    expect(await findHealthyGpuPushNode(cfg, 200)).toBeNull()
  })
})
