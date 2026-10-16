/** errors-app.tsx — 组件错误登记表详情页（`/errors`，plan/component-error-registry §4.6）。
 *
 *  三层（与 `/log` 页同规，独立页各自 bundle）：
 *    · 服务端 SSR 首帧给**列表投影**（`ErrorsPagePayload`）+ hydrate 复用同一份载荷；
 *    · 客户端每 **4s** 拉 `/api/errors`（非 follow 档；见 `log-app.tsx:150` 的 2/4 档口径）；
 *    · `detail`（traceback/上下文）**按 `fp` 懒取**（展开那一行时才问 `/api/errors?fp=`）——
 *      一页几十条 × 4000 字符不该每 4s 全量搬一遍。
 *
 *  ack = **纯本地**（`TC_ALERT_ACKS`，与首页告警坞同一份键空间）：本页提供逐条 ack 与
 *  「全部标记已读」，首页汇总条的 ack 只关它自己（分工写死在 §4.5 —— 两处各说各的就会漂）。
 *  只读视图（LAN）照常渲染与 ack：**只读的边界是动作，不是读面**（F13）。
 */

import { useMemo, useState } from 'preact/hooks'
import {
  ERROR_SEVERITY_RANK,
  type ErrorDetailPayload,
  type ErrorListItem,
  type ErrorsPageOptions,
  type ErrorsPagePayload,
  TC_ALERT_ACKS,
  alertAckKey,
  parseAlertAcks,
} from '../view'
import { readLocal, writeLocal } from './lib/storage'
import { usePolling } from './lib/usePolling'

/** 行的 ack 键（单一实现：`alerts.ts` 的汇总条与这里必须算出同一个键）。 */
function rowAckKey(it: ErrorListItem): string {
  return alertAckKey('component-error', it.fp, String(it.firstAt))
}

function relAgo(now: number, at: number): string {
  const s = Math.max(0, Math.round((now - at) / 1000))
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86_400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86_400)} 天前`
}

/** 来源徽标的人话标签。
 *
 *  ★ 为什么是**键值对数组 + Map** 而不是对象字面量（2026-10-16 实测踩到）：`node` 这个键写进对象
 *  字面量，转译产物里就是 `node: "节点"`，而 `server/build.ts` 的禁词闸 `FORBIDDEN` 里有一条
 *  `/node:/`（防客户端链 `node:fs` 之类的内置模块）⇒ **bundle 直接构建失败**。给键加引号
 *  **没用**：转译器会把合法的标识符键去引号（实测产物仍是 `node:`）。同类事故见
 *  docs/nn/console.md §1443（`Record<…>` 里一个 `node: []` 让三份 bundle 全挂）。 */
const SOURCE_LABEL = new Map<string, string>([
  ['local-log', '本机日志'],
  ['hub-job', 'hub 作业'],
  ['worker-report', '云机上报'],
  ['node', '节点'],
])

export interface ErrorsAppProps {
  initial: ErrorsPagePayload
  options: ErrorsPageOptions
}

export function ErrorsApp({ initial, options }: ErrorsAppProps) {
  const [items, setItems] = useState<ErrorListItem[]>(initial.items)
  const [scannedAt, setScannedAt] = useState(initial.scannedAt)
  const [acks, setAcks] = useState<string[]>(() => parseAlertAcks(readLocal(TC_ALERT_ACKS)))
  const [openFp, setOpenFp] = useState('')
  const [details, setDetails] = useState<Record<string, string>>({})
  const [filter, setFilter] = useState<{ source: string; severity: string }>({
    source: '',
    severity: '',
  })
  const [err, setErr] = useState('')

  const refetch = async (): Promise<void> => {
    const resp = await fetch('/api/errors')
    if (!resp.ok) throw new Error(`/api/errors ${resp.status}`)
    const body = (await resp.json()) as { items?: ErrorListItem[]; cursor?: number }
    setItems(Array.isArray(body.items) ? body.items : [])
    setScannedAt(typeof body.cursor === 'number' ? body.cursor : 0)
    setErr('')
  }
  usePolling({ enabled: true, intervalSec: 4, fetch: refetch, onError: (e) => setErr(String(e)) })

  const persist = (next: string[]): void => {
    setAcks(next)
    writeLocal(TC_ALERT_ACKS, JSON.stringify(next))
  }

  const pending = useMemo(() => items.filter((it) => !acks.includes(rowAckKey(it))), [items, acks])
  const shown = useMemo(
    () =>
      items
        .filter((it) => (filter.source ? it.source === filter.source : true))
        .filter((it) => (filter.severity ? it.severity === filter.severity : true))
        .sort(
          (a, b) =>
            ERROR_SEVERITY_RANK[a.severity] - ERROR_SEVERITY_RANK[b.severity] ||
            b.lastAt - a.lastAt,
        ),
    [items, filter],
  )

  const toggle = async (it: ErrorListItem): Promise<void> => {
    if (openFp === it.fp) {
      setOpenFp('')
      return
    }
    setOpenFp(it.fp)
    if (details[it.fp] !== undefined) return
    try {
      const resp = await fetch(`/api/errors?fp=${encodeURIComponent(it.fp)}`)
      if (!resp.ok) return
      const body = (await resp.json()) as ErrorDetailPayload
      setDetails((prev) => ({ ...prev, [it.fp]: body.detail || '（没有更长的上下文）' }))
    } catch {
      /* 详情取不到 ⇒ 展开区显示占位（不抛） */
    }
  }

  const ackAll = (): void => {
    const next = [...acks]
    for (const it of items) {
      const k = rowAckKey(it)
      if (!next.includes(k)) next.push(k)
    }
    persist(next)
  }

  const now = Date.now()
  const sources = [...new Set(items.map((it) => it.source))]

  return (
    <div class="tc-errors">
      <div class="tc-errors__head">
        <h1 class="tc-errors__title">组件错误</h1>
        <div class="tc-errors__sub">
          {items.length === 0
            ? '登记表是空的 —— 没有组件报错。'
            : `${pending.length} 条未处理 / ${items.length - pending.length} 条已确认`}
          {scannedAt > 0 ? `（最近巡检 ${relAgo(now, scannedAt)}）` : '（巡检还没跑过）'}
          {options.readOnly ? '（只读视图：只影响动作，不影响本页读数）' : ''}
        </div>
        <div class="tc-errors__acts">
          <label class="tc-errors__filter">
            来源
            <select
              value={filter.source}
              onChange={(e) =>
                setFilter((f) => ({ ...f, source: (e.target as HTMLSelectElement).value }))
              }
            >
              <option value="">全部</option>
              {sources.map((s) => (
                <option key={s} value={s}>
                  {SOURCE_LABEL.get(s) ?? s}
                </option>
              ))}
            </select>
          </label>
          <label class="tc-errors__filter">
            严重度
            <select
              value={filter.severity}
              onChange={(e) =>
                setFilter((f) => ({ ...f, severity: (e.target as HTMLSelectElement).value }))
              }
            >
              <option value="">全部</option>
              <option value="err">错误</option>
              <option value="warn">警告</option>
            </select>
          </label>
          <button type="button" class="tc-btn tc-btn--sm" onClick={ackAll}>
            全部标记已读
          </button>
        </div>
        {err ? <div class="tc-errors__err">轮询失败：{err}</div> : null}
      </div>

      {shown.length === 0 ? (
        <div class="tc-errors__empty">没有匹配的行。</div>
      ) : (
        <table class="tc-errors__table">
          <thead>
            <tr>
              <th>严重度</th>
              <th>来源</th>
              <th>组件 / 归属</th>
              <th>次数</th>
              <th>最近</th>
              <th>摘要</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((it) => {
              const acked = acks.includes(rowAckKey(it))
              return (
                <>
                  <tr
                    key={it.fp}
                    class={`tc-errors__row${acked ? ' tc-errors__row--acked' : ''}`}
                    onClick={() => void toggle(it)}
                  >
                    <td className="tc-errors__sev">{it.severity === 'err' ? '错误' : '警告'}</td>
                    <td>{SOURCE_LABEL.get(it.source) ?? it.source}</td>
                    <td>
                      {it.component}
                      {it.scope ? ` · ${it.scope}` : ''}
                    </td>
                    <td>{it.count}×</td>
                    <td title={new Date(it.lastAt).toLocaleString()}>{relAgo(now, it.lastAt)}</td>
                    <td class="tc-errors__msg">{it.message}</td>
                    <td>
                      {acked ? '已确认' : '未处理'}
                      {openFp === it.fp ? ' ▾' : ' ▸'}
                    </td>
                  </tr>
                  {openFp === it.fp ? (
                    <tr key={`${it.fp}-detail`} class="tc-errors__detail-row">
                      <td colSpan={7}>
                        <pre class="tc-errors__detail">{details[it.fp] ?? '（取详情中…）'}</pre>
                        <div class="tc-errors__acts">
                          {it.ref.kind === 'log' ? (
                            <a class="tc-btn tc-btn--sm" href={`/log/${it.ref.target}`}>
                              打开日志页
                            </a>
                          ) : it.ref.kind === 'node' ? (
                            <a class="tc-btn tc-btn--sm" href="/nodes">
                              去节点页
                            </a>
                          ) : (
                            <a class="tc-btn tc-btn--sm" href={`/courses`}>
                              去课程页
                            </a>
                          )}
                          {acked ? (
                            <span class="tc-errors__acked">已确认</span>
                          ) : (
                            <button
                              type="button"
                              class="tc-btn tc-btn--sm tc-btn--primary"
                              onClick={(e) => {
                                e.stopPropagation()
                                persist([...acks, rowAckKey(it)])
                              }}
                            >
                              知道了
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
