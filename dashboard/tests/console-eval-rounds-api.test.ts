/** console-eval-rounds-api.test.ts — 轮次选择器的 API 面（server/api/eval-games.ts）：
 *  `?iter=` 三态解析、`buildEvalRoundsView`（summary-only、降序、含 it0）、
 *  `buildEvalGamesView(course, iter)`（指定轮 / 不存在轮 `available:false` 不回落）。
 *
 *  测试面住 `REPO_ROOT/tmp/<唯一课程名>/eval_log.jsonl`：这两个函数读的就是控制台
 *  运行时的真路径（`tmp/<course>/eval_log.jsonl`）——只有走真路径才算验收「弹窗看到的数据」。
 *  课程名带随机后缀，用例自身建/删，绝不碰真课程的账本。 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../src/core/paths'
import {
  buildEvalGamesView,
  buildEvalRoundsView,
  parseEvalIterParam,
} from '../src/server/api/eval-games'

const COURSE = `__test-eval-rounds-${process.pid}-${Date.now()}`
const TRAJ = path.join(REPO_ROOT, 'tmp', COURSE)

afterAll(() => {
  rmSync(TRAJ, { recursive: true, force: true })
})

function summary(extra: Record<string, unknown>): string {
  return JSON.stringify({ event: 'eval_summary', iter: 3, wver: 'wver3333', ...extra })
}

function game(extra: Record<string, unknown>): string {
  return JSON.stringify({
    event: 'eval',
    iter: 3,
    wver: 'wver3333',
    stage: 0,
    seed: 100,
    outcome: 'gameover',
    win: 0,
    cleared: 0,
    ticks: 1200,
    kills: 2,
    ...extra,
  })
}

describe('parseEvalIterParam（?iter= 三态，§3.2 钉死）', () => {
  it('缺省 / 空串 ⇒ undefined（最新轮）；整数 ⇒ 数字；非整数 ⇒ null（400）', () => {
    expect(parseEvalIterParam(null)).toBeUndefined()
    expect(parseEvalIterParam('')).toBeUndefined()
    expect(parseEvalIterParam('2')).toBe(2)
    expect(parseEvalIterParam('0')).toBe(0)
    expect(parseEvalIterParam('2.5')).toBeNull()
    expect(parseEvalIterParam('abc')).toBeNull()
    expect(parseEvalIterParam('2x')).toBeNull()
  })
})

describe('buildEvalRoundsView / buildEvalGamesView（真路径 tmp/<course>）', () => {
  it('建账本：it0 + it2 + it5（含一行 reused_wver 回填）', () => {
    mkdirSync(TRAJ, { recursive: true })
    const ledger = [
      summary({ iter: 0, wver: 'wverb00', time: 't0', games: 200, wins: 40, winRate: 0.2 }),
      game({ iter: 0, wver: 'wverb00', seed: 1 }),
      summary({ iter: 2, wver: 'wver222', time: 't2', games: 100, wins: 50, winRate: 0.5 }),
      game({ iter: 2, wver: 'wver222', seed: 2 }),
      summary({ iter: 5, wver: 'wver555', time: 't5', games: 200, wins: 100, winRate: 0.5 }),
      game({ iter: 5, wver: 'wver555', seed: 5 }),
      // eval_a_once 的「同 wver 已在别轮评完」回填：本轮没有自己的逐局行。
      summary({ iter: 8, wver: 'wver555', time: 't8', games: 200, wins: 100, reused_wver: true }),
    ]
    writeFileSync(path.join(TRAJ, 'eval_log.jsonl'), ledger.join('\n') + '\n')
    // 夹具自检：账本真落盘了（否则下面几条会以「无账本」的姿势假绿）。
    expect(readFileSync(path.join(TRAJ, 'eval_log.jsonl'), 'utf8').trim().split('\n')).toHaveLength(
      ledger.length,
    )
  })

  it('/api/evalRounds：降序、含 it0；回填轮带 reusedWver', () => {
    const v = buildEvalRoundsView(COURSE)
    expect(v.course).toBe(COURSE)
    expect(v.rounds.map((r) => r.iter)).toEqual([8, 5, 2, 0])
    expect(v.rounds[3]?.winRate).toBeCloseTo(0.2)
    expect(v.rounds.find((r) => r.iter === 8)?.reusedWver).toBe(true)
    expect(v.rounds.find((r) => r.iter === 5)?.reusedWver).toBe(false)
  })

  it('回填轮（reused_wver）打开是一张空表：逐局行归属原 iter——弹窗据此标「回填读数」', () => {
    const v = buildEvalGamesView(COURSE, 8)
    expect(v.available).toBe(true)
    expect(v.games).toBe(0) // 视图口径 = 本轮**自己的**逐局行数，不拿 summary 的 200 冒充
    expect(v.rows).toEqual([]) // 本轮的逐局行一条都没有 ⇒ 无法勾选导出
    const r5 = buildEvalGamesView(COURSE, 5)
    expect(r5.rows.map((r) => r.seed)).toEqual([5])
  })

  it('/api/evalGames 缺省 = 最新轮；指定旧轮取该轮；不存在的轮 ⇒ available:false 不回落', () => {
    const dft = buildEvalGamesView(COURSE)
    expect(dft.available).toBe(true)
    expect(dft.iter).toBe(8) // 最新轮就是那个回填轮（真实形状：默认落在它上面、表是空的）
    expect(dft.rows).toEqual([])

    const it5 = buildEvalGamesView(COURSE, 5)
    expect(it5.iter).toBe(5)
    expect(it5.rows.map((r) => r.seed)).toEqual([5])

    const old = buildEvalGamesView(COURSE, 2)
    expect(old.available).toBe(true)
    expect(old.iter).toBe(2)
    expect(old.rows.map((r) => r.seed)).toEqual([2])

    const missing = buildEvalGamesView(COURSE, 99)
    expect(missing.available).toBe(false)
    expect(missing.iter).toBe(-1)
    expect(missing.rows).toEqual([])
  })

  it('空课程 ⇒ 两个视图都给出缺省形状（不抛）', () => {
    expect(buildEvalGamesView('').available).toBe(false)
    expect(buildEvalRoundsView('').rounds).toEqual([])
  })

  it('无该课程账本 ⇒ 轮列表空数组（弹窗显示「暂无评估记录」）', () => {
    expect(buildEvalRoundsView(`__test-no-such-${process.pid}`).rounds).toEqual([])
  })
})

describe('归因不变量（结构守卫，2026-10-06 F4）', () => {
  it('受理 evalReplays 后、spawn 前必须删旧 manifest——否则硬杀时旧产物会被当本次结果', () => {
    // 行为用例做不到这一档：真正的验收在真机（导出中强杀 python）。这里把「受理即作废」
    // 钉在不变量上：rmSync 必须在 spawn 之前、且在 busy.add 之后（受理成功才作废）。
    const src = readFileSync(path.join(import.meta.dir, '..', 'src/server/api/route.ts'), 'utf8')
    const iBusy = src.indexOf('busy.add(REPLAY_EXPORT_BUSY_KEY)')
    const iRm = src.indexOf('rmSync(replayExportPaths(ctx.course).manifest, { force: true })')
    const iSpawn = src.indexOf("const { spawn } = await import('child_process')")
    expect(iBusy).toBeGreaterThan(0)
    expect(iRm).toBeGreaterThan(iBusy)
    expect(iSpawn).toBeGreaterThan(iRm)
  })
})
