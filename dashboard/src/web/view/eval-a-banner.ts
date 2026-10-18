/** eval-a-banner.ts — 首页 evalA 提示横幅的状态与「什么时候该自己消失」。
 *
 *  用户口径（2026-10-18）：「dashboard 首页点击 evalA 按键后，在趋势图上方显示『evalA 已启动 …』
 *  提示，应改为横幅提示，读数回填后自动隐藏横幅」。
 *
 *  两件事分家：
 *    · **长相**在 `Hero` 里（`EvalBanner` 组件，`tc-banner` 横条——原来是一行 `tc-notice` 灰字，
 *      混在走势图上方像页脚，动作结果被读成「页面说明」）；
 *    · **该不该消失**在这里（纯函数）。为什么必须抽出来：Hero 是客户端状态机（preact hooks），
 *      SSR 首帧**恒无横幅**——把判据埋在 effect 里就只能靠人眼验「读数回填后它自己没了」，
 *      而那正是本次要钉的行为。
 */

import type { IterRow } from './metric-types'

/** 一条 evalA 横幅（Hero 的本地状态；不落 localStorage——它是**这次会话**的动作结果）。 */
export interface EvalABanner {
  /** 点的是哪一轮就等哪一轮。
   *
   *  ★ 不能用服务端文案里的轮号：单槽排队时服务端会如实说「前面 itM 正在跑」，可**用户等的是
   *  自己点的那一轮**——拿 itM 的读数当完成信号会让横幅在最不该消失的时刻消失（itM 早就有了）。 */
  iter: number
  /** 上屏文案（服务端 `message` 原样：启动中 / 已排队 / 已在运行 / 启动失败都是它）。 */
  message: string
  /** false = 启动/入队失败（红横幅，等用户看完手动关：没有读数会来，它不会自己消失）。 */
  ok: boolean
  /** 所属课程：横幅回答「**这门课**刚做了什么」，切课即作废（否则切到 B 课还挂着 A 课的结果）。 */
  course: string
}

/** 横幅该自己消失了吗：**读数已回填**（该轮出现 `evalData`）或横幅属于别的课程。
 *
 *  只认「点的那一轮」：别的轮有 evalData 不算（那是历史读数，不是这次动作的结果）；
 *  `ok=false` 的失败横幅不会因为读数而消失——它本来就没有读数，留给用户手动关（判据只看事实，
 *  不按 ok 分叉：万一失败文案之后读数照样到了，藏掉也是对的）。
 */
export function evalBannerDone(
  banner: EvalABanner,
  iters: readonly IterRow[],
  course: string,
): boolean {
  if (banner.course !== course) return true
  return iters.some((r) => r.iter === banner.iter && r.evalData != null)
}
