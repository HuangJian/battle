/** course-mode.ts — 每课 hub 派发模式（在线/离线）：**意图落盘 + 回灌**（R3-2）。
 *
 *  背景（plan §5 R3-1/R3-2）：hub 的 `offline` 语义早就有（`POST /admin/courses?mode=`），
 *  e2e 也钉过「离线课不派活、切回在线后同一个 job 被推走」，但**控制台从来没人调它**——
 *  面板上只有一个只读徽标，而 hub 的 mode 是 volatile（重启回启动参数）⇒ 谁想真用这个闸
 *  只能手敲 curl，且 hub 一重启就静默恢复派发。
 *
 *  本模块补上那两件缺的事：
 *    ① `setCourseMode`：热切 + 把**意图**写进 console-state（`courseModes`）；
 *    ② `restoreCourseModes`：起 hub 时把意图回灌（否则重启即静默回在线）。
 *
 *  改意向后 hub 不可达怎么办：**意图照样落盘**并如实报告「已记录，但 hub 未接受」——
 *  运维的决定不因为 hub 没起来而蒸发；回灌兜底。
 *
 *  ★ 2026-09-24（plan/train-mode-hot-switch.plan.md L1）：这颗开关升级为**唯一的模式开关**——
 *  hub 派发模式与本机训练配置（`courses.<课>.rollout_src/run_iters`）一起动，不再需要重开课。
 *  两件事拆在三个函数里，边界就是本文件的全部契约：
 *    · `pushCourseMode`：**只**推 hub + 落意图（开课/停课/回灌共用，绝不写配置）；
 *    · `setCourseMode`：**那颗开关** = `applyTrainModeToConfig`（唯一配置写面）+ `pushCourseMode`；
 *    · `restoreCourseModes`：起 hub 回灌（只推 hub：回灌不是用户动作，不该改训练配置）。
 *
 *  ★ 2026-09-25（plan/offline-switch-auto-bundle）：这颗开关还要**顺手把任务包导出来**——
 *  「切离线」= 「这门课交给云机接手」，而云机取的是 `tmp/<课>/task-<课>.zip`；此前只有**开课**
 *  才导包（`course-lifecycle.openCourse`），热切离线不导 ⇒ 在线课切成离线后云机 404 干等
 *  `wait_pack_sec`（30 分钟）才由一句 `SystemExit` 告诉人（用户 2026-09-25 报障）。
 *  规则 = `autoBundleDecision`（缺包才导、有包不动、导不出只说清不改 `ok`）。
 */

import { loadConfig } from '../../core/config'
import type { RlConfig } from '../../core/types'
import { hubCandidates, hubSetCourseMode } from '../../stack/hub-admin'
import {
  TASK_BUNDLE_BUSY_KEY,
  exportGuard,
  launchTaskBundleExport,
  taskBundleInfo,
  type TaskBundleInfo,
} from '../bundles'
import { loadConsoleState, saveConsoleState } from './console-state'
import { busy } from './result'
import { applyTrainModeToConfig } from './train-mode'

export type CourseMode = 'online' | 'offline'

/** 合法模式表（与 hub 侧 `COURSE_MODES` 同名同值；两边不一致时 hub 会 400 响亮）。 */
const MODES: readonly CourseMode[] = ['online', 'offline'] as const

/** 记录在案的意图（归一化：丢掉形状不对的键；空表 = 无意图）。 */
export function readCourseModes(): Record<string, CourseMode> {
  const raw = loadConsoleState().courseModes ?? {}
  const out: Record<string, CourseMode> = {}
  for (const [course, mode] of Object.entries(raw)) {
    if (course && (mode === 'online' || mode === 'offline')) out[course] = mode
  }
  return out
}

/** 把一门课的模式推给 hub（多个候选基址逐个试；返回人读错误，null = 被接受）。 */
async function pushMode(
  cfg: RlConfig,
  course: string,
  mode: CourseMode,
  only?: string,
): Promise<string | null> {
  const token = String(cfg.rl?.remote_token ?? '')
  const candidates = only ? [only] : hubCandidates(cfg, course)
  let last: string | null = '没有可试的 hub 地址'
  for (const base of candidates) {
    last = await hubSetCourseMode(base, token, course, mode)
    if (last === null) return null
  }
  return last
}

/** 「hub 活着，但它的课程表里还没有这门课」的错误指纹。
 *
 *  **只有这一类错误值得重试**：hub 的课程表是**扫盘发现**的（`_course_dir_live` 看
 *  `<traj>/<课>/{remote-jobs,offline}` 是否新鲜），而回灌跑在 `start hub` 之后**紧接着**
 *  的那一拍 —— 磁盘事实可能还没落（课程刚建目录）或 hub 还没扫到。2026-09-23 实测：
 *  hub 重启时 `courses=[]`，九条回灌 POST 全 400，三个离线课里**恰有一门**的重试窗口整段
 *  落在发现之前 ⇒ 该课静默留在 online（面板一路显示「在训/切离线」）。
 *
 *  反过来，**hub 根本连不上**（ECONNREFUSED / 超时）不该重试：那不会因为等 2 秒而好，
 *  而回灌在 `start` 的返回路径上 —— 白等 N×2s 只是让「起 hub」这个动作变慢。
 */
const UNKNOWN_COURSE_RE = /需要合法 course|未知课程|unknown course/i

/** **只推 hub + 落意图**（绝不动 rl-config）：开课 / 停课 / 那颗开关共用的原语。
 *
 *  ★ 2026-09-24（plan §2.2 F9）：它曾是 `setCourseMode` 的全部内容，而 `pushHubMode`
 *  （`course-lifecycle.ts`，带 3×2s 重试）就是循环调 `setCourseMode` 的 ⇒ 「往 setCourseMode
 *  里加写配置」会让**开课重复写盘 1–3 次**，并把「停课」误翻译成「云机接手」。故拆开：
 *  写配置是**用户动作**的事（只有那颗开关与开课弹窗有），推 hub 是**基建**的事。
 *
 *  校验与旧行为逐字一致（非法模式/空课程：一次都不打 hub，也不落意图）。 */
export async function pushCourseMode(
  course: string,
  mode: string,
): Promise<{ ok: boolean; message: string }> {
  const c = String(course ?? '').trim()
  const m = String(mode ?? '').trim() as CourseMode
  if (!c) return { ok: false, message: '需要课程（hub 的模式是按课程记的）' }
  if (!MODES.includes(m)) {
    return { ok: false, message: `模式非法: ${JSON.stringify(mode)}（只接受 online / offline）` }
  }
  const cfg = loadConfig()
  const prev = readCourseModes()[c]
  const err = await pushMode(cfg, c, m)
  saveConsoleState({ courseModes: { ...readCourseModes(), [c]: m } })
  if (err) {
    return {
      ok: false,
      message: `${c} → ${m} 意图已记录，但 hub 未接受：${err}（起 hub 时会按意图回灌）`,
    }
  }
  if (prev === m) return { ok: true, message: `${c} 已经是 ${m}（幂等：已重新下发）` }
  return {
    ok: true,
    message:
      m === 'offline'
        ? `${c} 已切离线：不实时派发 PPO，只接收 it 权重/指标回传`
        : `${c} 已切在线：恢复实时派发`,
  }
}

/** **热切一门课**（那颗「切离线 / 切换成在线」开关）：本机训练配置 + hub 模式**一起动**。
 *
 *  ★ 2026-09-24（plan/train-mode-hot-switch.plan.md L1）：此前只翻 hub 那半边 ⇒ 用户报障
 *  「离线课切回在线后，Kaggle 仍因缺 bun 拒单」（配置里 `rollout_src=run` 一直没撤，下一段
 *  照样派 `kind=run`）。现在：
 *    ① **先落本机事实**（同步写盘，改的是 python 每轮读的那份 rl-config）；
 *    ② 再推 hub 镜像（网络，可能失败——失败照样如实报告，意图已落盘、回灌兜底）。
 *  顺序即契约：配置写不进去就**不推 hub**（否则留下「hub 离线但本机仍本机采样」的半状态）。
 *
 *  ⚠ 生效时机是**轮边界**不是秒级：切离线后本机在下一个轮边界干净收官（`ROUND_OFFLINE_EXIT`），
 *  **不再有「段等待」**——「发一份 kind=run 队列项、本机等 8h」那条腿 2026-09-25 退役
 *  （plan/online-offline-role-routing §7）；切回在线同样在轮边界恢复本机采样。文案里写明，
 *  免得被当成「点了没反应」。
 */
/** 自动导出的**输入事实**（全部由调用方查好：纯函数不碰 IO，规则表见 plan §3.1）。 */
export interface AutoBundleFacts {
  /** 目标模式。 */
  mode: string
  /** ② hub 镜像是否被接受（`res.ok`）。**没接受就不导**：离线意图没落地，包没有消费者。 */
  hubAccepted: boolean
  /** `BCITY_NO_AUTO_TASK_BUNDLE` 已设（测试逃生阀：用例不该起真导出子进程）。 */
  valve: boolean
  /** 导出互斥键忙（导出是分钟级长任务，`launchTaskBundleExport` 也会自己拒第二次）。 */
  busy: boolean
  /** `exportGuard` 的拒启原因（`null` = 可以导）。 */
  guardReason: string | null
  /** 盘上已有包的形状（`taskBundleInfo`）。 */
  pack: TaskBundleInfo
}

/** 自动导出的结果：`started` 给人/测试断言，`note` 是人读一行（空串 = 没什么可说）。 */
export interface AutoBundleResult {
  started: boolean
  note: string
}

/** **切离线要不要顺手导一次任务包**（纯函数：规则表逐条可单测，全表见 plan §3.1）。
 *
 *  按序判定（第一条命中即返回）：
 *    ① 非离线 ⇒ 不动（导包只属于离线语义）；
 *    ② hub 没接受这次模式 ⇒ 不导（离线意图没落地，包没有消费者——顺序上导出在 ② 之后，
 *       这条把「排在 ② 之后」与「② 失败不导」两句话对齐）；
 *    ③ 逃生阀 ⇒ 不导；
 *    ④ 导出忙 ⇒ 不导（已有一次在跑，成果一样会被取到）；
 *    ⑤ 缺起点权重（`exportGuard`）⇒ 不导，**原样转述原因** + 指路；
 *    ⑥ 盘上已有包 ⇒ **不导也不作废**（热切不是重开课；包作废是 `launchTaskBundleExport`
 *       的内建行为（`export.ts` 的 `invalidateTaskBundle`），一旦调用就把旧包挪进
 *       `stale-packs/` ⇒ 云机在导出窗口里探到 404）；
 *    ⑦ 其余（缺包且可导）⇒ 导。
 *
 *  失败语义：**任何**结局都不改 `setCourseMode` 的 `ok`（模式切换本身已经成功了）。
 */
export function autoBundleDecision(f: AutoBundleFacts): AutoBundleResult {
  const skip = (note: string): AutoBundleResult => ({ started: false, note })
  if (f.mode !== 'offline') return skip('')
  if (!f.hubAccepted) {
    return skip('hub 未接受离线意图 —— 未自动导出任务包（修好 hub 后再切一次即可）')
  }
  if (f.valve) return skip('（测试逃生阀 BCITY_NO_AUTO_TASK_BUNDLE：未自动导出任务包）')
  if (f.busy) return skip('上一次任务包导出还在跑 —— 未重复触发（完成后云机即可取到包）')
  if (f.guardReason) {
    return skip(`${f.guardReason} —— 未自动导出；先跑至少一轮（或导入一份权重）再切离线/重导`)
  }
  if (f.pack.exists) {
    return skip(
      `已有任务包 ${f.pack.path}（${f.pack.bytes} bytes）—— 云机可直接取；` +
        '要重打请点「导出任务包」（那会先把旧包作废）',
    )
  }
  return {
    started: true,
    note: '任务包导出已启动（导出完成前 hub 的 /offline/task-pack 会 404，云机会等新包）',
  }
}

export async function setCourseMode(
  course: string,
  mode: string,
): Promise<{ ok: boolean; message: string; bundle?: AutoBundleResult }> {
  const c = String(course ?? '').trim()
  const m = String(mode ?? '').trim() as CourseMode
  if (!c) return { ok: false, message: '需要课程（hub 的模式是按课程记的）' }
  if (!MODES.includes(m)) {
    return { ok: false, message: `模式非法: ${JSON.stringify(mode)}（只接受 online / offline）` }
  }
  // ① 本机事实：`saveConfig` 的容量/槽位守卫会抛（core/config.ts）——写不进去就**不推 hub**，
  //    回执如实说「本机配置未落」，而不是把两半拆成一个假状态。
  let notes: string[] = []
  try {
    notes = applyTrainModeToConfig(c, m, { remember: true }).notes
  } catch (e: unknown) {
    return {
      ok: false,
      message: `${c} → ${m} 未生效：本机配置写不进去（${e instanceof Error ? e.message : String(e)}）——hub 未动（不留半状态）`,
    }
  }
  // ② hub 镜像
  const res = await pushCourseMode(c, m)
  // 文案按**合并后**的语义写（不再复用 `pushCourseMode` 那句「只接收 it 权重/指标回传」——
  // 那是旧的半语义：那颗开关现在同时把本机置成「这门课不归本机」，两句话并排会自相矛盾）。
  const head = res.message.includes('已经是')
    ? `${c} 已经是 ${m}（幂等：hub 已重新下发 + 本机配置已重写）`
    : m === 'offline'
      ? `${c} 已切离线：本机不跑这门课（云机取任务包接手——battle.offline.ipynb 跑 rollout+PPO）`
      : `${c} 已切在线：本机采样 + 云机只算 PPO（不需要 bun）`
  const timing =
    '★ 轮边界生效：本机在下一个轮边界干净收官（不再有「段等待」）；云机那份在它自己的会话里跑（要立刻断开请用停课/暂停）'
  // 配置侧的实情也回执（write 的 notes）：尤其「rollout 位置恢复为 node」这种——
  // 不说出来，操作员没法知道往返没把原来的选择弄丢。
  const cfgNote = notes.length > 0 ? notes.join('；') : ''
  // ③ 派生动作：切离线**顺手出包**（规则表 = autoBundleDecision）。
  //    位置契约：排在本机配置 ① 与 hub 镜像 ② **之后**，且 `hubAccepted` 传进去 ⇒ ②
  //    失败时决定必然是「不导」（plan §3.1 的第 ② 条）。顺序之外**不改 ok**。
  let bundle: AutoBundleResult = autoBundleDecision({
    mode: m,
    hubAccepted: res.ok,
    valve: Boolean(process.env.BCITY_NO_AUTO_TASK_BUNDLE),
    busy: busy.has(TASK_BUNDLE_BUSY_KEY),
    guardReason: exportGuard(c),
    pack: taskBundleInfo(c),
  })
  if (bundle.started) {
    const launched = launchTaskBundleExport(c)
    bundle = launched.ok
      ? { started: true, note: launched.message }
      : {
          started: false,
          note: `任务包导出未能启动（${launched.message}）—— 可稍后点「导出任务包」重试`,
        }
  }
  if (!res.ok) {
    return {
      ok: false,
      message: [res.message, cfgNote, timing, bundle.note].filter(Boolean).join('；'),
      bundle,
    }
  }
  return {
    ok: true,
    message: [head, cfgNote, timing, bundle.note].filter(Boolean).join('；'),
    bundle,
  }
}

/** 回灌的有界重试（`attempts` 含首试；测试注入小值避免空等）。 */
export interface ModeRetry {
  attempts?: number
  delayMs?: number
}

/** 推一门课，**只对「hub 还不认识这门课」做有界重试**（判据见 `UNKNOWN_COURSE_RE`）。 */
async function pushModeWithRetry(
  cfg: RlConfig,
  course: string,
  mode: CourseMode,
  only?: string,
  retry: ModeRetry = {},
): Promise<string | null> {
  const attempts = Math.max(1, retry.attempts ?? 3)
  const delayMs = Math.max(0, retry.delayMs ?? 2000)
  let last = await pushMode(cfg, course, mode, only)
  for (let i = 1; i < attempts && last !== null && UNKNOWN_COURSE_RE.test(last); i++) {
    if (delayMs > 0) await Bun.sleep(delayMs)
    last = await pushMode(cfg, course, mode, only)
  }
  return last
}

/** 起 hub 后回灌全部意图（幂等；hub 不可达只如实报告，不抛）。
 *
 *  回灌**两种模式都发**（不是只发 offline）：控制台的意图是权威的——hub 可能被以别的
 *  启动参数拉起来（例如 `--offline c5`），只补 offline 会让「我明明点过在线」悄悄失效。
 *
 *  ★ 2026-09-23：每门课**有界重试**（只对「不认识这门课」）——回灌跑在 hub 刚起来那一拍，
 *  而 hub 的课程表是扫盘发现的，磁盘事实/扫描都可能晚一两拍。此前是单发：偏巧落在发现之前
 *  的那一门课意图就静默失配（真机实测三门里的一门）。重试耗尽仍**不是失败**（意图已落盘，
 *  且 hub 侧现在也会在 mode POST 时按需真扫），只是如实报进 `failed`。
 */
export async function restoreCourseModes(
  cfg: RlConfig,
  only?: string,
  retry: ModeRetry = {},
): Promise<{ restored: number; failed: string[] }> {
  const modes = readCourseModes()
  const failed: string[] = []
  let restored = 0
  for (const course of Object.keys(modes).sort()) {
    const err = await pushModeWithRetry(cfg, course, modes[course], only, retry)
    if (err) failed.push(`${course}: ${err}`)
    else restored += 1
  }
  return { restored, failed }
}

/** 回灌结果 → 一行摘要（无意图 → 空串：调用方不要为「什么都没做」编文案）。 */
export async function restoreCourseModesNote(
  cfg: RlConfig,
  only?: string,
  retry: ModeRetry = {},
): Promise<string> {
  const { restored, failed } = await restoreCourseModes(cfg, only, retry)
  if (restored === 0 && failed.length === 0) return ''
  const head = `已回灌 ${restored} 门课的离线/在线意图`
  return failed.length ? `${head}；失败 ${failed.join('、')}` : head
}
