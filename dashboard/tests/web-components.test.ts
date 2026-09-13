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
import { shortUrl, type ConsoleStateView } from '../src/web/view'

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
