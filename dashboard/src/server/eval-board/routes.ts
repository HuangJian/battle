/** routes.ts — evalBoard 端点的**门控决策**（R4：缺省关；plan/dashboard-memory-evalboard-off）。
 *
 * 为什么单列（W1′ 测试缝）：
 *  - `server.ts` 顶层 `Bun.serve`、路由是内联箭头且**零导出**，生产测试从不在进程里 import 它
 *    ⇒ 「开关关时不做任何账本工作」这层决策放这里，测试直调（照 `poolHistoryCounters` 范式）。
 *  - 生产唯一调用面 = `server.ts` 的 `/api/evalboard`、`/eval`、30s `ladderTimer` 三处接线。
 *
 * 口径（G2）：开关关 ⇒ **不调** `buildEvalBoardView`（`composeEvalBoardView` 调用次数 = 0，
 * 由 `view.ts::evalboardComposeCalls()` 钉），`/eval` 说明页**不注入** `/eval.js`。
 */
import { evalboardEnabled } from '../../core/feature-flags'
import type { EvalBoardView } from '../../web/view'
import { buildEvalBoardView } from './view'

/** 端点停用时的载荷（200，不是 404：旧书签 / curl 要给一个体面的答案）。 */
export interface EvalboardDisabledPayload {
  disabled: true
  message: string
  flag: string
}

export type EvalboardApiPayload = EvalBoardView | EvalboardDisabledPayload

/** `/eval` 页数据决策：说明页分支与取数分支走同一缝（测试可直调，见 W2′ 用例）。 */
export type EvalPageDecision =
  | { kind: 'notice'; message: string }
  | { kind: 'views'; views: EvalBoardView[] }

/** 会话内计数（测试/验收用；生产只增不读）。 */
export const evalboardRouteCounters = {
  apiHandled: 0,
  apiDisabled: 0,
  pageNotice: 0,
  pageViews: 0,
  ladderStarted: 0,
}

/** 归零（测试夹具；生产路径不调）。 */
export function resetEvalboardRouteCounters(): void {
  evalboardRouteCounters.apiHandled = 0
  evalboardRouteCounters.apiDisabled = 0
  evalboardRouteCounters.pageNotice = 0
  evalboardRouteCounters.pageViews = 0
  evalboardRouteCounters.ladderStarted = 0
}

/** 停用文案（API `{ disabled }` 的 message 与 `/eval` 说明页**同一来源**）。 */
export function evalboardPageNotice(): string {
  return '评估板已停用（BCITY_EVALBOARD=1 可开）'
}

/** `GET /api/evalboard` 的决策：开关关 ⇒ disabled 形状，且**不碰**账本/视图。 */
export function evalboardApiPayload(
  course: string | undefined,
  fresh: boolean,
): EvalboardApiPayload {
  evalboardRouteCounters.apiHandled += 1
  if (!evalboardEnabled()) {
    evalboardRouteCounters.apiDisabled += 1
    return { disabled: true, message: evalboardPageNotice(), flag: 'BCITY_EVALBOARD' }
  }
  return buildEvalBoardView(course, fresh)
}

/** `GET /eval` 的决策：开关关 ⇒ 说明页（不取数、不 compose）。 */
export function evalboardPageDecision(courses: string[]): EvalPageDecision {
  if (!evalboardEnabled()) {
    evalboardRouteCounters.pageNotice += 1
    return { kind: 'notice', message: evalboardPageNotice() }
  }
  evalboardRouteCounters.pageViews += 1
  return { kind: 'views', views: courses.map((c) => buildEvalBoardView(c, false)) }
}

/** 30s 自动爬梯 ticker 的门控：开关关 ⇒ **不起** ticker（否则有 ladder_start 就每 30s 拉全账）。 */
export function shouldStartLadderTicker(): boolean {
  return evalboardEnabled()
}
