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

  it('只读视图 SSR：readOnly=true 渲染只读角标 + 告警坞条目，动作按钮不禁用；false 不渲染', async () => {
    const base = await api.buildStateView()
    // 组件 busy 置空：pending/busy 锁与只读无关，避免 base 状态干扰禁用断言
    const clean = {
      ...base,
      components: base.components.map((c) => ({ ...c, busy: false })),
    }
    const html = render.renderConsolePage({ ...clean, readOnly: true })
    // 只读可见面（docs/dashboard-redesign.md §5.4）：
    //   ① 侧栏常驻锁徽标 `tc-lock`（不可关闭，取代此前可关闭横幅的常驻职责）
    //   ② 告警坞里的只读条目（可关闭，仅提示一次）—— P2b 前是独立横幅 `tc-banner--ro`
    expect(html).toContain('class="tc-lock"')
    // 告警圾条目：断言限定在坞内（整页还内联了 theme.css，裸词断言会假通过——
    // `class="tc-dock"` 只在标记里出现，`:root` 里的 `.tc-dock {` 不会匹配）。
    const dock = html.slice(html.indexOf('class="tc-dock"'), html.indexOf('class="tc-kpi"'))
    expect(dock).toContain('tc-dock__item tc-dock__item--info')
    expect(dock).toContain('tc-dock__icon')
    expect(dock).toContain('只读模式：')
    // 只读视图不禁用动作按钮（物理禁用会让组件区灰败破碎——只读是动作边界，不是按钮状态）：
    // 悬停提示 + 真点击由服务端 403 + flash 兜底，按钮保持正常外观可点击。
    // （正则避开 <style> 内联 CSS 里的 :disabled 选择器）
    expect(html).not.toMatch(/<button[^>]*disabled/)
    // 动作按钮带只读悬停提示（说明动作仅限本机）
    expect(html).toContain('title="只读模式：操作仅限本机 localhost"')
    const html2 = render.renderConsolePage({ ...clean, readOnly: false })
    expect(html2).not.toContain('class="tc-lock"')
    expect(html2).not.toContain('title="只读模式：操作仅限本机 localhost"')
    // 空坞不出现（不留空壳、也不再有「暂无告警」占位）——但 KPI 条仍在（两区通用）
    expect(html2).not.toContain('class="tc-dock"')
    expect(html2).toContain('class="tc-kpi"')
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

  it('课程 select：hub 运行中恒不禁用（单 hub 多课程——切课程只改查看目标）', async () => {
    // 2026-09-18 语义变更（原用例断言「本机 hub 运行中锁定」）：hub 已是一个进程托管 N 份
    // 账本的多课程调度器，进程级状态不再与「操作员在看哪门课」绑定——旧锁定保护（§367，
    // 当时 hub 按课程建 jobRoot/日志目录）已无对象；多课程并行下它反倒会把查看/切换锁死
    // （看不到其它在训课程）。故断言改为「任何来源、任何 hub 状态下都可用」。
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
    for (const readOnly of [true, false]) {
      const sel = selTag(render.renderConsolePage(mk(readOnly)))
      expect(sel).toContain('id="courseSel"') // 守卫：正则真匹配到 select（防空跑）
      expect(sel).not.toContain('disabled')
      expect(render.renderConsolePage(mk(readOnly))).not.toContain('hub 运行中，课程已锁定')
    }
  })

  it('课程 select：所有在训课程在选项里高亮（🔥 + （正在训练）），不止第一门；标签列出“还有在训”的课', async () => {
    const base = await api.buildStateView()
    const view2: ConsoleStateView = {
      ...base,
      course: 'viewB',
      courses: ['a', 'viewB', 'b'],
      trainingCourses: ['b', 'a'], // 服务端 stamp：在训多门（按名排序）
    }
    const html = render.renderConsolePage(view2)
    for (const c of ['a', 'b']) {
      expect(html).toContain(`🔥 ${c}（正在训练）`)
    }
    expect(html).not.toContain('🔥 viewB')
    // 查看课程不是任一门在训 → 标签列出全部在训课程（文案为「还有在训：」——
    // 「正在训练：」会被读成「当前查看的这门在训」，而标签列的恰恰是**别的课**）
    expect(html).toContain('还有在训：b、a')
    // 查看课程恰是其中一门 → 标签只提醒「别处还在跑」（正在看的那门由 🔥 标记）
    const same = render.renderConsolePage({ ...view2, course: 'a' })
    expect(same).toContain('还有在训：b')
    expect(same).not.toContain('还有在训：b、a')
  })

  it('训练中课程标签：trainingLoop 运行且课程 ≠ 查看课程时，select 后高亮「还有在训：<课程>」', async () => {
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
    expect(html).toContain('还有在训：trainA')
    // 查看课程 = 训练课程 → 无标签（正在看的就是训练的）
    const same = render.renderConsolePage(mk('trainA', 'trainA', true))
    expect(same).not.toContain('还有在训：')
    // trainingLoop 未运行 → 无标签
    const idle = render.renderConsolePage(mk('viewB', 'trainA', false))
    expect(idle).not.toContain('还有在训：')
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
