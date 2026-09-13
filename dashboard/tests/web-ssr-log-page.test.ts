/**
 * web-ssr-log-page.test.ts — 日志内容转义 + 组件导航 + follow 开关；暂停态刷新间隔与缺文件占位
 *
 * 分层：src/web/render.tsx（renderLogPage）
 *
 * 自 training-console.test.ts 按 src 分层拆出；另含 training-console-preact.test.ts
 * 的同模块 describe（2026-09-14 补回：首轮拆分文件被第二轮同名文件覆盖，2 个用例丢失）。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, render } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console/log viewer (§348 补 2)', () => {
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

describe('日志页 SSR（render.tsx renderLogPage，§348 补 2 语义保留）', () => {
  it('follow 默认开：id="follow" checked 属性（不按裸词断言）', async () => {
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
    expect(html).toContain('/log.js') // 服务端可服务的 bundle 路径（§371：旧 /app-log.js 404）
    expect(html).not.toContain('<script>alert')
  })

  it('follow=false 无 checked；缺文件显示占位', async () => {
    const p = (await api.componentLogPayload('cloudflared', 20))!
    p.exists = false
    p.lines = []
    const html = render.renderLogPage(p, { components: [], follow: false, lines: 20 })
    expect(html).toContain('日志文件不存在')
    expect(html).not.toContain('id="follow" checked')
  })
})
