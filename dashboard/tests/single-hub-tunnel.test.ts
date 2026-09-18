/** single-hub-tunnel.test.ts — 「hub/隧道只开一个进程，服务所有并行课程」（2026-09-18 用户指令）。

 *  形状：`hubserver/trainingloop/selfNode/cloudflared 都只需要开一个进程，就能同时支持所有
 *  并行训练课程`。本文件钉的是 hub 与隧道这两条（trainingLoop 仍是每课一个会话——它是
 *  **有状态会话**，收敛成单进程要等 P2 的任务队列改造）。
 *
 *  四条不变量（每一条都能单独出事，且都只在「多课程同时跑」时才显形）：
 *   ① **槽位唯一**：hub/隧道的账本槽恒为 `''`（`scopeOf` 归一）。这是所有读写路径的唯一
 *      入口——不归一就会出现「看 A 课的卡片说 hub 停了」（其实在跑）、「停 A 课把共享 hub
 *      杀了」（其实该停的是 B 课自己的东西）；
 *   ② **地址唯一**：hub 端口只能经 `sharedHubPort`/`sharedHubUrl`（grep 门禁守）；
 *   ③ **旧形状拒重建**：`restartSpecFor('hubServer', '某课')` 必须 fail-closed（旧条目只能被
 *      显式换代接管）——用共享 spec 去重建一个 per-course 条目 = 两个 hub 读同一棵树；
 *   ④ **URL 全局**：隧道 URL 写单键 `rl.remote_hub_url`，不再写 per-course 的 `remote_hubs`。
 *
 *  纪律：夹具自造（不读线上 rl-config），账本/配置重定向到临时目录（同
 *  exit-watchdog.test.ts，绝不碰线上 tmp/training-start）。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { loadConfig, writeRemoteHubUrl } from '../src/core/config'
import { isSharedComponent, scopeOf } from '../src/core/registry'
import { sharedHubPort, sharedHubUrl, sharedTunnelMetricsPort, slotPort } from '../src/core/slots'
import { componentViews } from '../src/server/api/views'
import { restartSpecFor } from '../src/server/actions'
import { hubServerSpec } from '../src/stack/specs'
import type { RlConfig, RegistryEntry } from '../src/core/types'

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

/** 双课程夹具（两课各占一个槽位；hub 端口另有基数）。 */
function dualCfg(): RlConfig {
  return {
    version: 1,
    nodes: [],
    rl: {
      hub_port: 27789,
      agent_port: 27940,
      remote_token: 'fixture-token',
      local_slots: 0,
      workers: 8,
    },
    courses: {
      'course-a': { slot: 0, workers: 1, local_slots: 1 },
      'course-b': { slot: 1, workers: 1, local_slots: 1 },
    },
  } as RlConfig
}

/** 在临时账本/配置上跑一段（不读也不写线上文件）。
 *
 *  ⚠ **必须 await 回调**：async 回调里 `return fn(...)` 会在第一个 await 处就执行 finally
 *  ——环境变量被提前恢复，异步体后半段就跑去读**线上** registry.json（本文件第一次运行时
 *  真踩到：线上账本里那条历史遗留的 `hubServers[''] = {pid:1}` 被当成了被测对象）。 */
async function withScratch<T>(
  fn: (ctx: { reg: string; cfgPath: string }) => T | Promise<T>,
  cfg = dualCfg(),
): Promise<T> {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-single-hub-'))
  SCRATCH.push(dir)
  const reg = path.join(dir, 'registry.json')
  const cfgPath = path.join(dir, 'rl-config.json')
  writeFileSync(cfgPath, JSON.stringify(cfg, null, 2))
  const prevReg = process.env.BCITY_REGISTRY_FILE
  const prevCfg = process.env.BCITY_RL_CONFIG
  process.env.BCITY_REGISTRY_FILE = reg
  process.env.BCITY_RL_CONFIG = cfgPath
  try {
    return await fn({ reg, cfgPath })
  } finally {
    if (prevReg === undefined) delete process.env.BCITY_REGISTRY_FILE
    else process.env.BCITY_REGISTRY_FILE = prevReg
    if (prevCfg === undefined) delete process.env.BCITY_RL_CONFIG
    else process.env.BCITY_RL_CONFIG = prevCfg
  }
}

function writeRegistry(reg: string, body: Record<string, unknown>): void {
  writeFileSync(reg, JSON.stringify(body, null, 2))
}

// ────────────────────────── ① 槽位唯一 ──────────────────────────

describe('① 槽位唯一：共享组件的账本槽恒为空串', () => {
  it('scopeOf：hub/隧道归一为 ``,其余组件按课程（含 selfNode 不受影响）', () => {
    expect(isSharedComponent('hubServer')).toBe(true)
    expect(isSharedComponent('cloudflared')).toBe(true)
    expect(isSharedComponent('trainingLoop')).toBe(false)
    for (const c of ['course-a', 'course-b', '']) {
      expect(scopeOf('hubServer', c)).toBe('')
      expect(scopeOf('cloudflared', c)).toBe('')
      expect(scopeOf('trainingLoop', c)).toBe(c)
      expect(scopeOf('localWorker', c)).toBe(c)
    }
  })

  it('组件视图：任何课程页看到的都是同一个共享实例，并标 shared', async () => {
    await withScratch(async ({ reg }) => {
      const cfg = loadConfig()
      const hub: RegistryEntry = {
        pid: 999999,
        entry: 'hub-server',
        course: '',
        url: 'http://127.0.0.1:1',
      }
      const tl: RegistryEntry = { pid: 999998, entry: 'training-loop', course: 'course-b' }
      writeRegistry(reg, {
        hubServers: { '': hub },
        trainingLoops: { 'course-b': tl },
      })
      // 查看 B 课：hub 卡片读的是**共享**条目（不是「B 课自己的 hub 没起」）
      const viewsB = await componentViews(cfg, 'course-b')
      const hubB = viewsB.find((v) => v.key === 'hubServer')
      expect(hubB?.shared).toBe(true)
      expect(hubB?.pid).toBe(hub.pid)
      expect(hubB?.status).toBe('exited') // pid 999999 存在但已死 = 账在、进程没了
      // 查看 A 课：同一个共享条目（两课看到的是同一份真相）
      const viewsA = await componentViews(cfg, 'course-a')
      expect(viewsA.find((v) => v.key === 'hubServer')?.pid).toBe(hub.pid)
      // 非共享组件仍严格按课（A 课没有 trainer ⇒ stopped，不借 B 课的条目）
      expect(viewsA.find((v) => v.key === 'trainingLoop')?.status).toBe('stopped')
      expect(viewsB.find((v) => v.key === 'trainingLoop')?.status).toBe('exited')
      expect(viewsB.find((v) => v.key === 'trainingLoop')?.shared).toBe(false)
    })
  })
})

// ────────────────────────── ② 地址唯一 ──────────────────────────

describe('② 地址唯一：hub 端口只有一处算法', () => {
  it('sharedHubPort = 基数本身；共享 metrics 口 = 基数+1（与课程槽位无关）', () => {
    const cfg = dualCfg()
    const base = Number(cfg.rl.hub_port)
    expect(sharedHubPort(cfg)).toBe(base)
    expect(sharedHubUrl(cfg)).toBe(`http://127.0.0.1:${base}`)
    expect(sharedHubUrl(cfg, '100.64.0.9')).toBe(`http://100.64.0.9:${base}`)
    expect(sharedTunnelMetricsPort(cfg)).toBe(base + 1)
    // 课程槽位只决定 push（与共享 hub 地址不重合）
    expect(slotPort(cfg, 'course-b', 'push')).not.toBe(sharedHubPort(cfg))
    expect(slotPort(cfg, 'course-b', 'push')).not.toBe(sharedTunnelMetricsPort(cfg))
  })

  it("grep 门禁：`slotPort(…, 'hub')` 不再有任何调用面（hub 地址只能经 sharedHub*）", () => {
    const root = path.join(DASHBOARD_ROOT, 'src')
    const offenders: string[] = []
    const walk = (dir: string): void => {
      for (const ent of readdirSync(dir, { withFileTypes: true })) {
        const abs = path.join(dir, ent.name)
        if (ent.isDirectory()) {
          if (ent.name === 'node_modules') continue
          walk(abs)
          continue
        }
        if (!/\.tsx?$/.test(ent.name)) continue
        if (ent.name === 'slots.ts') continue // 算术模块自己（sharedHubPort 就住这里）
        const text = readFileSync(abs, 'utf-8')
        if (/slotPort\([^)]*'hub'/.test(text)) offenders.push(path.relative(root, abs))
      }
    }
    walk(root)
    expect(offenders).toEqual([])
  })

  it('共享 hub spec 与服务端口自洽（argv 的 --port 就是 sharedHubPort）', () => {
    const cfg = dualCfg()
    const argv = hubServerSpec(cfg).cmd.map(String)
    expect(argv[argv.indexOf('--port') + 1]).toBe(String(sharedHubPort(cfg)))
  })
})

// ────────────────────────── ③ 旧形状拒重建 ──────────────────────────

describe('③ 旧形状（每课一 hub/隧道）条目拒重建', () => {
  it('restartSpecFor：hub/隧道的 per-course 条目 → null（fail-closed，不静默换角色）', async () => {
    await withScratch(({ reg }) => {
      writeRegistry(reg, {
        hubServers: { 'course-a': { pid: 4321, course: 'course-a', entry: 'hub-server' } },
        cloudflareds: { 'course-b': { pid: 4322, course: 'course-b', entry: 'cloudflared' } },
      })
      // 旧条目仍在账本里（可见/可枚举/可停止），但**不给**重建 spec：
      // 用共享 spec 重建它 = 两个 hub 读同一棵 job 目录（双派发 / 双租约 / 结果回错家）。
      expect(restartSpecFor('hubServer', 'course-a')).toBeNull()
      expect(restartSpecFor('cloudflared', 'course-b')).toBeNull()
      // 共享槽正常重建（监督器的常态路径）
      writeRegistry(reg, {
        hubServers: { '': { pid: 4323, course: '', entry: 'hub-server' } },
      })
      expect(restartSpecFor('hubServer', 'course-a')).not.toBeNull()
    })
  })
})

// ────────────────────────── ④ URL 全局 ──────────────────────────

describe('④ 隧道 URL 是全局事实', () => {
  it('写单键；per-course 键不再被写（也不再被读）', async () => {
    await withScratch(({ cfgPath }) => {
      writeRemoteHubUrl('https://single-hub.trycloudflare.com')
      const cfg = loadConfig()
      expect(cfg.rl.remote_hub_url).toBe('https://single-hub.trycloudflare.com')
      expect(cfg.rl.remote_hubs ?? {}).toEqual({})
      // 幂等：同值不落盘
      const m1 = statSync(cfgPath).mtimeMs
      writeRemoteHubUrl('https://single-hub.trycloudflare.com')
      expect(statSync(cfgPath).mtimeMs).toBe(m1)
    })
  })
})
