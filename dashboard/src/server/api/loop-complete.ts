/** loop-complete.ts — 运行正常完成（停车态）派生：只认账本尾行，resume 后自动消失。 */
import type { CourseEdit, LoopComplete } from '../../web/view'

/** 账本尾行是 run_complete → 正常完成停车态；否则 null（纯函数，可单测）。
 *
 * 严格只认**尾行**：resume 后新 run_start/iteration 事件追加在后 → 自动 null
 *（横幅消失）；尾行非 JSON（写半行竞态）→ null（下周期再看，不误报）。 */
export type { CourseEdit }

export function loopCompleteFromLedgerTail(lines: string[]): LoopComplete | null {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.trim()
    if (!line) continue
    let r: { event?: unknown; time?: unknown; reason?: unknown; iter?: unknown; iters?: unknown }
    try {
      r = JSON.parse(line) as typeof r
    } catch {
      return null
    }
    if (!r || typeof r !== 'object' || r.event !== 'run_complete') return null
    const it = typeof r.iter === 'number' ? r.iter : 0
    return {
      at: typeof r.time === 'string' ? r.time : '',
      reason: typeof r.reason === 'string' ? r.reason : '正常完成',
      iters: typeof r.iters === 'number' ? r.iters : it,
    }
  }
  return null
}

/** 多课收官聚合（纯函数，可单测；plan/dashboard-banner-global §4.1）：逐课调用
 *  `loopCompleteFromLedgerTail`（不变 ⇒ 单课口径零漂移）。
 *
 *  - `loopAlive` = **共享** trainer 进程在跑（`trainingLoop` 是共享组件，`scopeOf` 恒 `''`，
 *    一个进程服务所有并行课程）——进程不在跑就没有「停车等待重启」这回事；在跑时收官课的
 *    条目留着（与旧单课行为一致）。
 *  - 课程名**显式排序**（禁止把对象键序当契约）；课程清单由调用方限定（只读已开课 ∪ 查看课，
 *    同 `harvestTrainingCourseActuals` 的成本闸）；单课读失败只少一门（下一拍重试）。 */
export function collectLoopCompletes(
  courses: readonly string[],
  loopAlive: boolean,
  readTail: (course: string) => string[],
): Record<string, LoopComplete> {
  if (!loopAlive) return {}
  const names = [...new Set(courses)].filter(Boolean).sort()
  if (names.length === 0) return {}
  const out: Record<string, LoopComplete> = {}
  for (const course of names) {
    try {
      const done = loopCompleteFromLedgerTail(readTail(course))
      if (done) out[course] = done
    } catch {
      /* 单课读失败只少一门 */
    }
  }
  return out
}

/** 取指定课程的快照：5s 内新鲜命中缓存；否则按课程单飞重算（并发共享同一次计算）。 */
