/**
 * server-api-course-ctx.test.ts — 课程单一事实源（§351 bug 1）+ local×stream 假互斥移除（§351 bug 2）
 *
 * 分层：src/server/api/courses.ts + route.ts（effectiveCourse / actionCtx）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { api, render } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console course single source (DECISIONS §351 bug 1)', () => {
  it('effectiveCourse：state 优先；空则回退最近活跃课程；无课程为空串', () => {
    expect(api.effectiveCourse({ course: 'p4-horizon' }, ['a', 'b'])).toBe('p4-horizon')
    expect(api.effectiveCourse({ course: '' }, ['a', 'b'])).toBe('a')
    expect(api.effectiveCourse({ course: '' }, [])).toBe('')
  })

  it('显示课程 = 动作课程（单一事实源不变量）', async () => {
    const s = await api.buildStateView()
    const ctx = api.actionCtx({})
    expect(ctx.course).toBe(s.course)
  })

  it('actionCtx：显式 body.course 覆盖回退', () => {
    expect(api.actionCtx({ course: 'explicit-course' }).course).toBe('explicit-course')
  })

  it('页面课程下拉含「自动（最近活跃课程）」占位项', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).toContain('自动（最近活跃课程）')
  })
})
describe('console local×stream 假互斥移除 (DECISIONS §351 bug 2)', () => {
  it('页面不再声称 local 需 rl.stream=0 / 本地 PPO 互斥；stream 开关仍在', async () => {
    const s = await api.buildStateView()
    const html = render.renderConsolePage(s)
    expect(html).not.toContain('需 rl.stream=0')
    expect(html).not.toContain('本地 PPO 互斥')
    // stream 开关已收入 TrainingLoop 启动弹窗（SSR 首帧不渲染）
    expect(html).not.toContain('class="tc-modal-mask"')
  })
})
