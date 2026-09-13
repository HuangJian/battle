/** api-client.ts — 客户端动作/数据拉取（client-safe：只有 fetch / JSON，零服务端 import）。
 *
 *  动作语义不变（§1 硬约束 3）：POST → 服务端写回 → 客户端拉一次 state。409 busy
 *  互斥转译为人话（GLM-U4）。 */

import type {
  ConsoleStateView,
  EvalBoardView,
  EvalCkptsView,
  EvalGamesView,
  EvalReplayJobView,
  LogPayload,
  PoolView,
} from '../../../ui/view'

export interface ActionResult {
  ok: boolean
  message: string
  detail?: string[]
}

export async function postAction(
  act: string,
  body: Record<string, unknown> = {},
): Promise<ActionResult> {
  try {
    const r = await fetch(`/api/${act}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    let data: Record<string, unknown> = {}
    try {
      data = (await r.json()) as Record<string, unknown>
    } catch {
      /* non-json body */
    }
    let message =
      typeof data.message === 'string' ? data.message : r.ok ? '完成' : `HTTP ${r.status}`
    if (Array.isArray(data.detail) && data.detail.length > 0) {
      message += `\n${(data.detail as string[]).join('\n')}`
    }
    if (r.status === 409 && (!data.message || String(data.message).includes('动作进行中'))) {
      message = '该组件正在执行另一动作（启动/停止/冒烟），请稍候再试'
    }
    return { ok: r.ok && data.ok !== false, message, detail: data.detail as string[] | undefined }
  } catch (e) {
    return { ok: false, message: String(e) }
  }
}

export async function fetchState(course = ''): Promise<ConsoleStateView> {
  const q = course ? `?course=${encodeURIComponent(course)}` : ''
  const r = await fetch(`/api/state${q}`)
  if (!r.ok) throw new Error(`/api/state HTTP ${r.status}`)
  return (await r.json()) as ConsoleStateView
}

export async function fetchPool(fresh = false, course = ''): Promise<PoolView> {
  const params = new URLSearchParams()
  if (fresh) params.set('fresh', '1')
  if (course) params.set('course', course)
  const q = params.toString()
  const r = await fetch(`/api/pool${q ? `?${q}` : ''}`)
  if (!r.ok) throw new Error(`/api/pool HTTP ${r.status}`)
  return (await r.json()) as PoolView
}

export async function fetchLog(
  key: string,
  lines: number | 'all',
  course = '',
): Promise<LogPayload> {
  const params = new URLSearchParams({ lines: String(lines) })
  if (course) params.set('course', course)
  const r = await fetch(`/api/log/${key}?${params.toString()}`)
  if (!r.ok) throw new Error(`/api/log/${key} HTTP ${r.status}`)
  return (await r.json()) as LogPayload
}

export async function fetchEvalBoard(fresh = false, course = ''): Promise<EvalBoardView> {
  const params = new URLSearchParams()
  if (fresh) params.set('fresh', '1')
  if (course) params.set('course', course)
  const q = params.toString()
  const r = await fetch(`/api/evalboard${q ? `?${q}` : ''}`)
  if (!r.ok) throw new Error(`/api/evalboard HTTP ${r.status}`)
  return (await r.json()) as EvalBoardView
}

/** R7：ckpt/iter 发现（?leg= 懒加载单腿明细）。 */
export async function fetchEvalCkpts(course = '', leg = ''): Promise<EvalCkptsView> {
  const params = new URLSearchParams()
  if (course) params.set('course', course)
  if (leg) params.set('leg', leg)
  const q = params.toString()
  const r = await fetch(`/api/evalCkpts${q ? `?${q}` : ''}`)
  if (!r.ok) throw new Error(`/api/evalCkpts HTTP ${r.status}`)
  return (await r.json()) as EvalCkptsView
}

/** 导出 replay：最新 in-loop eval 逐局视图（弹窗打开时拉取）。 */
export async function fetchEvalGames(course = ''): Promise<EvalGamesView> {
  const q = course ? `?course=${encodeURIComponent(course)}` : ''
  const r = await fetch(`/api/evalGames${q}`)
  if (!r.ok) throw new Error(`/api/evalGames HTTP ${r.status}`)
  return (await r.json()) as EvalGamesView
}

/** 导出 replay：任务态轮询（running / manifest / 日志尾）。 */
export async function fetchEvalReplayJob(course = ''): Promise<EvalReplayJobView> {
  const q = course ? `?course=${encodeURIComponent(course)}` : ''
  const r = await fetch(`/api/evalReplayJob${q}`)
  if (!r.ok) throw new Error(`/api/evalReplayJob HTTP ${r.status}`)
  return (await r.json()) as EvalReplayJobView
}

/** 导出 replay：**单局** .replay 拉取（Blob；写入选定目录或触发下载由调用方决定）。 */
export async function fetchEvalReplayFile(course: string, file: string): Promise<Blob> {
  const params = new URLSearchParams({ file })
  if (course) params.set('course', course)
  const r = await fetch(`/api/evalReplayFile?${params.toString()}`)
  if (!r.ok) {
    let message = `/api/evalReplayFile HTTP ${r.status}`
    try {
      const d = (await r.json()) as { message?: string }
      if (d.message) message = d.message
    } catch {
      /* non-json */
    }
    throw new Error(message)
  }
  return r.blob()
}

/** 逐局写入用户指定目录/下载（File System Access API 结构最小面——
 *  TS DOM lib 未收录 showDirectoryPicker，这里只声明用到的三个方法，零 any）。 */
export interface ReplayDirHandle {
  name: string
  getFileHandle: (
    name: string,
    opts?: { create?: boolean },
  ) => Promise<{
    createWritable: () => Promise<{
      write: (data: Blob) => Promise<void>
      close: () => Promise<void>
    }>
  }>
}

/** 目录选择器可用性（Chromium；Firefox/Safari 无 → 退化为逐文件浏览器下载）。 */
export function canPickDirectory(): boolean {
  return typeof window !== 'undefined' && 'showDirectoryPicker' in window
}

/** 弹目录选择器；用户取消/拒绝 → null。 */
export async function pickReplayDirectory(): Promise<ReplayDirHandle | null> {
  const fn = (
    window as unknown as {
      showDirectoryPicker?: (opts?: { mode?: string }) => Promise<ReplayDirHandle>
    }
  ).showDirectoryPicker
  if (!fn) return null
  try {
    return await fn({ mode: 'readwrite' })
  } catch {
    return null
  }
}
