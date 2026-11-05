/**
 * hub-sync.ts — 页面加载时把 `CFG['hub_url']` 对齐到 dashboard 当前的 cloudflared 隧道。
 *
 * 触发面（boot）：Kaggle 帧、Colab、AI Studio 的 single 页；host/外层页拿不到 cell。
 *
 * 纪律：
 *   · 只改 `hub_url` 的**值**（注释/缩进/其它键逐字不动）；值相同 ⇒ 一次写都不发；
 *   · 拿不到 URL / 找不到 CFG cell / 编辑器拒绝写入 ⇒ 带原因失败，绝不假装成功；
 *   · 页首 notebook 可能还没挂好：有界轮询（默认 20s），到点就报「等不到」。
 */
import type { Runtime } from '../../runtime'
import type { CellRef, CmdResult, FrameAdapter } from '../sites/types'
import { isHubUrl, normalizeHubUrl, planHubUrlEdit } from '../sites/hub-url'

export const HUB_SYNC_WAIT_MS = 20_000
const HUB_SYNC_POLL_MS = 750
const HUB_SYNC_TIMEOUT_MS = 5000
/** CFG 形状：`CFG = {` / `CFG={` / `CFG: {`（Kaggle 的 sourceText 会把多行拼成一行，故不锚行首）。 */
const CFG_SHAPE = /CFG\s*[=:]\s*\{/
/** 字面量键（与 planHubUrlEdit 同一口径；只用来找候选 cell）。 */
const HUB_KEY = /(["'])hub_url\1\s*:/

export interface HubSyncDeps {
  runtime: Pick<Runtime, 'gmRequest'>
  dashboardBase: string
  adapter: FrameAdapter
  /** 面板「CFG」用的同一份标记（CFG 形状之外的第二判据）。 */
  markers: string[]
  log?: (msg: string) => void
  /** 测试注入：时钟 / 睡眠 / 等待上界。 */
  now?: () => number
  sleep?: (ms: number) => Promise<void>
  waitMs?: number
  pollMs?: number
}

export type HubFetch = { ok: true; url: string } | { ok: false; reason: string; retry?: boolean }

export type LocatedCell =
  { ok: true; cell: CellRef } | { ok: false; reason: string; retry: boolean }

/**
 * 找承载 `hub_url` 的代码 cell：优先「有 CFG 形状/命中配置标记」的那段；
 * 没有形状但全页只有一处 ⇒ 认它；多处且都不像 CFG ⇒ 不猜（fail closed）。
 */
export function locateHubUrlCell(adapter: FrameAdapter, markers: string[] = []): LocatedCell {
  if (!adapter.ready) return { ok: false, reason: '笔记本未就绪', retry: true }
  const strong: CellRef[] = []
  const weak: CellRef[] = []
  for (const cell of adapter.cells()) {
    if (cell.kind !== 'code') continue
    const source = adapter.sourceText(cell)
    if (!HUB_KEY.test(source)) continue
    const looksCfg =
      CFG_SHAPE.test(source) ||
      markers.some((m) => typeof m === 'string' && m.length > 0 && source.includes(m))
    ;(looksCfg ? strong : weak).push(cell)
  }
  if (strong.length > 0) return { ok: true, cell: strong[0]! }
  if (weak.length === 1) return { ok: true, cell: weak[0]! }
  if (weak.length > 1) {
    return {
      ok: false,
      reason: `有 ${weak.length} 段代码含 hub_url，但没有一段像 CFG（不猜）`,
      retry: false,
    }
  }
  return { ok: false, reason: '还没找到含 hub_url 的 CFG cell', retry: true }
}

/** 从 dashboard 读当前隧道 URL（空/非法/HTTP 失败都带原因；可重试的标 `retry`）。 */
export async function fetchHubUrl(
  deps: Pick<HubSyncDeps, 'runtime' | 'dashboardBase'>,
): Promise<HubFetch> {
  let res: { status: number; responseText: string }
  try {
    res = await deps.runtime.gmRequest({
      method: 'GET',
      url: `${deps.dashboardBase}/api/notebookHub`,
      headers: { 'X-Notebook-Companion': '1' },
      timeout: HUB_SYNC_TIMEOUT_MS,
    })
  } catch (e) {
    return {
      ok: false,
      retry: true,
      reason: `取 hub URL 失败（dashboard 未起？）：${e instanceof Error ? e.message : String(e)}`,
    }
  }
  if (res.status !== 200) {
    return { ok: false, retry: true, reason: `/api/notebookHub HTTP ${res.status}` }
  }
  let body: { ok?: unknown; hubUrl?: unknown }
  try {
    body = JSON.parse(res.responseText) as { ok?: unknown; hubUrl?: unknown }
  } catch {
    return { ok: false, reason: '/api/notebookHub 返回的不是 JSON' }
  }
  if (!body || typeof body !== 'object') return { ok: false, reason: '/api/notebookHub 载荷无效' }
  if (body.ok === false) return { ok: false, reason: 'dashboard 拒绝读取 hub URL' }
  const raw = typeof body.hubUrl === 'string' ? body.hubUrl.trim() : ''
  if (!raw) {
    return {
      ok: false,
      retry: true,
      reason: 'dashboard 未登记 cloudflared 隧道 URL（先起 hub + 隧道）',
    }
  }
  const url = normalizeHubUrl(raw)
  if (!isHubUrl(url)) return { ok: false, reason: `dashboard 返回的 hub URL 无效：${raw}` }
  return { ok: true, url }
}

export interface HubSyncView {
  text: string
  tone: 'ok' | 'warn' | 'error'
}

/** 面板一句话：成功 → 新 URL；值相同 → 「已是最新」；失败 → 原因（不吞错）。 */
export function describeHubSync(r: { ok: boolean; detail?: string; reason?: string }): HubSyncView {
  if (!r.ok) return { text: `hub_url 未同步：${r.reason ?? '未知原因'}`, tone: 'error' }
  const detail = r.detail ?? ''
  if (detail.startsWith('updated ')) {
    return { text: `hub_url 已更新 → ${detail.slice('updated '.length)}`, tone: 'ok' }
  }
  if (detail.startsWith('unchanged ')) {
    return { text: `hub_url 已是最新（${detail.slice('unchanged '.length)}）`, tone: 'ok' }
  }
  return { text: `hub_url 已同步${detail ? `（${detail}）` : ''}`, tone: 'ok' }
}

export interface HubSyncHandle {
  cancel(): void
  done: Promise<CmdResult>
}

/**
 * 启动一次页面加载同步（幂等）：等 notebook 就绪 + URL 可取 ⇒ 只改 `hub_url` 的值。
 * 返回句柄：`cancel()` 用于页面卸载；`done` 是最终结果（成功/未改/带原因失败）。
 */
export function startHubUrlSync(deps: HubSyncDeps): HubSyncHandle {
  const now = deps.now ?? (() => Date.now())
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const waitMs = deps.waitMs ?? HUB_SYNC_WAIT_MS
  const pollMs = Math.max(50, deps.pollMs ?? HUB_SYNC_POLL_MS)
  const deadline = now() + waitMs
  let cancelled = false

  const done = (async (): Promise<CmdResult> => {
    let reason = '还没开始'
    let retry = true
    while (!cancelled) {
      const located = locateHubUrlCell(deps.adapter, deps.markers)
      if (!located.ok) {
        reason = located.reason
        retry = located.retry
      } else {
        const hub = await fetchHubUrl(deps)
        if (!hub.ok) {
          reason = hub.reason
          retry = hub.retry === true
        } else {
          const source = deps.adapter.sourceText(located.cell)
          const plan = planHubUrlEdit(source, hub.url)
          if (!plan.ok) {
            reason = plan.reason
            retry = false
          } else if (!plan.changed) {
            deps.log?.(`hub_url 已是最新（${hub.url}）`)
            return { ok: true, detail: `unchanged ${hub.url}` }
          } else {
            const r = await deps.adapter.replaceCellSource(located.cell, plan.text)
            if (r.ok) {
              deps.log?.(`hub_url 已更新 → ${hub.url}（原值：${plan.replaced || '空'}）`)
              return { ok: true, detail: `updated ${hub.url}` }
            }
            reason = r.reason
            retry = r.retry === true
          }
        }
      }
      if (!retry || now() >= deadline) break
      await sleep(pollMs)
    }
    const final = cancelled
      ? '页面已卸载（同步取消）'
      : `${reason}${now() >= deadline ? '（等待超时）' : ''}`
    deps.log?.(`hub_url 未同步：${final}`)
    return { ok: false, reason: final }
  })()

  return {
    cancel() {
      cancelled = true
    },
    done,
  }
}
