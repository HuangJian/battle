/**
 * web-sidebar-recent.test.ts — 侧栏「最近训练」五门 + 右侧迷你读数（2026-11-02 用户口径）。
 *
 * 分层：`src/web/app/shell/NavSidebar.tsx`（纯渲染，SSR 快照）。
 *
 * 钉四件事：
 *  ① 传入的五门**按调用方给的顺序**逐个成行（顺序 = 最近训练时间，由 `courses` 保证）。
 *  ② 每行右侧有迷你读数：状态色点（`tc-dot--*`）+ 最新 it（不可知 ⇒ `it—`）。
 *  ③ 当前查看的那门 `aria-current`，已开课的带 🔥（与顶栏 pill / 课程 select 同一名单）。
 *  ④ 没有课程上下文（`/eval`、`/log` 两个独立页）⇒ 整节不渲染，不留空标题。
 */

import { describe, expect, it } from 'bun:test'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { NavSidebar } from '../src/web/app/shell/NavSidebar'
import type { RecentCourseView } from '../src/web/view'

const NAMES = ['z4-pick-approach', 'z4-pick-wall', 'z4-nav', 'z3-bth', 'x21-psh-k5']

const FIVE: RecentCourseView[] = NAMES.map((course, i) => ({
  course,
  it: 41 - i,
  tone: i === 0 ? 'g' : i === 1 ? 'y' : i === 2 ? 'r' : 'gray',
  title: `${course}：状态与判据`,
}))

function render(props: Parameters<typeof NavSidebar>[0]): string {
  return renderToString(h(NavSidebar, props))
}

describe('侧栏：最近训练的五门', () => {
  it('按给定顺序出五门；每行带状态色点 + 最新 it；当前课唯一高亮，已开课标 🔥', () => {
    const html = render({
      activePath: '/',
      course: 'z4-pick-wall',
      recentCourses: FIVE,
      trainingCourses: ['z4-pick-approach'],
      onPickCourse: () => {},
    })
    expect(html).toContain('最近训练')
    const seg = html.slice(html.indexOf('tc-side__recent'))
    expect((seg.match(/tc-side__recent-item/g) ?? []).length).toBe(5)
    expect((seg.match(/tc-side__recent-readout/g) ?? []).length).toBe(5)
    // 顺序：最近训练的第一门排在最上（调用方已按 mtime 倒序）
    let last = -1
    for (const c of NAMES) {
      const i = seg.indexOf(`>${c}<`)
      expect(i, c).toBeGreaterThan(last)
      last = i
    }
    // it 跟着读数的形状：it41 / it40 …（不可知时是 it—，见下一条）
    expect(seg).toContain('it41')
    expect(seg).toContain('it37')
    // 状态色点四档都用同一套 tc-dot 词表（g/y/r/gray）
    expect(seg).toContain('tc-dot--on')
    expect(seg).toContain('tc-dot--warn')
    expect(seg).toContain('tc-dot--dead')
    expect(seg).toContain('tc-dot--empty')
    expect(seg).toContain('aria-current="true"')
    expect(seg).toContain('🔥')
    // 只有当前课高亮（不多不少）
    expect((seg.match(/aria-current="true"/g) ?? []).length).toBe(1)
  })

  it('it 不可知 ⇒ `it—`（不拿 0 冒充「还没跑过」）', () => {
    const html = render({
      activePath: '/',
      recentCourses: [{ course: 'new-course', it: null, tone: 'gray', title: '还没结算过' }],
    })
    const seg = html.slice(html.indexOf('tc-side__recent'))
    expect(seg).toContain('it—')
    expect(seg).not.toContain('it0')
  })

  it('没有课程上下文（独立页）⇒ 整节不出（不留空标题）', () => {
    const html = render({ activePath: '/eval' })
    expect(html).not.toContain('tc-side__recent')
    expect(html).not.toContain('最近训练')
  })
})
