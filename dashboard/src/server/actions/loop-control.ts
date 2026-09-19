/** loop-control.ts — 「暂停/恢复某课」的控制意图文件（R2d 操作面）。
 *
 *  背景：多课程并行后训练收敛为**一个进程**（`nn-training/run_rl_cluster.py --serve`，
 *  内部一台单线程 supervisor 持有每课一条任务队列）。控制台要能暂停某一课，就必须把指令
 *  送进那个已经跑着的进程 —— 而**不**为此多挂一个 HTTP 服务（多一个端口、多一份鉴权、
 *  多一处故障域，用户 2026-09-18 定案）。
 *
 *  通道 = 两侧本来就共享的工作区里的一份**意图文件**（`tmp/loop-control.json`）：
 *  控制台写、训练进程每拍读一次并施加到调度器（`nn-training/rl/loop_control.py`）。
 *  hub 挂了也能用，进程重启后意图仍在（对比 hub 的 course-mode 是 volatile，那里要靠
 *  `restoreCourseModes` 回灌；这里**文件本身就是状态**，不需要回灌）。
 *
 *  语义（与 python 侧逐条对齐）：
 *    · 只影响**调度**：队列与账本一个字不动，恢复后从原处接着跑；
 *    · 意图合法性由本层挡住（课程名/形状），非法输入**一次都不写盘**；
 *    · 写盘是**原子**的（tmp + rename）：训练侧每拍都在读，读到半个 JSON 就等于读到坏文件
 *      （那边会保守地继续训练 = 暂停不生效，但那是**静默**的失效，必须从写侧杜绝）。
 *
 *  与「离线开关」（`course-mode.ts`）的区别：离线 = hub 不派 PPO 活；暂停 = **本机训练进程
 *  这一课不再推进**（连 rollout/本机 PPO 也停）。两者可独立使用（离线课也可以继续在跑本机
 *  降级/预采）。
 */

import { existsSync, readFileSync, renameSync, writeFileSync } from 'fs'
import path from 'path'
import { pidAlive } from '../../core/net'
import { loopAppliedPath, loopControlPath } from '../../core/paths'

/** 控制文件的版本号（与 python 侧同一常量语义；未知版本按当前形状解析，不拒）。 */
export const LOOP_CONTROL_VERSION = 1

/** 课程名合法字符集（与 `core/slots.validateCourseName` / python `loop_control._ALLOWED` 同一约束）。 */
const COURSE_RE = /^[A-Za-z0-9._-]+$/

export interface LoopControl {
  /** 要求暂停的课程（顺序 = 写入顺序；训练侧只当集合用）。 */
  paused: string[]
  /** 文件不存在（正常态，不是错误）。 */
  found: boolean
  /** 读/解析问题（人读；空 = 无错）。有错时 `paused` 为空（保守：不误停）。 */
  error: string
}

/** 解析控制文件内容（纯函数；与 python `parse_control` 同判据）。 */
export function parseLoopControl(raw: unknown): LoopControl {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { paused: [], found: false, error: '控制文件根不是对象' }
  }
  const o = raw as Record<string, unknown>
  const p = o.paused
  if (p === undefined || p === null) return { paused: [], found: true, error: '' }
  if (!Array.isArray(p)) return { paused: [], found: true, error: 'paused 不是数组' }
  const bad: string[] = []
  const paused: string[] = []
  for (const item of p) {
    const c = typeof item === 'string' ? item.trim() : ''
    if (!c || !COURSE_RE.test(c) || c.includes('..')) bad.push(JSON.stringify(item))
    else if (!paused.includes(c)) paused.push(c)
  }
  return {
    paused,
    found: true,
    error: bad.length > 0 ? `paused 里有非法课程名（已忽略）：${bad.join(', ')}` : '',
  }
}

/** 读控制意图。文件不存在 / 读失败 / 解析失败 → 空意图 + 原因（**永不抛**：观测/操作面坏了不该带崩面板）。 */
export function readLoopControl(file: string = loopControlPath()): LoopControl {
  try {
    if (!existsSync(file)) return { paused: [], found: false, error: '' }
    return parseLoopControl(JSON.parse(readFileSync(file, 'utf8')) as unknown)
  } catch (e) {
    return {
      paused: [],
      found: false,
      error: `控制文件读失败：${e instanceof Error ? e.message : String(e)}`,
    }
  }
}

/** 原子写控制文件（tmp + rename）。返回 null = 成功，否则人读错误。 */
export function writeLoopControl(
  paused: string[],
  file: string = loopControlPath(),
): string | null {
  const body = `${JSON.stringify({ version: LOOP_CONTROL_VERSION, paused }, null, 2)}\n`
  const tmp = path.join(path.dirname(file), `.loop-control.${process.pid}.tmp`)
  try {
    writeFileSync(tmp, body, 'utf8')
    renameSync(tmp, file) // 同目录 rename：训练侧要么读到旧内容、要么读到新内容，永不会读到半个文件
    return null
  } catch (e) {
    return `${e instanceof Error ? e.message : String(e)}`
  }
}

/** 训练进程写回的回执（`loop-control.applied.json`）：**它实际施加了什么**。
 *
 *  为什么要它：只有意图文件时，控制台点完暂停只能盲猜生效没生效（进程可能没在跑、也可能
 *  还没轮到读文件）。回执里的 `pid` 是分辨「残留文件（进程已死）」与「真的在暂停着」的唯一
 *  依据——所以读侧一律用**进程存活**过滤，不死文件当事实。
 */
export interface LoopApplied {
  /** 该进程当前实际暂停着的课程。 */
  paused: string[]
  /** 写回执的进程号（0 = 读不到/形状不对）。 */
  pid: number
  /** 该进程是否还活着（false ⇒ `paused` 一律视为空：残留文件不是事实）。 */
  alive: boolean
  /** 回执时间戳（秒；0 = 未知）——只用于「多久没更新」的展示。 */
  at: number
}

const APPLIED_EMPTY: LoopApplied = { paused: [], pid: 0, alive: false, at: 0 }

/** 读回执（**永不抛**）。`alive` 为假时 `paused` 已被清空——调用方不必再自己判活。 */
export function readLoopApplied(
  file: string = loopAppliedPath(),
  alive: (pid: number) => boolean = pidAlive,
): LoopApplied {
  try {
    if (!existsSync(file)) return APPLIED_EMPTY
    const o = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>
    const pid = typeof o.pid === 'number' && Number.isFinite(o.pid) ? o.pid : 0
    const at = typeof o.at === 'number' && Number.isFinite(o.at) ? o.at : 0
    const isAlive = pid > 0 && alive(pid)
    const raw = Array.isArray(o.paused) ? o.paused : []
    const paused = raw
      .filter((x): x is string => typeof x === 'string')
      .filter((c) => COURSE_RE.test(c) && !c.includes('..'))
    return { paused: isAlive ? paused : [], pid, alive: isAlive, at }
  } catch {
    return APPLIED_EMPTY
  }
}

/** 控制面事实（一次读两侧）：意图 + 实际施加（回执已按 liveness 过滤）。 */
export function readPauseFacts(): { intent: string[]; applied: string[] } {
  return { intent: readLoopControl().paused, applied: readLoopApplied().paused }
}

/** 暂停/恢复一门课。**幂等**：重复点同一个方向不会把别的课程从表里挤掉。 */
export function setCoursePaused(
  course: string,
  paused: boolean,
  file: string = loopControlPath(),
): { ok: boolean; message: string } {
  const c = String(course ?? '').trim()
  if (!c) return { ok: false, message: '需要课程（暂停是按课程记的）' }
  if (!COURSE_RE.test(c) || c.includes('..')) {
    return { ok: false, message: `课程名非法: ${JSON.stringify(course)}` }
  }
  const cur = readLoopControl(file)
  if (cur.error) {
    // 意图文件坏了：**不覆盖**（可能有人在手改/另一份工具在写）——先让人看明白再动。
    return { ok: false, message: `控制文件有问题，未改动: ${cur.error}（${file}）` }
  }
  const wasPaused = cur.paused.includes(c)
  const next = paused
    ? wasPaused
      ? cur.paused
      : [...cur.paused, c]
    : cur.paused.filter((x) => x !== c)
  if (paused === wasPaused) {
    return { ok: true, message: `${c} 已经是${paused ? '暂停' : '运行'}态（幂等：已重写意图文件）` }
  }
  const err = writeLoopControl(next, file)
  if (err) return { ok: false, message: `写控制意图失败：${err}` }
  return {
    ok: true,
    message: paused
      ? `${c} 已请求暂停：训练进程下一拍生效（队列与账本保留，恢复后从原处接着跑）`
      : `${c} 已请求恢复：训练进程下一拍接着跑`,
  }
}
