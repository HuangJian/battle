/**
 * server-iters-metrics.test.ts — §361② 完整指标表不截断：600 轮返回最近 500
 * + 阶段耗时字段（pure_collect_sec / ppo_cloud_sec / dist_phase_sec）读取
 *
 * 分层：src/server/iters.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { readIterMetrics } from '../src/server/iters'
import { phaseSecs } from '../src/web/view'

describe('console/iters §361②：完整指标表不截断（MAX 500 上限）', () => {
  it('600 轮日志 → 返回最近 500 轮', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-361-'))
    try {
      const lines: string[] = []
      for (let i = 0; i < 600; i++) {
        lines.push(
          JSON.stringify({ event: 'iteration', iter: i, time: '', winRate: 0.5, score_mean: 0 }),
        )
      }
      writeFileSync(path.join(dir, 'training_log.jsonl'), lines.join('\n'), 'utf-8')
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(500)
      expect(rows[0]!.iter).toBe(599)
      expect(rows[499]!.iter).toBe(100)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('console/iters 行为指标聚合（eval 逐局行）', () => {
  it('同一 (iter,wver) 的逐局值 → 六种统计量挂在 evalData 上', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-behavior-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({ event: 'iteration', iter: 3, time: '', winRate: 0.5, score_mean: 0 }),
        'utf-8',
      )
      const evalRows = [
        { event: 'eval', iter: 3, wver: 'w3', stage: 1, seed: 11, win: true, dangerTicks: 4 },
        { event: 'eval', iter: 3, wver: 'w3', stage: 1, seed: 12, win: false, dangerTicks: 8 },
        // 别的 wver 不混入本 summary（按 (iter,wver) 配对）
        { event: 'eval', iter: 3, wver: 'other', stage: 1, seed: 13, win: true, dangerTicks: 99 },
        {
          event: 'eval_summary',
          iter: 3,
          wver: 'w3',
          time: '',
          games: 2,
          wins: 1,
          winRate: 0.5,
        },
      ]
      writeFileSync(
        path.join(dir, 'eval_log.jsonl'),
        evalRows.map((r) => JSON.stringify(r)).join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      const bm = rows[0]!.evalData!.behaviorMetrics!
      expect(bm.dangerTicks).toEqual({
        mean: 6,
        median: 6,
        min: 4,
        max: 8,
        p90: 7.6,
        p99: 7.96,
      })
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('旧账本无这些字段 ⇒ behaviorMetrics 为 null（不编 0）', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-behavior-old-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({ event: 'iteration', iter: 3, time: '', winRate: 0.5, score_mean: 0 }),
        'utf-8',
      )
      writeFileSync(
        path.join(dir, 'eval_log.jsonl'),
        [
          { event: 'eval', iter: 3, wver: 'w3', stage: 1, seed: 11, win: true },
          { event: 'eval_summary', iter: 3, wver: 'w3', time: '', games: 1, wins: 1 },
        ]
          .map((r) => JSON.stringify(r))
          .join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows[0]!.evalData!.behaviorMetrics).toBeNull()
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('console/iters 逐局指标聚合（rollout 画像）', () => {
  it('per-game.json 的 metricValues → 六种统计量挂到 actuals/IterRow 上', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-rollout-behavior-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({ event: 'iteration', iter: 3, time: '', winRate: 0.5, score_mean: 0 }),
        'utf-8',
      )
      const itDir = path.join(dir, 'it3')
      mkdirSync(itDir, { recursive: true })
      writeFileSync(
        path.join(itDir, 'per-game.json'),
        JSON.stringify([
          { stage: 1, seed: 11, nSamples: 1, metricValues: { dangerTicks: 4, wallPressTicks: 10 } },
          { stage: 1, seed: 12, nSamples: 1, metricValues: { dangerTicks: 8, wallPressTicks: 20 } },
        ]),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows[0]!.actuals!.behaviorMetrics!.dangerTicks).toEqual({
        mean: 6,
        median: 6,
        min: 4,
        max: 8,
        p90: 7.6,
        p99: 7.96,
      })
      expect(rows[0]!.actuals!.behaviorMetrics!.wallPressTicks.mean).toBe(15)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})

describe('console/iters 阶段耗时字段', () => {
  it('新账本三键读入；phaseSecs 拆出准确三段', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-phase-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({
          event: 'iteration',
          iter: 3,
          time: '2026-09-16 10:00:00',
          winRate: 0.2,
          score_mean: 1,
          rollout_sec: 500,
          ppo_sec: 150,
          pure_collect_sec: 420,
          ppo_cloud_sec: 100,
          dist_phase_sec: 30,
        }),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows[0]!.pureCollectSec).toBe(420)
      expect(rows[0]!.ppoCloudSec).toBe(100)
      expect(rows[0]!.distPhaseSec).toBe(30)
      expect(phaseSecs(rows[0]!)).toEqual({ rollout: 420, ppo: 100, net: 80 }) // 30 + (150-100)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('旧账本无三键 → null，phaseSecs 回退 rollout/ppo 且 net=0', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-legacy-'))
    try {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({
          event: 'iteration',
          iter: 1,
          time: '',
          winRate: 0.1,
          score_mean: 0,
          rollout_sec: 60,
          ppo_sec: 40,
        }),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows[0]!.pureCollectSec).toBeNull()
      expect(rows[0]!.ppoCloudSec).toBeNull()
      expect(rows[0]!.distPhaseSec).toBeNull()
      expect(phaseSecs(rows[0]!)).toEqual({ rollout: 60, ppo: 40, net: 0 })
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
