/** console-types.ts — 控制台整页视图类型（组件 / 节点 / 模式 / 指标 / 整页快照）与展示辅助。 */
import { ParallelOverviewView, PushWorkerRegistryView } from './course-overview'
import { LoopQueueView } from './loop-queue'
import { IterRow, PairedReferee } from './metric-types'
import { PhaseInfo } from './phase'

// ────────────────────────── 组件 / 节点 / 模式视图类型 ──────────────────────────

/** 卡数据源陈旧度（颜色+形状双编码：ok=绿圆 / refresh=黄半圆 / err=红方）。 */
export type StaleState = 'ok' | 'refresh' | 'err'

/** 组件作用域（服务端按 `core/registry.componentScope` 填）：
 *  singleton = 全机一份（selfNode）· shared = 一个进程服务所有课程（hub/隧道）· course = 按课程键控。
 *  卡片按它分组（服务面 vs 课程面，R3-3）——见 `component-groups.ts`。 */
export type ComponentScope = 'singleton' | 'shared' | 'course'

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
  /** **作用域**（R3-3）：`shared` = 一个进程服务所有并行课程（hub/隧道，2026-09-18 起），
   *  不属于当前查看的课程——卡片要标出来，否则操作员会以为「这门课自己的 hub 停了」；
   *  `singleton` = 全机一份（selfNode）；`course` = 按课键控。
   *  **缺省/未知 ⇒ 按 `course` 渲染**（单侧保守）：少一个徽章只是少信息，凭空空贴「共享」
   *  会让操作员以为「停它就是停全局」（而它其实只停本课）——假承诺比缺标签贵。 */
  scope?: ComponentScope
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
  /** 当前生效的协议瘦身开关（M2，`'on'|'off'`；缺省 on）——可选（additive）：
   *  旧视图/夹具不带此键时 UI 回退缺省。 */
  slim?: string
  /** 当前生效的 rollout 执行位置（M3，`'local'|'node'|'auto'`；缺省 local）——
   *  可选（additive）：旧视图/夹具不带此键时 UI 回退缺省。 */
  rolloutSrc?: string
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
  /** 在训课程（多课程并行）：registry 里 trainingLoop **进程存活**的课程，按名排序。
   *  课程 select 的多课高亮与总览的「在训」列同源；缺省（旧视图/测试直构）= 无在训课。 */
  trainingCourses?: string[]
  /** 多课程并行总览（hub `/admin/queue` + 每课一行）；缺省/null = 无 hub 应答或未计算。 */
  overview?: ParallelOverviewView | null
  /** 训练调度器（**单例**：一个进程服务所有并行课程）的每课队列视图（R2c-3）。
   *  数据源 = `run_rl_cluster.py --json`（只读计划视图）+ registry 的在训事实。
   *  缺省/null = 读失败（`error` 在视图里）或旧视图——UI 显空态，不编数据。 */
  loopQueue?: LoopQueueView | null
  /** push worker 登记视图（rl-config `nodes[].gpu_push` + 面板直探 + hub 侧探活）。 */
  workerRegistry?: PushWorkerRegistryView | null
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
  /** M1 隧道 A/B 探针结果（`tmp/tunnel-ab-*.json`，新→旧）。
   *  缺省（无探针文件/测试直构）= UI 显空态 + 重跑命令提示。 */
  tunnelAb?: TunnelAbView | null
}

/** 单腿单方向的 p50/p90/max（探针 `_stats()` 口径）。 */
export interface TunnelAbStat {
  n: number
  p50Sec: number
  p90Sec: number
  maxSec: number
  p50Mbps: number
}

/** 一行 = (腿, 方向)；方向是**关键列**（push 模式的真实大头是上行）。 */
export interface TunnelAbRow {
  leg: string
  dir: 'up' | 'down'
  stat: TunnelAbStat
}

/** 一次探针运行（一个 `tmp/tunnel-ab-*.json`）。 */
export interface TunnelAbRun {
  file: string
  /** 文件 mtime（epoch ms）。 */
  mtime: number
  bytes: number
  rounds: number
  rows: TunnelAbRow[]
  /** http2 vs quic 的 p50 倍率（quic ÷ http2；>1 = http2 更快）；缺任一腿 → null。 */
  speedup: { up: number | null; down: number | null }
}

export interface TunnelAbView {
  available: boolean
  /** 新的在前（看最近几次即可判定「连跑是否退化」）。 */
  runs: TunnelAbRun[]
  error?: string
}
