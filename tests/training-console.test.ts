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
import { mkdtempSync, mkdirSync, readFileSync, rmSync, utimesSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readIterMetrics } from '../tools/training/console/iters'
import { LOG_DIR } from '../tools/training/paths'
import type { ConsoleStateView, IterRow } from '../tools/training/ui/view'

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
    const courses = api.discoverCourses(50) // max 越 12 上限：环境课程目录增长会把 p4-fast 挤出
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

describe('console/api 慢部件快照缓存（§366：页面加载 <1s）', () => {
  it('buildStateView 冷算一次后缓存命中：重复请求零新增探测', async () => {
    api.invalidateSlowSnapshot() // 清掉前序测试可能留下的真实快照
    const origFetch = globalThis.fetch
    let calls = 0
    globalThis.fetch = ((_url: unknown, _init?: RequestInit) => {
      calls++
      return Promise.resolve(
        new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
      )
    }) as typeof fetch
    try {
      const s1 = await api.buildStateView()
      expect(calls).toBeGreaterThan(0) // 冷路径：发节点/组件探测
      const coldCalls = calls
      const s2 = await api.buildStateView()
      expect(s2.course).toBe(s1.course)
      expect(s2.components.length).toBe(s1.components.length)
      expect(calls).toBe(coldCalls) // 缓存命中：零新增探测 → 页面加载只读缓存
    } finally {
      globalThis.fetch = origFetch
      api.invalidateSlowSnapshot()
    }
  })

  it('invalidateSlowSnapshot 后下一次 buildStateView 重新冷算（动作即时上屏）', async () => {
    api.invalidateSlowSnapshot()
    const origFetch = globalThis.fetch
    let calls = 0
    globalThis.fetch = ((_url: unknown, _init?: RequestInit) => {
      calls++
      return Promise.resolve(
        new Response(JSON.stringify({ codeHash: 'x', cpus: 4 }), { status: 200 }),
      )
    }) as typeof fetch
    try {
      await api.buildStateView() // 冷算填缓存
      const warm = calls
      await api.buildStateView()
      expect(calls).toBe(warm)
      api.invalidateSlowSnapshot() // 模拟动作：置空缓存
      const before = calls
      await api.buildStateView() // 重新冷算
      expect(calls).toBeGreaterThan(before)
    } finally {
      globalThis.fetch = origFetch
      api.invalidateSlowSnapshot()
    }
  })
})

describe('console local 芯片（rl.local_slots = 0 也显示）', () => {
  it('buildStateView：local_slots=0 时 localNode 仍出现（slots=0）；配置缺失才缺省', async () => {
    const cfg = loadRealConfig()
    try {
      const patched = JSON.parse(JSON.stringify(cfg)) as TestConfig
      patched.rl.local_slots = 0
      writeFileSync(REAL_CONFIG, JSON.stringify(patched, null, 2))
      api.invalidateSlowSnapshot()
      const s = await api.buildStateView()
      expect(s.localNode).not.toBeNull()
      expect(s.localNode!.slots).toBe(0)
    } finally {
      writeFileSync(REAL_CONFIG, JSON.stringify(cfg, null, 2))
      api.invalidateSlowSnapshot()
    }
  })

  it('buildPoolView：local_slots=0 时 local 行 spec 显示「0 槽」（非缺失的 -）', async () => {
    const cfg = loadRealConfig()
    try {
      const patched = JSON.parse(JSON.stringify(cfg)) as TestConfig
      patched.rl.local_slots = 0
      writeFileSync(REAL_CONFIG, JSON.stringify(patched, null, 2))
      const p = await api.buildPoolView(true)
      expect(p.local).not.toBeNull()
      expect(p.local!.spec).toBe('0 槽')
    } finally {
      writeFileSync(REAL_CONFIG, JSON.stringify(cfg, null, 2))
    }
  })
})

describe('console 局域网只读边界（§…：LAN 查看 / localhost 控制）', () => {
  it('isLoopbackAddress：回环 IPv4/IPv6/mapped 为真，局域网地址与未知为假（fail closed）', async () => {
    const net = await import('../tools/training/net')
    expect(net.isLoopbackAddress('127.0.0.1')).toBe(true)
    expect(net.isLoopbackAddress('::1')).toBe(true)
    expect(net.isLoopbackAddress('0:0:0:0:0:0:0:1')).toBe(true)
    expect(net.isLoopbackAddress('::ffff:127.0.0.1')).toBe(true)
    expect(net.isLoopbackAddress('192.168.1.23')).toBe(false)
    expect(net.isLoopbackAddress('10.0.0.5')).toBe(false)
    expect(net.isLoopbackAddress(null)).toBe(false)
    expect(net.isLoopbackAddress(undefined)).toBe(false)
  })

  it('sanitizeViewCourse：放行真实课程，拒绝路径穿越与不存在的课程', () => {
    const courses = api.discoverCourses(50)
    const real = courses[0]
    if (real) expect(api.sanitizeViewCourse(real)).toBe(real)
    expect(api.sanitizeViewCourse('../../secret')).toBe('')
    expect(api.sanitizeViewCourse('a/b')).toBe('')
    expect(api.sanitizeViewCourse('no-such-course-xyz')).toBe('')
    expect(api.sanitizeViewCourse('')).toBe('')
    expect(api.sanitizeViewCourse(null)).toBe('')
  })

  it('buildStateView(course) 只读覆盖查看课程且不写 console-state', async () => {
    const before = actions.loadConsoleState()
    const courses = api.discoverCourses(50)
    const target = courses.find((c) => c !== before.course) ?? before.course
    const s = await api.buildStateView(target)
    expect(s.course).toBe(target)
    // 只读：查看课程绝不落盘 console-state（LAN 切换不影响操作员课程/训练）
    expect(actions.loadConsoleState()).toEqual(before)
    // 无参 = 操作员课程（原语义不变）
    const s2 = await api.buildStateView()
    expect(s2.course).toBe(api.effectiveCourse(before, courses))
  })

  it('buildPoolView 课程键控：course 覆盖改变返回课程（缓存 key 带课程）', async () => {
    const courses = api.discoverCourses(50)
    const target = courses[0]
    if (!target) return
    const p = await api.buildPoolView(false, target)
    expect(p.course).toBe(target)
  })

  it('componentLogPayload 接受课程覆盖（日志页跟课程）', async () => {
    const courses = api.discoverCourses(50)
    const target = courses[0]
    if (!target) return
    const p = await api.componentLogPayload('trainingLoop', 50, target)
    expect(p).not.toBeNull()
    expect(p!.component).toBe('trainingLoop')
    expect(Array.isArray(p!.lines)).toBe(true)
  })

  it('cloudflared token 行：局域网只读与回环同权展示 + 复制键（只读是动作边界，不是数据边界）', async () => {
    const base = await api.buildStateView()
    // 只读 SSR 与正常 SSR 一样渲染 token 行与复制键（secret 经 buildStateView 透传）
    for (const readOnly of [true, false]) {
      const html = render.renderConsolePage({ ...base, readOnly })
      expect(html).toContain('aria-label="复制auth key"') // cloudflared token 复制键
      expect(html).toContain('token') // token 行本体
    }
    // 数据源确认：快照里 cloudflared 恒带 secret
    const s = await api.buildStateView()
    expect(s.components.find((c) => c.key === 'cloudflared')!.secret).toBeDefined()
  })

  it('只读视图 SSR：readOnly=true 渲染只读角标 + 横幅，动作按钮不禁用；false 不渲染', async () => {
    const base = await api.buildStateView()
    // 组件 busy 置空：pending/busy 锁与只读无关，避免 base 状态干扰禁用断言
    const clean = {
      ...base,
      components: base.components.map((c) => ({ ...c, busy: false })),
    }
    const html = render.renderConsolePage({ ...clean, readOnly: true })
    // 角标与横幅（类名断言避开 CSS 内联定义里的同名串）
    expect(html).toContain('class="tc-badge tc-badge--ro"')
    expect(html).toContain('class="tc-banner tc-banner--ro"')
    expect(html).toContain('🔒 只读模式')
    // 只读视图不禁用动作按钮（物理禁用会让组件区灰败破碎——只读是动作边界，不是按钮状态）：
    // 悬停提示 + 真点击由服务端 403 + flash 兜底，按钮保持正常外观可点击。
    // （正则避开 <style> 内联 CSS 里的 :disabled 选择器）
    expect(html).not.toMatch(/<button[^>]*disabled/)
    // 动作按钮带只读悬停提示（说明动作仅限本机）
    expect(html).toContain('title="只读模式：操作仅限本机 localhost"')
    const html2 = render.renderConsolePage({ ...clean, readOnly: false })
    expect(html2).not.toContain('class="tc-badge tc-badge--ro"')
    expect(html2).not.toContain('class="tc-banner tc-banner--ro"')
    expect(html2).not.toContain('🔒 只读模式')
    expect(html2).not.toContain('title="只读模式：操作仅限本机 localhost"')
  })

  it('hydrate 安全：首屏组件不得在 useState 初始化里读 localStorage（横幅关闭后样式崩的根因）', () => {
    // 背景：SSR 无 localStorage，若首帧用「本地存储值」初始化 state，客户端 hydrate 的
    // vnode 与 SSR HTML 不一致 → Preact 水合错配 → 组件区 DOM 错位（只读横幅被关闭后
    // 首帧少一个兄弟节点，样式整体崩坏）。纪律：首帧用 SSR 默认值，本地偏好在
    // useEffect（hydrate 之后）恢复——与 Hero 的 TC_HERO_ITER_VIEW 同款写法。
    const files = [
      path.join(import.meta.dir, '..', 'tools', 'training', 'console', 'ui', 'app.tsx'),
      path.join(import.meta.dir, '..', 'tools', 'training', 'console', 'ui', 'panels', 'Hero.tsx'),
    ]
    for (const f of files) {
      const flat = readFileSync(f, 'utf8').replace(/\s+/g, ' ')
      // 匹配 useState(...)（容一层嵌套括号，覆盖 `() => readLocal(...)` 这类惰性初始化）
      const inits = flat.match(/useState(?:<[^>]*>)?\((?:[^()]|\([^()]*\))*\)/g) ?? []
      // 守卫：正则必须真匹配到（Hero 至少 2 处），否则断言会静默空跑
      expect(inits.length).toBeGreaterThan(1)
      for (const init of inits) {
        expect(init).not.toMatch(/readLocal\(|localStorage/)
      }
    }
    // 两个本地偏好必须走 hydrate 后的 effect 恢复
    const app = readFileSync(files[0]!, 'utf8')
    expect(app).toContain('TC_RO_BANNER_DISMISSED')
    expect(app).toContain('storedInterval()')
  })

  it('课程 select：局域网只读下 hub 运行也不禁用（可切查看课程）；本机 hub 运行才锁定', async () => {
    const base = await api.buildStateView()
    // hubServer 强制 running（hub 运行 = 在途训练状态）
    const mk = (readOnly: boolean): ConsoleStateView => ({
      ...base,
      readOnly,
      components: base.components.map((c) =>
        c.key === 'hubServer' ? { ...c, status: 'running' as const } : c,
      ),
    })
    const selTag = (html: string): string =>
      html.match(/<select[^>]*id="courseSel"[^>]*>/)?.[0] ?? ''
    // 局域网只读：hub 运行中 select 仍可用（切换仅影响查看），无锁定提示
    const roSel = selTag(render.renderConsolePage(mk(true)))
    expect(roSel).not.toContain('disabled')
    expect(render.renderConsolePage(mk(true))).not.toContain('hub 运行中，课程已锁定')
    // 本机：hub 运行中 select 锁定（原语义保留）
    const rwSel = selTag(render.renderConsolePage(mk(false)))
    expect(rwSel).toContain('disabled')
    expect(render.renderConsolePage(mk(false))).toContain('hub 运行中，课程已锁定')
  })

  it('训练中课程标签：trainingLoop 运行且课程 ≠ 查看课程时，select 后高亮「正在训练：<课程>」', async () => {
    const base = await api.buildStateView()
    const mk = (course: string, tlCourse: string | null, running: boolean): ConsoleStateView => ({
      ...base,
      course,
      components: base.components.map((c) =>
        c.key === 'trainingLoop'
          ? {
              ...c,
              status: running ? ('running' as const) : ('stopped' as const),
              course: tlCourse,
            }
          : c,
      ),
    })
    // 查看 viewB、训练 trainA → select 后高亮训练课程
    const html = render.renderConsolePage(mk('viewB', 'trainA', true))
    expect(html).toContain('正在训练：trainA')
    // 查看课程 = 训练课程 → 无标签（正在看的就是训练的）
    const same = render.renderConsolePage(mk('trainA', 'trainA', true))
    expect(same).not.toContain('正在训练：')
    // trainingLoop 未运行 → 无标签
    const idle = render.renderConsolePage(mk('viewB', 'trainA', false))
    expect(idle).not.toContain('正在训练：')
  })

  it('只读横幅关闭键带 tc. 前缀：cleanupNonTcKeys 白名单清理不误删', () => {
    expect(view.TC_RO_BANNER_DISMISSED.startsWith('tc.')).toBe(true)
    const mem = new Map<string, string>()
    const storage = {
      getItem: (k: string): string | null => mem.get(k) ?? null,
      setItem: (k: string, v: string): void => void mem.set(k, v),
      removeItem: (k: string): void => void mem.delete(k),
      key: (i: number): string | null => [...mem.keys()][i] ?? null,
      get length(): number {
        return mem.size
      },
    }
    mem.set(view.TC_RO_BANNER_DISMISSED, '1')
    mem.set('junk.readonly', 'x')
    const removed = view.cleanupNonTcKeys(storage)
    expect(removed).toEqual(['junk.readonly'])
    expect(mem.has(view.TC_RO_BANNER_DISMISSED)).toBe(true)
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
    expect(html).not.toContain('class="tc-modal-mask"')
  })
})

describe('console SSR renderConsolePage', () => {
  it('渲染包含区块标题、动作按钮、开关键与转义', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('炼丹炉')
    expect(html).toContain('tc-cc__name') // 组件小卡（名称渲染体）
    expect(html).toContain('tc-hero') // 训练状态 hero
    expect(html).toContain('tc-comps') // 组件 4 小卡
    expect(html).toContain('tc-npill') // 节点 pill 行
    expect(html).toContain('训练状态') // hero aria-label
    // 详情抽屉 / TrainingLoop 启动弹窗默认不渲染（SSR 首帧；tc-drawer 类名在 CSS，用 <aside 判定）
    expect(html).not.toContain('<aside class="tc-drawer"')
    expect(html).not.toContain('class="tc-modal-mask"')
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

  it('hero 渲染胜率趋势走势图（有数据课程）', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('tc-hero')
    if (s.metrics.available && s.metrics.iters.length > 0) {
      // 大胜率走势 SVG（tc-trend__svg），含坐标轴网格线
      expect(html).toContain('tc-trend__svg')
      expect(html).toContain('<line') // 网格线
    }
  })

  describe('console trend chart / 走势范围 (§382: 全量/最近30/最近10)', () => {
    const mkView = (n: number, withEval: boolean): ConsoleStateView => ({
      time: 't',
      course: 'c',
      courses: [],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      metrics: {
        available: true,
        iters: Array.from({ length: n }, (_, i) => ({
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
          actuals: {
            games: 4,
            totalKills: i,
            totalPU: i % 3,
            avgTicks: 100,
            avgResidualHp: null,
          },
          evalData: withEval
            ? {
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
                avgResidualHp: null,
                scoreMean: 0,
                scoreStd: 0,
              }
            : null,
        })),
      },
    })

    it('metricSeries 返回全量时间正序 + 逐位对齐的 iters', () => {
      const series = view.metricSeries(mkView(30, true).metrics.iters)
      const win = series.find((s) => s.key === 'winRate')!
      expect(win.vals.length).toBe(30)
      expect(win.iters.length).toBe(30)
      // 时间正序：iters 升序（输入 iter=100-i 是降序，排序后应升序）
      expect(win.iters[0]).toBe(71)
      expect(win.iters[29]).toBe(100)
    })

    it('sliceSeries：all 全量 / 30 / 10 截取点数正确', () => {
      const series = view.metricSeries(mkView(50, true).metrics.iters)
      const win = series.find((s) => s.key === 'winRate')!
      expect(view.sliceSeries(win, 'all').vals.length).toBe(50)
      expect(view.sliceSeries(win, '30').vals.length).toBe(30)
      expect(view.sliceSeries(win, '10').vals.length).toBe(10)
    })

    it('sliceSeries eval 特殊语义：「最近 N」= 最近 N 个有效点（跳过 NaN 缺口）', () => {
      // 仅偶数 iter 有 eval 数据 → 50 轮中约 25 个有效点
      const iters = Array.from({ length: 50 }, (_, i) => ({
        iter: i + 1,
        time: '',
        winRate: 0.5,
        scoreMean: 0,
        scoreStd: 0,
        samples: 1,
        rolloutSec: 1,
        ppoSec: 1,
        kl: 0,
        entropy: 1,
        policyLoss: 0,
        valueLoss: 0,
        meanRet: 0,
        lr: 0.0001,
        expectedGames: 4,
        halted: false,
        topDims: '',
        avgTicks: 100,
        accuracy: 0,
        loot: 0,
        kills: 0,
        actuals: null,
        evalData:
          (i + 1) % 2 === 0
            ? {
                time: '',
                games: 10,
                wins: 1,
                winRate: 0.1,
                clears: 0,
                clearRate: 0,
                dropped: 0,
                sec: 30,
                wver: 'v1',
                outcomes: {},
                avgTicks: 100,
                totalKills: 0,
                totalPU: 0,
                avgResidualHp: null,
                scoreMean: 0,
                scoreStd: 0,
              }
            : null,
      }))
      const series = view.metricSeries(iters)
      const evalS = series.find((s) => s.key === 'eval')!
      // 全量 eval：只保留非 NaN（25 个有效点），iters 逐位对齐
      const all = view.sliceSeries(evalS, 'all')
      expect(all.vals.length).toBe(25)
      expect(all.iters.length).toBe(25)
      // 最近 10 个有效 eval 点
      const last10 = view.sliceSeries(evalS, '10')
      expect(last10.vals.length).toBe(10)
      // 应为最后 10 个偶数 iter：32,34,...,50
      expect(last10.iters[0]).toBe(32)
      expect(last10.iters[9]).toBe(50)
    })

    it('hero 渲染 4 条走势图 + 范围档位开关', () => {
      const html = render.renderConsolePage(mkView(30, true))
      // 大胜率走势 1 + 击杀/道具/eval 三格 = 4 张走势图（匹配元素，排除 CSS 里的同名类定义）
      const charts = (html.match(/class="tc-trend__svg"/g) ?? []).length
      expect(charts).toBe(4)
      // 范围档位渲染且默认最近 30
      expect(html).toContain('tc-trend-range__btn')
      expect(html).toContain('全量')
      expect(html).toContain('最近30')
      expect(html).toContain('最近10')
    })
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

  it('readLogTail：maxLines=all 读全部行，小文件不截断（§371）', () => {
    const rel = 'tmp/logtail-all-371.log'
    const p = path.join(import.meta.dir, '..', 'nn-training', rel)
    mkdirSync(path.dirname(p), { recursive: true })
    const lines = Array.from({ length: 50 }, (_, i) => `line-${i}`)
    writeFileSync(p, lines.join('\n') + '\n', 'utf-8')
    try {
      const t = api.readLogTail(rel, 'all')
      expect(t.exists).toBe(true)
      expect(t.truncated).toBe(false)
      expect(t.lines).toEqual(lines)
      expect(t.totalLines).toBe(50) // 顶部「共 N 行」= 文件总行数（§372）
      // 数字模式仍然只取尾 N 行
      const t5 = api.readLogTail(rel, 5)
      expect(t5.lines).toEqual(lines.slice(-5))
      // 缺文件：totalLines null
      expect(api.readLogTail('tmp/no-such-log-xyz.log', 50).totalLines).toBeNull()
    } finally {
      rmSync(p, { force: true })
    }
  })

  it('日志 GBK 乱码修复（§373）：混合 UTF-8/GBK 行逐行容错解码，不误伤其它行', () => {
    // 「超时（瞬时连接被拒），重试 5.0s 之后」的 GBK 字节（python gbk encode 实测）
    const gbkB64 = 's6zKsaOoy7LKscGsvdOxu77co6mjrNbYytQgNS4wcyDWrrrz'
    const gbkBytes = Uint8Array.from(atob(gbkB64), (c) => c.charCodeAt(0))
    const rel = 'tmp/logtail-gbk-373.log'
    const p = path.join(import.meta.dir, '..', 'nn-training', rel)
    mkdirSync(path.dirname(p), { recursive: true })
    const buf = Buffer.concat([
      Buffer.from('[09:01:53] wait_job: job bb11e73f1d2c327d HTTP 530 '),
      Buffer.from(gbkBytes),
      Buffer.from('\n'),
      Buffer.from('[09:01:55] [sampler-agent] task ok utf8 中文正常行\n'),
    ])
    writeFileSync(p, buf)
    try {
      const t = api.readLogTail(rel, 'all')
      expect(t.lines[0]).toContain('超时（瞬时连接被拒）')
      expect(t.lines[0]).not.toContain('\uFFFD') // 无替换符乱码残留
      expect(t.lines[1]).toBe('[09:01:55] [sampler-agent] task ok utf8 中文正常行') // UTF-8 行不受影响
      // dashboard 卡的 logTail 同步修复
      const tail = api.logTail(rel, 5)
      expect(tail[0]).toContain('超时')
    } finally {
      rmSync(p, { force: true })
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

  it('scanLatestLog（§374/§381）：cloudflared 动态文件名按 mtime 取最新，忽略无关文件', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-logscan-'))
    try {
      const stale = path.join(dir, 'cloudflared-2026-09-08T04-00-00-a1.log')
      const fresh = path.join(dir, 'cloudflared-2026-09-08T05-00-00-a1.log')
      writeFileSync(stale, 'stale\n', 'utf-8')
      writeFileSync(fresh, 'fresh\n', 'utf-8')
      writeFileSync(path.join(dir, 'hub-server.out'), 'noise\n', 'utf-8')
      // 指定 mtime（utimes 确定性：stale < fresh），不依赖写入顺序
      utimesSync(stale, new Date('2026-09-08T05:00:00Z'), new Date('2026-09-08T05:00:00Z'))
      utimesSync(fresh, new Date('2026-09-08T06:00:00Z'), new Date('2026-09-08T06:00:00Z'))
      expect(api.scanLatestLog(dir, 'cloudflared', 'x')).toBe(fresh)
      // 无匹配文件 → null；目录不存在 → null（不抛）
      expect(api.scanLatestLog(dir, 'workerServe', 'x')).toBeNull()
      const missing = path.join(os.tmpdir(), 'bcity-logscan-no-such-dir-xyz')
      expect(api.scanLatestLog(missing, 'selfNode', 'x')).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('scanLatestLog（§374/§381）：trainingLoop 扫课程子目录与 course 直连路径', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-logscan2-'))
    try {
      const courseDir = path.join(dir, 'p3-vk1')
      const otherDir = path.join(dir, 'ep60')
      mkdirSync(courseDir, { recursive: true })
      mkdirSync(otherDir, { recursive: true })
      const a = path.join(courseDir, 'training-loop.log')
      const b = path.join(otherDir, 'training-loop.log')
      writeFileSync(a, 'a\n', 'utf-8')
      writeFileSync(b, 'b\n', 'utf-8')
      utimesSync(a, new Date('2026-09-08T05:00:00Z'), new Date('2026-09-08T05:00:00Z'))
      utimesSync(b, new Date('2026-09-08T06:00:00Z'), new Date('2026-09-08T06:00:00Z'))
      // 任意课程目录里最新的 training-loop.log（ep60 新）
      expect(api.scanLatestLog(dir, 'trainingLoop', '')).toBe(b)
      // course 直连路径存在且比其它都新 → 优先
      utimesSync(a, new Date('2026-09-08T07:00:00Z'), new Date('2026-09-08T07:00:00Z'))
      expect(api.scanLatestLog(dir, 'trainingLoop', 'p3-vk1')).toBe(a)
      // 目录不存在 → null
      expect(
        api.scanLatestLog(
          path.join(os.tmpdir(), 'bcity-logscan2-no-dir'),
          'trainingLoop',
          'p3-vk1',
        ),
      ).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('componentLogPayload：已知组件返回载荷；未知组件 null', async () => {
    const p = await api.componentLogPayload('selfNode', 50)
    expect(p).not.toBeNull()
    expect(p!.component).toBe('selfNode')
    // 2026-09-08 双 tmp 统一：组件日志统一落到 LOG_DIR = 仓库根 tmp/（绝对路径）
    expect(p!.log).toBe(path.join(LOG_DIR, 'sampler-agent.log'))
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

describe('console hero 最新 6 轮 eval toggle', () => {
  const mkIterRow = (iter: number, hasEval: boolean): IterRow => ({
    iter,
    time: `t${iter}`,
    winRate: 0.5,
    scoreMean: 0,
    scoreStd: 0,
    samples: 1,
    rolloutSec: 1,
    ppoSec: 1,
    kl: 0,
    entropy: 1,
    policyLoss: 0,
    valueLoss: 0,
    meanRet: 0,
    lr: 1e-4,
    expectedGames: 4,
    halted: false,
    topDims: '',
    avgTicks: 100,
    accuracy: 0,
    loot: 0,
    kills: 0,
    actuals: null,
    evalData: hasEval
      ? {
          time: `e${iter}`,
          games: 10,
          wins: 1,
          winRate: 0.1,
          clears: 0,
          clearRate: 0,
          dropped: 0,
          sec: 30,
          wver: 'v1',
          outcomes: {},
          avgTicks: 100,
          totalKills: 1,
          totalPU: 0,
          avgResidualHp: null,
          scoreMean: 0,
          scoreStd: 0,
        }
      : null,
  })

  it('eval 视图数据源 = 有 evalData 的轮，iter 倒序（与抽屉 eval 过滤同口径）', () => {
    const rows = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => mkIterRow(i, i % 2 === 0))
    const ev = view.filterGroups(view.iterGroups(rows), 'eval')
    expect(ev.map((g) => g.iter)).toEqual([8, 6, 4, 2])
    for (const g of ev) expect(g.eval).not.toBeNull()
    // rollout 过滤 = 无 eval 的轮（奇数 iter：1,3,5,7）；eval 过滤 = 有 eval 的轮（偶数）
    const mains = view.filterGroups(view.iterGroups(rows), 'rollout')
    expect(mains.map((g) => g.iter)).toEqual([7, 5, 3, 1])
    expect(mains.length).toBe(4)
  })

  it('hero SSR 默认主行视图：渲染主行/eval toggle 与「最新 6 轮完整指标」', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('主行') // toggle 主行档
    expect(html).toContain('完整指标表')
    expect(html).toContain('最新 ')
  })
})

describe('console/actions.resolveCourseBc（§384：种子路径读课程 bc 字段）', () => {
  it('p3-rd1/vk1 → 课程 bc（.ckpt.60）；未知课程 → legacy 硬编码', () => {
    expect(actions.resolveCourseBc('p3-rd1')).toContain('weights.json.ckpt.60')
    expect(actions.resolveCourseBc('p3-vk1')).toContain('weights.json.ckpt.60')
    expect(actions.resolveCourseBc('no-such-course-xyz').endsWith('weights.json')).toBe(true)
    expect(actions.resolveCourseBc('no-such-course-xyz')).not.toContain('ckpt')
  })
})

describe('detectPpoQueueStall（PPO 队列 >5min 无 worker 领取 → warning）', () => {
  const traj = mkdtempSync(path.join(os.tmpdir(), 'bcity-ppo-stall-'))
  const jobRoot = path.join(traj, 'remote-jobs')
  mkdirSync(jobRoot, { recursive: true })
  const logPath = path.join(traj, 'training_log.jsonl')

  afterAll(() => {
    rmSync(traj, { recursive: true, force: true })
  })

  const mkJob = (
    id: string,
    opts: { claimed?: boolean; result?: boolean; payload?: boolean; it?: number },
  ) => {
    const jd = path.join(jobRoot, id)
    mkdirSync(jd, { recursive: true })
    if (opts.payload !== false) writeFileSync(path.join(jd, 'payload.tar.xz'), 'x')
    writeFileSync(path.join(jd, 'manifest.json'), JSON.stringify({ it: opts.it ?? 7 }))
    if (opts.claimed) writeFileSync(path.join(jd, 'claimed'), '1')
    if (opts.result) mkdirSync(path.join(jd, 'result'), { recursive: true })
    return jd
  }

  const writeLedger = (events: Array<Record<string, unknown>>) => {
    writeFileSync(logPath, events.map((e) => JSON.stringify(e)).join('\n') + '\n')
  }

  const ageDir = (dir: string, ageMs: number) => {
    const t = (Date.now() - ageMs) / 1000
    utimesSync(dir, t, t)
  }

  it('无 result / 无 claimed 且超过 5min → 报警；含 claimed / result / 未超时 → null', () => {
    const now = Date.now()
    const stalled = mkJob('job-stalled', {})
    ageDir(stalled, 6 * 60_000)
    const claimed = mkJob('job-claimed', { claimed: true })
    ageDir(claimed, 10 * 60_000)
    const done = mkJob('job-done', { result: true })
    ageDir(done, 10 * 60_000)
    const fresh = mkJob('job-fresh', {})
    ageDir(fresh, 30_000)
    // 无账本 → 磁盘口径
    rmSync(logPath, { force: true })

    const hit = api.detectPpoQueueStall(jobRoot, now)
    expect(hit).not.toBeNull()
    expect(hit!.jobId).toBe('job-stalled')
    expect(hit!.it).toBe(7)
    expect(hit!.waitedSec).toBeGreaterThanOrEqual(5 * 60)

    // 只剩 claimed/done/fresh → 不报警
    rmSync(stalled, { recursive: true, force: true })
    expect(api.detectPpoQueueStall(jobRoot, now)).toBeNull()
    rmSync(claimed, { recursive: true, force: true })
    rmSync(done, { recursive: true, force: true })
    rmSync(fresh, { recursive: true, force: true })
  })

  it('账本 job_cancelled / job_completed：目录仍在盘也不告警（it30 悬空 job 回归）', () => {
    const now = Date.now()
    // 故障遗留：payload 在、无 claimed/result，但已 cancel_stale 作废
    const orphan = mkJob('job-orphan-cancelled', { it: 30 })
    ageDir(orphan, 20 * 60_000)
    const doneDir = mkJob('job-done-still-dir', { it: 29 })
    ageDir(doneDir, 20 * 60_000)
    const live = mkJob('job-live-stalled', { it: 32 })
    ageDir(live, 6 * 60_000)

    writeLedger([
      { event: 'job_pending', job_id: 'job-orphan-cancelled', it: 30, ts: 1 },
      { event: 'job_cancelled', job_id: 'job-orphan-cancelled', it: 30, ts: 2 },
      { event: 'job_pending', job_id: 'job-done-still-dir', it: 29, ts: 3 },
      { event: 'job_completed', job_id: 'job-done-still-dir', ts: 4 },
      { event: 'job_pending', job_id: 'job-live-stalled', it: 32, ts: 5 },
    ])

    const hit = api.detectPpoQueueStall(jobRoot, now)
    expect(hit).not.toBeNull()
    expect(hit!.jobId).toBe('job-live-stalled')
    expect(hit!.it).toBe(32)
    expect(hit!.jobId).not.toBe('job-orphan-cancelled')
  })

  it('空目录 / 不存在 → null', () => {
    expect(api.detectPpoQueueStall(path.join(jobRoot, 'nope'))).toBeNull()
  })
})
