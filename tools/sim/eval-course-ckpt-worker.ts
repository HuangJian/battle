#!/usr/bin/env bun
/**
 * eval-course-ckpt-worker.ts — worker half of eval-course-ckpt.ts.
 *
 * Receives one chunk payload:
 *   {
 *     weightsPath: string        // nn-weights JSON (student/bc/god arch); '' for god
 *     label: string              // row tag (usually the weights file name, or 'god')
 *     policy: string             // 'nn' | 'god' — passed through to runEvalOne
 *     difficulty, maxTicks, lives, level,
 *     stages: Array<{ name: string; json: string }>,   // single-stage JSON payloads
 *     jobs: Array<{ id: number; stageLocal: number; seed: number }>
 *   }
 * and posts back { results: Row[] } once. Each job is a pure greedy
 * evaluation (fresh World, seeded RNG — AGENTS §2.2/§2.3) through
 * export-eval-game.runEvalOne, so parallel == serial. Stage JSON is decoded
 * per (stageLocal, seed) via decodeStageGrid (spawn_variants seed-hash).
 */
import { decodeStageGrid } from '../../src/nn/config-stage'
import { readFileSync } from 'fs'
import { runEvalOne } from './export-eval-game'

export interface EvalJob {
  id: number
  stageLocal: number
  seed: number
}

export interface EvalCourseWorkerPayload {
  weightsPath: string
  label: string
  policy: string
  difficulty: string
  maxTicks: number
  lives: number
  level: number
  /** nn-goal 的外部目标源（god|heuristic）；其余 policy 忽略。 */
  goalSource?: string
  /** nn-goal 的 1b-posthoc 软偏置强度（正数）；缺省 = 1a 硬掩码。 */
  goalBias?: string
  stages: Array<{ name: string; json: string }>
  jobs: EvalJob[]
}

export interface EvalCourseRow {
  label: string
  id: number
  stageId: number
  stageName: string
  seed: number
  outcome: string
  win: boolean
  cleared: boolean
  ticks: number
  kills: number
  enemyHits: number
  playerHits: number
  playerDamageTaken: number
  playerShots: number
  powerUpsCollected: number
  score: number
}

self.onmessage = (ev: MessageEvent<EvalCourseWorkerPayload>): void => {
  const p = ev.data
  try {
    // nn-goal 的目标源/软偏置显式透传（不依赖 Worker env 继承语义）。
    if (p.goalSource) process.env.GOAL_SOURCE = p.goalSource
    if (p.goalBias) process.env.GOAL_BIAS = p.goalBias
    const weightsText =
      p.policy === 'nn' || p.policy === 'nn-goal' ? readFileSync(p.weightsPath, 'utf8') : ''
    const rows: EvalCourseRow[] = []
    for (const job of p.jobs) {
      const s = p.stages[job.stageLocal]
      const stageId = 2000 + job.stageLocal
      const stage = decodeStageGrid(s.json, stageId, job.seed)
      const res = runEvalOne(
        0,
        stage,
        job.seed,
        p.difficulty,
        p.maxTicks,
        weightsText,
        p.policy,
        '',
        '',
        0,
        0,
        p.lives,
        p.level,
      )
      rows.push({
        label: p.label,
        id: job.id,
        stageId,
        stageName: s.name,
        seed: job.seed,
        outcome: res.outcome,
        win: res.win,
        cleared: res.cleared,
        ticks: res.ticks,
        kills: res.kills,
        enemyHits: res.enemyHits,
        playerHits: res.playerHits,
        playerDamageTaken: res.playerDamageTaken,
        playerShots: res.playerShots,
        powerUpsCollected: res.powerUpsCollected,
        score: res.score,
      })
    }
    ;(self as any).postMessage({ results: rows })
  } catch (e) {
    ;(self as any).postMessage({
      results: [],
      error: `${p.label}: ${(e as Error).message}`,
    })
  }
}
