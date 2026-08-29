/**
 * cf-goal-worker.ts — T6 反事实标注的 per-game worker（WorkerPool 载体）。
 *
 * 每个消息 = 一局（stage,seed）；worker 内跑 runCounterfactualGame 并**就地写 shard**
 * （避免跨线程搬运 obs 大数组），返回轻量统计。纯函数（fresh World / 独立 RNG，
 * AGENTS 无共享状态约束）—— 并行结果与串行逐字节一致（由分片确定性保证）。
 */
import { runCounterfactualGame, writeCfShard } from './export-counterfactual-goals'
import { STAGES } from '../../src/config/stages'
import { mkdirSync } from 'node:fs'

interface CfTask {
  id: number
  stageIdx: number
  seed: number
  difficulty: string
  windows: number[]
  K: number
  replan: number
  maxTicks: number
  outDir: string
}

interface CfResult {
  id: number
  decisions: number
  truncated: number
  totalCands: number
  outcome: string
  ticks: number
}

self.onmessage = (event: MessageEvent<CfTask>) => {
  const task = event.data
  try {
    const stage = STAGES[task.stageIdx]
    if (!stage) throw new Error(`stage ${task.stageIdx} not found`)
    const res = runCounterfactualGame(task.stageIdx, stage, task.seed, task.difficulty, {
      replan: task.replan,
      windows: task.windows,
      K: task.K,
      maxTicks: task.maxTicks,
    })
    const dir = `${task.outDir}/cf_s${task.stageIdx}_seed${task.seed}`
    mkdirSync(dir, { recursive: true })
    writeCfShard(dir, res, task.K, {
      difficulty: task.difficulty,
      stage: task.stageIdx,
      seed: task.seed,
      windows: task.windows,
      K: task.K,
      replan: task.replan,
    })
    const msg: CfResult = {
      id: task.id,
      decisions: res.decisions.length,
      truncated: res.truncated,
      totalCands: res.totalCandidates,
      outcome: res.outcome,
      ticks: res.ticks,
    }
    ;(self as unknown as Worker).postMessage(msg)
  } catch (e) {
    ;(self as unknown as Worker).postMessage({
      id: task.id,
      decisions: 0,
      truncated: 0,
      totalCands: 0,
      outcome: `error: ${e instanceof Error ? e.message : String(e)}`,
      ticks: 0,
    } satisfies CfResult)
  }
}
