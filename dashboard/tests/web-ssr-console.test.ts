/**
 * web-ssr-console.test.ts — SSR 渲染包含外壳/路由页面与转义安全
 *
 * 分层：src/web/render.tsx（renderConsolePage）
 *
 * 自 training-console.test.ts 按 src 分层拆出；另含 training-console-preact.test.ts
 * 的同模块 describe（2026-09-14 补回：首轮拆分文件被第二轮同名文件覆盖，1 个用例丢失）。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 *
 * 2026-09-20（docs/dashboard-redesign.md P0）：页面改为「应用外壳 + 路由化页面」。
 * 本文件同步两处：
 *   ① 断言外壳结构（tc-side / tc-top）取代已随课程选择器迁移而消失的 tc-topbar__course；
 *   ② 新增**路由断言**（page → 渲染哪一页），首屏由服务端 stamp 决定（§5.1）。
 */

import { api, render } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

/**
 * 只取 `#root` 内的 SSG 渲染体。
 *
 * 为什么必须切：整个 `theme.css` 被内联进 `<style>`，所以 `toContain('tc-hero')` 之类
 * 的类名断言**永远为真**（样式表里就有这个串）——`not.toContain('tc-side')` 同理永远为假。
 * 切出渲染体后，类名断言才真的在断言 DOM。
 */
function body(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

describe('console SSR renderConsolePage', () => {
  it('渲染外壳 + 总览区块、动作按钮、开关键与转义', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    const dom = body(html)
    expect(html).toContain('炼丹炉')
    expect(dom).toContain('tc-side') // 应用外壳：侧栏
    expect(dom).toContain('tc-top') // 应用外壳：顶栏
    // 组件小卡：名称渲染体是 `<b>{key}</b>`（标签在 aria-label 上）。
    // ⚠ 别再断言 `tc-cc__name`——那个类名只存在于 CSS 里，断言它等于断言样式表，
    // 永远为真（本文件切出 #root 后才暴露出来）。
    expect(dom).toContain('self-node (采集节点)')
    expect(dom).toContain('tc-hero') // 训练状态 hero（总览页）
    expect(dom).toContain('tc-comps') // 组件小卡
    expect(dom).toContain('tc-row') // 统一行原语（节点行等）
    expect(dom).toContain('训练状态') // hero aria-label
    // 详情已路由化：首帧不渲染模态抽屉 / 弹窗（这是回归闸——抽屉已退役，别让它回来）
    expect(html).not.toContain('<aside class="tc-drawer"')
    expect(html).not.toContain('class="tc-modal-mask"')
    // 无原始 <script> 注入风险：SSR 输出经 preact 转义
    expect(html).not.toContain('<script>alert')
  })

  it('首屏包含标题/图标/引导载荷/卡结构，无整页 reload，无 script 注入', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('<title>炼丹炉</title>')
    expect(html).toContain('rel="icon"')
    expect(html).toContain('window.__INITIAL__')
    expect(html).toContain('自动（最近活跃课程）')
    expect(html).toContain('tc-cc') // 组件小卡驱动
    expect(html).toContain('/app.js')
    expect(html).not.toContain('location.reload()') // 无整页 reload（§4.5）
    expect(html).not.toContain('<script>alert')
    // 课程选择器在侧栏（不再是顶栏中部——问题 C3 的解法）
    const dom = body(html)
    expect(dom).toContain('id="courseSel"')
    expect(dom).toContain('tc-side')
    // 本机伪节点（worker_server）**不在受管组件里**：冒烟预演自起自停，
    // 永远不该在渲染体里出现它的卡片（键名会以 `<b>workerServe</b>` 形式出现）。
    expect(dom).not.toContain('workerServe')
  })

  it('服务端按 page 渲染对应页面：全览出 hero、指标页出完整指标表、节点页不重复 hero', async () => {
    const s = await api.buildStateView()
    const overview = body(render.renderConsolePage(s, { page: 'overview' }))
    expect(overview).toContain('tc-hero')
    expect(overview).toContain('总览') // 顶栏页面标题

    const metrics = body(render.renderConsolePage(s, { page: 'metrics' }))
    expect(metrics).toContain('指标') // 顶栏页面标题
    expect(metrics).not.toContain('tc-hero') // 指标页不重复渲染总览 hero
    expect(metrics).not.toContain('tc-comps') // 也不渲染总览的组件卡

    const nodes = body(render.renderConsolePage(s, { page: 'nodes' }))
    expect(nodes).toContain('节点')
    expect(nodes).not.toContain('tc-hero')

    const wire = body(render.renderConsolePage(s, { page: 'wire' }))
    expect(wire).toContain('传输')
    expect(wire).not.toContain('tc-hero')
  })

  it('引导载荷带 page：客户端 hydrate 与 SSR 首帧同页（无闪跳）', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s, { page: 'nodes' })
    expect(html).toContain('"page":"nodes"')
    // 缺省 = 总览（测试与旧调用方直接渲染时不必知道路由）
    expect(render.renderConsolePage(s)).toContain('"page":"overview"')
  })
})
