/**
 * base-loss-worker.ts — Worker entry for the base-loss forensics sweep.
 *
 * One task = one (seed, stage, difficulty) cell. Each run is a pure function
 * of its inputs (AGENTS §2.2/§2.3), so thread placement cannot change the
 * result; the pool re-orders by task id before aggregation.
 */
import { runForensics, type RunResult } from './base-loss-run'
import type { StageData } from '../../src/types'

export interface ForensicTask {
  id: number
  seed: number
  stage: StageData
  stageIndex: number
  difficulty: string
  maxTicks: number
}

export interface ForensicTaskResult {
  id: number
  ok: boolean
  result?: RunResult
  error?: string
}

declare var self: Worker

self.onmessage = (event: MessageEvent<ForensicTask>) => {
  const task = event.data
  let msg: ForensicTaskResult
  try {
    const result = runForensics({
      seed: task.seed,
      stage: task.stage,
      stageIndex: task.stageIndex,
      difficulty: task.difficulty,
      maxTicks: task.maxTicks,
    })
    msg = { id: task.id, ok: true, result }
  } catch (e) {
    msg = { id: task.id, ok: false, error: e instanceof Error ? e.message : String(e) }
  }
  self.postMessage(msg)
}
