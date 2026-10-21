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
  backfillHubRun,
  exportBusyReason,
  exportGuard,
  launchTaskBundleExport,
  newestCodeMtimeMs,
  readClusterSnapshot,
  taskBundleInfo,
  taskBundleMeta,
  weightsMtimeMs,
  type TaskBundleInfo,
} from '../bundles'
import { type ActionResult } from './result'

/** 自动导出的**输入事实**（全部由调用方查好：纯函数不碰 IO，规则表见下）。 */
export interface AutoBundleFacts {
  /** `BCITY_NO_AUTO_TASK_BUNDLE` 已设（测试逃生阀：用例不该起真导出子进程）。 */
  valve: boolean
  /** 本课导出忙（进程内互斥键 **或** 持久 `running` 状态都算；判据 = `exportBusyReason`）。
   *  导出是分钟级长任务，`launchTaskBundleExport` 也会自己拒第二次。 */
  busy: boolean
  /** `exportGuard` 的拒启原因（`null` = 可以导）。 */
  guardReason: string | null
  /** 盘上已有包的形状（`taskBundleInfo`）。 */
  pack: TaskBundleInfo
  /** 活动权重（`tmp/<课程>/weights.json`）的 mtime（ms，0 = 读不到）。 */
  weightsMtimeMs: number
  /** 代码维度的事实（两套口径，见 `codeFreshness()`）。 */
  code: CodeFreshness
}

/** 代码维度的事实（plan/cluster-code-snapshot §4.2）。
 *
 *  · `snapshot`（首选）：集群代码快照在、锚活着、包旁挂的 `.meta.json` 也在 ⇒ 比 sha。
 *    会话冻结语义下这才是**对的**判据：改了源码但不重启 ⇒ 重导也只能拿到同一份代码，
 *    旧 mtime 口径却会判「旧」⇒ 白烧一次分钟级导出 + 让云机多等一段 404 窗口。
 *  · `mtime`（回落）：其余一切情形（没快照 / 锚已死 / 老包没 sidecar）。保守方向：
 *    「宁可白重导一次，也不放一份旧代码去云端」。
 */
export type CodeFreshness =
  | { kind: 'snapshot'; same: boolean; sha: string }
  | { kind: 'mtime'; mtimeMs: number }

/** 代码维度是否比包「新」（纯函数：两套口径各自的判据）。 */
export function codeDimensionStale(code: CodeFreshness, packMtimeMs: number): boolean {
  return code.kind === 'snapshot' ? !code.same : code.mtimeMs > packMtimeMs
}

/** 取代码维度事实（**唯一**读盘处；判据本身是纯函数，便于单测）。 */
export function codeFreshness(course: string): CodeFreshness {
  const snap = readClusterSnapshot()
  const meta = snap && snap.anchorAlive ? taskBundleMeta(course) : null
  if (snap && meta) {
    return { kind: 'snapshot', same: meta.codeSha256 === snap.sha256, sha: snap.sha256 }
  }
  return { kind: 'mtime', mtimeMs: newestCodeMtimeMs() }
}

/** 回执里点出代码维度那一半（排障要知道「等的是什么」）。 */
function codeSideNote(code: CodeFreshness, packMtimeMs: number): string {
  if (!codeDimensionStale(code, packMtimeMs)) return ''
  return code.kind === 'snapshot'
    ? `集群代码快照已换（包里的 code.zip 是上一份；当前快照 sha ${code.sha.slice(0, 12)}）`
    : '代码在导出之后改过（包里的 code.zip 是旧的）'
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
 *    ④ 盘上已有包 **且不比权重/代码旧** ⇒ **不导也不作废**（旧包仍代表当前起点与当前代码）；
 *    ④' 盘上已有包**但比活动权重或代码旧** ⇒ **作废 + 重导**（2026-10-04 补，两个维度）：
 *       · 权重更新（训练已推进）——离线腿的续跑锚点只认回传/导入的轮次、看不见本机在线轮
 *         ⇒ 拿旧包会把云机拖回旧起点（在线训练的权重与动量白丢）；
 *       · 代码更新——包里是**导出那一刻的代码快照**（`code.zip`），旧包 = 云端跑旧代码。
 *         ★ 2026-10-10（plan/cluster-code-snapshot §4.2）：**代码维度的判据换了**——不再是
 *         「源文件 mtime vs 包 mtime」（集群代码快照冻结后，改源码但不重启会被**误**判成旧：
 *         重导也只能拿到同一份代码，白烧一次分钟级导出、还让云机多等一段 404 窗口），
 *         而是「包里的 `code.zip` 是不是**当前集群快照**那一份」（`codeFreshness()`）；
 *         快照不可知（没快照 / 锚已死 / 老包没旁挂件）才回落旧 mtime 口径。
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
  const codeStale = codeDimensionStale(f.code, f.pack.mtimeMs)
  if (f.pack.exists && f.pack.mtimeMs >= f.weightsMtimeMs && !codeStale) {
    const how =
      f.code.kind === 'snapshot'
        ? '不早于当前权重，且 code.zip 就是当前集群代码快照'
        : '不早于当前权重与源码'
    return skip(
      `已有任务包 ${f.pack.path}（${f.pack.bytes} bytes，${how}）——` +
        '云机可直接取；要重打请点「导出任务包」（那会先把旧包作废）',
    )
  }
  if (f.pack.exists) {
    const sides = [
      f.weightsMtimeMs > f.pack.mtimeMs ? '训练已推进（权重/动量比包新）' : '',
      codeSideNote(f.code, f.pack.mtimeMs),
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

/** 规则表的**输入事实**（唯一一处读盘；两个调用方共用 ⇒ 判据不会各读一份而漂开）。
 *
 *  `busy` **按课程**查（2026-10-21，plan/export-bundle-stall §3.1(1)）：A 课在导出不许
 *  否决 B 课的重导——两门课的产物零共享。
 *
 *  `busy` 的判据与「点导出」**同源**（`exportBusyReason`：进程内键 + 持久 `running` 状态）：
 *  控制台重启后进程内键消失，只看 `busy` 会把「同课导出还在跑」判成「可以导」⇒ 起第二个
 *  导出抢同一个 `.tmp`；而 `launchTaskBundleExport` 随后拒启又会让 hub 记一次失败。 */
export function bundleFacts(course: string): AutoBundleFacts {
  return {
    valve: Boolean(process.env.BCITY_NO_AUTO_TASK_BUNDLE),
    busy: exportBusyReason(course) !== null,
    guardReason: exportGuard(course),
    pack: taskBundleInfo(course),
    // ★ 2026-10-04：这条路的课**正在（或刚在）训练**——盘上的旧包会把云机拖回旧起点
    //   （离线腿的续跑锚点看不见本机在线轮）；代码维度见 `codeFreshness()`（2026-10-10
    //   plan/cluster-code-snapshot：快照口径优先，mtime 只在快照不可知时兜底）。
    weightsMtimeMs: weightsMtimeMs(course),
    code: codeFreshness(course),
  }
}

/** **开课顺手保新鲜**（plan/offline-segment-restart §3.3，2026-10-14）：盘上那份包**已被消化**
 *  （权重/代码比包新）⇒ 作废 + 重导；**缺包不预造**；失败不抛。
 *
 *  为什么必须有它：「跑满」闸锚在**包 zip 的 sha256** 上（`hub/queue_offline.py:1036`），而
 *  改 iters / 停课 / 重开课 / 重启 trainer+hub **一个都不碰包** ⇒ 人「停课 → 改大 iters →
 *  开课」之后云机仍看到 `completed`；而重导是**需求侧**触发的（要 claim 成功/取包），
 *  `completed` 又把 claim 挡死 ⇒ 「没有需求就没有导包，没有导包就永远 completed」的死锁
 *  （2026-10-14 现场：y5-hit 20 次轮询全被挡，人手动点「导出任务包」才解）。开课 = 人对
 *  「下一段」唯一的显式表态，就在这一刻把包换成新段 —— 新 sha ≠ 旧章 ⇒ 闸自动开。
 *
 *  ★ **缺包不预造**：★M4 定「包只为真实提出需求的云机而造」。无包时云机 claim 会走
 *  `pending_export` ⇒ `autoOfflineHandoff`（hub 的自动交接）那条腿一个字不改；这里是**另一条**腿。
 *  ★ 失败不连坐：导包起不来不能把开课判失败（开课已经在盘上成立了），只如实回一行。
 */
export function ensureFreshTaskBundle(course: string): AutoBundleResult {
  const c = String(course ?? '').trim()
  if (!c) return { started: false, note: '需要课程（保新鲜是按课做的）' }
  if (!taskBundleInfo(c).exists) {
    return {
      started: false,
      note: '盘上还没有任务包——**不预造**（云机 claim 时会走自动交接导一次）',
    }
  }
  const bundle = autoBundleDecision(bundleFacts(c))
  if (!bundle.started) return bundle
  const launched = launchTaskBundleExport(c)
  return launched.ok
    ? { started: true, note: [bundle.note, launched.message].filter(Boolean).join('；') }
    : {
        started: false,
        note: `${bundle.note}；导出未能启动（${launched.message}）——云机会等新包直到停滞告警`,
      }
}

/** **自动交接**（hub → 控制台的反向调用；plan §1.6-F1 定案 = 只导包）。
 *
 *  hub 侧调用点：`GET /offline/task-pack` 缺包自愈（`_task_pack_miss_gate`）与 claim 遇缺包
 *  （`_claim_without_pack`）→ 都经 `task_pack.trigger_auto_handoff()` POST 到这里。
 *  控制台不可达 ⇒ hub 降级为手动（半状态由 `offline_stalled` 告警兜住）。
 */
export async function autoOfflineHandoff(
  course: string,
  /** 回灌腿的注入点（测试传替身 ⇒ 不起 python；生产缺省就是真跑）。 */
  deps: { backfill?: typeof backfillHubRun } = {},
): Promise<ActionResult> {
  const c = String(course ?? '').trim()
  if (!c) return { ok: false, message: '需要 course（hub 的自动交接是按课触发的）' }
  if (!courseEnabled(c)) {
    return {
      ok: false,
      message: `${c} 未开课（停课删了开课标记）——自动交接只接在训的课；hub 侧该课应停在 waiting 等人处理`,
    }
  }
  const valve = Boolean(process.env.BCITY_NO_AUTO_TASK_BUNDLE)
  // ★ B0 回灌腿（plan §3.2.1 定案①）：**先回灌、后判包**。
  //   顺序是承重的：段末那一刻盘上只有**刚跑完那一段**的包（旧 sha），而本机账本停在段起点
  //   ⇒ 不先回灌的话规则表 ④ 只说「已有任务包，云机可直接取」，于是下一段永远不从段末起。
  //   回灌推进本机账本/权重之后，同一张规则表自然命中 ④'（包比权重旧 ⇒ 作废重导）。
  //   幂等由 python 侧保证（轮次目录只补缺、账本行按 it 去重、已到段末 ⇒ 直接说无需回灌）。
  const backfill = await (deps.backfill ?? backfillHubRun)(c)
  const guardReason = exportGuard(c)
  const bundle = autoBundleDecision(bundleFacts(c))
  const head = `${c} 自动交接：云机已认领（hub 的 pending_export）——控制台只负责把任务包导出来`
  if (bundle.started) {
    const launched = launchTaskBundleExport(c)
    if (!launched.ok) {
      return {
        ok: false,
        message: [
          head,
          backfill.note,
          `任务包导出未能启动（${launched.message}）——云机会等新包直到停滞告警`,
          '三条出路：TPU 重连 / 手工导入结果包 / 停课',
        ]
          .filter(Boolean)
          .join('；'),
      }
    }
    return { ok: true, message: [head, backfill.note, launched.message].filter(Boolean).join('；') }
  }
  // 没导：规则表说不用导（已有包 / 上一次导出还在跑 / 逃生阀）——都是正常结局；
  // `guardReason` 非空则是「导不了」（静态拒因：缺起点权重 / 无剩余轮次 / 轮数 > 硬上界）：
  // 那半状态靠 hub 的 stalled 告警与 `export-status.json` 兜。
  return {
    ok: valve || guardReason === null,
    message: [head, backfill.note, bundle.note].filter(Boolean).join('；'),
  }
}
