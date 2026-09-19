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
import { shortUrl, type ConsoleStateView, type PushTargetView } from '../src/web/view'

/** 构造一个带 trainingLoop 卡（+可选 push 执行面）的整页状态，SSR 渲染成 HTML。 */
function pageWithPushTarget(
  pushTarget: PushTargetView | null,
  opts: { mode?: 'pull' | 'push' | 'local' } = {},
): string {
  const mode = opts.mode ?? 'push'
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
        mode,
        healthy: true,
        log: null,
        logTail: [],
        busy: false,
      },
    ],
    nodes: [],
    modes: {
      trainerPpo: mode as 'pull' | 'push' | 'local',
      stream: 0,
      doubleBuffer: 0,
      precollectEarly: 0,
    },
    metrics: { available: false, iters: [] },
    phase: { phase: 'idle' as const, sinceMs: null, iter: null },
    localNode: null,
    pushTarget,
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
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
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

  it('push 执行面徽章：贴在 trainingLoop 卡上，区分本机 / 云机 / 未匹配', () => {
    // 绿色 = 本机 worker_server（active=正在用的执行面）
    const local = pageWithPushTarget({
      kind: 'local',
      url: 'http://127.0.0.1:8790',
      nodeId: 'local-push',
      healthy: true,
      active: true,
    })
    expect(local).toContain('class="tc-cc__push tc-cc__push--local"')
    expect(local).toContain('push→本机')
    expect(local).not.toContain('tc-cc__push--idle"') // active → 不降调
    expect(local).toContain('http://127.0.0.1:8790') // 悬停详情留全量 URL
    // 通信成功后「push」与「push→本机」重复——只留后者
    expect(local).not.toContain('class="tc-cc__mode"')

    // 蓝色 = 云 GPU 节点
    const cloud = pageWithPushTarget({
      kind: 'cloud',
      url: 'https://gpu.example',
      nodeId: 'gpu-push',
      healthy: true,
      active: true,
    })
    expect(cloud).toContain('class="tc-cc__push tc-cc__push--cloud"')
    expect(cloud).toContain('push→云机')
    // 通信成功后「push」与「push→云机」重复——只留后者（用户指令 2026-09-16）
    expect(cloud).not.toContain('class="tc-cc__mode"')
    expect(cloud).not.toContain('>push</b>')

    // 红色 = 指向 config 里不存在的节点（python 会回落 pull）――必须醒目；探测不通也上后缀
    const unresolved = pageWithPushTarget({
      kind: 'unresolved',
      url: 'https://ghost.example',
      nodeId: null,
      healthy: false,
      active: false,
    })
    expect(unresolved).toContain('class="tc-cc__push tc-cc__push--unresolved tc-cc__push--idle"')
    expect(unresolved).toContain('push→未匹配·不通')
    // 通信失败 → 仍保留「push」模式徽章（双徽章并存，便于看清当前模式）
    expect(unresolved).toContain('class="tc-cc__mode"')
  })

  it('push 目标未探通（healthy=null）→ 仍显示 push 模式徽章', () => {
    const html = pageWithPushTarget({
      kind: 'cloud',
      url: 'https://gpu.example',
      nodeId: 'gpu-push',
      healthy: null,
      active: false,
    })
    expect(html).toContain('push→云机')
    expect(html).toContain('class="tc-cc__mode"')
  })

  it('组件卡分族（R3-3）：服务面（单例角色）在前、课程面在后，族内顺序稳定', () => {
    // scope 由服务端按账本槽位规则填（这里照抄真值：selfNode 单例；hub / 隧道 / **trainer** /
    // **本机 worker** 共享——后两者分别自 2026-09-19 的 R3-5 与共享 worker 收敛起，各一个进程
    // 服务所有课程；workerServe 按课程但走节点行）。
    // 另携一个**合成**的课程面键：当前已无按课程的卡片组件，而这一族的渲染路径仍要在 SSR 上
    // 被真实走过（它是 scope 的函数，不是名单）。
    const scopes: Record<string, string> = {
      selfNode: 'singleton',
      hubServer: 'shared',
      cloudflared: 'shared',
      trainingLoop: 'shared',
      localWorker: 'shared',
      workerServe: 'course',
      someCourseThing: 'course',
    }
    // 输入故意乱序：顺序必须是**分组算出来的**，不是渲染顺序碰巧
    const keys = [
      'localWorker',
      'someCourseThing',
      'cloudflared',
      'workerServe',
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
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
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
    // 节点面组件（worker_server）不进卡片行：它渲染在节点行，两个入口 = 混淆
    expect(html).not.toContain('>workerServe<')
  })

  it('未配置 push 目标 → 卡片不出徽章', () => {
    expect(pageWithPushTarget(null)).not.toContain('class="tc-cc__push')
  })

  it('trainer 已切到 pull：config 残留的 push 目标不得再上卡（2026-09-16）', () => {
    const html = pageWithPushTarget(
      {
        kind: 'cloud',
        url: 'https://gpu.example',
        nodeId: 'gpu-push',
        healthy: false,
        active: false,
      },
      { mode: 'pull' },
    )
    expect(html).not.toContain('class="tc-cc__push')
    expect(html).not.toContain('push→云机')
    expect(html).toContain('class="tc-cc__mode"')
    expect(html).toContain('>pull</b>')
  })

  it('local pill：只读展示（槽位 + 上轮贡献）', () => {
    const s = {
      time: 't',
      course: 'c',
      courses: [],
      components: [],
      nodes: [],
      modes: { trainerPpo: 'pull' as const, stream: 0, doubleBuffer: 0, precollectEarly: 0 },
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
