/** course-metrics-mirror.ts — 备份库的**后台节拍**（启动首拍 + 每 60s）。
 *
 *  形状照 `snapshot-refresher.ts` 的三条独立节拍：unref、绝不抛、**绝不在请求路径上跑**
 *  （plan/course-metrics-persistence §3.4）。
 *
 *  每拍 = ① 增量转存（预算 `BCITY_COURSE_METRICS_BUDGET`，缺省 64MiB，0 = 不限）
 *        ② 冷存检查（至多打包 1 门，IO 有界）。
 *
 *  开关：`BCITY_COURSE_METRICS_SYNC=0` 停写侧（ticker/CLI 转存/compact）；**读侧回退不关**
 *  （读已存在的备份无害）。注意与库根 env `BCITY_COURSE_METRICS` 区分：后者是路径。
 */

import { LockBusyError, archiveMemoStats, compactAll, statusRows, syncAll } from './course-metrics'

export const COURSE_METRICS_INTERVAL_MS = 60_000
export const COURSE_METRICS_BUDGET = 64 * 1024 * 1024

export function courseMetricsWriteEnabled(): boolean {
  return process.env.BCITY_COURSE_METRICS_SYNC !== '0'
}

export function startCourseMetricsMirror(intervalMs = COURSE_METRICS_INTERVAL_MS): void {
  if (!courseMetricsWriteEnabled()) return
  let running = false
  const run = async (): Promise<void> => {
    if (running) return
    running = true
    const started = Date.now()
    try {
      const budget = Number(process.env.BCITY_COURSE_METRICS_BUDGET ?? '')
      const s = syncAll({
        budgetBytes: Number.isFinite(budget) && budget >= 0 ? budget : COURSE_METRICS_BUDGET,
      })
      const c = await compactAll({ max: 1 })
      const memo = archiveMemoStats()
      const parts = [
        `[mirror] 课 ${s.courses} ｜ 新增 ${(s.bytes / 1024).toFixed(1)}KB（${s.lines} 行）`,
        `未变 ${s.skipped} ｜ 复位 ${s.resets.length} ｜ 失败 ${s.failed.length}`,
      ]
      if (c.packed.length > 0) {
        parts.push(
          `冷存 ${c.packed.map((p) => `${p.course}→${(p.bytes / 1048576).toFixed(1)}MB`).join(',')}`,
        )
      }
      if (s.budgetHit || c.budgetHit) parts.push('预算用尽')
      parts.push(
        `memo ${(memo.bytes / 1048576).toFixed(0)}/${(memo.capBytes / 1048576).toFixed(0)}MB`,
      )
      parts.push(`${Date.now() - started}ms`)
      console.log(parts.join(' ｜ '))
      for (const x of s.resets) console.warn(`[mirror] 复位 ${x.course}/${x.rel}: ${x.reason}`)
      for (const x of s.failed) console.warn(`[mirror] 失败 ${x.course}/${x.rel}: ${x.error}`)
      for (const x of c.failed) console.warn(`[mirror] 冷存失败 ${x.course}: ${x.error}`)
    } catch (e) {
      if (!(e instanceof LockBusyError)) {
        console.warn(`[mirror] 本拍跳过：${e instanceof Error ? e.message : String(e)}`)
      }
    } finally {
      running = false
    }
  }
  void run()
  const t = setInterval(() => void run(), intervalMs)
  t.unref?.()
}

/** 服务端只读状态（`/api/courseMetrics`）：逐课热/冷 + memo 占用（CLI 跨进程读不到 memo）。 */
export function courseMetricsStatusPayload(): Record<string, unknown> {
  return {
    ok: true,
    enabled: courseMetricsWriteEnabled(),
    rows: statusRows(),
    memo: archiveMemoStats(),
    time: new Date().toISOString(),
  }
}
