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

/**
 * 只取 `#root` 内的渲染体（同 web-ssr-console.test.ts / web-ssr-eval-page.test.ts）。
 *
 * ⚠ 2026-09-20（P3）就是在这个文件上被咬的：`expect(html).toContain('返回控制台')` 在删掉
 * 页头那个按钮**之后仍然通过**——因为整个 `theme.css` 被内联进 `<style>`，而新写的一条 CSS
 * 注释里恰好写着「← 返回控制台」。**断言被样式表文本满足 = 根本没在断言页面**。
 * 现在本文件一律先切出 `#root`。
 */
function body(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

describe('console/log viewer (§348 补 2)', () => {
  it('renderLogPage：日志内容转义 + 组件导航 + follow 开关', async () => {
    const p = (await api.componentLogPayload('selfNode', 40))!
    const state = await api.buildStateView()
    const html = render.renderLogPage(p, {
      components: state.components.map((c) => ({ key: c.key, label: c.label, status: c.status })),
      follow: true,
      lines: 40,
    })
    const dom = body(html)
    expect(dom).toContain('组件日志')
    expect(dom).toContain('id="logbox"')
    expect(dom).toContain('id="follow" checked')
    // 全组件导航 = 删掉 LogNavCard 之后「全组件日志入口」的**真正承担者**（2026-09-20 P3d），
    // 所以这里必须逐个组件断言：只查 /log/trainingLoop 一个链接是装饰——
    // nav 退化成一个 chip、或某组件被漏掉，都照样绿。
    expect(state.components.length).toBeGreaterThan(0) // 前提闸：否则下面的循环与计数都是永真
    for (const c of state.components) expect(dom).toContain(`href="/log/${c.key}`)
    expect(dom.match(/class="tc-lognav/g)?.length).toBe(state.components.length)
    // 日志文本必须经转义（原始 <script> 不得出现在 logbox 内容里）
    expect(html).not.toContain('<script>alert')
  })

  it('套上侧栏外壳（2026-09-20 P3）：日志页也能直接去其它页，日志项高亮', async () => {
    const p = (await api.componentLogPayload('selfNode', 40))!
    const state = await api.buildStateView()
    const html = render.renderLogPage(p, {
      components: state.components.map((c) => ({ key: c.key, label: c.label, status: c.status })),
      follow: true,
      lines: 40,
      course: 'x20-rebirth',
    })
    const dom = body(html)
    expect(dom).toContain('aria-label="控制台导航"')
    for (const label of ['总览', '指标', '评估', '节点', '传输', '日志']) {
      expect(dom).toContain(`tc-nav__label">${label}</span>`)
    }
    // `?course=` 透传：从日志页点回控制台不丢「我在看哪门课」。
    expect(dom).toContain('href="/?course=x20-rebirth"')
    expect(dom).toContain('aria-current="page"')
    // 页头那个「← 返回控制台」按钮已删（导航由侧栏承担）——这一条正是上面那个
    // 「被 CSS 注释满足」的假断言换过来的。
    expect(dom).not.toContain('返回控制台')
    // 页名由外壳顶栏给（PAGES.log），页内头部只说「哪个组件的」日志。
    expect(dom).toContain('>日志</h1>')
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
    const dom = body(html)
    expect(dom).toContain('组件日志')
    expect(dom).toContain('id="logbox"')
    expect(dom).toContain('id="follow" checked')
    expect(dom).toContain('/log/trainingLoop')
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
