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
   *  不属于当前查看的课程；`singleton` = 全机一份（selfNode）；`course` = 按课键控。
   *  它决定卡片落在哪个**族**（`view/component-groups.ts::FAMILY_OF_SCOPE`），不再逐行上徽章
   *  （2026-09-20 删除：族标题已经说过一次）。
   *  **缺省/未知 ⇒ 按 `course` 渲染**（单侧保守）：误归类只是把行放进另一族，
   *  而凭空空贴「共享」会让操作员以为「停它就是停全局」（而它其实只停本课）——假承诺比缺标签贵。 */
  scope?: ComponentScope
  /** 需要展示的密钥型字段（仅 cloudflared：rl.remote_token，供用户复制贴给远端）。
   *  局域网只读与回环同权展示——只读是动作边界，不是数据边界（2026-09-09 用户指令）。 */
  secret?: string
  /** 非正常退出原因（§380 消费；服务端填充由该条目实施方完成）。 */
  error?: string | null
}

/** push 执行面（**机群级**事实，2026-09-19：课程与 worker 节点正交 ⇒ 不再按课程键控）。
 *
 *  `mode` 由部署事实推出（`stack/push-config.ts::remoteExecutionFace`，与 python
 *  `resolve_transport`/`resolve_hub_push` 同序）：有登记节点 + `rl.hub_push` + hub 地址
 *  ⇒ hub 中介派发；有节点但缺 hub（或显式关掉）⇒ 直推；没节点 ⇒ pull（worker 来领）。
 *  `probes` 是逐节点 `/ping` 的后台探活（慢快照）。 */
export interface PushFleetProbe {
  mode: 'hub-dispatch' | 'direct-push' | 'pull'
  text: string
  detail: string
  nodes: number
  hubPush: boolean
  hubUrl: string
  probes: Array<{ id: string; url: string; healthy: boolean | null }>
}

export interface NodeView {
  id: string
  url: string
  gpuPush: boolean
  enabled: boolean
  concurrency: number
  /** /v1/ping 实时探测；null = 未探测（disabled 时跳过）。 */
  online: boolean | null
  /** 慢节点（ping 失败但近期仍在成功结算，算力受限）：口径 = 「ping 超时 ≠ 掉线」。
   *  仅当 online === false 时有意义（在线节点无慢语义）。
   *  **2026-09-20 起 pill 行不再消费它**——健康度改由 `nodeHealth(最近完成轮贡献, 并发数)`
   *  判定（贡献数是「这台机器上一轮到底交没交活」的直接事实，ping 只是可达性）；
   *  本字段保留给 API / 节点统计表（`isSlowNode` 仍是服务端口径，见 pool-history）。 */
  slow: boolean
  codeHash: string | null
  cpus: number | null
  busy: boolean
  /** 最近**完成**轮贡献数（该轮内该节点成功局数，rollout + eval；-1 = 无池数据）。
   *  「完成」= 训练账本已写该轮 `iteration` 事件——进行中那一轮的半截计数不算数
   *  （否则先交活的节点看着健康、还没轮到的看着掉线）。 */
  lastContrib: number
}

/** 节点健康度（2026-09-20 用户指令：**只由最近完成轮的贡献数**判定，与 ping 探测无关）。
 *
 *  · `offline` — 贡献 0（无池数据 -1 同判）⇒ 这一轮它没产出，标红的「离线」
 *  · `healthy` — 贡献 ≥ 节点并发数 ⇒ 满负荷产出
 *  · `slow`    — 0 < 贡献 < 并发数 ⇒ 在产出但没吃满并发，标琥珀的「缓慢」
 *
 *  纯函数、无 IO：`server/pool-history` 与浏览器 pill 行共用（客户端安全，见 view/index.ts）。 */
export type NodeHealth = 'healthy' | 'slow' | 'offline'

export function nodeHealth(contrib: number, concurrency: number): NodeHealth {
  if (!(contrib > 0)) return 'offline'
  return contrib >= (concurrency >= 1 ? concurrency : 1) ? 'healthy' : 'slow'
}

export interface NodeLocalView {
  id: 'local'
  /** 本机直跑槽数（rl.local_slots）；显式 0 = 直跑未启用（仍出芯片，slots=0）；
   *  配置缺失/非法（NaN）时整个 localNode 缺省不出。 */
  slots: number
  /** 最近**完成**轮贡献数（与节点同口径：该轮内 local 成功局数；-1 = 无池数据）。 */
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

/** 行为/传输开关的当前生效值。
 *
 *  ★ 2026-09-19：删掉了 `trainerPpo`（pull/push/local）。启动训练不再选模式——
 *  pull 是「远端 worker 自己来领」（本机只保证 hub 在线 + 可选隧道），push 是
 *  「系统里登记了 push worker 节点」（入口在 worker 登记面板，数据住 rl-config.json）。
 *  两者都由**部署事实**决定，课程侧一个字都不配。 */
export interface ModeView {
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
  /** 在训课程（多课程并行）：**共享 trainer 在跑**（registry 的空串槽）∧ 该课未收官
   *  （python 队列状态 `state`，2026-09-19 / R3-5）。课程 select 的多课高亮与总览的
   *  「在训」列同源；缺省（旧视图/测试直构）= 无在训课。 */
  trainingCourses?: string[]
  /** 多课程并行总览（hub `/admin/queue` + 每课一行）；缺省/null = 无 hub 应答或未计算。 */
  overview?: ParallelOverviewView | null
  /** 控制台记录的**每课 hub 派发意图**（`console-state.courseModes`，R3-2）。
   *
   *  它与 `overview` 里的 hub 事实是**两个源**：hub 的模式是 volatile（重启回启动参数），
   *  控制台这份是运维的决定、由「切离线/切换成在线」与「离线开课」写入、起 hub 时回灌。
   *  两者不一致 = 意图没落地（2026-09-23 实测：回灌跑在 hub 发现这门课之前，POST 400，
   *  该课静默留在 online 而面板显示「在训/切离线」）——UI 拿它做漂移徽标，不自己推算。
   *  缺省/空 = 旧视图或没有任何意图（UI 不画漂移提示，不编状态）。 */
  courseModeIntents?: Record<string, 'online' | 'offline'> | null
  /** **逐课**的生效 rollout 源（`courses.<课>.rollout_src` > `rl.rollout_src` > 缺省 local）。
   *
   *  ★ 2026-09-24（plan/train-mode-hot-switch §2.5）：`modes.rolloutSrc` 只覆盖**查看课程**，
   *  而课程矩阵是逐行全课表——要判「hub 当它在线、可配置里还是 `run`（整段上云）」
   *  这种半状态，非当前课程的行也需要自己的那一格。
   *  缺省/null = 旧视图或夹具（UI 不画配置侧提示，不编状态）。 */
  courseRolloutSrc?: Record<string, string> | null
  /** 课程生命周期事实（2026-09-20：进程启动与课程解耦后，顶部「开课/停课」入口的判据）。
   *  `enabled` = **开课标记**（`tmp/<课>/training-enabled.txt`）——训练侧 `enabled_courses`
   *  与 hub `_course_dir_live` 的同一个闸（有账本 ≠ 在训：历史课都有账本）；
   *  `paused` = 暂停意图已写（调度器卡片的「暂停」同一份契约）。
   *  缺省/null = 无查看课程或旧视图（UI 不渲染该按钮，不编状态）。 */
  courseLifecycle?: { enabled: boolean; paused: boolean } | null
  /** 训练调度器（**单例**：一个进程服务所有并行课程）的每课队列视图（R2c-3）。
   *  数据源 = `run_rl_cluster.py --json`（只读计划视图）+ registry 的调度器存活事实。
   *  缺省/null = 读失败（`error` 在视图里）或旧视图——UI 显空态，不编数据。 */
  loopQueue?: LoopQueueView | null
  /** push worker 登记视图（rl-config `nodes[].gpu_push` + 面板直探 + hub 侧探活）。 */
  workerRegistry?: PushWorkerRegistryView | null
  components: ComponentView[]
  nodes: NodeView[]
  /** 本机直跑节点（§361⑤：pill 行只读展示；无池/无槽位时缺省）。 */
  localNode?: NodeLocalView | null
  /** push 执行面（机群级；2026-09-19 起不再按课程）：登记节点 + hub_push + 逐节点探活。 */
  pushFleet?: PushFleetProbe | null
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
