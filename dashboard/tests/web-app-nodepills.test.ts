/**
 * web-app-nodepills.test.ts — 慢 / 离线不折叠 · 停用折叠 · 启停 toggle
 *
 * 分层：src/web/app/panels/NodePills.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { NodeEditPill, NodePills } from '../src/web/app/panels/NodePills'
import { type NodeView } from '../src/web/view'

describe('NodePills（慢/离线不折叠 · 停用折叠 · 启停 toggle）', () => {
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

  it('慢节点始终展开显示「慢」（琥珀点），不进折叠桶', () => {
    const n = mkNode({ id: 'a95', online: false, slow: true, lastContrib: 5 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).not.toContain('慢 1')
    expect(html).not.toContain('>离线<')
    expect(html).toContain('tc-dot--warn')
    expect(html).toContain('<b>a95</b>')
    expect(html).toContain('>慢</span>')
  })

  it('停用节点：灰点（tc-dot--empty），文案「停用」，与离线红点区分', () => {
    const n = mkNode({ id: 'a97', enabled: false, online: null })
    const pill = renderToString(
      h(NodeEditPill, {
        n,
        editing: false,
        draft: '',
        off: true,
        onEdit: () => {},
        onDraft: () => {},
        onSave: () => {},
        onToggle: () => {},
        onSmoke: () => {},
      }),
    )
    expect(pill).toContain('tc-dot--empty')
    expect(pill).not.toContain('tc-dot--dead')
    expect(pill).toContain('>停用</span>')
    expect(pill).toContain('tc-npill--disabled')
  })

  it('真离线（无近期贡献）：始终展开显示红点 + 「离线」', () => {
    const n = mkNode({ id: 'a98', online: false, slow: false, lastContrib: -1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</span>')
    expect(html).not.toContain('离线 1')
  })

  it('启停为 toggle 开关：非编辑态 pill 上直接渲染 role=switch（aria 名 = 目标动作）', () => {
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
    // aria 名描述目标动作（enabled pill 上的开关点下去 = 停用）——接线契约：
    // Switch onClick → onChange(!checked) → onAction('setNodeEnabled', {id, enabled})
    expect(html).toContain('aria-label="停用 a1"')
    expect(html).toContain('tc-switch--on')
  })

  it('停用 pill 的开关同样在场（重新启用即点开）', () => {
    const n = mkNode({ id: 'a2', enabled: false, online: null })
    const pill = renderToString(
      h(NodeEditPill, {
        n,
        editing: false,
        draft: '',
        off: true,
        onEdit: () => {},
        onDraft: () => {},
        onSave: () => {},
        onToggle: () => {},
        onSmoke: () => {},
      }),
    )
    expect(pill).toMatch(/role="switch"/)
    expect(pill).toContain('aria-label="启用 a2"')
    expect(pill).not.toContain('tc-switch--on')
  })
})
