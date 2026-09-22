/**
 * server-iters-round-actuals.test.ts — 云机/回传腿的逐局画像入口：
 * `it<N>/per-game.json`（表上「耗时/击杀/残血/道具」四列的云机数据源）。
 *
 * 分层：src/server/iters.ts（`readRoundActuals` / `aggregateActuals`）
 *
 * 为什么必须单独钉住：云机离线腿的**单局 manifest 从不回传**（轮末 prune 只留最近 2 个
 * job 目录，随后销毁），所以那四条腿的表曾经恒空。现在云机随轮账本行多带一份压缩画像
 * （`rl/reports.py::compact_per_game`），由回传/导入落成 `it<N>/per-game.json`——若这条
 * 读取路径断掉，症状是「表在、列全空」，与本机腿的 manifest 扫描红法完全不同。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { readIterMetrics, readRoundActuals } from '../src/server/iters'

/** 一局压缩画像（字段名与单局 manifest 逐字相同，见 `rl/reports.py::_PER_GAME_FIELDS`）。 */
function game(stage: number, seed: number, over: Record<string, unknown> = {}) {
  return {
    stage,
    seed,
    nSamples: 100,
    kills: 6,
    ticks: 2000,
    outcome: 'stage_clear',
    powerUpsCollected: 1,
    playerDamageTaken: 300,
    playerDeaths: 0,
    puGotTank: 1,
    startLives: 1,
    enemyTotal: 20,
    puSpawnBomb: 1,
    ...over,
  }
}

function withTmp<T>(fn: (dir: string) => T): T {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-round-actuals-'))
  try {
    return fn(dir)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

describe('console/iters 云机腿逐局画像（it<N>/per-game.json）', () => {
  it('只有 per-game.json（无单局 manifest）也能算出四列', () => {
    withTmp((dir) => {
      mkdirSync(path.join(dir, 'it3'), { recursive: true })
      writeFileSync(
        path.join(dir, 'it3', 'per-game.json'),
        JSON.stringify([
          game(1, 11),
          game(1, 12, { kills: 4, ticks: 1000, outcome: 'lives_lost' }),
        ]),
        'utf-8',
      )
      const a = readRoundActuals(dir, 3)
      expect(a).not.toBeNull()
      expect(a!.games).toBe(2)
      expect(a!.totalKills).toBe(10)
      expect(a!.totalPU).toBe(2) // 道具列（每局 1 个）
      expect(a!.avgTicks).toBe(1500) // 耗时列
      // 残血列：容量 = 263×2（1 命 + puGotTank 一命）− 300 承伤 = 226；
      // 败局（lives_lost）按口径不进残血均值，所以是单局值。
      expect(a!.avgResidualHp).toBe(226)
      expect(a!.avgWinTicks).toBe(2000)
      expect(a!.avgLossTicks).toBe(1000)
    })
  })

  it('同一 (stage,seed) 多行 ⇒ 取样本数最多的那条（与 manifest 去重同口径）', () => {
    withTmp((dir) => {
      mkdirSync(path.join(dir, 'it1'), { recursive: true })
      writeFileSync(
        path.join(dir, 'it1', 'per-game.json'),
        JSON.stringify([
          game(2, 7, { nSamples: 10, kills: 1 }),
          game(2, 7, { nSamples: 90, kills: 9 }),
        ]),
        'utf-8',
      )
      const a = readRoundActuals(dir, 1)
      expect(a!.games).toBe(1)
      expect(a!.totalKills).toBe(9)
    })
  })

  it('per-game.json 缺席时回落到单局 manifest 目录扫描', () => {
    withTmp((dir) => {
      const g = path.join(dir, 'it5', 'stage1', 'rl_s0_seed42')
      mkdirSync(g, { recursive: true })
      writeFileSync(
        path.join(g, 'manifest.json'),
        JSON.stringify({ stage: 1, seed: 42, nSamples: 5, kills: 3, ticks: 900 }),
        'utf-8',
      )
      const a = readRoundActuals(dir, 5)
      expect(a!.games).toBe(1)
      expect(a!.totalKills).toBe(3)
      expect(a!.avgTicks).toBe(900)
    })
  })

  it('坏 JSON / 空数组 / 非对象行 ⇒ 跳过或回落，不抛', () => {
    withTmp((dir) => {
      mkdirSync(path.join(dir, 'it9'), { recursive: true })
      writeFileSync(path.join(dir, 'it9', 'per-game.json'), '{ not json', 'utf-8')
      expect(readRoundActuals(dir, 9)).toBeNull()

      writeFileSync(path.join(dir, 'it9', 'per-game.json'), '[]', 'utf-8')
      expect(readRoundActuals(dir, 9)).toBeNull()

      writeFileSync(
        path.join(dir, 'it9', 'per-game.json'),
        JSON.stringify([null, 7, { seed: 1 }, game(3, 1, { kills: 2 })]),
        'utf-8',
      )
      const a = readRoundActuals(dir, 9)
      expect(a!.games).toBe(1) // 无 stage/seed 的行丢掉，不编数字
      expect(a!.totalKills).toBe(2)
    })
  })

  it('readIterMetrics 的行带 actuals（表上四列真正读的那一格）', () => {
    withTmp((dir) => {
      mkdirSync(path.join(dir, 'it2'), { recursive: true })
      writeFileSync(
        path.join(dir, 'it2', 'per-game.json'),
        JSON.stringify([game(1, 1), game(1, 2)]),
        'utf-8',
      )
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({ event: 'iteration', iter: 2, time: 't-2', winRate: 0.5 }) + '\n',
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(1)
      expect(rows[0]!.actuals).not.toBeNull()
      expect(rows[0]!.actuals!.totalKills).toBe(12)
      expect(rows[0]!.actuals!.avgTicks).toBe(2000)
    })
  })
})
