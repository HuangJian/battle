/**
 * web-alert-dock.test.ts — 告警坞：条目派生 / 严重度排序 / 折叠切分 / SSR 结构。
 *
 * 分层：src/web/view/alerts.ts（纯函数）+ src/web/components/AlertDock.tsx（SSR 结构）
 *
 * 事故背景（P2b 收敛前）：首页最多同时堆 6 条**同权重**横幅（停机红 / 停机已恢复灰 /
 * 训练已完成灰 / PPO 排队超时红 / 课程编辑被拒红 / 只读蓝）。它们平级堆在 DNS 里，
 * 于是「红横幅排在蓝横幅下面」既是可能的、也是没人能管住的（顺序 = 代码书写顺序），
 * 而 6 条各占一行恰好在你最需要看主内容时把主内容推出首屏。
 *
 * 本用例钉住收敛后的三件事实：
 *   ① 每条横幅都变成了坞内条目，且**原文信息一字未删**（只是降了排版层级）；
 *   ② 排序由严重度决定，不由书写顺序决定；
 *   ③ 默认只出 2 条，其余折成「还有 N 条」；空的时候整坞不出现。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { AlertDock } from '../src/web/components/AlertDock'
import {
  ALERT_DOCK_DEFAULT_VISIBLE,
  alertDockSplit,
  alertDockSummary,
  buildAlerts,
  sortAlerts,
  type AlertInput,
  type AlertItem,
} from '../src/web/view'

/** 一件都不告警的基线输入（每条用例只改它关心的那一项）。 */
const clean: AlertInput = {
  cloudHalts: undefined,
  viewing: 'c1',
  acks: [],
  loopComplete: null,
  ppoQueueStall: null,
  courseEdit: null,
  readOnly: false,
  roDismissed: false,
  now: 1_700_000_000_000,
}

const item = (id: string, severity: AlertItem['severity']): AlertItem => ({
  id,
  severity,
  icon: '·',
  title: id,
  detail: '',
  role: 'status',
  actions: [],
})

describe('buildAlerts：七类告警各自成条目，原文不丢', () => {
  it('无任何告警 → 空数组（坞整块不出现，没有「暂无告警」占位）', () => {
    expect(buildAlerts(clean)).toEqual([])
  })

  it('停机中 → err 条，带「立即恢复」（主）+「知道了」', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T1', reason: '手动停机', status: 'halted' } },
    })
    expect(items.length).toBe(1)
    const a = items[0]!
    expect(a.severity).toBe('err')
    expect(a.role).toBe('alert')
    expect(a.title).toContain('停机中')
    expect(a.title).toContain('手动停机')
    // 动作两种语义必须分开：resume 真调 API，ack 只写本地
    expect(a.actions.map((x) => x.kind)).toEqual(['resume', 'ack'])
    expect(a.actions[0]!.act).toBe('cloud-resume')
    expect(a.actions[0]!.primary).toBe(true)
  })

  it('已恢复 → history 条（灰、温和留痕），只有「知道了」', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: {
        c1: {
          at: 'T1',
          reason: 'r',
          status: 'recovered',
          clearedAt: 'T2',
          clearReason: '恢复训练',
        },
      },
    })
    expect(items[0]!.severity).toBe('history')
    expect(items[0]!.role).toBe('status')
    expect(items[0]!.title).toContain('已恢复')
    expect(items[0]!.detail).toContain('恢复训练')
    expect(items[0]!.actions.map((x) => x.kind)).toEqual(['ack'])
  })

  it('停机条目的原文信息一字未删（收敛的是排版层级，不是信息量）', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T1', reason: 'r', status: 'halted' } },
    })
    const a = items[0]!
    // 旧横幅那句「停不掉则照常执行任务」是操作员决定要不要干预的依据，必须在。
    expect(a.detail).toContain('停不掉则照常执行任务')
    expect(a.detail).toContain('本地 hub/console 均正常')
  })

  it('ack 后不再出现（按事件身份：新一次停机是新 key）', () => {
    const halts = { c1: { at: 'T1', reason: 'r', status: 'halted' as const } }
    const first = buildAlerts({ ...clean, cloudHalts: halts })
    expect(first.length).toBe(1)
    const acked = buildAlerts({
      ...clean,
      cloudHalts: halts,
      acks: [first[0]!.actions[1]!.ackKey!],
    })
    expect(acked).toEqual([])
    // 新一次停机（at 变了）→ 新身份 → 重新出现
    const again = buildAlerts({
      ...clean,
      cloudHalts: { c1: { at: 'T2', reason: 'r', status: 'halted' } },
      acks: [first[0]!.actions[1]!.ackKey!],
    })
    expect(again.length).toBe(1)
  })

  it('别课的停机记录不进坞（停机不看当前课 = c6-chip 横幅关不掉那次事故）', () => {
    const items = buildAlerts({
      ...clean,
      viewing: 'c1',
      cloudHalts: { c2: { at: 'T1', reason: 'r', status: 'halted' } },
    })
    expect(items).toEqual([])
  })

  it('PPO 排队超时 → err，且写明等了多久 + job 身份', () => {
    const items = buildAlerts({
      ...clean,
      ppoQueueStall: { jobId: 'abcdef1234567890', waitedSec: 305, it: 37 },
    })
    expect(items[0]!.severity).toBe('err')
    expect(items[0]!.title).toContain('5 分5 秒')
    expect(items[0]!.detail).toContain('it37')
    // 没有 it 时回退 jobId 前缀（不能显示空身份）
    const noIter = buildAlerts({
      ...clean,
      ppoQueueStall: { jobId: 'abcdef1234567890', waitedSec: 305, it: null },
    })
    expect(noIter[0]!.detail).toContain('abcdef123456')
  })

  it('课程编辑被拒 → err；verdict 不是 rejected 时不出现', () => {
    const rejected = buildAlerts({
      ...clean,
      courseEdit: { verdict: 'rejected', fields: ['stages', 'difficulty'] },
    })
    expect(rejected[0]!.severity).toBe('err')
    expect(rejected[0]!.title).toContain('stages、difficulty')
    expect(rejected[0]!.detail).toContain('D14')
    for (const verdict of ['applied', 'restored'] as const) {
      expect(buildAlerts({ ...clean, courseEdit: { verdict, fields: [] } })).toEqual([])
    }
  })

  it('读写模式提示 → info（不是告警，排在错误之后）；关掉后不再出现', () => {
    const items = buildAlerts({ ...clean, readOnly: true })
    expect(items[0]!.severity).toBe('info')
    expect(items[0]!.actions[0]!.ackKey).toBe('ro-banner-dismissed')
    expect(buildAlerts({ ...clean, readOnly: true, roDismissed: true })).toEqual([])
  })

  it('训练完成停车 → history（是设计内停车，不是故障）', () => {
    // 传入的就是快照里的 `LoopComplete` 原形（at/reason/iters）——`AlertInput` 收的
    // 就是它，不在视图层再把类型窄化一遍。
    const items = buildAlerts({
      ...clean,
      loopComplete: { at: '2026-09-20T10:00:00Z', reason: 'iters 跑满', iters: 40 },
    })
    expect(items[0]!.severity).toBe('history')
    expect(items[0]!.title).toContain('iters 跑满')
    expect(items[0]!.actions).toEqual([])
  })

  it('六类全开 → 六条（收敛前是 6 个平级堆叠的横幅）', () => {
    const items = buildAlerts({
      ...clean,
      cloudHalts: {
        c1: { at: 'T1', reason: 'r', status: 'halted' },
        // 别课的停机不进坞（只取当前课）——它由课程矩阵的徽标承载
        c2: { at: 'T2', reason: 'r', status: 'halted' },
      },
      loopComplete: { at: 'T', reason: 'r', iters: 1 },
      ppoQueueStall: { jobId: 'j', waitedSec: 601, it: 1 },
      courseEdit: { verdict: 'rejected', fields: [] },
      readOnly: true,
    })
    expect(items.map((a) => a.id)).toEqual([
      'halt-c1',
      'loop-complete',
      'ppo-queue-stall',
      'course-edit-rejected',
      'read-only',
    ])
    // 严重度分布：3 err（停机 / 排队超时 / 编辑被拒）→ 1 info（只读）→ 1 history（训练完成）
    expect(sortAlerts(items).map((a) => a.severity)).toEqual([
      'err',
      'err',
      'err',
      'info',
      'history',
    ])
  })

  it('只读提示的字段缺失文案：未识别字段（不显示空括号）', () => {
    const items = buildAlerts({ ...clean, courseEdit: { verdict: 'rejected', fields: [] } })
    expect(items[0]!.title).toContain('未识别字段')
  })
})

describe('sortAlerts：严重度决定顺序，书写顺序只在同级内保持', () => {
  it('err → warn → info → history（与输入顺序无关）', () => {
    const input = [item('h', 'history'), item('i', 'info'), item('e', 'err'), item('w', 'warn')]
    expect(sortAlerts(input).map((a) => a.id)).toEqual(['e', 'w', 'i', 'h'])
  })

  it('同级保持输入顺序（稳定排序：同一局面重复渲染不跳位）', () => {
    const input = [item('a', 'err'), item('b', 'err'), item('c', 'warn'), item('d', 'warn')]
    expect(sortAlerts(input).map((a) => a.id)).toEqual(['a', 'b', 'c', 'd'])
  })

  it('空数组 / 单条不炸', () => {
    expect(sortAlerts([])).toEqual([])
    expect(sortAlerts([item('a', 'err')]).map((a) => a.id)).toEqual(['a'])
  })
})

describe('alertDockSplit：默认 2 条 + 「还有 N 条」', () => {
  const five = [
    item('h', 'history'),
    item('i', 'info'),
    item('e1', 'err'),
    item('w1', 'warn'),
    item('e2', 'err'),
  ]

  it('默认折叠：先按严重度排，再取前 2 条', () => {
    const { visible, hidden } = alertDockSplit(five, false)
    expect(ALERT_DOCK_DEFAULT_VISIBLE).toBe(2)
    expect(visible.map((a) => a.id)).toEqual(['e1', 'e2'])
    expect(hidden.map((a) => a.id)).toEqual(['w1', 'i', 'h'])
  })

  it('展开后全部可见（hidden 空）', () => {
    const { visible, hidden } = alertDockSplit(five, true)
    expect(visible.length).toBe(5)
    expect(hidden).toEqual([])
  })

  it('条数 ≤ cap 时不折叠（没有「还有 0 条」这种话）', () => {
    const two = [item('a', 'err'), item('b', 'err')]
    expect(alertDockSplit(two, false).hidden).toEqual([])
    expect(alertDockSplit([], false)).toEqual({ visible: [], hidden: [] })
  })

  it('alertDockSummary：有隐藏才给文案，否则空串', () => {
    expect(alertDockSummary(5, 3)).toBe('3 条未展开（共 5 条）')
    expect(alertDockSummary(2, 0)).toBe('')
  })
})

describe('AlertDock SSR 结构', () => {
  // 本仓 web 测试不用 JSX（`.ts` 夹具 + `h()`）——与 `web-components.test.ts` 同款。
  const renderDock = (items: AlertItem[], cap?: number): string =>
    renderToString(
      h(AlertDock, {
        items,
        cap,
        onAct: () => undefined,
        onAck: () => undefined,
      }),
    )

  it('空坞不渲染任何节点（不留空壳）', () => {
    expect(renderDock([])).toBe('')
  })

  it('条目：角色 / 严重度修饰类 / 结论 + 依据两行', () => {
    const html = renderDock([
      {
        id: 'x',
        severity: 'err',
        icon: '⚠',
        title: '一句话结论',
        detail: '依据与指引',
        role: 'alert',
        actions: [],
      },
    ])
    expect(html).toContain('class="tc-dock"')
    expect(html).toContain('tc-dock__item tc-dock__item--err')
    expect(html).toContain('role="alert"')
    expect(html).toContain('tc-dock__title">一句话结论')
    expect(html).toContain('tc-dock__detail">依据与指引')
  })

  it('默认只渲染 2 条 + 「还有 N 条 ▸」按钮（3 条时）', () => {
    const html = renderDock([item('a', 'err'), item('b', 'err'), item('c', 'err')])
    expect(html).toContain('tc-dock__more')
    expect(html).toContain('还有 1 条 ▸')
    expect(html.match(/tc-dock__item /g)?.length).toBe(2)
    // 折叠态是客户端 state、初值确定（SSR 与 hydrate 首帧一致）→ aria-expanded="false"
    expect(html).toContain('aria-expanded="false"')
  })

  it('条数 ≤ cap：不出现折叠按钮，也没有「收起」', () => {
    const html = renderDock([item('a', 'err'), item('b', 'warn')])
    expect(html).not.toContain('tc-dock__more')
    expect(html).not.toContain('收起')
  })

  it('动作渲染真按钮（主按钮带 tc-btn--primary）', () => {
    const html = renderDock([
      {
        id: 'x',
        severity: 'err',
        icon: '⚠',
        title: 't',
        detail: 'd',
        role: 'alert',
        actions: [
          {
            kind: 'resume',
            label: '立即恢复',
            act: 'cloud-resume',
            body: { course: 'c' },
            primary: true,
          },
          { kind: 'ack', label: '知道了', ackKey: 'k' },
        ],
      },
    ])
    expect(html).toContain('tc-btn tc-btn--sm tc-btn--primary')
    expect(html).toContain('>立即恢复</button>')
    expect(html).toContain('>知道了</button>')
  })
})

/**
 * ⚠ 测试盲区（与 `web-status-row.test.ts` 同款限制，必须让后来者知道）：
 * `preact-render-to-string` **丢弃全部事件处理器**（`h('button', {onClick}, 'x')`
 * 渲染出的 HTML 里没有 onClick），而本仓 web 测试**全是 SSR、无 DOM 夹具**。
 * 因此「点【还有 N 条】会展开 / 点动作会 invoke onAct」这类**交互**缺陷，任何断言都拦不住。
 * 这里能钉住的只有结构（条目数、类名、角色、折叠按钮的存在与文案）；
 * 交互正确性靠读代码评审。若要补，先引入 DOM 夹具（守零新依赖纪律，不要默默加）。
 */
