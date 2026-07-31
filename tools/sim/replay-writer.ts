/**
 * replay-writer.ts — write .replay files from SimResult data
 * (plan/God-AI-Replay-Visualization §4.2)
 *
 * Thin shell: SimResult.replay → envelope → filename → Bun.write.
 * src/ never touches fs; this is a tools-layer-only module.
 */

import { serializeReplayFile, buildReplayFilename } from '../../src/replay/file'
import type { SimResult } from './simulation-runner'
import type { ReplayType } from '../../src/replay/types'

/** Map SimResult outcome/failure to a ReplayType status. */
function statusFromResult(r: SimResult): ReplayType {
  if (r.outcome === 'stage_clear') return 'clear'
  if (r.failure?.cause === 'base_destroyed') return 'base'
  if (r.failure?.cause === 'lives_exhausted') return 'died'
  return 'timeout'
}

export interface WriteReplayOptions {
  /** SimResult from runSimulation({ record: true }). */
  result: SimResult
  /** Directory to write to (default: 'replays/'). */
  dir?: string
  /** Stage index for filename (0-based). */
  stageIndex: number
  /** Stage name for filename. */
  stageName: string
  /** Effective GodAI params used in the run. */
  godAIParams?: Record<string, unknown>
}

/**
 * Write a .replay file from a recorded simulation result.
 * Returns the written file path, or null if no replay data was recorded.
 */
export async function writeReplayFile(opts: WriteReplayOptions): Promise<string | null> {
  const { result, dir = 'replays', stageIndex, stageName, godAIParams } = opts
  if (!result.replay) return null

  const status = statusFromResult(result)
  const filename = buildReplayFilename({
    difficulty: result.difficulty,
    stageIndex,
    status,
    lives: result.finalState.lives,
    totalTicks: result.ticks,
    seed: result.seed,
  })

  const text = serializeReplayFile({
    source: 'sim',
    seed: result.seed,
    sim: {
      seed: result.seed,
      difficulty: result.difficulty,
      stageIndex,
      stageName,
      outcome: result.outcome,
      status,
      maxTicks: 36000,
      godAIParams,
    },
    finalState: {
      score: result.finalState.score,
      lives: result.finalState.lives,
      killCount: result.finalState.killCount,
      ticks: result.ticks,
    },
    initialSnapshot: result.replay.initialSnapshot,
    frames: result.replay.frames,
    totalTicks: result.replay.tickCount,
    metadata: {
      stage: stageIndex,
      stageName,
      difficulty: result.difficulty,
      lives: result.finalState.lives,
      playerLevel: result.finalState.playerLevel,
      score: result.finalState.score,
      killCount: result.finalState.killCount,
      enemiesTotal: 20,
      playTimeMs: result.finalState.playTimeMs,
    },
  })

  const path = `${dir}/${filename}`
  await Bun.write(path, text)
  return path
}
