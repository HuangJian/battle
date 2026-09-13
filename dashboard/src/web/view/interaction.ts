/** interaction.ts — UI 交互纯函数：动作 pending 锁 / 节点池状态 / 日志 follow / 轮询节奏。 */
import type { CloudHaltView } from './console-types'

// ────────────────────────── 纯函数：云端停机横幅（2026-09-14：只弹当前课） ──────────────────────────

/** 首页横幅可见的停机记录：**只取当前视图课程**那条；本课无记录时回退旧无课键 ''。
 *
 *  为什么必须过滤：`cloudHalts` 是全量表，而「停机条件消失（恢复训练）自动解除」
 *  只在本课程的 TrainingLoop 重启时触发（actions.startComponent → markCloudHaltRecovered）。
 *  2026-09-14 事故：切到 bc-c4-v3 后，c6-chip 的 halted 红横幅仍霸屏，且在本课**永远
 *  解不掉**（自动解除不会发生，只剩「立即恢复」这一条只能作用于那门课的出口）。
 *  其它课程的停机状态由多课总览徽标承载（CourseOverview.cloudHalt），不占首页横幅。 */
export function visibleCloudHalts(
  halts: Readonly<Record<string, CloudHaltView>> | undefined,
  course: string,
): Array<[string, CloudHaltView]> {
  if (!halts) return []
  const own = halts[course]
  if (own) return [[course, own]]
  const legacy = halts['']
  return legacy ? [['', legacy]] : []
}

/** 横幅已读键：事件身份（课程 + 触发/恢复时刻）——同一事件只提示一次，新事件重新弹。 */
export function cloudHaltAckKey(kind: 'halted' | 'recovered', course: string, at: string): string {
  return `${kind}|${course}|${at}`
}

/** 解析 localStorage 里的已读集合：新格式是 JSON 数组；旧格式是单个字符串（兼容）。 */
export function parseCloudHaltAcks(raw: string | null): string[] {
  if (!raw) return []
  try {
    const v: unknown = JSON.parse(raw)
    return Array.isArray(v) ? v.map((x) => String(x)) : [raw]
  } catch {
    return [raw]
  }
}

// ────────────────────────── 纯函数：组件动作 pending 锁（§367 UI 交互） ──────────────────────────

/** 组件「启动/停止」按钮 pending 锁的释放判定：点击时把 (key -> 当时 status) 记入
 *  pending；状态已从点击时值切换（如 stopped→running / running→stopped/exited）即视为
 *  动作完成、可解锁。statusOf 查不到该组件（消失）或状态未变（如启动失败仍为 stopped）
 *  不解锁——前者由调用方兜底（失败 flash 后直接释放）。 */
export function pendingLockReleases(
  pending: Record<string, string>,
  statusOf: (key: string) => string | undefined,
): string[] {
  const out: string[] = []
  for (const [key, from] of Object.entries(pending)) {
    const cur = statusOf(key)
    if (cur !== undefined && cur !== from) out.push(key)
  }
  return out
}

// ────────────────────────── 纯函数：节点池状态 ──────────────────────────

export function statusFromRecent(recent: boolean[]): 'healthy' | 'warn' | 'bad' | 'nodata' {
  const n = recent.length
  if (n === 0) return 'nodata'
  const okN = recent.filter(Boolean).length
  if (okN / n >= 0.9) return 'healthy'
  if (okN / n >= 0.7) return 'warn'
  return 'bad'
}

// ────────────────────────── 纯函数：日志智能 follow ──────────────────────────

export const FOLLOW_THRESHOLD = 24

export function shouldFollow(
  scrollTop: number,
  clientHeight: number,
  scrollHeight: number,
  threshold = FOLLOW_THRESHOLD,
): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold
}

// ────────────────────────── 纯函数：dirty / 轮询节奏 ──────────────────────────

export const REFRESH_INTERVALS = [60, 180, 300, 600, 1800] as const
export type RefreshSec = (typeof REFRESH_INTERVALS)[number]

export function isDirty(pendingEdits: ReadonlyMap<string, string>): boolean {
  return pendingEdits.size > 0
}

/** 全局节奏轮转：1m → 3m → 5m → 10m → 30m → 暂停 → 1m。 */
export function nextRefreshInterval(cur: RefreshSec | 'pause'): RefreshSec | 'pause' {
  if (cur === 60) return 180
  if (cur === 180) return 300
  if (cur === 300) return 600
  if (cur === 600) return 1800
  if (cur === 1800) return 'pause'
  return 60
}

export function refreshLabel(cur: RefreshSec | 'pause'): string {
  if (cur === 'pause') return '暂停'
  if (cur < 60) return `${cur}s`
  if (cur % 60 === 0) return `${cur / 60}m`
  return `${Math.floor(cur / 60)}m${cur % 60}s`
}
