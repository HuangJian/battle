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

  it('renderEvalPage SSR：矩阵/触发区/导出/返回控制台 + eval.js bundle', async () => {
    const { renderEvalPage } = await import('../src/web/render')
    const html = renderEvalPage({
      views: [view],
      options: { courses: ['c4-margin'], allCourses: ['c4-margin'], readOnly: false },
    })
    expect(html).toContain('EvalBoard 评估页')
    expect(html).toContain('/eval.js')
    expect(html).toContain('导出 CSV')
    expect(html).toContain('入队评估批')
    expect(html).toContain('返回控制台')
    expect(html).toContain('it30')
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
