/** web-course-filter.test.ts — 侧栏课程过滤（2026-11-02 用户报障：300+ 门课，滚动找课不现实）。
 *
 *  分层：纯函数在 `src/web/view/course-filter.ts`，接线在 `src/web/app/shell/Sidebar.tsx`。
 *
 *  钉子：
 *   · 系列名就是课名前缀 ⇒ `z4` 命中整个 z4 系（不建第二份系列表）；
 *   · 空格分隔 = AND、大小写不敏感；
 *   · **过滤不切课**：当前课不命中时仍在候选首位（select 的 value 必须有落点），且计数行说实话；
 *   · 空查询 = 原样全量（首帧与旧行为逐项相同，不因加了过滤而少一项）。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { Sidebar, type SidebarProps } from '../src/web/app/shell/Sidebar'
import {
  courseFilterNote,
  courseFilterTokens,
  courseSeries,
  filterCourses,
  REFRESH_INTERVALS,
} from '../src/web/view'

const COURSES = ['z4-polish', 'z4-pick-wall', 'z4-pick-approach', 'z2-r5nav', 'x20-dodge', 'c4']

describe('courseSeries：系列名 = 课名第一段', () => {
  it('带后缀取前缀；无后缀就是自己（不编一个不存在的系列）', () => {
    expect(courseSeries('z4-pick-approach')).toBe('z4')
    expect(courseSeries('x20-dodge')).toBe('x20')
    expect(courseSeries('c4')).toBe('c4')
    expect(courseSeries('')).toBe('')
  })
})

describe('courseFilterTokens：空格分隔 + 小写', () => {
  it('多词、多余空白、大小写都归一；全空 ⇒ 空表（= 不过滤）', () => {
    expect(courseFilterTokens(' Z4  Pick ')).toEqual(['z4', 'pick'])
    expect(courseFilterTokens('   ')).toEqual([])
    expect(courseFilterTokens('')).toEqual([])
  })
})

describe('filterCourses：系列过滤 / 多词 AND / 空查询原样', () => {
  it('空查询 ⇒ 原样全量、active=false、不画计数行', () => {
    const r = filterCourses(COURSES, '')
    expect(r.matches).toEqual(COURSES)
    expect(r.matches).not.toBe(COURSES) // 拷贝：调用方 sort 不回头改 props 数组
    expect(r).toMatchObject({ hits: 6, total: 6, active: false, pinnedKept: false })
    expect(courseFilterNote(r)).toBe('')
  })

  it('系列名即子串：`z4` 命中整个 z4 系，顺序保持', () => {
    const r = filterCourses(COURSES, 'z4')
    expect(r.matches).toEqual(['z4-polish', 'z4-pick-wall', 'z4-pick-approach'])
    expect(courseFilterNote(r)).toBe('命中 3 / 6 门')
  })

  it('多词 = AND（与词序无关）且大小写不敏感', () => {
    const a = filterCourses(COURSES, ' Pick  Z4 ')
    expect(a.matches).toEqual(['z4-pick-wall', 'z4-pick-approach'])
    expect(filterCourses(COURSES, 'z4 pick').matches).toEqual(a.matches)
  })

  it('无匹配 ⇒ 空候选，计数行点名总数（不冒充「就这么多」）', () => {
    const r = filterCourses(COURSES, 'nope')
    expect(r.matches).toEqual([])
    expect(r).toMatchObject({ hits: 0, active: true, pinnedKept: false })
    expect(courseFilterNote(r)).toBe('无匹配（共 6 门）')
  })

  it('没有可选课程时计数行不编数字', () => {
    expect(courseFilterNote(filterCourses([], 'z4'))).toBe('没有可选课程')
  })
})

describe('filterCourses：当前查看的课不被滤走（过滤 ≠ 切课）', () => {
  it('当前课命中 ⇒ 留在原位置，不重复、不置顶', () => {
    const r = filterCourses(COURSES, 'z4', 'z4-pick-wall')
    expect(r.matches).toEqual(['z4-polish', 'z4-pick-wall', 'z4-pick-approach'])
    expect(r.pinnedKept).toBe(false)
  })

  it('当前课不命中 ⇒ 补在候选首位并标注（select 的 value 有落点）', () => {
    const r = filterCourses(COURSES, 'z2', 'z4-polish')
    expect(r.matches).toEqual(['z4-polish', 'z2-r5nav'])
    expect(r).toMatchObject({ hits: 1, pinnedKept: true })
    // 计数只算真的找到的：当前课不是「命中」
    expect(courseFilterNote(r)).toBe('命中 1 / 6 门')
  })

  it('一个都没命中 ⇒ 只留当前课，文案说清「仅保留」', () => {
    const r = filterCourses(COURSES, 'nope', 'x20-dodge')
    expect(r.matches).toEqual(['x20-dodge'])
    expect(r.pinnedKept).toBe(true)
    expect(courseFilterNote(r)).toBe('无匹配 · 仅保留当前课（共 6 门）')
  })

  it('旧课名（已不在清单里）不注入幽灵候选；空 pinned 也不注入', () => {
    expect(filterCourses(COURSES, 'nope', 'ghost-course').matches).toEqual([])
    expect(filterCourses(COURSES, 'nope', '').matches).toEqual([])
  })
})

describe('Sidebar 接线：过滤框在课程选择器之前，首帧仍是全量', () => {
  const props: SidebarProps = {
    activePath: '/',
    course: 'z4-polish',
    courses: COURSES,
    recentCourses: [],
    trainingCourses: ['z4-polish'],
    onCourseChange: () => {},
    onPickCourse: () => {},
    onOpenCourse: () => {},
    courseEnabled: true,
    onNavigate: () => {},
    readOnly: false,
    gate: {
      visible: false,
      mode: 'halt',
      hours: null,
      until: null,
      applied: '',
      hint: '',
      disabled: false,
      onChange: () => {},
      onHoursChange: () => {},
    },
    refresh: { value: REFRESH_INTERVALS[0]!, onChange: () => {} },
  }

  it('输入框 id / 类型 / 说明齐全，且 DOM 序：过滤框 → 计数行 → 课程 select', () => {
    const html = renderToString(h(Sidebar, props))
    const filter = html.indexOf('id="courseFilter"')
    const select = html.indexOf('id="courseSel"')
    expect(filter).toBeGreaterThan(-1)
    expect(select).toBeGreaterThan(filter)
    expect(html).toContain('type="search"')
    // 首帧没有过滤词 ⇒ 不出现计数行（不与「命中 0 门」这种不存在的状态抢眼）
    expect(html).not.toContain('命中 ')
    // 「过滤不切课」必须写在上屏面能读到的地方（title）
    expect(html).toContain('当前查看的课永远留在候选里')
  })

  it('首帧候选 = 全量 + 「自动」，在训标记与排序不变', () => {
    const html = renderToString(h(Sidebar, props))
    const selectHtml = html.slice(html.indexOf('id="courseSel"'))
    const auto = selectHtml.indexOf('自动（最近活跃课程）')
    expect(auto).toBeGreaterThan(-1)
    for (const c of COURSES) {
      expect(selectHtml.indexOf(c)).toBeGreaterThan(auto - 1)
      expect(selectHtml.indexOf(c)).toBeGreaterThan(-1)
    }
    // 在训标记还跟着「已开课」口径走
    expect(selectHtml).toContain('🔥 z4-polish')
    expect(selectHtml).toContain('（已开课）')
    // 过滤框自己不是 option：没有「自动」以外的东西跑到 select 前面
    expect(html.indexOf('自动（最近活跃课程）')).toBeGreaterThan(html.indexOf('id="courseSel"'))
  })
})
