/** console-eval-games.test.ts — 导出 replay 弹窗的数据源（server/iters.ts）：
 *  `readEvalGames(dir, iter?)`（缺省 = 最大 iter，与今天逐字一致；指定轮不回落到最新轮）
 *  与 `readEvalRoundOptions(dir)`（summary-only 单趟扫描的轮列表）。
 *  latest summary 选择、(iter,wver) 配对、source 行排除、类型判定、承伤/杀与胜局残血推算、
 *  重复落账覆盖、it0 基线轮、回填读数（reused_wver）、谓词与视图同源、单趟成本钉。 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readEvalGames, readEvalRoundOptions } from '../src/server/iters'

const dirs: string[] = []
afterAll(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
})

function mkDir(name: string): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), `eval-games-${name}-`))
  dirs.push(dir)
  return dir
}

/** eval 账本行（event=eval 最小集 + 可选扩展字段）。 */
function gameRow(extra: Record<string, unknown>): string {
  return JSON.stringify({
    event: 'eval',
    iter: 3,
    wver: 'wver3333',
    time: '2026-09-13 12:00:00',
    stage: 0,
    seed: 100,
    node: 'local',
    outcome: 'gameover',
    win: 0,
    cleared: 0,
    ticks: 1200,
    score: 0.4,
    kills: 2,
    ...extra,
  })
}

function summaryRow(extra: Record<string, unknown>): string {
  return JSON.stringify({ event: 'eval_summary', iter: 3, wver: 'wver3333', ...extra })
}

function mkLedger(name: string, lines: string[]): string {
  const dir = mkDir(name)
  writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n') + '\n')
  return dir
}

describe('readEvalGames', () => {
  it('无 eval_log / 无 summary → null', () => {
    expect(readEvalGames(mkDir('missing'))).toBeNull()
    const dir = mkLedger('nosummary', [gameRow({})])
    expect(readEvalGames(dir)).toBeNull()
  })

  it('缺省 = 选最大 iter 的 summary，行按 (iter, wver) 配对（回归钉：今天的行为）', () => {
    const dir = mkLedger('pairing', [
      gameRow({ iter: 2, wver: 'wver2222', seed: 100 }),
      summaryRow({ iter: 2, wver: 'wver2222' }),
      gameRow({ seed: 101, outcome: 'stage_clear', win: 1, cleared: 1, kills: 5 }),
      gameRow({ seed: 102, outcome: 'max_ticks', win: 0, cleared: 0 }),
      summaryRow({ iter: 3, wver: 'wver3333', time: '2026-09-13 13:00:00' }),
      // it3 的 wver 不同 + 旧 iter 残留行——都不进 it3 视图
      gameRow({ iter: 4, wver: 'wver4444', seed: 103 }),
    ])
    const v = readEvalGames(dir)!
    expect(v.iter).toBe(3)
    expect(v.wver).toBe('wver3333')
    expect(v.time).toBe('2026-09-13 13:00:00')
    // it2/it4 的行都不进 it3 视图
    expect(v.rows.map((r) => r.seed)).toEqual([101, 102])
    expect(v.games).toBe(2)
  })

  it('指定旧轮：只收该轮的行、wver 按该轮 summary 配对；不存在的轮 → null（不回落）', () => {
    const dir = mkLedger('byiter', [
      gameRow({ iter: 2, wver: 'wver2222', seed: 200, kills: 7 }),
      summaryRow({ iter: 2, wver: 'wver2222' }),
      gameRow({ iter: 3, wver: 'wver3333', seed: 300 }),
      summaryRow({ iter: 3, wver: 'wver3333' }),
    ])
    const v = readEvalGames(dir, 2)!
    expect(v.iter).toBe(2)
    expect(v.wver).toBe('wver2222')
    expect(v.rows.map((r) => r.seed)).toEqual([200])
    expect(v.rows[0]?.kills).toBe(7)
    // 99 轮不存在：null —— 绝不回落到最新轮（回落 = 拿别的轮的读数冒充）
    expect(readEvalGames(dir, 99)).toBeNull()
  })

  it('it0 基线轮可被选中并收行（iter < 0 的 summary 不进）', () => {
    const dir = mkLedger('it0', [
      summaryRow({ iter: -1, wver: 'wverneg0' }), // 合成行：不是真轮
      gameRow({ iter: 0, wver: 'wver0000', seed: 1, outcome: 'stage_clear', win: 1, cleared: 1 }),
      summaryRow({ iter: 0, wver: 'wver0000', time: '2026-09-13 09:00:00' }),
      gameRow({ iter: 5, wver: 'wver5555', seed: 5 }),
      summaryRow({ iter: 5, wver: 'wver5555' }),
    ])
    const v = readEvalGames(dir, 0)!
    expect(v.iter).toBe(0)
    expect(v.wver).toBe('wver0000')
    expect(v.rows.map((r) => r.seed)).toEqual([1])
    expect(v.wins).toBe(1)
    // 缺省仍是最大真轮（it0 不放行 iter<0 的合成行）
    expect(readEvalGames(dir)!.iter).toBe(5)
  })

  it('该轮只有 summary 没有逐局行 → 概要 + 空表（不是 null）', () => {
    const dir = mkLedger('summaryonly', [summaryRow({ iter: 7, wver: 'wver7777' })])
    const v = readEvalGames(dir, 7)!
    expect(v.games).toBe(0)
    expect(v.rows).toEqual([])
    expect(v.winRate).toBeNull()
  })

  it('类型判定：win=胜利 / max_ticks=超时 / 其余=失败；cleared 独立透出', () => {
    const dir = mkLedger('classes', [
      gameRow({ seed: 1, outcome: 'stage_clear', win: 1, cleared: 1 }),
      gameRow({ seed: 2, outcome: 'max_ticks', win: 1, cleared: 1 }), // BONUS 截断胜局
      gameRow({ seed: 3, outcome: 'max_ticks', win: 0, cleared: 0 }), // 纯超时
      gameRow({ seed: 4, outcome: 'gameover', win: 0, cleared: 0 }), // 失败
      summaryRow({}),
    ])
    const v = readEvalGames(dir)!
    const bySeed = new Map(v.rows.map((r) => [r.seed, r]))
    expect(bySeed.get(1)?.cls).toBe('win')
    expect(bySeed.get(2)?.cls).toBe('win')
    expect(bySeed.get(3)?.cls).toBe('timeout')
    expect(bySeed.get(3)?.cleared).toBe(false)
    expect(bySeed.get(4)?.cls).toBe('fail')
    expect(v.wins).toBe(2)
    expect(v.winRate).toBeCloseTo(0.5)
  })

  it('承伤/杀与胜局残血推算（败局残血恒 null）', () => {
    const dir = mkLedger('residual', [
      // 胜局：1 命 + 拾 tank 1 - 死 1 = 1 命容量 263；承伤 100 → 残血 163
      gameRow({
        seed: 1,
        outcome: 'stage_clear',
        win: 1,
        kills: 4,
        playerDamageTaken: 100,
        playerDeaths: 1,
        puGotTank: 1,
      }),
      // 败局：承伤/杀 有定义，残血 null
      gameRow({ seed: 2, kills: 2, playerDamageTaken: 80 }),
      // 缺 playerDamageTaken（老课程）→ 都 null
      gameRow({ seed: 3, kills: 1, playerDamageTaken: null }),
      summaryRow({}),
    ])
    const v = readEvalGames(dir)!
    const bySeed = new Map(v.rows.map((r) => [r.seed, r]))
    expect(bySeed.get(1)?.residualHp).toBe(163)
    expect(bySeed.get(1)?.dmgPerKill).toBeCloseTo(25)
    expect(bySeed.get(2)?.residualHp).toBeNull()
    expect(bySeed.get(2)?.dmgPerKill).toBeCloseTo(40)
    expect(bySeed.get(3)?.dmgTaken).toBeNull()
    expect(bySeed.get(3)?.dmgPerKill).toBeNull()
  })

  it('source 行（EvalBoard B/C 批）与坏行排除；同键重复落账取最后', () => {
    const dir = mkLedger('dupes', [
      gameRow({ seed: 1, kills: 1 }),
      gameRow({ seed: 1, kills: 9 }), // 重试后覆盖
      gameRow({ seed: 2, source: 'evalboard', kills: 5 }), // B/C 行
      'not-json',
      summaryRow({}),
    ])
    const v = readEvalGames(dir)!
    expect(v.rows.length).toBe(1)
    expect(v.rows.find((r) => r.seed === 1)?.kills).toBe(9)
    expect(v.rows.find((r) => r.seed === 2)).toBeUndefined()
  })
})

describe('readEvalRoundOptions', () => {
  it('多轮降序 + it0 在列表里 + 同 iter 两条取后写', () => {
    const dir = mkLedger('rounds', [
      summaryRow({ iter: 2, wver: 'wver2222', time: 't2', games: 200, wins: 60, winRate: 0.3 }),
      summaryRow({
        iter: 0,
        wver: 'wverb00',
        time: 't0',
        games: 200,
        wins: 40,
        winRate: 0.2,
      }),
      summaryRow({ iter: 5, wver: 'wver5555', time: 't5a', games: 100, wins: 50, winRate: 0.5 }),
      // 重开腿/补跑：同一 iter 的后写覆盖（列表只出一项）
      summaryRow({ iter: 5, wver: 'wver5b', time: 't5b', games: 200, wins: 120, winRate: 0.6 }),
    ])
    const rs = readEvalRoundOptions(dir)
    expect(rs.map((r) => r.iter)).toEqual([5, 2, 0])
    expect(rs[0]?.wver).toBe('wver5b')
    expect(rs[0]?.winRate).toBeCloseTo(0.6)
    expect(rs[2]?.iter).toBe(0)
  })

  it('谓词与视图同源：缺 wver / iter<0 的 summary 不进列表；reused_wver 标出', () => {
    const dir = mkLedger('predicate', [
      summaryRow({ iter: 1, wver: '' }), // 缺 wver：视图配不上，列表也不列
      summaryRow({ iter: -1, wver: 'wverneg' }), // 合成行
      summaryRow({ iter: 5, wver: 'wver5', games: 200, wins: 100 }),
      summaryRow({ iter: 6, wver: 'wver6', games: 200, wins: 100, reused_wver: true }),
    ])
    const rs = readEvalRoundOptions(dir)
    expect(rs.map((r) => r.iter)).toEqual([6, 5])
    expect(rs.find((r) => r.iter === 6)?.reusedWver).toBe(true)
    expect(rs.find((r) => r.iter === 5)?.reusedWver).toBe(false)
    // 与视图同源：缺 wver 的轮在 readEvalGames 里也拿不到数据
    expect(readEvalGames(dir, 1)).toBeNull()
  })

  it('winRate 推导：summary 缺 winRate ⇒ wins/games；缺 games/wins ⇒ null（不伪造 0）', () => {
    const dir = mkLedger('winrate', [
      summaryRow({ iter: 2, wver: 'wver2', games: 200, wins: 50 }),
      summaryRow({ iter: 3, wver: 'wver3' }),
      summaryRow({ iter: 4, wver: 'wver4', games: 0, wins: 0 }),
    ])
    const rs = readEvalRoundOptions(dir)
    const byIter = new Map(rs.map((r) => [r.iter, r]))
    expect(byIter.get(2)?.winRate).toBeCloseTo(0.25)
    expect(byIter.get(3)?.games).toBeNull()
    expect(byIter.get(3)?.winRate).toBeNull()
    expect(byIter.get(4)?.winRate).toBeNull()
  })

  it('无 eval_log ⇒ 空数组（不是 null：弹窗显示「暂无评估记录」）', () => {
    expect(readEvalRoundOptions(mkDir('norounds'))).toEqual([])
  })

  it('成本钉：单趟线性扫描——只读一次文件，parse 次数 = 非空行数（不做第二遍聚合）', () => {
    // 真实账本里逐局行占体积 99%（单轮 200 行 × 上百轮）：本函数按 summary 挑数据，
    // 但**必须一趟扫完**。未来若改成「readEvalSummaries 聚合 + 反推」或追加第二遍，
    // parse 次数 / 读盘次数会立刻翻倍——这两个计数就是这条纪律的探针。
    const lines = [summaryRow({ games: 200, wins: 60, winRate: 0.3 })]
    for (let i = 0; i < 200; i++) lines.push(gameRow({ seed: 1000 + i }))
    const dir = mkLedger('costpin', lines)

    const origParse = JSON.parse
    let parses = 0
    ;(JSON as { parse: typeof JSON.parse }).parse = ((s: string) => {
      parses++
      return origParse(s)
    }) as typeof JSON.parse
    try {
      expect(readEvalRoundOptions(dir).length).toBe(1)
    } finally {
      ;(JSON as { parse: typeof JSON.parse }).parse = origParse
    }
    expect(parses).toBe(lines.length) // 201 条非空行，一条不多（第二遍/聚合会把它翻倍）
  })
})
