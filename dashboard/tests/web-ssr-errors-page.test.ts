/**
 * web-ssr-errors-page.test.ts — 组件错误详情页 `/errors` 的 SSR 首帧（renderErrorsPage）。
 *
 * 分层：src/web/render.tsx（renderErrorsPage）+ src/web/app/errors-app.tsx。
 *
 * 依据 plan/component-error-registry §4.6（G7）：第 3 个独立页 / 第 4 份 bundle，照 `/log` 范式
 * （服务端整页服务、不参与主 bundle 的客户端路由）。本文件钉三件事：
 *   ① 首帧就把**列表投影**渲出来（含排序与汇总计数）——首屏不靠 JS 也能看；
 *   ② **detail 不进首帧**（长文本按 fp 懒取）：投影里没有它，页面里也不许出现；
 *   ③ 只读（LAN）照常渲染：只读的边界是**动作**，不是读面（F13）。
 *
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { render, view } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'
import type { ErrorListItem } from '../src/web/view'

/**
 * 只取 `#root` 内的渲染体（同 web-ssr-log-page.test.ts）。
 *
 * ⚠ 必须切片：整页把 `theme.css` 内联进 `<style>`，裸词断言会被样式表文本满足（2026-09-20 事故）。
 */
function body(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

/** 列表投影夹具：`ref`/`count`/`severity` 都带上（投影是逐条的，不是一个数）。 */
const row = (over: Partial<ErrorListItem> = {}): ErrorListItem => ({
  fp: 'fp-0',
  source: 'local-log',
  component: 'trainingLoop',
  scope: 'c5-gae',
  severity: 'err',
  firstAt: 1_700_000_000_000,
  lastAt: 1_700_000_060_000,
  count: 3,
  message: 'RuntimeError: 缺 BC 参考',
  ref: { kind: 'log', target: 'trainingLoop' },
  ...over,
})

describe('/errors SSR（renderErrorsPage，plan/component-error-registry §4.6）', () => {
  it('首帧就渲出表格：来源的人话标签 / 组件 · 归属 / 次数 / 摘要 + 空态另有文案', () => {
    const html = render.renderErrorsPage(
      { items: [row()], scannedAt: 1_700_000_120_000 },
      { readOnly: false, scannedAt: 1_700_000_120_000 },
    )
    const dom = body(html)
    expect(dom).toContain('组件错误')
    expect(dom).toContain('本机日志') // source 徽标走 SOURCE_LABEL（不把 'local-log' 甩给人看）
    expect(dom).toContain('trainingLoop · c5-gae')
    expect(dom).toContain('3×')
    expect(dom).toContain('RuntimeError: 缺 BC 参考')
    expect(dom).toContain('未处理')

    const empty = body(
      render.renderErrorsPage({ items: [], scannedAt: 0 }, { readOnly: false, scannedAt: 0 }),
    )
    expect(empty).toContain('没有组件报错')
    expect(empty).toContain('巡检还没跑过') // scannedAt=0 ⇒ 不谎报「已检查」
    expect(empty).toContain('没有匹配的行')
  })

  it('★ `detail` 不进首帧（P1-4：长文本按 fp 懒取，不跟 4s 轮询一起搬）', () => {
    const long = 'Traceback (most recent call last):'.repeat(50)
    const html = render.renderErrorsPage(
      { items: [row()], scannedAt: 1 },
      { readOnly: false, scannedAt: 1 },
    )
    // 投影里根本没有 detail 字段，展开区也不在首帧（`openFp` 是客户端 state、初值为空）：
    // 长文本只在「展开那一行」时才由 `/api/errors?fp=` 取回。
    expect(body(html)).not.toContain('tc-errors__detail')
    expect(html).not.toContain(long)
    expect(html).not.toContain('取详情中') // 连占位都没有 = 首帧确实没在等 detail
  })

  it('排序在服务端就定死：`err` 在前，同档按最近在前（首屏与客户端同序）', () => {
    const html = render.renderErrorsPage(
      {
        items: [
          row({ fp: 'w', severity: 'warn', message: 'warn-msg', lastAt: 900 }),
          row({ fp: 'e-old', severity: 'err', message: 'err-old', lastAt: 100 }),
          row({ fp: 'e-new', severity: 'err', message: 'err-new', lastAt: 500 }),
        ],
        scannedAt: 1,
      },
      { readOnly: false, scannedAt: 1 },
    )
    const dom = body(html)
    const at = (s: string): number => dom.indexOf(s)
    expect(at('err-new')).toBeLessThan(at('err-old'))
    expect(at('err-old')).toBeLessThan(at('warn-msg'))
  })

  it('bundle 与载荷：`/errors.js` + `window.__INITIAL__`（hydrate 首帧与 SSR 同源）', () => {
    const payload = { items: [row()], scannedAt: 1_700_000_120_000 }
    const opts = { readOnly: false, scannedAt: 1_700_000_120_000 }
    const html = render.renderErrorsPage(payload, opts)
    expect(html).toContain('/errors.js')
    expect(html).toContain('window.__INITIAL__')
    // 载荷就是服务端那一份（不两头各写一遍）——hydrate 复用同一对象，首帧不会闪。
    expect(html).toContain(JSON.stringify({ payload, options: opts }))
  })

  it('只读（LAN）照常渲染 + 明说边界；路由表里 /errors 是第 3 个独立页', () => {
    const html = render.renderErrorsPage(
      { items: [row()], scannedAt: 1 },
      { readOnly: true, scannedAt: 1 },
    )
    const dom = body(html)
    expect(dom).toContain('只读视图')
    expect(dom).toContain('RuntimeError: 缺 BC 参考') // 读面不受只读影响
    expect(dom).toContain('全部标记已读') // 动作也在（ack 只写本地 localStorage）
    // 路由表：独立页（不在本 bundle 的 PageKey 面里），路径唯一来源是 PAGES。
    expect(view.PAGES.errors.path).toBe('/errors')
    expect(view.NAV_ITEMS.some((n) => n.href === '/errors')).toBe(true)
  })
})
