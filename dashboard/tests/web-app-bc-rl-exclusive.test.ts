/**
 * web-app-bc-rl-exclusive.test.ts — 首页 BC/RL 区互斥分流（2026-09-14，用户指令）
 *
 * 分层：src/web/app/app.tsx（isBc 分流开关）+ src/server/api/state-view.ts（isBc stamp）
 *
 * RL 课（缺省 isBc）→ Hero + EvalBoard 摘要，无 BC 区；BC 课（isBc=true）→ BC Epoch 区，
 * 无 Hero/EvalBoard。组件卡 / 节点 pill / 详情抽屉两课通用，不受分流影响。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { type ConsoleStateView } from '../src/web/view'

function mkView(isBc: boolean): ConsoleStateView {
  return {
    time: 't',
    course: isBc ? 'bc-x' : 'rl-x',
    courses: [isBc ? 'bc-x' : 'rl-x'],
    isBc,
    components: [],
    nodes: [],
    modes: { trainerPpo: 'pull', stream: 0, doubleBuffer: 0, precollectEarly: 0 },
    metrics: { available: false, iters: [] },
    phase: { phase: 'idle', sinceMs: null, iter: null },
  }
}

describe('首页 BC/RL 区互斥（isBc 分流）', () => {
  it('BC 课（isBc=true）：出 BC Epoch 区，不出 Hero/EvalBoard', async () => {
    const { App } = await import('../src/web/app/app')
    const html = renderToString(h(App, { initial: mkView(true) }))
    // 锚点用各区独有标记（底部说明文字同时提到两个区名，不能当互斥断言锚点）。
    expect(html).toContain('尚无 BC epoch 指标')
    expect(html).not.toContain('tc-hero')
    expect(html).not.toContain('tc-eval-summary')
  })

  it('RL 课（isBc 缺省 false）：出 Hero/EvalBoard，不出 BC 区', async () => {
    const { App } = await import('../src/web/app/app')
    const html = renderToString(h(App, { initial: mkView(false) }))
    expect(html).toContain('tc-hero')
    expect(html).toContain('tc-eval-summary')
    expect(html).not.toContain('尚无 BC epoch 指标')
  })
})
