/** push-config.test.ts — Push 执行面解析：URL 归一化 + rl-config 回写（无真实 HTTP）。
 *
 *  **2026-09-15 策略反转（用户指令「就算云机连接不上，也不能直接开本地 worker，横幅报错就好」）**：
 *  endpoint 留空且 config 无可用 gpu_push 时一律**响亮报错**，**绝不自动回落本机 worker_server**
 *  —— 旧行为会把「云机连不上」伪装成「训练正常」。本机回落只剩显式 opt-in（`allowLocal:true`），
 *  且复用扫描默认**排除** `local_push` 节点（残留的 enabled 条目不得被静默复用，那是同一缺陷
 *  的第二入口）。显式 opt-in 路径下两个节点 kinds 必须共存互不覆盖，回写要保证 python 侧
 *  `_gpu_push_nodes` 按 `courses.<课>.push_node_url` 过滤后**只有**本机 worker_server。
 */
import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { slotPort } from '../src/core/slots'
import {
  applyLocalPushNodeConfig,
  applyPushNodeConfig,
  configurePushEndpoint,
  enabledGpuPushNodes,
  findHealthyGpuPushNode,
  localPushUrl,
  normalizePushUrl,
  pushTargetFromConfig,
} from '../src/stack/push-config'
import type { RlConfig } from '../src/core/types'

const SCRATCH: string[] = []
afterAll(() => {
  for (const d of SCRATCH) {
    try {
      rmSync(d, { recursive: true, force: true })
    } catch {
      /* noop */
    }
  }
})

/** 在临时 rl-config 上跑一段（不碰线上配置）。 */
async function withScratchConfig<T>(cfg: RlConfig, fn: () => T | Promise<T>): Promise<T> {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-pushcfg-'))
  SCRATCH.push(dir)
  const file = path.join(dir, 'rl-config.json')
  writeFileSync(file, JSON.stringify(cfg, null, 2), 'utf-8')
  const prev = process.env.BCITY_RL_CONFIG
  process.env.BCITY_RL_CONFIG = file
  try {
    return await fn()
  } finally {
    if (prev === undefined) delete process.env.BCITY_RL_CONFIG
    else process.env.BCITY_RL_CONFIG = prev
  }
}

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

// ────────────── 2026-09-15：本机 worker_server 作为 push 执行面 ──────────────

describe('本机 worker_server 回落（一键本机 push）', () => {
  it('applyLocalPushNodeConfig：写 local_push 节点 + 课程 push_node_url，认证键 = rl.remote_token', () => {
    const cfg = baseCfg()
    const url = `http://127.0.0.1:${slotPort(cfg, 'x2-start', 'push')}`
    const out = applyLocalPushNodeConfig(cfg, 'x2-start', url, cfg.rl.remote_token)
    const node = out.nodes.find((n) => n.local_push)
    expect(node?.url).toBe(url)
    expect(node?.gpu_push).toBe(true) // python `_gpu_push_nodes` 只认 gpu_push
    expect(node?.enabled).toBe(true)
    expect(node?.authKey).toBe('hub-tok') // 与 worker_server --token 同源
    expect(out.courses?.['x2-start']?.push_node_url).toBe(url)
    // 幂等：同一条目更新，不堆节点
    const again = applyLocalPushNodeConfig(out, 'x2-start', url, cfg.rl.remote_token)
    expect(again.nodes.filter((n) => n.local_push).length).toBe(1)
  })

  it('两种执行面共存互不覆盖：写云节点不吃本机节点，回落不吃云节点', () => {
    let cfg = applyPushNodeConfig(baseCfg(), 'x2-start', 'https://gpu.example', 'tok-cloud')
    cfg = applyLocalPushNodeConfig(
      cfg,
      'x2-start',
      localPushUrl(cfg, 'x2-start'),
      cfg.rl.remote_token,
    )
    expect(cfg.nodes.filter((n) => n.gpu_push).length).toBe(2)
    // 再写云 URL：仍只有那一个云节点（原地更新），本机节点原样
    const localBefore = cfg.nodes.find((n) => n.local_push)
    cfg = applyPushNodeConfig(cfg, 'x2-start', 'https://gpu2.example', 'tok-cloud2')
    const clouds = cfg.nodes.filter((n) => n.gpu_push && !n.local_push)
    expect(clouds.length).toBe(1)
    expect(clouds[0]!.url).toBe('https://gpu2.example')
    expect(cfg.nodes.find((n) => n.local_push)).toEqual(localBefore)
  })

  it('localPushUrl = 该课槽位的 push 端口（与 workerServeSpec 同源）', () => {
    const cfg = baseCfg()
    expect(localPushUrl(cfg, 'x2-start')).toBe(
      `http://127.0.0.1:${slotPort(cfg, 'x2-start', 'push')}`,
    )
  })

  it('缺可用 gpu_push → 默认**响亮报错**，绝不自动回落本机（2026-09-15 用户指令）', async () => {
    await withScratchConfig(baseCfg(), async () => {
      await expect(configurePushEndpoint('x2-start', '', '')).rejects.toThrow(/Push 执行面不可用/)
      // 且**不得写盘**：课程 push_node_url 不能被悄悄改指本机（那会把「云机连不上」
      // 伪装成「训练正常」——本测试就是钉死这条）
      const onDisk = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as RlConfig
      expect(onDisk.courses?.['x2-start']?.push_node_url ?? '').toBe('')
      expect(onDisk.nodes.some((n) => n.local_push)).toBe(false)
    })
  })

  it('allowLocal:true（显式 opt-in）→ source=local 且写盘可被 python 过滤命中', async () => {
    const base = baseCfg()
    await withScratchConfig(base, async () => {
      const t = await configurePushEndpoint('x2-start', '', '', { allowLocal: true })
      expect(t.source).toBe('local')
      expect(t.viaLocalWorker).toBe(true)
      // 盘上：local_push 节点 + 课程 push_node_url 指向本机，且两者逐字一致
      // （python `_gpu_push_nodes` 按该键过滤 URL，不一致则匹配 0 个 → job 回落 pull 卡死）
      const onDisk = JSON.parse(readFileSync(process.env.BCITY_RL_CONFIG!, 'utf-8')) as RlConfig
      const node = onDisk.nodes.find((n) => n.local_push)
      expect(node?.url).toBe(t.url)
      expect(onDisk.courses?.['x2-start']?.push_node_url).toBe(t.url)
      expect(t.url).toBe(localPushUrl(onDisk, 'x2-start'))
      // 过滤语义：只剩本机一条（不会静默串到云节点）
      const matched = onDisk.nodes.filter(
        (n) => n.gpu_push && n.enabled !== false && n.url.replace(/\/+$/, '') === t.url,
      )
      expect(matched.length).toBe(1)
    })
  })

  it('allowLocal:false 与默认等价 → 同样响亮报错（两条都不静默改执行面）', async () => {
    await withScratchConfig(baseCfg(), async () => {
      await expect(
        configurePushEndpoint('x2-start', '', '', { allowLocal: false }),
      ).rejects.toThrow()
    })
  })

  it('复用扫描默认排除 local_push 节点（残留的 enabled 本机条目不得被静默复用）', async () => {
    // 起一个真会应答 /ping 的本地服务，冒充"活着的本机 worker_server"——
    // 只有它能 ping 通，所以"返回 null"只可能来自过滤，而不是 ping 失败。
    const srv = Bun.serve({ port: 0, fetch: () => new Response('{"ok":true}', { status: 200 }) })
    try {
      const url = `http://127.0.0.1:${srv.port}`
      const cfg: RlConfig = {
        ...baseCfg(),
        nodes: [
          ...baseCfg().nodes,
          {
            id: 'local-push',
            url,
            authKey: 'tok',
            concurrency: 1,
            gpu_push: true,
            local_push: true,
            enabled: true,
          },
        ],
      }
      // 默认：本机节点不参与复用扫描（哪怕它 ping 得通）
      expect(await findHealthyGpuPushNode(cfg)).toBeNull()
      // 显式 opt-in：才纳入
      expect((await findHealthyGpuPushNode(cfg, 5000, { includeLocal: true }))?.id).toBe(
        'local-push',
      )
      // 端到端：endpoint 留空 + 只有本机条目活着 → 仍然响亮报错，不复用本机
      await withScratchConfig(cfg, async () => {
        await expect(configurePushEndpoint('x2-start', '', '')).rejects.toThrow(/Push 执行面不可用/)
      })
    } finally {
      srv.stop(true)
    }
  })
})

// ────────────── 控制台展示：本课 push 执行面指向（2026-09-15） ──────────────

describe('pushTargetFromConfig（卡片徽章的纯数据源）', () => {
  it('未配置 push 目标 → null（非 push 场景不出徽章）', () => {
    expect(pushTargetFromConfig(baseCfg(), 'x2-start')).toBeNull()
  })

  it('指向 local_push 节点 → kind=local（本机 worker_server）+ 认领节点 id/authKey', () => {
    const cfg = applyLocalPushNodeConfig(
      baseCfg(),
      'x2-start',
      localPushUrl(baseCfg(), 'x2-start'),
      'hub-tok',
    )
    const t = pushTargetFromConfig(cfg, 'x2-start')
    expect(t?.kind).toBe('local')
    expect(t?.nodeId).toBe('local-push')
    expect(t?.authKey).toBe('hub-tok')
  })

  it('指向普通 gpu_push 节点 → kind=cloud（云机）', () => {
    const cfg = applyPushNodeConfig(baseCfg(), 'x2-start', 'https://gpu.example', 'tok-cloud')
    expect(pushTargetFromConfig(cfg, 'x2-start')?.kind).toBe('cloud')
  })

  it('两种执行面共存时按 `push_node_url` 认领：云 URL 写回后不误报本机', () => {
    let cfg = applyLocalPushNodeConfig(
      baseCfg(),
      'x2-start',
      localPushUrl(baseCfg(), 'x2-start'),
      'hub-tok',
    )
    const local = pushTargetFromConfig(cfg, 'x2-start')
    expect(local?.kind).toBe('local')
    cfg = applyPushNodeConfig(cfg, 'x2-start', 'https://gpu.example', 'tok-cloud')
    const cloud = pushTargetFromConfig(cfg, 'x2-start')
    expect(cloud?.kind).toBe('cloud')
    expect(cloud?.nodeId).toBe('gpu-push')
    // 尾斜杠/大小写不敏感地认领（写盘 URL 可能带尾斜杠）
    cfg.courses!['x2-start'] = { push_node_url: 'https://gpu.example/' }
    expect(pushTargetFromConfig(cfg, 'x2-start')?.kind).toBe('cloud')
  })

  it('指向 config 里不存在的节点 → unresolved（python 会回落 pull，必须显式暴露）', () => {
    const cfg = baseCfg()
    cfg.courses!['x2-start'] = { push_node_url: 'https://ghost.example' }
    const t = pushTargetFromConfig(cfg, 'x2-start')
    expect(t?.kind).toBe('unresolved')
    expect(t?.nodeId).toBeNull()
    expect(t?.authKey).toBe('hub-tok') // 无节点时回退 rl.remote_token（仍可探 /ping）
  })
})

// ────────────────────────── 预设接线（防「只改 helper 忘接线」） ──────────────────────────

describe('push 预设接线：回落本机时把 workerServe 排进顺序', () => {
  it('preset.ts 读 viaLocalWorker 并把 workerServe 入 order', () => {
    const src = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'actions', 'preset.ts'),
      'utf-8',
    )
    expect(src).toContain('viaLocalWorker')
    expect(src).toContain("['selfNode', 'workerServe', 'trainingLoop']")
  })

  it('workerServe 启动幂等：已在运行/已在服务 → 不 kill 不重起', () => {
    const src = readFileSync(
      path.join(DASHBOARD_ROOT, 'src', 'server', 'actions', 'start.ts'),
      'utf-8',
    )
    const block = src.slice(src.indexOf("case 'workerServe'"), src.indexOf("case 'trainingLoop'"))
    expect(block).toContain('已在运行')
    expect(block).toContain('/ping')
    expect(block).not.toContain('killPid')
  })
})
