/**
 * web-app-courseadmin.test.ts — 课程管理页（`/courses`）的接线与两步封存（源码锚点断言）。
 *
 * 分层：src/web/app/app.tsx（外壳接线）· src/web/app/panels/CourseAdmin.tsx（面板）
 *
 * 为什么用源码锚点而不是渲染快照：
 *   ① 这两条断言的实质是**顺序契约**——「预演在前、apply 在后」以及「动作结果要交回调用方」，
 *      它们在 SSR 渲染体里看不出来（渲染体只证明按钮画出来了）；
 *   ② 本仓已有同款惯例（`web-app-coursematrix.test.ts` 的 `expect(app).toContain(...)`）：
 *      总览那次事故是「面板写好了、app.tsx 没接上」，只有源码锚点抓得住。
 * 渲染侧另有 `web-ssr-console.test.ts` 的 `/courses` 用例（真渲染，证明两段都在 DOM 里）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'node:fs'

const appSrc = readFileSync('src/web/app/app.tsx', 'utf8')
const panelSrc = readFileSync('src/web/app/panels/CourseAdmin.tsx', 'utf8')
const routesSrc = readFileSync('src/web/view/routes.ts', 'utf8')

describe('课程管理页 · 接线', () => {
  it('app.tsx 按页面键渲染面板（page === "courses" → <CourseAdmin …/>）', () => {
    expect(appSrc).toContain("page === 'courses'")
    expect(appSrc).toContain('<CourseAdmin')
    expect(appSrc).toContain('stateView={stateView}')
    expect(appSrc).toContain('onSelectCourse={selectCourse}')
    expect(appSrc).toContain('onAction={doAction}')
  })

  it('路由表里有 /courses（页面键 + 导航项 + 路径解析三处同源）', () => {
    expect(routesSrc).toContain("'overview' | 'metrics' | 'nodes' | 'wire' | 'courses'")
    expect(routesSrc).toContain("if (p === '/courses') return 'courses'")
    expect(routesSrc).toContain("href: '/courses'")
  })

  it('开课目标显式带上：管理页是全课表，点 B 课的开课必须开 B（不走「当前查看课程」兜底）', () => {
    expect(appSrc).toContain('openCourseTarget')
    expect(appSrc).toContain('...(target ? { course: target } : {})')
    // 弹窗读的也是同一个目标（起点权重/训练模式要落在那门课上）
    expect(appSrc).toContain('course={openCourseTarget ?? viewCourse}')
  })

  it('doAction 把整份 ActionResult 交回调用方（预演的数字要拿来做决定，不能只给 ok）', () => {
    expect(appSrc).toContain('Promise<ActionResult>')
    expect(appSrc).toContain('return r')
  })
})

describe('课程管理页 · 封存两步（预演 → 确认）', () => {
  it('第一步不带 apply、第二步才带：顺序契约在源码里可读', () => {
    // 预演：零写零删（服务端默认 --dry-run）
    const previewAt = panelSrc.indexOf("onAction('archiveCourse', { course: c })")
    expect(previewAt).toBeGreaterThan(0)
    // 真封存：只有「确认封存」那个键才走到这里
    const applyAt = panelSrc.indexOf("onAction('archiveCourse', { course: c, apply: true })")
    expect(applyAt).toBeGreaterThan(previewAt)
  })

  it('预演结果留在页面上（清单 + 字节账），不是一闪而过的 flash', () => {
    expect(panelSrc).toContain('tc-ca__confirm')
    expect(panelSrc).toContain('确认封存')
  })

  it('预演的数字只上屏一遍：postAction 折进 message 尾巴的 detail 要摘掉（清单自己渲染）', () => {
    // `api-client.postAction` 为 flash 把 detail 拼进 message；预演块又单独渲染 detail 清单
    // ⇒ 不摘就会同一组数字上屏两遍（2026-09-27 评审 F1）。
    expect(panelSrc).toContain('function splitDetail')
    expect(panelSrc).toContain('r.message.endsWith(tail)')
    expect(panelSrc).toContain('...splitDetail(r)')
  })

  it('真封存失败不收起结果块（409/500/504 最需要看清），成功才收起；文案分得出栽在哪一步', () => {
    const applyAt = panelSrc.indexOf("onAction('archiveCourse', { course: c, apply: true })")
    expect(applyAt).toBeGreaterThan(0)
    const failAt = panelSrc.indexOf("phase: 'apply'", applyAt)
    const clearAt = panelSrc.indexOf('setPreview(null)', failAt)
    expect(failAt).toBeGreaterThan(applyAt)
    expect(clearAt).toBeGreaterThan(failAt)
    expect(panelSrc).toContain("preview.phase === 'apply' ? '封存失败'")
  })

  it('行内动作面齐全：查看 / 开课·停课 / 暂停·恢复 / 切离线·在线 / 封存', () => {
    expect(panelSrc).toContain("onAction('stopCourse', { course: r.course })")
    expect(panelSrc).toContain("onAction('setCoursePaused'")
    expect(panelSrc).toContain("onAction('setCourseMode'")
    expect(panelSrc).toContain('onOpenCourseFor')
    expect(panelSrc).toContain('archiveOp')
  })
})
