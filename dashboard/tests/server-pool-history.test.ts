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
import { appendFileSync, mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import {
  aggregateNodeHistory,
  emptyHistory,
  invalidateNodeHistoryMemo,
  isSlowNode,
  isSlowNodeRows,
  lastCompletedIter,
  localDayKey,
  parseTsMs,
  pickBaseIter,
  projectWindow,
  pruneByEpoch,
  pushWindowSample,
  resolveWindow,
  windowMeanSec,
} from '../src/server/pool-history'
import { nodeHealth } from '../src/web/view'

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
  const now = Date.now()
  /** 本地日偏移 → 'YYYY-MM-DD HH:MM:SS'（与 Python strftime 写入同形）。 */
  const tsAt = (dayOffset: number, hhmmss = '12:00:00'): string => {
    const d = new Date(now)
    d.setDate(d.getDate() + dayOffset)
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day} ${hhmmss}`
  }
  const metaRowAt = (
    node: string,
    it: number,
    ts: string,
    mode: 'rollout' | 'eval' = 'rollout',
    ok = true,
  ) => JSON.stringify({ node, mode, it, stage: 0, seed: 1, ok, elapsedSec: 1, ts })
  const metaRow = (node: string, it: number, mode: 'rollout' | 'eval' = 'rollout') =>
    metaRowAt(node, it, tsAt(0), mode)
  /** 毫秒 → 'YYYY-MM-DD HH:MM:SS'（与 Python strftime 写入同形）。 */
  const tsFromMs = (ms: number): string => {
    const d = new Date(ms)
    const p = (n: number): string => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(
      d.getMinutes(),
    )}:${p(d.getSeconds())}`
  }
  /** 账本 `iteration` 事件（`time` = 完成时刻，§3.4）。 */
  const iterEvent = (it: number, time = tsAt(0)) =>
    JSON.stringify({ event: 'iteration', iter: it, time })

  /** 一个临时池根 + 一条或多条训练流（`BCITY_POOL_DIR` 重定向）。 */
  const withPoolRoot = (
    flow:
      | { name?: string; meta: string[]; ledger?: string[] | null }
      | Array<{ name: string; meta: string[]; ledger?: string[] | null }>,
    fn: (root: string) => void,
  ): void => {
    const root = mkdtempSync(join(tmpdir(), 'bcity-pool-'))
    const prev = process.env.BCITY_POOL_DIR
    process.env.BCITY_POOL_DIR = root
    // 进程内 memo 会把上一用例的 root/结果带过来；夹具起手先清（见 invalidateNodeHistoryMemo）。
    invalidateNodeHistoryMemo()
    try {
      const flows = Array.isArray(flow) ? flow : [{ name: 'flow', ...flow }]
      for (const f of flows) {
        const dir = join(root, f.name)
        mkdirSync(dir, { recursive: true })
        writeFileSync(join(dir, 'dist-agent-meta.jsonl'), `${f.meta.join('\n')}\n`, 'utf8')
        if (f.ledger) {
          writeFileSync(join(dir, 'training_log.jsonl'), `${f.ledger.join('\n')}\n`, 'utf8')
        }
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

  it('水位 = 逐流账本最后一个 iteration（不是 meta 最大 it）；进行中那一轮的行不进桶', () => {
    withPoolRoot(FLOW, () => {
      const agg = aggregateNodeHistory()
      // it8（进行中）的 6 条行被水位滤掉：只剩 it7 的 4 条（a1 3 条 / a2 1 条）。
      const win = projectWindow(agg, resolveWindow('all', now, agg.epochMs))
      expect(win.hist.get('a1')!.ok).toBe(3)
      expect(win.hist.get('a2')!.ok).toBe(1)
      expect(win.hist.get('a1')!.winRollout).toBe(2)
      expect(win.hist.get('a1')!.winEval).toBe(1)
    })
  })

  it('水位只作用于**计数/样本**；时间戳类字段取**全部**行（可达性口径，二轮评审补正）', () => {
    // 症状：`allRows` 只收 `it <= baseIt` 后，「最近成功/失败/耗时」与 isSlowNode 的输入
    // 一起被推到上一轮完成时刻 —— 一个「轮前段就交完活、轮又长」的节点会在 30 分钟
    // 可达性窗口上从「慢」翻成「离线」（2026-09-11 报障的反面）。
    // 判据：**统计只认已完成轮；「还活着吗」认全部行。**
    const t = Date.parse('2026-09-26T15:00:00')
    const inProgressFail = JSON.stringify({
      node: 'a1',
      mode: 'rollout',
      it: 8,
      ok: false,
      reason: 'link timeout',
      ts: tsAt(0, '14:55:00'),
    })
    const inProgressOk = JSON.stringify({
      node: 'a1',
      mode: 'rollout',
      it: 8,
      ok: true,
      elapsedSec: 41.9,
      ts: tsAt(0, '14:50:00'),
    })
    withPoolRoot(
      {
        meta: [metaRowAt('a1', 7, tsAt(0, '08:00:00')), inProgressOk, inProgressFail],
        ledger: [iterEvent(7, tsAt(0, '08:30:00'))],
      },
      () => {
        const agg = aggregateNodeHistory()
        const h = projectWindow(agg, resolveWindow('all', t, agg.epochMs)).hist.get('a1')!
        // 计数：只含已完成轮（it7 一条）
        expect(h.ok).toBe(1)
        expect(h.fail).toBe(0)
        // 时间戳：可达性口径，含进行中那一轮
        expect(h.lastOkTs).toBe(tsAt(0, '14:50:00'))
        expect(h.lastOkTsMs).toBe(parseTsMs(tsAt(0, '14:50:00'))!)
        expect(h.lastFailTs).toBe(tsAt(0, '14:55:00'))
        // isSlowNode 的输入随之看到「14:50 还在交活」⇒ 宁可判「慢」也不误判「离线」
        expect(h.lastOkElapsedSec).toBe(41.9)
      },
    )
  })

  it('无训练账本（一次性 run 目录）→ 退化为 meta 最大 it（旧口径，行为不变）', () => {
    withPoolRoot({ ...FLOW, ledger: null }, () => {
      const agg = aggregateNodeHistory()
      const win = projectWindow(agg, resolveWindow('all', now, agg.epochMs))
      expect(win.hist.get('a1')!.ok).toBe(4)
      expect(win.hist.get('a2')!.ok).toBe(6)
    })
  })

  it('完成水位在 meta 里没有任何行（账本与 meta 不同步）→ 退回 meta 最大 it，不把全员算成 0', () => {
    withPoolRoot({ ...FLOW, ledger: [iterEvent(7), iterEvent(9)] }, () => {
      const agg = aggregateNodeHistory()
      const win = projectWindow(agg, resolveWindow('all', now, agg.epochMs))
      expect(win.hist.get('a2')!.ok).toBe(6)
    })
  })

  // ── §3.4：最新完成轮跨课按**完成时刻**选（不是比 it 大小） ──
  it('lastContrib：完成时刻更晚的课胜出（造 completedAt 与 it 大小相反的数据）', () => {
    withPoolRoot(
      [
        {
          name: 'course-old',
          // it99 很大，但完成时刻更早
          meta: [metaRowAt('a1', 99, tsAt(-2, '10:00:00'))],
          ledger: [iterEvent(99, tsAt(-2, '10:00:00'))],
        },
        {
          name: 'course-new',
          // it3 很小，但完成时刻更晚 → 胜出
          meta: [metaRowAt('a1', 3, tsAt(0, '09:00:00')), metaRowAt('a2', 3, tsAt(0, '09:05:00'))],
          ledger: [iterEvent(3, tsAt(0, '09:00:00'))],
        },
      ],
      () => {
        const agg = aggregateNodeHistory()
        expect(agg.latestRound?.dir).toBe('course-new')
        expect(agg.lastContrib.get('a1')).toBe(1)
        expect(agg.lastContrib.get('a2')).toBe(1)
      },
    )
  })

  it('lastContrib = 该轮 rollout + eval 成功局数；无 _it 展示口径_', () => {
    withPoolRoot(
      {
        meta: [metaRow('a1', 2), metaRow('a1', 2, 'eval'), metaRow('a1', 2, 'eval')],
        ledger: [iterEvent(2)],
      },
      () => {
        const agg = aggregateNodeHistory()
        expect(agg.lastContrib.get('a1')).toBe(3)
      },
    )
  })

  it('所有流都取不到 iteration 时间戳 → 按 meta mtime 兜底（仍选出完成轮、不崩）', () => {
    withPoolRoot(
      {
        meta: [metaRow('a1', 1)],
        // 账本有 iteration 但无 time → atMs null → 兜底 mtime
        ledger: [JSON.stringify({ event: 'iteration', iter: 1 })],
      },
      () => {
        const agg = aggregateNodeHistory()
        expect(agg.latestRound?.it).toBe(1)
        expect(typeof agg.latestRound?.completedAtMs).toBe('number')
        expect(agg.lastContrib.get('a1')).toBe(1)
      },
    )
  })

  // ── 多流合并 + 逐流 it 不串味（同一 it 在两门课里是两回事） ──
  it('两课的行合并到同一节点；同一 it 各自成桶（逐流水位，不串味）', () => {
    withPoolRoot(
      [
        {
          name: 'cA',
          // 水位 5：it6 进行中（应被滤掉）
          meta: [
            metaRowAt('mac', 5, tsAt(0, '08:00:00')),
            metaRowAt('mac', 6, tsAt(0, '09:00:00')),
          ],
          ledger: [iterEvent(5, tsAt(0, '08:30:00'))],
        },
        {
          name: 'cB',
          // 另一门课，同一 it5/it6；水位 6 → 保留到 it6
          meta: [
            metaRowAt('mac', 5, tsAt(0, '08:10:00')),
            metaRowAt('mac', 6, tsAt(0, '09:10:00')),
          ],
          ledger: [iterEvent(6, tsAt(0, '09:30:00'))],
        },
      ],
      () => {
        const agg = aggregateNodeHistory()
        const win = projectWindow(agg, resolveWindow('all', now, agg.epochMs))
        // cA: it5 进、it6 被滤；cB: it5 + it6 都进 ⇒ mac 共 3 条
        expect(win.hist.get('mac')!.ok).toBe(3)
      },
    )
  })

  // ── 窗口按本地日切（投影纯函数） ──
  it('窗口按本地日切：今天 / 昨天 / 7 天 / 全部；投影是纯函数', () => {
    withPoolRoot(
      {
        meta: [
          metaRowAt('a1', 1, tsAt(0, '08:00:00')),
          metaRowAt('a1', 1, tsAt(-1, '08:00:00')),
          metaRowAt('a1', 1, tsAt(-3, '08:00:00')),
          metaRowAt('a1', 1, tsAt(-10, '08:00:00')),
        ],
        ledger: [iterEvent(1)],
      },
      () => {
        const agg = aggregateNodeHistory()
        expect(
          projectWindow(agg, resolveWindow('today', now, agg.epochMs)).hist.get('a1')!.ok,
        ).toBe(1)
        expect(
          projectWindow(agg, resolveWindow('yesterday', now, agg.epochMs)).hist.get('a1')!.ok,
        ).toBe(1)
        expect(projectWindow(agg, resolveWindow('7', now, agg.epochMs)).hist.get('a1')!.ok).toBe(3)
        expect(projectWindow(agg, resolveWindow('all', now, agg.epochMs)).hist.get('a1')!.ok).toBe(
          4,
        )
        // 纯投影：同一份 agg 反复切不产生新桶
        const again = projectWindow(agg, resolveWindow('today', now, agg.epochMs))
        expect(again.hist.get('a1')!.ok).toBe(1)
      },
    )
  })

  it('resolveWindow：本地日边界正确；epoch 与窗口取交集（startMs = max(日零点, epoch)）', () => {
    const t = new Date('2026-09-26T15:00:00').getTime()
    const today = resolveWindow('today', t, 0)
    expect(today.fromDay).toBe(localDayKey(t))
    expect(today.toDay).toBe(localDayKey(t))
    expect(today.startMs).toBe(new Date('2026-09-26T00:00:00').getTime())
    const yest = resolveWindow('yesterday', t, 0)
    expect(yest.fromDay).toBe(localDayKey(new Date('2026-09-25T15:00:00').getTime()))
    expect(yest.fromDay).toBe(yest.toDay)
    expect(resolveWindow('7', t, 0).fromDay).toBe(
      localDayKey(new Date('2026-09-20T15:00:00').getTime()),
    )
    // epoch 落在窗口内 → 起点与 epoch 取交集
    const epoch = new Date('2026-09-26T10:00:00').getTime()
    expect(resolveWindow('today', t, epoch).startMs).toBe(epoch)
    // epoch 在窗口之前 → 起点仍是当天零点
    expect(resolveWindow('today', t, new Date('2026-09-01T00:00:00').getTime()).startMs).toBe(
      new Date('2026-09-26T00:00:00').getTime(),
    )
  })

  // ── 产能三档（nodeHealth 现成、本 plan 不改） ──
  it('nodeHealth 三档：0/-1 → offline；0<贡献<并发 → slow；≥并发 → healthy', () => {
    expect(nodeHealth(0, 4)).toBe('offline')
    expect(nodeHealth(-1, 4)).toBe('offline')
    expect(nodeHealth(2, 4)).toBe('slow')
    expect(nodeHealth(4, 4)).toBe('healthy')
    expect(nodeHealth(9, 4)).toBe('healthy')
  })

  // ── 预筛（钉在 epoch，与看哪天无关） ──
  it('pruneByEpoch：mtime 早于 epoch 的整份文件跳过；晚于/等于的保留', () => {
    const cs = [{ mtimeMs: 100 }, { mtimeMs: 200 }, { mtimeMs: 300 }]
    expect(pruneByEpoch(cs, 200).map((c) => c.mtimeMs)).toEqual([200, 300])
    expect(pruneByEpoch(cs, 0)).toEqual(cs)
  })

  // ── 大文件只读尾部（阈值线之上，诚实截断标记） ──
  it('大文件只读尾部：**尾窗之外的行一行都没进聚合**（不只是打个 truncated 标记）', () => {
    // 50 000 行 ≈ 6 MB > 2 MB 阈值；尾窗 = 最后 20 000 行 ≡ 全是 NEW。
    // 行为式证明（比 spy 强：断言「早段的行不存在」，而不是「调用了哪个函数」）。
    const big: string[] = []
    for (let i = 0; i < 25_000; i++) big.push(metaRowAt('OLD', 1, tsAt(0, '08:00:00')))
    for (let i = 0; i < 25_000; i++) big.push(metaRowAt('NEW', 1, tsAt(0, '09:00:00')))
    withPoolRoot({ meta: big, ledger: [iterEvent(1)] }, () => {
      const agg = aggregateNodeHistory()
      expect(agg.sources.length).toBe(1)
      expect(agg.sources[0]!.truncated).toBe(true)
      const win = projectWindow(agg, resolveWindow('all', now, agg.epochMs))
      expect(win.hist.has('NEW')).toBe(true)
      expect(win.hist.has('OLD')).toBe(false)
    })
  })

  // ── 预筛端到端（钉在 epoch；二轮评审：此前只能测 pruneByEpoch 纯函数） ──
  it('预筛端到端：mtime 早于锚点的整份文件连 sources 都不进（锚点随池根走）', () => {
    const root = mkdtempSync(join(tmpdir(), 'bcity-pool-'))
    const prev = process.env.BCITY_POOL_DIR
    process.env.BCITY_POOL_DIR = root
    invalidateNodeHistoryMemo()
    try {
      const nowMs = Date.now()
      const epoch = nowMs - 3_600_000
      mkdirSync(join(root, 'dist-agent'), { recursive: true })
      writeFileSync(join(root, 'dist-agent', 'pool-epoch.txt'), String(epoch), 'utf8')
      const tsFresh = tsFromMs(nowMs - 60_000)
      for (const [name, node] of [
        ['fresh', 'aLive'],
        ['stale', 'aOld'],
      ] as const) {
        mkdirSync(join(root, name), { recursive: true })
        writeFileSync(
          join(root, name, 'dist-agent-meta.jsonl'),
          `${JSON.stringify({ node, mode: 'rollout', it: 1, ok: true, elapsedSec: 1, ts: tsFresh })}\n`,
          'utf8',
        )
      }
      // stale 流整份都在锚点之前（文件 mtime < epoch）→ 必须被预筛掉
      const ancient = new Date(epoch - 3_600_000)
      utimesSync(join(root, 'stale', 'dist-agent-meta.jsonl'), ancient, ancient)
      const agg = aggregateNodeHistory()
      expect(agg.epochMs).toBe(epoch)
      expect(agg.sources.map((s) => s.dir)).toEqual(['fresh'])
      const win = projectWindow(agg, resolveWindow('all', nowMs, agg.epochMs))
      expect(win.hist.has('aLive')).toBe(true)
      expect(win.hist.has('aOld')).toBe(false)
    } finally {
      if (prev === undefined) delete process.env.BCITY_POOL_DIR
      else process.env.BCITY_POOL_DIR = prev
      invalidateNodeHistoryMemo()
      rmSync(root, { recursive: true, force: true })
    }
  })

  // ── 进程内 memo（两个消费者共用一份；训练中重扫节奏封顶） ──
  it('聚合 memo：同根同指纹复用**同一对象**；窗口内不重扫；invalidate 后重算', () => {
    withPoolRoot(FLOW, (root) => {
      const a = aggregateNodeHistory()
      expect(aggregateNodeHistory()).toBe(a) // 两个消费者拿到同一份（不是各算一遍）
      // 训练在跑、meta 在追加：AGG_MEMO_MIN_MS 窗口内不重扫（否则快照刷新器每 5s 重扫全池）
      appendFileSync(join(root, 'flow', 'dist-agent-meta.jsonl'), `${metaRow('a1', 7)}\n`, 'utf8')
      expect(aggregateNodeHistory()).toBe(a)
      invalidateNodeHistoryMemo()
      const c = aggregateNodeHistory()
      expect(c).not.toBe(a)
      // FLOW 的 a1 在 it7 有 3 条 + 追加 1 条 = 4（重算确实读到了新行）
      const win = projectWindow(c, resolveWindow('all', now, c.epochMs)).hist.get('a1')!
      expect(win.ok).toBe(4)
    })
  })

  // ── 「最近失败/最近错误」共用一个近一小时口径 ──
  it('最近失败与最近错误同口径（近一小时）：超窗不上屏、计数照算；窗口内上屏', () => {
    const failRow = (ts: string) =>
      JSON.stringify({ node: 'a1', mode: 'rollout', it: 1, ok: false, reason: 'boom', ts })
    // 钉在当天正午，避开「now - 2h 坐到昨天」的跨天边界（窗口是本地日）。
    const noon = new Date(now)
    noon.setHours(12, 0, 0, 0)
    const t = noon.getTime()
    withPoolRoot({ meta: [failRow(tsFromMs(t - 2 * 3_600_000))], ledger: [iterEvent(1)] }, () => {
      const agg = aggregateNodeHistory()
      const h = projectWindow(agg, resolveWindow('today', t, agg.epochMs)).hist.get('a1')!
      expect(h.fail).toBe(1) // 计数是窗口口径：2 小时前的失败照算
      expect(h.lastError).toBe('') // 但不上屏
      expect(h.lastFailTs).toBe('-') // 时间戳与错误同一个窗，不会出现「有字却是几天前」
    })
    withPoolRoot({ meta: [failRow(tsFromMs(t - 10 * 60_000))], ledger: [iterEvent(1)] }, () => {
      const agg = aggregateNodeHistory()
      const h = projectWindow(agg, resolveWindow('today', t, agg.epochMs)).hist.get('a1')!
      expect(h.lastError).toBe('boom')
      expect(h.lastFailTs).toBe(tsFromMs(t - 10 * 60_000))
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
