/**
 * web-view-rows.test.ts — DS-U1 指标行分组 / 过滤 / 排序（13 列 + eval 子行语义）
 *
 * 分层：src/web/view/rows.ts
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import {
  filterGroups,
  iterGroups,
  keywordMatch,
  latestRow,
  sortRows,
  statusFromRecent,
  type IterRow,
} from '../src/web/view'

// ────────────────────────── 纯函数：指标行 / 排序 / 过滤 ──────────────────────────
function fakeRow(iter: number, evalData: IterRow['evalData'] = null): IterRow {
  return {
    iter,
    time: '2026-09-07 09:00:00',
    winRate: 0.1,
    scoreMean: 1,
    scoreStd: 0.1,
    samples: 100,
    rolloutSec: 60,
    ppoSec: 120,
    pureCollectSec: null,
    ppoCloudSec: null,
    distPhaseSec: null,
    kl: 0.02,
    entropy: 0.3,
    policyLoss: 0.1,
    valueLoss: 0.2,
    meanRet: 0.1,
    lr: 0.0001,
    expectedGames: 100,
    halted: false,
    topDims: '',
    avgTicks: 900,
    accuracy: 0.5,
    loot: 0.1,
    kills: 5,
    actuals: null,
    evalData,
  }
}

describe('view 指标行分组 / 过滤 / 排序（DS-U1 13 列 + eval 子行语义）', () => {
  const evalData = {
    time: 't',
    games: 10,
    wins: 4,
    winRate: 0.4,
    clears: 1,
    clearRate: 0.1,
    dropped: 0,
    sec: 60,
    wver: 'w',
    outcomes: {},
    avgTicks: 800,
    avgWinTicks: null,
    totalKills: 40,
    totalPU: 5,
    avgResidualHp: null,
    avgLossTicks: null,
    dmgPerKill: null,
    scoreMean: 0.5,
    scoreStd: 0.1,
  }
  const rows = [fakeRow(3), fakeRow(4, evalData), fakeRow(5)]

  it('iterGroups 时间倒序（新轮在前）', () => {
    expect(iterGroups(rows).map((g) => g.iter)).toEqual([5, 4, 3])
  })

  it('filterGroups：all 全保 / rollout 只主行 / eval 只子行', () => {
    const groups = iterGroups(rows)
    expect(filterGroups(groups, 'all')).toHaveLength(3)
    expect(filterGroups(groups, 'rollout')).toHaveLength(2)
    const ev = filterGroups(groups, 'eval')
    expect(ev).toHaveLength(1)
    expect(ev[0]!.iter).toBe(4)
  })

  it('latestRow：空 → null；取最大 iter（固定头部状态条口径）', () => {
    expect(latestRow([])).toBeNull()
    expect(latestRow([fakeRow(3), fakeRow(9), fakeRow(5)])!.iter).toBe(9)
  })

  it('sortRows：数值优先 + null 沉底 + 字符串 localeCompare', () => {
    const rows = [{ k: 3 }, { k: 1 }, { k: 2 }]
    expect(sortRows(rows, 'k', 'asc').map((r) => r.k)).toEqual([1, 2, 3])
    expect(sortRows(rows, 'k', 'desc').map((r) => r.k)).toEqual([3, 2, 1])
    const withNull = [{ k: null }, { k: 5 }]
    expect(sortRows(withNull, 'k', 'asc').map((r) => r.k)).toEqual([5, null])
    const strs = sortRows([{ s: 'b' }, { s: 'a' }], 's', 'asc')
    expect(strs.map((r) => r.s)).toEqual(['a', 'b'])
  })

  it('keywordMatch：空词放行；大小写不敏感；缺失字段安全', () => {
    expect(keywordMatch({ a: 'Hello' }, ['a'], '')).toBe(true)
    expect(keywordMatch({ a: 'Hello' }, ['a'], 'HELLO')).toBe(true)
    expect(keywordMatch({ a: 5 }, ['a'], '5')).toBe(true)
    expect(keywordMatch({ a: 'x' }, ['b'], 'x')).toBe(false)
  })

  it('statusFromRecent：≥90% 健康 / ≥70% 波动 / <70% 异常 / 空 = 无数据', () => {
    expect(statusFromRecent([])).toBe('nodata')
    expect(statusFromRecent([true, true, true, true, true, true, true, true, true, true])).toBe(
      'healthy',
    )
    expect(statusFromRecent([true, true, true, true, true, true, true, true, false, false])).toBe(
      'warn',
    )
    expect(statusFromRecent([false, false])).toBe('bad')
  })
})
