/** log-app.tsx — 组件日志页（/log/<key> SSR + hydrate；§367 视觉重构）。
 *  设计（直观/审美/交互）：
 *  - 顶卡：组件状态点 + 标题 + 文件元信息（路径/体积/截断徽章）+ 返回。
 *  - 吸顶工具栏：组件导航 pill（带状态点）+ follow 开关 + 尾行数 + 搜索框（/ 聚焦、Esc 清空）
 *    + 级别过滤（全部/错误/警告，带计数）。
 *  - 日志体：终端风面板 + 行号 gutter + 时间戳列 + [组件] 徽章；trainingLoop 的 JSON 行
 *    渲染为结构化事件卡（event 徽章 + 精选 k:v，iter_error 红）；错误/警告行按级别着色；
 *    搜索命中 <mark> 高亮。
 *  - FAB「回到底部」带新到行数徽章。
 *  智能 follow（§4.9，U1）：贴底才跟随、上滚自动暂停、滚回底部自动恢复；显式开关关 = 永不自动滚动（4s）。
 *  URL ?lines / ?follow 只做首帧初始值（GLM-E1）。 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import {
  formatBytes,
  parseLogLine,
  parseTrainingEvent,
  shouldFollow,
  type LogPageOptions,
  type LogPayload,
  type TrainingLogEvent,
} from '../../ui/view'
import { usePolling } from './lib/usePolling'
import { fetchLog } from './lib/api-client'
import { SegmentedControl } from '../../ui/components/SegmentedControl'

export interface LogAppProps {
  initial: LogPayload
  options: LogPageOptions
}

const LINES_OPTIONS = [100, 200, 500, 1000]
type LevelFilter = 'all' | 'error' | 'warn'

/** 状态点颜色：running 绿（pulse）/ exited 红 / stopped 灰。 */
function statusDot(status: string): string {
  if (status === 'running') return 'tc-dot tc-dot--on'
  if (status === 'exited') return 'tc-dot tc-dot--dead'
  return 'tc-dot tc-dot--empty'
}

/** 搜索命中高亮（文本经 preact 转义，<mark> 包裹命中段）。 */
function Highlight({ text, query }: { text: string; query: string }): JSX.Element {
  if (!query) return <>{text}</>
  const q = query.toLowerCase()
  const lower = text.toLowerCase()
  const parts: Array<string | JSX.Element> = []
  let i = 0
  for (;;) {
    const j = lower.indexOf(q, i)
    if (j < 0) {
      parts.push(text.slice(i))
      break
    }
    if (j > i) parts.push(text.slice(i, j))
    parts.push(<mark key={j}>{text.slice(j, j + q.length)}</mark>)
    i = j + q.length
  }
  return <>{parts}</>
}

/** 结构化训练事件行（trainingLoop JSON）。 */
function TrainingRow({
  idx,
  ev,
  hasError,
}: {
  idx: number
  ev: TrainingLogEvent
  hasError?: string
}): JSX.Element {
  return (
    <div className={`tc-logline tc-logline--ev tc-logline--ev-${ev.event}`}>
      <span className="tc-logline__no">{idx}</span>
      <span className="tc-logline__body">
        <span className="tc-logchip">{ev.event}</span>
        {ev.fields.map(([k, v]) => (
          <span key={k} className="tc-logkv">
            <span className="tc-logkv__k">{k}</span>
            <span className="tc-logkv__v">{v}</span>
          </span>
        ))}
        {hasError ? <span className="tc-logkv__err">✗ {hasError}</span> : null}
      </span>
    </div>
  )
}

interface ParsedEntry {
  i: number
  line: string
  ev: TrainingLogEvent | null
  pl: ReturnType<typeof parseLogLine>
  /** ev 存在时级别取自 event 名（iter_error → error）。 */
  level: 'error' | 'warn' | 'info'
}

export function LogApp({ initial, options }: LogAppProps) {
  const [payload, setPayload] = useState<LogPayload>(initial)
  const [follow, setFollow] = useState<boolean>(options.follow)
  const [lines, setLines] = useState<number>(options.lines)
  const [pinned, setPinned] = useState<boolean>(true)
  const [query, setQuery] = useState('')
  const [level, setLevel] = useState<LevelFilter>('all')
  const [arrivals, setArrivals] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const followRef = useRef(follow)
  const pinnedRef = useRef(true)
  const linesLenRef = useRef(payload.lines.length)
  followRef.current = follow
  pinnedRef.current = pinned

  const scrollToBottom = (): void => {
    const b = boxRef.current
    if (b) b.scrollTop = b.scrollHeight
    setArrivals(0)
  }

  const onScroll = (): void => {
    const b = boxRef.current
    if (!b) return
    const next = shouldFollow(b.scrollTop, b.clientHeight, b.scrollHeight)
    pinnedRef.current = next
    setPinned(next)
    if (next) setArrivals(0)
  }

  const refetch = useCallback(async (): Promise<void> => {
    const p = await fetchLog(initial.component, lines)
    // 未贴底（读历史）时统计新到行数，悬浮纽出 +N 徽章；贴底直接滚到底。
    const grew = p.lines.length - linesLenRef.current
    setPayload(p)
    if (followRef.current && pinnedRef.current) {
      linesLenRef.current = p.lines.length
      scrollToBottom()
    } else if (grew > 0) {
      linesLenRef.current = p.lines.length
      setArrivals((a) => a + grew)
    } else {
      linesLenRef.current = p.lines.length
    }
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
    linesLenRef.current = -1 // 尾行数变了，到来日计数基准重置
    void refetch()
    // eslint 无需：refetch 变化即重新拉取
  }, [lines, refetch])

  // 键盘：/ 聚焦搜索（输入类已聚焦时除外）；Esc 清空搜索
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      const typing =
        t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')
      if (typing) return
      if (e.key === '/') {
        e.preventDefault()
        searchRef.current?.focus()
      } else if (e.key === 'Escape') {
        setQuery('')
        searchRef.current?.blur()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 逐行预解析（payload 变化才重算）+ 级别过滤 + 搜索过滤。
  const entries = useMemo<ParsedEntry[]>(
    () =>
      payload.lines.map((line, i) => {
        const ev = parseTrainingEvent(line)
        const pl = parseLogLine(line)
        const lv: ParsedEntry['level'] = ev
          ? ev.event.includes('error')
            ? 'error'
            : 'info'
          : pl.level
        return { i, line, ev, pl, level: lv }
      }),
    [payload],
  )

  const counts = useMemo(() => {
    let e = 0
    let w = 0
    for (const r of entries) {
      if (r.level === 'error') e++
      else if (r.level === 'warn') w++
    }
    return { error: e, warn: w, total: entries.length }
  }, [entries])

  const shown = useMemo(() => {
    const q = query.toLowerCase()
    return entries.filter(
      (r) => (level === 'all' || r.level === level) && (!q || r.line.toLowerCase().includes(q)),
    )
  }, [entries, level, query])

  const exists = payload.exists
  const meta = payload.log ?? ''
  const label = payload.label

  return (
    <div className="tc-wrap tc-logwrap">
      {/* ── 顶卡：标题 + 文件元信息 + 返回 ── */}
      <header className="tc-loghead">
        <span
          className={`tc-loghead__dot${exists ? ' tc-loghead__dot--live' : ''}`}
          aria-hidden="true"
        />
        <h1>
          组件日志 <span className="tc-loghead__sep">/</span>
          <span className="tc-loghead__comp">{label}</span>
        </h1>
        <div className="tc-loghead__meta">
          {meta ? (
            <code className="tc-logpath" title={meta}>
              {meta}
            </code>
          ) : null}
          {exists ? (
            <span className="tc-chip">
              共 <b>{counts.total}</b> 行
            </span>
          ) : null}
          {exists ? (
            <span className="tc-chip tc-chip--muted">{formatBytes(payload.fileSize)}</span>
          ) : null}
          {payload.truncated ? (
            <span className="tc-chip tc-chip--amber" title={`仅显示尾部 ${lines} 行`}>
              已截断·尾部 {lines} 行
            </span>
          ) : null}
        </div>
        <a className="tc-btn tc-btn--sm tc-loghead__back" href="/">
          ← 返回控制台
        </a>
      </header>

      {/* ── 吸顶工具栏：导航 + follow + 尾行数 + 搜索 + 级别 ── */}
      <div className="tc-logtool">
        <nav className="tc-logtool__nav" aria-label="组件">
          {options.components.map((c) => (
            <a
              key={c.key}
              className={`tc-lognav${c.key === payload.component ? ' tc-lognav--on' : ''}`}
              href={`/log/${c.key}`}
            >
              <span className={statusDot(c.status)} />
              {c.label}
            </a>
          ))}
        </nav>
        <div className="tc-logtool__right">
          <span className="tc-logtool__search">
            <span className="tc-logtool__search-icon" aria-hidden="true">
              ⌕
            </span>
            <input
              ref={searchRef}
              id="tclog-search"
              type="text"
              placeholder="过滤日志…"
              aria-label="过滤日志（/ 聚焦，Esc 清空）"
              value={query}
              onInput={(e) => setQuery((e.target as HTMLInputElement).value)}
            />
            {query ? (
              <button
                type="button"
                className="tc-logtool__clear"
                aria-label="清空搜索"
                onClick={() => setQuery('')}
              >
                ✕
              </button>
            ) : null}
          </span>
          <SegmentedControl<LevelFilter>
            value={level}
            ariaLabel="级别过滤"
            options={[
              { value: 'all', label: `全部 ${counts.total}` },
              { value: 'error', label: `错误 ${counts.error}` },
              { value: 'warn', label: `警告 ${counts.warn}` },
            ]}
            onChange={setLevel}
          />
          <label className="tc-toggle tc-toggle--sm">
            <input
              type="checkbox"
              id="follow"
              checked={follow}
              onChange={(e) => setFollow((e.target as HTMLInputElement).checked)}
            />
            <span>{follow ? '跟随中 2s' : '自动刷新 4s'}</span>
          </label>
          <label className="tc-logtool__lines">
            尾行
            <select
              id="lines"
              className="tc-sel"
              value={lines}
              onChange={(e) => setLines(Number((e.target as HTMLInputElement).value))}
            >
              {LINES_OPTIONS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          {!exists ? <span className="tc-chip tc-chip--red">文件不存在</span> : null}
        </div>
      </div>

      {!pinned && follow ? (
        <div className="tc-logpause" role="status">
          已暂停跟随 · 正在读历史
        </div>
      ) : null}

      {/* ── 日志体 ── */}
      <div className="tc-logpanel">
        <div className="tc-logpanel__hd">
          <span className="tc-logpanel__dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="tc-logpanel__file">{meta || '（无日志文件）'}</span>
          {exists ? (
            <span className="tc-logpanel__live" aria-hidden="true">
              <i />
              {follow ? 'LIVE' : `refresh ${follow ? '2s' : '4s'}`}
            </span>
          ) : null}
          <span className="tc-logpanel__count">
            {query || level !== 'all'
              ? `${shown.length} / ${counts.total} 行`
              : `${counts.total} 行`}
          </span>
        </div>
        <div
          id="logbox"
          ref={boxRef}
          className="tc-logbox"
          onScroll={onScroll}
          aria-label={`${label} 日志内容`}
          tabIndex={0}
        >
          {!exists ? (
            <div className="tc-logempty">
              <span className="tc-logempty__icon">🗂</span>
              <p>
                日志文件不存在——组件可能从未启动。
                <br />
                <span className="tc-muted">启动对应组件后日志会自动出现。</span>
              </p>
            </div>
          ) : shown.length === 0 ? (
            <div className="tc-logempty">
              <span className="tc-logempty__icon">🔍</span>
              <p>
                无匹配行
                <br />
                <span className="tc-muted">换个关键词或放宽级别过滤。</span>
              </p>
            </div>
          ) : (
            shown.map((r) =>
              r.ev ? (
                <TrainingRow key={r.i} idx={r.i + 1} ev={r.ev} hasError={r.ev.hasError} />
              ) : (
                <LogRow key={r.i} idx={r.i + 1} pl={r.pl} text={r.line} query={query} />
              ),
            )
          )}
        </div>
      </div>

      {/* ── FAB：回到底部（带新到行数徽章） ── */}
      {!pinned && follow ? (
        <button
          type="button"
          className="tc-logfab"
          aria-label="回到底部并恢复跟随"
          onClick={scrollToBottom}
        >
          <span className="tc-logfab__icon">↓</span>
          底部
          {arrivals > 0 ? <span className="tc-logfab__badge">+{arrivals}</span> : null}
        </button>
      ) : null}
    </div>
  )
}

/** 普通文本行：gutter + 时间戳列 + [组件] 徽章 + 正文（搜索命中高亮）。 */
function LogRow({
  idx,
  pl,
  text,
  query,
}: {
  idx: number
  pl: ReturnType<typeof parseLogLine>
  text: string
  query: string
}): JSX.Element {
  return (
    <div className={`tc-logline tc-logline--${pl.level}`}>
      <span className="tc-logline__no">{idx}</span>
      {pl.ts ? (
        <span className="tc-logline__ts">{pl.ts}</span>
      ) : (
        <span className="tc-logline__ts" />
      )}
      {pl.tag ? <span className="tc-logchip tc-logchip--tag">{pl.tag}</span> : null}
      <span className="tc-logline__text">
        <Highlight text={text} query={query} />
      </span>
    </div>
  )
}
