/**
 * quota-banner.ts — Kaggle 外层页顶部的「本周配额」横幅。
 *
 * 数据来自页面可见的 `Quota:09:20 / 20 hrs`（class 是哈希的，只能按文本认）。
 * 该值在页面加载后才异步渲染（点开 Session options 或等会话起来），所以启动后
 * 用低频轮询 + MutationObserver 兜底，直到抓到数值才显示。
 *
 * 横幅住 Shadow DOM（与 panel 同款），样式不污染 Kaggle；可拖动，位置存 GM。
 */
import type { Runtime } from '../../runtime'
import { clamp } from '../../utils'

export const QUOTA_POS_KEY = 'bnc:quotaBannerPos'
export const QUOTA_HOST_ID = 'bnc-quota-banner-host'
/** 周配额粒度粗，10 分钟刷新一次足够（API 与 DOM 兜底同周期）。 */
export const QUOTA_REFRESH_MS = 10 * 60_000
const STYLE_ID = 'bnc-quota-banner-style'

export type QuotaUsage = {
  usedLabel: string
  totalLabel: string
  remainingLabel: string
  percent: number
  tier: 'ok' | 'warn' | 'danger'
}

const QUOTA_RE = /Quota:\s*(\d+):([0-5]\d)\s*\/\s*(\d+(?:\.\d+)?)\s*(?:hours|hrs|h)\b/i
/** Kaggle 前端取配额的同源接口（点 Session options 时它也会调）。 */
export const QUOTA_API_URL =
  'https://www.kaggle.com/api/i/kernels.KernelsService/GetAcceleratorQuotaStatistics'

function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  return `${hours}:${String(minutes % 60).padStart(2, '0')}`
}

/** 从 API 的秒数字符串（如 "31468.330738800s"）取秒；非法返回 null。 */
function seconds(raw: unknown): number | null {
  if (typeof raw !== 'string') return null
  const n = Number.parseFloat(raw)
  return Number.isFinite(n) ? n : null
}

export type QuotaPools = { gpu: QuotaUsage | null; tpu: QuotaUsage | null }

/** 解析单个配额池。剩余 = total - used - reserved（reserved 是已预留未用的量）。 */
function parsePool(raw: unknown): QuotaUsage | null {
  if (typeof raw !== 'object' || raw === null) return null
  const pool = raw as Record<string, unknown>
  const totalSec = seconds(pool.totalTimeAllowed)
  const usedSec = seconds(pool.timeUsed)
  if (totalSec === null || usedSec === null || totalSec <= 0) return null
  const reservedSec = seconds(pool.timeReserved) ?? 0
  const remainingSec = Math.max(0, totalSec - usedSec - reservedSec)

  const totalHours = totalSec / 3600
  const totalLabel = `${Number.isInteger(totalHours) ? totalHours : totalHours.toFixed(1)} hrs`
  const percent = Math.min(100, Math.round(((usedSec + reservedSec) / totalSec) * 100))
  return {
    usedLabel: formatDuration(Math.floor(usedSec / 60)),
    totalLabel,
    remainingLabel: formatDuration(Math.floor(remainingSec / 60)),
    percent,
    tier: percent >= 80 ? 'danger' : percent >= 50 ? 'warn' : 'ok',
  }
}

/** 解析 GetAcceleratorQuotaStatistics 响应：GPU / TPU 各自一行，互不覆盖。 */
export function parseQuotaApi(json: unknown): QuotaPools | null {
  if (typeof json !== 'object' || json === null) return null
  const root = json as Record<string, unknown>
  const gpu = parsePool(root.gpuQuota)
  const tpu = parsePool(root.tpuQuota)
  if (!gpu && !tpu) return null
  return { gpu, tpu }
}

/** 同源 GET 配额接口；任何失败返回 null（DOM 文本仍可兜底）。 */
export async function fetchQuotaUsage(runtime: Runtime): Promise<QuotaPools | null> {
  try {
    const res = await runtime.gmRequest({
      method: 'GET',
      url: QUOTA_API_URL,
      timeout: 5000,
    })
    if (res.status < 200 || res.status >= 300) return null
    return parseQuotaApi(JSON.parse(res.responseText))
  } catch {
    return null
  }
}

/** 从「Quota:」标签父节点的文本解析；抓不到数值返回 null（绝不抛）。 */
export function parseQuota(text: string): QuotaUsage | null {
  const match = QUOTA_RE.exec(text.replace(/\s+/g, ' '))
  if (!match) return null
  const totalHours = Number(match[3])
  if (!Number.isFinite(totalHours) || totalHours <= 0) return null
  const usedMinutes = Number(match[1]) * 60 + Number(match[2])
  const totalMinutes = Math.round(totalHours * 60)
  const percent = Math.min(100, Math.round((usedMinutes / totalMinutes) * 100))
  const remaining = Math.max(0, totalMinutes - usedMinutes)
  return {
    usedLabel: `${match[1]}:${match[2]}`,
    totalLabel: `${match[3]} hrs`,
    remainingLabel: formatDuration(remaining),
    percent,
    tier: percent >= 80 ? 'danger' : percent >= 50 ? 'warn' : 'ok',
  }
}

/** 只读 Quota 标签的父节点，不扫 body 全文（里面混着导航数字，会误匹配）。 */
export function findQuotaText(doc: Document): string {
  for (const el of doc.querySelectorAll('span, div, p')) {
    if (el.children.length === 0 && (el.textContent || '').trim() === 'Quota:') {
      return el.parentElement?.textContent ?? ''
    }
  }
  return ''
}

export function parseQuotaPosition(raw: unknown): { left: number; top: number } | null {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null
  const { left, top } = raw as Record<string, unknown>
  if (typeof left !== 'number' || typeof top !== 'number') return null
  if (!Number.isFinite(left) || !Number.isFinite(top)) return null
  return { left, top }
}

const CSS = `
:host { all: initial; }
.bq {
  position: fixed; z-index: 2147483647; display: flex; flex-direction: column; gap: 2px;
  padding: 5px 10px; border-radius: 6px; border: 1px solid rgba(0,0,0,.12);
  background: #fff; box-shadow: 0 3px 12px rgba(0,0,0,.16); color: #1f2937;
  font: 600 12px/1.3 system-ui, -apple-system, sans-serif; font-variant-numeric: tabular-nums;
  white-space: nowrap; cursor: move; user-select: none; touch-action: none;
}
.bq[data-dragging='1'] { opacity: .85; box-shadow: 0 8px 24px rgba(0,0,0,.28); }
.row .ok { color: #15803d; } .row .warn { color: #b45309; } .row .danger { color: #b91c1c; }
`

const DRAG_MARGIN = 6

function clampToViewport(
  el: HTMLElement,
  view: Window | null,
  left: number,
  top: number,
): { left: number; top: number } {
  const vw = view?.innerWidth ?? 0
  const vh = view?.innerHeight ?? 0
  if (vw <= 0 || vh <= 0) return { left, top }
  return {
    left: clamp(left, DRAG_MARGIN, Math.max(DRAG_MARGIN, vw - el.offsetWidth - DRAG_MARGIN)),
    top: clamp(top, DRAG_MARGIN, Math.max(DRAG_MARGIN, vh - el.offsetHeight - DRAG_MARGIN)),
  }
}

export interface QuotaBanner {
  /** 主动同步一次（轮询也会调）；返回是否上屏/更新。 */
  sync(): boolean
  destroy(): void
}

/**
 * 挂载横幅。位置经 `runtime.getValue`/`setValue` 存 GM（本仓同步 API）。
 * `pollMs=0` 关闭轮询（测试用）。
 */
export function mountQuotaBanner(
  doc: Document,
  runtime: Runtime,
  view: Window | null,
  pollMs = QUOTA_REFRESH_MS,
): QuotaBanner {
  if (doc.getElementById(QUOTA_HOST_ID)) {
    // 幂等：同一页重复挂载先撤掉旧的，避免两个横幅。
    doc.getElementById(QUOTA_HOST_ID)?.remove()
  }

  let pos = parseQuotaPosition(runtime.getValue(QUOTA_POS_KEY, null))
  let rows: Array<{ label: string; usage: QuotaUsage }> = []
  let changed = false

  const host = doc.createElement('div')
  host.id = QUOTA_HOST_ID
  doc.body.append(host)
  const shadow = host.attachShadow({ mode: 'open' })
  shadow.innerHTML = `<style id="${STYLE_ID}"></style><div class="bq" data-quota hidden></div>`
  const styleEl = shadow.getElementById(STYLE_ID) as HTMLStyleElement
  styleEl.textContent = CSS
  const card = shadow.querySelector('.bq') as HTMLElement

  const place = (): void => {
    if (!pos) {
      card.style.left = ''
      card.style.top = '12px'
      card.style.right = '16px'
      return
    }
    const box = clampToViewport(card, view, pos.left, pos.top)
    card.style.left = `${box.left}px`
    card.style.top = `${box.top}px`
    card.style.right = 'auto'
  }

  const render = (): void => {
    if (rows.length === 0) {
      card.hidden = true
      return
    }
    card.className = 'bq'
    card.innerHTML = rows
      .map(
        ({ label, usage }) =>
          `<div class="row"><span class="${usage.tier}">${label} 剩余 ${usage.remainingLabel} / ${usage.totalLabel}</span></div>`,
      )
      .join('')
    card.hidden = false
    place()
  }

  const rowSig = (next: Array<{ label: string; usage: QuotaUsage }>): string =>
    next
      .map((r) => `${r.label}:${r.usage.remainingLabel}/${r.usage.totalLabel}/${r.usage.tier}`)
      .join('|')

  const applyRows = (next: Array<{ label: string; usage: QuotaUsage }>): boolean => {
    if (rowSig(next) === rowSig(rows)) return changed
    rows = next
    changed = true
    render()
    return true
  }

  // DOM 文本兜底：Session options 打开后才渲染出数值，且此时若 API 数据已在，
  // 不让 DOM 的（可能是 TPU / 稍旧的）值覆盖 API 的权威值。
  let hasApi = false
  const sync = (): boolean => {
    if (hasApi) return false
    const usage = parseQuota(findQuotaText(doc))
    return applyRows(usage ? [{ label: '本周配额', usage }] : [])
  }

  // 拖拽（pointer，与 panel 同款）：只认卡片本身，落点夹进视口后存 GM。
  let dragging = false
  let startX = 0
  let startY = 0
  let originLeft = 0
  let originTop = 0
  const onDown = (e: Event): void => {
    const pe = e as PointerEvent
    if (pe.button !== 0) return
    dragging = true
    const styleLeft = Number.parseFloat(card.style.left)
    const styleTop = Number.parseFloat(card.style.top)
    originLeft = Number.isFinite(styleLeft) ? styleLeft : card.offsetLeft
    originTop = Number.isFinite(styleTop) ? styleTop : card.offsetTop
    startX = pe.clientX
    startY = pe.clientY
    card.dataset.dragging = '1'
    if (typeof pe.pointerId === 'number') card.setPointerCapture?.(pe.pointerId)
    pe.preventDefault()
  }
  const onMove = (e: Event): void => {
    if (!dragging) return
    const pe = e as PointerEvent
    const box = clampToViewport(
      card,
      view,
      originLeft + pe.clientX - startX,
      originTop + pe.clientY - startY,
    )
    card.style.left = `${box.left}px`
    card.style.top = `${box.top}px`
    card.style.right = 'auto'
  }
  const onUp = (): void => {
    if (!dragging) return
    dragging = false
    delete card.dataset.dragging
    // 读 style（拖拽自己写的权威值）；offsetLeft 在 happy-dom/无布局时不可靠。
    const left = Number.parseFloat(card.style.left)
    const top = Number.parseFloat(card.style.top)
    if (Number.isFinite(left) && Number.isFinite(top)) {
      pos = { left, top }
      runtime.setValue(QUOTA_POS_KEY, pos)
    }
  }
  card.addEventListener('pointerdown', onDown)
  card.addEventListener('pointermove', onMove)
  card.addEventListener('pointerup', onUp)

  render() // 先放空卡（hidden），位置记忆已恢复
  sync()

  // 权威数据：启动即拉配额 API，不依赖用户点开 Session options。
  // 失败不报错（DOM 文本继续兜底）；成功后按 QUOTA_REFRESH_MS 刷新。
  const refreshApi = async (): Promise<void> => {
    const pools = await fetchQuotaUsage(runtime)
    if (!pools) return
    hasApi = true
    const next: Array<{ label: string; usage: QuotaUsage }> = []
    if (pools.gpu) next.push({ label: 'GPU', usage: pools.gpu })
    if (pools.tpu) next.push({ label: 'TPU', usage: pools.tpu })
    applyRows(next)
  }
  void refreshApi()

  const timers: ReturnType<typeof setInterval>[] = []
  if (pollMs > 0) {
    timers.push(setInterval(sync, pollMs))
    timers.push(setInterval(() => void refreshApi(), QUOTA_REFRESH_MS))
  }

  return {
    sync,
    destroy() {
      for (const t of timers) clearInterval(t)
      card.removeEventListener('pointerdown', onDown)
      card.removeEventListener('pointermove', onMove)
      card.removeEventListener('pointerup', onUp)
      host.remove()
    },
  }
}
