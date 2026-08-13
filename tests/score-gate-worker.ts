// Bun web worker for the God-AI SCORE gate pool.
// Receives a chunk of {difficulty, idx} jobs, runs each via `scoreStage`, and
// posts back the per-job mean v7 scores. Isolated isolate → safe module-local
// state (e.g. World.genId's counter) per worker.

import { scoreStage } from './score-gate-core'

export interface ScoreJob {
  difficulty: string
  idx: number
}
export interface ScoreResult {
  difficulty: string
  idx: number
  score: number
}

self.onmessage = (ev: MessageEvent) => {
  const { jobs } = ev.data as { jobs: ScoreJob[] }
  const results: ScoreResult[] = []
  for (const job of jobs) {
    results.push({
      difficulty: job.difficulty,
      idx: job.idx,
      score: scoreStage(job.difficulty, job.idx),
    })
  }
  ;(self as unknown as { postMessage: (m: unknown) => void }).postMessage({ results })
}
