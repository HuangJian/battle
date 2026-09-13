/** ppo-queue.ts — PPO 队列排队超时检测（无 worker 领取超阈值 → 告警）。 */
import { existsSync, readFileSync, readdirSync, statSync } from 'fs'
import path from 'path'

// ────────────────────────── PPO 队列排队超时（无 worker 领取 >5min → warning） ──────────────────────────

/** 排队超时阈值：job 发布后若无任何 worker 通过 /jobs/next 领取（无 claimed 标记）
 *  超过该时长，控制台提示云端 worker 可能断连。 */
export const PPO_QUEUE_STALL_MS = 5 * 60_000

export interface PpoQueueStall {
  jobId: string
  waitedSec: number
  it: number | null
}

const PAYLOAD_FILES = ['payload.tar.xz', 'payload.zip']

/** 从 training_log.jsonl 读「仍开放」的 job：job_pending 且无 job_completed / job_cancelled。
 *  悬空/已作废 job（§381 cancel_stale、hub 故障遗留）目录仍在盘上，但不应再告警。 */
function loadOpenPpoJobs(logPath: string): Map<string, number | null> | null {
  if (!existsSync(logPath)) return null
  const pending = new Map<string, number | null>()
  const terminal = new Set<string>()
  try {
    for (const line of readFileSync(logPath, 'utf8').split('\n')) {
      if (!line.trim()) continue
      let e: { event?: string; job_id?: string; it?: number }
      try {
        e = JSON.parse(line)
      } catch {
        continue
      }
      const jid = e.job_id
      if (typeof jid !== 'string') continue
      if (e.event === 'job_pending') {
        pending.set(jid, typeof e.it === 'number' ? e.it : null)
      } else if (e.event === 'job_completed' || e.event === 'job_cancelled') {
        terminal.add(jid)
      }
    }
  } catch {
    return null
  }
  for (const jid of terminal) pending.delete(jid)
  return pending
}

/** 扫描 remote-jobs：有 payload、无 result、无 claimed，且目录 mtime 超过阈值。
 *  claimed 由 hub GET /jobs/next 首次下发时 touch——无 worker 轮询则永不出现。
 *  必须同时在账本里仍是 open pending（无 completed/cancelled），否则悬空目录会永久误报。
 *  返回等待最久的一条；全部正常/无队列 → null。 */
export function detectPpoQueueStall(
  jobRoot: string,
  nowMs = Date.now(),
  thresholdMs = PPO_QUEUE_STALL_MS,
): PpoQueueStall | null {
  if (!existsSync(jobRoot)) return null
  // jobRoot = <traj>/remote-jobs → 账本在 <traj>/training_log.jsonl
  const openJobs = loadOpenPpoJobs(path.join(path.dirname(jobRoot), 'training_log.jsonl'))
  let names: string[]
  try {
    names = readdirSync(jobRoot, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
  } catch {
    return null
  }
  let worst: PpoQueueStall | null = null
  for (const name of names) {
    if (name === 'code.zip') continue
    // 账本可读时：仅 open pending 才可能告警（cancelled/completed/未知 → 跳过）
    if (openJobs && !openJobs.has(name)) continue
    const jd = path.join(jobRoot, name)
    try {
      if (existsSync(path.join(jd, 'result'))) continue
      if (existsSync(path.join(jd, 'claimed'))) continue
      const hasPayload = PAYLOAD_FILES.some((f) => existsSync(path.join(jd, f)))
      if (!hasPayload) continue
      const waited = nowMs - statSync(jd).mtimeMs
      if (waited < thresholdMs) continue
      let it: number | null = openJobs?.get(name) ?? null
      if (it == null) {
        try {
          const m = JSON.parse(readFileSync(path.join(jd, 'manifest.json'), 'utf8')) as {
            it?: number
          }
          if (typeof m.it === 'number') it = m.it
        } catch {
          /* manifest 缺失/损坏不阻断告警 */
        }
      }
      const stall: PpoQueueStall = { jobId: name, waitedSec: Math.round(waited / 1000), it }
      if (!worst || stall.waitedSec > worst.waitedSec) worst = stall
    } catch {
      /* 单目录 IO 错误不拖垮快照 */
    }
  }
  return worst
}

/** 完整状态快照（页面轮询的数据源）。慢部件（节点 ping/组件探测/池历史）走快照缓存。
 *  courseOverride 为只读视图课程（?course=，已 sanitize）；空则回退操作员课程。
 *  只读覆盖不写 console-state——LAN 切换查看课程绝不影响训练。 */
