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

/** 本课 push 执行面的探测结果（慢快照；纯 config 解析 + 一次 `/ping` 直探）。 */
export interface PushTargetProbe {
  /** local = 本机 worker_server（local_push 节点）；cloud = 云 GPU 节点；
   *  unresolved = 课程 `push_node_url` 指向 config 里不存在的节点（python 侧「匹配 0 个」
   *  → 静默回落 pull，必须显式暴露）。 */
  kind: 'local' | 'cloud' | 'unresolved'
  url: string
  nodeId: string | null
  /** `/ping` 直探结果；null = 未探（无鉴权键）。 */
  healthy: boolean | null
}

/** 本课 push 执行面视图（2026-09-15）：trainer 的 job 现在推给谁——本机 worker_server
 *  还是云 GPU。kind 由 config 解析（`push_node_url` → 认领节点），active = 本课 trainer
 *  正以 push 模式在跑（执行面此刻真的生效；false 时徽章是「配置指向」而非「正在用」）。 */
export interface PushTargetView extends PushTargetProbe {
  active: boolean
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
  /** 当前生效的隧道协议/边缘 IP（M1；缺省 http2/4）——UI 显示「当前生效值」。
   *  可选（additive）：旧视图/夹具不带此键时 UI 回退缺省。 */
  cfProtocol?: string
  cfEdgeIp?: string
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
  components: ComponentView[]
  nodes: NodeView[]
  /** 本机直跑节点（§361⑤：pill 行只读展示；无池/无槽位时缺省）。 */
  localNode?: NodeLocalView | null
  /** 本课 push 执行面（2026-09-15）：未配置 push 目标时为 null/缺省。 */
  pushTarget?: PushTargetView | null
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
  /** 课程种类（2026-09-14 首页 BC/RL 区互斥分流）：true = BC 课程（*.bc.jsonc，
   *  首页只出 BC Epoch 区）；缺省/false = RL 课程（首页只出 RL 区：Hero + EvalBoard 摘要）。
   *  服务端按查看课程 stamp（buildStateView），与 readOnly 同机制——测试直构缺省按 RL。 */
  isBc?: boolean
}
