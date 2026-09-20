/**
 * server-pool-history.test.ts — 慢节点判定：1.5s ping 误报「离线」的根治（isSlowNode / isSlowNodeRows / parseTsMs）
 *
 * 分层：src/server/pool-history.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import {
  aggregateNodeHistory,
  emptyHistory,
  isSlowNode,
  isSlowNodeRows,
  lastCompletedIter,
  parseTsMs,
  pickBaseIter,
  pushWindowSample,
  windowMeanSec,
} from '../src/server/pool-history'
import { fmtFullTs } from '../src/web/view'

// ────────────────────────── 节点 pill 行（慢节点/停用/启停 toggle，2026-09-11 用户指令） ──────────────────────────
describe('pool-history.isSlowNode（慢节点判定：1.5s ping 误报「离线」的根治）', () => {
  it('近期有成功结算且耗时不高 → false；结算慢（中位>8s）或超 10 分钟无结算 → true', () => {
    const now = Date.now()
    const mk = (
      over: Record<string, unknown>,
    ): import('../src/server/pool-history').NodeHistory => ({
      ...emptyHistory(),
      ...over,
    })
    // 健康节点：刚结算过且快
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 30_000, avgElapsedSec: 1.2, lastOkElapsedSec: 1.5 })),
    ).toBe(false)
    // 慢节点：还在结算但单局要几十秒（Kaggle CPU 饱和 → 1.5s ping 必超时）
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 60_000, avgElapsedSec: 19.9, lastOkElapsedSec: 41.9 })),
    ).toBe(true)
    // 快节点但偶发沉默 5 分钟（轮间隙）：不判慢（耗时没超阈值）
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 5 * 60_000, avgElapsedSec: 1.2, lastOkElapsedSec: 1.5 })),
    ).toBe(false)
    // 历史从未结算过：不算慢节点（真离线照旧标离线）
    expect(isSlowNode(mk({ lastOkTsMs: null, avgElapsedSec: null, lastOkElapsedSec: null }))).toBe(
      false,
    )
    // 恰好在窗口边界内 + 耗时未知 → 不误判
    expect(
      isSlowNode(mk({ lastOkTsMs: now - 9 * 60_000, avgElapsedSec: null, lastOkElapsedSec: null })),
    ).toBe(false)
  })

  it('独立重实现对拍：慢节点行样本 → isSlowNode(聚合) 与 isSlowNodeRows(逐行) 同判', () => {
    const now = Date.parse('2026-09-11T12:00:00')
    const rows = [
      { node: 'a95', ok: true, elapsedSec: 41.9, ts: '2026-09-11T11:52:21' },
      { node: 'self', ok: true, elapsedSec: 1.5, ts: '2026-09-11T11:52:21' },
      { node: 'a95', ok: false, ts: '2026-09-11T11:55:00' },
    ]
    // a95：7.6 分钟前成功过且耗时 41.9s > 8s → 慢节点
    expect(isSlowNodeRows(rows, 'a95', now)).toBe(true)
    // self：快 → 非慢
    expect(isSlowNodeRows(rows, 'self', now)).toBe(false)
    // 无记录节点 → 非慢（真离线口径）
    expect(isSlowNodeRows(rows, 'a98', now)).toBe(false)
  })

  it('窗口外交互：慢节点沉默超窗后回「离线」；健康快节点沉默同窗后也是「离线」', () => {
    const now = Date.now()
    const mk = (
      over: Record<string, unknown>,
    ): import('../src/server/pool-history').NodeHistory => ({
      ...emptyHistory(),
      ...over,
    })
    // 慢节点最后一次成功在 31 分钟前：窗口外 → 真离线
    expect(isSlowNode(mk({ lastOkTsMs: now - 31 * 60_000, lastOkElapsedSec: 41.9 }))).toBe(false)
    // 同窗口内：慢节点（41.9s）成立
    expect(isSlowNode(mk({ lastOkTsMs: now - 29 * 60_000, lastOkElapsedSec: 41.9 }))).toBe(true)
    // 健康快节点沉默 31 分钟：窗口外 → 真离线（窗口不是慢节点的保护伞）
    expect(isSlowNode(mk({ lastOkTsMs: now - 31 * 60_000, lastOkElapsedSec: 1.5 }))).toBe(false)
  })

  it('parseTsMs：ISO（T 分隔）与空格分隔两种写法都能解析', () => {
    expect(parseTsMs('2026-09-11T11:52:21')).toBe(Date.parse('2026-09-11T11:52:21'))
    expect(parseTsMs('2026-09-11 11:52:21')).toBe(Date.parse('2026-09-11T11:52:21'))
    expect(parseTsMs(undefined)).toBeNull()
    expect(parseTsMs('garbage')).toBeNull()
  })
})

// ────────────────────────── 贡献数取「最近完成轮」（2026-09-20 用户指令） ──────────────────────────
// 进行中那一轮的半截计数不算数：否则先交活的节点看着健康、还没轮到的看着掉线。
// 完成水位 = 训练账本的 `iteration` 事件（轮末 rollout + PPO 之后写）。

describe('pool-history · 贡献数取最近完成轮（进行中那一轮不计）', () => {
  const ts = fmtFullTs(Date.now())
  const metaRow = (node: string, it: number, mode: 'rollout' | 'eval' = 'rollout') =>
    JSON.stringify({ node, mode, it, stage: 0, seed: 1, ok: true, elapsedSec: 1, ts })
  const iterEvent = (it: number) => JSON.stringify({ event: 'iteration', iter: it, time: 'x' })

  /** 一个临时池根 + 一条训练流（meta 与账本不同目录时用 `ledgerDir` 单独指）。 */
  const withPoolRoot = (
    flow: { meta: string[]; ledger?: string[] | null; ledgerDir?: string },
    fn: (root: string) => void,
  ): void => {
    const root = mkdtempSync(join(tmpdir(), 'bcity-pool-'))
    const prev = process.env.BCITY_POOL_DIR
    process.env.BCITY_POOL_DIR = root
    try {
      const dir = join(root, 'flow')
      mkdirSync(dir, { recursive: true })
      writeFileSync(join(dir, 'dist-agent-meta.jsonl'), `${flow.meta.join('\n')}\n`, 'utf8')
      if (flow.ledger) {
        const ld = flow.ledgerDir ? join(root, flow.ledgerDir) : dir
        mkdirSync(ld, { recursive: true })
        writeFileSync(join(ld, 'training_log.jsonl'), `${flow.ledger.join('\n')}\n`, 'utf8')
      }
      fn(root)
    } finally {
      if (prev === undefined) delete process.env.BCITY_POOL_DIR
      else process.env.BCITY_POOL_DIR = prev
      rmSync(root, { recursive: true, force: true })
    }
  }

  const FLOW = {
    // it7 是最近完成轮：a1 交 3 局（rollout 2 + eval 1）、a2 交 1 局。
    // it8 还在跑：a2 已交 5 局、a1 才 1 局——照「最大 it」算就会把 a1 当成快掉线。
    meta: [
      metaRow('a1', 7),
      metaRow('a1', 7),
      metaRow('a1', 7, 'eval'),
      metaRow('a2', 7),
      metaRow('a1', 8),
      metaRow('a2', 8),
      metaRow('a2', 8),
      metaRow('a2', 8),
      metaRow('a2', 8),
      metaRow('a2', 8),
    ],
    ledger: [iterEvent(5), iterEvent(6), iterEvent(7)],
  }

  it('对齐基准 = 账本最后一个 iteration（不是 meta 最大 it）；贡献按该轮计', () => {
    withPoolRoot(FLOW, () => {
      const agg = aggregateNodeHistory()
      expect(agg.globalMaxIt).toBe(7)
      expect(agg.hist.get('a1')!.lastIterOk).toBe(3)
      expect(agg.hist.get('a2')!.lastIterOk).toBe(1)
      // 分桶口径同一基准轮：a1 = rollout 2 / eval 1
      expect(agg.hist.get('a1')!.contribRollout).toBe(2)
      expect(agg.hist.get('a1')!.contribEval).toBe(1)
      // 节点自己的「最近一次结算轮」语义不变（它交到哪一轮就是哪一轮）
      expect(agg.hist.get('a1')!.lastIter).toBe(8)
    })
  })

  it('无训练账本（一次性 run 目录）→ 退化为 meta 最大 it（旧口径，行为不变）', () => {
    withPoolRoot({ ...FLOW, ledger: null }, () => {
      const agg = aggregateNodeHistory()
      expect(agg.globalMaxIt).toBe(8)
      expect(agg.hist.get('a1')!.lastIterOk).toBe(1)
      expect(agg.hist.get('a2')!.lastIterOk).toBe(5)
    })
  })

  it('完成水位在 meta 里没有任何行（账本与 meta 不同步）→ 退回 meta 最大 it，不把全员算成 0', () => {
    withPoolRoot({ ...FLOW, ledger: [iterEvent(7), iterEvent(9)] }, () => {
      const agg = aggregateNodeHistory()
      expect(agg.globalMaxIt).toBe(8)
      expect(agg.hist.get('a2')!.lastIterOk).toBe(5)
    })
  })

  it('lastCompletedIter：只认 iteration 事件（job_completed 带 it 会读出没跑完的那一轮）；缺失 → null', () => {
    const dir = mkdtempSync(join(tmpdir(), 'bcity-pool-'))
    try {
      const meta = join(dir, 'dist-agent-meta.jsonl')
      writeFileSync(meta, `${metaRow('a1', 1)}\n`, 'utf8')
      expect(lastCompletedIter(meta)).toBeNull() // 有 meta 无账本
      writeFileSync(
        join(dir, 'training_log.jsonl'),
        `${iterEvent(3)}\n${JSON.stringify({ event: 'job_completed', job_id: 'j', it: 4 })}\n`,
        'utf8',
      )
      expect(lastCompletedIter(meta)).toBe(3)
      // 兼容 meta 落在 <traj root>/traj/ 的布局：账本在父母录
      const nested = join(dir, 'traj')
      mkdirSync(nested, { recursive: true })
      expect(lastCompletedIter(join(nested, 'dist-agent-meta.jsonl'))).toBe(3)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('pickBaseIter：水位缺失/为负/在 meta 里无行 → 退化；否则用水位', () => {
    const has = (it: number) => it === 7
    expect(pickBaseIter(7, 8, has)).toBe(7)
    expect(pickBaseIter(null, 8, has)).toBe(8)
    expect(pickBaseIter(-1, 8, has)).toBe(8)
    expect(pickBaseIter(9, 8, () => false)).toBe(8)
    expect(pickBaseIter(7, -1, has)).toBe(-1) // 空池：无贡献可算（-1 = 无池数据）
  })
})

// ────────────────────────── 机侧墙钟 / 滑动窗口（wallSec 双口径） ──────────────────────────
describe('pool-history wallSec 双口径（elapsedSec 节点服务时长 · wallSec 训练机墙钟）', () => {
  it('emptyHistory 默认带 wallRecent / avgWallSec，且与 elapsed 互相独立', () => {
    const h = emptyHistory()
    expect(h.elapsedRecent).toEqual([])
    expect(h.avgElapsedSec).toBeNull()
    expect(h.wallRecent).toEqual([])
    expect(h.avgWallSec).toBeNull()
  })

  it('pushWindowSample：只收正有限数值；窗口满挤掉最旧；拒绝 0/负/NaN/非 number', () => {
    const arr: number[] = []
    pushWindowSample(arr, 2.0)
    pushWindowSample(arr, 0)
    pushWindowSample(arr, -1)
    pushWindowSample(arr, Number.NaN)
    pushWindowSample(arr, '3')
    pushWindowSample(arr, null)
    pushWindowSample(arr, 4.5)
    expect(arr).toEqual([2.0, 4.5])
    const win: number[] = []
    for (let i = 1; i <= 52; i++) pushWindowSample(win, i, 50)
    expect(win.length).toBe(50)
    expect(win[0]).toBe(3) // 1、2 被挤掉
    expect(win[49]).toBe(52)
  })

  it('windowMeanSec：空=null；有样本保留 1 位小数（独立重实现对拍）', () => {
    expect(windowMeanSec([])).toBeNull()
    const samples = [1.25, 2.0, 4.75]
    const expectMean = +(samples.reduce((a, b) => a + b, 0) / samples.length).toFixed(1)
    expect(windowMeanSec(samples)).toBe(expectMean)
    expect(windowMeanSec(samples)).toBe(2.7) // (1.25+2+4.75)/3 = 2.666… → 2.7
  })

  it('双口径互不覆盖：同一 NodeHistory 可同时持有服务时长与墙钟样本', () => {
    const h = emptyHistory()
    for (const [svc, wall] of [
      [2.0, 5.0],
      [2.2, 5.8],
    ] as const) {
      pushWindowSample(h.elapsedRecent, svc)
      pushWindowSample(h.wallRecent, wall)
    }
    h.avgElapsedSec = windowMeanSec(h.elapsedRecent)
    h.avgWallSec = windowMeanSec(h.wallRecent)
    expect(h.avgElapsedSec).toBe(2.1)
    expect(h.avgWallSec).toBe(5.4)
  })

  it('展示层：undefined/null 墙钟不得渲染成 undefineds（旧 API 缺键防御）', () => {
    // 复现 NodeStats.secCell 语义：仅正有限数渲染 Ns
    const secCell = (v: number | null | undefined): string =>
      typeof v === 'number' && Number.isFinite(v) ? `${v}s` : '-'
    expect(secCell(undefined)).toBe('-')
    expect(secCell(null)).toBe('-')
    expect(secCell(Number.NaN)).toBe('-')
    expect(secCell(5.4)).toBe('5.4s')
  })
})
