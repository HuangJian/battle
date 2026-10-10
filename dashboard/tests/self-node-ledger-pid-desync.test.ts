/**
 * self-node-ledger-pid-desync.test.ts — **账本 pid 不得单独推出「已退出 / 未启动」**。
 *
 * 现场（2026-10-10，plan/self-node-ledger-pid-desync §0）：agent 11:47:31 由 `/v1/restart` 换代
 * （8340 → 21300，**触发者在控制台之外** —— trainer 的 M8 主动升级），控制台账本没跟上，于是
 *   ① 组件卡红点 `exited`；
 *   ② `/api/pool.selfStatus = null` ⇒ 节点统计页「agent 未启动」+ `SelfDiskBadge` 消失；
 * 而**同一页**的 `nodes.self.online=true` / ping 2ms / 达标 10/10 / `selfDisk.level=ok` 全是好的。
 * 一个读数坏掉，操作员看到的是「采集节点挂了」这个伪结论。
 *
 * 本文件钉四件事（语义规格 = plan §2）：
 *  · T1 `fetchSelfStatus`：账本 pid 死了也**照探**；且超时预算不因「账本陈旧」而变小（评审 F1）；
 *  · T2 `componentViews`：账本 pid 死 + 健康探到「有实例在服务」⇒ `running` / `healthy=true`（G2）；
 *  · T4 `stepSelfNode`：「已在运行」分支也要用**组件自报 pid** 对齐账本（G4）；
 *  · F3 钉子：`cloudflared` **不走**这条 —— 它的健康是 hub 派生的，不是存活判据。
 *
 * 纪律：纯函数 + 注入（不联网、不读写真账本）。账本经 `BCITY_REGISTRY_FILE` 重定向到临时目录
 * （registry.ts 惰性读 env，top-level 赋值即生效 —— 与 exit-watchdog.test.ts 同规）。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'

const REG_DIR = mkdtempSync(path.join(os.tmpdir(), 'bcity-ledger-pid-'))
process.env.BCITY_REGISTRY_FILE = path.join(REG_DIR, 'registry.json')
afterAll(() => {
  try {
    rmSync(REG_DIR, { recursive: true, force: true })
  } catch {
    /* noop */
  }
})

import { pidAlive } from '../src/core/net'
import type { Component, Registry, RegistryEntry, RlConfig } from '../src/core/types'
import { fetchSelfStatus } from '../src/server/api/pool'
import { componentViews, computeComponentHealth } from '../src/server/api/views'
import { stepSelfNode } from '../src/stack/hub'
import { SELF_NODE_ENTRY } from '../src/stack/specs'

/** 账本里那个**上一代进程**的 pid（现场值 8340 的同形）。 */
const STALE_PID = 999999
/** 自报 pid（现场值 21300 的同形）。 */
const LIVE_PID = 21300

const CFG = {
  version: 1,
  nodes: [
    {
      id: 'self',
      url: 'http://127.0.0.1:8443',
      authKey: 'self-key',
      concurrency: 4,
      enabled: true,
    },
  ],
  rl: { agent_port: 8443, remote_token: 'rt', local_slots: 0 },
} as unknown as RlConfig

function writeRegistry(reg: Registry): void {
  writeFileSync(process.env.BCITY_REGISTRY_FILE!, JSON.stringify(reg), 'utf-8')
}

/** `/v1/status` 的假应答（与 pool.ts 的解析面同形状）。 */
function statusResp(body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

describe('T1 fetchSelfStatus：账本 pid 不是判决（G1）', () => {
  it('前置：STALE_PID 确实是死 pid（否则这条用例什么都没测）', () => {
    expect(pidAlive(STALE_PID)).toBe(false)
  })

  it('账本 = 死 pid + /v1/status 200 ⇒ 仍返回对象（现场：账本 8340 / 真身 21300）', async () => {
    writeRegistry({ selfNode: { pid: STALE_PID, entry: SELF_NODE_ENTRY, startedAt: 1 } })
    const s = await fetchSelfStatus(CFG, {
      probe: async () => statusResp({ workers: 4, gamesDoneTotal: 187, inflight: [1, 2] }),
    })
    expect(s).not.toBeNull()
    expect(s!.workers).toBe(4)
    expect(s!.inflight).toBe(2)
    expect(s!.gamesDoneTotal).toBe(187)
  })

  it('账本**没有**条目也照探（「停止」之后就不该再假装没在服务）', async () => {
    writeRegistry({})
    const s = await fetchSelfStatus(CFG, { probe: async () => statusResp({ workers: 1 }) })
    expect(s).not.toBeNull()
    expect(s!.workers).toBe(1)
  })

  it('探针 1500ms 才应答 ⇒ 仍非 null（预算不给「账本陈旧」这一档更小的值，评审 F1）', async () => {
    writeRegistry({})
    const s = await fetchSelfStatus(CFG, {
      probe: async () => {
        await new Promise((r) => setTimeout(r, 1500))
        return statusResp({ workers: 2 })
      },
    })
    expect(s?.workers).toBe(2)
  })

  it('探针不通 ⇒ null（真死仍是 null，G5 不引入新假绿）', async () => {
    writeRegistry({ selfNode: { pid: STALE_PID } })
    const s = await fetchSelfStatus(CFG, {
      probe: async () => {
        throw new Error('connection refused')
      },
    })
    expect(s).toBeNull()
  })

  it('HTTP 非 200 ⇒ null', async () => {
    const s = await fetchSelfStatus(CFG, {
      probe: async () => new Response('nope', { status: 503 }),
    })
    expect(s).toBeNull()
  })

  it('返回体缺字段 ⇒ 归零而不是 NaN / 伪造', async () => {
    const s = await fetchSelfStatus(CFG, { probe: async () => statusResp({}) })
    expect(s).not.toBeNull()
    expect(s!.workers).toBe(0)
    expect(s!.inflight).toBe(0)
    expect(s!.diskFreeMB).toBeNull()
  })
})

describe('T2 componentViews：账本 pid 死 + 有实例在服务 ⇒ running（G2）', () => {
  it('健康探到 true ⇒ status=running、healthy=true（改动前报 exited）', async () => {
    writeRegistry({ selfNode: { pid: STALE_PID, entry: SELF_NODE_ENTRY } })
    const views = await componentViews(
      CFG,
      '',
      new Map<Component, boolean | null>([['selfNode', true]]),
    )
    const self = views.find((v) => v.key === 'selfNode')!
    expect(self.status).toBe('running')
    expect(self.healthy).toBe(true)
  })

  it('健康探到 false ⇒ 维持 exited + healthy=null（不引入新假绿，G5）', async () => {
    writeRegistry({ selfNode: { pid: STALE_PID, entry: SELF_NODE_ENTRY } })
    const views = await componentViews(CFG, '', new Map([['selfNode', false]]))
    const self = views.find((v) => v.key === 'selfNode')!
    expect(self.status).toBe('exited')
    expect(self.healthy).toBeNull()
  })

  it('健康表缺席该组件（探不动）⇒ 维持 exited（不猜）', async () => {
    writeRegistry({ selfNode: { pid: STALE_PID, entry: SELF_NODE_ENTRY } })
    const views = await componentViews(CFG, '', new Map<Component, boolean | null>())
    expect(views.find((v) => v.key === 'selfNode')!.status).toBe('exited')
  })

  it('无条目 ⇒ stopped（「有人悄悄死了」与「从没起过」仍是两件事，N3）', async () => {
    writeRegistry({})
    const views = await componentViews(CFG, '', new Map<Component, boolean | null>())
    expect(views.find((v) => v.key === 'selfNode')!.status).toBe('stopped')
  })
})

describe('F3 钉子：cloudflared 不走第二事实源（hub 派生的健康不是存活判据）', () => {
  it('隧道条目 pid 死 + hub 探通 ⇒ 不进健康表、视图仍 exited', async () => {
    writeRegistry({ cloudflareds: { '': { pid: STALE_PID, url: '' } } })
    // 探什么通什么：如果实现按「这个 key 有探测逻辑」把 cloudflared 也算进来，
    // cloudflaredHealthy(hubOk=true, tunnelOk=null) = true ⇒ 卡片会变 running（假绿）。
    const h = await computeComponentHealth(CFG, { probe: async () => true })
    expect(h.get('cloudflared')).toBeUndefined()
    const views = await componentViews(CFG, '', h)
    expect(views.find((v) => v.key === 'cloudflared')!.status).toBe('exited')
  })

  it('「有探针」= HEALTHY_PORTS 有条目：selfNode pid 死也照探（对照上一条）', async () => {
    writeRegistry({ selfNode: { pid: STALE_PID, entry: SELF_NODE_ENTRY } })
    const h = await computeComponentHealth(CFG, { probe: async () => true })
    expect(h.get('selfNode')).toBe(true)
  })
})

describe('T4 stepSelfNode：已在运行也要对齐账本（G4）', () => {
  it('健康早退 + 自报 pid ≠ 账本 ⇒ 写一次、pid = 自报、其余字段原样带过', async () => {
    const saved: RegistryEntry[] = []
    const reg: Registry = { selfNode: { pid: STALE_PID, entry: SELF_NODE_ENTRY, startedAt: 42 } }
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => LIVE_PID,
      loadReg: () => reg,
      save: (e) => saved.push(e),
    })
    expect(saved).toHaveLength(1)
    expect(saved[0]!.pid).toBe(LIVE_PID)
    expect(saved[0]!.entry).toBe(SELF_NODE_ENTRY)
    expect(saved[0]!.startedAt).toBe(42)
  })

  it('已对齐 ⇒ 一个字都不写（稳态不写盘）', async () => {
    const saved: RegistryEntry[] = []
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => LIVE_PID,
      loadReg: () => ({ selfNode: { pid: LIVE_PID, entry: SELF_NODE_ENTRY } }),
      save: (e) => saved.push(e),
    })
    expect(saved).toHaveLength(0)
  })

  it('「意外退出」标记不被这条路径吃掉（归看护器的自愈路径）', async () => {
    const saved: RegistryEntry[] = []
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => LIVE_PID,
      loadReg: () => ({
        selfNode: { pid: STALE_PID, error: '意外退出 (PID 999999)', exitAt: 'T0' },
      }),
      save: (e) => saved.push(e),
    })
    expect(saved[0]!.error).toBe('意外退出 (PID 999999)')
    expect(saved[0]!.exitAt).toBe('T0')
  })

  it('自报 pid 拿不到 ⇒ 不写（没证据就不动账本）', async () => {
    const saved: RegistryEntry[] = []
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => null,
      loadReg: () => ({ selfNode: { pid: STALE_PID } }),
      save: (e) => saved.push(e),
    })
    expect(saved).toHaveLength(0)
  })

  it('自报 pid 探测抛错 ⇒ 不写、不抛（绝不让账本对齐把「启动」拖炸）', async () => {
    const saved: RegistryEntry[] = []
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => {
        throw new Error('timeout')
      },
      loadReg: () => ({ selfNode: { pid: STALE_PID } }),
      save: (e) => saved.push(e),
    })
    expect(saved).toHaveLength(0)
  })

  it('自报 pid 已被别的条目认领 ⇒ 不写（跨课错配不放松）', async () => {
    const saved: RegistryEntry[] = []
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => LIVE_PID,
      loadReg: () => ({
        selfNode: { pid: STALE_PID },
        trainingLoops: { '': { pid: LIVE_PID } },
      }),
      save: (e) => saved.push(e),
    })
    expect(saved).toHaveLength(0)
  })

  it('账本里没有 selfNode 条目 + 服务在跑 ⇒ 登记（手工起的实例被接管）', async () => {
    const saved: RegistryEntry[] = []
    await stepSelfNode(CFG, {
      healthy: async () => true,
      probePidOf: async () => LIVE_PID,
      loadReg: () => ({}),
      save: (e) => saved.push(e),
    })
    expect(saved).toHaveLength(1)
    expect(saved[0]!.pid).toBe(LIVE_PID)
    expect(saved[0]!.entry).toBe(SELF_NODE_ENTRY)
  })
})
