/**
 * web-app-evalsummary.test.ts — 阶梯 God vs 学生 B 层聚合卡片
 *
 * 分层：src/web/app/panels/EvalSummary.tsx
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { renderConsolePage } from '../src/web/render'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'

// ────────────────────────── 首页 EvalBoard 摘要（列 = 阶梯；学生仅 B 层） ──────────────────────────
describe('首页 EvalSummary（阶梯 God vs 学生 B 层）', () => {
  const mockRung = (
    id: string,
    over: Partial<import('../src/web/view').EvalLadderRow> = {},
  ): import('../src/web/view').EvalLadderRow => ({
    rung: id,
    dimension: '基准',
    lives: 1,
    starLevel: 1,
    stageBrief: {
      name: 'arena-13x13-4enemies',
      enemies: ['basic'],
      enemyCount: 1,
      hasBase: false,
      hasTerrain: false,
    },
    god: { winRate: null, lifePrice: null, n: 0, provisional: null },
    batches: 0,
    n: 0,
    latestWin: null,
    windowWin: null,
    aTrend: null,
    deltaVsGod: null,
    gate: null,
    partial: false,
    ...over,
  })

  it('SSR 首屏含摘要壳与抽屉入口（数据客户端拉，首帧 loading）', async () => {
    const { buildStateView } = await import('../src/server/api')
    const html = renderConsolePage(await buildStateView())
    expect(html).toContain('tc-eval-summary')
    expect(html).toContain('EvalBoard 摘要')
    expect(html).toContain('完整评估看板')
    expect(html).toContain('加载评估摘要')
  })

  const mockIter = (
    over: Partial<import('../src/web/view').EvalIterRow> = {},
  ): import('../src/web/view').EvalIterRow => ({
    kind: 'iter',
    course: 'c4-margin',
    iter: 30,
    cells: {},
    n: 400,
    batchIds: ['b1'],
    screening: true,
    ...over,
  })
  const mockGod = (
    cells: Record<string, number | null> = {},
  ): import('../src/web/view').EvalIterRow => ({
    kind: 'god',
    course: 'c4-margin',
    iter: -1,
    cells,
    n: 0,
    batchIds: [],
    screening: false,
  })

  it('矩阵：行=iter（含 God），列=rung×指标；hover 表头 = rungLabel', async () => {
    const { EvalSummaryTable } = await import('../src/web/app/panels/EvalSummary')
    const { EVAL_METRIC_KEYS } = await import('../src/web/view')
    const ladder = [
      mockRung('c4l1', { god: { winRate: 0.64, lifePrice: 0.1, n: 1600, provisional: false } }),
      mockRung('c6l1', { dimension: '敌数' }),
    ]
    const html = renderToString(
      h(EvalSummaryTable, {
        ladder,
        iterRows: [
          mockGod({ 'c4l1.winRate': 0.64 }),
          mockIter({ iter: 30, cells: { 'c4l1.winRate': 0.5, 'c4l1.meanKills': 3.2 } }),
        ],
        course: 'c4-margin',
        metricKeys: [...EVAL_METRIC_KEYS],
      }),
    )
    expect(html).toContain('God')
    expect(html).toContain('it30')
    expect(html).toContain('64.0%')
    expect(html).toContain('50.0%')
    expect(html).toContain('3.20') // meanKills 两位小数
    expect(html).toContain('c4l1 平均击杀')
    // hover 标题含关卡画像（rungLabel：1★ / 无基地）
    expect(html).toContain('1★')
    expect(html).toContain('无基地')
    // 筛查级标注（A4）
    expect(html).toContain('筛查级')
    expect(html).toContain('c4-margin')
  })

  it('指标显隐：勾掉「平均道具」后该指标列消失', async () => {
    const { EvalSummaryTable } = await import('../src/web/app/panels/EvalSummary')
    const html = renderToString(
      h(EvalSummaryTable, {
        ladder: [mockRung('c4l1')],
        iterRows: [mockGod({}), mockIter({ cells: { 'c4l1.winRate': 0.5 } })],
        course: 'c4-margin',
        metricKeys: ['winRate'],
      }),
    )
    expect(html).toContain('c4l1 胜率')
    expect(html).not.toContain('c4l1 平均道具')
    expect(html).not.toContain('c4l1 平均击杀')
  })

  it('A12 空态：B 层 0 行 → 引导（不显示空数据行），并列出在途批', async () => {
    const { EvalSummaryTable } = await import('../src/web/app/panels/EvalSummary')
    const html = renderToString(
      h(EvalSummaryTable, {
        ladder: [mockRung('c4l1')],
        iterRows: [mockGod({})], // 只有 God 行 = B 层 0 行
        course: 'c6-margin',
        metricKeys: ['winRate'],
        batches: [
          {
            batch_id: 'b-20260911T014319-0806',
            course: 'c6-margin',
            rung_from: 'c4l1',
            status: 'pending',
            iter: 30,
            trigger: 'standalone',
            units: { of: 2, done: [] },
            elapsed_sec: null,
            created_ts: '2026-09-11T01:43:19.050Z',
            policy: 'nn',
          },
        ],
      }),
    )
    expect(html).toContain('尚无 B 层数据')
    expect(html).toContain('kick-once.py')
    expect(html).toContain('it30')
    expect(html).not.toContain('tc-tablewrap') // 不渲染空矩阵
  })
})
