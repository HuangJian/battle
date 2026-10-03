/**
 * worker-contribution.test.ts — 并行 worker 贡献度（plan/worker-contribution-view）。
 *
 * 覆盖三层：
 *   ① 纯函数（`src/web/view/contribution.ts`）：份额分母组内自洽、缺数据「—」不是 0%、
 *      课程矩阵单点标记、角色隔离（同名不合并）、缩略是同一份聚合的裁剪；
 *   ② 采样窗口（`src/server/pool-history.ts`）：滚动窗吃**子日事件环**（日桶之外的新数据面），
 *      课维度 day/rolling 两种窗口都按课名切；
 *   ③ PPO 归属（`src/server/contribution.ts`）：`job_completed` **按 job_id join**
 *      `job_result_accepted` 取 worker；`job_rejected` 是「晚到·白算」。
 */

import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { DASHBOARD_ROOT } from '../src/core/paths'
import { poolHistoryCounters, resetPoolHistoryCounters } from '../src/server/pool-history'
import { h } from 'preact'
import { renderToString } from 'preact-render-to-string'
import { NodePills } from '../src/web/app/panels/NodePills'
import { WorkerContribution } from '../src/web/app/panels/WorkerContribution'
import {
  buildContributionMatrix,
  buildPpoContribution,
  buildSamplingContribution,
  compactSummary,
  fmtCount,
  fmtShare,
  type ContributionView,
} from '../src/web/view'
import {
  type HistoryAggregate,
  aggregateNodeHistory,
  invalidateNodeHistoryMemo,
  projectCourseBreakdown,
  projectWindow,
  resolveWindow,
} from '../src/server/pool-history'
import {
  buildContributionView,
  invalidatePpoAttributionMemo,
  readPpoAttribution,
} from '../src/server/contribution'

// ────────────────────────── ① 纯函数 ──────────────────────────

describe('contribution 纯函数（份额 / 缺数据 / 矩阵 / 缩略）', () => {
  it('份额分母组内自洽：采样与 PPO 各用各的分母，绝不互加（W0-①）', () => {
    const s = buildSamplingContribution([
      { id: 'a', split: { rollout: 3, eval: 0, fail: 0 } },
      { id: 'b', split: { rollout: 1, eval: 0, fail: 0 } },
    ])
    expect(s.rows.find((r) => r.id === 'a')!.share).toBeCloseTo(0.75)
    expect(s.total).toEqual({ rollout: 4, eval: 0, fail: 0 })
    // 采样局数不进 PPO 分母（两个单位组永不合并）
    const p = buildPpoContribution([
      { worker: 'x', done: 3, rejected: 0 },
      { worker: 'y', done: 1, rejected: 0 },
    ])
    expect(p.rows.find((r) => r.worker === 'x')!.share).toBeCloseTo(0.75)
    expect(p.totalDone).toBe(4)
  })

  it('缺数据渲染「—」而不是 0%（W0-②）：分母 0 ⇒ share null；格式化细则', () => {
    const s = buildSamplingContribution([{ id: 'a', split: { rollout: 0, eval: 0, fail: 2 } }])
    expect(s.rows[0]!.share).toBeNull()
    expect(fmtShare(s.rows[0]!.share)).toBe('—')
    expect(fmtShare(0)).toBe('0.0%')
    expect(fmtShare(0.00001)).toBe('<0.1%')
    expect(fmtShare(0.5)).toBe('50.0%')
    expect(fmtCount(12345)).toBe('12,345')
  })

  it('矩阵单点依赖标记（W0-③/G5）：某课只有一个身份有贡献 ⇒ 该组 solo 列表命中', () => {
    const m = buildContributionMatrix(
      {
        self: { k25: { rollout: 2, eval: 0, fail: 0 }, c0: { rollout: 1, eval: 0, fail: 0 } },
        mac: { c0: { rollout: 1, eval: 0, fail: 0 } },
      },
      { cloudA: { k25: { done: 3, rejected: 0 } } },
    )
    expect(m.courses).toEqual(['c0', 'k25'])
    expect(m.soloSamplingCourses).toEqual(['k25']) // c0 有 self+mac 两身份
    expect(m.soloPpoCourses).toEqual(['k25'])
    // 格位定位正确：self 在 k25 上 2 局、在 c0 上 1 局
    const selfRow = m.samplingRows.find((r) => r.id === 'self')!
    expect(selfRow.cells[m.courses.indexOf('k25')]!.split.rollout).toBe(2)
    expect(selfRow.cells[m.courses.indexOf('c0')]!.split.rollout).toBe(1)
  })

  it('角色隔离（G9）：同名身份在两组各自成行，绝不互相补数/归并', () => {
    const m = buildContributionMatrix(
      { self: { k25: { rollout: 2, eval: 0, fail: 0 } } },
      { self: { k25: { done: 5, rejected: 1 } } },
    )
    expect(m.samplingRows.map((r) => r.id)).toEqual(['self'])
    expect(m.ppoRows.map((r) => r.worker)).toEqual(['self'])
    // 矩阵行内不混单位：采样格 done=0、PPO 格 split 全零
    expect(m.samplingRows[0]!.cells[0]!.done).toBe(0)
    expect(m.ppoRows[0]!.cells[0]!.split).toEqual({ rollout: 0, eval: 0, fail: 0 })
    expect(m.ppoRows[0]!.cells[0]!.done).toBe(5)
    expect(m.ppoRows[0]!.cells[0]!.rejected).toBe(1)
  })

  it('缩略 = 同一份聚合的裁剪：组总量逐字相等、top 是排序前缀（W3b 一致性）', () => {
    const view: ContributionView = {
      sampling: buildSamplingContribution([
        { id: 'self', split: { rollout: 5, eval: 1, fail: 1 } },
        { id: 'mac', split: { rollout: 2, eval: 0, fail: 0 } },
      ]),
      ppo: buildPpoContribution([
        { worker: 'cloudA', done: 4, rejected: 1 },
        { worker: 'cloudB', done: 2, rejected: 0 },
      ]),
      matrix: {
        courses: [],
        samplingRows: [],
        ppoRows: [],
        soloSamplingCourses: [],
        soloPpoCourses: [],
      },
      samplingSources: { flows: 0, truncated: false, rollingTruncated: false },
      ppoSources: { ledgers: 0 },
    }
    const brief = compactSummary(view)
    expect(brief.sampling.total).toBe(5 + 1 + 1 + 2)
    expect(brief.ppo.totalDone).toBe(6)
    expect(brief.ppo.top.map((t) => t.worker)).toEqual(
      view.ppo.rows.slice(0, 3).map((r) => r.worker),
    )
    expect(brief.sampling.top.map((t) => t.id)).toEqual(
      view.sampling.rows.slice(0, 3).map((r) => r.id),
    )
  })
})

// ────────────────────────── ② 采样窗口（滚动环 + 课维度） ──────────────────────────

const pad = (n: number): string => String(n).padStart(2, '0')
function tsFromMs(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

function withPoolRoot(
  flows: Array<{ name: string; meta: string[] }>,
  fn: (root: string) => void,
): void {
  const root = mkdtempSync(join(tmpdir(), 'bcity-contrib-'))
  const prev = process.env.BCITY_POOL_DIR
  process.env.BCITY_POOL_DIR = root
  invalidateNodeHistoryMemo()
  invalidatePpoAttributionMemo()
  try {
    for (const f of flows) {
      const dir = join(root, f.name)
      mkdirSync(dir, { recursive: true })
      writeFileSync(join(dir, 'dist-agent-meta.jsonl'), `${f.meta.join('\n')}\n`, 'utf8')
    }
    fn(root)
  } finally {
    if (prev === undefined) delete process.env.BCITY_POOL_DIR
    else process.env.BCITY_POOL_DIR = prev
    invalidateNodeHistoryMemo()
    invalidatePpoAttributionMemo()
    rmSync(root, { recursive: true, force: true })
  }
}

const metaRow = (node: string, ms: number, mode = 'rollout', ok = true, it = 1): string =>
  JSON.stringify({ node, mode, ok, it, elapsedSec: 1.2, ts: tsFromMs(ms) })

/** 固定时钟（与 server-pool-history.test.ts 同规）：bun test 的 TZ 是 UTC，墙钟 `now - 45min`
 *  在 UTC 午夜后 45 分钟内落到**昨天** ⇒ 「日窗应含 45 分钟前的局」两条用例假红
 *  （2026-10-03 08:0x HKT 实测；此前绿只是恰好跑在 UTC 日内）。取固定正午：任何 TZ 下
 *  ±45 分钟都不跨天，且不锚墙钟。 */
const FIXED_NOW = Date.parse('2026-09-15T12:00:00')

describe('pool-history 滚动窗与课维度（plan §4.4 / §4.3）', () => {
  it('滚动窗只计窗内事件：45 分钟前的局进日窗但不进「近 30 分钟」', () => {
    const now = FIXED_NOW
    withPoolRoot(
      [
        {
          name: 'k25',
          meta: [
            metaRow('a1', now - 5 * 60_000),
            metaRow('a1', now - 45 * 60_000),
            metaRow('a2', now - 10 * 60_000, 'eval'),
          ],
        },
      ],
      (root) => {
        const agg = aggregateNodeHistory(now)
        expect(agg.rolling.get('a1')).toHaveLength(2) // 环收 2h 内全部事件（含窗外的 45 分钟前）
        const w = resolveWindow('30m', now, agg.epochMs)
        expect(w.kind).toBe('rolling')
        expect(w.durationMs).toBe(30 * 60_000)
        const win = projectWindow(agg, w)
        expect(win.hist.get('a1')!.winRollout).toBe(1)
        expect(win.hist.get('a2')!.winEval).toBe(1)
        // 日窗仍见到 a1 两条（滚动环不改变日窗）
        const day = projectWindow(agg, resolveWindow('today', now, agg.epochMs))
        expect(day.hist.get('a1')!.winRollout).toBe(2)
        expect(root).toBeTruthy()
      },
    )
  })

  it('课维度：day/rolling 两种窗口都按课名（流目录首段）切，不用 it', () => {
    const now = FIXED_NOW
    withPoolRoot(
      [
        {
          name: 'k25',
          meta: [metaRow('a1', now - 5 * 60_000), metaRow('a1', now - 40 * 60_000)],
        },
      ],
      (root) => {
        const agg = aggregateNodeHistory(now)
        const day = projectCourseBreakdown(agg, resolveWindow('today', now, agg.epochMs))
        expect(day.get('a1')!.get('k25')!.rollout).toBe(2)
        const roll = projectCourseBreakdown(agg, resolveWindow('30m', now, agg.epochMs))
        expect(roll.get('a1')!.get('k25')!.rollout).toBe(1)
        expect(root).toBeTruthy()
      },
    )
  })
})

// ────────────────────────── ③ PPO 归属（读侧 join） ──────────────────────────

describe('server/contribution PPO 归属（job_completed join job_result_accepted）', () => {
  it('完成按 job_id 取承接 worker；job_rejected 单列；不同课不串册', () => {
    const now = Date.now()
    // 事件全部落在窗口末刻**之前**（否则 done/rejected 掉出 endMs 边界）。
    const ts = (now - 60_000) / 1000
    withPoolRoot([], (root) => {
      mkdirSync(join(root, 'k25'), { recursive: true })
      writeFileSync(
        join(root, 'k25', 'training_log.jsonl'),
        [
          JSON.stringify({ event: 'job_result_accepted', job_id: 'j1', worker: 'cloudA', ts }),
          JSON.stringify({ event: 'job_completed', job_id: 'j1', ts: ts + 1 }),
          JSON.stringify({
            event: 'job_rejected',
            job_id: 'j2',
            worker: 'cloudB',
            reason: 'result already stored',
            ts: ts + 2,
          }),
          JSON.stringify({ event: 'job_completed', job_id: 'j3', ts: ts + 3 }), // 无承接记录（旧数据）
        ].join('\n') + '\n',
        'utf8',
      )
      const agg = readPpoAttribution(now, 0)
      expect(agg.ledgers).toBe(1)
      const done = agg.events.filter((e) => e.kind === 'done')
      expect(done.map((e) => e.worker).sort()).toEqual(['', 'cloudA'])
      expect(done.find((e) => e.worker === 'cloudA')!.course).toBe('k25')
      const rej = agg.events.filter((e) => e.kind === 'rejected')
      expect(rej).toHaveLength(1)
      expect(rej[0]!.worker).toBe('cloudB')

      // 组装视图：窗口含全部事件；未归属的旧 job_completed 保留成「(空 worker)」行（诚实）
      const stub: HistoryAggregate = {
        byDay: new Map(),
        byCourse: new Map(),
        rolling: new Map(),
        rollingTruncated: new Set(),
        sources: [],
        epochMs: 0,
        lastContrib: new Map(),
        latestRound: null,
      }
      const view = buildContributionView(stub, resolveWindow('today', now, 0))
      const cloudA = view.ppo.rows.find((r) => r.worker === 'cloudA')!
      expect(cloudA.done).toBe(1)
      expect(view.ppo.rows.find((r) => r.worker === 'cloudB')!.rejected).toBe(1)
      expect(view.ppo.totalDone).toBe(2)
      // 共享矩阵：cloudA 在 k25 有 1 个完成
      expect(view.matrix.ppoRows.find((r) => r.worker === 'cloudA')!.cells[0]!.done).toBe(1)
    })
  })
})

// ────────────────────────── ④ SSR（full / compact / NodePills 接入） ──────────────────────────

function sampleView(): ContributionView {
  const sampling = buildSamplingContribution([
    { id: 'self', split: { rollout: 5, eval: 1, fail: 0 } },
    { id: 'mac', split: { rollout: 0, eval: 0, fail: 2 } }, // 只有失败 ⇒ 份额「—」
  ])
  const ppo = buildPpoContribution([
    { worker: 'cloudA', done: 4, rejected: 1, inflight: 1 },
    { worker: 'cloudB', done: 2, rejected: 0 },
  ])
  return {
    sampling,
    ppo,
    matrix: buildContributionMatrix(
      {
        self: { k25: { rollout: 5, eval: 1, fail: 0 } },
        mac: { k25: { rollout: 0, eval: 0, fail: 2 } },
      },
      { cloudA: { k25: { done: 4, rejected: 1 } }, cloudB: { k25: { done: 2, rejected: 0 } } },
    ),
    samplingSources: { flows: 2, truncated: false, rollingTruncated: false },
    ppoSources: { ledgers: 1 },
  }
}

describe('WorkerContribution SSR（plan W3a/W3b）', () => {
  it('full 首帧：两组表头 + 份额列 + 口径脚注；缺数据渲染「—」（不是 0%）', () => {
    const html = renderToString(
      h(WorkerContribution, {
        variant: 'full',
        contribution: sampleView(),
        windowLabel: '今天',
      }),
    )
    expect(html).toContain('云端 PPO worker（job）')
    expect(html).toContain('采样节点（局）')
    expect(html).toContain('份额')
    expect(html).toContain('晚到·白算')
    expect(html).toContain('窗口「今天」')
    expect(html).toContain('课程矩阵') // 视图切换键在首帧
    expect(html).toContain('训练机本地时')
    // 分母 0（组内没有任何成功局）⇒ 份额「—」，不是 0%（G7）
    const zero = sampleView()
    zero.sampling = buildSamplingContribution([
      { id: 'self', split: { rollout: 0, eval: 0, fail: 1 } },
    ])
    const zeroHtml = renderToString(
      h(WorkerContribution, { variant: 'full', contribution: zero, windowLabel: '今天' }),
    )
    expect(zeroHtml).toContain('—')
    expect(zeroHtml).not.toContain('0.0%')
  })

  it('compact：一行（两组 top + 组总量），不铺开矩阵/表格', () => {
    const html = renderToString(
      h(WorkerContribution, { variant: 'compact', brief: compactSummary(sampleView()) }),
    )
    expect(html).toContain('tc-contrib-compact')
    expect(html).toContain('PPO')
    expect(html).toContain('采样')
    expect(html).toContain('cloudA')
    expect(html).not.toContain('tc-contrib-matrixwrap')
    expect(html).not.toContain('tc-contrib-table')
  })

  it('compact 缺数据 ⇒ 不渲染（空行会被读成「有东西没加载出来」）', () => {
    expect(renderToString(h(WorkerContribution, { variant: 'compact', brief: null }))).toBe('')
  })

  it('NodePills 接入：缩略行在节点区里（点击走既有 onMore）', () => {
    const html = renderToString(
      h(NodePills, {
        nodes: [],
        brief: compactSummary(sampleView()),
        onAction: () => {},
        onMore: () => {},
      }),
    )
    expect(html).toContain('tc-contrib-compact')
    expect(html).toContain('节点统计 ›')
  })
})

// ────────────────────────── R3：PPO 侧统一扫描 + memo 前置（reload-perf W3） ──────────────────────────

describe('server/contribution · 统一扫描与 memo 前置（reload-perf W3）', () => {
  it('W3-①：账本清单消费同一份扫描（不再独立 readdir）——源码结构断言', () => {
    const raw = readFileSync(join(DASHBOARD_ROOT, 'src', 'server', 'contribution.ts'), 'utf-8')
    // 只看代码行（注释里解释「不再自己 readdirSync」——那不是回退）。
    const code = raw
      .split('\n')
      .filter(
        (l) =>
          !l.trim().startsWith('//') && !l.trim().startsWith('*') && !l.trim().startsWith('/*'),
      )
      .join('\n')
    expect(code).toContain('scanPoolStreams(')
    expect(code).not.toContain('readdirSync')
    expect(code).not.toContain('existsSync')
  })

  it('W3-②：memo 判定在读取之前——窗口内第二次调用**零读盘**', () => {
    const now = Date.now()
    const ts = (now - 60_000) / 1000
    withPoolRoot([], (root) => {
      mkdirSync(join(root, 'k25'), { recursive: true })
      writeFileSync(
        join(root, 'k25', 'training_log.jsonl'),
        `${JSON.stringify({ event: 'job_completed', job_id: 'j1', ts })}\n`,
        'utf8',
      )
      invalidatePpoAttributionMemo()
      const first = readPpoAttribution(now, 30_000)
      expect(first.ledgers).toBe(1)
      // 同一窗口内再调（指纹相同 ⇒ 一字节账本都不读）
      const second = readPpoAttribution(now + 1000, 30_000)
      expect(second).toBe(first) // 同一对象（memo 命中）
      // 跨过窗口 ⇒ 重读（拿到新事件）
      writeFileSync(
        join(root, 'k25', 'training_log.jsonl'),
        [
          JSON.stringify({ event: 'job_completed', job_id: 'j1', ts }),
          JSON.stringify({ event: 'job_result_accepted', job_id: 'j2', worker: 'w1', ts: ts + 1 }),
          JSON.stringify({ event: 'job_completed', job_id: 'j2', ts: ts + 2 }),
        ].join('\n') + '\n',
        'utf8',
      )
      const third = readPpoAttribution(now + 60_000, 30_000)
      expect(third.events.length).toBeGreaterThan(first.events.length)
    })
  })

  it('W3-③：同一拍内采样聚合与 PPO 只 walk 一次（scans 计数只增 1）', () => {
    const now = Date.now()
    withPoolRoot([{ name: 'k25', meta: [metaRow('a1', now - 5 * 60_000)] }], (root) => {
      mkdirSync(join(root, 'k25'), { recursive: true })
      writeFileSync(
        join(root, 'k25', 'training_log.jsonl'),
        `${JSON.stringify({ event: 'job_completed', job_id: 'j1', ts: (now - 60_000) / 1000 })}\n`,
        'utf8',
      )
      invalidateNodeHistoryMemo()
      invalidatePpoAttributionMemo()
      resetPoolHistoryCounters()
      aggregateNodeHistory(now)
      readPpoAttribution(now, 0)
      // 第一次调用 walk 了一次；第二次调用（PPO）命中扫描 memo ⇒ scans 仍是 1。
      expect(poolHistoryCounters().scans).toBe(1)
    })
  })
})
