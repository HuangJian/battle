/**
 * sim-worker.ts — Worker entry for parallel God-AI evaluation.
 *
 * Receives one SimTask per message, runs a single headless simulation
 * (synchronous, deterministic: seed + stage + difficulty + params fully
 * determine the outcome regardless of which thread runs it), and posts
 * back only the four fields the optimizer's fitness aggregation consumes.
 *
 * Errors inside a run are caught and reported as `ok: false`, mirroring
 * the serial evaluateParams() catch branch exactly.
 */
import { runSimulation, type RunTelemetry } from './simulation-runner'
import type { GodAIParams } from '../src/ai/GodAIInput'
import type { StageData } from '../src/types'

export interface SimTask {
  /** Index into the batch's task array — results are re-ordered by this. */
  id: number
  seed: number
  stage: StageData
  difficulty: string
  params: GodAIParams
  maxTicks: number
  /**
   * Collect v6 evaluation telemetry and return the extra scoring fields
   * (plan/God-AI-Evaluation-Redesign.md). Default off: the v5 fitness path
   * ships exactly the same four fields it always did, so its payload size
   * and aggregation order are unchanged.
   */
  telemetry?: boolean
}

export interface SimTaskResult {
  id: number
  ok: boolean
  outcome: string
  ticks: number
  killCount: number
  baseAlive: boolean
  /** Only populated when the task requested telemetry. */
  lives?: number
  firstKillTick?: number
  telemetry?: RunTelemetry
}

declare var self: Worker

self.onmessage = (event: MessageEvent<SimTask>) => {
  const task = event.data
  let msg: SimTaskResult
  try {
    const result = runSimulation({
      seed: task.seed,
      stage: task.stage,
      difficulty: task.difficulty,
      godAIParams: task.params,
      maxTicks: task.maxTicks,
      sampleInterval: 60, // same as the serial path (metrics are discarded)
      telemetry: task.telemetry === true,
    })
    msg = {
      id: task.id,
      ok: true,
      outcome: result.outcome,
      ticks: result.ticks,
      killCount: result.finalState.killCount,
      baseAlive: result.finalState.baseAlive,
    }
    if (task.telemetry === true) {
      msg.lives = result.finalState.lives
      msg.firstKillTick = result.firstKillTick
      msg.telemetry = result.telemetry
    }
  } catch {
    msg = { id: task.id, ok: false, outcome: 'error', ticks: 0, killCount: 0, baseAlive: false }
  }
  self.postMessage(msg)
}
