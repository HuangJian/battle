#!/usr/bin/env bun
/**
 * export-intent-labels-worker.ts — 机械意图 tagger 的池工作进程壳。
 * 逻辑全部在 intent-label-core.ts（可单测）；本文件只做消息契约适配。
 */
import { processJob, type TaggerPayload } from './intent-label-core'

self.onmessage = (ev: MessageEvent<TaggerPayload>) => {
  // 持久池契约（WorkerPool.runBatch）：每条消息 = 单个带 id 的结果；
  // 主进程收到后才向该 worker 派发下一任务。
  const agg = processJob(ev.data, ev.data.jobs[0])
  ;(self as unknown as { postMessage: (m: unknown) => void }).postMessage(agg)
}
