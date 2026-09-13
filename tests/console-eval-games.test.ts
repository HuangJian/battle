/** console-eval-games.test.ts — readLatestEvalGames（导出 replay 弹窗数据源）：
 *  latest summary 选择、(iter,wver) 配对、source 行排除、类型判定、
 *  承伤/杀与胜局残血推算、重复落账覆盖。 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readLatestEvalGames } from '../tools/training/console/iters'

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

describe('readLatestEvalGames', () => {
  it('无 eval_log / 无 summary → null', () => {
    expect(readLatestEvalGames(mkDir('missing'))).toBeNull()
    const dir = mkDir('nosummary')
    writeFileSync(path.join(dir, 'eval_log.jsonl'), gameRow({}) + '\n')
    expect(readLatestEvalGames(dir)).toBeNull()
  })

  it('选最大 iter 的 summary，行按 (iter, wver) 配对', () => {
    const dir = mkDir('pairing')
    const lines = [
      gameRow({ iter: 2, wver: 'wver2222', seed: 100 }),
      summaryRow({ iter: 2, wver: 'wver2222' }),
      gameRow({ seed: 101, outcome: 'stage_clear', win: 1, cleared: 1, kills: 5 }),
      gameRow({ seed: 102, outcome: 'max_ticks', win: 0, cleared: 0 }),
      summaryRow({ iter: 3, wver: 'wver3333', time: '2026-09-13 13:00:00' }),
      // it3 的 wver 不同 + 旧 iter 残留行——都不进 it3 视图
      gameRow({ iter: 4, wver: 'wver4444', seed: 103 }),
    ]
    writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n') + '\n')
    const v = readLatestEvalGames(dir)!
    expect(v.iter).toBe(3)
    expect(v.wver).toBe('wver3333')
    expect(v.time).toBe('2026-09-13 13:00:00')
    // it2/it4 的行都不进 it3 视图
    expect(v.rows.map((r) => r.seed)).toEqual([101, 102])
    expect(v.games).toBe(2)
  })

  it('类型判定：win=胜利 / max_ticks=超时 / 其余=失败；cleared 独立透出', () => {
    const dir = mkDir('classes')
    const lines = [
      gameRow({ seed: 1, outcome: 'stage_clear', win: 1, cleared: 1 }),
      gameRow({ seed: 2, outcome: 'max_ticks', win: 1, cleared: 1 }), // BONUS 截断胜局
      gameRow({ seed: 3, outcome: 'max_ticks', win: 0, cleared: 0 }), // 纯超时
      gameRow({ seed: 4, outcome: 'gameover', win: 0, cleared: 0 }), // 失败
      summaryRow({}),
    ]
    writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n') + '\n')
    const v = readLatestEvalGames(dir)!
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
    const dir = mkDir('residual')
    const lines = [
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
    ]
    writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n') + '\n')
    const v = readLatestEvalGames(dir)!
    const bySeed = new Map(v.rows.map((r) => [r.seed, r]))
    expect(bySeed.get(1)?.residualHp).toBe(163)
    expect(bySeed.get(1)?.dmgPerKill).toBeCloseTo(25)
    expect(bySeed.get(2)?.residualHp).toBeNull()
    expect(bySeed.get(2)?.dmgPerKill).toBeCloseTo(40)
    expect(bySeed.get(3)?.dmgTaken).toBeNull()
    expect(bySeed.get(3)?.dmgPerKill).toBeNull()
  })

  it('source 行（EvalBoard B/C 批）与坏行排除；同键重复落账取最后', () => {
    const dir = mkDir('dupes')
    const lines = [
      gameRow({ seed: 1, kills: 1 }),
      gameRow({ seed: 1, kills: 9 }), // 重试后覆盖
      gameRow({ seed: 2, source: 'evalboard', kills: 5 }), // B/C 行
      'not-json',
      summaryRow({}),
    ]
    writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n') + '\n')
    const v = readLatestEvalGames(dir)!
    expect(v.rows.length).toBe(1)
    expect(v.rows.find((r) => r.seed === 1)?.kills).toBe(9)
    expect(v.rows.find((r) => r.seed === 2)).toBeUndefined()
  })
})
