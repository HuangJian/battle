/**
 * server-iters-rollout-end.test.ts — 采集收官即刻上屏：`rollout_end` 行 → 走势图 / 指标表
 *
 * 分层：src/server/iters.ts（`buildIterRows`）
 *
 * ★ 用户口径（2026-10-19）：**每个 it rollout 一结束就显示该 it 的指标**。
 *   旧行为是只认 `iteration` 事件——它排在一轮 13 步的第 ⑪ 位，前面压着 `ppo`
 *   （远端模式 = 发布 job → 等云机跑完 → 权重回传，几十分钟量级）。于是采集早就
 *   结束了，控制台却要等 W(it+1) 回来才多一个点，看起来像"卡住"。
 *
 * 两条硬口径（破了就是旧病复发）：
 *   ① **PPO 侧列必须是 NaN 缺口，不是 0** —— `rollout_end` 行落账时梯度步还没跑，
 *      写 0 会在图上多画一个假零点；
 *   ② **本轮 `iteration` 行落账后 `rollout_end` 行必须自动消失**（同 iter 取最后一条），
 *      否则结算完的轮会同时留两行读数。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'fs'
import { readIterMetrics } from '../src/server/iters'

function withTmp<T>(fn: (dir: string) => T): T {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-rollout-end-'))
  try {
    return fn(dir)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

/** 一局 manifest（`readRoundActuals` 的扫描目标；字段取它真读的那几个）。 */
function manifest(stage: number, seed: number, over: Record<string, unknown> = {}) {
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

describe('console/iters rollout 收官即刻入表（不等 PPO 回传）', () => {
  it('只有 rollout_end 行 ⇒ 该 it 进表，PPO 列是 NaN 缺口（不是 0）', () => {
    withTmp((dir) => {
      mkdirSync(path.join(dir, 'it5', 's0'), { recursive: true })
      writeFileSync(
        path.join(dir, 'it5', 's0', 'manifest.json'),
        JSON.stringify(manifest(1, 101)),
        'utf-8',
      )
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        JSON.stringify({
          event: 'rollout_end',
          iter: 5,
          time: '2026-10-19 07:00:00',
          winRate: 0.42,
          score_mean: 0.75,
          score_std: 0.11,
          dim_means: { progress: 0.3, loot: 0.2 },
          samples: 12345,
          ticks: 90000,
          rollout_sec: 31.5,
          pure_collect_sec: 28.0,
          dist_phase_sec: 2.5,
        }) + '\n',
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(1)
      const r = rows[0]!
      expect(r.iter).toBe(5)
      // 采集侧读数全部到位（含两段采集墙钟）
      expect(r.winRate).toBeCloseTo(0.42)
      expect(r.scoreMean).toBeCloseTo(0.75)
      expect(r.scoreStd).toBeCloseTo(0.11)
      expect(r.samples).toBe(12345)
      expect(r.rolloutSec).toBeCloseTo(31.5)
      expect(r.pureCollectSec).toBeCloseTo(28.0)
      expect(r.distPhaseSec).toBeCloseTo(2.5)
      expect(r.actuals).not.toBeNull()
      // PPO 侧 = NaN（① 不冒充 0）
      for (const v of [
        r.ppoSec,
        r.ppoCloudSec,
        r.kl,
        r.entropy,
        r.policyLoss,
        r.valueLoss,
        r.meanRet,
        r.lr,
      ]) {
        expect(Number.isNaN(v)).toBe(true)
      }
      expect(r.pendingPpo).toBe(true)
    })
  })

  it('同 it 的 iteration 行在后 ⇒ 覆盖 rollout_end 行（不留两行、不留 pending 标记）', () => {
    withTmp((dir) => {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        [
          // 采集收官即刻（PPO 还没跑）
          JSON.stringify({
            event: 'rollout_end',
            iter: 7,
            time: 't0',
            winRate: 0.42,
            samples: 100,
            rollout_sec: 30,
          }),
          // 本轮结算完（含 PPO + 传输账）
          JSON.stringify({
            event: 'iteration',
            iter: 7,
            time: 't1',
            winRate: 0.42,
            score_mean: 1,
            samples: 100,
            rollout_sec: 30,
            ppo_sec: 120,
            ppo_cloud_sec: 90,
            kl: 0.012,
            entropy: 0.8,
            policy: -0.01,
            value: 0.02,
            mean_ret: -0.4,
            lr: 0.0001,
          }),
        ].join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(1)
      expect(rows[0]!.pendingPpo).toBe(false)
      expect(rows[0]!.ppoSec).toBeCloseTo(120)
      expect(rows[0]!.kl).toBeCloseTo(0.012)
      expect(rows[0]!.lr).toBeCloseTo(0.0001)
    })
  })

  it('iteration 行在前、rollout_end 在后（重放/乱序）⇒ 以 iteration 为准', () => {
    withTmp((dir) => {
      writeFileSync(
        path.join(dir, 'training_log.jsonl'),
        [
          JSON.stringify({
            event: 'iteration',
            iter: 2,
            time: 't1',
            winRate: 0.3,
            kl: 0.02,
            lr: 0.0002,
          }),
          JSON.stringify({ event: 'rollout_end', iter: 2, time: 't0', winRate: 0.3 }),
        ].join('\n'),
        'utf-8',
      )
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(1)
      expect(rows[0]!.pendingPpo).toBe(false)
      expect(rows[0]!.kl).toBeCloseTo(0.02)
    })
  })
})
