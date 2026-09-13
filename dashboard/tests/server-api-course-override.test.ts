/**
 * server-api-course-override.test.ts — 课程覆盖只读查看：sanitizeViewCourse 放行真实课程、buildStateView(course) 不写 state、pool 课程键控、componentLogPayload 跟课程
 *
 * 分层：src/server/api/courses.ts + state-view.ts + pool.ts + component-meta.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { actions, api } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console 局域网只读边界（§…：LAN 查看 / localhost 控制）', () => {
  it('sanitizeViewCourse：放行真实课程，拒绝路径穿越与不存在的课程', () => {
    const courses = api.discoverCourses(50)
    const real = courses[0]
    if (real) expect(api.sanitizeViewCourse(real)).toBe(real)
    expect(api.sanitizeViewCourse('../../secret')).toBe('')
    expect(api.sanitizeViewCourse('a/b')).toBe('')
    expect(api.sanitizeViewCourse('no-such-course-xyz')).toBe('')
    expect(api.sanitizeViewCourse('')).toBe('')
    expect(api.sanitizeViewCourse(null)).toBe('')
  })

  it('buildStateView(course) 只读覆盖查看课程且不写 console-state', async () => {
    const before = actions.loadConsoleState()
    const courses = api.discoverCourses(50)
    const target = courses.find((c) => c !== before.course) ?? before.course
    const s = await api.buildStateView(target)
    expect(s.course).toBe(target)
    // 只读：查看课程绝不落盘 console-state（LAN 切换不影响操作员课程/训练）
    expect(actions.loadConsoleState()).toEqual(before)
    // 无参 = 操作员课程（原语义不变）
    const s2 = await api.buildStateView()
    expect(s2.course).toBe(api.effectiveCourse(before, courses))
  })

  it('buildPoolView 课程键控：course 覆盖改变返回课程（缓存 key 带课程）', async () => {
    const courses = api.discoverCourses(50)
    const target = courses[0]
    if (!target) return
    const p = await api.buildPoolView(false, target)
    expect(p.course).toBe(target)
  })

  it('componentLogPayload 接受课程覆盖（日志页跟课程）', async () => {
    const courses = api.discoverCourses(50)
    const target = courses[0]
    if (!target) return
    const p = await api.componentLogPayload('trainingLoop', 50, target)
    expect(p).not.toBeNull()
    expect(p!.component).toBe('trainingLoop')
    expect(Array.isArray(p!.lines)).toBe(true)
  })
})
