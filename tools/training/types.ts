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

/** rl-config.json（本工具链只消费 nodes + rl 块，其余键原样保留）。 */
export interface RlConfig {
  version: number
  nodes: NodeConf[]
  rl: {
    hub_port: number
    agent_port: number
    remote_token: string
    remote_hub_url?: string
    torch_threads?: number
    local_slots?: number
    [key: string]: unknown
  }
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
  url?: string
  log?: string
  metrics?: number
  /** 启动模式：控制台 trainer 编排（pull/push/local）或 'remote'（冒烟预演）。 */
  mode?: 'pull' | 'push' | 'local' | 'remote'
}

/** registry.json：全部组件条目（缺省组件 = 未启动）。 */
export interface Registry {
  selfNode?: RegistryEntry
  hubServer?: RegistryEntry
  cloudflared?: RegistryEntry
  trainingLoop?: RegistryEntry
  workerServe?: RegistryEntry
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
}
