/**
 * server-iters-metrics.test.ts — §361② 完整指标表不截断：600 轮返回最近 500
 *
 * 分层：src/server/iters.ts
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import os from 'os'
import path from 'path'
import { describe, expect, it } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'fs'
import { readIterMetrics } from '../src/server/iters'

describe('console/iters §361②：完整指标表不截断（MAX 500 上限）', () => {
  it('600 轮日志 → 返回最近 500 轮', () => {
    const dir = mkdtempSync(path.join(os.tmpdir(), 'bcity-iters-361-'))
    try {
      const lines: string[] = []
      for (let i = 0; i < 600; i++) {
        lines.push(
          JSON.stringify({ event: 'iteration', iter: i, time: '', winRate: 0.5, score_mean: 0 }),
        )
      }
      writeFileSync(path.join(dir, 'training_log.jsonl'), lines.join('\n'), 'utf-8')
      const { rows } = readIterMetrics(dir)
      expect(rows.length).toBe(500)
      expect(rows[0]!.iter).toBe(599)
      expect(rows[499]!.iter).toBe(100)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
