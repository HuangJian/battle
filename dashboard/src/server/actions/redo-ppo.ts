/** redo-ppo.ts — ★M3「重发本轮 PPO」的**控制台面**（plan/ppo-job-lifecycle-ownership §4）。
 *
 *  要解决的问题：想让一门课「丢弃当前 PPO job、用当前 code 立刻重派」，此前只有两条路
 *  ——重启整条训练腿（共享 trainer 一重启，别的课跟着损失一轮）或手动 `rm` 掉 job 目录
 *  （2026-10-13 事故里那次误删就是这么来的）。本动作只**写一份意图文件**：
 *
 *      tmp/<课>/redo-ppo.json          （控制台写、训练进程每拍读）
 *      tmp/<课>/redo-ppo.applied.json  （训练进程写、控制台/人读：读到没有 + 何时）
 *
 *  与「暂停/恢复」（`loop-control.ts`）同一套通道纪律，理由同一份：训练进程不提供 HTTP
 *  服务（用户 2026-09-18 定案：不为一条指令再挂一个端口/鉴权/故障域），而 `tmp/` 是同机
 *  共享目录 ⇒ 一份意图文件最省，hub 挂了也能用。
 *
 *  契约（与 python 侧**逐字对齐**，判据唯一实现住 `nn-training/trainer/loop_control.py`）：
 *    · 形状 `{version:1, it:<int>, at:<float 秒>}`；`at` = **去重键**（训练侧按它判断
 *      「同一份意图只执行一次」——没有它，每拍都会再撤一次刚重发的活，变成无限重发）；
 *    · `it <= 0` = 对**当前轮**生效。控制台**不回填行上的 it**：面板里的 `iter` 是
 *      最后一条 iteration 事件（上一轮已经结算），而用户要重发的是**在飞**那一轮的 job
 *      ⇒ 回填一个落后的数字只会让意图永远等不到那一轮（python 侧 `intent.it != it` ⇒
 *      原地等），按钮看起来「点了没反应」；
 *    · 写盘**原子**（tmp + rename）：训练侧每拍都在读，读到半个 JSON 等于读到坏文件
 *      （那边会保守地不动作 = 静默失效，必须从写侧杜绝）；
 *    · 坏输入**一次都不写盘**（课程名形状在写侧挡住）。
 *
 *  与 hub 的关系：本动作**不碰 hub、不碰进程**。撤单/重发全在训练进程侧完成
 *  （写账本 `job_cancelled` → best-effort `/admin/unfreeze` → best-effort
 *  `/admin/cancel-job`（在飞 409 不撤、目录留给回传）→ 下一拍重发同 jid）。
 *  也就是说：点了按钮**不保证云机上那份在算的活被硬杀**——那是 2026-10-03 的用户裁决
 *  （「不管在算的，只管没领的」）。
 */

import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs'
import path from 'path'
import { pidAlive } from '../../core/net'
import { tmpLogsDir } from '../../core/paths'
import { ActionResult, done } from './result'

/** 意图/回执文件名（**必须与 python 侧 `loop_control.REDO_NAME` / `REDO_APPLIED_NAME` 同名**）。 */
export const REDO_NAME = 'redo-ppo.json'
export const REDO_APPLIED_NAME = 'redo-ppo.applied.json'
/** 意图版本号（与 python 侧同一常量语义；未知版本按当前形状解析，不拒）。 */
export const REDO_VERSION = 1

/** 课程名合法字符集（与 `loop-control.ts` / python `loop_control._ALLOWED` 同一约束）。 */
const COURSE_RE = /^[A-Za-z0-9._-]+$/

function courseDir(course: string, root: string): string {
  return path.join(root, course)
}

/** 某课的重发意图路径（`<tmp>/<课>/redo-ppo.json`）。 */
export function redoIntentPath(course: string, root: string = tmpLogsDir()): string {
  return path.join(courseDir(course, root), REDO_NAME)
}

/** 某课的重发回执路径（与意图同目录）。 */
export function redoAppliedPath(course: string, root: string = tmpLogsDir()): string {
  return path.join(courseDir(course, root), REDO_APPLIED_NAME)
}

/** 回执的读面：训练进程**实际消费**了哪一份意图（`at` = 被消费的意图时间戳）。
 *
 *  `alive` 是分辨「残留文件（进程已死）」与「真的在读」的唯一依据——与
 *  `readLoopApplied` 同口径。`at` 与本动作刚写入的 `at` 相等 ⇒ 已被读到；
 *  不等 = 还没轮到它（或没有进程在读，看 `alive`）。**永不抛**。 */
export interface RedoApplied {
  it: number
  at: number
  pid: number
  alive: boolean
  ts: number
}

const APPLIED_EMPTY: RedoApplied = { it: 0, at: 0, pid: 0, alive: false, ts: 0 }

export function readRedoApplied(
  course: string,
  root: string = tmpLogsDir(),
  alive: (pid: number) => boolean = pidAlive,
): RedoApplied {
  try {
    const p = redoAppliedPath(course, root)
    if (!existsSync(p)) return APPLIED_EMPTY
    const o = JSON.parse(readFileSync(p, 'utf8')) as Record<string, unknown>
    const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
    const pid = num(o.pid)
    return { it: num(o.it), at: num(o.at), pid, alive: pid > 0 && alive(pid), ts: num(o.ts) }
  } catch {
    return APPLIED_EMPTY
  }
}

/** 原子写重发意图。返回 `{ err, at }`：`err` 非空 = 没写成（人读原因），此时**没有**原子性问题。
 *
 *  `it <= 0` = 对当前轮生效（控制台缺省用它，见文件头）。`at` 由调用方注入以便单测钉去重键。 */
export function writeRedoIntent(
  course: string,
  it = 0,
  at: number = Date.now() / 1000,
  root: string = tmpLogsDir(),
): { err: string; at: number } {
  const c = String(course ?? '').trim()
  if (!c) return { err: '需要课程（重发是按课程记的）', at: 0 }
  if (!COURSE_RE.test(c) || c.includes('..')) {
    return { err: `课程名非法: ${JSON.stringify(course)}`, at: 0 }
  }
  const dir = courseDir(c, root)
  const file = redoIntentPath(c, root)
  const tmp = path.join(dir, `.${REDO_NAME}.${process.pid}.tmp`)
  const body = `${JSON.stringify({ version: REDO_VERSION, it: Math.trunc(it) || 0, at }, null, 2)}\n`
  try {
    mkdirSync(dir, { recursive: true })
    writeFileSync(tmp, body, 'utf8')
    renameSync(tmp, file) // 同目录 rename：读侧要么读到旧意图、要么读到新意图，永不读到半个
  } catch (e) {
    return { err: e instanceof Error ? e.message : String(e), at: 0 }
  }
  return { err: '', at }
}

/** 「重发本轮 PPO」动作：写意图 + 回执核对（只写意图文件，**不碰进程、不碰 hub**）。
 *
 *  控制台**不回填** `iter`（见文件头）：缺省 `it=0` = 当前轮——用户点的就是「我刚看到的
 *  那一轮在飞的活」。`it>0` 保留给手写/将来的定向用法（python 侧会对不上的轮原地等）。
 *  `now` 可注入（单测钉回执核对的两支：读到 / 还没轮到）。 */
export function redoPpo(course: string, it = 0, now: number = Date.now() / 1000): ActionResult {
  const c = String(course ?? '').trim()
  const before = readRedoApplied(c)
  const { err, at } = writeRedoIntent(c, it, now)
  if (err) return done(false, `写重发意图失败：${err}`, [redoAppliedLine(c, before, 0)])
  const detail = [
    `意图文件：${redoIntentPath(c)}（{version:${REDO_VERSION}, it:${Math.trunc(it) || 0}, at:${at.toFixed(3)}}）`,
    '训练进程每一拍读一次：命中（it 未限定或与本轮一致）⇒ 撤当前 job 的账本记录 + 解冻 + ' +
      'best-effort 让 hub 作废（在飞 409 不撤、目录留给回传）⇒ 本拍用**同一 jid** 重发（幂等键不含时间）。',
    '★ 不硬杀在算的云机（2026-10-03 裁决）：若那份活已被认领，它照旧跑完/回传；本机不再等它。',
    redoAppliedLine(c, before, at),
  ]
  return done(true, `${c} 已请求重发本轮 PPO——训练进程下一拍生效（若它正在跑）`, detail)
}

/** 回执一行的措辞：把「读到没有」直接说穿（这是回执存在的唯一理由）。 */
function redoAppliedLine(course: string, a: RedoApplied, wroteAt: number): string {
  if (a.at <= 0) {
    return (
      `回执：还没有（${redoAppliedPath(course)} 不存在）——训练进程没读到过任何重发意图` +
      '（进程没在跑？或这门课没进调度？）'
    )
  }
  const who = a.alive ? `进程 pid=${a.pid} 存活` : `进程 pid=${a.pid} 已退出（残留回执）`
  const same = wroteAt > 0 && Math.abs(a.at - wroteAt) < 1e-6
  return (
    `回执：it=${a.it} at=${a.at.toFixed(3)}（${who}）——` +
    (wroteAt <= 0
      ? '这是上一次被读到的意图'
      : same
        ? '★ 已读到你刚写的那一份'
        : '还不是你刚写的这一份（下一拍才轮到它）')
  )
}
