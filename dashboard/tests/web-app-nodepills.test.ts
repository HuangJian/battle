/**
 * web-app-nodepills.test.ts — 健康 / 缓慢 / 离线不折叠 · 停用折叠 · 启停 toggle · 展开并发编辑
 *
 * 分层：src/web/app/panels/NodePills.tsx（健康度纯函数 = src/web/view/console-types.ts）
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变。
 *
 * 2026-09-20（docs/dashboard-redesign.md P1）：行迁移到 `StatusRow` 原语，类名随迁移更新
 * （`tc-npill` → `tc-row`，`tc-npill--disabled` → `tc-row--off`）；同时新增一条
 * 「展开并发编辑」用例——旧交互是「点整行把 chip 换成编辑表单」（行形状会跳变），
 * 新交互是展开行下方详情。每条旧用例的意图都保留。
 *
 * 同日（DECISIONS §2026-09-20-node-health-by-completed-round-contrib）：三态判据从
 * 「ping 在线/慢/离线」换成「**最近完成轮贡献 vs 并发数**」——健康 / 缓慢 / 离线；
 * 旧用例的意图（慢与离线不折叠、停用折叠、状态点与文案三重区分）逐条保留，
 * 触发条件改由贡献数给出。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { LocalRow, NodePills, NodeRow } from '../src/web/app/panels/NodePills'
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
  it('缓慢节点（0 < 贡献 < 并发）始终展开显示「缓慢」（琥珀菱形），不进折叠桶', () => {
    const n = mkNode({ id: 'a95', concurrency: 4, lastContrib: 1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).not.toContain('缓慢 1')
    expect(html).not.toContain('>离线</span>')
    expect(html).toContain('tc-dot--warn')
    expect(html).toContain('<b>a95</b>')
    expect(html).toContain('>缓慢</b>')
    // 判据的两个数都在场：并发数（值列）+ 最近完成轮贡献（元信息）
    expect(html).toContain('>4</span>')
    expect(html).toContain('上轮 1')
    // 语义 modifier：缓慢 ≠ 离线 ≠ 停用（三重区分里的行级一重）
    expect(html).toContain('tc-row--slow')
  })

  it('健康节点（贡献 ≥ 并发）：绿点 ●，并发数**不带对钩**，贡献数露出', () => {
    const n = mkNode({ id: 'a1', concurrency: 2, lastContrib: 5 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--on')
    expect(html).not.toContain('✓')
    expect(html).toContain('>2</span>')
    expect(html).toContain('上轮 5')
    expect(html).not.toContain('tc-row--slow')
  })

  it('贡献 0 → 离线（红方）——**ping 通也照样离线**：健康度只看上一轮交没交活', () => {
    const n = mkNode({ id: 'a98', online: true, concurrency: 3, lastContrib: 0 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</b>')
    expect(html).not.toContain('离线 1')
    expect(html).toContain('上轮 0')
  })

  it('无池数据（-1）：离线 + 「上轮 —」（不是「上轮 0」：从没结算过 ≠ 这一轮没交活）', () => {
    const n = mkNode({ id: 'a98', online: false, lastContrib: -1 })
    const html = renderToString(h(NodePills, { nodes: [n], onAction: () => {}, onMore: () => {} }))
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</b>')
    expect(html).toContain('上轮 —')
  })

  it('ping 慢（online=false/slow=true）但上一轮有贡献 ⇒ 按贡献判健康，不再标「慢」', () => {
    const n = mkNode({ id: 'a95', online: false, slow: true, concurrency: 2, lastContrib: 5 })
    const row = rowHtml(n)
    expect(row).toContain('tc-dot--on')
    expect(row).not.toContain('tc-dot--warn')
    expect(row).not.toContain('>缓慢</b>')
    expect(row).not.toContain('tc-row--slow')
  })

  it('停用节点：灰环（tc-dot--empty），文案「停用」，与离线红方区分', () => {
    const n = mkNode({ id: 'a97', enabled: false, online: null })
    const row = rowHtml(n)
    expect(row).toContain('tc-dot--empty')
    expect(row).not.toContain('tc-dot--dead')
    expect(row).toContain('>停用</span>')
    expect(row).toContain('tc-row--off')
  })

  it('停用节点默认折叠成计数行（不进主行区），折叠行可展开', () => {
    const off = mkNode({ id: 'a97', enabled: false, online: null })
    const on = mkNode({ id: 'a1' })
    const html = renderToString(
      h(NodePills, { nodes: [on, off], onAction: () => {}, onMore: () => {} }),
    )
    expect(html).toContain('停用 1') // 计数行
    expect(html).not.toContain('<b>a97</b>') // 折叠态下不渲染该行
    expect(html).toContain('<b>a1</b>') // 健康行始终在
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

  it('缓慢/离线/停用行的开关同样在场（都能就地启用/停用）', () => {
    for (const over of [
      { concurrency: 4, lastContrib: 1 },
      { lastContrib: 0 },
      { enabled: false, online: null },
    ]) {
      const row = rowHtml(mkNode({ id: 'a2', ...over }))
      expect(row).toMatch(/role="switch"/)
    }
    // 停用行上是「启用」（点开即参与派发）；缓慢/离线行上是「停用」
    expect(rowHtml(mkNode({ id: 'a2', enabled: false, online: null }))).toContain(
      'aria-label="启用 a2"',
    )
    expect(rowHtml(mkNode({ id: 'a2', lastContrib: 0 }))).toContain('aria-label="停用 a2"')
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
})

describe('NodePills · local（本机直跑）', () => {
  it('本机直跑行：槽位 + 上轮贡献（只读展示，不可展开）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        local: mkLocal({ slots: 3, lastContrib: 4 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('tc-row--local')
    expect(html).toContain('>local<')
    expect(html).toContain('3槽')
    expect(html).toContain('上轮 4')
    expect(html).toContain('tc-dot--on')
    expect(html).not.toContain('停用 1')
  })

  it('slots > 0 但上一轮零贡献：同节点口径判「离线」——槽位仍照出（本机直跑也是一个 rollout 执行面）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        local: mkLocal({ slots: 3, lastContrib: 0 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('tc-dot--dead')
    expect(html).toContain('>离线</b>')
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
    expect(html).toContain('>缓慢</b>')
    expect(html).toContain('tc-row--slow')
  })

  it('slots = 0（直跑未启用）：行内不出行，只并入「停用」折叠计数', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [mkNode({ id: 'a1', lastContrib: 5 })],
        local: mkLocal({ slots: 0, lastContrib: 0 }),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).not.toContain('tc-row--local')
    expect(html).not.toContain('>local<')
    expect(html).toContain('停用 1')
    expect(html).toContain('aria-expanded') // 折叠计数行在（展开态才渲染 local 行）
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

  it('折叠态的本机直跑行（off）：灰环 + 「停用」，并写明是 rl.local_slots = 0', () => {
    const row = renderToString(
      h(LocalRow, { local: mkLocal({ slots: 0, lastContrib: 0 }), off: true }),
    )
    expect(row).toContain('tc-dot--empty')
    expect(row).toContain('>停用</span>')
    expect(row).toContain('rl.local_slots = 0')
    expect(row).not.toContain('tc-dot--on')
  })
})
