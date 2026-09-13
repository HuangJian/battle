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

/** 取指定课程的快照：5s 内新鲜命中缓存；否则按课程单飞重算（并发共享同一次计算）。 */
