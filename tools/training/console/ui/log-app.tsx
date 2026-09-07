/** log-app.tsx — 组件日志页（P2）：/log/<key> SSR + hydrate。
 *  智能 follow（§4.9，U1）：贴底才跟随、上滚自动暂停（按钮「回到底部」快捷恢复）、
 *  滚回底部自动恢复；显式开关关 = 永不自动滚动（4s）；URL ?lines / ?follow 只做首帧初始值（GLM-E1）。 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { shouldFollow, type LogPageOptions, type LogPayload } from '../../ui/view'
import { usePolling } from './lib/usePolling'
import { fetchLog } from './lib/api-client'

export interface LogAppProps {
  initial: LogPayload
  options: LogPageOptions
}

const LINES_OPTIONS = [100, 200, 500, 1000]

export function LogApp({ initial, options }: LogAppProps) {
  const [payload, setPayload] = useState<LogPayload>(initial)
  const [follow, setFollow] = useState<boolean>(options.follow)
  const [lines, setLines] = useState<number>(options.lines)
  const [pinned, setPinned] = useState<boolean>(true)
  const boxRef = useRef<HTMLPreElement>(null)
  const followRef = useRef(follow)
  const pinnedRef = useRef(true)
  followRef.current = follow

  const scrollToBottom = (): void => {
    const b = boxRef.current
    if (b) b.scrollTop = b.scrollHeight
  }

  const onScroll = (): void => {
    const b = boxRef.current
    if (!b) return
    const next = shouldFollow(b.scrollTop, b.clientHeight, b.scrollHeight)
    pinnedRef.current = next
    setPinned(next)
  }

  const refetch = useCallback(async (): Promise<void> => {
    const p = await fetchLog(initial.component, lines)
    setPayload(p)
    // 智能 follow：贴底时刷新后自动滚到底；已上滚不拉回。
    if (followRef.current && pinnedRef.current) scrollToBottom()
  }, [initial.component, lines])

  usePolling({ enabled: true, intervalSec: follow ? 2 : 4, fetch: refetch })

  // follow 打开 → 立即贴底（显式开关语义）
  useEffect(() => {
    if (follow) {
      pinnedRef.current = true
      setPinned(true)
      scrollToBottom()
    }
  }, [follow])

  // 尾行数变化 → 立即拉取（URL 只做首帧初始值，不回写）
  useEffect(() => {
    void refetch()
  }, [lines, refetch])

  const meta = payload.log
    ? `${payload.log} · ${payload.exists ? `${payload.fileSize} bytes${payload.truncated ? ' · 已截断（仅显示尾部）' : ''}` : '文件不存在'}`
    : ''

  return (
    <div>
      <div
        className="tc-topbar"
        style={{ borderBottom: 'none', marginBottom: 0, paddingBottom: 0 }}
      >
        <h1>
          <span className="dot" style={{ background: payload.exists ? undefined : 'var(--red)' }} />
          组件日志 — {payload.label}
        </h1>
        <div className="tc-topbar__right">
          <span className="tc-topbar__ts" id="meta">
            {meta}
          </span>
          <a className="tc-btn tc-btn--sm" href="/">
            ← 返回控制台
          </a>
        </div>
      </div>
      <div className="tc-toolbar" style={{ paddingLeft: 0, gap: 12 }}>
        {options.components.map((c) => (
          <a
            key={c.key}
            className={`tc-preset${c.key === payload.component ? ' tc-preset--on' : ''}`}
            href={`/log/${c.key}`}
          >
            {c.label}
          </a>
        ))}
        <label className="tc-toggle">
          <input
            type="checkbox"
            id="follow"
            checked={follow}
            onChange={(e) => setFollow((e.target as HTMLInputElement).checked)}
          />
          <span>跟随滚动（自动刷新{follow ? ' 2s' : ''}）</span>
        </label>
        <label className="tc-toggle">
          尾行数
          <select
            id="lines"
            className="tc-sel"
            value={lines}
            onChange={(e) => setLines(Number((e.target as HTMLSelectElement).value))}
          >
            {LINES_OPTIONS.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        {!pinned ? <span className="tc-muted tc-small">已暂停跟随（上滚读历史）</span> : null}
      </div>
      <pre
        id="logbox"
        ref={boxRef}
        className="tc-logbox"
        onScroll={onScroll}
        aria-label={`${payload.label} 日志内容`}
      >
        {payload.exists
          ? payload.lines.join('\n') || <span className="tc-muted">（空）</span>
          : '日志文件不存在——组件可能从未启动。'}
      </pre>
      {!pinned && follow ? (
        <button
          type="button"
          className="tc-log-toggle"
          aria-label="回到底部并恢复跟随"
          onClick={scrollToBottom}
        >
          ↓ 回到底部
        </button>
      ) : null}
    </div>
  )
}
