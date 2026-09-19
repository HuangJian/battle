/** push-config.test.ts — Push 执行面解析：URL 归一化 + rl-config 回写（无真实 HTTP）。
 *
 *  **2026-09-15 策略反转（用户指令「就算云机连接不上，也不能直接开本地 worker，横幅报错就好」）**：
 *  endpoint 留空且 config 无可用 gpu_push 时一律**响亮报错**，**绝不自动回落本机 worker_server**
 *  —— 旧行为会把「云机连不上」伪装成「训练正常」。
 *
 *  **2026-09-19：本机伪节点彻底退出控制台**（用户指令「workerServe 伪节点直接从 dashboard
 *  去掉，它只是用于 trainingloop 冒烟测试」）：执行面只剩两档（用户填的 endpoint / 复用云
 *  节点），`allowLocal` opt-in 与写入器（`applyLocalPushNodeConfig` / `localPushUrl`）一并删除；
 *  留下来的只有 `local_push` **遗留标记**——复用扫描一律排除它，卡片徽章则如实把它说成本机。
 */
import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { registryTriples, COURSE_COMPONENTS } from '../src/core/registry'
import { ALL_COMPONENTS } from '../src/server/api/component-meta'
import {
  applyPushNodeConfig,
  configurePushEndpoint,
  enabledGpuPushNodes,
  findHealthyGpuPushNode,
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

// ────── 2026-09-19：本机伪节点退出控制台，只剩 `local_push` **遗留标记**的读面识别 ──────

/** 造一个历史遗留的本机伪节点条目（`local_push`；旧回落在 2026-09-15 写下过它）。 */
function legacyLocalNode(url: string, token = 'hub-tok') {
  return {
    id: 'local-push',
    url,
    authKey: token,
    concurrency: 1,
    enabled: true,
    gpu_push: true,
    local_push: true,
  }
}

describe('本机伪节点退出后：没有写入口，只有识别面', () => {
  it('push-config 不再导出「把执行面改指本机」的写入口（防回流）', () => {
    const src = readFileSync(path.join(DASHBOARD_ROOT, 'src', 'stack', 'push-config.ts'), 'utf-8')
    // 为什么必须没有：伪节点不是受管组件了（没有 spec / 账本键 / 卡片），留着写入口就是一条
    // 把训练指向**无人服务的本机地址**的路径——而表面看起来「训练正常」。
    expect(src).not.toMatch(/export function (applyLocalPushNodeConfig|localPushUrl)\b/)
    expect(src).not.toMatch(/opts:\s*\{\s*allowLocal/)
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

  it('复用扫描一律排除 local_push 节点（哪怕它 ping 得通）', async () => {
    // 起一个真会应答 /ping 的本地服务，冒充"活着的本机 worker_server"——
    // 只有它能 ping 通，所以"返回 null"只可能来自过滤，而不是 ping 失败。
    const srv = Bun.serve({ port: 0, fetch: () => new Response('{"ok":true}', { status: 200 }) })
    try {
      const url = `http://127.0.0.1:${srv.port}`
      const cfg: RlConfig = {
        ...baseCfg(),
        nodes: [...baseCfg().nodes, legacyLocalNode(url, 'tok')],
      }
      // 本机节点不参与复用扫描（哪怕它 ping 得通）——没有 opt-in 这回事了。
      expect(await findHealthyGpuPushNode(cfg)).toBeNull()
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

  it('指向遗留 local_push 条目 → kind=local（必须说出来：控制台不再提供该执行面）', () => {
    const cfg = baseCfg()
    cfg.nodes = [...cfg.nodes, legacyLocalNode('http://127.0.0.1:8791')]
    cfg.courses!['x2-start'] = { push_node_url: 'http://127.0.0.1:8791' }
    const t = pushTargetFromConfig(cfg, 'x2-start')
    expect(t?.kind).toBe('local')
    expect(t?.nodeId).toBe('local-push')
    expect(t?.authKey).toBe('hub-tok')
  })

  it('指向普通 gpu_push 节点 → kind=cloud（云机）', () => {
    const cfg = applyPushNodeConfig(baseCfg(), 'x2-start', 'https://gpu.example', 'tok-cloud')
    expect(pushTargetFromConfig(cfg, 'x2-start')?.kind).toBe('cloud')
  })

  it('遗留本机条目与云条目并存时按 `push_node_url` 认领：云 URL 写回后不误报本机', () => {
    let cfg = baseCfg()
    cfg.nodes = [...cfg.nodes, legacyLocalNode('http://127.0.0.1:8791')]
    cfg.courses!['x2-start'] = { push_node_url: 'http://127.0.0.1:8791' }
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

// ────────────────── 接线回归：受管组件与启动面不得再出现 workerServe ──────────────────

describe('本机伪节点不在启动面/受管面（防回流）', () => {
  const read = (rel: string): string =>
    readFileSync(path.join(DASHBOARD_ROOT, 'src', ...rel.split('/')), 'utf-8')

  it('受管组件全集（卡片/日志页/冒烟/停全部的数据源）里没有 workerServe', () => {
    expect(ALL_COMPONENTS).not.toContain('workerServe')
    expect(COURSE_COMPONENTS).not.toContain('workerServe')
    // 账本枚举路径也管不到它（旧账本里的 workerServes 表已无读者）
    expect(registryTriples({ workerServes: { x: { pid: 1 } } } as never).length).toBe(0)
  })

  it('启动面：start/preset 里没有它的分派分支与启动顺序', () => {
    expect(read('server/actions/start.ts')).not.toContain("case 'workerServe'")
    const preset = read('server/actions/preset.ts')
    expect(preset).not.toContain('workerServe')
    expect(preset).toContain("['selfNode', 'trainingLoop']") // push 预设只剩这两个
    expect(read('server/actions/smoke.ts')).not.toContain('workerServe')
  })

  it('spec 面：没有它的 ProcSpec（伪节点由冒烟预演自起自停）', () => {
    const specs = read('stack/specs.ts')
    expect(specs).not.toContain('workerServeSpec')
    expect(specs).not.toContain('WORKER_SERVE_ENTRY')
    // 预演侧确实自起自停（同一份远端入口）
    const push = read('stack/push.ts')
    expect(push).toContain("'remote_worker_serve'")
    expect(push).toContain('killPid')
  })
})
