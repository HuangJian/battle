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
export type Component = 'selfNode' | 'hubServer' | 'cloudflared' | 'trainingLoop' | 'workerServe'

/** 单组件登记条目（PID 账本 + 可选元数据）。 */
export interface RegistryEntry {
  pid: number
  /** 进程入口（监督重启/变更检测用）。 */
  entry?: string
  course?: string
  /** 槽位（§1.4 重建契约：hubServer/cloudflared/workerServe 重启时必须知道自己占哪槽）。 */
  slot?: number
  url?: string
  log?: string
  metrics?: number
  /** hub 的 jobRoot（§1.4 重建契约：重启不得用全局状态猜课程目录）。 */
  jobRoot?: string
  /** trainingLoop 的 push 节点 URL（REMOTE_PUSH_NODE 重启注入）。 */
  pushNodeUrl?: string
  /** 启动模式：控制台 trainer 编排（pull/push/local）或 'remote'（冒烟预演）。 */
  mode?: 'pull' | 'push' | 'local' | 'remote'
  /** 非正常退出记录（§380 exit-watchdog 写入；UI/API 展示退出原因）。 */
  error?: string
  exitAt?: string
}

/** registry.json：全部组件条目（缺省组件 = 未启动）。
 *
 *  多课程形状（plan §1.4，P1b）：按课程键控的四个组件各有一份 `Record<course, Entry>`；
 *  同时保留旧扁平单键**读兼容到 P5**（R1）——期间新旧共存，线上旧账本不丢监督。
 *  枚举一律走 `registry.ts::registryComponents()`（三元组），不要直接按 key 枚举账本对象。 */
export interface Registry {
  /** 单例（agent 全局一份）。 */
  selfNode?: RegistryEntry
  /** 旧扁平单键（P1–P4 读兼容，P5 移除读写）。 */
  hubServer?: RegistryEntry
  cloudflared?: RegistryEntry
  trainingLoop?: RegistryEntry
  workerServe?: RegistryEntry
  /** per-course 键（P1b 起写入路径）。 */
  hubServers?: Record<string, RegistryEntry>
  cloudflareds?: Record<string, RegistryEntry>
  workerServes?: Record<string, RegistryEntry>
  trainingLoops?: Record<string, RegistryEntry>
}

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
}
