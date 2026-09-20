/** app.tsx — 训练控制台根组件（SSR + hydrate；一屏仪表盘布局，DECISIONS §355）。
 *
 *  布局：顶栏（课程▾ + 「训练」按键 + 在训课程 pill + 刷新间隔 select + ⟳）→ Hero（胜率焦点 + 迷你条）
 *  → 组件 4 小卡 → 节点 pill 行 → 详情抽屉（指标 | 节点统计 | 日志）→ TrainingLoop 启动弹窗。
 *
 *  交互纪律：无「停止全部」（用户指令）· 无「暂停刷新」按钮（改刷新间隔 select）·
 *  停用节点默认折叠（慢/离线始终展开）· 工具行并入启动弹窗 · 详情一律进右侧抽屉（Esc / ✕ / 遮罩关闭）。
 *
 *  polling 优先级（GLM-E6）：visibility(后台 tab) > 刷新间隔；客户端输入均为本地 state，
 *  3s 轮询不覆盖（不再需要全局 dirty 暂停）。 */

import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { Flash, type FlashState } from '../components/Flash'
import { Drawer } from '../components/Drawer'
import { PanelErrorBoundary } from '../components/PanelErrorBoundary'
import { usePolling } from './lib/usePolling'
import { fetchState, postAction } from './lib/api-client'
import { Hero } from './panels/Hero'
import { ComponentCards } from './panels/ComponentCards'
import { NodePills } from './panels/NodePills'
import { TrainingPills } from './panels/TrainingPills'
import { MetricsTable } from './panels/MetricsTable'
import { NodeStats } from './panels/NodeStats'
import { LogNavCard } from './panels/LogNavCard'
import { TrainLaunchModal, type TunnelLaunchOpts } from './panels/TrainLaunchModal'
import { OpenCourseModal } from './panels/OpenCourseModal'
import { BcPanel } from './panels/BcPanel'
import { CourseOverview } from './panels/CourseOverview'
import { WorkerRegistry } from './panels/WorkerRegistry'
import { LoopQueue } from './panels/LoopQueue'
import { TaskBundlePanel } from './panels/TaskBundlePanel'
import { WirePanel } from './panels/WirePanel'
import { EvalSummary } from './panels/EvalSummary'
import type { RolloutSrcMode, TrainMode } from '../../core/types'
import {
  fmtTs,
  latestRow,
  REFRESH_INTERVALS,
  refreshLabel,
  TC_CLOUDHALT_ACK,
  TC_GLOBAL_INTERVAL,
  TC_RO_BANNER_DISMISSED,
  cloudHaltAckKey,
  parseCloudHaltAcks,
  type ConsoleStateView,
  type PhaseInfo,
  type RefreshSec,
  visibleCloudHalts,
} from '../view'

export interface AppProps {
  initial: ConsoleStateView
}

type DrawerTabKey = 'metrics' | 'nodes' | 'log' | 'wire'

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
  // 开课弹窗（2026-09-20：课程级选项随它走，不再搭「启动服务进程」的车）。
  const [openCourseModal, setOpenCourseModal] = useState(false)
  const [poolFreshNonce, setPoolFreshNonce] = useState(0)
  // 只读横幅可关闭：localStorage 记住「不再显示」（仅局域网只读视图相关；tc. 前缀防误删）。
  // 首帧恒 false（与 SSR 一致），localStorage 偏好 hydrate 后恢复——见下方 effect。
  const [roBannerDismissed, setRoBannerDismissed] = useState(false)
  useEffect(() => {
    if (readLocal(TC_RO_BANNER_DISMISSED) === '1') setRoBannerDismissed(true)
  }, [])
  // 云端停机横幅已读（§386；2026-09-14 扩到 halted）：按「事件身份」记（课程+时刻），
  // 同一事件只提示一次，新一次停机/恢复会重新弹。
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
  // 只读视图标记：服务端按请求来源 stamp（SSR 首帧即正确，无闪跳）；缺省回退 hostname 判定。
  const readOnly = initial.readOnly ?? !isLocal
  const [viewCourse, setViewCourse] = useState<string>(initial.course)
  const viewCourseRef = useRef(viewCourse)
  viewCourseRef.current = viewCourse
  // ── 门禁动作模式（2026-09-13）：halt = 触发门禁就下发 cloud halt（默认）；
  //    notify = 只横幅告警、绝不杀云端 PPO worker。
  //    为什么需要它：G4(plateau) 的 REMEDIATE 在平台期**每 5 轮必然复现**，历史上
  //    c6-pickup3（6 次）/ c6-bonus（10 次）就是被它反复杀掉云机，后半程全在中断态下训练。
  //    注意 hydrate 安全：初始值**恒为 'halt'**（服务端标志文件 + localStorage 都在
  //    挂载后的 effect 里校准）——在 useState 初始化里读 localStorage 会让 SSR 首帧与
  //    客户端不一致（横幅关闭后样式崩的根因，见 tests/console-lan.test.ts）。
  const [gateHaltMode, setGateHaltMode] = useState<'halt' | 'notify'>('halt')
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
    (e: Event) => {
      const el = e.target as HTMLSelectElement | null
      const v: 'halt' | 'notify' = el?.value === 'notify' ? 'notify' : 'halt'
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

  const handleLaunch = async (opts?: TunnelLaunchOpts): Promise<void> => {
    setTrainOpen(false)
    // 启动**只带进程级选项**（2026-09-20）：本机 agent → 共享 hub → 共享 trainer。
    // 课程级选项（训练模式 / rollout 位置 / 降级本机）随「开课」走（`openCourse`），
    // 服务端也把往 preset 里的这些字段当错误拒掉（响亮，而不是静默丢掉）。
    const body: Record<string, unknown> = {}
    // M1/M2：传输选项随启动回写 rl-config + console-state（未选 = 不传，沿用现值）。
    if (opts?.cfProtocol) body.cfProtocol = opts.cfProtocol
    if (opts?.cfEdgeIp) body.cfEdgeIp = opts.cfEdgeIp
    if (opts?.slim) body.slim = opts.slim
    await doAction('preset', body)
  }

  /** 开课：课程级选项随它一起下发（服务端写 `courses.<课>.*` + 建发现事实 + 解暂停 + 置 hub 模式）。 */
  const handleOpenCourse = async (opts: {
    trainMode: TrainMode
    rolloutSrc?: RolloutSrcMode
    remoteDegrade: boolean
  }): Promise<void> => {
    setOpenCourseModal(false)
    await doAction('openCourse', {
      trainMode: opts.trainMode,
      remoteDegrade: opts.remoteDegrade,
      ...(opts.rolloutSrc ? { rolloutSrc: opts.rolloutSrc } : {}),
    })
  }

  /** 停课：非破坏（删开课标记 + 写暂停意图 + 该课 hub 置离线；队列与账本一个字不动）。
   *
   *  ★ 课程显式带上（不依赖 doAction 的「当前查看课程」兜底）：停课的入口在**每门课的 pill**
   *  上（2026-09-20 用户口径），点 A 课的 ■ 必须停 A——若走兜底，一旦查看目标与 pill 不同步
   *  （或用户在两次渲染之间切了课）就会停错一门，那是这里最贵的一类错误。 */
  const handleStopCourse = async (course: string): Promise<void> => {
    await doAction('stopCourse', { course })
  }

  // 课程锁已随「单 hub 多课程」解除（2026-09-18）：hub 现在一个进程托管 N 份账本
  // （`--course <课>` 可重复），进程级状态不再与「操作员在看哪门课」绑定——原来的
  // 「hub 运行中锁定课程」保护（§367，当时 hub 按课程建 jobRoot/日志目录）已无对象，
  // 而多课程并行下它反倒会把查看/切换彻底锁死。切课程现在只改「看哪门课」。

  // 课程下拉 onChange：本机 = 查看 + POST setCourse 同步操作员课程；局域网 = 仅查看 + 写 URL。
  // 注意：ref 须在此同步更新（setState 后下一渲染才赋值）——随后的 refreshState/doAction
  // 立即读到的必须已是新课程，否则轮询仍拉旧课程。
  const selectCourse = useCallback(
    (c: string): void => {
      viewCourseRef.current = c
      setViewCourse(c)
      writeUrlCourse(c)
      void refreshState()
      setPoolFreshNonce((n) => n + 1)
      // 只读视图不 POST（服务端也会 403 兜底）；用服务端 stamp 的 readOnly 而非 isLocal。
      if (!readOnly) void doAction('setCourse', { course: c })
    },
    [readOnly, refreshState, doAction],
  )

  const onCourseChange = (e: Event): void => {
    selectCourse((e.target as HTMLSelectElement).value)
  }

  // 顶栏阶段耗时（至今；now 由 10s ticker 驱动，轮询间隙不冻结）。
  const phaseInfo: PhaseInfo | null = stateView?.phase ?? null
  const phaseElapsed = phaseInfo && phaseInfo.sinceMs != null ? now - phaseInfo.sinceMs : null

  // 在训课程（**可多门**）：以服务端 stamp 的 `trainingCourses` 为准——2026-09-20 起它是
  // **已开课**的课程（事实源 = 开课标记，与训练侧 `enabled_courses` / hub `_course_dir_live`
  // 同一个闸），不是「共享 trainer 在跑」（那是进程事实，两者正交：开了课但进程没跑是合法
  // 稳态）。旧服务端没有该字段 ⇒ 空表（**不**回退到「trainer 在跑就当作这门课在训」：那个
  // 回退会在旧版上给一门从未开课的课挂上在训 pill，而 pill 上的停课键会真去停一门没开的课）。
  const trainingLoop = (stateView?.components ?? []).find((c) => c.key === 'trainingLoop')
  const trainingCourses = stateView?.trainingCourses ?? []
  const trainingSet = new Set(trainingCourses)
  // 共享 trainer 是否在跑（「已开课」与「进程在跑」是两件事，pill 状态与按钮提示都要说清）。
  const trainerRunning = trainingLoop?.status === 'running'
  // 课程生命周期（2026-09-20）：开课/停课入口的判据由服务端 stamp（账本存在 + 暂停意图）。
  // 旧视图无此字段 ⇒ 不渲染按钮（不编状态；宁可少一个按钮，不可给一个假承诺）。
  const lifecycle = stateView?.courseLifecycle ?? null

  return (
    <div className="tc-wrap">
      <Flash flash={flash} onHide={() => setFlash(null)} />
      <header className="tc-topbar">
        <div className="tc-topbar__row">
          <h1>
            <span className="dot" />
            炼丹炉
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
              // 恒可切（含历史课程）：切课程只改「本浏览器看哪门课」+ 本机的操作员课程，
              // 不碰任何在训进程——多课程并行时它还必须可切（否则看不到其它在训课程）。
              title={
                readOnly
                  ? '局域网只读：切换仅影响当前浏览器的查看课程，不影响训练'
                  : '切换查看课程（含历史课程）。不影响任何在训课程'
              }
              onChange={onCourseChange}
            >
              <option value="">自动（最近活跃课程）</option>
              {(stateView?.courses ?? []).map((c) => (
                <option key={c} value={c}>
                  {trainingSet.has(c) ? '🔥 ' : ''}
                  {c}
                  {trainingSet.has(c) ? '（已开课）' : ''}
                </option>
              ))}
            </select>
          </label>
          {/* 开课入口（2026-09-20 用户口径：**进程启动与课程解耦** + **课程开训必须手动开**）。
              先在上面的课程 select 选课，再点这里：弹窗收课程级选项（训练模式 / rollout
              位置 / 降级本机），确认即开课——写开课标记（训练侧/hub 的「在训」判据）+ 课程级
              旋钮 + 发现事实（账本/权重/`remote-jobs`）+ 解暂停 + 置 hub 模式（进程没跑也能开）。

              **停课不在这里**：它在每门课的 pill 上（同一行的 TrainingPills），按课停、按课消失
              ——一个按钮同时做「开这门」与「停这门」在两门课并存时语义不明。 */}
          {viewCourse ? (
            <button
              type="button"
              className={`tc-btn tc-btn--sm${lifecycle?.enabled ? '' : ' tc-btn--primary'}`}
              // 只读视图**不物理禁用**（只读是动作边界，不是按钮状态：禁用会让整条工具栏
              // 看起来灰败破碎，真点击由服务端 403 + flash 兜底）。
              title={
                readOnly
                  ? '只读模式：开课仅限本机 localhost'
                  : lifecycle?.enabled
                    ? `${viewCourse} 已在课程表（在训）。再点「训练」= 按当前选项重写课程级旋钮 + 重新置 hub 模式（机器侧旋钮要重开课才生效）`
                    : `${viewCourse} 未开课。点「训练」= 开课：写开课标记 + 建账本/权重/remote-jobs + 按所选训练模式置 hub 派发闸（进程没跑也能开）`
              }
              onClick={() => setOpenCourseModal(true)}
            >
              训练
            </button>
          ) : null}
          {/* ── 在训课程 pill（用户 2026-09-20 口径）：一门课一个 pill（it 数 + 状态），
               点 pill 切查看目标（Hero 趋势 + 指标表跟着走）并高亮，pill 上的 ■ 停课（非破坏）
               ——停课后服务端 stamp 里不再有它，pill 自行从顶部消失（不做本地乐观删除：队列与
               账本一字未动这件事只能由服务端事实说话）。
               **与课程 select / 「训练」按键同一行**（用户口径「和课程 select 挤进同一行，避免
               占用宝贵的纵向页面空间」）：pill 数是零到几，单独占一行每条都白花 ~34px 纵向；
               pill 多时本组自身横向滚动（不把顶栏顶成两行 —— 那又回到吃纵向空间）。── */}
          <PanelErrorBoundary>
            <TrainingPills
              courses={trainingCourses}
              rows={stateView?.loopQueue?.rows ?? []}
              trainerRunning={trainerRunning}
              viewCourse={viewCourse}
              onSelect={selectCourse}
              onStop={(c) => void handleStopCourse(c)}
              readOnly={readOnly}
            />
          </PanelErrorBoundary>
          {/* 门禁动作（**仅在有训练时显示**）：停机 = 触发门禁即下发 cloud halt；
              提示 = 只横幅告警，绝不杀云端 PPO worker。
              背景：G4(plateau) 的 REMEDIATE 每 5 轮必复现，c6-pickup3 / c6-bonus
              被它反复停机 6 次 / 10 次，后半程训练全在中断态下进行。切换**即时生效**。 */}
          {trainingCourses.length > 0 ? (
            <label
              className="tc-topbar__course"
              title={
                '门禁触发时对云端 PPO worker 的动作。\n' +
                '· 停机（默认）：下发停机达令，云机释放。\n' +
                '· 提示：只记录 verdict 并显示横幅，不停机——平台期（G4）会每 5 轮复现，' +
                '停机等于反复杀掉 PPO worker。\n' +
                '切换后立即对下一轮门判定生效，无需重启训练。'
              }
            >
              <span className="tc-topbar__course-lbl">触发门禁</span>
              <select
                id="gateHaltSel"
                className="tc-sel"
                value={gateHaltMode}
                disabled={!isLocal || readOnly}
                onChange={onGateHaltModeChange}
              >
                <option value="halt">停机</option>
                <option value="notify">提示</option>
              </select>
            </label>
          ) : null}
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
              ? '控制台无响应（服务端可能已退出）——检查 `bun run dashboard` 进程'
              : '刷新失败，正在重试'}
          </span>
          <button type="button" className="tc-btn tc-btn--sm" onClick={() => void refreshState()}>
            重试
          </button>
        </div>
      ) : null}{' '}
      {visibleCloudHalts(stateView?.cloudHalts, stateView?.course ?? '')
        .filter(([, h]) => h.status === 'halted')
        .filter(
          ([courseName, h]) => !cloudHaltAcks.includes(cloudHaltAckKey('halted', courseName, h.at)),
        )
        .map(([courseName, h]) => (
          <div key={`halt-${courseName}`} className="tc-banner tc-banner--err" role="alert">
            <span>
              ⚠ {courseName ? `课程 ${courseName} ` : ''}停机中（{h.reason}
              ）：已向云机下发停机命令——云机先尝试停机； 停不掉则照常执行任务（不闲置空烧）。本地
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
      {visibleCloudHalts(stateView?.cloudHalts, stateView?.course ?? '')
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
      {/* ── RL 区（2026-09-14 与 BC 区互斥：isBc 课只出 BC 区，Hero/EvalBoard 只属 RL） ── */}
      {stateView?.isBc ? null : (
        <PanelErrorBoundary>
          <Hero
            stateView={stateView}
            onMore={() => setDrawerTab('metrics')}
            onRefresh={() => void refreshState()}
            readOnly={readOnly}
          />
        </PanelErrorBoundary>
      )}
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
      {/* ── 多课程并行总览（RL 区）：每课一行（在训/离线/iter/队列/在飞）+ hub 调度行 ── */}
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
      {/* ── 训练调度器（单例，**两区通用**）：每课任务队列 + 「在等什么」——
           总览卡回答 hub 侧「谁在派活」，本卡回答训练侧「这一轮卡在哪一步」。

           ★ 本卡**不受 `isBc` 门控**（2026-09-19，R3-4）：它是**跨课程**卡（一次列出所有
           账本可发现的课，每行自带 kind），而 `isBc` 说的是**当前查看的那门课**——用它门控
           这张卡是范畴错误，后果是「选中一门 BC 课 ⇒ 整张调度器卡片消失」，于是 BC 课在
           调度器视图里根本不存在（而 BC 课正是需要看「在等哪个 GPU job 回传」的那类）。
           BC 行与 RL 行并列：行上有 BC 徽标，粒度/指针/在飞各取自自己的账本。 ── */}
      <PanelErrorBoundary>
        <LoopQueue
          loopQueue={stateView?.loopQueue ?? null}
          course={viewCourse}
          onSelectCourse={selectCourse}
          onAction={doAction}
        />
      </PanelErrorBoundary>
      {/* ── push worker 登记（两区通用）：写 rl-config nodes[] + hub 周期探活 ── */}
      <PanelErrorBoundary>
        <WorkerRegistry
          registry={stateView?.workerRegistry ?? null}
          onAction={doAction}
          readOnly={readOnly}
        />
      </PanelErrorBoundary>
      {/* ── 任务包（导出 task-<课程>.zip / 导入 deliver-<课程>.zip 并评估）：两区通用 ── */}
      <PanelErrorBoundary>
        <TaskBundlePanel course={viewCourse} enabled={documentVisible} readOnly={readOnly} />
      </PanelErrorBoundary>
      {/* ── EvalBoard 摘要（RL 区）：行 = B 层 iter × 列 = rung×指标；完整看板独立成页 /eval。 ── */}
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
      {/* ── BC 区（与 RL 区互斥）：仅 *.bc.jsonc 课程显示；epoch/eval 数据由 BcPanel 拉取 ── */}
      {stateView?.isBc ? (
        <PanelErrorBoundary>
          <BcPanel course={viewCourse} enabled={documentVisible} />
        </PanelErrorBoundary>
      ) : null}
      {/* 详情视图直连入口（2026-09-10）：此前「评估」只能先点 Hero/节点 pill 的「更多」
          进抽屉、再切 tab —— 入口不可见（底部说明也只列了 3 个）。五视图平权直连
          （2026-09-17 增「传输」：wire 计量 + 隧道 A/B）。 */}
      <nav
        aria-label="详情视图"
        style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '2px 0 10px' }}
      >
        {(
          [
            ['metrics', '指标'],
            ['wire', '传输'],
            ['nodes', '节点统计'],
            ['log', '日志'],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className="tc-btn tc-btn--sm"
            aria-label={`打开${label}`}
            onClick={() => setDrawerTab(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      <p className="tc-caption">
        局域网只读：可查看任意课程/日志/节点统计/评估（课程▾仅本浏览器切换）；启停/冒烟/模式/节点编辑
        仅本机 localhost 生效 · /api/state {refreshInterval}s 轮询 · 首页按课程分流（互斥）：RL 课 =
        胜率焦点 + EvalBoard 摘要（行=iter×列=rung×指标），BC 课 = BC Epoch 区 ·
        组件卡（点击卡在下方展开全宽最近日志）+ 节点 pill 行 + 详情抽屉（指标 | 传输 | 节点统计 |
        日志，上方按钮可直连）两课通用 · 传输页 = 每轮实发/实收字节与秒（wire）+ 隧道 A/B 探针结果 ·
        评估已独立成页 /eval（RL 课上方「完整评估看板」）· Esc 关闭弹窗/抽屉 · r 立即刷新全部。
      </p>
      <Drawer
        open={drawerTab !== null}
        activeTab={drawerTab ?? 'metrics'}
        tabs={[
          { key: 'metrics', label: '指标' },
          { key: 'wire', label: '传输' },
          { key: 'nodes', label: '节点统计' },
          { key: 'log', label: '日志' },
        ]}
        onTab={(k) => setDrawerTab(k as DrawerTabKey)}
        onClose={() => setDrawerTab(null)}
      >
        {drawerTab === 'metrics' ? (
          <MetricsTable
            stateView={stateView}
            onRefresh={() => void refreshState()}
            readOnly={readOnly}
          />
        ) : null}
        {drawerTab === 'wire' ? <WirePanel stateView={stateView} course={viewCourse} /> : null}
        {drawerTab === 'nodes' ? (
          <NodeStats enabled poolFreshNonce={poolFreshNonce} course={viewCourse} />
        ) : null}
        {drawerTab === 'log' ? <LogNavCard stateView={stateView} course={viewCourse} /> : null}
      </Drawer>
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
