// Bun web worker for the God-AI gate pool.
// Receives a chunk of {difficulty, idx} jobs, runs each via `runStage`, and
// posts back the per-job win counts. Isolated isolate → safe module-local
// state (e.g. World.genId's counter) per worker.

import { runStage } from './gate-core'

export interface GateJob {
  difficulty: string
  idx: number
}
export interface GateResult {
  difficulty: string
  idx: number
  wins: number
}

self.onmessage = (ev: MessageEvent) => {
  const { jobs } = ev.data as { jobs: GateJob[] }
  const results: GateResult[] = []
  for (const job of jobs) {
    results.push({
      difficulty: job.difficulty,
      idx: job.idx,
      wins: runStage(job.difficulty, job.idx),
    })
  }
  ;(self as unknown as { postMessage: (m: unknown) => void }).postMessage({ results })
}
