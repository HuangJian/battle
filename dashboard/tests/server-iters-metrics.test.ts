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
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
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
