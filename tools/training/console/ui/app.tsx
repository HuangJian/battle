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
import { EvalBoard } from './panels/EvalBoard'
import {
  fmtTs,
  latestRow,
  REFRESH_INTERVALS,
  refreshLabel,
  TC_GLOBAL_INTERVAL,
  TC_RO_BANNER_DISMISSED,
  type ConsoleStateView,
  type PhaseInfo,
  type RefreshSec,
} from '../../ui/view'

export interface AppProps {
  initial: ConsoleStateView
}

type DrawerTabKey = 'metrics' | 'nodes' | 'log' | 'eval'

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

/** 刷新间隔默认值——必须与 SSR 首帧一致（SSR 无 localStorage，恒为 300）。 */
const DEFAULT_INTERVAL: RefreshSec = 300

/** 已存刷新间隔（仅合法值；非法回退默认）。hydrate 后在 effect 里恢复，不参与首帧渲染。 */
function storedInterval(): RefreshSec {
  const v = readLocal(TC_GLOBAL_INTERVAL)
  if (v === '60' || v === '180' || v === '300' || v === '600' || v === '1800')
    return Number(v) as RefreshSec
  return DEFAULT_INTERVAL
}

/** 本机判定（局域网只读边界）：页面经 localhost/127.0.0.1 打开 = 本机，可执行动作；
 *  经局域网 IP 打开 = 只读查看（服务端 POST 还会 403 兜底，双保险）。 */
function isLocalHost(): boolean {
  if (typeof location === 'undefined') return true // SSR 首帧无 location，按本机渲染
  const h = location.hostname
  return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]'
}

/** 把查看课程写入 URL（?course=，history.replaceState）：局域网刷新/分享链接保持所选课程。 */
function writeUrlCourse(c: string): void {
  try {
    const u = new URL(location.href)
    if (c) u.searchParams.set('course', c)
    else u.searchParams.delete('course')
    history.replaceState(null, '', u.pathname + u.search)
  } catch {
    /* ignore */
  }
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
  // 首帧一律用 SSR 默认值（localStorage 服务端不可读）——首帧读本地存储会让客户端
  // vnode 与 SSR HTML 不一致 → hydrate 错配 → 组件区 DOM 错位（§：只读横幅关闭后样式崩）。
  const [refreshInterval, setRefreshInterval] = useState<RefreshSec>(DEFAULT_INTERVAL)
  // 本地偏好在 hydrate 之后恢复（与 Hero 的 TC_HERO_ITER_VIEW 同款写法）。
  useEffect(() => {
    const v = storedInterval()
    if (v !== DEFAULT_INTERVAL) setRefreshInterval(v)
  }, [])
  const [flash, setFlash] = useState<FlashState | null>(null)
  const [documentVisible, setDocumentVisible] = useState(
    typeof document === 'undefined' || !document.hidden,
  )
  const [drawerTab, setDrawerTab] = useState<DrawerTabKey | null>(null)
  const [trainOpen, setTrainOpen] = useState(false)
  const [poolFreshNonce, setPoolFreshNonce] = useState(0)
  // 只读横幅可关闭：localStorage 记住「不再显示」（仅局域网只读视图相关；tc. 前缀防误删）。
  // 首帧恒 false（与 SSR 一致），localStorage 偏好 hydrate 后恢复——见下方 effect。
  const [roBannerDismissed, setRoBannerDismissed] = useState(false)
  useEffect(() => {
    if (readLocal(TC_RO_BANNER_DISMISSED) === '1') setRoBannerDismissed(true)
  }, [])
  // 视图课程（局域网只读核心）：初始 = SSR 的 ?course= 覆盖或操作员课程；切换只改本浏览器
  // 的查看 + URL，本机才额外 POST setCourse 同步操作员课程（动作 WYSIWYG 走 body.course）。
  const isLocal = isLocalHost()
  // 只读视图标记：服务端按请求来源 stamp（SSR 首帧即正确，无闪跳）；缺省回退 hostname 判定。
  const readOnly = initial.readOnly ?? !isLocal
  const [viewCourse, setViewCourse] = useState<string>(initial.course)
  const viewCourseRef = useRef(viewCourse)
  viewCourseRef.current = viewCourse
  // 阶段耗时段 10s 客户端自走（sinceMs 是服务器锚点；两次轮询之间显示不冻结）。
  const [now, setNow] = useState(() => Date.now())

  const wasError = useRef(false)
  const failCount = useRef(0)

  // ── 拉取 /api/state（刷新间隔；单次失败 retry，连续 3 次 down） ──
  // 始终带当前查看课程（?course= 只读覆盖）——切课程后轮询/iter 加速/可见性恢复都取同一课程。
  const refreshState = useCallback(async (): Promise<void> => {
    try {
      const s = await fetchState(viewCourseRef.current)
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
  // 课程敏感动作（start/preset/smoke/smokeTrain/nodeSmoke/setCourse）统一带上当前查看课程：
  // 「所见即所控」——操作员启动的 trainer/hub 一定用他正在看的课程，不依赖全局 console-state。
  // 局域网来源 POST 会被服务端 403（只读门控），此处只是本机路径的语义保证。
  const doAction = useCallback(
    async (act: string, body: Record<string, unknown> = {}): Promise<{ ok: boolean }> => {
      const course = viewCourseRef.current
      const fullBody = course ? { course, ...body } : body
      const r = await postAction(act, fullBody)
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

  // hub-server 运行中锁定课程（仅本机）：hub 按课程建 jobRoot/日志目录，切操作员课程会打乱
  // 在途训练状态——先停止 hub-server 再切换（§367 UI 交互）。局域网查看不受此限：只读切换
  // 课程不影响任何训练状态。
  const hubRunning = (stateView?.components ?? []).some(
    (c) => c.key === 'hubServer' && c.status === 'running',
  )

  // 课程下拉 onChange：本机 = 查看 + POST setCourse 同步操作员课程；局域网 = 仅查看 + 写 URL。
  // 注意：ref 须在此同步更新（setState 后下一渲染才赋值）——随后的 refreshState/doAction
  // 立即读到的必须已是新课程，否则轮询仍拉旧课程。
  const onCourseChange = (e: Event): void => {
    const c = (e.target as HTMLSelectElement).value
    viewCourseRef.current = c
    setViewCourse(c)
    writeUrlCourse(c)
    void refreshState()
    setPoolFreshNonce((n) => n + 1)
    // 只读视图不 POST（服务端也会 403 兜底）；用服务端 stamp 的 readOnly 而非 isLocal。
    if (!readOnly) void doAction('setCourse', { course: c })
  }

  // 顶栏阶段耗时（至今；now 由 10s ticker 驱动，轮询间隙不冻结）。
  const phaseInfo: PhaseInfo | null = stateView?.phase ?? null
  const phaseElapsed = phaseInfo && phaseInfo.sinceMs != null ? now - phaseInfo.sinceMs : null

  // 正在训练的课程：trainingLoop 运行时的注册课程（启动即记账）；监督重启丢 course 时
  // 回退服务端生效课程（console-state，正常流程与训练课程一致）。查看课程 ≠ 训练课程时，
  // 在课程 select 后高亮提示——局域网切去查看其它课程也能一眼看到训练在哪个课程上。
  const trainingLoop = (stateView?.components ?? []).find((c) => c.key === 'trainingLoop')
  const trainingCourse =
    trainingLoop?.status === 'running' ? trainingLoop.course || stateView?.course || '' : ''

  return (
    <div className="tc-wrap">
      <Flash flash={flash} onHide={() => setFlash(null)} />
      <header className="tc-topbar">
        <div className="tc-topbar__row">
          <h1>
            <span className="dot" />
            网训战役指挥部
          </h1>
          {readOnly ? (
            <span
              className="tc-badge tc-badge--ro"
              title="本页面为局域网只读视图；启停/冒烟/模式开关/节点编辑仅在本机 localhost 打开控制台时可用"
            >
              🔒 局域网只读
            </span>
          ) : null}
          {/* 课程选择：标题行中部（最新 iter 指标 chips 已移除）——本机可切操作员课程，
              局域网只读切换查看课程（不落盘、不影响训练） */}
          <label className="tc-topbar__course" title={undefined}>
            <span className="tc-topbar__course-lbl">课程</span>
            <select
              id="courseSel"
              className="tc-sel"
              value={viewCourse}
              // 课程锁只对本机生效：用服务端 stamp 的 readOnly（SSR 首帧即正确）而非客户端 isLocal——
              // 后者 SSR 期恒 true，会渲染出局域网首帧 disabled 的 select（靠 hydration 纠正不可靠）。
              // 局域网只读切换课程不影响训练，任何训练状态下都可切。
              disabled={hubRunning && !readOnly}
              title={
                hubRunning && !readOnly
                  ? 'hub-server 运行中——切课程会打乱在途训练状态，先停止 hub-server 再切换'
                  : readOnly
                    ? '局域网只读：切换仅影响当前浏览器的查看课程，不影响训练'
                    : undefined
              }
              onChange={onCourseChange}
            >
              <option value="">自动（最近活跃课程）</option>
              {(stateView?.courses ?? []).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            {trainingCourse && trainingCourse !== viewCourse ? (
              <span
                className="tc-training-tag"
                title={`正在训练 ${trainingCourse}；当前查看 ${viewCourse || '(自动)'}——切换查看不影响训练`}
              >
                <span className="tc-dot tc-dot--on" />
                正在训练：{trainingCourse}
              </span>
            ) : null}
            {hubRunning && !readOnly ? (
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
      ) : null}{' '}
      {readOnly && !roBannerDismissed ? (
        <div className="tc-banner tc-banner--ro" role="status">
          <span>
            🔒 只读模式：可查看任意课程/日志/节点统计；启停组件、冒烟、模式开关与节点编辑 仅在本机
            localhost 打开控制台时可用（动作按钮可点击，执行时会被服务端拒绝并提示）。
          </span>
          <button
            type="button"
            className="tc-btn tc-btn--sm"
            aria-label="关闭只读提示"
            title="关闭后不再显示（清 tc.* localStorage 可恢复）"
            onClick={() => {
              writeLocal(TC_RO_BANNER_DISMISSED, '1')
              setRoBannerDismissed(true)
            }}
          >
            ✕
          </button>
        </div>
      ) : null}
      <PanelErrorBoundary>
        <Hero stateView={stateView} onMore={() => setDrawerTab('metrics')} />
      </PanelErrorBoundary>
      {/* ── 组件卡 4  row：在 LAN 只读视图里也正常交互样式（不在 banner 里、不 opacity 灰败） ── */}
      <PanelErrorBoundary>
        <ComponentCards
          stateView={stateView}
          onAction={doAction}
          onLaunchTrainer={() => setTrainOpen(true)}
          course={viewCourse}
          readOnly={readOnly}
        />
      </PanelErrorBoundary>
      <PanelErrorBoundary>
        <NodePills
          nodes={stateView?.nodes ?? []}
          local={stateView?.localNode ?? null}
          onAction={doAction}
          onMore={() => setDrawerTab('nodes')}
          readOnly={readOnly}
        />
      </PanelErrorBoundary>
      <p className="tc-caption">
        局域网只读：可查看任意课程/日志/节点统计（课程▾仅本浏览器切换）；启停/冒烟/模式/节点编辑
        仅本机 localhost 生效 · /api/state {refreshInterval}s 轮询 · 首页即训练态势：胜率焦点 +
        组件卡 （点击卡在下方展开全宽最近日志）+ 节点 pill 行 · 详情进抽屉（指标 | 节点统计 |
        日志）· Esc 关闭弹窗/抽屉 · r 立即刷新全部。
      </p>
      <Drawer
        open={drawerTab !== null}
        activeTab={drawerTab ?? 'metrics'}
        tabs={[
          { key: 'metrics', label: '指标' },
          { key: 'nodes', label: '节点统计' },
          { key: 'log', label: '日志' },
          { key: 'eval', label: '评估' },
        ]}
        onTab={(k) => setDrawerTab(k as DrawerTabKey)}
        onClose={() => setDrawerTab(null)}
      >
        {drawerTab === 'metrics' ? <MetricsTable stateView={stateView} /> : null}
        {drawerTab === 'nodes' ? (
          <NodeStats enabled poolFreshNonce={poolFreshNonce} course={viewCourse} />
        ) : null}
        {drawerTab === 'log' ? <LogNavCard stateView={stateView} course={viewCourse} /> : null}
        {drawerTab === 'eval' ? (
          <EvalBoard enabled={drawerTab === 'eval'} course={viewCourse} readOnly={readOnly} />
        ) : null}
      </Drawer>
      {stateView ? (
        <TrainLaunchModal
          open={trainOpen}
          modes={stateView.modes}
          onClose={() => setTrainOpen(false)}
          onAction={doAction}
          onLaunch={(m) => void handleLaunch(m)}
          readOnly={readOnly}
        />
      ) : null}
    </div>
  )
}

void null
