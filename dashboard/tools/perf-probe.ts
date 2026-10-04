/**
 * perf-probe.ts — 控制台重载性能探针（plan/dashboard-reload-perf W0/W5 的验收工具）。
 *
 * 为什么存在：本 plan 的 KPI（冷算耗时 / RSS 增量 / 稳态零读 / p99）**不能进 CI 门禁**
 * （机器差异），但必须有可复现的量法 —— 这个脚本就是那份量法的唯一实现，人跑、输出 JSON、
 * 基线数字落 plan §1 与 PR 描述（评审 O4：不进 CI）。
 *
 * 量法（与 plan §1.3 同一口径）：
 *   ① `aggregateNodeHistory()` 冷算：耗时 + RSS 增量（先 `invalidateNodeHistoryMemo`）；
 *   ② 命中 memo：再调一次（稳态路径，期望零读 + 毫秒级）；
 *   ③ `listCourseLedgers()` / `readPpoAttribution()` 冷/热；
 *   ④ `buildContributionView()` 纯投影；
 *   ⑤ 计数器账（calls/computes/scans/bytesRead/fullRescans）—— 结构性证据。
 *
 * 用法（在**真实池根**上跑，别在空 tmp 上跑）：
 *   cd dashboard && bun tools/perf-probe.ts            # 人类可读 + JSON
 *   cd dashboard && bun tools/perf-probe.ts > out.json # 只留 JSON（stderr 打印说明）
 */

import {
  aggregateNodeHistory,
  invalidateNodeHistoryMemo,
  poolHistoryCounters,
  resetPoolHistoryCounters,
  resolveWindow,
} from '../src/server/pool-history'
import {
  buildContributionView,
  invalidatePpoAttributionMemo,
  listCourseLedgers,
  readPpoAttribution,
} from '../src/server/contribution'
import { tmpPoolDir } from '../src/core/paths'

function rssMB(): number {
  return Math.round((process.memoryUsage().rss / 1024 / 1024) * 10) / 10
}

function ms(fn: () => void): number {
  const t0 = performance.now()
  fn()
  return Math.round((performance.now() - t0) * 10) / 10
}

/** 量一段同步工作：耗时 + RSS 增量 + 计数器增量。 */
function measure(fn: () => void): {
  ms: number
  rssDeltaMB: number
  counters: ReturnType<typeof poolHistoryCounters>
} {
  resetPoolHistoryCounters()
  const before = rssMB()
  const t = ms(fn)
  const after = rssMB()
  return {
    ms: t,
    rssDeltaMB: Math.round((after - before) * 10) / 10,
    counters: poolHistoryCounters(),
  }
}

const out: Record<string, unknown> = { root: tmpPoolDir(), at: new Date().toISOString() }

// ① 冷算（清 memo + 流态 ⇒ 一次全量）
invalidateNodeHistoryMemo()
invalidatePpoAttributionMemo()
out.coldAggregate = measure(() => void aggregateNodeHistory())

// ② 命中 memo（稳态）
out.warmAggregate = measure(() => void aggregateNodeHistory())

// ③ 账本清单 + PPO 归属（冷/热）
invalidatePpoAttributionMemo()
out.ppoCold = measure(() => void readPpoAttribution())
out.ppoWarm = measure(() => void readPpoAttribution())
out.listLedgers = measure(() => void listCourseLedgers())

// ④ 纯投影（贡献度视图）
const agg = aggregateNodeHistory()
const w = resolveWindow('today', Date.now(), agg.epochMs)
out.buildContributionView = measure(() => void buildContributionView(agg, w))

out.rssTotalMB = rssMB()

process.stdout.write(`${JSON.stringify(out, null, 2)}\n`)
process.stderr.write(
  [
    'perf-probe：上面 JSON 即本 plan 的验收数字。',
    '  · coldAggregate.counters.bytesRead / fullRescans = 冷启动读盘量与重建流数；',
    '  · warmAggregate 期望 ms ≈ 0、bytesRead = 0（G2 零读）；',
    '  · 跑在空 tmp 上数字无意义 —— 请在真实池根（有 39 份 meta 的那台）上跑。',
    '',
  ].join('\n'),
)
