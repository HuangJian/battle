import { describe, expect, it } from 'bun:test'
import { taskKey } from '../../tools/agent/sampler-agent'

/**
 * 决策门任务键后缀（plan/new-era-stop.plan.md §6 事件 rung + plan/k5-rhythm.plan.md K）：
 * 决策粒度变了 ⇒ 同一种子也是不同的局。仅激活时（`de='de1'` / `dk='<n>'`）进键尾，
 * 缺席 = 历史键逐字节不变（老缓存/老轮询/旧训练侧不受影响）。
 * 与三处同配方：提交端 taskKey 调用 · 轮询端 taskKey 调用 ·
 * `dist_common.fetch_task(decision_events=…, decision_k=…)` 的透传。
 */
describe('taskKey 决策事件后缀（仅激活时进键）', () => {
  it('缺席 = 历史四形状逐字节不变', () => {
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7)).toBe('it1:rollout:rollout:2000:7')
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, 'abcdef1234567890')).toBe(
      'it1:rollout:rollout:2000:7:abcdef1234567890',
    )
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', 'c0ffee1234567890')).toBe(
      'it1:rollout:rollout:2000:7:cc0ffee1234567890',
    )
  })

  it('激活 = 尾缀 :de1（四形状各加后缀，不与 sjHash/courseFp 语义纠缠）', () => {
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', 'de1')).toBe(
      'it1:rollout:rollout:2000:7:de1',
    )
    expect(taskKey('it1', 'eval', 'eval', 2001, 9, 'abcdef1234567890', '', 'de1')).toBe(
      'it1:eval:eval:2001:9:abcdef1234567890:de1',
    )
  })

  it('空串 de 与缺席等价（调用方传空串不漂移）', () => {
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '')).toBe(
      taskKey('it1', 'rollout', 'rollout', 2000, 7),
    )
  })
})

describe('taskKey 决策周期 K 后缀（仅非默认值进键）', () => {
  it('激活 = 尾缀 :k<N>；与 de1 并存顺序 de→k', () => {
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '', '5')).toBe(
      'it1:rollout:rollout:2000:7:k5',
    )
    expect(taskKey('it1', 'eval', 'eval', 2001, 9, 'abcdef1234567890', '', 'de1', '5')).toBe(
      'it1:eval:eval:2001:9:abcdef1234567890:de1:k5',
    )
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', 'c0ffee1234567890', '', '5')).toBe(
      'it1:rollout:rollout:2000:7:cc0ffee1234567890:k5',
    )
  })

  it('空串 dk 与缺席等价（10 = 老键逐字节不变）', () => {
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '', '')).toBe(
      taskKey('it1', 'rollout', 'rollout', 2000, 7),
    )
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', 'de1', '')).toBe(
      taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', 'de1'),
    )
  })
})
