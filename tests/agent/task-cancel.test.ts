import { describe, expect, it } from 'bun:test'
import { cancelTaskState } from '../../tools/agent/sampler-agent'

/**
 * `/v1/cancel` 的判定必须幂等：reaper 可能重试同一条取消（网络超时），
 * 第二次不能再调 abort / 杀进程；已结束的任务返回 not-found（handler 回 404）。
 */
describe('cancelTaskState', () => {
  it('missing = not-found', () => {
    expect(cancelTaskState(undefined)).toBe('not-found')
  })

  it('first cancel calls abort once; second is already', () => {
    let calls = 0
    const task = {
      cancelled: false,
      cancel: () => {
        calls++
      },
    }
    expect(cancelTaskState(task)).toBe('cancelled')
    expect(task.cancelled).toBe(true)
    expect(cancelTaskState(task)).toBe('already')
    expect(calls).toBe(1)
  })
})
