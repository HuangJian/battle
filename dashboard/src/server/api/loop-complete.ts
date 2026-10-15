/** loop-complete.ts — 运行正常完成（停车态）派生：只认账本尾行，resume 后自动消失。 */
import type { CourseEdit, LoopComplete, RunCompleteCause } from '../../web/view'
import { RUN_COMPLETE_CAUSES } from '../../web/view'

/** 账本尾行是 run_complete → 正常完成停车态；否则 null（纯函数，可单测）。
 *
 * 严格只认**尾行**：resume 后新 run_start/iteration 事件追加在后 → 自动 null
 *（横幅消失）；尾行非 JSON（写半行竞态）→ null（下周期再看，不误报）。
 *
 * ★ 2026-10-15（plan/run-complete-cause-banner §3.3）：尾行上的 `cause`/`detail`（新账本）
 * 直接透传；**老账本**（2026-10-15 之前写下的行）没有这两个键 ⇒ 退一步看**它的上一行**：
 * 上一行恰好是 `gate_verdict` 且 `verdict === 'STOP'`（当时唯一会带原因的判决 = G5 预算硬断）
 * ⇒ 归到 `max_hours` 并把**内部术语原文改写成说人话**（原文形如
 * `max_hours 12.0h 到顶（轮级硬断）`——屏上不许出现这种词，见 plan §5 DoD）。
 * **只认恰好上一行、只认 STOP**：不扫历史、不猜（两行都没有 ⇒ 不带 cause，读面回落原文）。
 * 放宽尾行是安全的：resume 后尾行不再是 run_complete ⇒ 依旧 null；半行竞态处置不变
 * （尾行解析失败 ⇒ null）；只多读一行。
 */
export type { CourseEdit }

function _causeOf(raw: unknown): RunCompleteCause | '' {
  return typeof raw === 'string' && (RUN_COMPLETE_CAUSES as readonly string[]).includes(raw)
    ? (raw as RunCompleteCause)
    : ''
}

/** 老账本的「上一行判决」→ 原因（认不出 ⇒ null，不编）。 */
function _legacyCauseOf(prev: string): { cause: RunCompleteCause; detail: string } | null {
  let r: { event?: unknown; verdict?: unknown; reason?: unknown }
  try {
    r = JSON.parse(prev) as typeof r
  } catch {
    return null
  }
  if (!r || typeof r !== 'object' || r.event !== 'gate_verdict' || r.verdict !== 'STOP') return null
  const reason = typeof r.reason === 'string' ? r.reason : ''
  const m = /max_hours\s+([\d.]+)\s*h/.exec(reason)
  if (m) {
    return {
      cause: 'max_hours',
      detail: `墙钟 ${Number(m[1])}h 预算到顶（预算从课程首启累计，重启不续命）`,
    }
  }
  return null
}

export function loopCompleteFromLedgerTail(lines: string[]): LoopComplete | null {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i]!.trim()
    if (!line) continue
    let r: {
      event?: unknown
      time?: unknown
      reason?: unknown
      iter?: unknown
      iters?: unknown
      cause?: unknown
      detail?: unknown
    }
    try {
      r = JSON.parse(line) as typeof r
    } catch {
      return null
    }
    if (!r || typeof r !== 'object' || r.event !== 'run_complete') return null
    const it = typeof r.iter === 'number' ? r.iter : 0
    const base: LoopComplete = {
      at: typeof r.time === 'string' ? r.time : '',
      reason: typeof r.reason === 'string' ? r.reason : '正常完成',
      iters: typeof r.iters === 'number' ? r.iters : it,
      it,
    }
    const cause = _causeOf(r.cause)
    if (cause) {
      return { ...base, cause, detail: typeof r.detail === 'string' ? r.detail : '' }
    }
    const prev = i > 0 ? lines[i - 1]!.trim() : ''
    const legacy = prev ? _legacyCauseOf(prev) : null
    return legacy ? { ...base, ...legacy } : base
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
