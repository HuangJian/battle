/** metrics-loot-pickdrop.test.ts — 指标表道具列「拾取数/掉落数」
 *  分层：src/web/view/format.ts + src/server/iters.ts（eval 聚合 totalPUSpawn）
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { fmtLootPickDrop, fmtPerGameAvg } from '../src/web/view'
import { readEvalSummaries } from '../src/server/iters'

describe('fmtLootPickDrop：每局平均拾取/掉落', () => {
  it('两侧都有 → `pick/drop`（2 位小数）', () => {
    expect(fmtLootPickDrop(20, 30, 10)).toBe('2.00/3.00')
  })
  it('掉落数缺失 → `pick/-`', () => {
    expect(fmtLootPickDrop(20, null, 10)).toBe('2.00/-')
  })
  it('拾取缺失 → `-`', () => {
    expect(fmtLootPickDrop(null, 30, 10)).toBe('-')
  })
  it('games=0 时显示总量，不除零', () => {
    expect(fmtLootPickDrop(3, 4, 0)).toBe('3.00/4.00')
    expect(fmtPerGameAvg(null, 5)).toBe('-')
  })
})

describe('iters 聚合 totalPUSpawn（eval_log）', () => {
  it('puSpawn* 分项之和 → totalPUSpawn', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-loot-spawn-'))
    try {
      const lines = [
        JSON.stringify({
          event: 'eval',
          iter: 0,
          wver: 'w',
          time: 't',
          stage: 1,
          seed: 1,
          outcome: 'stage_clear',
          win: 1,
          powerUpsCollected: 2,
          puSpawnBomb: 1,
          puSpawnTank: 1,
          puSpawnFreeze: 0,
          puSpawnShield: 0,
          puSpawnStar: 0,
        }),
        JSON.stringify({
          event: 'eval',
          iter: 0,
          wver: 'w',
          time: 't',
          stage: 1,
          seed: 2,
          outcome: 'gameover',
          win: 0,
          powerUpsCollected: 1,
          puSpawnBomb: 0,
          puSpawnTank: 0,
          puSpawnFreeze: 1,
          puSpawnShield: 0,
          puSpawnStar: 0,
        }),
        JSON.stringify({
          event: 'eval_summary',
          iter: 0,
          wver: 'w',
          time: 't',
          games: 2,
          wins: 1,
          winRate: 0.5,
          clears: 1,
          clearRate: 0.5,
          dropped: 0,
          sec: 1,
          outcomes: {},
        }),
      ]
      writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n'), 'utf-8')
      const s = readEvalSummaries(dir).get(0)
      expect(s).toBeDefined()
      expect(s!.totalPU).toBe(3)
      expect(s!.totalPUSpawn).toBe(3)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('分项全 0 但有拾取且 dims.loot 比值可推 → 反推 spawn', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-loot-ratio-'))
    try {
      const lines = [
        JSON.stringify({
          event: 'eval',
          iter: 0,
          wver: 'w',
          time: 't',
          stage: 1,
          seed: 1,
          outcome: 'stage_clear',
          win: 1,
          powerUpsCollected: 2,
          dims: { loot: 2 / 3 },
          puSpawnBomb: 0,
          puSpawnTank: 0,
          puSpawnFreeze: 0,
          puSpawnShield: 0,
          puSpawnStar: 0,
        }),
        JSON.stringify({
          event: 'eval_summary',
          iter: 0,
          wver: 'w',
          time: 't',
          games: 1,
          wins: 1,
          winRate: 1,
          clears: 1,
          clearRate: 1,
          dropped: 0,
          sec: 1,
          outcomes: {},
        }),
      ]
      writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n'), 'utf-8')
      const s = readEvalSummaries(dir).get(0)
      expect(s!.totalPU).toBe(2)
      expect(s!.totalPUSpawn).toBe(3) // round(2 / (2/3))
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('无任何 spawn 线索 → totalPUSpawn=null（UI 显示拾取/-）', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-loot-null-'))
    try {
      const lines = [
        JSON.stringify({
          event: 'eval',
          iter: 0,
          wver: 'w',
          time: 't',
          stage: 1,
          seed: 1,
          outcome: 'gameover',
          win: 0,
          powerUpsCollected: 1,
          dims: { loot: null },
        }),
        JSON.stringify({
          event: 'eval_summary',
          iter: 0,
          wver: 'w',
          time: 't',
          games: 1,
          wins: 0,
          winRate: 0,
          clears: 0,
          clearRate: 0,
          dropped: 0,
          sec: 1,
          outcomes: {},
        }),
      ]
      writeFileSync(path.join(dir, 'eval_log.jsonl'), lines.join('\n'), 'utf-8')
      const s = readEvalSummaries(dir).get(0)
      expect(s!.totalPU).toBe(1)
      expect(s!.totalPUSpawn).toBeNull()
      expect(fmtLootPickDrop(s!.totalPU, s!.totalPUSpawn, s!.games)).toBe('1.00/-')
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
