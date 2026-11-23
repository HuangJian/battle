/**
 * web-app-course-switch-fetch.test.ts — 切课一次点击只拉一拍 /api/state（2026-11-23）
 *
 * 旧 `selectCourse` 自己 `refreshState()`，`doAction('setCourse')` 内部又 `refreshState()`；
 * 服务端那一拍要全量解析 eval 账本（g9-def 3.4s）⇒ 一次点击双份重活。
 * 这里钉住「切课那一拍显式关掉 doAction 的重拉」，其他动作不受影响。
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'

import { describe, expect, it } from 'bun:test'

const app = readFileSync(path.join(import.meta.dir, '../src/web/app/app.tsx'), 'utf8')
const selectStart = app.indexOf('const selectCourse = useCallback')
const selectEnd = app.indexOf('const phaseInfo =', selectStart)
const selectCourse = app.slice(selectStart, selectEnd)

describe('切课拉取次数', () => {
  it('selectCourse 只发一次 refreshState，setCourse 显式关掉动作内的重拉', () => {
    expect(selectCourse.match(/refreshState\(\)/g)?.length).toBe(1)
    expect(selectCourse).toContain("doAction('setCourse', { course: c }, { refresh: false })")
  })

  it('doAction 默认仍重拉，只有显式 refresh:false 才跳过', () => {
    expect(app).toContain('if (opts.refresh !== false) void refreshState()')
  })
})

describe('挂载 / 回前台不再双发同端点请求', () => {
  const src = (rel: string): string =>
    readFileSync(path.join(import.meta.dir, '../src/web/app', rel), 'utf8')

  it('app.tsx 回前台不手动拉 state：usePolling 恢复时会首拉', () => {
    const start = app.indexOf('const onVis = (): void =>')
    const end = app.indexOf("document.addEventListener('visibilitychange', onVis)", start)
    const onVis = app.slice(start, end)
    expect(onVis).not.toContain('refreshState()')
  })

  it('五处「立即刷新」只在身份变化时触发，挂载/开关翻 true 交给 usePolling', () => {
    expect(src('panels/EvalSummary.tsx')).toContain('lastCourseRef.current === course')
    expect(src('panels/BcPanel.tsx')).toContain('lastCourseRef.current === course')
    expect(src('lib/useHomeCompare.ts')).toContain('lastKeyRef.current === requestKey')
    expect(src('panels/CompareTrendsModal.tsx')).toContain('lastKeyRef.current === requestKey')
    expect(src('eval-app.tsx')).toContain('lastCoursesRef.current === key')
  })
})
