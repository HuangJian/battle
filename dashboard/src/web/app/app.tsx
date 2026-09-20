/** app.tsx — 训练控制台根组件（SSR + hydrate）。
 *
 *  布局（docs/dashboard-redesign.md §3.1 / §3.3）：
 *    Shell（侧栏 · 顶栏 · 主内容）→ 页面区（按 `page` 切换）
 *      `/`        总览 = 告警 + 组件 + 节点 + 课程 + 调度 + 交付 + 评估摘要
 *      `/metrics` 指标 = 完整指标表（原先只在抽屉里）
 *      `/nodes`   节点 = 节点统计 + push worker 登记
 *      `/wire`    传输 = 每轮实发/实收 + 隧道 A/B
 *
 *  路由（§5.1）：服务端按 URL 决定 `initial.page` 并据此 SSR 首帧；客户端导航走
 *  `history.pushState`（不整页 reload），`popstate` 回退。**首帧不读 `location`**：
 *  SSR 期没有它，且「首帧依赖浏览器状态」就是 hydrate 错配的老病根（§5.1 纪律）。
 *
 *  交互纪律（沿用，勿回退）：无「停止全部」（用户指令）· 无「暂停刷新」按钮（改刷新间隔）
 *  · 停用节点默认折叠（慢/离线始终展开）· 详情一律走独立页面而非模态抽屉（§3.2）。
 *
 *  polling 优先级（GLM-E6）：visibility(后台 tab) > 刷新间隔；客户端输入均为本地 state，
 *  3s 轮询不覆盖（不再需要全局 dirty 暂停）。
 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { Flash, type FlashState } from '../components/Flash'
import { PanelErrorBoundary } from '../components/PanelErrorBoundary'
import { usePolling } from './lib/usePolling'
import { fetchState, postAction } from './lib/api-client'
import { Shell } from './shell/Shell'
import { Hero } from './panels/Hero'
import { ComponentCards } from './panels/ComponentCards'
import { NodePills } from './panels/NodePills'
import { MetricsTable } from './panels/MetricsTable'
import { NodeStats } from './panels/NodeStats'
import { LogNavCard } from './panels/LogNavCard'
import { TrainLaunchModal, type TunnelLaunchOpts } from './panels/TrainLaunchModal'
import { BcPanel } from './panels/BcPanel'
import { CourseOverview } from './panels/CourseOverview'
import { WorkerRegistry } from './panels/WorkerRegistry'
import { LoopQueue } from './panels/LoopQueue'
import { TaskBundlePanel } from './panels/TaskBundlePanel'
import { WirePanel } from './panels/WirePanel'
import { EvalSummary } from './panels/EvalSummary'
import {
  bootstrapPage,
  canonicalPath,
  DEFAULT_PAGE,
  fmtTs,
  latestRow,
  pageForPath,
  REFRESH_INTERVALS,
  TC_CLOUDHALT_ACK,
  TC_GLOBAL_INTERVAL,
  TC_RO_BANNER_DISMISSED,
  cloudHaltAckKey,
  parseCloudHaltAcks,
  withCourse,
  type ConsoleBootstrap,
  type PageKey,
  type RefreshSec,
  visibleCloudHalts,
} from '../view'

export interface AppProps {
  initial: ConsoleBootstrap
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

/** 刷新间隔默认值——必须与 SSR 首帧一致（SSR 无 localStorage，恒为 300）。 */
const DEFAULT_INTERVAL: RefreshSec = 300

/** 已存刷新间隔（仅合法值；非法回退默认）。hydrate 后在 effect 里恢复，不参与首帧渲染。 */
function storedInterval(): RefreshSec {
  const v = readLocal(TC_GLOBAL_INTERVAL)
  if (REFRESH_INTERVALS.some((s) => String(s) === v)) return Number(v) as RefreshSec
  return DEFAULT_INTERVAL
}

/** 本机判定（局域网只读边界）：页面经 localhost/127.0.0.1 打开 = 本机，可执行动作；
 *  经局域网 IP 打开 = 只读查看（服务端 POST 还会 403 兜底，双保险）。 */
function isLocalHost(): boolean {
  if (typeof location === 'undefined') return true // SSR 首帧无 location，按本机渲染
  const h = location.hostname
  return h === 'localhost' || h === '127.0.0.1' || h === '::1' || h === '[::1]'
}

/** 把查看课程写入 URL（?course=，history.replaceState）：刷新/分享链接保持所选课程。
 *  保留当前 pathname —— 路由与课程两个维度互不覆盖。 */
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

export function App({ initial }: AppProps) {
  const [stateView, setStateView] = useState<ConsoleBootstrap | null>(initial)
  const [connError, setConnError] = useState<'off' | 'retry' | 'down'>('off')
  // 首帧一律用 SSR 默认值（localStorage 服务端不可读）——首帧读本地存储会让客户端
  // vnode 与 SSR HTML 不一致 → hydrate 错配 → 组件区 DOM 错位（见 tests/web-ssr-readonly.test.ts）。
  const [refreshInterval, setRefreshInterval] = useState<RefreshSec>(DEFAULT_INTERVAL)
  useEffect(() => {
    const v = storedInterval()
    if (v !== DEFAULT_INTERVAL) setRefreshInterval(v)
  }, [])
  const [flash, setFlash] = useState<FlashState | null>(null)
  const [documentVisible, setDocumentVisible] = useState(
    typeof document === 'undefined' || !document.hidden,
  )
  const [trainOpen, setTrainOpen] = useState(false)
  const [poolFreshNonce, setPoolFreshNonce] = useState(0)
  // 当前页面（§5.1）：首帧取服务端 stamp 的 page（SSR 与客户端同值 → hydrate 一致）；
  // URL 校准放到挂载后的 effect（`/api/state` 直接消费时 initial 无 page）。
  const [page, setPage] = useState<PageKey>(() => bootstrapPage(initial, '/'))
  // 只读横幅可关闭：localStorage 记住「不再显示」。首帧恒 false（与 SSR 一致）。
  const [roBannerDismissed, setRoBannerDismissed] = useState(false)
  useEffect(() => {
    if (readLocal(TC_RO_BANNER_DISMISSED) === '1') setRoBannerDismissed(true)
  }, [])
  // 云端停机横幅已读（按「事件身份」记：课程+时刻），同一事件只提示一次。
  const [cloudHaltAcks, setCloudHaltAcks] = useState<string[]>([])
  useEffect(() => {
    setCloudHaltAcks(parseCloudHaltAcks(readLocal(TC_CLOUDHALT_ACK)))
  }, [])
  /** 记住「知道了」：写入 localStorage（数组格式，旧单值格式兼容）。 */
  const ackCloudHalt = useCallback((key: string): void => {
    setCloudHaltAcks((prev) => {
      const next = prev.includes(key) ? prev : [...prev, key]
      writeLocal(TC_CLOUDHALT_ACK, JSON.stringify(next))
      return next
    })
  }, [])
  // 视图课程（局域网只读核心）：初始 = SSR 的 ?course= 覆盖或操作员课程；切换只改本浏览器
  // 的查看 + URL，本机才额外 POST setCourse 同步操作员课程（动作 WYSIWYG 走 body.course）。
  const isLocal = isLocalHost()
  const readOnly = initial.readOnly ?? !isLocal
  const [viewCourse, setViewCourse] = useState<string>(initial.course)
  const viewCourseRef = useRef(viewCourse)
  viewCourseRef.current = viewCourse
  // ── 门禁动作模式：halt = 触发门禁就下发 cloud halt（默认）；notify = 只告警不停机。
  //    hydrate 安全：初始值恒为 'halt'，挂载后由服务端标志文件 + localStorage 校准。
  const [gateHaltMode, setGateHaltMode] = useState<'halt' | 'notify'>('halt')
  // 阶段耗时段 10s 客户端自走（sinceMs 是服务器锚点；两次轮询之间显示不冻结）。
  const [now, setNow] = useState(() => Date.now())

  const wasError = useRef(false)
  const failCount = useRef(0)

  // ── 路由：URL ↔ 页面键（§5.1） ──
  // 首帧后的双向同步：① 挂载时按当前 URL 校准（覆盖 initial 无 page 的路径）；
  // ② popstate（浏览器前进/后退）跟着改。守卫：SSR 无 location，故整段只跑在浏览器。
  useEffect(() => {
    if (typeof location === 'undefined') return
    const sync = (): void => setPage(pageForPath(location.pathname) ?? DEFAULT_PAGE)
    sync()
    window.addEventListener('popstate', sync)
    return () => window.removeEventListener('popstate', sync)
  }, [])

  /** 导航（§5.1）：拦截为 pushState + 切页，不整页 reload；`?course=` 跟随。 */
  const navigate = useCallback(
    (target: PageKey, e?: JSX.TargetedMouseEvent<HTMLAnchorElement>): void => {
      e?.preventDefault()
      setPage(target)
      try {
        if (typeof history !== 'undefined')
          history.pushState(
            { page: target },
            '',
            withCourse(canonicalPath(target), viewCourseRef.current),
          )
      } catch {
        /* 无 history（测试环境）时只切本地状态 */
      }
    },
    [],
  )

  // ── 拉取 /api/state（刷新间隔；单次失败 retry，连续 3 次 down） ──
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

  // iter 结束自动刷新（§361④）：观测到迭代号增长即立即补拉一次 + 池统计 nonce++
  // （10s 去抖，防 eval 尾巴/同 iter 重写连跳）。轮询间隔不变。
  const headIter = latestRow(stateView?.metrics.iters ?? [])?.iter ?? null
  const lastIterSeen = useRef<number | null>(latestRow(initial.metrics.iters)?.iter ?? null)
  const lastBoostAt = useRef(0)
  useEffect(() => {
    if (headIter == null || !documentVisible) return
    const prev = lastIterSeen.current ?? -1
    lastIterSeen.current = headIter
    if (headIter <= prev) return
    const t = Date.now()
    if (t - lastBoostAt.current < 10_000) return
    lastBoostAt.current = t
    void refreshState()
    setPoolFreshNonce((n) => n + 1)
  }, [headIter, documentVisible, refreshState])

  // 键盘：Esc 关 TrainingLoop 弹窗；r 立即刷新（输入框聚焦时禁用）
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')) return
      if (e.key === 'Escape') {
        setTrainOpen(false)
      } else if (e.key.toLowerCase() === 'r') {
        void refreshState()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [refreshState])

  // ── 动作派发（POST → flash → 重拉 state） ──
  // 课程敏感动作统一带上当前查看课程：「所见即所控」——操作员启动的 trainer/hub
  // 一定用他正在看的课程，不依赖全局 console-state。
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

  // 首次进入/切课程时，用**服务端标志文件**校准本地开关（文件是真相，localStorage 只是记忆）。
  useEffect(() => {
    void (async () => {
      let v: 'halt' | 'notify' | null = null
      try {
        const r = await postAction('getGateHaltMode', {})
        if (r.ok && (r.message === 'halt' || r.message === 'notify')) v = r.message
      } catch {
        /* 拉取失败 → 退到 localStorage */
      }
      if (!v) {
        try {
          if (localStorage.getItem('tc.gateHaltMode') === 'notify') v = 'notify'
        } catch {
          /* 隐私模式下读不了 */
        }
      }
      if (v) setGateHaltMode(v)
    })()
  }, [viewCourse])

  const onGateHaltModeChange = useCallback(
    (v: 'halt' | 'notify'): void => {
      setGateHaltMode(v)
      try {
        localStorage.setItem('tc.gateHaltMode', v)
      } catch {
        /* 隐私模式下写不了就算了 */
      }
      // 写 <traj>/gate-halt-mode.txt ⇒ Python 下一轮门判定即生效，无需重启训练。
      void doAction('setGateHaltMode', { mode: v })
    },
    [doAction],
  )

  const onRefreshIntervalChange = useCallback((v: RefreshSec): void => {
    setRefreshInterval(v)
    writeLocal(TC_GLOBAL_INTERVAL, String(v))
  }, [])

  const handleLaunch = async (
    opts?: TunnelLaunchOpts & { remoteDegrade?: boolean },
  ): Promise<void> => {
    setTrainOpen(false)
    // 启动**不传模式**：执行面由 rl.hub_push + 登记节点推出来。
    const body: Record<string, unknown> = {
      remoteDegrade: opts?.remoteDegrade === true,
    }
    // M1/M2/M3：传输选项随启动回写 rl-config + console-state（未选 = 不传，沿用现值）。
    if (opts?.cfProtocol) body.cfProtocol = opts.cfProtocol
    if (opts?.cfEdgeIp) body.cfEdgeIp = opts.cfEdgeIp
    if (opts?.slim) body.slim = opts.slim
    if (opts?.rolloutSrc) body.rolloutSrc = opts.rolloutSrc
    // 训练模式：在线/离线。离线时服务端会忽略上面的 rolloutSrc（只写该课的课程级键）。
    if (opts?.trainMode) body.trainMode = opts.trainMode
    await doAction('preset', body)
  }

  // 课程锁已随「单 hub 多课程」解除：hub 现在一个进程托管 N 份账本，进程级状态不再与
  // 「操作员在看哪门课」绑定——切课程只改「看哪门课」。
  // 注意：ref 须在此同步更新（setState 后下一渲染才赋值）——随后的 refreshState/doAction
  // 立即读到的必须已是新课程，否则轮询仍拉旧课程。
  const selectCourse = useCallback(
    (c: string): void => {
      viewCourseRef.current = c
      setViewCourse(c)
      writeUrlCourse(c)
      void refreshState()
      setPoolFreshNonce((n) => n + 1)
      if (!readOnly) void doAction('setCourse', { course: c })
    },
    [readOnly, refreshState, doAction],
  )

  // 顶栏阶段耗时（至今；now 由 10s ticker 驱动，轮询间隙不冻结）。
  const phaseInfo = stateView?.phase ?? null
  const phaseElapsed = phaseInfo && phaseInfo.sinceMs != null ? now - phaseInfo.sinceMs : null

  // 在训课程（**可多门**）：以服务端 stamp 的 `trainingCourses` 为准（共享 trainer 在跑
  // ∧ 该课未收官）；旧视图缺字段时回退到「trainer 在跑就当作当前查看的这门课在跑」
  // （失败方向是**少报**，不编）。
  const trainingLoop = (stateView?.components ?? []).find((c) => c.key === 'trainingLoop')
  const trainingCourses =
    stateView?.trainingCourses && stateView.trainingCourses.length > 0
      ? stateView.trainingCourses
      : trainingLoop?.status === 'running'
        ? [trainingLoop.course || stateView?.course || ''].filter(Boolean)
        : []

  const courses = stateView?.courses ?? []
  // 算力摘要（Topbar chip）：本机直跑槽位计入「在线」（它没有 ping 语义，有槽位即在用）。
  const nodes = stateView?.nodes ?? []
  const localNode = stateView?.localNode ?? null
  const nodeTotal = nodes.length + (localNode ? 1 : 0)
  const nodeSummary =
    nodeTotal > 0
      ? {
          online: nodes.filter((n) => n.online === true).length + (localNode ? 1 : 0),
          total: nodeTotal,
        }
      : null

  return (
    <div>
      <Flash flash={flash} onHide={() => setFlash(null)} />
      <Shell
        sidebar={{
          // 激活态按**页面键**推（不是 location）：SSR 无 location，首帧读它会 hydrate 错配。
          activePath: canonicalPath(page),
          course: viewCourse,
          courses,
          trainingCourses,
          onCourseChange: selectCourse,
          onNavigate: navigate,
          readOnly,
          gate: {
            visible: trainingCourses.length > 0,
            mode: gateHaltMode,
            disabled: !isLocal || readOnly,
            onChange: onGateHaltModeChange,
          },
          refresh: { value: refreshInterval, onChange: onRefreshIntervalChange },
        }}
        topbar={{
          page,
          stateView,
          phaseElapsedMs: phaseElapsed,
          trainingCount: trainingCourses.length,
          courseCount: courses.length,
          nodeSummary,
          connError,
          onRetry: () => void refreshState(),
          onRefreshNow: () => {
            void refreshState()
            setPoolFreshNonce((n) => n + 1)
          },
        }}
      >
        {/* ── 告警坞（P2 会合并为单容器；P0 保持既有横幅不变） ── */}
        {visibleCloudHalts(stateView?.cloudHalts, viewCourse)
          .filter(([, h]) => h.status === 'halted')
          .filter(
            ([courseName, h]) =>
              !cloudHaltAcks.includes(cloudHaltAckKey('halted', courseName, h.at)),
          )
          .map(([courseName, h]) => (
            <div key={`halt-${courseName}`} className="tc-banner tc-banner--err" role="alert">
              <span>
                ⚠ {courseName ? `课程 ${courseName} ` : ''}停机中（{h.reason}
                ）：已向云机下发停机命令——云机先尝试停机； 停不掉则照常执行任务（不闲置空烧）。 本地
                hub/console 均正常。本课恢复训练会自动解除；其它课的停机状态见「多课总览」徽标。
              </span>
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                onClick={() => void doAction('cloud-resume', { course: courseName })}
              >
                立即恢复
              </button>
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                onClick={() => ackCloudHalt(cloudHaltAckKey('halted', courseName, h.at))}
              >
                知道了
              </button>
            </div>
          ))}
        {visibleCloudHalts(stateView?.cloudHalts, viewCourse)
          .filter(([, h]) => h.status === 'recovered' && !!h.clearedAt)
          .filter(
            ([courseName, h]) =>
              !cloudHaltAcks.includes(cloudHaltAckKey('recovered', courseName, h.clearedAt ?? '')),
          )
          .map(([courseName, h]) => (
            <div key={`rec-${courseName}`} className="tc-banner tc-banner--muted" role="status">
              <span>
                {courseName ? `课程 ${courseName} ` : ''}曾停机（{h.reason}）· 已恢复（
                {h.clearReason ?? '手动恢复'}，{' '}
                {fmtTs(new Date(h.clearedAt ?? '').getTime(), Date.now())}）；停机期间
                停不掉的云机继续工作，未闲置浪费。
              </span>
              <button
                type="button"
                className="tc-btn tc-btn--sm"
                onClick={() =>
                  ackCloudHalt(cloudHaltAckKey('recovered', courseName, h.clearedAt ?? ''))
                }
              >
                知道了
              </button>
            </div>
          ))}
        {stateView?.loopComplete ? (
          <div className="tc-banner tc-banner--muted" role="status">
            <span>
              ✅ 训练已完成（{stateView.loopComplete.reason}）：本地已停止采集，云机已停机省配额，
              进程停车等待重启。改大 iters 后经「停止→启动」继续。
            </span>
          </div>
        ) : null}
        {stateView?.ppoQueueStall ? (
          <div className="tc-banner tc-banner--err" role="alert">
            <span>
              ⚠ PPO 任务排队超时：job{' '}
              <code>
                {stateView.ppoQueueStall.it != null
                  ? `it${stateView.ppoQueueStall.it}`
                  : stateView.ppoQueueStall.jobId.slice(0, 12)}
              </code>{' '}
              已等待 {Math.floor(stateView.ppoQueueStall.waitedSec / 60)} 分
              {stateView.ppoQueueStall.waitedSec % 60} 秒仍无 worker 领取——云端 worker
              可能断连或未在轮询 hub。检查 Colab/Kaggle worker 日志与 hub 是否在线。
            </span>
          </div>
        ) : null}
        {stateView?.courseEdit?.verdict === 'rejected' ? (
          <div className="tc-banner tc-banner--err" role="alert">
            <span>
              ⚠ 课程文件含<strong>语料身份</strong>改动（
              {stateView.courseEdit.fields.join('、') || '未识别字段'}
              ）——热加载已拒绝：沿用启动配置继续训练，编辑内容不进云端 payload。
              要应用请派生新关卡/新课程（D14 语料血缘不可 mid-run 破坏）；改回原文件后自动解除。
            </span>
          </div>
        ) : null}
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
              title="关闭后不再显示（侧栏常驻 🔒 徽标不受影响）"
              onClick={() => {
                writeLocal(TC_RO_BANNER_DISMISSED, '1')
                setRoBannerDismissed(true)
              }}
            >
              ✕
            </button>
          </div>
        ) : null}

        {/* ══════════════════ 总览 ══════════════════ */}
        {page === 'overview' ? (
          <>
            {/* RL 区（与 BC 区互斥：isBc 课只出 BC 区；Hero/EvalBoard 只属 RL） */}
            {stateView?.isBc ? null : (
              <PanelErrorBoundary>
                <Hero
                  stateView={stateView}
                  onMore={() => navigate('metrics')}
                  onRefresh={() => void refreshState()}
                  readOnly={readOnly}
                />
              </PanelErrorBoundary>
            )}
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
                nodes={nodes}
                local={localNode}
                onAction={doAction}
                onMore={() => navigate('nodes')}
                readOnly={readOnly}
              />
            </PanelErrorBoundary>
            {stateView?.isBc ? null : (
              <PanelErrorBoundary>
                <CourseOverview
                  overview={stateView?.overview ?? null}
                  course={viewCourse}
                  onSelectCourse={selectCourse}
                  onAction={doAction}
                />
              </PanelErrorBoundary>
            )}
            {/* 训练调度器（单例，**两区通用**）：每课任务队列 + 「在等什么」。
                不受 isBc 门控——它是跨课程卡，一次列出所有账本可发现的课（每行自带 kind）。 */}
            <PanelErrorBoundary>
              <LoopQueue
                loopQueue={stateView?.loopQueue ?? null}
                course={viewCourse}
                onSelectCourse={selectCourse}
                onAction={doAction}
              />
            </PanelErrorBoundary>
            {/* 任务包（导出 task-<课程>.zip / 导入 deliver-<课程>.zip 并评估）：两区通用 */}
            <PanelErrorBoundary>
              <TaskBundlePanel course={viewCourse} enabled={documentVisible} readOnly={readOnly} />
            </PanelErrorBoundary>
            {/* EvalBoard 摘要（RL 区）：完整看板独立成页 /eval */}
            {stateView?.isBc ? null : (
              <PanelErrorBoundary>
                <EvalSummary
                  course={viewCourse}
                  enabled={documentVisible}
                  readOnly={readOnly}
                  onMore={() => {
                    window.location.href = viewCourse
                      ? `/eval?course=${encodeURIComponent(viewCourse)}`
                      : '/eval'
                  }}
                />
              </PanelErrorBoundary>
            )}
            {stateView?.isBc ? (
              <PanelErrorBoundary>
                <BcPanel course={viewCourse} enabled={documentVisible} />
              </PanelErrorBoundary>
            ) : null}
            {/* 组件日志入口全集（独立页 /log/<key>） */}
            <PanelErrorBoundary>
              <LogNavCard stateView={stateView} course={viewCourse} />
            </PanelErrorBoundary>
          </>
        ) : null}

        {/* ══════════════════ 指标 ══════════════════ */}
        {page === 'metrics' ? (
          <PanelErrorBoundary>
            <MetricsTable
              stateView={stateView}
              onRefresh={() => void refreshState()}
              readOnly={readOnly}
            />
          </PanelErrorBoundary>
        ) : null}

        {/* ══════════════════ 节点 ══════════════════ */}
        {page === 'nodes' ? (
          <>
            <PanelErrorBoundary>
              <NodeStats
                enabled={documentVisible}
                poolFreshNonce={poolFreshNonce}
                course={viewCourse}
              />
            </PanelErrorBoundary>
            <PanelErrorBoundary>
              <WorkerRegistry
                registry={stateView?.workerRegistry ?? null}
                onAction={doAction}
                readOnly={readOnly}
              />
            </PanelErrorBoundary>
          </>
        ) : null}

        {/* ══════════════════ 传输 ══════════════════ */}
        {page === 'wire' ? (
          <PanelErrorBoundary>
            <WirePanel stateView={stateView} course={viewCourse} />
          </PanelErrorBoundary>
        ) : null}
      </Shell>
      {stateView ? (
        <TrainLaunchModal
          open={trainOpen}
          modes={stateView.modes}
          onClose={() => setTrainOpen(false)}
          onAction={doAction}
          onLaunch={(opts) => void handleLaunch(opts)}
          readOnly={readOnly}
        />
      ) : null}
    </div>
  )
}

void null
