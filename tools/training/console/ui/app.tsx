/** app.tsx — 训练控制台根组件（SSR + hydrate；一屏仪表盘布局，DECISIONS §355）。
 *
 *  布局：顶栏（课程▾ + 训练状态 chips + 刷新间隔 select + ⟳）→ Hero（胜率焦点 + 迷你条）
 *  → 组件 4 小卡 → 节点 pill 行 → 详情抽屉（指标 | 节点统计 | 日志）→ TrainingLoop 启动弹窗。
 *
 *  交互纪律：无「停止全部」（用户指令）· 无「暂停刷新」按钮（改刷新间隔 select）·
 *  离线节点默认折叠 · 工具行并入启动弹窗 · 详情一律进右侧抽屉（Esc / ✕ / 遮罩关闭）。
 *
 *  polling 优先级（GLM-E6）：visibility(后台 tab) > 刷新间隔；客户端输入均为本地 state，
 *  3s 轮询不覆盖（不再需要全局 dirty 暂停）。 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { Flash, type FlashState } from '../../ui/components/Flash'
import { Drawer } from '../../ui/components/Drawer'
import { PanelErrorBoundary } from '../../ui/components/PanelErrorBoundary'
import { usePolling } from './lib/usePolling'
import { fetchState, postAction } from './lib/api-client'
import { Hero } from './panels/Hero'
import { ComponentCards } from './panels/ComponentCards'
import { NodePills } from './panels/NodePills'
import { MetricsTable } from './panels/MetricsTable'
import { NodeStats } from './panels/NodeStats'
import { LogNavCard } from './panels/LogNavCard'
import { TrainLaunchModal } from './panels/TrainLaunchModal'
import {
  fmtPct,
  fmtTs,
  klTone,
  latestRow,
  REFRESH_INTERVALS,
  TC_GLOBAL_INTERVAL,
  winTone,
  type ConsoleStateView,
  type RefreshSec,
  type ValueTone,
} from '../../ui/view'

export interface AppProps {
  initial: ConsoleStateView
}

type DrawerTabKey = 'metrics' | 'nodes' | 'log'

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

function initInterval(): RefreshSec {
  const v = readLocal(TC_GLOBAL_INTERVAL)
  if (v === '3' || v === '5' || v === '30') return Number(v) as RefreshSec
  return 3
}

/** 顶栏状态 chips 小件。 */
function Chip({ lbl, val, tone }: { lbl?: string; val: string; tone?: ValueTone | 'a' }) {
  return (
    <span className={`tc-cchip${tone ? ` tc-cchip--${tone}` : ''}`}>
      {lbl ? <span className="lbl">{lbl}</span> : null}
      <b>{val}</b>
    </span>
  )
}

export function App({ initial }: AppProps) {
  const [stateView, setStateView] = useState<ConsoleStateView | null>(initial)
  const [connError, setConnError] = useState<'off' | 'retry' | 'down'>('off')
  const [refreshInterval, setRefreshInterval] = useState<RefreshSec>(initInterval)
  const [flash, setFlash] = useState<FlashState | null>(null)
  const [documentVisible, setDocumentVisible] = useState(
    typeof document === 'undefined' || !document.hidden,
  )
  const [drawerTab, setDrawerTab] = useState<DrawerTabKey | null>(null)
  const [trainOpen, setTrainOpen] = useState(false)
  const [poolFreshNonce, setPoolFreshNonce] = useState(0)

  const wasError = useRef(false)
  const failCount = useRef(0)

  // ── 拉取 /api/state（刷新间隔；单次失败 retry，连续 3 次 down） ──
  const refreshState = useCallback(async (): Promise<void> => {
    try {
      const s = await fetchState()
      setStateView(s)
      setConnError('off')
      failCount.current = 0
      if (wasError.current) setPoolFreshNonce((n) => n + 1)
      wasError.current = false
    } catch {
      wasError.current = true
      failCount.current += 1
      setConnError(failCount.current >= 3 ? 'down' : 'retry')
    }
  }, [])

  usePolling({
    enabled: documentVisible,
    intervalSec: refreshInterval,
    fetch: refreshState,
  })

  useEffect(() => {
    const onVis = (): void => {
      const vis = !document.hidden
      setDocumentVisible(vis)
      if (vis) void refreshState()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refreshState])

  // 键盘：Esc 依次关 TrainingLoop 弹窗 / 抽屉；r 立即刷新（输入框聚焦时禁用）
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return
      if (e.key === 'Escape') {
        setTrainOpen(false)
        setDrawerTab(null)
      } else if (e.key.toLowerCase() === 'r') {
        void refreshState()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [refreshState])

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

  const handleLaunch = async (mode: 'pull' | 'push' | 'local'): Promise<void> => {
    setTrainOpen(false)
    await doAction('preset', { mode })
  }

  // ── 顶栏状态 chips（最新迭代口径） ──
  const course = stateView?.course ?? ''
  const iters = stateView?.metrics.iters ?? []
  const head = latestRow(iters)
  const headChips = head
    ? [
        <Chip key="it" lbl="it" val={String(head.iter)} tone="a" />,
        <Chip key="ro" lbl="rollout" val={`${head.rolloutSec.toFixed(0)}s`} />,
        <Chip key="ppo" lbl="ppo" val={`${head.ppoSec.toFixed(0)}s`} />,
        <Chip key="wr" lbl="胜率" val={fmtPct(head.winRate)} tone={winTone(head.winRate)} />,
        <Chip
          key="kills"
          lbl="击杀"
          val={head.actuals ? `${head.actuals.totalKills}/${head.actuals.games}局` : '—'}
        />,
        head.evalData && head.evalData.winRate !== null ? (
          <Chip
            key="ev"
            lbl="eval"
            val={fmtPct(head.evalData.winRate)}
            tone={winTone(head.evalData.winRate)}
          />
        ) : null,
        <Chip key="kl" lbl="KL" val={head.kl.toFixed(4)} tone={klTone(head.kl)} />,
        <Chip key="ent" lbl="熵" val={head.entropy.toFixed(3)} />,
      ].filter(Boolean)
    : [
        <Chip
          key="none"
          val={stateView && stateView.metrics.available === false ? '本课程暂无迭代记录' : '—'}
        />,
      ]

  return (
    <div className="tc-wrap">
      <Flash flash={flash} onHide={() => setFlash(null)} />

      <header className="tc-topbar">
        <div className="tc-status">
          <label className="tc-toggle tc-small" style={{ margin: 0 }}>
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
          {headChips}
        </div>
        <div className="tc-topbar__row">
          <h1>
            <span className="dot" />
            NN 训练控制台
          </h1>
          <div className="tc-topbar__right">
            <label className="tc-toggle tc-small">
              刷新
              <select
                className="tc-sel"
                aria-label="刷新间隔"
                value={refreshInterval}
                onChange={(e) => {
                  const v = Number((e.target as HTMLSelectElement).value) as RefreshSec
                  setRefreshInterval(v)
                  writeLocal(TC_GLOBAL_INTERVAL, String(v))
                }}
              >
                {REFRESH_INTERVALS.map((s) => (
                  <option key={s} value={s}>
                    {s}s
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
            <span className="tc-topbar__ts">
              {stateView ? `更新于 ${fmtTs(new Date(stateView.time).getTime(), Date.now())}` : ''}
            </span>
          </div>
        </div>
      </header>

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

      <PanelErrorBoundary>
        <Hero stateView={stateView} onMore={() => setDrawerTab('metrics')} />
      </PanelErrorBoundary>

      <PanelErrorBoundary>
        <ComponentCards
          stateView={stateView}
          onAction={doAction}
          onLaunchTrainer={() => setTrainOpen(true)}
        />
      </PanelErrorBoundary>

      <PanelErrorBoundary>
        <NodePills
          nodes={stateView?.nodes ?? []}
          onAction={doAction}
          onMore={() => setDrawerTab('nodes')}
        />
      </PanelErrorBoundary>

      <p className="tc-caption">
        仅回环 127.0.0.1 无鉴权（DECISIONS §348）· /api/state {refreshInterval}s 轮询 · 池统计独立
        /api/pool（抽屉内 5 分钟） · 详情进右侧抽屉 · Esc 关闭弹窗/抽屉 · r 立即刷新全部。
      </p>

      <Drawer
        open={drawerTab !== null}
        activeTab={drawerTab ?? 'metrics'}
        tabs={[
          { key: 'metrics', label: '指标' },
          { key: 'nodes', label: '节点统计' },
          { key: 'log', label: '日志' },
        ]}
        onTab={(k) => setDrawerTab(k as DrawerTabKey)}
        onClose={() => setDrawerTab(null)}
      >
        {drawerTab === 'metrics' ? <MetricsTable stateView={stateView} /> : null}
        {drawerTab === 'nodes' ? <NodeStats enabled poolFreshNonce={poolFreshNonce} /> : null}
        {drawerTab === 'log' ? <LogNavCard stateView={stateView} /> : null}
      </Drawer>

      {stateView ? (
        <TrainLaunchModal
          open={trainOpen}
          modes={stateView.modes}
          onClose={() => setTrainOpen(false)}
          onAction={doAction}
          onLaunch={(m) => void handleLaunch(m)}
        />
      ) : null}
    </div>
  )
}

void null
