/**
 * web-ssr-console.test.ts — SSR 渲染包含关键区块与转义安全
 *
 * 分层：src/web/render.tsx（renderConsolePage）
 *
 * 自 training-console.test.ts 按 src 分层拆出；另含 training-console-preact.test.ts
 * 的同模块 describe（2026-09-14 补回：首轮拆分文件被第二轮同名文件覆盖，1 个用例丢失）。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, render } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

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

describe('控制台 SSR（render.tsx renderConsolePage）', () => {
  it('首屏包含标题/锚点/卡结构，无整页 reload，无 script 注入', async () => {
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
    // 标题行置顶（§382：课程 select 并入标题行中部，指标 chips 已移除）+ 一屏仪表盘结构
    expect(html).toContain('tc-topbar__course')
    expect(html).toContain('tc-hero')
    expect(html).toContain('tc-comps')
    expect(html).toContain('tc-npill')
    // 本机伪节点（worker_server）**不在受管组件里**（2026-09-19 退出）：冒烟预演自起自停，
    // 永远不该出现在渲染体里（它没有卡片/账本/日志页入口）。
    expect(html).not.toContain('<span class="tc-cc__name">workerServe')
    // 详情抽屉 / 弹窗 SSR 首帧不渲染（tc-drawer 类名在 CSS，用渲染体判定）
    expect(html).not.toContain('<aside class="tc-drawer"')
    // 弹窗本体不渲染（勿用 '启动 TrainingLoop' 裸子串——它与未运行时训练卡启动键的
    // aria-label '启动 TrainingLoop (trainer)' 撞词，训练态一停就误报）
    expect(html).not.toContain('class="tc-modal-mask"')
  })
})
