/**
 * training-console.test.ts — tools/training/console 的单元测试。
 *
 * 覆盖（headless，不 spawn 真实训练进程）：
 *  - api.buildStateView：快照结构（组件/节点/模式/指标）与课程发现；
 *  - api.routeAction：未知动作 404、参数错误 400、busy 互斥 409、
 *    节点启停/并发回写 rl-config.json（临时副本，跑完还原）；
 *  - actions 控制台状态：trainer 模式持久化（console-state.json 副本）；
 *  - 纯函数与 SSR（render.tsx 包装）：渲染包含关键区块与转义安全。
 */

import { afterAll, beforeAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readIterMetrics } from '../tools/training/console/iters'

// ── 隔离：rl-config.json 与 console-state.json 指向临时副本（跑前备份，跑后还原）──

const REAL_CONFIG = path.join(import.meta.dir, '..', 'nn-training', 'rl-config.json')
const REAL_STATE = path.join(
  import.meta.dir,
  '..',
  'tools',
  'training',
  'console',
  'console-state.json',
)
const BACKUP_CONFIG = mkdtempSync(path.join(os.tmpdir(), 'bcity-console-test-')) + '/rl-config.json'
const BACKUP_STATE = BACKUP_CONFIG.replace('rl-config.json', 'console-state.json')

// console-state.json / rl-config.json 路径在被测模块内为常量拼接 —— 测试采用
// 「写前备份 / 写后字节级还原」策略：写盘分支真实执行，跑完恢复原内容，零残留。
let realStateExisted = false
let realStateContent = ''
let realConfigContent = ''

interface TestNode {
  id: string
  url: string
  authKey: string
  concurrency: number
  enabled: boolean
  gpu_push?: boolean
}

interface TestConfig {
  version: number
  nodes: TestNode[]
  rl: Record<string, unknown>
}

function loadRealConfig(): TestConfig {
  return JSON.parse(readFileSync(REAL_CONFIG, 'utf-8')) as TestConfig
}

beforeAll(() => {
  realConfigContent = readFileSync(REAL_CONFIG, 'utf-8')
  writeFileSync(BACKUP_CONFIG, realConfigContent)
  try {
    realStateContent = readFileSync(REAL_STATE, 'utf-8')
    realStateExisted = true
    writeFileSync(BACKUP_STATE, realStateContent)
  } catch {
    realStateExisted = false
  }
})

afterAll(() => {
  // 字节级还原（测试进程可能中途改写真实文件）
  writeFileSync(REAL_CONFIG, realConfigContent)
  if (realStateExisted) writeFileSync(REAL_STATE, realStateContent)
  else {
    try {
      rmSync(REAL_STATE)
    } catch {
      /* absent */
    }
  }
  rmSync(path.dirname(BACKUP_CONFIG), { recursive: true, force: true })
})

// ── 被测模块（在备份落盘后 import，模块初始化无副作用）──

const api = await import('../tools/training/console/api')
const actions = await import('../tools/training/console/actions')
const render = await import('../tools/training/console/render')
const view = await import('../tools/training/ui/view')

function post(act: string, body: Record<string, unknown> = {}): Promise<Response> {
  return api.routeAction(act, body) as Promise<Response>
}

async function postJson(
  act: string,
  body: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const r = (await post(act, body)) as Response
  return { ...((await r.json()) as Record<string, unknown>), __status: r.status }
}

describe('console/api.buildStateView', () => {
  it('快照包含五个组件、节点表与模式区块', async () => {
    const s = await api.buildStateView()
    const keys = s.components.map((c) => c.key) as string[]
    expect(keys.sort()).toEqual(
      ['cloudflared', 'hubServer', 'selfNode', 'trainingLoop', 'workerServe'].sort(),
    )
    for (const c of s.components) {
      expect(['running', 'stopped', 'exited']).toContain(c.status)
      expect(c.label.length).toBeGreaterThan(0)
    }
    expect(s.nodes.length).toBeGreaterThan(0)
    expect(['pull', 'push', 'local']).toContain(s.modes.trainerPpo)
    expect([0, 1]).toContain(s.modes.stream)
    expect([0, 1]).toContain(s.modes.doubleBuffer)
    expect(s.courses).toBeInstanceOf(Array)
  })

  it('课程发现含 curricula/*.jsonc（即使 tmp 无日志）', () => {
    const courses = api.discoverCourses()
    // curricula/ 至少有 p4-fast.jsonc 等；不强制非空，但类型必须对
    for (const c of courses) expect(typeof c).toBe('string')
    // p4-fast 在 curricula/ 有定义但 tmp/ 可能无日志——应被补充进列表
    expect(courses).toContain('p4-fast')
  })
})

describe('console/api.nodeViews 并行 ping（§365：串行导致 /api/state 超时空回复）', () => {
  it('enabled 节点并发探测：全部同时发起，顺序保持，disabled 不探测', async () => {
    const origFetch = globalThis.fetch
    let active = 0
    let maxActive = 0
    const started: string[] = []
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    globalThis.fetch = ((url: unknown, init?: RequestInit) => {
      active++
      maxActive = Math.max(maxActive, active)
      started.push(String(url))
      return new Promise<Response>((resolve, reject) => {
        const onAbort = (): void => {
          active--
          reject(new DOMException('aborted', 'AbortError'))
        }
        init?.signal?.addEventListener('abort', onAbort)
        void gate.then(() => {
          init?.signal?.removeEventListener('abort', onAbort)
          active--
          resolve(
            new Response(JSON.stringify({ codeHash: 'abcd1234', cpus: 8 }), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        })
      })
    }) as typeof fetch
    try {
      const cfg = {
        version: 1,
        nodes: [
          { id: 'a', url: 'http://node-a', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'b', url: 'http://node-b', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'c', url: 'http://node-c', authKey: 'k', concurrency: 2, enabled: true },
          { id: 'off', url: 'http://node-off', authKey: 'k', concurrency: 2, enabled: false },
        ],
        rl: { hub_port: 8900, agent_port: 8910, remote_token: 't' },
      } as Parameters<typeof api.nodeViews>[0]
      const p = api.nodeViews(cfg)
      // 并行实现下全部 enabled 节点在同一微任务批次已发起 fetch（串行实现此刻仅 1 个挂起）
      expect(started.length).toBe(3)
      expect(started.every((u) => u.endsWith('/v1/ping'))).toBe(true)
      expect(maxActive).toBe(3) // 三个 ping 同时挂起 = 并行；串行永远 maxActive=1
      release()
      const nv = await p
      expect(nv.map((n) => n.id)).toEqual(['a', 'b', 'c', 'off']) // Promise.all 保序
      expect(nv.filter((n) => n.online === true).length).toBe(3)
      expect(nv.find((n) => n.id === 'off')!.online).toBeNull() // disabled 不探测
    } finally {
      release()
      globalThis.fetch = origFetch
    }
  })
})

describe('console/api.routeAction', () => {
  it('未知动作 → null（路由层 404）', async () => {
    expect(await post('noSuchAction', {})).toBeNull()
  })

  it('未知组件 → 400', async () => {
    const r = (await postJson('start', { component: 'nope' })) as { __status: number }
    expect(r.__status).toBe(400)
  })

  it('节点并发越界 → 动作失败且不写盘', async () => {
    const before = readFileSync(REAL_CONFIG, 'utf-8')
    const r = await postJson('setNodeConcurrency', { id: 'self', concurrency: 999 })
    expect(r.ok).toBe(false)
    expect(readFileSync(REAL_CONFIG, 'utf-8')).toBe(before)
  })

  it('节点不存在 → 409 ActionError', async () => {
    const r = (await postJson('setNodeEnabled', { id: 'nosuch-node', enabled: true })) as {
      __status: number
    }
    expect(r.__status).toBe(409)
  })

  it('节点并发回写 rl-config.json（写后还原）', async () => {
    const cfg = loadRealConfig()
    const target = cfg.nodes.find((n) => n.enabled) ?? cfg.nodes[0]!
    const orig = target.concurrency
    const r = await postJson('setNodeConcurrency', { id: target.id, concurrency: orig + 1 })
    expect(r.ok).toBe(true)
    const after = loadRealConfig()
    expect(after.nodes.find((n) => n.id === target.id)!.concurrency).toBe(orig + 1)
    // 还原
    const r2 = await postJson('setNodeConcurrency', { id: target.id, concurrency: orig })
    expect(r2.ok).toBe(true)
    expect(loadRealConfig().nodes.find((n) => n.id === target.id)!.concurrency).toBe(orig)
  })

  it('节点启停回写 rl-config.json（写后还原）', async () => {
    const cfg = loadRealConfig()
    const target = cfg.nodes.find((n) => n.enabled)!
    const orig = target.enabled
    const r = await postJson('setNodeEnabled', { id: target.id, enabled: !orig })
    expect(r.ok).toBe(true)
    expect(loadRealConfig().nodes.find((n) => n.id === target.id)!.enabled).toBe(!orig)
    await postJson('setNodeEnabled', { id: target.id, enabled: orig })
    expect(loadRealConfig().nodes.find((n) => n.id === target.id)!.enabled).toBe(orig)
  })

  it('模式开关回写 rl-config.json 的 rl 键（写后还原）', async () => {
    const before = loadRealConfig().rl.stream as number | undefined
    const flip = Number(before ?? 0) === 1 ? 0 : 1
    const r = await postJson('setMode', { key: 'rl.stream', value: String(flip) })
    expect(r.ok).toBe(true)
    expect(loadRealConfig().rl.stream).toBe(flip)
    await postJson('setMode', { key: 'rl.stream', value: String(before ?? 0) })
    expect(loadRealConfig().rl.stream).toBe(before ?? 0)
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

describe('console course single source (DECISIONS §351 bug 1)', () => {
  it('effectiveCourse：state 优先；空则回退最近活跃课程；无课程为空串', () => {
    expect(api.effectiveCourse({ course: 'p4-horizon' }, ['a', 'b'])).toBe('p4-horizon')
    expect(api.effectiveCourse({ course: '' }, ['a', 'b'])).toBe('a')
    expect(api.effectiveCourse({ course: '' }, [])).toBe('')
  })

  it('显示课程 = 动作课程（单一事实源不变量）', async () => {
    const s = await api.buildStateView()
    const ctx = api.actionCtx({})
    expect(ctx.course).toBe(s.course)
  })

  it('actionCtx：显式 body.course 覆盖回退', () => {
    expect(api.actionCtx({ course: 'explicit-course' }).course).toBe('explicit-course')
  })

  it('页面课程下拉含「自动（最近活跃课程）」占位项', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('自动（最近活跃课程）')
  })
})

describe('console local×stream 假互斥移除 (DECISIONS §351 bug 2)', () => {
  it('页面不再声称 local 需 rl.stream=0 / 本地 PPO 互斥；stream 开关仍在', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).not.toContain('需 rl.stream=0')
    expect(html).not.toContain('本地 PPO 互斥')
    // stream 开关已收入 TrainingLoop 启动弹窗（SSR 首帧不渲染）
    expect(html).not.toContain('启动 TrainingLoop')
  })
})

describe('console SSR renderConsolePage', () => {
  it('渲染包含区块标题、动作按钮、开关键与转义', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('NN 训练控制台')
    expect(html).toContain('tc-cc__name') // 组件小卡（名称渲染体）
    expect(html).toContain('tc-hero') // 训练状态 hero
    expect(html).toContain('tc-comps') // 组件 4 小卡
    expect(html).toContain('tc-npill') // 节点 pill 行
    expect(html).toContain('训练状态') // hero aria-label
    // 详情抽屉 / TrainingLoop 启动弹窗默认不渲染（SSR 首帧；tc-drawer 类名在 CSS，用 <aside 判定）
    expect(html).not.toContain('<aside class="tc-drawer"')
    expect(html).not.toContain('启动 TrainingLoop')
    // 无原始 <script> 注入风险：SSR 输出经 preact 转义
    expect(html).not.toContain('<script>alert')
  })
})

describe('console sparkline (ui/view)', () => {
  it('正态序列：polyline 坐标数 = 数据点数，含末点圆点', () => {
    const svg = view.sparkline([1, 2, 3, 4, 5])
    expect(svg).toContain('<svg')
    expect(svg).toContain('<polyline')
    // 5 个坐标对（每对 x,y；[\d.] 同时匹配整数与小数）
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(5)
    expect(svg).toContain('<circle')
  })

  it('恒定序列：满幅平线（y 折半）+ 灰色（无形状可循）', () => {
    const svg = view.sparkline([7, 7, 7, 7])
    // 全部 y 相同 = height/2
    const ys = [...svg.matchAll(/,([\d.]+) /g)].map((m) => m[1])
    expect(new Set(ys).size).toBeLessThanOrEqual(1)
    expect(svg).toContain('#94a3b8')
  })

  it('空序列与非有限值：占位符 / NaN 点被跳过', () => {
    expect(view.sparkline([])).toContain('muted')
    const svg = view.sparkline([1, Number.NaN, 3])
    const pairs =
      svg
        .split('<polyline')[1]!
        .split('/>')[0]!
        .match(/[\d.]+,[\d.]+/g) ?? []
    expect(pairs.length).toBe(2)
  })

  it('hero 渲染胜率趋势 sparkline（有数据课程）', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('tc-hero')
    if (s.metrics.available && s.metrics.iters.length > 0) {
      expect(html).toContain('<svg class="spark"')
    }
  })

  it('sparkline 只取最近 20 轮且时间正序', () => {
    // 直接验证 metricSeries 的排序窗口语义（经 renderConsolePage 间接覆盖亦可）
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      metrics: {
        available: true,
        iters: Array.from({ length: 30 }, (_, i) => ({
          iter: 100 - i,
          time: '',
          winRate: i / 100,
          scoreMean: i,
          scoreStd: 1,
          samples: 1,
          rolloutSec: 1,
          ppoSec: 1,
          kl: 0.01,
          entropy: 1,
          policyLoss: 0,
          valueLoss: 0,
          meanRet: 0,
          lr: 0.00005,
          expectedGames: 4,
          halted: false,
          topDims: '',
          avgTicks: 100,
          accuracy: 0,
          loot: 0,
          kills: 0,
          actuals: { games: 4, totalKills: i, totalPU: i % 3, avgTicks: 100 },
          evalData: {
            time: '',
            games: 10,
            wins: i % 10,
            winRate: (i % 10) / 10,
            clears: 0,
            clearRate: 0,
            dropped: 0,
            sec: 30,
            wver: 'v1',
            outcomes: {},
            avgTicks: 100,
            totalKills: i,
            totalPU: 0,
            scoreMean: 0,
            scoreStd: 0,
          },
        })),
      },
    }
    const html = render.renderConsolePage(s)
    // 30 轮输入 → hero 画 4 条 polyline：KPI 大胜率走势 1 条 + 击杀/道具/eval 三格各 1 条（胜率不再重复画），点数 ≤20
    const polylines = html.match(/<polyline/g) ?? []
    expect(polylines.length).toBe(4)
    for (const seg of html.split('<polyline').slice(1)) {
      const pts = seg.split('/>')[0]!.match(/[\d.]+,[\d.]+/g) ?? []
      expect(pts.length).toBeLessThanOrEqual(20)
      expect(pts.length).toBeGreaterThan(0)
    }
  })
})

describe('console/log viewer (§348 补 2)', () => {
  it('readLogTail：文件尾部窗口 + maxLines 截断 + 缺文件安全', () => {
    const missing = api.readLogTail('tmp/no-such-log-xyz.log', 50)
    expect(missing.exists).toBe(false)
    expect(missing.lines).toEqual([])
    // sampler-agent.log 在真实仓库中通常存在（历史运行产物）；若存在则行数受 maxLines 约束
    const real = api.readLogTail('tmp/sampler-agent.log', 30)
    if (real.exists) {
      expect(real.lines.length).toBeLessThanOrEqual(30)
      expect(real.fileSize).toBeGreaterThan(0)
    }
  })

  it('resolveComponentLog：五个组件均有日志映射；未知组件 null', () => {
    const cfg = JSON.parse(readFileSync(REAL_CONFIG, 'utf-8')) as Parameters<
      typeof api.resolveComponentLog
    >[1]
    for (const key of [
      'selfNode',
      'hubServer',
      'cloudflared',
      'trainingLoop',
      'workerServe',
    ] as const) {
      expect(api.resolveComponentLog(key, cfg, 'p4-horizon')).toBeTruthy()
    }
    expect(api.resolveComponentLog('nope' as never, cfg, 'x')).toBeNull()
  })

  it('componentLogPayload：已知组件返回载荷；未知组件 null', async () => {
    const p = await api.componentLogPayload('selfNode', 50)
    expect(p).not.toBeNull()
    expect(p!.component).toBe('selfNode')
    expect(p!.log).toBe('tmp/sampler-agent.log')
    expect(Array.isArray(p!.lines)).toBe(true)
    expect(await api.componentLogPayload('nope' as never, 50)).toBeNull()
  })

  it('renderLogPage：日志内容转义 + 组件导航 + follow 开关', async () => {
    const p = (await api.componentLogPayload('selfNode', 40))!
    const state = await api.buildStateView()
    const html = render.renderLogPage(p, {
      components: state.components.map((c) => ({ key: c.key, label: c.label, status: c.status })),
      follow: true,
      lines: 40,
    })
    expect(html).toContain('组件日志')
    expect(html).toContain('id="logbox"')
    expect(html).toContain('id="follow" checked')
    expect(html).toContain('/log/trainingLoop')
    expect(html).toContain('返回控制台')
    // 日志文本必须经转义（原始 <script> 不得出现在 logbox 内容里）
    expect(html).not.toContain('<script>alert')
  })

  it('renderLogPage：暂停态（follow=false）刷新间隔 4s；缺文件显示占位', async () => {
    const p = (await api.componentLogPayload('cloudflared', 20))!
    p.exists = false
    p.lines = []
    const html = render.renderLogPage(p, { components: [], follow: false, lines: 20 })
    expect(html).toContain('日志文件不存在')
    // follow 复选框无 checked 属性（跟随节奏由客户端 usePolling + shouldFollow 纯函数实现）
    expect(html).not.toContain('id="follow" checked')
  })
})

describe('console/api §361③：配置损坏兜底与日志尾容错', () => {
  it('loadConfigSafe：rl-config.json 瞬时损坏回退上次成功配置，不抛 500', async () => {
    const before = readFileSync(REAL_CONFIG, 'utf-8')
    const good = api.loadConfigSafe()
    expect(good.nodes).toBeInstanceOf(Array)
    try {
      // 模拟 saveConfig 写盘窗口的半截 JSON（§339 同款竞态家族）
      writeFileSync(REAL_CONFIG, '{"version":1,"nodes":[', 'utf-8')
      const safe = api.loadConfigSafe()
      expect(safe.nodes).toEqual(good.nodes) // 回退内存缓存
    } finally {
      writeFileSync(REAL_CONFIG, before, 'utf-8')
    }
    expect(api.loadConfigSafe().nodes).toBeInstanceOf(Array) // 恢复后无崩溃
  })

  it('logTail：缺文件安全 + 尾窗口 + 超长行截断（单行损坏不拖垮）', () => {
    expect(api.logTail('tmp/no-such-log-xyz.log', 5)).toEqual([])
    const p = path.join(import.meta.dir, '..', 'nn-training', 'tmp', 'logtail-test-361.log')
    mkdirSync(path.dirname(p), { recursive: true })
    writeFileSync(p, 'a\n' + 'x'.repeat(300) + '\nb\n')
    try {
      const t = api.logTail('tmp/logtail-test-361.log', 5)
      expect(t[0]).toBe('a')
      expect(t[1]!.length).toBe(200) // 超长行截到 200
      expect(t[2]).toBe('b')
    } finally {
      rmSync(p, { force: true })
    }
  })
})

describe('console/iters §361②：完整指标表不截断（MAX 500 上限）', () => {
  it('600 轮日志 → 返回最近 500 轮', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-361-'))
    try {
      const lines: string[] = []
      for (let i = 0; i < 600; i++) {
        lines.push(
          JSON.stringify({ event: 'iteration', iter: i, time: '', winRate: 0.5, score_mean: 0 }),
        )
      }
      writeFileSync(path.join(dir, 'training_log.jsonl'), lines.join('\n'), 'utf-8')
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(500)
      expect(rows[0]!.iter).toBe(599)
      expect(rows[499]!.iter).toBe(100)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
