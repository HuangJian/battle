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
    try {
      const budget = Number(process.env.BCITY_COURSE_METRICS_BUDGET ?? '')
      const s = syncAll({
        budgetBytes: Number.isFinite(budget) && budget >= 0 ? budget : COURSE_METRICS_BUDGET,
      })
      const c = await compactAll({ max: 1 })
      // ★ 2026-10-20（用户口径「这个 log 一直在刷屏，请删除」）：**删掉每拍一行的汇总**。
      //   60s 一拍、每拍一行 ⇒ 控制台被它刷满；而这行里的数（课数/新增/未变/耗时/memo）都是
      //   **常态读数**，出问题时也定位不到具体文件——它是「看着很热闹、实际不指向任何动作」的行。
      //   真正要看的信号各有各的行：复位 / 失败 / 冷存失败 = 逐条 warn（下面几行），
      //   「预算用尽」= 唯一只活在这行里的可行动计数 ⇒ 单独升成 warn。
      //   ⇒ 常态静默，异常必响。
      if (s.budgetHit || c.budgetHit) {
        console.warn(
          `[mirror] 预算用尽：本拍转存/冷存量超出预算（${(s.bytes / 1048576).toFixed(1)}MB）` +
            '——备份库正在落后，下拍继续',
        )
      }
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
