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
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

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

/** 递归收集 `src/web` 下的所有 `.tsx`（用于「类名不得复活」的全量扫描）。 */
function webComponents(dir = 'src/web', out: string[] = []): string[] {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) webComponents(p, out)
    else if (e.name.endsWith('.tsx')) out.push(p)
  }
  return out
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
    // KPI 条（P2b）：总览首屏第一块（告警坞之下、趋势之上），六格齐全
    expect(dom).toContain('aria-label="关键指标"')
    for (const label of ['采样胜率', 'eval 胜率', '当前阶段', '在训课程', '算力', '队列']) {
      expect(dom).toContain(`>${label}</span>`)
    }
    // 详情已路由化：首帧不渲染模态抽屉 / 弹窗（这是回归闸——抽屉已退役，别让它回来）
    expect(html).not.toContain('class="tc-modal-mask"')

    // 组件卡 → 各自日志页（2026-09-20 P3c：总览底部那个「日志入口全集」面板已下线）。
    // 这是**能力保全闸**：面板下线前本仓没有一个断言盯着「每张卡能去自己的日志页」，
    // 卡上的链接再被删掉也不会有测试变红——最后一条路径会默默消失。
    // 链接分两个分支，各证各的（fixture 里没有运行中的进程，running 分支只能查源）：
    //   ① 渲染级：退出且有错的组件，⚠ 入口按 key 指向自己的 /log/<key>；
    //   ② 源文件级：running 分支的 ≡ 入口同样带 href —— 那是「正在跑」的组件读日志的路径。
    const logHrefs = [...dom.matchAll(/href="\/log\/([A-Za-z0-9_-]+)/g)].map((m) => m[1])
    expect(logHrefs).toContain('hubServer') // fixture：exited + error ⇒ ⚠ 入口
    expect(logHrefs).toContain('trainingLoop')
    // 一个进程都没跑时，侧栏「日志」是唯一入口——它必须一直在（否则日志页彻底不可达）。
    expect(dom).toContain('>日志</span>')
    const cardsSrc = readFileSync('src/web/app/panels/ComponentCards.tsx', 'utf8')
    expect(cardsSrc).toContain('aria-label={`日志 ${c.label}`}')
    // exited ⚠ + running ≡ = 恰好两处；少一处即某个分支被拆掉。
    expect(cardsSrc.match(/href=\{logHref\}/g)?.length).toBe(2)
    // 退役的面板不得复活（它的唯一产物是这些）：
    expect(dom).not.toContain('日志 →')
    expect(dom).not.toContain('tc-preset')
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
    expect(overview).toContain('aria-label="关键指标"') // KPI 条只属总览

    const metrics = body(render.renderConsolePage(s, { page: 'metrics' }))
    expect(metrics).toContain('指标') // 顶栏页面标题
    expect(metrics).not.toContain('tc-hero') // 指标页不重复渲染总览 hero
    expect(metrics).not.toContain('tc-comps') // 也不渲染总览的组件卡
    expect(metrics).not.toContain('aria-label="关键指标"') // KPI 条不进详情页

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

  it('已退役的抽屉类名不得复活（markup / 样式表规则 / 组件三通道）', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    const dom = body(html)
    // ① markup：`dom` 是 `#root` 切片，**不含** head 里的内联 <style>，所以这一条只查 DOM。
    expect(dom).not.toContain('tc-drawer')
    // ② 样式表：不得再有 `.tc-drawer*` **规则**。只锚行首选择器——注释里保留旧名（说明改名史）
    //    是故意的，而 theme.css 是**整体内联**进 SSR 的（见本文件头注），连注释都会随页面发出：
    //    所以「字符串为 0」这条断言会把自己的历史注也判红，必须分通道、按语义查。
    expect(readFileSync('src/web/theme.css', 'utf8')).not.toMatch(/^\.tc-drawer/m)
    // ③ 组件：任何 .tsx 都不得再引用它（P3 的唯一复用者是两个面板，已改名 tc-panelbody）。
    //    比原先只查 `<aside class="tc-drawer"` 强：换个标签、写进 className 字符串拼接都会漏。
    const hits = webComponents().filter((f) => readFileSync(f, 'utf8').includes('tc-drawer'))
    expect(hits).toEqual([])
    // 前提闸：内联样式表确实在 html 里（否则 ② 之外的类名断言会退化成永真 —— 见 #root 切片注）
    expect(html).toContain('.tc-panelbody')
  })
})
