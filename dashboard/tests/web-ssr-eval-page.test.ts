/**
 * web-ssr-eval-page.test.ts — R8 / R9：/eval 独立页 + CSV 导出
 *
 * 分层：src/web/app/eval-app.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'

/**
 * 只取 `#root` 内的渲染体（同 web-ssr-console.test.ts 的同名助手）。
 *
 * ⚠ 为什么必须切：整个 `theme.css` 被内联进 `<style>`，所以对 `html` 做
 * `toContain('返回控制台')` 这类断言会被**样式表文本（连注释也算）**满足。
 * 本文件就是这么被咬过一口的（见下方「外壳导航」用例的注释）。
 */
function body(html: string): string {
  const i = html.indexOf('<div id="root">')
  if (i < 0) return html
  const j = html.indexOf('<script>', i)
  return j > i ? html.slice(i, j) : html.slice(i)
}

// ────────────────────────── /eval 独立页 + CSV（R8/R9） ──────────────────────────
describe('/eval 独立页 + CSV 导出（R8/R9）', () => {
  const rung: import('../src/web/view').EvalLadderRow = {
    rung: 'c4l1',
    dimension: '基准',
    lives: 1,
    starLevel: 1,
    stageBrief: {
      name: 'arena-13x13-4enemies',
      enemies: ['basic', 'basic', 'basic', 'basic'],
      enemyCount: 4,
      hasBase: false,
      hasTerrain: false,
    },
    god: { winRate: 0.64, lifePrice: 1.5, n: 1600, provisional: false },
    batches: 4,
    n: 400,
    latestWin: 0.5,
    windowWin: 0.5,
    aTrend: null,
    deltaVsGod: -0.14,
    gate: null,
    partial: false,
  }
  const view: import('../src/web/view').EvalBoardView = {
    cachedAt: 0,
    course: 'c4-margin',
    ingested: 0,
    evalEvery: null,
    abWarn: null,
    ladder: [rung],
    alerts: [],
    batches: [],
    flips: [],
    rows: 400,
    spaceCalibrated: false,
    iterRows: [
      {
        kind: 'god',
        course: 'c4-margin',
        iter: -1,
        cells: { 'c4l1.winRate': 0.64 },
        n: 0,
        batchIds: [],
        screening: false,
      },
      {
        kind: 'iter',
        course: 'c4-margin',
        iter: 30,
        cells: { 'c4l1.winRate': 0.5 },
        n: 400,
        batchIds: ['b1'],
        screening: true,
      },
    ],
    runnerState: null,
    ladderState: null,
  }

  it('renderEvalPage SSR：矩阵/触发区/导出 + eval.js bundle', async () => {
    const { renderEvalPage } = await import('../src/web/render')
    const html = renderEvalPage({
      views: [view],
      options: { courses: ['c4-margin'], allCourses: ['c4-margin'], readOnly: false },
    })
    const dom = body(html)
    // 页名/描述改由外壳顶栏给（PAGES.eval）：标题「评估」+ 描述里保留 EvalBoard。
    expect(dom).toContain('tc-top__title')
    expect(dom).toContain('>评估</h1>')
    expect(dom).toContain('EvalBoard')
    expect(html).toContain('/eval.js')
    expect(dom).toContain('导出 CSV')
    expect(dom).toContain('入队评估批')
    expect(dom).toContain('it30')
  })

  it('套上侧栏外壳（2026-09-20 P3）：六个入口都在，评估项高亮', async () => {
    const { renderEvalPage } = await import('../src/web/render')
    const html = renderEvalPage({
      views: [view],
      options: { courses: ['c4-margin'], allCourses: ['c4-margin'], readOnly: false },
    })
    const dom = body(html)
    // 侧栏（与 /app.js 同一个 NavSidebar）在场，且六个条目一个不少。
    expect(dom).toContain('tc-side')
    expect(dom).toContain('aria-label="控制台导航"')
    for (const label of ['总览', '指标', '评估', '节点', '传输', '日志']) {
      expect(dom).toContain(`tc-nav__label">${label}</span>`)
    }
    // 当前页在侧栏里是选中态（不是靠手写的一行「返回控制台」找回控制台）。
    // href 带 `?course=`（本页主课程透传，点回控制台不丢「我在看哪门课」），故只断言前缀。
    expect(dom).toContain('href="/eval')
    expect(dom).toContain('aria-current="page"')
    // 课程透传到控制台那四项（主课程 = 首个勾选的课程）。
    expect(dom).toContain('href="/?course=c4-margin"')
    // 头部那行「← 返回控制台」已删：导航由侧栏承担。
    expect(dom).not.toContain('返回控制台')
  })

  it('CSV：UTF-8 BOM + 拍平表头 + RFC4180 转义 + 空值不写 0', async () => {
    const { buildEvalCsv } = await import('../src/web/view')
    const rows: import('../src/web/view').EvalIterRow[] = [
      {
        kind: 'iter',
        course: 'c4-margin',
        iter: 30,
        cells: { 'c4l1.winRate': 0.5 },
        n: 100,
        batchIds: ['b1'],
        screening: true,
      },
    ]
    const csv = buildEvalCsv(rows, [
      { header: 'n', value: (r) => (r.n > 0 ? r.n : null) },
      { header: 'c4l1 / 胜率, "quoted"', value: (r) => r.cells['c4l1.winRate'] ?? null },
      { header: 'missing', value: () => null },
    ])
    expect(csv.startsWith('\uFEFF')).toBe(true)
    const firstLine = csv.slice(1).split('\r\n')[0]
    expect(firstLine).toBe('course / iter,n,"c4l1 / 胜率, ""quoted""",missing')
    expect(csv).toContain('c4-margin / it30,100,0.5,') // 末列空值 = 空串
    expect(csv).not.toContain('1e-') // 无科学计数
  })
})
