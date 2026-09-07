/** app.tsx — 控制台根组件（SSR + hydrate）。
 *
 *  全局状态只有 5 项（评审 E1 定案，无 Context/signals，两层 props 传递）：
 *  course（随 /api/state）、refreshInterval（全局节奏）、maximizedCard、pendingEdits、connError。
 *  卡片内部状态（排序/过滤/展开/输入/滚动）一律自有 useState，绝不上升。
 *
 *  polling 优先级（GLM-E6）：dirty(L2) > visibility(后台 tab) > 用户暂停 > 卡折叠；
 *  节奏类：单卡覆盖 tc.interval.<card>（仅独立数据源）> 全局节奏。 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { CARD_REGISTRY, visibleCards, type CardDef, type CardSource } from './cards'
import type { CardActionDesc } from './cards'
import { Card } from '../../ui/components/Card'
import { Flash, type FlashState } from '../../ui/components/Flash'
import { PanelErrorBoundary } from '../../ui/components/PanelErrorBoundary'
import { usePolling } from './lib/usePolling'
import { fetchState, postAction } from './lib/api-client'
import {
  fmtTs,
  isDirty,
  nextRefreshInterval,
  TC_CARD_KEY,
  TC_GLOBAL_INTERVAL,
  type ConsoleStateView,
  type RefreshSec,
  type StaleState,
} from '../../ui/view'

export interface AppProps {
  initial: ConsoleStateView
}

function readLocal(key: string): string | null {
  try {
    if (typeof localStorage === 'undefined') return null
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeLocal(key: string, v: string): void {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(key, v)
  } catch {
    /* ignore */
  }
}

function initInterval(): RefreshSec | 'pause' {
  const v = readLocal(TC_GLOBAL_INTERVAL)
  if (v === '3' || v === '5' || v === '30') return Number(v) as RefreshSec
  return 3
}

function initCollapsed(): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const def of CARD_REGISTRY) {
    const v = readLocal(TC_CARD_KEY(def.key))
    out[def.key] = v === null ? (def.defaultCollapsed ?? false) : v === '1'
  }
  return out
}

export function App({ initial }: AppProps) {
  const [stateView, setStateView] = useState<ConsoleStateView | null>(initial)
  const [stateStale, setStateStale] = useState<StaleState>('ok')
  const [connError, setConnError] = useState<'off' | 'retry' | 'down'>('off')
  const [refreshInterval, setRefreshInterval] = useState<RefreshSec | 'pause'>(initInterval)
  const [maximizedCard, setMaximizedCard] = useState<string | null>(null)
  const [pendingEdits, setPendingEdits] = useState<Map<string, string>>(new Map())
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(initCollapsed)
  const [transientExpanded, setTransientExpanded] = useState<Set<string>>(new Set())
  const [flash, setFlash] = useState<FlashState | null>(null)
  const [userPaused, setUserPaused] = useState(false)
  const [documentVisible, setDocumentVisible] = useState(
    typeof document === 'undefined' || !document.hidden,
  )
  const [stopAllArmed, setStopAllArmed] = useState(false)
  const [poolFreshNonce, setPoolFreshNonce] = useState(0)
  const [poolStale, setPoolStale] = useState<StaleState>('refresh')
  const [poolAt, setPoolAt] = useState(0)

  const wasError = useRef(false)
  const failCount = useRef(0)
  const stopTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── 拉取 /api/state（全局节奏；单次失败 retry，连续 3 次 down） ──
  const refreshState = useCallback(async (): Promise<void> => {
    try {
      const s = await fetchState()
      setStateView(s)
      setStateStale('ok')
      setConnError('off')
      failCount.current = 0
      if (wasError.current) setPoolFreshNonce((n) => n + 1) // 连接恢复 → 补拉一次池统计
      wasError.current = false
    } catch {
      wasError.current = true
      failCount.current += 1
      setStateStale('err')
      setConnError(failCount.current >= 3 ? 'down' : 'retry')
    }
  }, [])

  const dirty = isDirty(pendingEdits)
  const globalPollEnabled = refreshInterval !== 'pause' && !dirty && !userPaused && documentVisible
  usePolling({
    enabled: globalPollEnabled,
    intervalSec: refreshInterval === 'pause' ? 3 : refreshInterval,
    fetch: refreshState,
  })

  // 后台 tab：切回前台立即重拉一次
  useEffect(() => {
    const onVis = (): void => {
      const vis = !document.hidden
      setDocumentVisible(vis)
      if (vis) void refreshState()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refreshState])

  // 键盘：Esc 退出最大化；r 立即刷新全部（输入框聚焦时禁用）
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return
      if (e.key === 'Escape') setMaximizedCard(null)
      else if (e.key.toLowerCase() === 'r') {
        void refreshState()
        setPoolFreshNonce((n) => n + 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [refreshState])

  // 最大化时 body 锁滚动
  useEffect(() => {
    document.body.style.overflow = maximizedCard ? 'hidden' : ''
    return () => {
      document.body.style.overflow = ''
    }
  }, [maximizedCard])

  // ── 动作派发（POST → flash → 重拉 state） ──
  const doAction = useCallback(
    async (act: string, body: Record<string, unknown> = {}): Promise<{ ok: boolean }> => {
      const r = await postAction(act, body)
      setFlash({ ok: r.ok, message: r.message })
      void refreshState()
      return { ok: r.ok }
    },
    [refreshState],
  )

  // 卡级动作装配（DS-E2）：source 映射 exhaustive（DS-E5），新增 source 缺实现编译报错
  const refreshBySource = useMemo((): Record<CardSource, () => void> => {
    return {
      state: () => void refreshState(),
      pool: () => setPoolFreshNonce((n) => n + 1),
      log: () => undefined,
    }
  }, [refreshState])

  const handleCardAction = (def: CardDef, desc: CardActionDesc): void => {
    if (desc.key === 'refresh') refreshBySource[def.source]()
    else if (desc.key === 'poolFresh') setPoolFreshNonce((n) => n + 1)
  }

  // ── L2 dirty（唯一控件：节点并发数） ──
  const onPending = (key: string, value: string): void => {
    setPendingEdits((prev) => {
      const n = new Map(prev)
      n.set(key, value)
      return n
    })
  }
  const onDiscardPending = (): void => setPendingEdits(new Map())
  const onCommitConcurrency = async (id: string, value: string): Promise<void> => {
    const num = Number(value)
    if (!Number.isInteger(num) || num < 1 || num > 64) {
      setFlash({ ok: false, message: `并发数需为 1-64 的整数，收到: ${value}` })
      return
    }
    const r = await doAction('setNodeConcurrency', { id, concurrency: num })
    if (r.ok) onDiscardPending()
  }

  // ── 停止全部（两步内联确认，3s 超时还原；DS-U2） ──
  const handleStopAll = (): void => {
    if (!stopAllArmed) {
      setStopAllArmed(true)
      stopTimer.current = setTimeout(() => setStopAllArmed(false), 3000)
      return
    }
    if (stopTimer.current) clearTimeout(stopTimer.current)
    setStopAllArmed(false)
    void doAction('stopAll')
  }

  // ── 折叠 / 锚点临时展开（DS-U4：锚点只临时展开，不写 localStorage） ──
  const toggleCollapsed = (key: string, dflt: boolean): void => {
    setTransientExpanded((prev) => {
      const n = new Set(prev)
      n.delete(key)
      return n
    })
    setCollapsed((prev) => {
      const next = !(prev[key] ?? dflt)
      writeLocal(TC_CARD_KEY(key), next ? '1' : '0')
      return { ...prev, [key]: next }
    })
  }
  const anchorTo = (key: string): void => {
    setTransientExpanded((prev) => new Set(prev).add(key))
    document.getElementById(`card-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // ── 陈旧度（nodes 卡 = 池节奏；其余 = state 节奏；title 用服务端时间戳保 SSR 确定） ──
  const onPoolState = useCallback((st: StaleState, at: number): void => {
    setPoolStale(st)
    if (at > 0) setPoolAt(at)
  }, [])
  const staleFor = (def: CardDef): { state: StaleState; title?: string } => {
    if (def.key === 'nodes')
      return {
        state: poolStale,
        title: poolAt > 0 ? `池更新于 ${fmtTs(poolAt, poolAt)}` : undefined,
      }
    const t = stateView ? new Date(stateView.time).getTime() : 0
    return { state: stateStale, title: t > 0 ? `更新于 ${fmtTs(t, t)}` : undefined }
  }

  const course = stateView?.course ?? ''
  const buses = visibleCards(course)

  const handleInterval = (): void => {
    setRefreshInterval((cur) => {
      const nxt = nextRefreshInterval(cur)
      writeLocal(TC_GLOBAL_INTERVAL, String(nxt))
      return nxt
    })
  }

  return (
    <div className="tc-wrap">
      <Flash flash={flash} onHide={() => setFlash(null)} />

      <header className="tc-topbar">
        <h1>
          <span className="dot" />
          NN 训练控制台
        </h1>
        <nav className="tc-topbar__anchors" aria-label="卡片锚点">
          {buses.map((d) => (
            <button key={d.key} type="button" className="tc-anchor" onClick={() => anchorTo(d.key)}>
              {d.title}
            </button>
          ))}
        </nav>
        <div className="tc-topbar__right">
          <label className="tc-toggle tc-small">
            课程
            <select
              id="courseSel"
              className="tc-sel"
              value={course}
              onChange={(e) =>
                void doAction('setCourse', { course: (e.target as HTMLSelectElement).value })
              }
            >
              <option value="">自动（最近活跃课程）</option>
              {(stateView?.courses ?? []).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            aria-label="立即刷新全部 (r)"
            onClick={() => {
              void refreshState()
              setPoolFreshNonce((n) => n + 1)
            }}
          >
            ⟳
          </button>
          <button
            type="button"
            className={`tc-btn tc-btn--sm${refreshInterval === 'pause' ? ' tc-btn--danger' : ''}`}
            aria-pressed={refreshInterval === 'pause'}
            title="暂停后不再自动轮询 /api/state"
            onClick={handleInterval}
          >
            {refreshInterval === 'pause' ? '▶ 已暂停·UI' : `⏸ 暂停刷新（${refreshInterval}s）`}
          </button>
          <button
            type="button"
            className={`tc-stopall${stopAllArmed ? ' tc-stopall--arm' : ''}`}
            aria-label="停止全部受管进程（危险）"
            onClick={handleStopAll}
          >
            {stopAllArmed ? '确认停止全部？' : '停止全部'}
          </button>
          <span className="tc-topbar__ts">
            {stateView ? `更新于 ${fmtTs(new Date(stateView.time).getTime(), Date.now())}` : ''}
          </span>
        </div>
      </header>

      {userPaused ? (
        <div className="tc-banner tc-banner--dirty">
          <span>▶ 已暂停·UI（已暂停刷新）——点击恢复自动轮询</span>
          <button type="button" className="tc-btn tc-btn--sm" onClick={() => setUserPaused(false)}>
            恢复
          </button>
        </div>
      ) : null}

      {dirty ? (
        <div className="tc-banner tc-banner--dirty">
          <span>已暂停：节点并发数有未保存修改（失焦自动丢弃；保存按钮提交本控件）</span>
          <button type="button" className="tc-btn tc-btn--sm" onClick={onDiscardPending}>
            丢弃修改
          </button>
        </div>
      ) : null}

      {connError !== 'off' ? (
        <div className="tc-banner tc-banner--err" role="alert">
          <span>
            {connError === 'down'
              ? '控制台无响应（服务端可能已退出）——检查 `bun run train` 进程'
              : '刷新失败，正在重试'}
          </span>
          <button type="button" className="tc-btn tc-btn--sm" onClick={() => void refreshState()}>
            重试
          </button>
        </div>
      ) : null}

      {buses.map((def) => {
        const isMax = maximizedCard === def.key
        const colDflt = def.defaultCollapsed ?? false
        const col = collapsed[def.key] ?? colDflt
        const isCol = col && !transientExpanded.has(def.key)
        const P = def.component
        return (
          <Card
            key={def.key}
            id={`card-${def.key}`}
            title={def.title}
            sub={def.key === 'metrics' ? course || undefined : undefined}
            stale={staleFor(def)}
            collapsed={isCol}
            maximized={isMax}
            onToggleCollapsed={() => toggleCollapsed(def.key, colDflt)}
            onToggleMaximized={() => setMaximizedCard(isMax ? null : def.key)}
            actions={def.actions?.map((a) => ({ ...a, run: () => handleCardAction(def, a) }))}
          >
            <PanelErrorBoundary>
              <P
                course={course}
                stateView={stateView}
                active={!isCol && !dirty && !userPaused}
                paused={dirty || userPaused || !documentVisible}
                maximizedCard={maximizedCard}
                onAction={doAction}
                onPoolState={onPoolState}
                poolFreshNonce={poolFreshNonce}
                pendingEdits={pendingEdits}
                onPending={onPending}
                onDiscardPending={onDiscardPending}
                onCommitConcurrency={onCommitConcurrency}
              />
            </PanelErrorBoundary>
          </Card>
        )
      })}

      <p className="tc-caption">
        仅回环 127.0.0.1 无鉴权（DECISIONS §348）· /api/state 3s 轮询（无整页 reload）· 池统计独立
        /api/pool（默认 5 分钟，折叠/最大化节点卡即暂停拉取）· 动作 POST 后立即重拉 · 快捷键： Esc
        退出最大化 · r 立即刷新全部。
      </p>
    </div>
  )
}
