/**
 * web-view-pending-lock.test.ts — §367 启 / 停按钮 pending 锁：状态从点击时值切换才解锁，未变或组件消失不解锁
 *
 * 分层：src/web/view/interaction.ts（pendingLockReleases）
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { pendingLockReleases } from '../src/web/view'

describe('组件动作 pending 锁（§367：启/停 点击先 disable，状态切换完成再 enable）', () => {
  it('pendingLockReleases：状态从点击时值切换才解锁；未变 / 组件消失不解锁', () => {
    // 当前状态：a=启动完成(running) / b=停止完成(stopped) / c=启动失败(still stopped)
    const statusOf = (k: string): string | undefined =>
      ({ a: 'running', b: 'stopped', c: 'stopped' })[k]
    // 启动完成：stopped → running
    expect(pendingLockReleases({ a: 'stopped' }, statusOf)).toEqual(['a'])
    // 停止完成：running → stopped
    expect(pendingLockReleases({ b: 'running' }, statusOf)).toEqual(['b'])
    // 启动失败：状态仍是 stopped → 不解锁（由失败回调直接释放）
    expect(pendingLockReleases({ c: 'stopped' }, statusOf)).toEqual([])
    // 组件从 stateView 消失（statusOf 返回 undefined）→ 不解锁
    expect(pendingLockReleases({ ghost: 'stopped' }, statusOf)).toEqual([])
    // 混合：已切换的解锁，未变/消失的保留
    expect(
      pendingLockReleases({ a: 'stopped', b: 'running', c: 'stopped', ghost: 'x' }, statusOf),
    ).toEqual(['a', 'b'])
  })
})
