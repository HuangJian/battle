/** auto-offline-handoff.ts — hub → 控制台的**自动交接**（只做一件事：**导出任务包**）。
 *
 *  触发者不是人：自主 worker 在 hub 上 claim 了一门在训课，而盘上**没有包**（或包比权重/
 *  源码旧）⇒ hub 调 `/api/autoOfflineHandoff`，控制台把任务包导出来。包到手 + claim 成功
 *  才建立**接管（hold）**——「这门课归谁」自此只由 hub 的 hold 回答。
 *
 *  本模块是 `actions/course-mode.ts`（★M4 删除）里**唯一活下来的语义**。plan
 *  `worker-type-dispatch-model` §1.6-F1 定案：
 *    · 整条退役 **否决** —— hub 的触发链要靠它出包，否则 `pending_export` 永远没有包
 *      （`offline_stalled` 的锚点与触发账本一起落空）；
 *    · 保留「兼写 `courses.<课>.rollout_src=run` 停本机」那条腿 **也否决** —— 那正是 Q1
 *      花半页拆掉的耦合（本机停不停跑现在由 hub 的 hold 回答，见 M2 的 `held` 通道）。
 *  ⇒ 剩下的只有导包。这也让本模块**一个字都不写 rl-config / console-state**：它唯一
 *    的副作用是起一个导出子进程（`launchTaskBundleExport`）。
 *
 *  与已删除的 `setCourseMode` 的三点差异（刻意保留，逐条对应二轮 P0）：
 *    ① **不写意图、不 pin**：这不是人的决定；
 *    ② **不自动停课、不推 hub**（hub 已经在自己的临界区里记下 `pending_export` 了）；
 *    ③ 导包走同一张规则表 `autoBundleDecision`（已有包不重导、缺起点权重不导）。
 *
 *  返回体就是 hub 日志里那一行 `trigger_note`。
 */

import { courseEnabled } from '../../stack/courses'
import {
  TASK_BUNDLE_BUSY_KEY,
  exportGuard,
  launchTaskBundleExport,
  newestCodeMtimeMs,
  taskBundleInfo,
  weightsMtimeMs,
  type TaskBundleInfo,
} from '../bundles'
import { busy, type ActionResult } from './result'

/** 自动导出的**输入事实**（全部由调用方查好：纯函数不碰 IO，规则表见下）。 */
export interface AutoBundleFacts {
  /** `BCITY_NO_AUTO_TASK_BUNDLE` 已设（测试逃生阀：用例不该起真导出子进程）。 */
  valve: boolean
  /** 导出互斥键忙（导出是分钟级长任务，`launchTaskBundleExport` 也会自己拒第二次）。 */
  busy: boolean
  /** `exportGuard` 的拒启原因（`null` = 可以导）。 */
  guardReason: string | null
  /** 盘上已有包的形状（`taskBundleInfo`）。 */
  pack: TaskBundleInfo
  /** 活动权重（`tmp/<课程>/weights.json`）的 mtime（ms，0 = 读不到）。 */
  weightsMtimeMs: number
  /** `code.zip` 源文件的最新 mtime（ms；`newestCodeMtimeMs()`，0 = 读不到）。 */
  codeMtimeMs: number
}

/** 自动导出的结果：`started` 给人/测试断言，`note` 是人读一行（空串 = 没什么可说）。 */
export interface AutoBundleResult {
  started: boolean
  note: string
}

/** **要不要顺手导一次任务包**（纯函数：规则表逐条可单测）。
 *
 *  按序判定（第一条命中即返回）：
 *    ① 逃生阀 ⇒ 不导；
 *    ② 导出忙 ⇒ 不导（已有一次在跑，成果一样会被取到）；
 *    ③ 缺起点权重（`exportGuard`）⇒ 不导，**原样转述原因** + 指路；
 *    ④ 盘上已有包 **且不比权重/源码旧** ⇒ **不导也不作废**（旧包仍代表当前起点与当前代码）；
 *    ④' 盘上已有包**但比活动权重或源码旧** ⇒ **作废 + 重导**（2026-10-04 补，两个维度）：
 *       · 权重更新（训练已推进）——离线腿的续跑锚点只认回传/导入的轮次、看不见本机在线轮
 *         ⇒ 拿旧包会把云机拖回旧起点（在线训练的权重与动量白丢）；
 *       · 代码更新（开课导出包之后改过代码 / 带新代码重启过 hub）——包里是**导出那一刻的
 *         代码快照**（`code.zip`），旧包 = 云端跑旧代码；
 *    ⑤ 其余（缺包且可导）⇒ 导。
 *
 *  ★M4 删掉的两条（它们的前提是「课程有在线/离线模式」，随模式语义一起退役）：
 *    「非离线 ⇒ 不动」与「hub 没接受这次模式 ⇒ 不导」——本模块今天只有一个调用方
 *    （hub 的自动交接），那两条判据的输入在调用点上恒真/不存在。
 *
 *  失败语义：**任何**结局都不改调用方的 ok（接管已经在 hub 那边成立了）。
 */
export function autoBundleDecision(f: AutoBundleFacts): AutoBundleResult {
  const skip = (note: string): AutoBundleResult => ({ started: false, note })
  if (f.valve) return skip('（测试逃生阀 BCITY_NO_AUTO_TASK_BUNDLE：未自动导出任务包）')
  if (f.busy) return skip('上一次任务包导出还在跑 —— 未重复触发（完成后云机即可取到包）')
  if (f.guardReason) {
    return skip(`${f.guardReason} —— 未自动导出；先跑至少一轮（或导入一份权重）再重导`)
  }
  if (f.pack.exists && f.pack.mtimeMs >= Math.max(f.weightsMtimeMs, f.codeMtimeMs)) {
    return skip(
      `已有任务包 ${f.pack.path}（${f.pack.bytes} bytes，不早于当前权重与源码）——` +
        '云机可直接取；要重打请点「导出任务包」（那会先把旧包作废）',
    )
  }
  if (f.pack.exists) {
    const sides = [
      f.weightsMtimeMs > f.pack.mtimeMs ? '训练已推进（权重/动量比包新）' : '',
      f.codeMtimeMs > f.pack.mtimeMs ? '代码在导出之后改过（包里的 code.zip 是旧的）' : '',
    ]
      .filter(Boolean)
      .join('；')
    return {
      started: true,
      note:
        `盘上任务包已过期（${sides}）—— 旧包已作废、重导中：` +
        `云机在导出窗口会看到 404 并等新包（取到的一定是当前进度 + 最新代码）`,
    }
  }
  return {
    started: true,
    note: '任务包导出已启动（导出完成前 hub 的 /offline/task-pack 会 404，云机会等新包）',
  }
}

/** **自动交接**（hub → 控制台的反向调用；plan §1.6-F1 定案 = 只导包）。
 *
 *  hub 侧调用点：`GET /offline/task-pack` 缺包自愈（`_task_pack_miss_gate`）与 claim 遇缺包
 *  （`_claim_without_pack`）→ 都经 `task_pack.trigger_auto_handoff()` POST 到这里。
 *  控制台不可达 ⇒ hub 降级为手动（半状态由 `offline_stalled` 告警兜住）。
 */
export async function autoOfflineHandoff(course: string): Promise<ActionResult> {
  const c = String(course ?? '').trim()
  if (!c) return { ok: false, message: '需要 course（hub 的自动交接是按课触发的）' }
  if (!courseEnabled(c)) {
    return {
      ok: false,
      message: `${c} 未开课（停课删了开课标记）——自动交接只接在训的课；hub 侧该课应停在 waiting 等人处理`,
    }
  }
  const valve = Boolean(process.env.BCITY_NO_AUTO_TASK_BUNDLE)
  const guardReason = exportGuard(c)
  let bundle = autoBundleDecision({
    valve,
    busy: busy.has(TASK_BUNDLE_BUSY_KEY),
    guardReason,
    pack: taskBundleInfo(c),
    // ★ 2026-10-04：这条路的课**正在（或刚在）训练**——盘上的旧包会把云机拖回旧起点
    //   （离线腿的续跑锚点看不见本机在线轮）；代码也可能在导出之后改过 ⇒ 规则表 ④'
    //   按「权重/源码谁比包新」判重导。
    weightsMtimeMs: weightsMtimeMs(c),
    codeMtimeMs: newestCodeMtimeMs(),
  })
  const head = `${c} 自动交接：云机已认领（hub 的 pending_export）——控制台只负责把任务包导出来`
  if (bundle.started) {
    const launched = launchTaskBundleExport(c)
    if (!launched.ok) {
      return {
        ok: false,
        message: [
          head,
          `任务包导出未能启动（${launched.message}）——云机会等新包直到停滞告警`,
          '三条出路：TPU 重连 / 手工导入结果包 / 停课',
        ]
          .filter(Boolean)
          .join('；'),
      }
    }
    return { ok: true, message: [head, launched.message].filter(Boolean).join('；') }
  }
  // 没导：规则表说不用导（已有包 / 上一次导出还在跑 / 逃生阀）——都是正常结局；
  // `guardReason` 非空则是「导不了」（缺起点权重）：那半状态靠 hub 的 stalled 告警兜。
  return {
    ok: valve || guardReason === null,
    message: [head, bundle.note].filter(Boolean).join('；'),
  }
}
