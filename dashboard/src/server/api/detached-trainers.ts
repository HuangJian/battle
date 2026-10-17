/** detached-trainers.ts — 「有独立 trainer 在跑」的读面（plan/single-resident-trainer §1.5）。
 *
 *  ## 数据从哪来
 *
 *  **闸自己报**，不扫锁：python 侧准入闸（`worker/trainer_gate.py`）的放行分支
 *  （`--allow-detached-trainer`，运维逃生舱）原子写 `tmp/trainer-detached.json`
 *  （最近 5 条）。为什么不「每拍扫锁」：判断「这个锁的持有人是不是 trainer」要么只看
 *  pid（分不出身份），要么起 `wmic`/`pwsh` 核命令行（~0.5s/次 × N 门课 = 不可接受）。
 *
 *  ## 读面纪律
 *
 *  · **纯读**：不探测、不起进程、不写任何东西（`/api/state` 路径上零副作用）；
 *  · **过滤**：`pidAlive` ∧ `at` 在 24h 内——pid 复用时旧条目会指向一个不相关的进程，
 *    而「独立 trainer 在跑」是一条**会被人拿去停进程**的事实，不许拿旧文件吓人；
 *  · **不可知 ≠ 没事**：文件缺失/坏 JSON ⇒ `null`（坞不出条目），只有「文件在、条目都被
 *    过滤光」才是 `[]`（确实没有）。
 */

import { readFileSync } from 'fs'
import { pidAlive } from '../../core/net'
import { trainerDetachedPath } from '../../core/paths'
import type { DetachedTrainerView } from '../../web/view'

/** 保留窗口：更早的条目即使 pid 还活着也不上报（文件是 append-最多-5 条的环形表）。 */
export const DETACHED_TTL_MS = 24 * 3600 * 1000

export interface DetachedTrainersIO {
  /** 自报文件路径（单测注入；默认走 `core/paths.trainerDetachedPath`）。 */
  path?: string
  /** `at` 的当前时刻（单测注入；默认 `Date.now()`）。 */
  now?: number
  /** 存活探测（单测注入；默认 `core/net.pidAlive`）。 */
  alive?: (pid: number) => boolean
}

/** 读 + 过滤。返回 `null` = 不可知（文件不在/读不动/坏 JSON）；`[]` = 确实没有。 */
export function readDetachedTrainers(io: DetachedTrainersIO = {}): DetachedTrainerView[] | null {
  const path = io.path ?? trainerDetachedPath()
  const now = io.now ?? Date.now()
  const alive = io.alive ?? pidAlive
  let raw: string
  try {
    raw = readFileSync(path, 'utf-8')
  } catch {
    return null // 没写过 = 旧控制台世界（不是「有独立 trainer」）
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null // 半写/坏文件：读面不编事实（写侧是原子写，这是防御）
  }
  if (!Array.isArray(parsed)) return null
  const out: DetachedTrainerView[] = []
  for (const item of parsed) {
    if (typeof item !== 'object' || item === null) continue
    const rec = item as Record<string, unknown>
    const pid = Number(rec.pid)
    const at = Number(rec.at)
    if (!Number.isInteger(pid) || !Number.isFinite(at)) continue
    if (now - at * 1000 > DETACHED_TTL_MS) continue
    if (!alive(pid)) continue
    out.push({
      pid,
      course: String(rec.course ?? ''),
      at,
      entry: String(rec.entry ?? ''),
      argv: String(rec.argv ?? ''),
    })
  }
  return out
}
