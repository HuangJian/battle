/** types.ts — training 工具链共享类型（配置 / 登记 / 监督进程）。 */

/** rl-config.json 的 nodes 条目（rollout 节点）。 */
export interface NodeConf {
  id: string
  url: string
  authKey: string
  concurrency: number
  enabled: boolean
  /** GPU push 节点（DECISIONS §340 补充 4：URL 指向其 worker_server 隧道）。 */
  gpu_push?: boolean
  /** **本机** worker_server 回落节点（2026-09-15）：endpoint 留空且 config 里没有任何
   *  ping 通的 gpu_push 时，控制台 push 预设把本课 push 目标改指本机的 `workerServe`
   *  组件（`http://127.0.0.1:<push 端口>`）。与云节点**并存**：`applyPushNodeConfig` 只
   *  认非本机节点，回落也绝不覆盖用户填的云 URL（两种节点可随时互相切换）。 */
  local_push?: boolean
}

/** 课程配置块（plan multi-course-parallel-training §1.3）。
 *  slot 0–3 = 槽位（hub_port = base + slot*10）；workers/local_slots = 本机并发配额。 */
export interface CourseConf {
  slot?: number
  workers?: number
  local_slots?: number
  /** push 节点（worker_server 隧道）URL；多课同值 = N:1 共享（§3.8）。 */
  push_node_url?: string
}

/** rl-config.json（本工具链只消费 nodes + rl + courses 块，其余键原样保留）。 */
export interface RlConfig {
  version: number
  nodes: NodeConf[]
  rl: {
    hub_port: number
    agent_port: number
    remote_token: string
    remote_hub_url?: string
    /** 每课 hub 隧道 URL（P3：hub 与隧道每课独立；单键 remote_hub_url 保留兼容读）。 */
    remote_hubs?: Record<string, string>
    torch_threads?: number
    local_slots?: number
    [key: string]: unknown
  }
  /** per-course 槽位/配额（唯一事实来源；console-state 不存这些）。 */
  courses?: Record<string, CourseConf>
  [key: string]: unknown
}

/** 受管组件（registry 分文件账本的键）。 */
export type Component =
  | 'selfNode'
  | 'hubServer'
  | 'cloudflared'
  | 'localWorker'
  | 'trainingLoop'
  | 'workerServe'

/** 单组件登记条目（PID 账本 + 可选元数据）。 */
export interface RegistryEntry {
  pid: number
  /** 进程入口（监督重启/变更检测用）。 */
  entry?: string
  course?: string
  /** 槽位（§1.4 重建契约：hubServer/cloudflared/localWorker/workerServe 重启时必须知道自己占哪槽）。 */
  slot?: number
  url?: string
  log?: string
  metrics?: number
  /** hub 的 jobRoot（§1.4 重建契约：重启不得用全局状态猜课程目录）。 */
  jobRoot?: string
  /** trainingLoop 的 push 节点 URL（REMOTE_PUSH_NODE 重启注入）。 */
  pushNodeUrl?: string
  /** T7：启动时是否 opt-in 远端连败降级本机 PPO（默认 false；监督重启复现）。 */
  remoteDegrade?: boolean
  /** 启动模式：控制台 trainer 编排（pull/push/local）或 'remote'（冒烟预演）。 */
  mode?: 'pull' | 'push' | 'local' | 'remote'
  /** 非正常退出记录（§380 exit-watchdog 写入；UI/API 展示退出原因）。 */
  error?: string
  exitAt?: string
}

/** registry.json：全部组件条目（缺省组件 = 未启动）。
 *
 *  多课程形状（plan §1.4，P1b）：按课程键控的五个组件各有一份 `Record<course, Entry>`。
 *  旧扁平单键（`hubServer`/…）**已在 P5 移除**（R2）：类型里不再声明，唯一读点是
 *  `registry.ts::migrateFlatCourseEntries` 的一次性搬迁（把旧条目搬进 per-course 表再删键），
 *  写入路径不再产生扁平键（`saveComponent` 只服务 selfNode）。
 *  枚举一律走 `registry.ts::registryComponents()`（三元组），不要直接按 key 枚举账本对象。 */
export interface Registry {
  /** 单例（agent 全局一份）。 */
  selfNode?: RegistryEntry
  /** per-course 键（P1b 起唯一写入路径）。 */
  hubServers?: Record<string, RegistryEntry>
  cloudflareds?: Record<string, RegistryEntry>
  workerServes?: Record<string, RegistryEntry>
  localWorkers?: Record<string, RegistryEntry>
  trainingLoops?: Record<string, RegistryEntry>
}

/** 旧扁平账本键（P1–P4 的历史形状）——**仅**供 `registry.ts` 的一次性搬迁读取（R2）。
 *  任何其它代码不得读它：编译期把它们挡在 `Registry` 之外，正是为了不留读兼容后门。 */
export type LegacyFlatRegistry = Partial<
  Record<'hubServer' | 'cloudflared' | 'trainingLoop' | 'workerServe', RegistryEntry>
>

/** 需要**整树停止**的组件（stop / 全部停止 / 监督重启三处共用，实现见 net.ts::killPidTree）。
 *
 *  只有「自身还带一个子进程监督器」的组件在内：localWorker 跑的就是云端那条
 *  `remote.worker` 入口——父 `supervise_worker` + 子 `worker_loop`（子进程 60s 心跳续租、
 *  长期轮询 hub 抢 job）。只杀父进程 = 留一个继续抢 job 的孤儿，「随时启停」形同虚设。
 *  其余组件都是单进程，不进此集合（默认 False 路径行为不变）。 */
export const COMPONENT_KILL_TREE: ReadonlySet<string> = new Set(['localWorker'])

/** 受管进程的描述（spawn + 监督 + 变更检测的统一载体）。 */
export interface ProcSpec {
  /** registry 键（selfNode/hubServer/...）。 */
  key: Component
  /** 展示名。 */
  name: string
  /** 命令行（argv[0] = 可执行文件）。 */
  cmd: string[]
  cwd?: string
  /** 追加到子进程 env（在 process.env 之上）。 */
  env?: Record<string, string>
  /** stdout/stderr 直写的日志文件。 */
  log: string
  /** 健康检查（就绪判定）；监督循环周期性调用。 */
  healthy: () => Promise<boolean>
  /** 变更检测哨兵文件（mtime 变了 = 该进程运行的代码已更新）。 */
  sentinels: string[]
  /** 归属课程（多课监督以 (key, course) 为单位；空串 = 无课程）。 */
  course?: string
  /** 停止/重启时是否连**整棵进程树**一起杀（默认 false = 只杀登记的那个 PID）。
   *  true 只给「自身还带着一个子进程监督器」的组件（localWorker 跑云端同款
   *  `remote.worker`：父进程 supervise_worker + 子进程 worker_loop）——只杀父进程会
   *  留下继续轮询 hub 抢 job 的孤儿，停止/重启就形同虚设。 */
  killTree?: boolean
}
