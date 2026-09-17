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

/** 从 training_log.jsonl 读「仍开放」的 job。
 *
 * 以**每个 job 的最后一条事件**为准（last-write-wins），而不是「出现过终局即关闭」：
 * 重发同一个 job（同幂等键 → 同 job_id）会再追加一条 job_pending——它又该被盯排队，
 * 哪怕之前已 failed/cancelled（旧口径会把它永久当已关闭，真卡住时不告警）。
 * 悬空/已作废 job（§381 cancel_stale、hub 故障遗留、节点确定性失败）目录仍在盘上，
 * 但最后一条是终局 → 不告警。 */
function loadOpenPpoJobs(logPath: string): Map<string, number | null> | null {
  if (!existsSync(logPath)) return null
  const last = new Map<string, { event: string; it: number | null }>()
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
      if (typeof jid !== 'string' || typeof e.event !== 'string') continue
      last.set(jid, { event: e.event, it: typeof e.it === 'number' ? e.it : null })
    }
  } catch {
    return null
  }
  const open = new Map<string, number | null>()
  for (const [jid, e] of last) {
    if (e.event === 'job_pending') open.set(jid, e.it)
  }
  return open
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
      // 节点确定性失败（`fail.json`，hub_server.store_job_failure）：job 不会再出结果。
      // 账本可读时上面已按 job_failed 排除；无账本（老 run/账本被清）时靠这一条。
      if (existsSync(path.join(jd, 'fail.json'))) continue
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
