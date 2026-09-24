/** train-mode.ts — 训练模式（在线/离线）→ `courses.<课>` 的**唯一写面**（2026-09-24）。
 *
 *  为什么单开一个模块（而不是让 `course-mode.ts` import `course-lifecycle.ts`）：
 *  `course-lifecycle.ts` 已经 import 了 `course-mode.ts`（它要用 `pushHubMode`）——反向 import
 *  会成环。域换算不在这里重写：仍走 `stack/specs.ts::trainModeKnobs`（唯一换算函数）。
 *
 *  谁调它：
 *    · `course-lifecycle.ts::writeCourseConfigForOpen`（**开课**：弹窗里选的模式）；
 *    · `course-mode.ts::setCourseMode`（**那颗「切离线/切换成在线」开关**，运行中热切）。
 *
 *  ★ 评审结论（plan/train-mode-hot-switch.plan.md §2.2 F9）：**只有这两条用户动作路径写配置**。
 *  hub 推送的内部复用路径（`pushCourseMode`：开课的 hub 推送、停课置离线、起 hub 回灌）**一个字
 *  都不写** —— 否则开课会重复写盘 1–3 次（`pushHubMode` 带 3×2s 重试），而「停课」还会被误翻译成
 *  「整段上云」。
 *
 *  ★ node 往返（plan §2.6）：`offline` 会把 `courses.<课>.rollout_src` **覆写**成 `run`。若就此
 *  不管，显式选过 `node`（整轮上云）的课一下离线再切回在线时，`node` 那格已经没了 ⇒ 静默降级成
 *  `rl.rollout_src`/`local`。开课路径没这个问题（弹窗每次都重新选），**热切的一次点击往返才把它
 *  变成可达**。所以切 offline 时把**当前生效的非 run 源**记进 console-state（`courseRolloutSrc`），
 *  切回 online 时优先恢复。
 *  只记 `node`/`auto`：`local` 是缺省值（`resolveRolloutSrc` 对缺键就返回它），写进配置是无意义的噪声。
 */

import { loadConfig, saveConfig } from '../../core/config'
import type { RolloutSrcMode, TrainMode } from '../../core/types'
import { resolveRolloutSrc, trainModeKnobs } from '../../stack/specs'
import { loadConsoleState, saveConsoleState } from './console-state'

/** `applyTrainModeToConfig` 的人读说明（回执用；与 `writeCourseConfigForOpen` 的 notes 同形）。 */
export interface TrainModeNotes {
  notes: string[]
}

/** 默认值不需要记（`resolveRolloutSrc` 对缺键就返回 `local`）——只记会**丢失信息**的两档。 */
function isWorthRemembering(src: string): src is RolloutSrcMode {
  return src === 'node' || src === 'auto'
}

/** 记下「被 offline 覆写掉的那个非 run 源」（`local`/空 ⇒ 删掉这条记录）。 */
function rememberRolloutSrc(course: string, src: string): void {
  // 展开 `undefined` 本就是 no-op（unicorn/no-useless-fallback-in-spread），故不写 `?? {}`。
  const table = { ...loadConsoleState().courseRolloutSrc }
  if (isWorthRemembering(src)) table[course] = src
  else delete table[course]
  saveConsoleState({ courseRolloutSrc: table })
}

/** 取回并**消费**上次记下的源（一次性：恢复后配置自己就带着它了）。 */
function takeRolloutSrc(course: string): RolloutSrcMode | null {
  const table = { ...loadConsoleState().courseRolloutSrc }
  const got = table[course]
  if (got === undefined) return null
  delete table[course]
  saveConsoleState({ courseRolloutSrc: table })
  return isWorthRemembering(got) ? got : null
}

/** 把训练模式落到 `courses.<课>`（**唯一**配置写入点）。
 *
 *  `opts.rolloutSrc`：开课弹窗显式选的 rollout 位置 —— 在线档**原值直写**（与历史行为逐字一致：
 *  旧代码就是这么写的，**不过** `trainModeKnobs`；过一遍会把 `run` 换成本机 `local` = 行为漂移）。
 *  `opts.remember`：热切路径开（记/取 node 往返，见文件头）；开课路径不开（弹窗每次重选，没有往返）。
 *
 *  会抛：`saveConfig` 的容量/槽位守卫（`core/config.ts`）——调用方负责转成人读回执，
 *  并**不要在抛之后继续推 hub**（否则留下「hub 离线但本机仍本机采样」的半状态）。
 */
export function applyTrainModeToConfig(
  course: string,
  mode: TrainMode,
  opts: { rolloutSrc?: RolloutSrcMode; remember?: boolean } = {},
): TrainModeNotes {
  const notes: string[] = []
  const cfg = loadConfig()
  const courses = { ...cfg.courses }
  const row = { ...courses[course] }
  if (mode === 'offline') {
    // 记「即将被覆写的那个源」——必须在写 `run` **之前**读（顺序即契约）。
    if (opts.remember) rememberRolloutSrc(course, resolveRolloutSrc(cfg, course))
    const knobs = trainModeKnobs('offline', opts.rolloutSrc ?? 'local')
    // 两个键缺一不可：`run` 是声明，`run_iters` 是段长（`-1` = 到课程末）。
    row.rollout_src = knobs.rolloutSrc
    row.run_iters = knobs.runIters ?? -1
    notes.push('训练模式 离线：本课 rollout_src=run + run_iters=-1（整段上云）')
  } else {
    // 切回在线 = **撤掉离线标记**：段长必删（留着它 = 下一轮又被当成段长 + 本机采样 = 半状态）。
    delete row.run_iters
    let restored: RolloutSrcMode | null = null
    if (row.rollout_src === 'run') {
      // 课程级的 `run` 删掉；别的课程级覆盖（有人显式写过 `rollout_src:'node'`）**不归这里管**。
      delete row.rollout_src
      restored = opts.remember ? takeRolloutSrc(course) : null
      if (restored) row.rollout_src = restored
    }
    notes.push(
      restored
        ? `训练模式 在线：已撤掉离线标记（run/run_iters），rollout 位置恢复为 ${restored}`
        : '训练模式 在线：已撤掉离线标记（run/run_iters）',
    )
  }
  if (opts.rolloutSrc && mode === 'online') {
    row.rollout_src = opts.rolloutSrc
    notes.push(`rollout 位置覆盖：courses.${course}.rollout_src=${opts.rolloutSrc}`)
  }
  courses[course] = row
  saveConfig({ ...cfg, courses })
  return { notes }
}
