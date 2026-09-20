/**
 * web-app-nodepills.test.ts — 慢 / 离线不折叠 · 停用折叠 · 启停 toggle · 展开并发编辑
 *
 * 分层：src/web/app/panels/NodePills.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变。
 *
 * 2026-09-20（docs/dashboard-redesign.md P1）：行迁移到 `StatusRow` 原语，类名随迁移更新
 * （`tc-npill` → `tc-row`，`tc-npill--disabled` → `tc-row--off`）；同时新增一条
 * 「展开并发编辑」用例——旧交互是「点整行把 chip 换成编辑表单」（行形状会跳变），
 * 新交互是展开行下方详情。每条旧用例的意图都保留。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { NodePills, NodeRow } from '../src/web/app/panels/NodePills'
import { type NodeView } from '../src/web/view'

const mkNode = (over: Partial<NodeView>): NodeView => ({
  id: 'a1',
  url: 'http://a1',
  gpuPush: false,
  enabled: true,
  concurrency: 2,
  online: true,
  codeHash: null,
  cpus: 8,
  busy: false,
  lastContrib: 3,
  slow: false,
  ...over,
})

/** 行级渲染（绕过「停用行默认折叠」，与旧用例直接渲染 NodeEditPill 同法）。 */
function rowHtml(n: NodeView, open = false, readOnly = false): string {
  return renderToString(
    h(NodeRow, {
      n,
      open,
      draft: String(n.concurrency),
      readOnly,
      onToggle: () => {},
      onDraft: () => {},
      onSave: () => {},
      onToggleEnabled: () => {},
      onSmoke: () => {},
    }),
  )
}

describe('NodePills（慢/离线不折叠 · 停用折叠 · 启停 toggle）', () => {
  it('慢节点始终展开显示「慢」（琥珀菱形），不进折叠桶', () => {
    const n = mkNode({ id: 'a95', online: false, slow: true, lastContrib: 5 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).not.toContain('慢 1')
    expect(html).not.toContain('>离线<')
    expect(html).toContain('tc-dot--warn')
    expect(html).toContain('<b>a95</b>')
    expect(html).toContain('>慢</span>')
    // 语义 modifier：慢 ≠ 离线 ≠ 停用（三重区分里的行级一重）
    expect(html).toContain('tc-row--slow')
  })

  it('停用节点：灰环（tc-dot--empty），文案「停用」，与离线红方区分', () => {
    const n = mkNode({ id: 'a97', enabled: false, online: null })
    const row = rowHtml(n)
    expect(row).toContain('tc-dot--empty')
    expect(row).not.toContain('tc-dot--dead')
    expect(row).toContain('>停用</span>')
    expect(row).toContain('tc-row--off')
  })

  it('真离线（无近期贡献）：始终展开显示红方 + 「离线」', () => {
    const n = mkNode({ id: 'a98', online: false, slow: false, lastContrib: -1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</span>')
    expect(html).not.toContain('离线 1')
  })

  it('停用节点默认折叠成计数行（不进主行区），折叠行可展开', () => {
    const off = mkNode({ id: 'a97', enabled: false, online: null })
    const on = mkNode({ id: 'a1' })
    const html = renderToString(
      h(NodePills, { nodes: [on, off], onAction: () => {}, onMore: () => {} }),
    )
    expect(html).toContain('停用 1') // 计数行
    expect(html).not.toContain('<b>a97</b>') // 折叠态下不渲染该行
    expect(html).toContain('<b>a1</b>') // 在线行始终在
    // 折叠行是 aria-expanded 控件（不是装饰性文本）
    expect(html).toContain('aria-expanded="false"')
  })

  it('启停为 toggle 开关：行上直接渲染 role=switch（aria 名 = 目标动作）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [mkNode({ id: 'a1', enabled: true })],
        onAction: () => {},
        onMore: () => {},
      }),
    )
    // aria 压缩后按属性片段断言（preact-render-to-string 省略 boolean 值的 ="true"）
    expect(html).toMatch(/role="switch"/)
    expect(html).toContain('aria-checked="true"')
    // aria 名描述目标动作（enabled 行上的开关点下去 = 停用）——接线契约：
    // Switch onClick → onChange(!checked) → onAction('setNodeEnabled', {id, enabled})
    expect(html).toContain('aria-label="停用 a1"')
    expect(html).toContain('tc-switch--on')
  })

  it('停用行的开关同样在场（重新启用即点开）', () => {
    const row = rowHtml(mkNode({ id: 'a2', enabled: false, online: null }))
    expect(row).toMatch(/role="switch"/)
    expect(row).toContain('aria-label="启用 a2"')
    expect(row).not.toContain('tc-switch--on')
  })

  it('展开行 = 并发编辑详情（旧交互「点整行把 chip 换成表单」已改为展开，行形状不跳变）', () => {
    const collapsed = rowHtml(mkNode({ id: 'a1', concurrency: 4 }))
    expect(collapsed).toContain('aria-expanded="false"')
    expect(collapsed).not.toContain('aria-label="a1 并发数"')

    const open = rowHtml(mkNode({ id: 'a1', concurrency: 4 }), true)
    expect(open).toContain('aria-expanded="true"')
    expect(open).toContain('aria-label="a1 并发数"')
    expect(open).toContain('value="4"') // 草稿初始值 = 当前并发
    expect(open).toContain('>保存</button>')
    expect(open).toContain('aria-label="冒烟 a1"')
    // 行本身仍在（同一行），开关仍在动作区——不是换了一个组件
    expect(open).toContain('<b>a1</b>')
    expect(open).toContain('aria-label="停用 a1"')
  })

  it('只读：行不渲染交互角色（无 role=button / tabIndex），开关仍在（服务端 403 兜底）', () => {
    const row = rowHtml(mkNode({ id: 'a1' }), false, true)
    expect(row).not.toContain('tabindex')
    expect(row).not.toContain('role="button"')
    expect(row).toContain('只读模式：节点编辑/冒烟仅限本机 localhost')
    expect(row).toMatch(/role="switch"/)
  })

  it('本机直跑行：槽位 + 上轮贡献（只读展示，不可展开）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        local: { id: 'local', slots: 3, lastContrib: 2 },
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('tc-row--local')
    expect(html).toContain('>local<')
    expect(html).toContain('3槽')
    expect(html).toContain('上轮 2')
  })
})
