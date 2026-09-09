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
  fmtTs,
  latestRow,
  REFRESH_INTERVALS,
  refreshLabel,
  TC_GLOBAL_INTERVAL,
  type ConsoleStateView,
  type PhaseInfo,
  type RefreshSec,
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
  if (v === '60' || v === '180' || v === '300' || v === '600' || v === '1800')
    return Number(v) as RefreshSec
  return 300
}

/** 阶段耗时格式化 'Xs' / 'Xm Ys' / 'Xh Ym'。 */
function fmtElapsed(ms: number | null): string {
  if (ms == null || ms < 0) return '—'
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rs = s % 60
  if (m < 60) return `${m}m${rs > 0 ? ` ${rs}s` : ''}`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return `${h}h${rm > 0 ? ` ${rm}m` : ''}`
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
  // 阶段耗时段 10s 客户端自走（sinceMs 是服务器锚点；两次轮询之间显示不冻结）。
  const [now, setNow] = useState(() => Date.now())

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
      setNow(Date.now())
      if (vis) void refreshState()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [refreshState])

  // 阶段耗时自走：可见时 10s 一跳（后台 tab 靠 visibilitychange 回来时校正）。
  useEffect(() => {
    if (!documentVisible) return
    const t = setInterval(() => setNow(Date.now()), 10_000)
    return () => clearInterval(t)
  }, [documentVisible])

  // iter 结束自动刷新（§361④）：SSR __INITIAL__ 已注入最新 iter；观测到迭代号增长
  // 即立即补拉一次 + 池统计 nonce++（10s 去抖，防 eval 尾巴/同 iter 重写连跳）。轮询间隔不变。
  const headIter = latestRow(stateView?.metrics.iters ?? [])?.iter ?? null
  const lastIterSeen = useRef<number | null>(latestRow(initial.metrics.iters)?.iter ?? null)
  const lastBoostAt = useRef(0)
  useEffect(() => {
    if (headIter == null || !documentVisible) return
    const prev = lastIterSeen.current ?? -1
    lastIterSeen.current = headIter
    if (headIter <= prev) return
    const now = Date.now()
    if (now - lastBoostAt.current < 10_000) return
    lastBoostAt.current = now
    void refreshState()
    setPoolFreshNonce((n) => n + 1)
  }, [headIter, documentVisible, refreshState])

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

  // hub-server 运行中锁定课程：hub 按课程建 jobRoot/日志目录，切课程会打乱在途训练
  // 状态——先停止 hub-server 再切换（§367 UI 交互）。
  const hubRunning = (stateView?.components ?? []).some(
    (c) => c.key === 'hubServer' && c.status === 'running',
  )

  // ── 顶栏（标题行放到页面最顶端） ──
  const course = stateView?.course ?? ''

  // 顶栏阶段耗时（至今；now 由 10s ticker 驱动，轮询间隙不冻结）。
  const phaseInfo: PhaseInfo | null = stateView?.phase ?? null
  const phaseElapsed = phaseInfo && phaseInfo.sinceMs != null ? now - phaseInfo.sinceMs : null

  return (
    <div className="tc-wrap">
      <Flash flash={flash} onHide={() => setFlash(null)} />

      <header className="tc-topbar">
        <div className="tc-topbar__row">
          <h1>
            <span className="dot" />
            网训战役指挥部
          </h1>
          {/* 课程选择：标题行中部（最新 iter 指标 chips 已移除） */}
          <label className="tc-topbar__course" title={undefined}>
            <span className="tc-topbar__course-lbl">课程</span>
            <select
              id="courseSel"
              className="tc-sel"
              value={course}
              disabled={hubRunning}
              title={
                hubRunning
                  ? 'hub-server 运行中——切课程会打乱在途训练状态，先停止 hub-server 再切换'
                  : undefined
              }
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
            {hubRunning ? (
              <span className="tc-muted tc-small" title="先停止 hub-server 再切换课程">
                hub 运行中，课程已锁定
              </span>
            ) : null}
          </label>
          <div className="tc-topbar__right">
            {phaseInfo && phaseInfo.phase !== 'idle' ? (
              <span
                className={`tc-phase tc-phase--${phaseInfo.phase}`}
                title={
                  phaseInfo.iter != null
                    ? `it${phaseInfo.iter} ${phaseInfo.phase === 'rollout' ? '采集' : 'PPO'} 阶段`
                    : phaseInfo.phase === 'rollout'
                      ? '采集阶段'
                      : 'PPO 阶段'
                }
              >
                <span className="tc-phase__icon" aria-hidden="true">
                  {phaseInfo.phase === 'rollout' ? '◎' : '⬡'}
                </span>
                <span className="tc-phase__label">
                  {phaseInfo.phase === 'rollout' ? 'rollout' : 'ppo'}
                </span>
                <span className="tc-phase__elapsed">{fmtElapsed(phaseElapsed)}</span>
              </span>
            ) : null}
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
                    {refreshLabel(s)}
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
          local={stateView?.localNode ?? null}
          onAction={doAction}
          onMore={() => setDrawerTab('nodes')}
        />
      </PanelErrorBoundary>

      <p className="tc-caption">
        仅回环 127.0.0.1 无鉴权（DECISIONS §348）· /api/state {refreshInterval}s 轮询 ·
        首页即训练态势：胜率焦点 + 组件卡（点击卡在下方展开全宽最近日志）+ 节点 pill 行 ·
        详情进抽屉（指标 | 节点统计 | 日志）· Esc 关闭弹窗/抽屉 · r 立即刷新全部。
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
