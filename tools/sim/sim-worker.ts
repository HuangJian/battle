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
import {
  runSimulation,
  type RunTelemetry,
  type RunForensics,
  type ThreatLedgerRun,
  type SimResult,
} from './simulation-runner'
import type { GodAIParams } from '../../src/ai/GodAIInput'
import type { StageData } from '../../src/types'

export interface SimTask {
  /** Index into the batch's task array — results are re-ordered by this. */
  id: number
  seed: number
  stage: StageData
  difficulty: string
  params: GodAIParams
  maxTicks: number
  /**
   * 0-based stage index (scoring scale). Omitted ⇒ 0, which is what every
   * existing pool caller already used, so their runs stay byte-identical.
   * Supplying the real index matters for A/B parity with `level-sim`: kill
   * score scales with the index and `rules.dropOnScoreMilestone` turns
   * accumulated score into power-up drops, so index 0 and index 33 are
   * genuinely different runs on the same seed.
   */
  stageIndex?: number
  /**
   * Collect v6 evaluation telemetry and return the extra scoring fields
   * (plan/God-AI-Evaluation-Redesign.md). Default off: the v5 fitness path
   * ships exactly the same four fields it always did, so its payload size
   * and aggregation order are unchanged.
   */
  telemetry?: boolean
  /** Return `suicideReturnCommits` (§116/§117 A/B trigger-rate probe). */
  commitCounts?: boolean
  /** Return per-run forensics (DECISIONS §119): terminal snapshot, action
   *  trace, death/kill/pickup history. Read-only — outcome unaffected. */
  forensics?: boolean
  /** Return the event-driven threat ledger (God-AI breakthrough plan §4.1).
   *  Read-only — outcome unaffected. Default off. */
  threatLedger?: boolean
  /** Dual-God-AI mode: both players controlled by God AI (coop). Default off. */
  coop?: boolean
  /** 督战双玩家: supervise mode with a second God AI driving player2 (distinct
   *  from `coop`, the Lie-Back-Win / human-P1 mode). Default off. */
  spectateDual?: boolean
  /** Record the run and return the full SimResult (incl. replay) in the task
   *  result, so the caller can persist failure replays. Default off. */
  recordReplay?: boolean
  /** M4 star census observer (spawn/pickup/min-dist per star). Read-only. */
  powerupCensus?: boolean
  /** Player policy: 'god' (default), 'nn', 'intent' (stub), 'intent-exec' (M6) or 'intent-oracle' (M7① 探针). */
  policy?: 'god' | 'nn' | 'intent' | 'intent-exec' | 'intent-oracle'
  /** M7① cadence 扫描：意图 replan 周期覆盖（0/缺省 = 策略默认）。 */
  replanEvery?: number
  /** Weights directory for the 'nn' policy (auto-discovers latest). */
  nnWeightsDir?: string
  /** Weights JSON file for the 'intent' policy (M4 stub / M5 trained). */
  intentWeightsDir?: string
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
  /** Suicide-trade commit ticks (only when the task requested commitCounts). */
  suicideReturnCommits?: number
  /** §121 self-fire base-guard block ticks (only when commitCounts requested). */
  selfFireGuardBlocks?: number
  /** Per-run forensics (only when the task requested forensics). */
  forensics?: RunForensics
  /** Event-driven threat ledger (only when the task requested threatLedger). */
  ledger?: ThreatLedgerRun
  /** M4 star census (only when the task requested powerupCensus). */
  powerupCensus?: SimResult['powerupCensus']
  /** Failure cause ('base_destroyed' | 'lives_exhausted' | 'timeout'). */
  failureCause?: string
  /** Kind of the tank whose bullet destroyed the base (self-inflicted = 'player'). */
  failureKillerKind?: string
  /** paramsHash of the params the run actually used (live probe identity tag). */
  paramsHash?: string
  /** Full SimResult (incl. replay frames) — only when the task set `recordReplay`.
   *  Lets the caller persist a .replay for losing runs without re-running them. */
  replayResult?: SimResult
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
      stageIndex: task.stageIndex,
      maxTicks: task.maxTicks,
      sampleInterval: 60, // same as the serial path (metrics are discarded)
      telemetry: task.telemetry === true,
      commitCounts: task.commitCounts === true,
      forensics: task.forensics === true,
      threatLedger: task.threatLedger === true,
      powerupCensus: task.powerupCensus === true,
      coop: task.coop === true,
      spectateDual: task.spectateDual === true,
      record: task.recordReplay === true,
      policy: task.policy,
      nnWeightsDir: task.nnWeightsDir,
      intentWeightsDir: task.intentWeightsDir,
      replanEvery: task.replanEvery,
    })
    msg = {
      id: task.id,
      ok: true,
      outcome: result.outcome,
      ticks: result.ticks,
      killCount: result.finalState.killCount,
      baseAlive: result.finalState.baseAlive,
      paramsHash: result.paramsHash,
    }
    // When the caller asked to persist replays, hand back the full result
    // (it already carries the recorded frames). Outcome/aggregation fields are
    // unchanged, so non-recording callers are unaffected.
    if (task.recordReplay === true) msg.replayResult = result
    if (task.telemetry === true) {
      msg.lives = result.finalState.lives
      msg.firstKillTick = result.firstKillTick
      msg.telemetry = result.telemetry
    }
    if (task.commitCounts === true) {
      msg.suicideReturnCommits = result.suicideReturnCommits
      msg.selfFireGuardBlocks = result.selfFireGuardBlocks
    }
    if (task.forensics === true) {
      msg.forensics = result.forensics
      // Failure attribution rides along with the forensics opt-in (keeps the
      // result-shape contract flag-gated like telemetry/commitCounts).
      msg.failureCause = result.failure?.cause
      msg.failureKillerKind = result.failure?.killerKind
    }
    if (task.threatLedger === true) msg.ledger = result.ledger
    if (task.powerupCensus === true) msg.powerupCensus = result.powerupCensus
  } catch {
    msg = { id: task.id, ok: false, outcome: 'error', ticks: 0, killCount: 0, baseAlive: false }
  }
  self.postMessage(msg)
}
