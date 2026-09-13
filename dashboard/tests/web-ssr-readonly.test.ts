/**
 * web-ssr-readonly.test.ts — 只读视图 SSR：角标 + 横幅、动作按钮不禁用、hydrate 安全、课程 select 与训练中标签、tc. 前缀白名单清理
 *
 * 分层：src/web/render.tsx + src/web/app/**
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import path from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { api, render, view } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import type { ConsoleStateView } from '../src/web/view'

describe('console 局域网只读边界（§…：LAN 查看 / localhost 控制）', () => {
  it('cloudflared token 行：局域网只读与回环同权展示 + 复制键（只读是动作边界，不是数据边界）', async () => {
    const base = await api.buildStateView()
    // 只读 SSR 与正常 SSR 一样渲染 token 行与复制键（secret 经 buildStateView 透传）
    for (const readOnly of [true, false]) {
      const html = render.renderConsolePage({ ...base, readOnly })
      expect(html).toContain('aria-label="复制key"') // cloudflared token 复制键
      expect(html).toContain('⧉ key') // 按钮文案即 key 字样
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
      path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'app.tsx'),
      path.join(DASHBOARD_ROOT, 'src', 'web', 'app', 'panels', 'Hero.tsx'),
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
