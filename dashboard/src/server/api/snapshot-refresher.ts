/** snapshot-refresher.ts — 慢快照读写门面与后台刷新器（请求只读缓存）。 */
import type { RlConfig } from '../../core/types'
import { loadConsoleState } from '../actions'
import { loadConfigSafe } from './config'
import { discoverCourses, effectiveCourse } from './courses'
import {
  SNAPSHOT_REFRESH_MS,
  SNAPSHOT_VIEW_TTL_MS,
  SlowSnapshot,
  computeSlowSnapshot,
  slowSnapshots,
  snapshotInFlight,
} from './snapshot-cache'

export function getSlowSnapshot(cfg: RlConfig, course: string): Promise<SlowSnapshot> {
  const now = Date.now()
  const ent = slowSnapshots.get(course)
  if (ent) ent.lastAccess = now
  if (ent && now - ent.at < SNAPSHOT_REFRESH_MS) return Promise.resolve(ent.snap)
  const inflight = snapshotInFlight.get(course)
  if (inflight) return inflight
  const p = computeSlowSnapshot(cfg, course).then(
    (snap) => {
      slowSnapshots.set(course, { snap, at: Date.now(), lastAccess: Date.now() })
      snapshotInFlight.delete(course)
      return snap
    },
    (e) => {
      snapshotInFlight.delete(course)
      throw e
    },
  )
  snapshotInFlight.set(course, p)
  return p
}

/** 动作后置空缓存（全部课程）：下一次 buildStateView 冷算，动作结果即时上屏
 *  （不主动后台刷新，避免与请求竞争）。 */
export function invalidateSlowSnapshot(): void {
  slowSnapshots.clear()
}

/** 后台刷新器：立即暖一次 + 每 intervalMs 重算（unref，不阻止进程退出）。服务端启动时
 *  调用一次。操作员课程每拍必刷（保持原语义）；近期被查看（SNAPSHOT_VIEW_TTL_MS 内）的
 *  其它课程跟随刷新；超时未看的课程丢弃，不再占用探测预算。 */
export function startSnapshotRefresher(intervalMs: number = SNAPSHOT_REFRESH_MS): void {
  const run = async (): Promise<void> => {
    try {
      const cfg = loadConfigSafe()
      const operatorCourse = effectiveCourse(loadConsoleState(), discoverCourses())
      const now = Date.now()
      const targets = new Set<string>([operatorCourse])
      for (const [c, ent] of slowSnapshots) {
        if (c === operatorCourse) continue
        if (now - ent.lastAccess > SNAPSHOT_VIEW_TTL_MS) {
          slowSnapshots.delete(c)
          continue
        }
        targets.add(c)
      }
      await Promise.all([...targets].map((c) => getSlowSnapshot(cfg, c)))
    } catch {
      /* 下一拍重试 */
    }
  }
  void run()
  const t = setInterval(() => void run(), intervalMs)
  t.unref?.()
}
