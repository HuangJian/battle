/**
 * server-iters-eval-wire-shape.test.ts — 云机补传的 eval 逐局行不是本机那种「全字段平铺」，
 * 而是 `nn-training/remote/deliver_wire.py::slim_eval_row` 的**白名单 + `metricValues` 快照**
 * 投影：白名单外的数值列（`enemyTotal` / `powerUpsSpawned` / `playerHpRatio` / `startLives` …）
 * 只活在嵌套的 `metricValues` 里。
 *
 * 2026-11-11 用户报障（`o2-reflex`）：云机跑的 it290/295 指标表「歼灭率 8.4%、道具 0.35/0.35」，
 * 本地跑的 it300 是「87.4%、0.39/0.91」。前者正是这两条降级路径：
 *   · `enemyTotal` 缺席 ⇒ 退回关卡敌数反查（classic 关 20 敌）⇒ 699 杀 / (400×20) = 8.4%；
 *   · `powerUpsSpawned` 缺席 ⇒ 退回 `dims.loot` 比值（该维 = 拾取数本身）⇒ spawn 被写成 collected。
 * 两种形态必须聚出**同一份**读数：白名单里的列看顶层，其余列看 `metricValues`。
 *
 * 分层：src/server/iters.ts（`readEvalSummaries` → `collectEvalSummaries` 的取数口径）。
 * 白名单在上面的 Python 投影里，故这里**独立重实现**一份（不 import 生产代码）。
 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { readEvalGames, readEvalSummaries } from '../src/server/iters'

const dirs: string[] = []
afterAll(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
})

/** 一门课 = `tmp/<课>/eval_log.jsonl`；目录名就是课程名（`courseFromTrajDir`）。 */
function mkCourse(name: string, lines: unknown[]): string {
  const root = mkdtempSync(path.join(os.tmpdir(), 'eval-wire-'))
  dirs.push(root)
  const dir = path.join(root, name)
  mkdirSync(dir)
  writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.map((l) => JSON.stringify(l)).join('\n'))
  return dir
}

/** `deliver_wire.EVAL_ROW_WIRE_KEYS` 的独立重实现（云机实时体保留的顶层键）。 */
const WIRE_KEYS = new Set([
  'event',
  'iter',
  'wver',
  'stage',
  'seed',
  'time',
  'node',
  'outcome',
  'win',
  'cleared',
  'ticks',
  'score',
  'quality',
  'kills',
  'playerDamageTaken',
  'playerDeaths',
  'puGotTank',
  'powerUpsCollected',
  'dims',
  'metricValues',
])

/** 全字段平铺行 → 云机实时体形态（白名单 + 其余数值列进 `metricValues`）。 */
function toWire(row: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const metrics: Record<string, number> = {}
  for (const [k, v] of Object.entries(row)) {
    if (WIRE_KEYS.has(k) && k !== 'metricValues') out[k] = v
    else if (typeof v === 'number' && Number.isFinite(v)) metrics[k] = v
  }
  if (Object.keys(metrics).length > 0) out.metricValues = metrics
  return out
}

/** 一局（本机写的形态）：2 敌、掉 3 枚道具、只捡 1 枚、剩半条命。 */
function game(seed: number, spawn: number): Record<string, unknown> {
  return {
    event: 'eval',
    iter: 7,
    wver: 'w7',
    stage: 3,
    seed,
    node: 'self',
    outcome: 'stage_clear',
    win: true,
    cleared: true,
    ticks: 400,
    score: 0.9,
    quality: 0.9,
    kills: 1,
    enemyTotal: 2,
    enemyHits: 2,
    hitRate: 0.5,
    powerUpsSpawned: spawn,
    powerUpsCollected: 1,
    puSpawnBomb: spawn,
    puSpawnTank: 0,
    puSpawnFreeze: 0,
    puSpawnShield: 0,
    puSpawnStar: 0,
    playerDamageTaken: 50,
    playerDeaths: 0,
    puGotTank: 0,
    playerShots: 4,
    playerHpRatio: 0.5,
    startLives: 1,
    firstKillTick: 300,
    stuckTicks: 0,
    cellsVisited: 40,
    // o2 系列 `dims.loot` 就是「本局拾到几枚」（不是 collected/spawn），比值反推会得到 collected
    dims: { loot: 1 },
  }
}

const GAMES = [game(31, 3), game(32, 1)]
const SUMMARY = {
  event: 'eval_summary',
  iter: 7,
  wver: 'w7',
  games: 2,
  wins: 2,
  winRate: 1,
  clears: 2,
  clearRate: 1,
  dropped: 0,
  sec: 4,
  outcomes: { stage_clear: 2 },
}

/** 期望值：2 杀（两局各 1） / (2+2) 敌 = 0.5；道具掉 3+1、拾 1+1。 */
const TOTAL_KILLS = 2

describe('eval 逐局行的两种形态（本机平铺 / 云机白名单+快照）聚合必须一致', () => {
  it('本机平铺行：对照组（全字段在顶层）', () => {
    const s = readEvalSummaries(mkCourse('o2-wire-flat', [...GAMES, SUMMARY])).get(7)!
    expect(s.totalKills).toBe(TOTAL_KILLS)
    expect(s.totalPU).toBe(2)
    expect(s.totalPUSpawn).toBe(4)
    expect(s.killRate).toBe(0.5)
    expect(s.avgResidualHpPct!).toBeCloseTo(Math.round(0.5 * 263) / 263, 10)
  })

  it('云机投影行：白名单外的列从 metricValues 读，读数逐字段相同', () => {
    const flat = readEvalSummaries(mkCourse('o2-wire-flat-b', [...GAMES, SUMMARY])).get(7)!
    const wire = readEvalSummaries(mkCourse('o2-wire-cloud', [...GAMES.map(toWire), SUMMARY])).get(
      7,
    )!
    // 歼灭率不能退回「关卡敌数反查」（classic 关 20 敌 ⇒ 2/40 = 5%）
    expect(wire.killRate).toBe(0.5)
    // 掉落不能退回 dims.loot 比值（那会得到 collected = 2）
    expect(wire.totalPUSpawn).toBe(4)
    expect(wire).toEqual(flat)
  })
})

describe('逐局 eval 弹窗（readEvalGames）同一套取数口径', () => {
  it('云机投影行的残血是精确值，不退成「满血 − 整局承伤」的下限', () => {
    const flat = readEvalGames(mkCourse('o2-games-flat', [...GAMES, SUMMARY]))!
    const wire = readEvalGames(mkCourse('o2-games-cloud', [...GAMES.map(toWire), SUMMARY]))!
    // 半条命 = 0.5 × 263 = 132；缺 playerHpRatio 时公式会给出 263 − 50 = 213
    expect(wire.rows[0]!.residualHp).toBe(Math.round(0.5 * 263))
    expect(wire.rows).toEqual(flat.rows)
  })
})
