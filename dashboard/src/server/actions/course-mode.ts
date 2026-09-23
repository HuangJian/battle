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
 */

import { loadConfig } from '../../core/config'
import type { RlConfig } from '../../core/types'
import { hubCandidates, hubSetCourseMode } from '../../stack/hub-admin'
import { loadConsoleState, saveConsoleState } from './console-state'

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

/** 热切一门课：hub 接受与否都落意图（意图是运维决定；回灌见 `restoreCourseModes`）。 */
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
