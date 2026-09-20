/**
 * web-app-nodepills.test.ts — 节点 pill 行：健康度由**最近完成轮贡献数**判定 ·
 * 缓慢/离线不折叠 · 停用（含 local 0 槽）折叠 · 启停 toggle。
 *
 * 分层：src/web/app/panels/NodePills.tsx（健康度纯函数 = src/web/view/console-types.ts）
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 *
 * 2026-09-20 用户指令改写三态判据（原「ping 在线/慢/离线」→「健康/缓慢/离线」，
 * 贡献数取最近完成轮）——见 DECISIONS §2026-09-20-node-health-by-completed-round-contrib。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { LocalPill, NodeEditPill, NodePills } from '../src/web/app/panels/NodePills'
import { type NodeLocalView, type NodeView, nodeHealth } from '../src/web/view'

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

const mkLocal = (over: Partial<NodeLocalView>): NodeLocalView => ({
  id: 'local',
  slots: 3,
  lastContrib: 4,
  ...over,
})

const pillProps = (n: NodeView) => ({
  n,
  editing: false,
  draft: '',
  onEdit: () => {},
  onDraft: () => {},
  onSave: () => {},
  onToggle: () => {},
  onSmoke: () => {},
})

describe('nodeHealth（纯函数：贡献数 vs 并发数 → 健康/缓慢/离线）', () => {
  it('贡献 0（或无池数据 -1）→ 离线；≥ 并发数 → 健康；其间 → 缓慢', () => {
    expect(nodeHealth(0, 2)).toBe('offline')
    expect(nodeHealth(-1, 2)).toBe('offline')
    expect(nodeHealth(2, 2)).toBe('healthy')
    expect(nodeHealth(9, 2)).toBe('healthy')
    expect(nodeHealth(1, 2)).toBe('slow')
    expect(nodeHealth(3, 4)).toBe('slow')
  })

  it('并发数非法（0/负）按 1 收口——贡献 ≥1 即健康，不出现除零式的全缓慢', () => {
    expect(nodeHealth(1, 0)).toBe('healthy')
    expect(nodeHealth(1, -3)).toBe('healthy')
    expect(nodeHealth(0, 0)).toBe('offline')
  })
})

describe('NodePills（健康/缓慢/离线不折叠 · 停用折叠 · 启停 toggle）', () => {
  it('健康节点（贡献 ≥ 并发）：绿点，并发数**不带对钩**，贡献数露出', () => {
    const n = mkNode({ id: 'a1', concurrency: 2, lastContrib: 5 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--on')
    expect(html).not.toContain('✓')
    expect(html).toContain('tc-npill__contrib')
    expect(html).toContain('>5</span>')
    expect(html).toContain('>2</span>')
  })

  it('缓慢节点（0 < 贡献 < 并发）始终展开显示「缓慢」（琥珀点），不进折叠桶', () => {
    const n = mkNode({ id: 'a95', concurrency: 4, lastContrib: 1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).not.toContain('缓慢 1')
    expect(html).not.toContain('>离线<')
    expect(html).toContain('tc-dot--warn')
    expect(html).toContain('<b>a95</b>')
    expect(html).toContain('>缓慢</span>')
    expect(html).toContain('>4</span>') // 并发数恒出（判据：贡献 vs 并发）
    expect(html).toContain('>1</span>') // 判据本身也要看得见
  })

  it('贡献 0 → 离线（红点）——**ping 通也照样离线**：健康度不看 ping，只看上一轮交没交活', () => {
    const n = mkNode({ id: 'a98', online: true, lastContrib: 0, concurrency: 3 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</span>')
    expect(html).not.toContain('离线 1')
    expect(html).toContain('<span class="tc-muted">0</span>')
  })

  it('无池数据（-1）：离线 + 「—」（不是 0：从没结算过 ≠ 这一轮没交活）', () => {
    const n = mkNode({ id: 'a99', online: false, lastContrib: -1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</span>')
    expect(html).toContain('—')
  })

  it('停用节点：灰点（tc-dot--empty），文案「停用」，与离线红点区分', () => {
    const n = mkNode({ id: 'a97', enabled: false, online: null })
    const pill = renderToString(h(NodeEditPill, pillProps(n)))
    expect(pill).toContain('tc-dot--empty')
    expect(pill).not.toContain('tc-dot--dead')
    expect(pill).toContain('>停用</span>')
    expect(pill).toContain('tc-npill--disabled')
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

  it('缓慢/离线 pill 的开关同样在场（可点；停用 pill 上是「启用」）', () => {
    for (const over of [{ lastContrib: 1 }, { lastContrib: 0 }]) {
      const pill = renderToString(h(NodeEditPill, pillProps(mkNode({ id: 'a2', ...over }))))
      expect(pill).toMatch(/role="switch"/)
      expect(pill).toContain('aria-label="停用 a2"')
      expect(pill).toContain('tc-switch--on')
    }
    const off = renderToString(
      h(NodeEditPill, pillProps(mkNode({ id: 'a2', enabled: false, online: null }))),
    )
    expect(off).toContain('aria-label="启用 a2"')
    expect(off).not.toContain('tc-switch--on')
  })
})

describe('NodePills · local（本机直跑）', () => {
  it('slots > 0 且上一轮有贡献：行内 pill（绿点 + `3槽` + 贡献数）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        local: mkLocal({ slots: 3, lastContrib: 4 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('tc-npill--local')
    expect(html).toContain('>local<')
    expect(html).toContain('3槽')
    expect(html).toContain('tc-dot--on')
    expect(html).not.toContain('停用 1')
  })

  it('slots > 0 但上一轮零贡献：同节点口径判「离线」（本机直跑也是一个 rollout 执行面）——槽位仍照出', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        local: mkLocal({ slots: 3, lastContrib: 0 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</span>')
    expect(html).toContain('3槽')
  })

  it('slots > 0 但贡献不足（0 < 2 < 3）：缓慢——槽位与状态词并列（§361⑤ 槽位展示不回退）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        local: mkLocal({ slots: 3, lastContrib: 2 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('3槽')
    expect(html).toContain('tc-dot--warn')
    expect(html).toContain('>缓慢</span>')
  })

  it('slots = 0（直跑未启用）：行内不出 pill，只并入「停用」折叠计数', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [mkNode({ id: 'a1', lastContrib: 5 })],
        local: mkLocal({ slots: 0, lastContrib: 0 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).not.toContain('tc-npill--local')
    expect(html).not.toContain('>local<')
    expect(html).toContain('停用 1')
    expect(html).toContain('aria-expanded') // 折叠计数按钮在（展开态才渲染 local pill）
  })

  it('slots = 0 + 停用节点：折叠计数把两者加在一起', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [mkNode({ id: 'a2', enabled: false, online: null })],
        local: mkLocal({ slots: 0 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('停用 2')
  })

  it('折叠态的 local pill（off）：灰点 + 「停用」，并写明是 rl.local_slots = 0', () => {
    const pill = renderToString(
      h(LocalPill, { local: mkLocal({ slots: 0, lastContrib: 0 }), off: true }),
    )
    expect(pill).toContain('tc-dot--empty')
    expect(pill).toContain('>停用</span>')
    expect(pill).toContain('local_slots = 0')
    expect(pill).not.toContain('tc-dot--on')
  })
})
