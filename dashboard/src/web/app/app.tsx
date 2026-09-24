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
import type { RolloutSrcMode, TrainMode } from '../../core/types'
import { AlertDock } from '../components/AlertDock'
import { Flash, type FlashState } from '../components/Flash'
import { PanelErrorBoundary } from '../components/PanelErrorBoundary'
import { usePolling } from './lib/usePolling'
import { fetchState, postAction } from './lib/api-client'
import { Shell } from './shell/Shell'
import { Sidebar } from './shell/Sidebar'
import { Topbar } from './shell/Topbar'
import { Hero } from './panels/Hero'
import { ComponentCards } from './panels/ComponentCards'
import { NodePills } from './panels/NodePills'
import { MetricsTable } from './panels/MetricsTable'
import { NodeStats } from './panels/NodeStats'
import { TrainLaunchModal, type TunnelLaunchOpts } from './panels/TrainLaunchModal'
import { OpenCourseModal } from './panels/OpenCourseModal'
import { TrainingPills } from './panels/TrainingPills'
import { BcPanel } from './panels/BcPanel'
import { CourseMatrix } from './panels/CourseMatrix'
import { WorkerRegistry } from './panels/WorkerRegistry'

import { WirePanel } from './panels/WirePanel'
import { EvalSummary } from './panels/EvalSummary'
import {
  bootstrapPage,
  buildAlerts,
  canonicalPath,
  DEFAULT_PAGE,
  latestRow,
  pageForPath,
  REFRESH_INTERVALS,
  TC_CLOUDHALT_ACK,
  TC_GLOBAL_INTERVAL,
  TC_RO_BANNER_DISMISSED,
  parseCloudHaltAcks,
  isStaleStateResponse,
  withCourse,
  type ConsoleBootstrap,
  type PageKey,
  type RefreshSec,
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
  // 开课弹窗（2026-09-20：进程与课程解耦后，「开哪门课」的课程级旋钮住在这里——训练模式 /
  // rollout 位置；而「启动服务进程」弹窗只带进程级选项）。
  const [openCourseModal, setOpenCourseModal] = useState(false)
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
  //
  //  ★ 切课竞态（2026-09-22）：请求**发起时**记下查看课程，响应回来时若已切走则丢弃——
  //   否则 A→B 快速切换时 A 的迟到响应会把 stateView 覆盖成 A 的数据（面板显示另一门课
  //   的指标/走势，要等下一次轮询才纠正）。服务端把切课做快到毫秒级后，这个竞态窗口更小，
  //   但「点了两下」仍然会撞上——保护在这里，与服务端快慢无关。
  const refreshState = useCallback(async (): Promise<void> => {
    const want = viewCourseRef.current
    try {
      const s = await fetchState(want)
      if (isStaleStateResponse(want, viewCourseRef.current)) return
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
        setOpenCourseModal(false)
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
      // 池面板（节点统计）与动作不同源：服务端已在动作后软作废那份视图（旧值 + 后台重算），
      // 这里推一下 nonce 让面板**立即**再校验 —— 否则「停用节点」要等 300s 的下一次轮询
      // 才在池表里上屏（同一页上下两处事实不合）。
      if (!readOnly) setPoolFreshNonce((n) => n + 1)
      return { ok: r.ok }
    },
    [readOnly, refreshState],
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

  const handleLaunch = async (opts?: TunnelLaunchOpts): Promise<void> => {
    setTrainOpen(false)
    // 启动**只带进程级选项**（2026-09-20）：本机 agent → 共享 hub → 共享 trainer。
    // 课程级选项（训练模式 / rollout 位置）随「开课」走（`openCourse`），
    // 服务端也把往 preset 里塞这些字段当错误拒掉（响亮，而不是静默丢掉）。
    const body: Record<string, unknown> = {}
    // M1/M2：传输选项随启动回写 rl-config + console-state（未选 = 不传，沿用现值）。
    if (opts?.cfProtocol) body.cfProtocol = opts.cfProtocol
    if (opts?.cfEdgeIp) body.cfEdgeIp = opts.cfEdgeIp
    if (opts?.slim) body.slim = opts.slim
    await doAction('preset', body)
  }

  /** 开课：课程级选项随它一起下发（服务端写 `courses.<课>.*` + 建发现事实 + 解暂停 + 置 hub
   *  模式；进程没跑也能开）。与「启动服务进程」解耦——进程是共享的一台，回答不了
   *  「这门课怎么跑」。 */
  const handleOpenCourse = async (opts: {
    trainMode: TrainMode
    rolloutSrc?: RolloutSrcMode
  }): Promise<void> => {
    setOpenCourseModal(false)
    await doAction('openCourse', {
      trainMode: opts.trainMode,
      ...(opts.rolloutSrc ? { rolloutSrc: opts.rolloutSrc } : {}),
    })
  }

  /** 停课：非破坏（删开课标记 + 写暂停意图 + 该课 hub 置离线；队列与账本一个字不动）。
   *
   *  ★ 课程显式带上（不依赖 doAction 的「当前查看课程」兜底）：停课入口在**每门课的 pill** 上，
   *  点 A 课的 ■ 必须停 A —— 走兜底时，一旦查看目标与 pill 不同步（或两次渲染之间切了课）
   *  就会停错一门，那是这里最贵的一类错误。 */
  const handleStopCourse = async (course: string): Promise<void> => {
    await doAction('stopCourse', { course })
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

  // 在训课程（**可多门**）：以服务端 stamp 的 `trainingCourses` 为准——2026-09-20 起它是
  // **已开课**的课程（事实源 = 开课标记 `training-enabled.txt`，与训练侧 `enabled_courses` /
  // hub `_course_dir_live` 同一个闸），不是「共享 trainer 在跑」（那是进程事实，两者正交：
  // 开了课但进程没跑是合法稳态）。旧服务端没有该字段 ⇒ 空表（**不**回退到「trainer 在跑就
  // 当作这门课在训」：那个回退会给一门从未开课的课挂上 pill，而 pill 上的停课键会真去停一门
  // 没开的课）。
  const trainingLoop = (stateView?.components ?? []).find((c) => c.key === 'trainingLoop')
  const trainingCourses = stateView?.trainingCourses ?? []
  // 共享 trainer 是否在跑：「已开课」与「进程在跑」是两件事，pill 的状态字要把两半都说清。
  const trainerRunning = trainingLoop?.status === 'running'
  // 课程生命周期（开课入口的判据，服务端 stamp）；旧视图无此字段 ⇒ 不渲染该按钮（不编状态）。
  const lifecycle = stateView?.courseLifecycle ?? null

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
        sidebar={
          <Sidebar
            // 激活态按**页面键**推（不是 location）：SSR 无 location，首帧读它会 hydrate 错配。
            activePath={canonicalPath(page)}
            course={viewCourse}
            courses={courses}
            trainingCourses={trainingCourses}
            onCourseChange={selectCourse}
            // 开课入口（课程级）：紧挨课程选择器（操作读序：选课 → 训练 → 看哪几门在训）。
            onOpenCourse={() => setOpenCourseModal(true)}
            courseEnabled={lifecycle?.enabled ?? null}
            onNavigate={navigate}
            readOnly={readOnly}
            gate={{
              visible: trainingCourses.length > 0,
              mode: gateHaltMode,
              disabled: !isLocal || readOnly,
              onChange: onGateHaltModeChange,
            }}
            refresh={{ value: refreshInterval, onChange: onRefreshIntervalChange }}
          />
        }
        topbar={
          <Topbar
            page={page}
            stateView={stateView}
            phaseElapsedMs={phaseElapsed}
            trainingCount={trainingCourses.length}
            courseCount={courses.length}
            // 在训课程 pill 行 = 「在训 n/N」裸计数的**明细版**（每门课一个 pill：it / 状态 /
            // 停课）。零门在训时 pill 行自身不渲染，顶栏退回裸计数 chip。
            pills={
              <TrainingPills
                courses={trainingCourses}
                rows={stateView?.loopQueue?.rows ?? []}
                trainerRunning={trainerRunning}
                overview={stateView?.overview ?? null}
                modeIntents={stateView?.courseModeIntents ?? null}
                viewCourse={viewCourse}
                onSelect={selectCourse}
                onStop={(c) => void handleStopCourse(c)}
                readOnly={readOnly}
              />
            }
            nodeSummary={nodeSummary}
            connError={connError}
            onRetry={() => void refreshState()}
            onRefreshNow={() => {
              void refreshState()
              setPoolFreshNonce((n) => n + 1)
            }}
          />
        }
      >
        {/* ── 告警坞（P2b：原先 6 条同权重横幅收敛成一个容器，见 view/alerts.ts） ──
            条目、排序、折叠判据全在纯函数层；这里只把动作绑到通道上。 */}
        <AlertDock
          items={buildAlerts({
            cloudHalts: stateView?.cloudHalts,
            viewing: viewCourse,
            acks: cloudHaltAcks,
            loopComplete: stateView?.loopComplete,
            ppoQueueStall: stateView?.ppoQueueStall,
            courseEdit: stateView?.courseEdit,
            readOnly,
            roDismissed: roBannerDismissed,
            now: Date.now(),
          })}
          onAct={(act, body) => doAction(act, body)}
          onAck={(key) => {
            // 会话级一次性已读（只读提示）走自己的键；其余按事件身份记进停机 ack 表。
            if (key === 'ro-banner-dismissed') {
              writeLocal(TC_RO_BANNER_DISMISSED, '1')
              setRoBannerDismissed(true)
              return
            }
            ackCloudHalt(key)
          }}
        />

        {/* ══════════════════ 总览 ══════════════════ */}
        {page === 'overview' ? (
          <>
            {/* ★ KPI 条（「关键指标」六格）已于 2026-09-20 下线（用户指令）——连同
                `components/KpiStrip.tsx` / `view/kpi.ts` / `tests/web-kpi.test.ts` 一并删除。
                理由：它是「索引」型面板（六个数都能在 Hero / 节点行 / 课程表头读到同一个值），
                首屏纵向空间给了**能直接动手**的那几区；留一个指针不如把那几区往上提。 */}
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
            {/* 课程矩阵（**两区通用**，取代原「并行课程总览」+「训练调度器」两张表）：
                hub 侧（派活/队列/离线段）与训练侧（指针/卡在哪一步/在等什么）合并成一行。
                不受 isBc 门控——它是**跨课程**表，行自带 BC/RL 种类徽标；而两张表分开时
               「hub 在派活但没进程」/「在训但 hub 没注册」这两种矛盾各自都是「正常」的。 */}
            <PanelErrorBoundary>
              <CourseMatrix
                overview={stateView?.overview ?? null}
                loopQueue={stateView?.loopQueue ?? null}
                modeIntents={stateView?.courseModeIntents ?? null}
                courseRolloutSrc={stateView?.courseRolloutSrc ?? null}
                course={viewCourse}
                onSelectCourse={selectCourse}
                onAction={doAction}
              />
            </PanelErrorBoundary>
            {/* ★2026-09-22 改版（用户指令）：首页不再有独立「任务包」区域——离线课程的
                导出（点一下即取回）/ 导入训练结果下沉到课程矩阵每行「操作」列（BundleRowActions）。 */}
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
            {/* 组件日志入口由组件卡行内的「≡ 日志」承担（2026-09-20 P3c 下线 LogNavCard：
                同一组入口在同页出现两处，且它那句轮询说明讲的是日志页自己的行为——
                已并入组件卡的 ≡ 悬停文案）。 */}
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
      {stateView ? (
        <OpenCourseModal
          open={openCourseModal}
          course={viewCourse}
          modes={stateView.modes}
          onClose={() => setOpenCourseModal(false)}
          onConfirm={(opts) => void handleOpenCourse(opts)}
          readOnly={readOnly}
        />
      ) : null}
    </div>
  )
}

void null
