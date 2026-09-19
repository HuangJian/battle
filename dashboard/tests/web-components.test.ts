/**
 * web-components.test.ts — DS-E3 单 panel 崩溃不整页断 + §361 icon 复制键 / 云端 endpoint 截断与复制 / local pill
 *
 * 分层：src/web/components/PanelErrorBoundary.tsx + CopyButton.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { renderConsolePage } from '../src/web/render'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { CopyButton } from '../src/web/components/CopyButton'
import { PanelErrorBoundary } from '../src/web/components/PanelErrorBoundary'
import { shortUrl, type ConsoleStateView, type PushFleetProbe } from '../src/web/view'

/** 造一个机群级执行面探针（`stateView.pushFleet` 的形状）。 */
function fleet(patch: Partial<PushFleetProbe> = {}): PushFleetProbe {
  return {
    mode: 'hub-dispatch',
    text: 'hub 中介派发',
    detail: '',
    nodes: 1,
    hubPush: true,
    hubUrl: 'http://127.0.0.1:8787',
    probes: [{ id: 'gpu1', url: 'https://gpu.example', healthy: true }],
    ...patch,
  }
}

/** 构造一个带 trainingLoop 卡（+可选执行面徽章）的整页状态，SSR 渲染成 HTML。 */
function pageWithPushTarget(pushFleet: PushFleetProbe | null): string {
  return renderConsolePage({
    time: 't',
    course: 'c',
    courses: [],
    components: [
      {
        key: 'trainingLoop',
        label: '训练循环',
        status: 'running',
        pid: 1,
        url: null,
        course: 'c',
        mode: 'remote',
        healthy: true,
        log: null,
        logTail: [],
        busy: false,
      },
    ],
    nodes: [],
    modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
    metrics: { available: false, iters: [] },
    phase: { phase: 'idle' as const, sinceMs: null, iter: null },
    localNode: null,
    pushFleet,
  } as ConsoleStateView)
}

describe('PanelErrorBoundary SSR 隔离（DS-E3）', () => {
  it('单 panel render 崩溃 → 错误占位 + 兄弟节点正常，不整页断', () => {
    // 静态 ESM import（与 render.tsx 同一模块实例；options.errorBoundaries 已由其置位）
    const Boom: () => import('preact').ComponentChildren = () => {
      throw new Error('boom-panel')
    }
    const html = renderToString(
      h(
        'div',

        null,
        h(PanelErrorBoundary, null, h(Boom, null)),
        h('section', { id: 'sibling' }, '存活'),
      ),
    )
    expect(html).toContain('该卡片加载失败：boom-panel')
    expect(html).toContain('存活')
    expect(html).toContain('id="sibling"')
  })
})

describe('§361：icon 复制键 / cloudflared endpoint 截断与复制 / local pill', () => {
  it('CopyButton icon 模式：按钮无可见「复制」文字（仅 ⧉/✓；语义走 title/aria）', () => {
    const html = renderToString(
      h(CopyButton, { text: 'https://abc.trycloudflare.com', label: '隧道', icon: true }),
    )
    expect(html).toContain('⧉')
    expect(html).not.toContain('⧉ 复制')
    expect(html).toContain('复制隧道') // title/aria 仍有复制语义
    // 非 icon 模式仍带「复制」字样
    const plain = renderToString(h(CopyButton, { text: 'x' }))
    expect(plain).toContain('复制')
  })

  it('cloudflared endpoint：复制钮显示 url 字样（不展示完整 URL 字符串）', () => {
    const longUrl = 'https://abc-def.trycloudflare.com/abcdefgh/%2F%2F%2F%2F%2F'
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [
        {
          key: 'cloudflared',
          label: 'cloudflared (入站隧道)',
          status: 'running',
          pid: 1,
          url: longUrl,
          course: null,
          mode: null,
          healthy: true,
          log: null,
          logTail: [],
          busy: false,
          secret: 'tok_123456789012345',
        },
      ],
      nodes: [],
      modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      localNode: null,
    } as ConsoleStateView
    const html = renderConsolePage(s)
    expect(html).toContain('⧉ url')
    expect(html).toContain('⧉ key')
    expect(html).not.toContain(shortUrl(longUrl))
    // 可见组件区不得出现完整 URL（__INITIAL__ 脚本里的初始 state 另论）
    const body = html.replace(/<script[\s\S]*?<\/script>/g, '')
    expect(body).not.toContain('trycloudflare.com')
  })

  it('执行面徽章（机群级）：hub 派发 / 直推 / 等待拉取三态 + 探活汇总', () => {
    // hub 派发：hub 按队列推给登记节点
    const hub = pageWithPushTarget(fleet())
    expect(hub).toContain('class="tc-cc__push tc-cc__push--hub-dispatch"')
    expect(hub).toContain('hub→1 台')
    expect(hub).toContain('https://gpu.example') // 悬停详情留全量 URL

    // 直推：训练侧按登记顺序直连节点（hub_push 关 / hub 不可用）
    const direct = pageWithPushTarget(
      fleet({
        mode: 'direct-push',
        nodes: 2,
        probes: [{ id: 'g1', url: 'https://a', healthy: false }],
      }),
    )
    expect(direct).toContain('class="tc-cc__push tc-cc__push--direct-push"')
    expect(direct).toContain('直推→2 台·1 台不通')

    // 等待拉取：没有登记节点（谁在轮询 hub 谁就能领到活）
    const pull = pageWithPushTarget(fleet({ mode: 'pull', nodes: 0, probes: [] }))
    expect(pull).toContain('class="tc-cc__push tc-cc__push--pull"')
    expect(pull).toContain('dispatch→拉取')
  })

  it('组件卡分族（R3-3）：服务面（单例角色）在前、课程面在后，族内顺序稳定', () => {
    // scope 由服务端按账本槽位规则填（这里照抄真值：selfNode 单例；hub / 隧道 / **trainer** /
    // **本机 worker** 共享——后两者分别自 2026-09-19 的 R3-5 与共享 worker 收敛起，各一个进程
    // 服务所有课程；本机伪节点同日退出受管组件）。
    // 另携一个**合成**的课程面键：当前已无按课程的卡片组件，而这一族的渲染路径仍要在 SSR 上
    // 被真实走过（它是 scope 的函数，不是名单）。
    const scopes: Record<string, string> = {
      selfNode: 'singleton',
      hubServer: 'shared',
      cloudflared: 'shared',
      trainingLoop: 'shared',
      localWorker: 'shared',
      someCourseThing: 'course',
    }
    // 输入故意乱序：顺序必须是**分组算出来的**，不是渲染顺序碰巧
    const keys = [
      'localWorker',
      'someCourseThing',
      'cloudflared',
      'trainingLoop',
      'hubServer',
      'selfNode',
    ]
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: keys.map((key) => ({
        key,
        label: key,
        scope: scopes[key],
        status: 'stopped' as const,
        pid: null,
        url: null,
        course: 'c',
        mode: null,
        healthy: null,
        log: null,
        logTail: [],
        busy: false,
      })),
      nodes: [],
      modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      localNode: null,
    } as ConsoleStateView
    const html = renderConsolePage(s)
    // 两组标题都上屏（分组这件事本身要看得见，不能只靠间距）
    expect(html).toContain('服务面 · 单例')
    expect(html).toContain('课程面 · 按课程')
    expect(html).toContain('data-family="service"')
    expect(html).toContain('data-family="course"')
    // 服务面在前、课程面在后；族内顺序：agent → hub → 隧道 → trainer → 本机 worker
    const order = ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop', 'localWorker']
    let prev = -1
    for (const k of order) {
      const idx = html.indexOf(`>${k}<`)
      expect(idx, k).toBeGreaterThan(prev)
      prev = idx
    }
    // 课程面族在服务面之后（合成键所在那族；它本身也必须在卡行里出现过）
    expect(html.indexOf('>someCourseThing<')).toBeGreaterThan(html.indexOf('>localWorker<'))
    // 作用域徽章：共享四个（hub / 隧道 / trainer / 本机 worker）+ 单例一个（selfNode）；
    // 按课程不挂标签（组标题已说）。断言整段 class 属性而不是子串——页面里内联了整份
    // theme.css，类名本身也会出现。
    expect(html.match(/class="tc-cc__scope tc-cc__scope--shared"/g)).toHaveLength(4)
    expect(html.match(/class="tc-cc__scope tc-cc__scope--singleton"/g)).toHaveLength(1)
  })

  it('cloudflared 进程在但 hub 不通 → 黄点（healthy=false）', () => {
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [
        {
          key: 'cloudflared',
          label: 'cloudflared (入站隧道)',
          status: 'running' as const,
          pid: 9,
          url: 'https://x.trycloudflare.com',
          course: 'c',
          mode: null,
          healthy: false,
          log: null,
          logTail: [],
          busy: false,
          secret: 'tok',
        },
      ],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      localNode: null,
    } as ConsoleStateView
    const html = renderConsolePage(s)
    // 组件 pill 上的状态点必须是黄（CSS 里仍有 .tc-dot--on 规则，不能整页 not.toContain）
    expect(html).toMatch(/cloudflared[\s\S]{0,200}tc-dot--warn/)
    expect(html).not.toMatch(/cloudflared[\s\S]{0,200}tc-dot--on/)
  })

  it('未配置 push 目标 → 卡片不出徽章', () => {
    expect(pageWithPushTarget(null)).not.toContain('class="tc-cc__push')
  })

  it('local pill：只读展示（槽位 + 上轮贡献）', () => {
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [],
      nodes: [],
      modes: { stream: 0, doubleBuffer: 0, precollectEarly: 0 },
      metrics: { available: false, iters: [] },
      phase: { phase: 'idle' as const, sinceMs: null, iter: null },
      localNode: { id: 'local', slots: 3, lastContrib: 2 },
    } as ConsoleStateView
    const html = renderConsolePage(s)
    expect(html).toContain('tc-npill--local')
    expect(html).toContain('>local<')
    expect(html).toContain('3槽')
    expect(html).toContain('本机直跑')
  })
})
