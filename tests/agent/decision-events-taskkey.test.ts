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

describe('taskKey v6 BC 标签来源后缀（仅非空进键）', () => {
  it('空串 = 历史键不变；argmax/sampled 各自成键', () => {
    const base = taskKey('it1', 'bc-v6', 'rollout', 2000, 7)
    expect(taskKey('it1', 'bc-v6', 'rollout', 2000, 7, '', '', '', '', '')).toBe(base)
    expect(taskKey('it1', 'bc-v6', 'rollout', 2000, 7, '', '', '', '', 'argmax')).toBe(
      `${base}:lmargmax`,
    )
    expect(taskKey('it1', 'bc-v6', 'rollout', 2000, 7, '', '', '', '', 'sampled')).toBe(
      `${base}:lmsampled`,
    )
  })

  it('与决策后缀并存时顺序为 de→k→lm', () => {
    expect(taskKey('it1', 'bc-v6', 'rollout', 2000, 7, '', '', 'de1', '5', 'argmax')).toBe(
      'it1:bc-v6:rollout:2000:7:de1:k5:lmargmax',
    )
  })
})

describe('taskKey greedy 分层采集后缀（2026-11-10，全节点）', () => {
  it('greedy 局与采样局不同键；缺席 = 老键逐字节不变', () => {
    const base = taskKey('it1', 'rollout', 'rollout', 2000, 7)
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '', '', 'greedy')).toBe(
      `${base}:lmgreedy`,
    )
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '', '', '')).toBe(base)
    expect(taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '', '', 'greedy')).not.toBe(
      taskKey('it1', 'rollout', 'rollout', 2000, 7, '', '', '', '', 'argmax'),
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

describe('taskKey 顶钢空枪门后缀（评估/部署专用，仅激活时进键）', () => {
  it('缺席 = 历史键逐字节不变；开启 = 尾缀 :skipSteelShots', () => {
    const base = taskKey('it1', 'eval', 'rollout', 2000, 7)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '')).toBe(base)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '1')).toBe(
      `${base}:skipSteelShots`,
    )
  })

  it('与 de/k/lm 并存时顺序为 de→k→lm→skipSteelShots', () => {
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', 'de1', '5', 'argmax', '1')).toBe(
      'it1:eval:rollout:2000:7:de1:k5:lmargmax:skipSteelShots',
    )
  })
})

describe('taskKey 拆砖免绕路后缀（评估/部署专用，仅激活时进键）', () => {
  it('缺席 = 历史键逐字节不变；开启 = 尾缀 :breakBricks', () => {
    const base = taskKey('it1', 'eval', 'rollout', 2000, 7)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '', '')).toBe(base)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '', '1')).toBe(
      `${base}:breakBricks`,
    )
  })

  it('与顶钢门并存时顺序为 skipSteelShots→breakBricks', () => {
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '1', '1')).toBe(
      'it1:eval:rollout:2000:7:skipSteelShots:breakBricks',
    )
  })
})

describe('taskKey B8/B10 空枪门后缀（评估/部署专用，仅激活时进键）', () => {
  it('B8 缺席 = 历史键；开启 = 尾缀 :suppressUselessFire', () => {
    const base = taskKey('it1', 'eval', 'rollout', 2000, 7)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '', '', '')).toBe(base)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '', '', '1')).toBe(
      `${base}:suppressUselessFire`,
    )
  })

  it('B10 缺席 = 历史键；开启 = 尾缀 :suppressLeadMissFire', () => {
    const base = taskKey('it1', 'eval', 'rollout', 2000, 7)
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '', '', '', '')).toBe(
      base,
    )
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '', '', '', '1')).toBe(
      `${base}:suppressLeadMissFire`,
    )
  })

  it('四个评估开关并存时顺序为 skipSteelShots→breakBricks→B8→B10', () => {
    expect(taskKey('it1', 'eval', 'rollout', 2000, 7, '', '', '', '', '', '1', '1', '1', '1')).toBe(
      'it1:eval:rollout:2000:7:skipSteelShots:breakBricks:suppressUselessFire:suppressLeadMissFire',
    )
  })
})
