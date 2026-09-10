/** api-client.ts — 客户端动作/数据拉取（client-safe：只有 fetch / JSON，零服务端 import）。
 *
 *  动作语义不变（§1 硬约束 3）：POST → 服务端写回 → 客户端拉一次 state。409 busy
 *  互斥转译为人话（GLM-U4）。 */

import type { ConsoleStateView, EvalBoardView, LogPayload, PoolView } from '../../../ui/view'

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
