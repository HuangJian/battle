/** console-types.ts — 控制台整页视图类型（组件 / 节点 / 模式 / 指标 / 整页快照）与展示辅助。 */
import { IterRow, PairedReferee } from './metric-types'
import { PhaseInfo } from './phase'

// ────────────────────────── 组件 / 节点 / 模式视图类型 ──────────────────────────

/** 卡数据源陈旧度（颜色+形状双编码：ok=绿圆 / refresh=黄半圆 / err=红方）。 */
export type StaleState = 'ok' | 'refresh' | 'err'

export interface ComponentView {
  key: string
  label: string
  /** running = 进程存活；stopped = 无存活进程；exited = 登记仍在但进程已死。 */
  status: 'running' | 'stopped' | 'exited'
  pid: number | null
  url: string | null
  course: string | null
  mode: string | null
  healthy: boolean | null
  /** 日志文件相对 nn-training/ 的路径。 */
  log: string | null
  logTail: string[]
  busy: boolean
  /** 需要展示的密钥型字段（仅 cloudflared：rl.remote_token，供用户复制贴给远端）。
   *  局域网只读与回环同权展示——只读是动作边界，不是数据边界（2026-09-09 用户指令）。 */
  secret?: string
  /** 非正常退出原因（§380 消费；服务端填充由该条目实施方完成）。 */
  error?: string | null
}

export interface NodeView {
  id: string
  url: string
  gpuPush: boolean
  enabled: boolean
  concurrency: number
  /** /v1/ping 实时探测；null = 未探测（disabled 时跳过）。 */
  online: boolean | null
  /** 慢节点（ping 失败但近期仍在成功结算，算力受限）：展示「慢」而非「离线」。
   *  仅当 online === false 时有意义（在线节点无慢语义）。 */
  slow: boolean
  codeHash: string | null
  cpus: number | null
  busy: boolean
  /** 上一轮贡献数（全局最新轮下该节点成功局数；-1 = 无池数据）。 */
  lastContrib: number
}

export interface NodeLocalView {
  id: 'local'
  /** 本机直跑槽数（rl.local_slots）；显式 0 = 直跑未启用（仍出芯片，slots=0）；
   *  配置缺失/非法（NaN）时整个 localNode 缺省不出。 */
  slots: number
  /** 上一轮贡献数（与节点同口径：全局最新轮下 local 成功局数；-1 = 无池数据）。 */
  lastContrib: number
}

/** URL 展示整形（§361①）：协议 + 域名 + 尾 4 位。cloudflared 隧道域名过长，
 *  卡片内溢出换行——截断展示 + CopyButton 复制全量（title 仍给完整 URL）。 */
export function shortUrl(url: string): string {
  const m = url.match(/^(https?:\/\/[^/]+)/)
  if (!m) return url
  const host = m[1]!
  return url.length <= host.length + 4 ? url : `${host}…${url.slice(-4)}`
}

export interface ModeView {
  trainerPpo: 'pull' | 'push' | 'local'
  stream: number
  doubleBuffer: number
  precollectEarly: number
}

export interface MetricsView {
  available: boolean
  iters: IterRow[]
  error?: string
  /** 配对裁判（只读；缺数据/单轮时为 null，UI 显示空态）。 */
  pairedReferee?: PairedReferee | null
}

/** 训练正常完成停车态（账本 run_complete 事件派生，api.ts 填充）。 */
export interface LoopComplete {
  at: string
  reason: string
  iters: number
}

/** course_edit 事件（trainer 每轮热加载钩子写入本地账本，不进云端 payload；
 *  §2026-09-13-hot-reload）。rejected = 语料身份编辑被拒 → 控制台错误横幅。 */
export interface CourseEdit {
  verdict: 'applied' | 'rejected' | 'restored'
  fields: string[]
  detail: string
  at: string
  it: number
}

/** 单课总览里的组件状态（P5-W2 同屏多课；**不含 selfNode**——它是全局单例，只出一次）。 */
export interface CourseOverviewComponent {
  key: string
  status: 'running' | 'stopped' | 'exited'
  pid: number | null
}

/** 同屏多课总览的单行（P5-W2）：只读，不参与动作路由（动作仍在选中课程的组件卡）。
 *  数据源 = 账本（进程状态）+ 该课 training_log 的最近一轮 + console-state 的按课 halt。 */
export interface CourseOverview {
  course: string
  /** hubServer/trainingLoop/workerServe/cloudflared 四件（无 selfNode）。 */
  components: CourseOverviewComponent[]
  /** 当前训练阶段（训练循环日志尾解析）。 */
  phase: PhaseInfo
  /** 最近一轮关键指标（无日志 → null）。 */
  last: {
    iter: number
    winRate: number
    rolloutSec: number
    ppoSec: number
    halted: boolean
  } | null
  /** 日志轮数（≈已训练迭代数）。 */
  iters: number
  /** 该课云端停机记录（有则展示徽标）。 */
  cloudHalt: { status: 'halted' | 'recovered'; reason: string } | null
  /** 该课 PPO 队列排队超时。 */
  ppoQueueStall: { jobId: string; waitedSec: number; it: number | null } | null
}

/** 单课云端停机记录（§386 + S17：halted=红横幅，recovered=灰横幅历史）。 */
export interface CloudHaltView {
  at: string
  reason: string
  status: 'halted' | 'recovered'
  clearedAt?: string
  clearReason?: string
}

export interface ConsoleStateView {
  time: string
  course: string
  /** 控制台当前课程（P5-W1 additive；旧视图无此字段 → 回退 `course`）。 */
  activeCourse?: string
  courses: string[]
  /** 同屏多课总览（P5-W2；单课/旧视图缺省 — 仅当发现多课才填充）。 */
  courseOverviews?: CourseOverview[]
  components: ComponentView[]
  nodes: NodeView[]
  /** 本机直跑节点（§361⑤：pill 行只读展示；无池/无槽位时缺省）。 */
  localNode?: NodeLocalView | null
  modes: ModeView
  metrics: MetricsView
  /** 当前训练阶段（顶栏图标用）。 */
  phase: PhaseInfo
  /** 每课云端停机记录（§386 + S17：键 = 课程名；halted=红横幅，recovered=灰横幅历史）。 */
  cloudHalts?: Record<string, CloudHaltView>
  /** PPO 任务排队超时（>5min 无 worker 领取）：warning 横幅——云端 worker 可能断连。 */
  ppoQueueStall?: {
    jobId: string
    waitedSec: number
    it: number | null
  } | null
  /** 训练正常完成且进程停车等待重启（账本尾行 run_complete + 进程仍存活时派生）：
   *  info 横幅——本地已停采、云机已停机；resume（新 run_start/iteration）后自动消失。 */
  loopComplete?: LoopComplete | null
  /** 课程热加载最新判决（§2026-09-13-hot-reload；账本最近一条 course_edit 事件）。
   *  rejected = 语料身份编辑被拒 → 错误横幅；restored/applied 不上横幅。 */
  courseEdit?: CourseEdit | null
  /** 局域网只读视图（服务端按请求来源 stamp；true = 本页只读——动作按钮禁用 + 只读角标）。
   *  缺省（SSR/测试直构）时客户端回退 location.hostname 判定。 */
  readOnly?: boolean
}
