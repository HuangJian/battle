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
 */

import { loadConfig } from '../../core/config'
import type { RlConfig } from '../../core/types'
import { hubCandidates, hubSetCourseMode } from '../../stack/hub-admin'
import { loadConsoleState, saveConsoleState } from './console-state'
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
 *  里加写配置」会让**开课重复写盘 1–3 次**，并把「停课」误翻译成「整段上云」。故拆开：
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
 *  ⚠ 生效时机是**段边界**不是秒级：`run_iters<0` 时一段 job 覆盖到课程末，trainer 阻塞在段等待里
 *  （plan §2.4 F8）——文案里写明，免得被当成「点了没反应」。
 */
export async function setCourseMode(
  course: string,
  mode: string,
): Promise<{ ok: boolean; message: string }> {
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
  // 那是旧的半语义：那颗开关现在同时把本机置成整段上云，两句话并排会自相矛盾）。
  const head = res.message.includes('已经是')
    ? `${c} 已经是 ${m}（幂等：hub 已重新下发 + 本机配置已重写）`
    : m === 'offline'
      ? `${c} 已切离线：整段上云（本机不再采样，云机自己跑 rollout——节点必须有 bun）`
      : `${c} 已切在线：本机采样 + 云机只算 PPO（不需要 bun）`
  const timing =
    '★ 段边界生效：已在飞的一段不会被抢占，而一段可能跑到课程末（要立刻断开请用停课/暂停）'
  // 配置侧的实情也回执（write 的 notes）：尤其「rollout 位置恢复为 node」这种——
  // 不说出来，操作员没法知道往返没把原来的选择弄丢。
  const cfgNote = notes.length > 0 ? notes.join('；') : ''
  if (!res.ok) {
    return {
      ok: false,
      message: [res.message, cfgNote, timing].filter(Boolean).join('；'),
    }
  }
  return { ok: true, message: [head, cfgNote, timing].filter(Boolean).join('；') }
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
